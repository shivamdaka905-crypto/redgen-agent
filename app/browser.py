"""Real browser control (Playwright), optional: agent falls back gracefully if not installed.

Design notes
------------
* One persistent Chromium profile in data/browser_profile, so a login done once is reused.
* Every action returns text + a screenshot path the UI can show.
* Nothing here bypasses a site's login, captcha or 2FA: it drives a normal browser.
"""
import asyncio
import time
from pathlib import Path

from . import store

PROFILE = store.DATA / "browser_profile"
SCREENS = store.WORKSPACE / "screens"
PROFILE.mkdir(parents=True, exist_ok=True)
SCREENS.mkdir(parents=True, exist_ok=True)

_state = {"pw": None, "ctx": None, "page": None}
_lock = asyncio.Lock()


def _need_playwright():
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: pip install playwright && playwright install chromium"
        ) from exc


async def _page():
    _need_playwright()
    async with _lock:
        if _state["page"] is None or _state["page"].is_closed():
            from playwright.async_api import async_playwright
            _state["pw"] = _state["pw"] or await async_playwright().start()
            _state["ctx"] = _state["ctx"] or await _state["pw"].chromium.launch_persistent_context(
                str(PROFILE),
                headless=True,
                viewport={"width": 1366, "height": 900},
                args=["--no-sandbox", "--disable-dev-shm-usage"],
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
            )
            pages = _state["ctx"].pages
            _state["page"] = pages[0] if pages else await _state["ctx"].new_page()
        return _state["page"]


async def _shot(page, label="shot"):
    name = f"{label}-{int(time.time())}.png"
    path = SCREENS / name
    await page.screenshot(path=str(path), full_page=False)
    return name


async def open_url(url, wait_ms=2500):
    page = await _page()
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(wait_ms)
    shot = await _shot(page, "open")
    title = await page.title()
    text = (await page.inner_text("body"))[:4000]
    return f"title: {title}\nurl: {page.url}\nscreenshot: {shot}\n\n{text}"


async def read_page(selector=None, limit=6000):
    page = await _page()
    text = await (page.inner_text(selector) if selector else page.inner_text("body"))
    return f"url: {page.url}\n\n{text[:limit]}"


async def click(selector):
    page = await _page()
    await page.click(selector, timeout=20000)
    await page.wait_for_timeout(1200)
    shot = await _shot(page, "click")
    return f"clicked {selector}\nurl: {page.url}\nscreenshot: {shot}"


async def type_text(selector, text, submit=False):
    page = await _page()
    await page.fill(selector, text)
    if submit:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(1500)
    shot = await _shot(page, "type")
    return f"typed into {selector}\nurl: {page.url}\nscreenshot: {shot}"


async def press(keys):
    page = await _page()
    await page.keyboard.press(keys)
    await page.wait_for_timeout(1200)
    shot = await _shot(page, "key")
    return f"pressed {keys}\nurl: {page.url}\nscreenshot: {shot}"


async def go_back():
    page = await _page()
    await page.go_back()
    await page.wait_for_timeout(1200)
    return f"url: {page.url}"


THUM_FALLBACK = "https://image.thum.io/get/width/1280/crop/900/noanimate/{url}"


async def _fallback_shot(url):
    """No Playwright? Still take a real screenshot through a public render service."""
    import time as _t
    import httpx
    shots = store.WORKSPACE / "screens"
    shots.mkdir(parents=True, exist_ok=True)
    name = f"shot-{int(_t.time() * 1000)}.png"
    async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
        resp = await client.get(THUM_FALLBACK.format(url=url))
        resp.raise_for_status()
        (shots / name).write_bytes(resp.content)
    return {"screenshot": f"screens/{name}", "url": f"/api/screens/{name}", "engine": "render-service"}


async def browse_page(url, chars=5000):
    """Read a page and screenshot it. Works with or without a local browser."""
    import json as _json
    import re
    import httpx
    text, title = "", ""
    try:
        async with httpx.AsyncClient(timeout=45, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0 (RedGenAgent)"}) as client:
            resp = await client.get(url)
            html = resp.text
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        title = (m.group(1).strip() if m else "").strip()
        html = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()[:chars]
    except Exception as exc:
        text = f"fetch failed: {exc}"
    try:
        shot = await screenshot_url(url)
    except Exception as exc:
        shot = {"error": f"screenshot failed: {exc}"}
    return _json.dumps({"title": title, "url": url, "screenshot": shot, "text": text}, ensure_ascii=False)


async def screenshot_url(url):
    """One-shot: open a URL, wait for it to settle, keep the screenshot."""
    try:
        _need_playwright()
    except Exception:
        return await _fallback_shot(url)
    page = await _page()
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2500)
    shot = await _shot(page, "shot")
    return f"screenshot: {shot}\nurl: {page.url}\ntitle: {await page.title()}"


async def close():
    if _state["ctx"]:
        await _state["ctx"].close()
    if _state["pw"]:
        await _state["pw"].stop()
    _state.update({"pw": None, "ctx": None, "page": None})
    return "browser closed"


def _spec(name, desc, props, required=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required or []}}}


SCHEMAS = [
    _spec("browser_open", "Open a URL in a real Chromium browser and return the page text plus a screenshot.",
          {"url": {"type": "string"}, "wait_ms": {"type": "integer"}}, ["url"]),
    _spec("browser_read", "Read text from the current browser page, or from one CSS selector.",
          {"selector": {"type": "string"}}),
    _spec("browser_click", "Click an element by CSS selector in the current page.",
          {"selector": {"type": "string"}}, ["selector"]),
    _spec("browser_type", "Type into an input by CSS selector, optionally pressing Enter.",
          {"selector": {"type": "string"}, "text": {"type": "string"}, "submit": {"type": "boolean"}},
          ["selector", "text"]),
    _spec("browser_press", "Press a key or chord, e.g. Enter, Tab, Control+A.", {"keys": {"type": "string"}}, ["keys"]),
    _spec("browser_back", "Go back one page.", {}),
    _spec("browser_close", "Close the browser and free memory.", {}),
    _spec("browse_page", "Read a page (title + text) AND save its screenshot. Use this first when looking at a site.",
          {"url": {"type": "string"}, "chars": {"type": "number"}}, ["url"]),
    _spec("screenshot_url", "Open a URL and save a screenshot of it.",
          {"url": {"type": "string"}}, ["url"]),
]

HANDLERS = {
    "browser_open": open_url,
    "browser_read": read_page,
    "browser_click": click,
    "browser_type": type_text,
    "browser_press": press,
    "browser_back": go_back,
    "browser_close": close,
    "browse_page": browse_page,
    "screenshot_url": screenshot_url,
}
