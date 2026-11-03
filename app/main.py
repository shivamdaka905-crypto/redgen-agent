"""HTTP surface: chat UI, JSON API, WhatsApp webhook, scheduler boot."""
import asyncio
import contextlib
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from . import agent, connectors, scheduler, store, whatsapp

WEB = Path(__file__).resolve().parent.parent / "web"
app = FastAPI(title="RedGen Agent", version="0.1.0")
_task = None


@app.on_event("startup")
async def _startup():
    global _task
    store.init_db()
    _task = asyncio.create_task(scheduler.loop())


@app.on_event("shutdown")
async def _shutdown():
    if _task:
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _task


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/health")
async def health():
    s = store.load_settings()
    return {"ok": True, "model": s.get("model"), "key_set": bool(s.get("api_key")),
            "automations": len(store.list_automations())}


@app.get("/api/capabilities")
async def capabilities():
    return {"capabilities": store.CAPABILITIES}


class ChatIn(BaseModel):
    message: str
    conversation_id: str | None = None


@app.post("/api/chat")
async def chat(payload: ChatIn):
    try:
        reply = await agent.run_turn(payload.conversation_id or uuid.uuid4().hex[:10], payload.message)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"reply": reply}


@app.get("/api/settings")
async def get_settings():
    return store.public_settings()


@app.post("/api/settings")
async def post_settings(patch: dict):
    return store.save_settings(patch)


@app.get("/api/memory")
async def get_memory(q: str | None = None):
    return {"memories": store.search_memories(q) if q else store.list_memories()}


@app.post("/api/memory")
async def post_memory(body: dict):
    return store.add_memory(body.get("content", ""), body.get("kind", "fact"), body.get("category", "General"))


@app.delete("/api/memory/{mid}")
async def delete_memory(mid: str):
    store.delete_memory(mid)
    return {"ok": True}


@app.get("/api/notes")
async def notes():
    return {"notes": store.note_list()}


@app.get("/api/notes/{name}")
async def note(name: str):
    return {"name": name, "body": store.note_read(name)}


@app.post("/api/notes")
async def write_note(body: dict):
    return {"saved": store.note_write(body.get("title", "note"), body.get("body", ""))}


@app.get("/api/automations")
async def automations():
    return {"automations": store.list_automations()}


@app.post("/api/automations")
async def add_automation(body: dict):
    aid = store.add_automation(body.get("name", "job"), body.get("prompt", ""),
                               int(body.get("every_minutes", 60)), body.get("channel", "chat"),
                               body.get("target", ""))
    return {"id": aid}


@app.delete("/api/automations/{aid}")
async def drop_automation(aid: str):
    store.delete_automation(aid)
    return {"ok": True}


@app.get("/webhook/whatsapp")
async def wa_verify(request: Request):
    q = request.query_params
    ok = whatsapp.verify(q.get("hub.mode"), q.get("hub.verify_token"), q.get("hub.challenge"),
                         store.load_settings().get("whatsapp_verify_token"))
    if not ok:
        raise HTTPException(status_code=403, detail="verification failed")
    return PlainTextResponse(ok)


@app.post("/webhook/whatsapp")
async def wa_inbound(request: Request):
    body = await request.json()
    allowed = (store.load_settings().get("whatsapp_allow_from") or "").replace(" ", "")
    for sender, text in whatsapp.parse(body):
        if allowed and sender not in allowed.split(","):
            continue
        reply = await agent.run_turn(f"wa:{sender}", text)
        await whatsapp.send_text(sender, reply)
    return {"ok": True}


SCREENS = Path(__file__).resolve().parent.parent / "workspace" / "screens"


@app.get("/api/screens")
async def screens():
    if not SCREENS.exists():
        return {"screens": []}
    files = sorted(SCREENS.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    return {"screens": [f.name for f in files[:50]]}


@app.get("/api/screens/{name}")
async def screen(name: str):
    path = SCREENS / name
    if not path.exists() or path.suffix != ".png":
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path)


@app.get("/api/connectors")
async def list_connectors():
    return {"providers": connectors.status()}


@app.post("/api/connectors/{provider}")
async def configure_connector(provider: str, body: dict):
    return connectors.set_config(provider, body.get("client_id", ""), body.get("client_secret", ""),
                                 body.get("scope", ""), body.get("redirect_base", ""))


@app.get("/api/connect/{provider}")
async def connect(provider: str, request: Request):
    from fastapi.responses import RedirectResponse

    base = str(request.base_url).rstrip("/")
    url = connectors.authorize_url(provider, f"{base}/api/callback/{provider}")
    return RedirectResponse(url)


@app.get("/api/callback/{provider}")
async def callback(provider: str, request: Request):
    from fastapi.responses import HTMLResponse

    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="missing code")
    base = str(request.base_url).rstrip("/")
    try:
        result = await connectors.exchange(provider, code, f"{base}/api/callback/{provider}")
    except Exception as exc:
        return HTMLResponse(f"<h3>{provider} connect failed</h3><pre>{exc}</pre>", status_code=400)
    return HTMLResponse(f"<h3>{provider} connected</h3><pre>{result}</pre>"
                        "<p>You can close this tab.</p>")


@app.get("/api/media")
async def media_index():
    from .media import MEDIA

    files = sorted(MEDIA.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)[:60]
    return {"files": [f.name for f in files]}


@app.get("/api/media/{name}")
async def media_file(name: str):
    from .media import MEDIA

    path = MEDIA / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path)
