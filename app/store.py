"""Tiny persistence layer: settings JSON + one SQLite file. No ORM, no migrations."""
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
WORKSPACE = ROOT / "workspace"
NOTES = WORKSPACE / "notes"
for p in (DATA, WORKSPACE, NOTES):
    p.mkdir(parents=True, exist_ok=True)

SETTINGS_PATH = DATA / "settings.json"
DB_PATH = DATA / "agent.db"

DEFAULT_SETTINGS = {
    "provider": "openrouter",
    "base_url": "https://openrouter.ai/api/v1",
    "model": "anthropic/claude-sonnet-4.5",
    "api_key": "",
    "owner_name": "Shivam",
    "business": "RED GEN DEVELOPMENT",
    "system_prompt": (
        "You are a personal AI agent. You take ownership of the task, use your tools "
        "to actually finish the work instead of only explaining it, and report the "
        "verified result in a short, friendly message. Ask only when a decision is "
        "blocked on a human. Never invent facts."
    ),
    "max_tool_rounds": 8,
    "active_provider": "openrouter",
    "providers": {},
    "whatsapp_token": "",
    "whatsapp_phone_id": "",
    "whatsapp_verify_token": "redgen-verify",
    "whatsapp_allow_from": "",
}

PROVIDER_PRESETS = {
    "openrouter": {"base_url": "https://openrouter.ai/api/v1",
                   "model": "anthropic/claude-sonnet-4.5",
                   "label": "OpenRouter (one key, every model)"},
    "groq": {"base_url": "https://api.groq.com/openai/v1",
             "model": "llama-3.3-70b-versatile",
             "label": "Groq (fast, free tier)"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "model": "gemini-2.0-flash",
               "label": "Google Gemini (OpenAI-compatible endpoint)"},
    "custom": {"base_url": "", "model": "",
               "label": "Custom / self-hosted (any OpenAI-compatible URL)"},
}

CAPABILITIES = [
    {"name": "chat", "what": "Talk in natural language, remember context per conversation"},
    {"name": "tools", "what": "Call functions (files, shell, web, notes, memory, HTTP, scheduling)"},
    {"name": "memory", "what": "Save durable facts and read them into every system prompt"},
    {"name": "notes", "what": "Markdown knowledge base in workspace/notes"},
    {"name": "automations", "what": "Recurring prompts executed by an internal scheduler"},
    {"name": "channels", "what": "WhatsApp Cloud API webhook in and out; HTTP API for anything else"},
    {"name": "web", "what": "Fetch pages and run web searches"},
]


def load_settings():
    if SETTINGS_PATH.exists():
        try:
            stored = json.loads(SETTINGS_PATH.read_text())
        except Exception:
            stored = {}
    else:
        stored = {}
    out = dict(DEFAULT_SETTINGS)
    out.update({k: v for k, v in stored.items() if v is not None})
    for key, env in (("api_key", "LLM_API_KEY"), ("whatsapp_token", "WHATSAPP_TOKEN"),
                     ("whatsapp_phone_id", "WHATSAPP_PHONE_ID")):
        if not out.get(key) and os.getenv(env):
            out[key] = os.getenv(env)
    if os.getenv("LLM_BASE_URL"):
        out["base_url"] = os.getenv("LLM_BASE_URL")
    if os.getenv("LLM_MODEL"):
        out["model"] = os.getenv("LLM_MODEL")
    return out


def _store():
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except Exception:
            return {}
    return {}


def _write(raw):
    tmp = SETTINGS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, indent=2))
    tmp.replace(SETTINGS_PATH)
    try:
        os.chmod(SETTINGS_PATH, 0o600)
    except Exception:
        pass


def save_settings(patch):
    """Known top-level keys land directly; provider keys land inside providers[active]."""
    raw = _store()
    patch = dict(patch or {})
    active = patch.pop("active_provider", None) or raw.get("active_provider") or DEFAULT_SETTINGS["active_provider"]
    raw["active_provider"] = active
    providers = raw.get("providers") or {}
    entry = dict(providers.get(active) or {})
    for key in ("api_key", "base_url", "model"):
        if key in patch and patch[key] != "":
            entry[key] = patch.pop(key)
    providers[active] = entry
    raw["providers"] = providers
    for key, value in patch.items():
        if key in DEFAULT_SETTINGS:
            raw[key] = value
    _write(raw)
    return load_settings()


