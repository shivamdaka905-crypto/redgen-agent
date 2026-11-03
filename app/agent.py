"""The agent loop: system prompt + memory -> model -> tools -> answer."""
import json

from . import llm, store, tools


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
        "owner states a lasting preference; never invent data."
    )


async def run_turn(conversation, user_text, max_rounds=None):
    s = store.load_settings()
    rounds = int(max_rounds or s.get("max_tool_rounds") or 8)
    store.add_message(conversation, "user", user_text)
    history = store.get_messages(conversation, 24)
    messages = [{"role": "system", "content": build_system_prompt()}] + history

    for _ in range(rounds):
        message = await llm.complete(messages, tools=tools.SCHEMAS)
        calls = message.get("tool_calls") or []
        if not calls:
            text = (message.get("content") or "").strip()
            store.add_message(conversation, "assistant", text)
            return text
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
        for call in calls:
            fn = call.get("function", {})
            result = await tools.call(fn.get("name"), fn.get("arguments"))
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id"),
                "content": result[:8000],
            })
        store.add_message(conversation, "assistant", json.dumps(
            [c.get("function", {}).get("name") for c in calls]))
    fallback = "I stopped after too many tool rounds. Tell me the next single step."
    store.add_message(conversation, "assistant", fallback)
    return fallback
