from pathlib import Path

from harnessclaw.memory import markdown
from harnessclaw.memory.facts import Fact, clean_content, project_scope


def test_round_trip_keeps_everything():
    facts = [
        Fact(id="m_00000001", content="Prefers window seats", category="preference",
             by="claude-code", model="claude-opus-5-5", at="2026-11-09T10:00:00Z"),
        Fact(id="m_00000002", content="Partner Mei is vegetarian", category="person", by="user",
             at="2026-11-09T10:01:00Z"),
        Fact(id="m_00000003", content="Lives in Taipei", at="2026-11-09T10:02:00Z"),
    ]
    text = markdown.render(facts, "personal", False)
    parsed = markdown.parse(text, "personal", False)
    assert not parsed.needs_ids
    assert {f.id: f for f in parsed.facts} == {f.id: f for f in facts}


def test_general_heading_comes_first_then_alphabetical():
    facts = [Fact(id="m_00000001", content="b", category="work"),
             Fact(id="m_00000002", content="a", category="general"),
             Fact(id="m_00000003", content="c", category="health")]
    headings = [l for l in markdown.render(facts, "personal", False).splitlines() if l.startswith("## ")]
    assert headings == ["## general", "## health", "## work"]


def test_hand_written_lines_get_ids_and_are_by_user():
    text = "# Personal memory\n\n## routine\n\n- Runs every morning\n* Swims on Sundays\nnot a fact\n"
    parsed = markdown.parse(text, "personal", False)
    assert parsed.needs_ids
    assert [(f.content, f.category, f.by) for f in parsed.facts] == [
        ("Runs every morning", "routine", "user"), ("Swims on Sundays", "routine", "user")]
    assert all(f.id.startswith("m_") for f in parsed.facts)


def test_duplicate_ids_get_a_new_one():
    line = "- x <!-- id=m_00000001 by=user -->\n"
    parsed = markdown.parse(line + line.replace("- x", "- y"), "personal", False)
    assert parsed.needs_ids
    assert len({f.id for f in parsed.facts}) == 2


def test_paths_for_scopes(tmp_path: Path):
    assert markdown.scope_path(tmp_path, "personal", False) == tmp_path / "personal.md"
    assert markdown.scope_path(tmp_path, "personal", True) == tmp_path / "personal.private.md"
    assert markdown.scope_path(tmp_path, "project:web", False) == tmp_path / "projects" / "web.md"
    for scope, private in [("personal", True), ("project:web", False), ("project:web", True)]:
        assert markdown.path_scope(tmp_path, markdown.scope_path(tmp_path, scope, private)) == (scope, private)


def test_content_cannot_break_the_format():
    assert clean_content("  a\n b <!-- x -->  ") == "a b <! -- x -- >"


def test_project_names_are_sanitised():
    assert project_scope("My App!") == "project:my-app"
    assert project_scope("") == "personal"
    assert project_scope("../..") == "personal"