def provider_config(name=None):
    """Resolved config for the active (or named) provider, with env fallbacks."""
    s = load_settings()
    name = name or s.get("active_provider") or "openrouter"
    preset = PROVIDER_PRESETS.get(name, {})
    entry = (s.get("providers") or {}).get(name, {})
    base = entry.get("base_url") or preset.get("base_url") or s.get("base_url") or ""
    model = entry.get("model") or preset.get("model") or s.get("model") or ""
    key = entry.get("api_key") or s.get("api_key") or ""
    if not key:
        env_name = f"{name.upper()}_API_KEY"
        key = os.getenv(env_name, "") or (os.getenv("LLM_API_KEY", "") if name == s.get("active_provider") else "")
    if not base:
        base = s.get("base_url") or os.getenv("LLM_BASE_URL", "")
    if not model:
        model = s.get("model") or os.getenv("LLM_MODEL", "")
    return {"provider": name, "base_url": base.rstrip("/"), "model": model, "api_key": key}


def public_settings():
    s = load_settings()
    active = provider_config()
    providers = {}
    for name, entry in (s.get("providers") or {}).items():
        providers[name] = {k: ("***set***" if k == "api_key" and v else v) for k, v in entry.items()}
    return {
        "active_provider": active["provider"],
        "base_url": active["base_url"],
        "model": active["model"],
        "api_key": "***set***" if active["api_key"] else "",
        "providers": providers,
        "presets": {k: {"base_url": v["base_url"], "model": v["model"], "label": v["label"]}
                    for k, v in PROVIDER_PRESETS.items()},
        "query": "Use whichever of OpenRouter / Groq / Gemini / custom you save a key for; the active one drives every task. Switch any time, or type any model id you like.",
    }


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              conversation TEXT NOT NULL,
              role TEXT NOT NULL,
              content TEXT NOT NULL,
              ts REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memories (
              id TEXT PRIMARY KEY,
              content TEXT NOT NULL,
              kind TEXT NOT NULL DEFAULT 'fact',
              category TEXT NOT NULL DEFAULT 'General',
              ts REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS automations (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              prompt TEXT NOT NULL,
              every_minutes INTEGER NOT NULL,
              channel TEXT NOT NULL DEFAULT 'chat',
              target TEXT NOT NULL DEFAULT '',
              enabled INTEGER NOT NULL DEFAULT 1,
              next_run REAL NOT NULL,
              last_result TEXT NOT NULL DEFAULT '',
              ts REAL NOT NULL
            );
            """
        )


def add_message(conversation, role, content):
    with conn() as c:
        c.execute("INSERT INTO messages (conversation, role, content, ts) VALUES (?,?,?,?)",
                  (conversation, role, content, time.time()))


def get_messages(conversation, limit=24):
    with conn() as c:
        rows = c.execute(
            "SELECT role, content FROM messages WHERE conversation=? ORDER BY id DESC LIMIT ?",
            (conversation, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def add_memory(content, kind="fact", category="General"):
    mid = uuid.uuid4().hex[:16]
    with conn() as c:
        c.execute("INSERT INTO memories (id, content, kind, category, ts) VALUES (?,?,?,?,?)",
                  (mid, content, kind, category, time.time()))
    return {"id": mid, "content": content, "kind": kind, "category": category}


def list_memories(limit=100):
    with conn() as c:
        rows = c.execute("SELECT * FROM memories ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def search_memories(query, limit=20):
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM memories WHERE content LIKE ? ORDER BY ts DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_memory(mid):
    with conn() as c:
        c.execute("DELETE FROM memories WHERE id=?", (mid,))


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or uuid.uuid4().hex[:8]


def note_write(title, body):
    path = NOTES / f"{slug(title)}.md"
    path.write_text(f"# {title}\n\n{body.strip()}\n")
    return path.name


def note_list():
    return sorted(p.name for p in NOTES.glob("*.md"))


def note_read(name):
    if not name.endswith(".md"):
        name += ".md"
    path = NOTES / name
    return path.read_text() if path.exists() else ""


def add_automation(name, prompt, every_minutes, channel="chat", target=""):
    aid = uuid.uuid4().hex[:12]
    with conn() as c:
        c.execute(
            "INSERT INTO automations (id,name,prompt,every_minutes,channel,target,enabled,next_run,ts)"
            " VALUES (?,?,?,?,?,?,1,?,?)",
            (aid, name, prompt, int(every_minutes), channel, target, time.time() + int(every_minutes) * 60, time.time()),
        )
    return aid


def list_automations():
    with conn() as c:
        rows = c.execute("SELECT * FROM automations ORDER BY ts DESC").fetchall()
    return [dict(r) for r in rows]


def due_automations(now=None):
    now = now or time.time()
    with conn() as c:
        rows = c.execute("SELECT * FROM automations WHERE enabled=1 AND next_run<=?", (now,)).fetchall()
    return [dict(r) for r in rows]


def finish_automation(aid, result, every_minutes):
    with conn() as c:
        c.execute("UPDATE automations SET last_result=?, next_run=? WHERE id=?",
                  (result[-4000:], time.time() + int(every_minutes) * 60, aid))


def delete_automation(aid):
    with conn() as c:
        c.execute("DELETE FROM automations WHERE id=?", (aid,))
