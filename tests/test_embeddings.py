import httpx

from harnessclaw.memory.embeddings import Embedder, SemanticIndex, VectorIndex, query_prefix
from tests.conftest import make_fact


def fake_endpoint(calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        calls.append(body["input"])
        data = [{"index": i, "embedding": [float(len(t)), 1.0]} for i, t in enumerate(body["input"])]
        return httpx.Response(200, json={"data": data})
    return httpx.MockTransport(handler)


def test_query_prefix_only_for_instruction_models():
    assert query_prefix("qwen3-embedding:0.6b").startswith("Instruct: ")
    assert query_prefix("nomic-embed-text") == ""
    assert query_prefix("anything", "Find facts") == "Instruct: Find facts\nQuery: "
    assert query_prefix("qwen3-embedding:0.6b", "") == ""


async def test_vectors_are_cached_until_the_text_changes(tmp_path):
    calls: list = []
    emb = Embedder("http://x/v1", "m", transport=fake_endpoint(calls), query_instruction="")
    sem = SemanticIndex(emb, VectorIndex(tmp_path / "index.db"))
    a = make_fact("abc")
    assert await sem.ensure([a])
    assert await sem.ensure([a])
    assert calls == [["abc"]]
    a.content = "abcd"
    await sem.ensure([a])
    assert sem.vectors_for([a]) == {a.id: [4.0, 1.0]}
    # A new index over the same file reloads the cache.
    sem2 = SemanticIndex(emb, VectorIndex(tmp_path / "index.db"))
    assert sem2.vectors_for([a]) == {a.id: [4.0, 1.0]}


async def test_endpoint_failure_backs_off():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500)

    emb = Embedder("http://x/v1", "m", transport=httpx.MockTransport(handler))
    assert await emb.embed(["a"]) is None
    assert await emb.embed(["a"]) is None
    assert len(calls) == 1 and not emb.available and "500" in emb.last_error
