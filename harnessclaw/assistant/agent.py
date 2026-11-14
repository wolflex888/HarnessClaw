"""The assistant: answers a message from any channel, using memory and the chosen model."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime

from harnessclaw.assistant.conversations import Conversation, ConversationStore
from harnessclaw.assistant.messages import Message, ToolResult
from harnessclaw.assistant.models import ChatModel, ModelError, create_model
from harnessclaw.assistant.tools import TOOLS, ToolContext, run_tool
from harnessclaw.config import Config, ModelSpec
from harnessclaw.memory.service import MemoryService, render_context

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8

SYSTEM = """You are {name}, a personal assistant. You talk with one person, mostly through a chat app on their phone.

You have a long-term memory about them, shared with their other assistants. Each message may start with a <context> block holding the current time and facts from that memory; the person doesn't see it. Use those facts when they help, without reciting them. When you learn something lasting about the person (a preference, someone in their life, a plan, a routine), save it with `remember`. When a fact you were shown has changed, call `remember` with its id as `replaces`. If they ask you to forget something, use `forget`. Don't save one-off requests, passwords or payment details.

Write like a thoughtful friend texting: short, direct, and warm. Prefer a few sentences to long lists. Light Markdown (bold, lists, code) is fine. If you don't know something, say so."""

HELP = """Commands:
/new — start a fresh conversation (memory stays)
/model — show the models; /model <name> to switch
/help — this message

Anything else goes to the assistant."""


class Assistant:
    def __init__(
        self,
        config: Callable[[], Config],
        memory: MemoryService,
        store: ConversationStore,
        model_factory: Callable[[ModelSpec], ChatModel] = create_model,
    ) -> None:
        self._config = config
        self.memory = memory
        self.store = store
        self._model_factory = model_factory
        self._models: dict[tuple[str, str, str, str], ChatModel] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def handle(self, key: str, text: str, *, channel: str) -> str:
        """Reply to one message. `key` identifies the chat, e.g. "telegram-12345"."""
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            text = text.strip()
            if text.startswith("/"):
                return self._command(key, text)
            return await self._reply(key, text, channel)

    # ------------------------------------------------------------------ commands

    def _command(self, key: str, text: str) -> str:
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@", 1)[0].lower()  # "/model@MyBot" in Telegram groups
        arg = arg.strip()
        conv = self.store.load(key)
        if cmd in ("/start", "/help"):
            spec = self._spec(conv)
            return f"Hi, I'm {self._config().assistant_name}. Talking through {spec.name} ({spec.model}).\n\n{HELP}"
        if cmd == "/new":
            conv.reset()
            self.store.save(conv)
            return "Started a fresh conversation. I still remember what I've saved about you."
        if cmd == "/model":
            return self._switch_model(conv, arg)
        return f"Unknown command {cmd}.\n\n{HELP}"

    def _switch_model(self, conv: Conversation, name: str) -> str:
        config = self._config()
        current = self._spec(conv)
        if not name:
            lines = ["Models:"]
            for m in config.models:
                mark = "→" if m.name == current.name else "  "
                where = "local" if m.local else "cloud"
                lines.append(f"{mark} {m.name}: {m.model} ({where})")
            lines.append("\nSwitch with /model <name>.")
            return "\n".join(lines)
        spec = config.model(name)
        if spec is None:
            return f"No model named {name!r}. Try /model to see the list."
        conv.model = spec.name
        note = ""
        if not spec.local and conv.private_context:
            conv.reset()
            note = "\nThis conversation included private memories, so I started a fresh one before switching to a cloud model."
        self.store.save(conv)
        return f"Now using {spec.name} ({spec.model}).{note}"

    # ------------------------------------------------------------------ replying

    def _spec(self, conv: Conversation) -> ModelSpec:
        config = self._config()
        spec = config.model(conv.model) or config.model(config.assistant_model)
        if spec is None:
            if not config.models:
                raise ModelError("no models configured; add a [[models]] section to config.toml")
            spec = config.models[0]
        return spec

    def _model(self, spec: ModelSpec) -> ChatModel:
        cache_key = (spec.provider, spec.model, spec.base_url, spec.api_key)
        if cache_key not in self._models:
            self._models[cache_key] = self._model_factory(spec)
        return self._models[cache_key]

    def system_prompt(self) -> str:
        config = self._config()
        prompt = SYSTEM.format(name=config.assistant_name)
        if config.assistant_instructions.strip():
            prompt += "\n\nWhat the person asked for:\n" + config.assistant_instructions.strip()
        return prompt

    async def _reply(self, key: str, text: str, channel: str) -> str:
        if not text:
            return "Send me a message and I'll reply."
        config = self._config()
        conv = self.store.load(key)
        notes: list[str] = []
        if conv.messages and conv.idle_hours() >= config.new_conversation_after_hours:
            conv.reset()
        try:
            spec = self._spec(conv)
            model = self._model(spec)
        except (ModelError, ValueError) as exc:
            return f"I can't answer right now: {exc}"
        if not spec.local and conv.private_context:
            conv.reset()
            notes.append("(Started a fresh conversation: the last one included private memories.)")

        facts = await self.memory.recall(text, include_private=spec.local)
        context = [f"Now: {datetime.now().astimezone():%A %Y-%m-%d %H:%M %Z}"]
        memory = render_context(facts)
        if memory:
            context.append(memory)
        conv.private_context = conv.private_context or any(f.private for f in facts)

        saved = list(conv.messages)
        conv.messages.append(Message("user", text=f"<context>\n{chr(10).join(context)}\n</context>\n\n{text}"))
        tool_ctx = ToolContext(self.memory, by=f"assistant/{channel}", model=spec.model, local=spec.local)
        system = self.system_prompt()

        try:
            reply = await self._loop(conv, model, system, tool_ctx)
        except ModelError as exc:
            conv.messages = saved  # leave the conversation as it was; nothing half-finished
            log.warning("model %s failed: %s", spec.name, exc)
            hint = " Try again, or switch with /model." if len(config.models) > 1 else ""
            return f"I couldn't reach {spec.name} ({exc}).{hint}"
        self.store.save(conv)
        return "\n\n".join([*notes, reply]) if notes else reply

    async def _loop(self, conv: Conversation, model: ChatModel, system: str, ctx: ToolContext) -> str:
        texts: list[str] = []
        for _ in range(MAX_TOOL_ROUNDS):
            completion = await model.respond(system, conv.messages, TOOLS)
            if completion.stop_reason == "refusal":
                return "The model declined to answer that. You could rephrase, or try another with /model."
            conv.messages.append(completion.message)
            if completion.message.text:
                texts.append(completion.message.text)
            calls = completion.message.tool_calls
            if completion.stop_reason == "max_tokens":
                texts.append("(cut off: the reply hit the length limit)")
                if calls:  # never run a tool call that may be truncated; close it out instead
                    conv.messages.append(Message("user", tool_results=[
                        ToolResult(c.id, "Not run: the reply was cut off.", is_error=True) for c in calls]))
                break
            if not calls:
                break
            results = [await run_tool(call, ctx) for call in calls]
            conv.messages.append(Message("user", tool_results=results))
        else:
            texts.append("(stopped: too many steps in one reply)")
        return "\n\n".join(texts) or "(no reply)"
