"""Repeating jobs: an asyncio poller instead of a cron dependency."""
import asyncio
import traceback

from . import agent, store, whatsapp


async def _run_one(job):
    conversation = f"auto:{job['id']}"
    try:
        result = await agent.run_turn(conversation, job["prompt"])
    except Exception:
        result = "automation failed:\n" + traceback.format_exc()[-1200:]
    store.finish_automation(job["id"], result, job["every_minutes"])
    if job["channel"] == "whatsapp" and job["target"]:
        await whatsapp.send_text(job["target"], result)


async def loop(interval=20):
    while True:
        try:
            for job in store.due_automations():
                await _run_one(job)
        except Exception:
            traceback.print_exc()
        await asyncio.sleep(interval)
