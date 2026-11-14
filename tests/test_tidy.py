import json

from harnessclaw.memory import tidy
from harnessclaw.memory.store import MemoryStore
from tests.conftest import make_fact


class FakeModel:
    name = "fake"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


def ids(proposals):
    return [p.ids for p in proposals]


def test_merge_may_only_use_existing_words():
    a = make_fact("Prefers window seats", id="m_0000000a")
    b = make_fact("Likes a window seat on flights", id="m_0000000b")
    ok, bad = tidy.check([
        {"op": "merge", "ids": [a.id, b.id], "content": "Prefers window seats on flights"},
    ], [a, b])
    assert ids(ok) == [[a.id, b.id]] and not bad
    ok, bad = tidy.check([
        {"op": "merge", "ids": [a.id, b.id], "content": "Prefers window seats on long-haul flights"},
    ], [a, b])
    assert not ok and "long" in bad[0][1]


def test_numbers_must_be_in_the_sources():
    a = make_fact("Lives in Taipei", id="m_0000000a")
    b = make_fact("Moved from Tokyo", id="m_0000000b")
    _, bad = tidy.check([{"op": "merge", "ids": [a.id, b.id], "content": "Lives in Taipei, moved from Tokyo in 2026"}], [a, b])
    assert "2026" in bad[0][1]


def test_user_written_lines_are_never_touched():
    a = make_fact("Prefers window seats", id="m_0000000a", by="user")
    b = make_fact("Prefers window seats on flights", id="m_0000000b")
    ok, bad = tidy.check([
        {"op": "merge", "ids": [a.id, b.id], "content": "Prefers window seats on flights"},
        {"op": "drop", "id": a.id, "reason": "dupe"},
    ], [a, b])
    assert not ok and all("written by you" in why for _, why in bad)


def test_a_pass_removes_at_most_a_fifth_or_four():
    facts = [make_fact(f"fact {i}", id=f"m_{i:08x}") for i in range(30)]
    ok, bad = tidy.check([{"op": "drop", "id": f.id} for f in facts], facts)
    assert len(ok) == 6 and len(bad) == 24
    small = facts[:12]
    ok, _ = tidy.check([{"op": "drop", "id": f.id} for f in small], small)
    assert len(ok) == 4


def test_unknown_or_reused_ids_are_rejected():
    a, b, c = (make_fact(x, id=f"m_0000000{x}") for x in "abc")
    ok, bad = tidy.check([
        {"op": "drop", "id": "m_ffffffff"},
        {"op": "merge", "ids": [a.id], "content": "a"},
        {"op": "drop", "id": b.id},
        {"op": "merge", "ids": [b.id, c.id], "content": "b c"},
    ], [a, b, c])
    assert ids(ok) == [[b.id]]
    assert [why for _, why in bad][:2] == ["no line m_ffffffff", "a merge needs at least two lines"]


def test_parse_reply_tolerates_fences_and_prose():
    assert tidy.parse_reply('Sure!\n```json\n[{"op": "drop", "id": "m_1"}]\n```') == [{"op": "drop", "id": "m_1"}]


async def test_tidy_applies_checked_changes_and_logs_them(store: MemoryStore):
    for i in range(10):
        store.remember(f"Filler fact number {i}", by="claude-code")
    a, _ = store.remember("Prefers window seats", by="claude-code")
    b, _ = store.remember("Likes a window seat on flights", by="claude-code")
    w, _ = store.remember("Asked about the weather today", by="claude-code")
    mine, _ = store.remember("Runs every morning", by="user")
    model = ScriptedModel(json.dumps([
        {"op": "merge", "ids": [a.id, b.id], "content": "Prefers window seats on flights"},
        {"op": "drop", "id": w.id, "reason": "one-off"},
        {"op": "drop", "id": mine.id, "reason": "nope"},
    ]), {"Asked about the weather today": "no"})
    report = await tidy.tidy(store, model)
    assert len(report.applied) == 2 and len(report.rejected) == 1
    contents = {f.content for f in store.facts()}
    assert "Prefers window seats on flights" in contents
    assert "Asked about the weather today" not in contents
    assert "Runs every morning" in contents
    merged = next(f for f in store.facts() if f.content == "Prefers window seats on flights")
    assert merged.by == "tidy" and merged.model == "fake"
    # Every change can be undone.
    for change_id in report.change_ids:
        store.undo(change_id)
    assert {"Prefers window seats", "Likes a window seat on flights", "Asked about the weather today"} <= {
        f.content for f in store.facts()}


async def test_a_failed_model_call_changes_nothing(store: MemoryStore):
    for i in range(12):
        store.remember(f"Fact {i}", by="claude-code")
    before = store.facts()
    report = await tidy.tidy(store, FakeModel("I can't do that"))
    assert report.errors and store.facts() == before


async def test_small_files_are_left_alone(store: MemoryStore):
    store.remember("Only fact", by="claude-code")
    model = FakeModel("[]")
    await tidy.tidy(store, model)
    assert model.prompts == []


def test_merging_unrelated_lines_is_rejected():
    a = make_fact("Sister Lin lives in Vancouver", id="m_0000000a")
    b = make_fact("Moved to Taipei in October 2026", id="m_0000000b")
    _, bad = tidy.check([{"op": "merge", "ids": [a.id, b.id],
                          "content": "Sister Lin lives in Vancouver (Moved to Taipei in October 2026)"}], [a, b])
    assert bad[0][1] == "the lines are about different things"


def test_a_merge_must_keep_each_lines_details():
    a = make_fact("Works as a product designer at Acme", id="m_0000000a")
    b = make_fact("Product designer, mostly mobile apps for banks", id="m_0000000b")
    _, bad = tidy.check([{"op": "merge", "ids": [a.id, b.id], "content": "Works as a product designer at Acme"}], [a, b])
    assert "loses most of" in bad[0][1]


class ScriptedModel(FakeModel):
    """Answers the list prompt with `reply`, and each yes/no drop check from `answers`."""

    def __init__(self, reply: str, answers: dict[str, str]) -> None:
        super().__init__(reply)
        self.answers = answers

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        for content, answer in self.answers.items():
            if f'"{content}"' in prompt and "a month from now" in prompt:
                return answer
        return self.reply


async def test_drops_are_double_checked(store: MemoryStore):
    for i in range(10):
        store.remember(f"Filler fact number {i}", by="claude-code")
    bday, _ = store.remember("Birthday is March 3", by="claude-code")
    weather, _ = store.remember("Asked about the weather in Kyoto today", by="claude-code")
    model = ScriptedModel(
        json.dumps([{"op": "drop", "id": bday.id, "reason": "x"}, {"op": "drop", "id": weather.id, "reason": "x"}]),
        {"Birthday is March 3": "Yes.", "Asked about the weather in Kyoto today": "No"},
    )
    report = await tidy.tidy(store, model)
    assert [p.ids for p in report.applied] == [[weather.id]]
    assert store.get(bday.id) is not None and store.get(weather.id) is None
