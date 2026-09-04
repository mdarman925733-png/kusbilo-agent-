"""
Kusbilo voice-ordering agent.

Runs on LiveKit Cloud (no VM needed). Uses Gemini's realtime model for
audio-in/audio-out directly, so there's no separate STT/TTS step.

The Flutter app (lib/core/services/live_voice_service.dart) registers two
RPC methods on itself — 'addToCart' and 'confirmOrder' — and expects THIS
agent to call them via room.local_participant.perform_rpc(). That's what
the two @function_tool functions below do: the tool call itself is just a
forward, all the real logic (updating the cart, placing the order) still
lives in the Flutter app.

Catalog / FAQ / language: the Flutter app currently sends these as
arguments to the `createLiveKitToken` Cloud Function, not as room state.
For the agent to see them, that Cloud Function needs to also store them as
room metadata (or you pass them another way) when it creates the room —
see the README for the one-line change needed there. Until that's done,
this agent falls back to a generic catalog-less greeting.
"""

import json
import logging

from google.cloud import firestore
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    RunContext,
    ToolError,
    cli,
    function_tool,
)
from livekit.plugins import google

logger = logging.getLogger("kusbilo-voice-agent")
logger.setLevel(logging.INFO)

server = AgentServer()
_firestore_client = firestore.Client()


def _fetch_gemini_api_key() -> str:
    """Reads settings/voiceAgent.geminiApiKey from Firestore, same admin
    panel pattern as settings/paymentGateway.baseUrl for UPI. Re-read on
    every call start so a key swapped in the admin panel (e.g. after
    hitting a rate limit) takes effect on the very next call, no redeploy.
    """
    doc = _firestore_client.collection("settings").document("voiceAgent").get()
    key = (doc.to_dict() or {}).get("geminiApiKey")
    if not key:
        raise RuntimeError(
            "settings/voiceAgent.geminiApiKey is not set in Firestore — "
            "set it from the admin panel's voice-agent tab."
        )
    return key


def _load_room_context(ctx: JobContext) -> dict:
    """Room metadata is expected to be a JSON string like:
    {"isHindi": true, "appFaq": "...", "catalog": "..."}
    Falls back to safe defaults if it's missing or malformed.
    """
    raw = ctx.room.metadata or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Room metadata was not valid JSON, using defaults: %r", raw)
        data = {}
    return {
        "is_hindi": bool(data.get("isHindi", False)),
        "app_faq": data.get("appFaq", ""),
        "catalog": data.get("catalog", ""),
    }


def _first_remote_identity(ctx: JobContext) -> str:
    if not ctx.room.remote_participants:
        raise ToolError("No buyer is connected to this call.")
    return next(iter(ctx.room.remote_participants))


@function_tool()
async def add_to_cart(context: RunContext, product_id: str, quantity: int) -> dict:
    """Add a product to the buyer's cart.

    Args:
        product_id: The product's id, exactly as given in the catalog.
        quantity: How many units the buyer wants.
    """
    ctx: JobContext = context.userdata["job_ctx"]
    try:
        raw = await ctx.room.local_participant.perform_rpc(
            destination_identity=_first_remote_identity(ctx),
            method="addToCart",
            payload=json.dumps({"productId": product_id, "quantity": quantity}),
            response_timeout=8.0,
        )
        return json.loads(raw)
    except Exception as e:
        logger.warning("addToCart RPC failed: %s", e)
        raise ToolError("Could not add that item to the cart, please try again.")


@function_tool()
async def confirm_order(context: RunContext) -> dict:
    """Call this once the buyer says they're done and want to place the order.
    Placing the order (location, Firestore write) happens on the app side —
    this just tells the app to go ahead and wait for the result.
    """
    ctx: JobContext = context.userdata["job_ctx"]
    try:
        raw = await ctx.room.local_participant.perform_rpc(
            destination_identity=_first_remote_identity(ctx),
            method="confirmOrder",
            payload="{}",
            response_timeout=25.0,
        )
        return json.loads(raw)
    except Exception as e:
        logger.warning("confirmOrder RPC failed: %s", e)
        raise ToolError("Could not place the order, please try again.")


def _build_instructions(room_ctx: dict) -> str:
    language_line = (
        "Speak Hindi throughout the call, in a warm, simple, everyday tone."
        if room_ctx["is_hindi"]
        else "Speak English throughout the call, in a warm, simple, everyday tone."
    )
    return f"""You are Kusbilo's voice ordering assistant. {language_line}

You help the buyer pick items and place an order, entirely by voice.

Available products (id | names | price | tags | description):
{room_ctx['catalog'] or 'No catalog was provided for this call.'}

App FAQ, use this if the buyer asks a general question about the app:
{room_ctx['app_faq'] or 'No FAQ was provided for this call.'}

Rules:
- Only offer products that appear in the catalog above. Never invent products or prices.
- When the buyer clearly wants an item, call add_to_cart with its exact product id and quantity.
- Confirm the full order out loud (items, quantities) before calling confirm_order.
- Only call confirm_order once, after the buyer has explicitly agreed to place the order.
- Keep responses short — this is a voice call, not a chat.
"""


@server.rtc_session()
async def entrypoint(ctx: JobContext):
    await ctx.connect()
    await ctx.wait_for_participant()

    room_ctx = _load_room_context(ctx)

    agent = Agent(
        instructions=_build_instructions(room_ctx),
        tools=[add_to_cart, confirm_order],
    )

    session = AgentSession(
        llm=google.beta.realtime.RealtimeModel(
            model="gemini-2.5-flash-native-audio-preview-12-2025",
            api_key=_fetch_gemini_api_key(),
        ),
        userdata={"job_ctx": ctx},
    )

    await session.start(agent=agent, room=ctx.room)
    await session.generate_reply(
        instructions="Greet the buyer briefly and ask what they'd like to order today."
    )


if __name__ == "__main__":
    cli.run_app(server)
