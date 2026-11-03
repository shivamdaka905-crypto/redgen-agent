"""One-time manual login helper.

Run this on YOUR OWN computer (it opens a visible browser):
    pip install playwright && playwright install chromium
    python -m app.browser_login https://facebook.com

Log in by hand, close the window. The profile in data/browser_profile now holds the
session, so the agent can reuse it. Copy that folder to your server if you host elsewhere.

Never share your password with the agent. If a site shows 2FA, finish it yourself here.
"""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

PROFILE = Path(__file__).resolve().parent.parent / "data" / "browser_profile"


async def main(url):
    PROFILE.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(str(PROFILE), headless=False)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(url)
        print("Log in in the open window, then close it.")
        await ctx.wait_for_event("close", timeout=0)
        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "https://example.com"))
