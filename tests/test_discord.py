from types import SimpleNamespace

import discord

from harnessclaw.channels.discord_bot import DiscordChannel, intents, invite_url, split_message


class FakeChannel:
    def __init__(self):
        self.sent: list[str] = []
        self.typed = 0

    async def send(self, text):
        self.sent.append(text)

    def typing(self):
        channel = self

        class Typing:
            async def __aenter__(self):
                channel.typed += 1

            async def __aexit__(self, *exc):
                return False

        return Typing()


def dm(user_id, text, *, bot=False, guild=None, channel=None):
    author = SimpleNamespace(id=user_id, name=f"user{user_id}", bot=bot)
    return SimpleNamespace(author=author, content=text, guild=guild, channel=channel or FakeChannel())


def make_channel(handle, allowed=frozenset({42})):
    return DiscordChannel("TOKEN", handle, lambda: set(allowed))


async def test_allowed_user_gets_an_answer_with_typing():
    seen = []

    async def handle(key, text):
        seen.append((key, text))
        return "**Hello**"

    bot = make_channel(handle)
    msg = dm(42, " hi ")
    await bot.on_message(msg)
    assert seen == [("discord-42", "hi")]
    assert msg.channel.sent == ["**Hello**"] and msg.channel.typed == 1


async def test_strangers_get_their_id_once_and_nothing_else():
    async def handle(key, text):
        raise AssertionError("should not be called")

    bot = make_channel(handle)
    ch = FakeChannel()
    await bot.on_message(dm(7, "hello?", channel=ch))
    await bot.on_message(dm(7, "again", channel=ch))
    assert ch.sent == ["This assistant is private. Your Discord user id is 7."]


async def test_ignores_bots_and_server_channels():
    async def handle(key, text):
        raise AssertionError("should not be called")

    bot = make_channel(handle)
    for msg in (dm(42, "x", bot=True), dm(42, "x", guild=object())):
        await bot.on_message(msg)
        assert msg.channel.sent == []


async def test_errors_and_empty_messages_still_get_a_reply():
    async def handle(key, text):
        raise RuntimeError("boom")

    bot = make_channel(handle)
    msg = dm(42, "x")
    await bot.on_message(msg)
    assert "went wrong" in msg.channel.sent[0]
    empty = dm(42, "")
    await bot.on_message(empty)
    assert "only read text" in empty.channel.sent[0]


def test_only_dm_intents():
    flags = intents()
    assert flags.dm_messages and flags.guilds
    assert not flags.message_content and not flags.members and not flags.presences  # nothing privileged
    assert flags.value == discord.Intents(dm_messages=True, guilds=True).value


def test_invite_url():
    assert invite_url(123) == "https://discord.com/oauth2/authorize?client_id=123&scope=bot&permissions=0"


def test_split_message_fits_and_keeps_code_blocks_closed():
    text = ("p" * 1500) + "\n\n" + ("q" * 1500)
    assert split_message(text) == ["p" * 1500, "q" * 1500]
    code = "```\n" + "\n".join("line %d" % i for i in range(600)) + "\n```"
    chunks = split_message(code)
    assert len(chunks) > 1 and all(len(c) <= 2000 for c in chunks)
    assert all(c.count("```") % 2 == 0 for c in chunks)
