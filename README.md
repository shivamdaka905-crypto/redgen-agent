# RedGen Agent

A self-hosted personal AI agent: chat, tools, memory, notes, recurring automations and a
WhatsApp channel, in one small Python app. This is the "own your stack" version of the
Superagent you have been using, rebuilt so you can run it free on a GitHub Codespace or a VM.

## What it does

| Capability | How it works here |
|---|---|
| Chat | `POST /api/chat` + browser UI at `/` |
| Tools | Model-driven function calling (`app/tools.py`), 15 tools |
| Memory | SQLite table, injected into every system prompt |
| Notes | Markdown files under `workspace/notes`, readable/writable by the agent |
| Automations | `every_minutes` jobs polled by `app/scheduler.py`, delivered to chat or WhatsApp |
| WhatsApp | Cloud API webhook (`GET/POST /webhook/whatsapp`) with outbound send |
| Web | `web_fetch` + `web_search` tools |
| Shell / files | Sandboxed to `workspace/`, 60s shell timeout |
| Any API | `http_request` tool for JSON APIs and backend functions |
| Real browser | Playwright Chromium, optional: open/click/type/screenshot via 7 `browser_*` tools + `screenshot_url` |
| Images | `generate_image` via any OpenAI-compatible images endpoint |
| Voice | `text_to_speech` with edge-tts, free, no key, loudness-normalised |
| Video | `make_video`: scenes -> voice -> cards -> ffmpeg mp4 |
| YouTube | `yt_search`, `yt_channel_uploads`, `yt_video_stats` (API key or OAuth) |
| Accounts | OAuth2 engine for Google, Meta, TikTok, GitHub, Slack + `connected_call` |

## Three providers, switchable, any model

| Provider | Base URL baked in | Default model |
|---|---|---|
| OpenRouter | `https://openrouter.ai/api/v1` | `anthropic/claude-sonnet-4.5` |
| Groq | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.0-flash` |
| Custom | any URL you type | any model id you type |

All three speak the same OpenAI-compatible protocol, so one code path drives them.
Each provider keeps its own key, URL and model in `data/settings.json`; the **Settings** tab
switches the active one without losing the others. For a fine-tuned or self-hosted model,
pick **Custom**, paste its URL (`http://localhost:11434/v1` for Ollama) and type the model id.
Mixing is fine: cheap model for chat, a strong one for video scripting.

## Run it (GitHub Codespaces, free)

1. Push this folder to a GitHub repo.
2. Repo -> **Code -> Codespaces -> Create codespace**.
3. In the terminal: `bash setup.sh`
4. Open `.env` and paste your model key (OpenRouter / Groq / OpenAI / DeepSeek all work).
5. `source .venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 8000`
6. Open the forwarded port 8000 -> chat UI. Add the key in the **Settings** tab if you skipped `.env`.

## Files

```
app/main.py        HTTP API, webhook, startup
app/agent.py       the tool-calling loop
app/tools.py       tool schemas + handlers
app/llm.py         OpenAI-compatible client (provider agnostic)
app/store.py       settings JSON + SQLite (messages, memories, automations)
app/scheduler.py   recurring job runner
app/whatsapp.py    Cloud API verify/parse/send
web/index.html     chat + settings + memory + notes + automation UI
workspace/         the agent's file sandbox (notes live here)
```

## WhatsApp (optional)

Meta developer app -> WhatsApp -> add the webhook URL `https://<your-host>/webhook/whatsapp`
with your verify token, then set `whatsapp_token` and `whatsapp_phone_id` in Settings.
Put your own number in `whatsapp_allow_from` so only you can talk to it.

## What this is not

It is not the Base44 Superagent. There is no managed billing, no OAuth connector library,
no cloud browser, no sub-agents and no hosted storage. You bring the model key and the host.
Everything else (tools, memory, notes, automations, one channel) runs locally and free.

## Real browser (optional)

```bash
pip install -r requirements-optional.txt
playwright install chromium
```

Then the agent gets 7 more tools: `browser_open`, `browser_read`, `browser_click`,
`browser_type`, `browser_press`, `browser_back`, `browser_close`. It runs headless
Chromium with a persistent profile in `data/browser_profile`, so a login survives restarts.
Screenshots land in `workspace/screens` and are served at `/api/screens/<name>.png`.

### Logging into a site (do this once, by hand)

Never give the agent your password. Instead, on your own computer:

```bash
python -m app.browser_login https://facebook.com
```

A visible browser opens, you log in and finish any 2FA, you close the window, and the
session is saved in `data/browser_profile`. Copy that folder to your server and the agent
reuses the session. `app/browser_login.py` documents the flow.

### Why this is not the same as "log into anything for me"

Facebook, TikTok, Instagram, YouTube and banks fingerprint the browser, see datacenter IPs
and throw captchas, checkpoints or account locks at headless automation. Automating a
personal account also breaks those platforms' terms. The reliable route for those three:

* YouTube -> YouTube Data API v3 with an OAuth token (upload, playlist, analytics).
* Facebook / Instagram -> Meta Graph API with a Page token.
* TikTok -> Content Posting API.

OAuth tokens can be revoked any time; a saved password cannot. Use the browser for reading
public pages, filling forms on small business sites, screenshots and research, and use
provider APIs for posting to your own accounts.

## Media (images, voice, video)

```bash
pip install -r requirements-optional.txt
# ffmpeg is required for video:
sudo apt-get install -y ffmpeg        # Codespaces / Ubuntu
```

* `generate_image("a dental clinic homepage mockup")` -> `workspace/media/img-*.png`
* `text_to_speech("hello")` -> mp3, edge-tts, free, normalised to -16 LUFS
* `make_video([{"title": "...", "text": "...", "image": "img-123.png"}, ...])` -> mp4 with voice
* Files are listed at `/api/media` and served at `/api/media/<name>`

## Connecting any account (OAuth2)

The engine is generic, so a new provider is one dict entry in `app/connectors.py`.

1. Create an app with the provider (Google Cloud, Meta developers, TikTok for developers,
   GitHub developer settings) and copy its client id + secret.
2. `POST /api/connectors/google` `{"client_id": "...", "client_secret": "...", "scope": "..."}`
3. Register the redirect URI `<your-host>/api/callback/google` with the provider.
4. Open `/api/connect/google` in a browser and approve. The token is stored in
   `data/connectors.json` (chmod 600) and refreshed automatically.
5. The agent then calls any endpoint of that account through `connected_call`.

Tokens, not passwords. Revocable at the provider, and worthless if the file leaks and you revoke.

## Where to host it, free

| Option | Cost | Good for | Catch |
|---|---|---|---|
| **Hugging Face Spaces** (Docker SDK) | free | fastest path, drag-and-drop upload, public HTTPS URL | sleeps when idle, ~30s cold start |
| **Oracle Cloud Always Free VM** | free forever | permanent server, your own IP, WhatsApp webhook | one-time SSH setup |
| **GitHub Codespaces** | 60 core-hrs/month | development and testing | URL needs a GitHub login, stops when idle |
| **Render free web service** | free tier | simple git deploy | sleeps, limited hours |

Fastest for a non-terminal person: Hugging Face Space -> **New Space -> Docker -> Blank**,
upload the files from this zip in the web UI, add the env vars in Settings, done.
WhatsApp webhook then points at `https://<space>.hf.space/webhook/whatsapp`.
