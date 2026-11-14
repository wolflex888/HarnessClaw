import time

import pytest

from harnessclaw.memory.store import MemoryStore


def test_remember_writes_the_file(store: MemoryStore):
    fact, change = store.remember("Prefers window seats", category="preference", by="claude-code",
                                  model="claude-opus-5-5")
    assert change is not None
    text = (store.root / "personal.md").read_text()
    assert f"- Prefers window seats <!-- id={fact.id} by=claude-code model=claude-opus-5-5" in text


def test_identical_fact_is_not_saved_twice(store: MemoryStore):
    a, _ = store.remember("Prefers window seats")
    b, change = store.remember("prefers   window seats")
    assert change is None and a.id == b.id


def test_replaces_updates_in_place(store: MemoryStore):
    a, _ = store.remember("Lives in Tokyo", category="profile")
    store.remember("Something else")
    b, change = store.remember("Lives in Taipei", replaces=a.id, by="claude-code")
    assert b.id == a.id and b.category == "profile" and change.action == "update"
    assert [f.content for f in store.facts()] == ["Lives in Taipei", "Something else"]


def test_scopes_and_private_go_to_separate_files(store: MemoryStore):
    store.remember("A", scope="personal")
    store.remember("B", scope="personal", private=True)
    store.remember("C", scope="project:web")
    assert {f.content for f in store.facts(include_private=False)} == {"A", "C"}
    assert {f.content for f in store.facts({"personal"})} == {"A", "B"}
    assert (store.root / "personal.private.md").exists()
    assert (store.root / "projects" / "web.md").exists()


def test_hand_edits_are_picked_up_and_logged(store: MemoryStore):
    a, _ = store.remember("Prefers window seats")
    path = store.root / "personal.md"
    time.sleep(0.01)
    text = path.read_text().replace("Prefers window seats", "Prefers aisle seats")
    path.write_text(text + "- Runs every morning\n")
    contents = {f.content for f in store.facts()}
    assert contents == {"Prefers aisle seats", "Runs every morning"}
    assert store.get(a.id).content == "Prefers aisle seats"  # same id, edited text
    last = store.history(1)[0]
    assert last["action"] == "edit" and last["by"] == "user"
    assert "id=m_" in path.read_text().split("Runs every morning")[1]  # new line got an id


def test_deleting_a_line_forgets_it(store: MemoryStore):
    a, _ = store.remember("Prefers window seats")
    path = store.root / "personal.md"
    path.write_text("\n".join(l for l in path.read_text().splitlines() if "window" not in l))
    assert store.get(a.id) is None


def test_undo_restores_a_forgotten_fact(store: MemoryStore):
    a, _ = store.remember("Prefers window seats")
    change = store.forget(a.id)
    store.undo(change.id)
    assert store.get(a.id).content == "Prefers window seats"
    with pytest.raises(ValueError):
        store.undo(change.id)


def test_undo_of_an_update_brings_back_the_old_text(store: MemoryStore):
    a, _ = store.remember("Lives in Tokyo")
    _, change = store.remember("Lives in Taipei", replaces=a.id)
    store.undo(change.id)
    assert store.get(a.id).content == "Lives in Tokyo"


def test_state_survives_a_restart(store: MemoryStore, tmp_path):
    a, _ = store.remember("Prefers window seats", category="preference", by="x", model="m")
    again = MemoryStore(tmp_path)
    assert again.get(a.id) == a
