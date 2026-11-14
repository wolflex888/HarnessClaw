"""The assistant's tools. For now: its own memory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from harnessclaw.assistant.messages import ToolCall, ToolResult, ToolSpec
from harnessclaw.memory.service import MemoryService

TOOLS = [
    ToolSpec(
        name="remember",
        description=(
            "Save one short, lasting fact about the user for future conversations, e.g. "
            "'Prefers window seats', 'Partner Mei is vegetarian', 'Works at Acme in Taipei'. "
            "Not for one-off requests, passwords or payment details. When a fact you were shown "
            "has changed, pass its id as `replaces` instead of saving a second one."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The fact, one short sentence."},
                "category": {
                    "type": "string",
                    "description": "One word: profile, preference, person, plan, routine, work, health, or another.",
                },
                "replaces": {"type": "string", "description": "Id (m_xxxxxxxx) of the fact this one updates."},
                "private": {
                    "type": "boolean",
                    "description": "True for sensitive facts (health, money, relationships) that cloud models shouldn't see.",
                },
            },
            "required": ["content"],
        },
    ),
    ToolSpec(
        name="recall",
        description="Search memory for facts about the user beyond the ones already shown.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "What to look for, in a few words."}},
            "required": ["query"],
        },
    ),
    ToolSpec(
        name="forget",
        description="Forget one remembered fact by its id, when the user asks or it's wrong with no replacement.",
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string", "description": "The fact's id, m_xxxxxxxx."}},
            "required": ["id"],
        },
    ),
]


@dataclass
class ToolContext:
    memory: MemoryService
    by: str  # recorded on facts the assistant writes, e.g. "assistant/telegram"
    model: str
    local: bool  # the model runs locally, so it may see and change private facts


async def run_tool(call: ToolCall, ctx: ToolContext) -> ToolResult:
    try:
        text = await _run(call.name, call.input, ctx)
        return ToolResult(call.id, text)
    except (KeyError, ValueError, TypeError) as exc:
        return ToolResult(call.id, f"{type(exc).__name__}: {exc}", is_error=True)


async def _run(name: str, args: dict[str, Any], ctx: ToolContext) -> str:
    if "_invalid_json" in args:
        raise ValueError("the tool input was not valid JSON; try again")
    if name == "remember":
        content = args.get("content")
        if not isinstance(content, str):
            raise ValueError("`content` must be a string")
        replaces = _str(args.get("replaces"))
        fields = dict(
            category=_str(args.get("category")), private=bool(args.get("private", False)),
            by=ctx.by, model=ctx.model, include_private=ctx.local,
        )
        note = ""
        try:
            fact, changed = await ctx.memory.remember(content, replaces=replaces, **fields)
        except KeyError:
            # Small models sometimes invent an id; save the fact rather than lose it.
            fact, changed = await ctx.memory.remember(content, **fields)
            note = f" There is no fact {replaces}, so it was saved as a new one."
            replaces = None
        if not changed:
            return f"Already remembered [{fact.id}]."
        return f"{'Updated' if replaces else 'Remembered'} [{fact.id}].{note}"
    if name == "recall":
        facts = await ctx.memory.recall(
            str(args.get("query", "")), limit=10, include_private=ctx.local, pad_with_recent=False
        )
        if not facts:
            return "No matching memories."
        return "\n".join(f"- {f.content} [{f.id}]" for f in facts)
    if name == "forget":
        fact = ctx.memory.forget(str(args.get("id", "")), by=ctx.by, include_private=ctx.local)
        return f"Forgot: {fact.content}" if fact else "No memory with that id."
    raise ValueError(f"unknown tool {name!r}")


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
