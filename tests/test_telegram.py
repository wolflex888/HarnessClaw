import asyncio
import json

import httpx

from harnessclaw.channels.telegram import TelegramChannel, pair_telegram, split_message, to_html


class FakeTelegram:
    def __init__(self, updates):
        self.updates = list(updates)
        self.sent: list[dict] = []
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        self.calls.append(method)
        body = json.loads(request.content or b"{}")
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"username": "claw_bot"}})
        if method == "getUpdates":
            batch, self.updates = self.updates[:1], self.updates[1:]
            return httpx.Response(200, json={"ok": True, "result": batch})
        if method == "sendMessage":
            if body.get("parse_mode") == "HTML" and "bad" in body["text"]:
                return httpx.Response(400, json={"ok": False, "error_code": 400, "description": "can't parse"})
            self.sent.append(body)
        return httpx.Response(200, json={"ok": True, "result": True})


def message(update_id, user_id, text, chat_type="private"):
    return {"update_id": update_id, "message": {
        "chat": {"id": user_id, "type": chat_type}, "from": {"id": user_id, "username": "u"}, "text": text}}


async def run_channel(fake, handle, allowed=frozenset({42})):
    async def handler(request):
        await asyncio.sleep(0.002)  # like a real long poll, give other tasks a turn
        return fake.handler(request)

    channel = TelegramChannel("TOKEN", handle, lambda: set(allowed),
                              transport=httpx.MockTransport(handler), retry_delay=0.01)
    task = asyncio.create_task(channel.run())
    for _ in range(200):
        await asyncio.sleep(0.01)
        if not fake.updates and not channel._tasks:
            break
    task.cancel()
    await channel.close()
    return channel


async def test_allowed_user_gets_an_answer_and_others_their_id():
    fake = FakeTelegram([message(1, 42, "hi"), message(2, 7, "hello?"), message(3, 7, "again"),
                         message(4, 99, "group", chat_type="group")])
    seen = []

    async def handle(key, text):
        seen.append((key, text))
        return "**Hello** <you>"

    channel = await run_channel(fake, handle)
    assert channel.username == "claw_bot"
    assert seen == [("telegram-42", "hi")]
    assert fake.sent[0] == {"chat_id": 42, "text": "<b>Hello</b> &lt;you&gt;", "parse_mode": "HTML"}
    strangers = [m for m in fake.sent if m["chat_id"] == 7]
    assert len(strangers) == 1 and "Your Telegram user id is 7" in strangers[0]["text"]
    assert "deleteWebhook" in fake.calls


async def test_falls_back_to_plain_text_and_survives_handler_errors():
    fake = FakeTelegram([message(1, 42, "a"), message(2, 42, "b")])

    async def handle(key, text):
        if text == "b":
            raise RuntimeError("boom")
        return "`<bad>`"

    await run_channel(fake, handle)
    texts = [m["text"] for m in fake.sent]
    assert "`<bad>`" in texts and any("went wrong" in t for t in texts)


def test_to_html():
    assert to_html("**a** & `x<y` [docs](https://e.com/?a=1&b=2)\n# Title") == (
        '<b>a</b> &amp; <code>x&lt;y</code> <a href="https://e.com/?a=1&amp;b=2">docs</a>\n<b>Title</b>')
    assert to_html("```py\nif a<b: **x**\n```") == "<pre>if a&lt;b: **x**</pre>"


def test_split_message():
    text = ("p" * 3000) + "\n\n" + ("q" * 3000)
    assert split_message(text) == ["p" * 3000, "q" * 3000]
    assert all(len(c) <= 4000 for c in split_message("x" * 9000))


def test_pairing_returns_the_first_private_sender():
    fake = FakeTelegram([message(1, 99, "group", chat_type="group"), message(2, 42, "hi")])

    def handler(request):  # pair_telegram uses a sync client
        return fake.handler(request)

    lines = []
    user = pair_telegram("TOKEN", lines.append, wait=5, transport=httpx.MockTransport(handler))
    assert user["id"] == 42
    assert fake.sent[-1]["chat_id"] == 42 and "Paired" in fake.sent[-1]["text"]
