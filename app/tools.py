"""Tool registry. One schema, one handler, no framework needed."""
import asyncio
import json
import re

import httpx

from . import store

MAX_OUT = 8000


def _spec(name, desc, props, required=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required or []},
        },
    }


SCHEMAS = [
    _spec("list_files", "List files in the agent workspace (optionally inside a subfolder).",
          {"path": {"type": "string", "description": "Relative folder, default '.'"}}),
    _spec("read_file", "Read a text file from the workspace.",
          {"path": {"type": "string"}}, ["path"]),
    _spec("write_file", "Create or overwrite a text file in the workspace.",
          {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    _spec("run_shell", "Run a shell command in the workspace (bash, 60s timeout).",
          {"command": {"type": "string"}, "timeout": {"type": "integer"}}, ["command"]),
    _spec("web_fetch", "Download a web page or API response and return its readable text.",
          {"url": {"type": "string"}}, ["url"]),
    _spec("web_search", "Search the web and return titles, links and snippets.",
          {"query": {"type": "string"}}, ["query"]),
    _spec("save_memory", "Save one durable fact about the user or the business.",
          {"content": {"type": "string"}, "kind": {"type": "string", "enum": ["fact", "preference", "instruction", "decision"]},
           "category": {"type": "string"}}, ["content"]),
    _spec("search_memory", "Search saved memories by keyword.",
          {"query": {"type": "string"}}, ["query"]),
    _spec("notes_write", "Save or update a markdown note in the knowledge base.",
          {"title": {"type": "string"}, "body": {"type": "string"}}, ["title", "body"]),
    _spec("notes_list", "List note filenames.", {}),
    _spec("notes_read", "Read one note by filename.", {"name": {"type": "string"}}, ["name"]),
    _spec("http_request", "Call an external JSON HTTP API (any method).",
          {"url": {"type": "string"}, "method": {"type": "string"}, "headers": {"type": "object"},
           "body": {"type": "object"}}, ["url"]),
    _spec("schedule_automation", "Create a recurring job: the prompt runs every N minutes.",
          {"name": {"type": "string"}, "prompt": {"type": "string"},
           "every_minutes": {"type": "integer"}, "channel": {"type": "string", "enum": ["chat", "whatsapp"]},
           "target": {"type": "string", "description": "WhatsApp number for channel=whatsapp"}},
          ["name", "prompt", "every_minutes"]),
    _spec("list_automations", "List scheduled jobs.", {}),
    _spec("delete_automation", "Delete a scheduled job by id.", {"id": {"type": "string"}}, ["id"]),
]


def _safe_path(path):
    target = (store.WORKSPACE / (path or ".")).resolve()
    if store.WORKSPACE.resolve() not in target.parents and target != store.WORKSPACE.resolve():
        raise ValueError("Outside workspace")
    return target


async def _list_files(path="."):
    target = _safe_path(path)
    if not target.exists():
        return f"{path} does not exist"
    rows = [f"{p.relative_to(store.WORKSPACE)}" for p in sorted(target.rglob('*')) if p.is_file()]
    return "\n".join(rows[:400]) or "(empty)"


async def _read_file(path):
    target = _safe_path(path)
    return target.read_text(errors="replace")[:MAX_OUT] if target.exists() else f"{path} not found"


async def _write_file(path, content):
    target = _safe_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return f"wrote {len(content)} chars to {path}"


async def _run_shell(command, timeout=60):
    proc = await asyncio.create_subprocess_shell(
        command, cwd=str(store.WORKSPACE),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return "timed out"
    return out.decode(errors="replace")[:MAX_OUT] or "(no output)"


def _strip_html(html):
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


async def _web_fetch(url):
    async with httpx.AsyncClient(timeout=45, follow_redirects=True,
                                 headers={"User-Agent": "Mozilla/5.0 redgen-agent"}) as c:
        r = await c.get(url)
    body = _strip_html(r.text)[:MAX_OUT] if "html" in r.headers.get("content-type", "") else r.text[:MAX_OUT]
    return f"HTTP {r.status_code}\n{body}"


async def _web_search(query):
    async with httpx.AsyncClient(timeout=45, follow_redirects=True,
                                 headers={"User-Agent": "Mozilla/5.0 redgen-agent"}) as c:
        r = await c.get("https://html.duckduckgo.com/html/", params={"q": query})
    hits = re.findall(r'result__a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S)
    out = []
    for href, title in hits[:8]:
        out.append(f"- {_strip_html(title)}\n  {href}")
    return "\n".join(out) or "no results (search engine may be blocking)"


async def _save_memory(content, kind="fact", category="General"):
    rec = store.add_memory(content, kind, category)
    return f"saved memory {rec['id']}"


async def _search_memory(query):
    rows = store.search_memories(query)
    return "\n".join(f"[{r['category']}] {r['content']}" for r in rows) or "nothing found"


async def _notes_write(title, body):
    return f"saved note {store.note_write(title, body)}"


async def _notes_list():
    return "\n".join(store.note_list()) or "(no notes)"


async def _notes_read(name):
    return store.note_read(name)[:MAX_OUT] or "note not found"


async def _http_request(url, method="GET", headers=None, body=None):
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.request(method.upper(), url, headers=headers or {}, json=body)
    return f"HTTP {r.status_code}\n{r.text[:MAX_OUT]}"


async def _schedule_automation(name, prompt, every_minutes, channel="chat", target=""):
    aid = store.add_automation(name, prompt, int(every_minutes), channel, target)
    return f"scheduled '{name}' every {every_minutes} min (id {aid})"


async def _list_automations():
    rows = store.list_automations()
    return "\n".join(f"{r['id']} · {r['name']} · every {r['every_minutes']}m · {r['channel']}"
                     for r in rows) or "(none)"


async def _delete_automation(id):
    store.delete_automation(id)
    return "deleted"


HANDLERS = {
    "list_files": _list_files,
    "read_file": _read_file,
    "write_file": _write_file,
    "run_shell": _run_shell,
    "web_fetch": _web_fetch,
    "web_search": _web_search,
    "save_memory": _save_memory,
    "search_memory": _search_memory,
    "notes_write": _notes_write,
    "notes_list": _notes_list,
    "notes_read": _notes_read,
    "http_request": _http_request,
    "schedule_automation": _schedule_automation,
    "list_automations": _list_automations,
    "delete_automation": _delete_automation,
}


async def call(name, args):
    handler = HANDLERS.get(name)
    if handler is None:
        return f"unknown tool {name}"
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except json.JSONDecodeError:
            args = {}
    try:
        return str(await handler(**(args or {})))
    except TypeError as exc:
        return f"bad arguments for {name}: {exc}"
    except Exception as exc:  # tools must never kill the loop
        return f"{name} failed: {exc}"


for _mod_name in ("media", "connectors", "youtube"):
    try:  # pragma: no cover
        _mod = __import__(f"{__package__}.{_mod_name}", fromlist=["SCHEMAS", "HANDLERS"])
        _MEDIA_SCHEMAS = getattr(_mod, "SCHEMAS", [])
        _MEDIA_HANDLERS = getattr(_mod, "HANDLERS", {})
        SCHEMAS.extend(_MEDIA_SCHEMAS)
        HANDLERS.update(_MEDIA_HANDLERS)
    except Exception as _mod_error:  # pragma: no cover
        import sys as _sys2

        print(f"[tools] {_mod_name} tools unavailable: {_mod_error}", file=_sys2.stderr)

# Optional browser tools. Kept in their own module so the agent still runs without
# Playwright installed; see app/browser.py and app/browser_login.py.
try:  # pragma: no cover
    from .browser import SCHEMAS as _B_SCHEMAS, HANDLERS as _B_HANDLERS

    SCHEMAS.extend(_B_SCHEMAS)
    HANDLERS.update(_B_HANDLERS)
except Exception as _browser_error:  # pragma: no cover
    import sys as _sys

    print(f"[tools] browser tools unavailable: {_browser_error}", file=_sys.stderr)

try:  # parallel research + PDF tools
    from .research import SCHEMAS as _R_SCHEMAS, HANDLERS as _R_HANDLERS

    SCHEMAS.extend(_R_SCHEMAS)
    HANDLERS.update(_R_HANDLERS)
except Exception as _research_error:  # pragma: no cover
    import sys as _sys2

    print(f"[tools] research tools unavailable: {_research_error}", file=_sys2.stderr)
