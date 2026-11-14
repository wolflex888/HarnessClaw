from harnessclaw.memory.search import keyword_rank, select, tokens
from tests.conftest import make_fact


def test_tokens_fold_plurals_and_skip_stopwords():
    assert tokens("The user prefers flights") == {"prefer", "flight"}


def test_cjk_uses_bigrams():
    assert {"素食", "食者"} <= tokens("伴侣是素食者")


def test_keyword_rank_prefers_rare_words():
    facts = [make_fact("Likes coffee in the morning"), make_fact("Likes tea"), make_fact("Likes coffee")]
    ranked = [f.content for _, f in keyword_rank("morning coffee", facts)]
    assert ranked[0] == "Likes coffee in the morning"
    assert "Likes tea" not in ranked


def test_select_pads_with_newest_when_asked():
    old = make_fact("Plays chess", at="2026-01-01T00:00:00Z")
    new = make_fact("Lives in Taipei", at="2026-11-01T00:00:00Z")
    match = make_fact("Prefers window seats", at="2025-01-01T00:00:00Z")
    assert [f.content for f in select("window seat", [old, new, match], 2)] == [
        "Prefers window seats", "Lives in Taipei"]
    assert [f.content for f in select("window seat", [old, new, match], 2, pad_with_recent=False)] == [
        "Prefers window seats"]


def test_select_keeps_vector_matches_close_to_the_best():
    a, b, c = make_fact("a"), make_fact("b"), make_fact("c")
    vectors = {a.id: [1.0, 0.0], b.id: [0.95, 0.31], c.id: [0.0, 1.0]}
    chosen = select("zzz", [a, b, c], 5, query_vec=[1.0, 0.0], vectors=vectors, pad_with_recent=False)
    assert [f.id for f in chosen] == [a.id, b.id]


def test_select_returns_nothing_when_nothing_is_close():
    a = make_fact("a")
    chosen = select("zzz", [a], 5, query_vec=[1.0, 0.0], vectors={a.id: [0.2, 1.0]}, pad_with_recent=False)
    assert chosen == []


def test_plural_folding():
    assert tokens("lives cities boxes dishes flights glass status") == {
        "live", "city", "box", "dish", "flight", "glass", "status"}
