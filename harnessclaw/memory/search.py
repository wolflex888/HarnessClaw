"""Ranking facts against a message: by shared words, and by meaning when embeddings exist."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

from harnessclaw.memory.facts import Fact

# Words in any script, with CJK handled separately as character bigrams.
_WORD_RE = re.compile(r"[^\W_぀-ヿ㐀-鿿가-힯]+")
_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿가-힯]+")

STOPWORDS = frozenset(
    """a an and are as at be been but by can could did do does for from had has have he her
    him his how i if in into is it its just me my no not of on or our she so than that the
    their them then there these they this to too us was we were what when where which who
    why will with would you your yours about after again all also am any because before
    being both each few more most other over same should some such only own very s t don
    want need like please tell know get got make let user users""".split()
)

RRF_K = 60


def tokens(text: str) -> set[str]:
    out: set[str] = set()
    for word in _WORD_RE.findall(text.lower()):
        if word in STOPWORDS or len(word) < 2 and not word.isdigit():
            continue
        out.add(_stem(word))
    for run in _CJK_RE.findall(text):
        if len(run) == 1:
            out.add(run)
        out.update(run[i : i + 2] for i in range(len(run) - 1))
    return out


def _stem(word: str) -> str:
    # Just enough folding that "flights" finds "flight" and "prefers" finds "prefer".
    if len(word) <= 3 or not word.endswith("s") or word.endswith(("ss", "us", "is")):
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"  # cities -> city
    if word.endswith(("sses", "xes", "zes", "ches", "shes")):
        return word[:-2]  # boxes -> box, dishes -> dish
    return word[:-1]  # lives -> live, flights -> flight


def keyword_rank(query: str, facts: Sequence[Fact]) -> list[tuple[float, Fact]]:
    """Facts sharing words with the query, best first. Rare words count for more."""
    q = tokens(query)
    if not q or not facts:
        return []
    fact_tokens = [tokens(f.content + " " + f.category) for f in facts]
    n = len(facts)
    df: dict[str, int] = {}
    for toks in fact_tokens:
        for t in toks & q:
            df[t] = df.get(t, 0) + 1
    scored = []
    for fact, toks in zip(facts, fact_tokens):
        shared = toks & q
        if not shared:
            continue
        score = sum(math.log(1 + n / df[t]) for t in shared) / math.sqrt(len(toks))
        scored.append((score, fact))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def vector_rank(
    query_vec: Sequence[float], facts: Sequence[Fact], vectors: dict[str, list[float]]
) -> list[tuple[float, Fact]]:
    scored = [(cosine(query_vec, vectors[f.id]), f) for f in facts if f.id in vectors]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored


def fuse(*rankings: list[Fact]) -> list[Fact]:
    """Reciprocal rank fusion: a fact high in either ranking ends up high."""
    scores: dict[str, float] = {}
    by_id: dict[str, Fact] = {}
    for ranking in rankings:
        for rank, fact in enumerate(ranking):
            scores[fact.id] = scores.get(fact.id, 0.0) + 1.0 / (RRF_K + rank + 1)
            by_id[fact.id] = fact
    return [by_id[i] for i in sorted(scores, key=lambda i: scores[i], reverse=True)]


def select(
    query: str,
    facts: Sequence[Fact],
    limit: int,
    *,
    query_vec: Sequence[float] | None = None,
    vectors: dict[str, list[float]] | None = None,
    pad_with_recent: bool = True,
    min_similarity: float = 0.3,
    margin: float = 0.1,
) -> list[Fact]:
    """The facts to show for `query`: matches first, then (optionally) the newest others."""
    if limit <= 0 or not facts:
        return []
    keyword = [f for _, f in keyword_rank(query, facts)]
    rankings = [keyword[:limit]]
    if query_vec is not None and vectors:
        scored = vector_rank(query_vec, facts, vectors)
        # Similarity scales differ between models, so keep what is close to the best
        # match rather than everything above a fixed number.
        if scored:
            cutoff = max(min_similarity, scored[0][0] - margin)
            rankings.append([f for s, f in scored if s >= cutoff][:limit])
    chosen = fuse(*rankings)[:limit]
    if pad_with_recent and len(chosen) < limit:
        seen = {f.id for f in chosen}
        for fact in sorted(facts, key=lambda f: f.at, reverse=True):
            if len(chosen) >= limit:
                break
            if fact.id not in seen:
                chosen.append(fact)
                seen.add(fact.id)
    return chosen
