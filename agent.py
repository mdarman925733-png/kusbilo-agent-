"""
Kusbilo voice-ordering agent.

Runs on LiveKit Cloud (no VM needed). Uses Gemini's realtime model for
audio-in/audio-out directly, so there's no separate STT/TTS step.
"""

import json
import logging
import os

from google.cloud import firestore
from google.oauth2 import service_account
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

_creds_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
if _creds_json:
    _credentials = service_account.Credentials.from_service_account_info(json.loads(_creds_json))
    _firestore_client = firestore.Client(credentials=_credentials, project=_credentials.project_id)
else:
    _firestore_client = firestore.Client()


def _fetch_gemini_config() -> dict:
    """Reads settings/geminiLiveApi from Firestore — this is the same doc
    Gaonadmin's "Voice Call" tab already writes to (apiKey + instructions).
    """
    doc = _firestore_client.collection("settings").document("geminiLiveApi").get()
    data = doc.to_dict() or {}
    key = data.get("apiKey")
    if not key:
        raise RuntimeError(
            "settings/geminiLiveApi.apiKey is not set in Firestore — "
            "set it from the admin panel's Voice Call tab."
        )
    return {"api_key": key, "admin_instructions": data.get("instructions", "")}


def _load_room_context(ctx: JobContext) -> dict:
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
    """Call this once the buyer says they're done and want to place the order."""
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


def _build_instructions(room_ctx: dict, admin_instructions: str) -> str:
    language_line = (
        "Speak Hindi throughout the call, in a warm, simple, everyday tone."
        if room_ctx["is_hindi"]
        else "Speak English throughout the call, in a warm, simple, everyday tone."
    )
    admin_block = (
        f"\nCurrent notes from the shop admin (offers, greetings, tone) — follow these:\n{admin_instructions}\n"
        if admin_instructions
        else ""
    )
    return f"""You are Kusbilo's voice ordering assistant. {language_line}

You help the buyer pick items and place an order, entirely by voice.

Available products (id | names | price | tags | description):
{room_ctx['catalog'] or 'No catalog was provided for this call.'}

App FAQ, use this if the buyer asks a general question about the app:
{room_ctx['app_faq'] or 'No FAQ was provided for this call.'}
{admin_block}
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
    gemini_config = _fetch_gemini_config()

    agent = Agent(
        instructions=_build_instructions(room_ctx, gemini_config["admin_instructions"]),
        tools=[add_to_cart, confirm_order],
    )

    session = AgentSession(
        llm=google.beta.realtime.RealtimeModel(
            model="gemini-2.5-flash-native-audio-preview-12-2025",
            api_key=gemini_config["api_key"],
        ),
        userdata={"job_ctx": ctx},
    )

    await session.start(agent=agent, room=ctx.room)
    await session.generate_reply(
        instructions="Greet the buyer briefly and ask what they'd like to order today."
    )


if __name__ == "__main__":
    cli.run_app(server)
