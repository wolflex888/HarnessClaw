"""Telegram, through the Bot API with long polling.

The bot asks Telegram for new messages, so the server needs no public URL or
open port. Only the user ids in `[telegram] allowed_users` get answers.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
POLL_TIMEOUT = 50  # seconds Telegram holds a getUpdates request open
MAX_MESSAGE = 4000  # Telegram's limit is 4096 characters
TYPING_EVERY = 4.5  # the typing indicator lasts about 5 seconds

Handler = Callable[[str, str], Awaitable[str]]  # (chat key, text) -> reply


class TelegramError(Exception):
    pass


class TelegramChannel:
    name = "telegram"

    def __init__(
        self,
        token: str,
        handle: Handler,
        allowed_users: Callable[[], set[int]],
        transport: httpx.AsyncBaseTransport | None = None,
        retry_delay: float = 5.0,
    ) -> None:
        self._base = f"{API}/bot{token}"
        self._handle = handle
        self._allowed = allowed_users
        self._http = httpx.AsyncClient(timeout=POLL_TIMEOUT + 20, transport=transport, trust_env=False)
        self._retry_delay = retry_delay
        self._tasks: set[asyncio.Task[None]] = set()
        self._warned: set[int] = set()
        self.username = ""

    async def call(self, method: str, **params: Any) -> Any:
        try:
            resp = await self._http.post(f"{self._base}/{method}", json=params)
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TelegramError(f"{method}: {type(exc).__name__}") from exc
        if not data.get("ok"):
            raise TelegramError(f"{method}: {data.get('error_code')} {data.get('description')}")
        return data["result"]

    async def run(self) -> None:
        try:
            me = await self.call("getMe")
        except TelegramError as exc:
            log.error("Telegram: the bot token was rejected (%s); the Telegram channel is off", exc)
            return
        self.username = me.get("username", "")
        # Long polling doesn't work while a webhook is set.
        await self.call("deleteWebhook")
        log.info("Telegram: listening as @%s", self.username)
        offset = 0
        while True:
            try:
                updates = await self.call(
                    "getUpdates", offset=offset, timeout=POLL_TIMEOUT, allowed_updates=["message"]
                )
            except TelegramError as exc:
                log.warning("Telegram: %s; retrying in %.0fs", exc, self._retry_delay)
                await asyncio.sleep(self._retry_delay)
                continue
            for update in updates:
                offset = max(offset, update["update_id"] + 1)
                message = update.get("message")
                if message:
                    task = asyncio.create_task(self._on_message(message))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await self._http.aclose()

    async def _on_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat", {})
        user = message.get("from", {})
        chat_id, user_id = chat.get("id"), user.get("id")
        if chat.get("type") != "private" or chat_id is None:
            return  # groups aren't supported yet
        if user_id not in self._allowed():
            if user_id not in self._warned:
                self._warned.add(user_id)
                log.warning("Telegram: message from %s (@%s), who isn't allowed. To allow them: "
                            "harnessclaw telegram allow %s", user_id, user.get("username", "?"), user_id)
                await self._send(chat_id, f"This assistant is private. Your Telegram user id is {user_id}.")
            return
        text = message.get("text")
        if not text:
            await self._send(chat_id, "I can only read text messages for now.")
            return

        typing = asyncio.create_task(self._typing(chat_id))
        try:
            reply = await self._handle(f"telegram-{chat_id}", text)
        except Exception:  # noqa: BLE001 - always answer, even when something broke
            log.exception("Telegram: failed to answer a message")
            reply = "Something went wrong on my side. The details are in the server log."
        finally:
            typing.cancel()
        await self._send(chat_id, reply)

    async def _typing(self, chat_id: int) -> None:
        with contextlib.suppress(TelegramError):
            while True:
                await self.call("sendChatAction", chat_id=chat_id, action="typing")
                await asyncio.sleep(TYPING_EVERY)

    async def _send(self, chat_id: int, text: str) -> None:
        for chunk in split_message(text):
            try:
                await self.call("sendMessage", chat_id=chat_id, text=to_html(chunk), parse_mode="HTML")
            except TelegramError:
                # Formatting Telegram won't accept: send it as plain text instead.
                try:
                    await self.call("sendMessage", chat_id=chat_id, text=chunk)
                except TelegramError as exc:
                    log.error("Telegram: could not send a reply: %s", exc)
                    return


def split_message(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Split on paragraph, then line, then hard boundaries to fit Telegram's limit."""
    text = text.strip() or "(empty reply)"
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    chunks.append(text)
    return chunks


_FENCE_RE = re.compile(r"```[^\n`]*\n?(.*?)```", re.DOTALL)
_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")


def to_html(text: str) -> str:
    """The light Markdown models write, as the HTML subset Telegram accepts."""
    stash: list[str] = []

    def keep(fragment: str) -> str:
        stash.append(fragment)
        return f"\x00{len(stash) - 1}\x00"

    text = _FENCE_RE.sub(lambda m: keep(f"<pre>{html.escape(m.group(1).rstrip())}</pre>"), text)
    text = _CODE_RE.sub(lambda m: keep(f"<code>{html.escape(m.group(1))}</code>"), text)
    text = _LINK_RE.sub(
        lambda m: keep(f'<a href="{html.escape(m.group(2), quote=True)}">{html.escape(m.group(1))}</a>'), text
    )
    text = html.escape(text, quote=False)
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _HEADING_RE.sub(r"<b>\1</b>", text)
    return re.sub("\x00(\\d+)\x00", lambda m: stash[int(m.group(1))], text)


def pair_telegram(
    token: str, say: Callable[[str], object], wait: float = 120.0,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any] | None:
    """Check the token, then wait for the first private message to the bot and return its
    sender, the account to allow. Returns None if nobody wrote in time."""
    import time

    base = f"{API}/bot{token}"
    with httpx.Client(timeout=40, transport=transport, trust_env=False) as http:
        def call(method: str, **params: Any) -> Any:
            data = http.post(f"{base}/{method}", json=params).json()
            if not data.get("ok"):
                raise TelegramError(f"{data.get('error_code')} {data.get('description')}")
            return data["result"]

        try:
            me = call("getMe")
        except (TelegramError, httpx.HTTPError, ValueError) as exc:
            raise ValueError(f"Telegram rejected that token ({exc})") from exc
        call("deleteWebhook")
        say(f"\n3. Now send any message to @{me['username']} from your Telegram account (waiting {wait:.0f}s)…")
        deadline = time.monotonic() + wait
        offset = 0
        while time.monotonic() < deadline:
            try:
                updates = call("getUpdates", offset=offset, timeout=min(30, max(1, int(deadline - time.monotonic()))),
                               allowed_updates=["message"])
            except TelegramError as exc:
                if "409" in str(exc):
                    raise ValueError("the bot is already being polled; stop `harnessclaw serve` and try again") from exc
                raise ValueError(str(exc)) from exc
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message") or {}
                if msg.get("chat", {}).get("type") == "private" and msg.get("from"):
                    user = msg["from"]
                    call("getUpdates", offset=offset, timeout=0)  # mark it handled
                    name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")]))
                    say(f"   Got it: {name or user.get('username')} (id {user['id']}). Allowed.")
                    call("sendMessage", chat_id=msg["chat"]["id"],
                         text="Paired. Restart `harnessclaw serve` and I'll answer here.")
                    return user
        say("   No message arrived. Message the bot later; it will tell you your id for `harnessclaw telegram allow`.")
        return None

