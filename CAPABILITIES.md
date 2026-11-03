# What the Superagent does, and how this clone covers it

I analysed my own tool set and split it into what can be rebuilt in your own code and what
needs someone else's infrastructure.

## Rebuilt here (works standalone, free)

1. **Conversation** - multi-turn chat with per-conversation history. -> `app/agent.py`, `app/store.py`
2. **Tool use** - the model chooses tools; results feed back into the loop until the task is done. -> `app/tools.py`
3. **File work** - read, write, list inside a sandbox. -> `read_file`, `write_file`, `list_files`
4. **Code execution** - shell commands with a timeout. -> `run_shell`
5. **Web** - page fetch and search. -> `web_fetch`, `web_search`
6. **Memory** - durable facts saved on request and loaded into every prompt. -> `save_memory`, `search_memory`
7. **Knowledge notes** - markdown knowledge base the agent edits itself. -> `notes_*`
8. **Automations** - "every N minutes run this prompt", including delivery. -> `app/scheduler.py`
9. **Messaging channel** - WhatsApp Cloud API in and out. -> `app/whatsapp.py`
10. **External APIs** - any JSON API or backend function call. -> `http_request`
11. **Settings UI** - provider, model, key, identity, channel tokens. -> `web/index.html`
12. **Media generation** - images via an OpenAI-compatible images endpoint, voice via edge-tts,
    video via ffmpeg scene assembly. -> `app/media.py`
13. **Connected accounts** - generic OAuth2 engine with presets for Google, Meta, TikTok,
    GitHub, Slack, plus `connected_call` to use any endpoint of a connected account. -> `app/connectors.py`
14. **YouTube data** - search, channel uploads, video stats. -> `app/youtube.py`
15. **Self-description** - `GET /api/capabilities` returns what it can do.

## Needs external infrastructure (cannot be rebuilt in one repo)

- **Managed credits/billing** (Base44) - you use provider keys directly instead.
- **OAuth connector library** (Gmail, Slack, Calendar, HubSpot) - each needs its own app
  registration and token flow. Start with `http_request` + the provider's REST API.
- **Cloud browser** with stealth + residential proxies - my version adds 7 `browser_*` tools
  (Playwright Chromium, persistent profile, screenshots). It handles real clicks and typing,
  but sites like Facebook, TikTok and YouTube fingerprint datacenter IPs and block headless
  automation, so account-login automation is unreliable and against their terms. Use provider
  OAuth APIs for publishing instead; keep the browser for public pages, research and forms.
- **Sub-agents / long-running goals** - add a queue table and spawn N loops; the pattern is
  the same loop function with a different system prompt.
- **Hosted file storage + CDN** - use the host's disk or an S3-compatible bucket.
- **Voice calls** - needs a telephony provider (Twilio / Exotel); wire it as another webhook.

## Practical build order

1. Get chat + memory working (1 file each).
2. Add the five tools you actually use daily: shell, web fetch, search, write file, save memory.
3. Add automations, because that is what makes it feel alive.
4. Add WhatsApp so you can use it from your phone.
5. Only then add connectors, sub-agents, browser automation.
