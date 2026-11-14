"""Discord, through a bot you message directly (DMs).

The bot connects out to Discord's gateway, so the server needs no public URL
or open port. Only the user ids in `[discord] allowed_users` get answers.

Bots can read the text of DMs without Discord's privileged "Message Content"
setting, so the bot only asks for DM messages (plus the basic, non-privileged guilds intent). You need to share a server
with the bot to DM it: a private server just for you works.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import discord

log = logging.getLogger(__name__)
# discord.py warns at startup that voice isn't installed; the bot never uses voice.
logging.getLogger("discord.client").setLevel(logging.ERROR)

MAX_MESSAGE = 2000  # Discord's limit for one message

Handler = Callable[[str, str], Awaitable[str]]  # (chat key, text) -> reply


def intents() -> discord.Intents:
    flags = discord.Intents.none()
    flags.guilds = True  # not privileged; discord.py expects it to track channels
    flags.dm_messages = True
    return flags


def invite_url(application_id: int) -> str:
    # No permissions needed: the bot only answers DMs.
    return f"https://discord.com/oauth2/authorize?client_id={application_id}&scope=bot&permissions=0"


class DiscordChannel:
    name = "discord"

    def __init__(self, token: str, handle: Handler, allowed_users: Callable[[], set[int]]) -> None:
        self._token = token
        self._handle = handle
        self._allowed = allowed_users
        self._warned: set[int] = set()
        self.client = discord.Client(intents=intents())
        self.client.event(self.on_ready)
        self.client.event(self.on_message)

    async def run(self) -> None:
        try:
            await self.client.start(self._token)
        except discord.LoginFailure:
            log.error("Discord: the bot token was rejected; the Discord channel is off")
        except discord.PrivilegedIntentsRequired:
            log.error("Discord: the bot asked for a setting that isn't enabled in the Developer Portal")

    async def close(self) -> None:
        await self.client.close()

    async def on_ready(self) -> None:
        user = self.client.user
        log.info("Discord: connected as %s. Invite link: %s", user, invite_url(user.id) if user else "?")

    async def on_message(self, message: Any) -> None:
        author = message.author
        if author.bot or message.guild is not None:
            return  # other bots, and server channels: only DMs for now
        if author.id not in self._allowed():
            if author.id not in self._warned:
                self._warned.add(author.id)
                log.warning("Discord: DM from %s (%s), who isn't allowed. To allow them: "
                            "harnessclaw discord allow %s", author.id, author.name, author.id)
                await _send(message.channel, f"This assistant is private. Your Discord user id is {author.id}.")
            return
        text = (message.content or "").strip()
        if not text:
            await _send(message.channel, "I can only read text messages for now.")
            return
        try:
            async with message.channel.typing():
                reply = await self._handle(f"discord-{author.id}", text)
        except Exception:  # noqa: BLE001 - always answer, even when something broke
            log.exception("Discord: failed to answer a message")
            reply = "Something went wrong on my side. The details are in the server log."
        await _send(message.channel, reply)


async def _send(channel: Any, text: str) -> None:
    for chunk in split_message(text):
        try:
            await channel.send(chunk)
        except discord.HTTPException as exc:
            log.error("Discord: could not send a reply: %s", exc)
            return


def split_message(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Split on paragraph, then line, then hard boundaries, keeping code blocks closed."""
    text = text.strip() or "(empty reply)"
    chunks: list[str] = []
    while len(text) > limit:
        room = limit - 4  # space to close a code block left open by the cut
        cut = text.rfind("\n\n", 0, room)
        if cut < room // 2:
            cut = text.rfind("\n", 0, room)
        if cut < room // 2:
            cut = room
        chunk, text = text[:cut].rstrip(), text[cut:].lstrip()
        if chunk.count("```") % 2:  # cut inside a code block: close it here, reopen after
            chunk += "\n```"
            text = "```\n" + text
        chunks.append(chunk)
    chunks.append(text)
    return chunks


async def pair_discord(token: str, say: Callable[[str], object], wait: float = 300.0) -> dict[str, Any] | None:
    """Log in, show the invite link, and wait for the first DM to the bot. Returns its
    sender ({"id", "name"}), the account to allow, or None if nobody wrote in time."""
    client = discord.Client(intents=intents())
    found: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()

    @client.event
    async def on_ready() -> None:
        assert client.user is not None
        say(f"\nLogged in as {client.user}.")
        say("3. Add the bot to a server you're in (a private server just for you is fine):")
        say(f"   {invite_url(client.user.id)}")
        say(f"4. Then send the bot a direct message (waiting {wait:.0f}s)…")

    @client.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot or message.guild is not None or found.done():
            return
        await message.channel.send("Paired. Restart `harnessclaw serve` and I'll answer here.")
        found.set_result({"id": message.author.id, "name": str(message.author)})

    async def login() -> None:
        try:
            await client.start(token)
        except discord.LoginFailure as exc:
            if not found.done():
                found.set_exception(ValueError("Discord rejected that token"))
            raise exc

    task = asyncio.create_task(login())
    try:
        user = await asyncio.wait_for(asyncio.shield(found), timeout=wait)
        say(f"   Got it: {user['name']} (id {user['id']}). Allowed.")
        return user
    except TimeoutError:
        say("   No message arrived. DM the bot later; it will tell you your id for `harnessclaw discord allow`.")
        return None
    finally:
        await client.close()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, discord.LoginFailure):
            pass
