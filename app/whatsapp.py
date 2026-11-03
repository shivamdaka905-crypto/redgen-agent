"""WhatsApp Cloud API: webhook verification, inbound parsing, outbound send."""
import httpx

from . import store

GRAPH = "https://graph.facebook.com/v20.0"


def verify(mode, token, challenge, expected):
    if mode == "subscribe" and token == expected:
        return challenge
    return None


def parse(payload):
    """Return list of (from_number, text) from a Cloud API webhook body."""
    out = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            for msg in value.get("messages", []) or []:
                if msg.get("type") == "text":
                    out.append((msg.get("from", ""), (msg.get("text") or {}).get("body", "")))
    return out


async def send_text(to, body):
    s = store.load_settings()
    token, phone_id = s.get("whatsapp_token"), s.get("whatsapp_phone_id")
    if not (token and phone_id and to):
        return False
    url = f"{GRAPH}/{phone_id}/messages"
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body[:4000]}}
    async with httpx.AsyncClient(timeout=45) as c:
        r = await c.post(url, json=payload, headers={"Authorization": f"Bearer {token}"})
    return r.status_code < 300
