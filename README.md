# Kusbilo Voice Agent — phone-only deploy

No laptop, no terminal. Everything below happens through GitHub's and
LiveKit's websites (or the GitHub app) in your phone browser. GitHub
Actions does the actual `lk agent deploy` work for you, on their servers.

## 1. LiveKit Cloud account (phone browser)
- Go to `cloud.livekit.io`, sign up (free, no card).
- Create a project.
- Settings → API Keys → create one. Note the 3 values: URL, API Key, API
  Secret.

## 2. GitHub account + repo (phone browser or GitHub app)
- Sign up at `github.com` if you don't have an account.
- Create a new repository (e.g. `kusbilo-voice-agent`).
- Open it → "Add file" → "Upload files" → upload every file from this
  folder, keeping the `.github/workflows/` folder structure intact.

## 3. Firebase service account (phone browser)
- Firebase console → your project → Project settings → Service accounts
  → Generate new private key. A `.json` file downloads.
- Open that file (any file/text viewer app), copy its full contents.

## 4. Add secrets in GitHub (Settings tab of your repo, phone browser)
Go to Settings → Secrets and variables → Actions → "New repository secret"
and add each of these:

| Secret name | Value |
|---|---|
| `LIVEKIT_URL` | from step 1 |
| `LIVEKIT_API_KEY` | from step 1 |
| `LIVEKIT_API_SECRET` | from step 1 |
| `SECRET_LIST` | `GOOGLE_APPLICATION_CREDENTIALS_JSON=<paste the whole json from step 3>` |

## 5. Run the deploy (Actions tab, phone browser)
- Go to the "Actions" tab of your repo.
- Click "Create or Deploy LiveKit Agent (manual)" in the left list.
- Click "Run workflow" → operation = **create** → Run.
- Wait for the green tick (few minutes). This creates the agent on
  LiveKit Cloud and commits a `livekit.toml` back into your repo.

That's it — the agent is live. From now on, if the file `agent.py` ever
changes, the second workflow (`Auto-deploy agent on change`) redeploys it
automatically — no need to run anything manually again.

## Setting/changing the Gemini key (day-to-day, no GitHub needed)
The agent reads the Gemini key from Firestore, not from GitHub:
Firestore → `settings` collection → `voiceAgent` document → field
`geminiApiKey`. Set/change it there directly, or wire your Gaonadmin
"Voice Agent" tab to write to that same field. Either way, changing it
there is enough — no redeploy required.

## One thing your Firebase Cloud Function needs (ask whoever built it)
`createLiveKitToken` receives `isHindi`, `appFaq`, `catalog` from the app
but needs to also store them as the room's metadata (JSON) when it
creates the room, so the agent can read them. Point them to this repo's
`agent.py` — it explains exactly what JSON shape it expects.
