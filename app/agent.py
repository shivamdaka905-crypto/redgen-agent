"""The agent loop: system prompt + memory -> model -> tools -> answer.

Every step is written to the event log so the UI can show live activity:
what tool ran, with which arguments, and what came back (including screenshots).
"""
import asyncio
import json

from . import llm, store, tools

TOOL_TIMEOUT = 150
PREVIEW_TOOLS = {"screenshot_url", "browse_page", "browser_open", "browser_click", "browser_type"}


def build_system_prompt():
    s = store.load_settings()
    memories = store.list_memories(60)
    mem_block = "\n".join(f"- ({m['category']}) {m['content']}" for m in memories) or "- (nothing saved yet)"
    notes = ", ".join(store.note_list()) or "(none)"
    return (
        f"{s['system_prompt']}\n\n"
        f"Owner: {s['owner_name']}. Business: {s['business']}.\n"
        f"Saved memory about the owner:\n{mem_block}\n\n"
        f"Knowledge notes available: {notes}\n"
        "Guidelines: prefer acting over asking; use tools for anything factual, numeric or external; "
        "keep the final answer short and lead with the result; save durable facts with save_memory when the "
        "owner states a lasting preference; never invent data. "
        "When you look at a website, call browse_page (text + screenshot) so the owner can see the page too. "
        "For local business lists (clinics, gyms, salons...), call lead_finder with the city instead of guessing; "
        "it scans the map, fills phone numbers, and returns a PDF + CSV link the owner can download. "
        "Never claim a business has no website unless the data says so."
    )


def _short(value, limit=1400):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text[:limit]


async def run_turn(conversation, user_text, max_rounds=None):
    s = store.load_settings()
    rounds = int(max_rounds or s.get("max_tool_rounds") or 8)
    store.set_conversation(conversation)
    store.add_message(conversation, "user", user_text)
    store.add_event(conversation, "think", "reading context", "")
    history = store.get_messages(conversation, 24)
    messages = [{"role": "system", "content": build_system_prompt()}] + history

    for _ in range(rounds):
        message = await llm.complete(messages, tools=tools.SCHEMAS)
        calls = message.get("tool_calls") or []
        if not calls:
            text = (message.get("content") or "").strip()
            store.add_message(conversation, "assistant", text)
            store.add_event(conversation, "answer", "reply", text[:400])
            return text
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
        for call in calls:
            fn = call.get("function", {})
            name = fn.get("name")
            raw_args = fn.get("arguments")
            store.add_event(conversation, "tool", name, _short(raw_args, 600))
            try:
                result = await asyncio.wait_for(tools.call(name, raw_args), timeout=TOOL_TIMEOUT)
            except Exception as exc:  # never kill the turn because one tool failed
                result = f"{name} failed: {exc}"
            store.add_event(conversation, "result", name, _short(result))
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id"),
                "content": result[:8000],
            })
    fallback = "I stopped after too many tool rounds. Tell me the next single step."
    store.add_message(conversation, "assistant", fallback)
    store.add_event(conversation, "answer", "reply", fallback)
    return fallback
