"""Optional Claude client. Everything works without it (deterministic fallbacks);
set ANTHROPIC_API_KEY to let Claude write answers and plan actions.

Token usage is tallied in USAGE so evals can report cost. Prices are read from env
(USD per million tokens) — set them to your model's current list price.
"""
from __future__ import annotations

import os

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
PRICE_IN = float(os.getenv("PRICE_IN_PER_MTOK", "0") or 0)
PRICE_OUT = float(os.getenv("PRICE_OUT_PER_MTOK", "0") or 0)

USAGE = {"calls": 0, "input_tokens": 0, "output_tokens": 0}


def reset_usage() -> None:
    USAGE.update(calls=0, input_tokens=0, output_tokens=0)


def cost_usd(usage: dict | None = None) -> float:
    u = usage or USAGE
    return (u["input_tokens"] * PRICE_IN + u["output_tokens"] * PRICE_OUT) / 1_000_000


def _track(msg) -> None:
    USAGE["calls"] += 1
    u = getattr(msg, "usage", None)
    if u is not None:
        USAGE["input_tokens"] += getattr(u, "input_tokens", 0) or 0
        USAGE["output_tokens"] += getattr(u, "output_tokens", 0) or 0


def client():
    """Return an Anthropic client, or None when no API key is configured."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    return anthropic.Anthropic()


def complete(client, system: str, user: str, max_tokens: int = 500) -> str:
    msg = client.messages.create(model=MODEL, max_tokens=max_tokens, system=system,
                                 messages=[{"role": "user", "content": user}])
    _track(msg)
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def tool_calls(client, system: str, user: str, tools: list[dict], max_tokens: int = 1500) -> list[dict]:
    """Ask Claude to call tools. Returns [{'name', 'input'}] — the calls are NOT executed here."""
    msg = client.messages.create(model=MODEL, max_tokens=max_tokens, system=system, tools=tools,
                                 messages=[{"role": "user", "content": user}])
    _track(msg)
    return [{"name": b.name, "input": b.input} for b in msg.content if getattr(b, "type", "") == "tool_use"]
