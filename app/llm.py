"""Provider-agnostic chat client for any OpenAI-compatible endpoint."""
import httpx

from . import store


class LLMError(RuntimeError):
    pass


def _endpoint(cfg):
    base = (cfg.get("base_url") or "").rstrip("/")
    if not base:
        raise LLMError("No base URL for this provider. Open Settings and pick a provider preset or paste a URL.")
    return base + "/chat/completions"


async def complete(messages, tools=None, temperature=0.4):
    cfg = store.provider_config()
    key = cfg.get("api_key") or ""
    if not key:
        raise LLMError(
            f"No API key for '{cfg['provider']}'. Open Settings, pick the provider and paste its key "
            f"(or set {cfg['provider'].upper()}_API_KEY in .env)."
        )
    if not cfg.get("model"):
        raise LLMError("No model set. Pick a preset model or type any custom model id in Settings.")
    payload = {"model": cfg["model"], "messages": messages, "temperature": temperature}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "HTTP-Referer": "https://redgen.dev", "X-Title": "RedGen Agent"}
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(_endpoint(cfg), json=payload, headers=headers)
    if resp.status_code >= 400:
        raise LLMError(f"{cfg['provider']} {resp.status_code}: {resp.text[:400]}")
    data = resp.json()
    try:
        return data["choices"][0]["message"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"Unexpected response shape: {str(data)[:400]}") from exc
