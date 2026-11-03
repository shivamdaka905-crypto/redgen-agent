"""Generic OAuth2 connector engine.

One JSON file holds provider client credentials and the tokens you granted. Nothing is
hardcoded, so the same code works for Google, Meta, TikTok, GitHub, Slack or anything else
that speaks OAuth2. Tokens are revocable at the provider side.

Flow:
  1. POST /api/connectors/{provider}   {client_id, client_secret, scope, redirect_base}
  2. GET  /api/connect/{provider}      -> redirects to the provider's consent screen
  3. GET  /api/callback/{provider}     -> exchanges the code, stores the token
  4. agent calls `connected_call(provider, url, ...)` and the token is attached for you
"""
import json
import os
import time

import httpx

from . import store

CFG = store.DATA / "connectors.json"

PRESETS = {
    "google": {
        "auth": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "scope": "https://www.googleapis.com/auth/youtube.readonly",
        "extra": {"access_type": "offline", "prompt": "consent"},
    },
    "facebook": {
        "auth": "https://www.facebook.com/v20.0/dialog/oauth",
        "token": "https://graph.facebook.com/v20.0/oauth/access_token",
        "scope": "public_profile",
        "extra": {},
    },
    "tiktok": {
        "auth": "https://www.tiktok.com/v2/auth/authorize/",
        "token": "https://open.tiktokapis.com/v2/oauth/token/",
        "scope": "user.info.basic",
        "extra": {},
    },
    "github": {
        "auth": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "scope": "read:user repo",
        "extra": {},
    },
    "slack": {
        "auth": "https://slack.com/oauth/v2/authorize",
        "token": "https://slack.com/api/oauth.v2.access",
        "scope": "chat:write users:read",
        "extra": {},
    },
}


def _load():
    if CFG.exists():
        try:
            return json.loads(CFG.read_text())
        except Exception:
            pass
    return {}


def _save(data):
    CFG.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(CFG, 0o600)
    except Exception:
        pass


def set_config(provider, client_id, client_secret="", scope="", redirect_base=""):
    provider = provider.lower()
    data = _load()
    entry = data.get(provider, {})
    entry.update({k: v for k, v in {
        "client_id": client_id, "client_secret": client_secret,
        "scope": scope or entry.get("scope"), "redirect_base": redirect_base or entry.get("redirect_base"),
    }.items() if v})
    data[provider] = entry
    _save(data)
    return {"provider": provider, "configured": bool(entry.get("client_id"))}


def status():
    data = _load()
    out = []
    for name, preset in PRESETS.items():
        entry = data.get(name, {})
        out.append({
            "provider": name,
            "configured": bool(entry.get("client_id")),
            "connected": bool(entry.get("access_token")),
            "scope": entry.get("scope") or preset["scope"],
            "expires_at": entry.get("expires_at"),
        })
    return out


def authorize_url(provider, redirect_uri):
    provider = provider.lower()
    preset = PRESETS.get(provider)
    if not preset:
        raise ValueError(f"unknown provider {provider}")
    entry = _load().get(provider, {})
    if not entry.get("client_id"):
        raise ValueError(f"{provider} has no client_id yet: POST /api/connectors/{provider} first")
    import urllib.parse as up

    params = {
        "client_id": entry["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": entry.get("scope") or preset["scope"],
        "state": provider,
        **preset.get("extra", {}),
    }
    return preset["auth"] + "?" + up.urlencode(params)


async def exchange(provider, code, redirect_uri):
    provider = provider.lower()
    preset = PRESETS[provider]
    data = _load()
    entry = data.get(provider, {})
    payload = {
        "client_id": entry.get("client_id"),
        "client_secret": entry.get("client_secret"),
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    headers = {"Accept": "application/json"}
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(preset["token"], data=payload, headers=headers)
    body = r.json()
    token = body.get("access_token") or (body.get("data") or {}).get("access_token")
    if not token:
        raise RuntimeError(f"token exchange failed: {str(body)[:300]}")
    expires_in = body.get("expires_in") or (body.get("data") or {}).get("expires_in")
    entry.update({
        "access_token": token,
        "refresh_token": body.get("refresh_token") or (body.get("data") or {}).get("refresh_token") or entry.get("refresh_token", ""),
        "expires_at": time.time() + float(expires_in) - 60 if expires_in else 0,
        "scope": body.get("scope") or entry.get("scope"),
    })
    data[provider] = entry
    _save(data)
    return {"provider": provider, "connected": True, "scope": entry.get("scope")}


async def access_token(provider):
    provider = provider.lower()
    data = _load()
    entry = data.get(provider)
    if not entry or not entry.get("access_token"):
        return None
    if not entry.get("expires_at") or entry["expires_at"] > time.time():
        return entry["access_token"]
    if not entry.get("refresh_token"):
        return entry["access_token"]
    preset = PRESETS[provider]
    payload = {"client_id": entry.get("client_id"), "client_secret": entry.get("client_secret"),
               "grant_type": "refresh_token", "refresh_token": entry["refresh_token"]}
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(preset["token"], data=payload, headers={"Accept": "application/json"})
    body = r.json()
    token = body.get("access_token") or (body.get("data") or {}).get("access_token")
    if not token:
        return None
    expires_in = body.get("expires_in") or 3600
    entry.update({"access_token": token, "expires_at": time.time() + float(expires_in) - 60})
    data[provider] = entry
    _save(data)
    return token


async def connected_call(provider, url, method="GET", body=None, headers=None):
    token = await access_token(provider)
    if not token:
        return f"{provider} is not connected. Open /api/connect/{provider} and approve access."
    hdrs = {"Accept": "application/json", **(headers or {})}
    if provider == "slack":
        hdrs["Authorization"] = f"Bearer {token}"
    else:
        hdrs["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=90, follow_redirects=True) as c:
        r = await c.request(method.upper(), url, headers=hdrs, json=body)
    return f"HTTP {r.status_code}\n{r.text[:6000]}"


def _spec(name, desc, props, required=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required or []}}}


SCHEMAS = [
    _spec("connectors_status", "Show which accounts are configured and connected.", {}),
    _spec("connected_call",
          "Call any API of a connected account (google, facebook, tiktok, github, slack). "
          "The access token is attached automatically.",
          {"provider": {"type": "string"}, "url": {"type": "string"},
           "method": {"type": "string"}, "body": {"type": "object"}},
          ["provider", "url"]),
]


async def _status_tool():
    return "\n".join(f"{s['provider']}: configured={s['configured']} connected={s['connected']}"
                     for s in status())


HANDLERS = {
    "connectors_status": _status_tool,
    "connected_call": connected_call,
}
