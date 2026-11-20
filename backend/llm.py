"""Optional Claude client. Everything works without it (deterministic fallbacks);
set ANTHROPIC_API_KEY to let Claude write answers and plan actions.
"""
from __future__ import annotations

import os

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")


def client():
    """Return an Anthropic client, or None when no API key is configured."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    return anthropic.Anthropic()


def complete(client, system: str, user: str, max_tokens: int = 500) -> str:
    msg = client.messages.create(model=MODEL, max_tokens=max_tokens, system=system,
                                 messages=[{"role": "user", "content": user}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def tool_calls(client, system: str, user: str, tools: list[dict], max_tokens: int = 1500) -> list[dict]:
    """Ask Claude to call tools. Returns [{'name', 'input'}] — the calls are NOT executed here."""
    msg = client.messages.create(model=MODEL, max_tokens=max_tokens, system=system, tools=tools,
                                 messages=[{"role": "user", "content": user}])
    return [{"name": b.name, "input": b.input} for b in msg.content if getattr(b, "type", "") == "tool_use"]
