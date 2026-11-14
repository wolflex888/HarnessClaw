import httpx
import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from harnessclaw.client import MemoryClient, ServerError
from harnessclaw.config import Config


def api(config: Config, name: str) -> MemoryClient:
    return MemoryClient(config.url, config.client(name).token)


def test_requests_need_a_known_token(server: Config):
    assert httpx.get(server.url + "/health").status_code == 200
    assert httpx.post(server.url + "/v1/recall", json={"query": "x"}).status_code == 401
    with pytest.raises(ServerError, match="unknown token"):
        MemoryClient(server.url, "hc_wrong").status()


def test_recall_returns_context_and_hides_private_facts_from_cloud_clients(server: Config):
    me, cloud = api(server, "user"), api(server, "cloud")
    me.remember("Partner Mei is vegetarian", category="person")
    me.remember("Allergic to penicillin", category="health", private=True)
    me.remember("Uses pnpm, not npm", project="webapp")

    mine = me.recall("allergies and dinner plans")
    assert {f["content"] for f in mine["facts"]} == {"Partner Mei is vegetarian", "Allergic to penicillin"}
    theirs = cloud.recall("allergies and dinner plans", project="webapp")
    assert {f["content"] for f in theirs["facts"]} == {"Partner Mei is vegetarian", "Uses pnpm, not npm"}
    assert "Partner Mei is vegetarian" in theirs["context"] and 'project "webapp"' in theirs["context"]


def test_cloud_clients_cannot_touch_private_facts_or_history(server: Config):
    me, cloud = api(server, "user"), api(server, "cloud")
    secret = me.remember("Allergic to penicillin", private=True)["fact"]
    with pytest.raises(ServerError, match="no memory"):
        cloud.forget(secret["id"])
    with pytest.raises(ServerError, match="private access"):
        cloud.history()
    with pytest.raises(ServerError, match="no memory"):
        cloud.remember("x", replaces=secret["id"])


def test_writes_record_who_made_them(server: Config):
    fact = api(server, "cloud").remember("Prefers window seats", model="claude-opus-5-5")["fact"]
    assert (fact["by"], fact["model"]) == ("cloud", "claude-opus-5-5")
    assert api(server, "user").history(1)[0]["by"] == "cloud"


async def test_mcp_tools(server: Config):
    token = server.client("cloud").token
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http:
        async with streamable_http_client(server.url + "/mcp", http_client=http) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                assert names == {"recall", "remember", "forget"}

                async def call(name, **args) -> str:
                    return (await session.call_tool(name, args)).content[0].text

                saved = await call("remember", content="Lives in Tokyo", category="profile", model="m1")
                fact_id = saved.split("[")[1].rstrip("].")
                assert await call("remember", content="Lives in Tokyo") == f"Already remembered [{fact_id}]."
                assert await call("remember", content="Lives in Taipei", replaces=fact_id) == f"Updated [{fact_id}]."
                assert "Lives in Taipei" in await call("recall", query="where do they live")
                assert await call("recall", query="kayaking") == "No matching memories."
                assert (await call("forget", id=fact_id)).startswith("Forgot")
    assert api(server, "user").facts() == []


def test_updates_keep_privacy_and_project(server: Config):
    me = api(server, "user")
    secret = me.remember("Allergic to penicillin", private=True)["fact"]
    updated = me.remember("Allergic to penicillin and latex", replaces=secret["id"])["fact"]
    assert updated["private"] is True and updated["id"] == secret["id"]
    proj = me.remember("Uses pnpm", project="webapp")["fact"]
    assert me.remember("Uses pnpm 9", replaces=proj["id"])["fact"]["scope"] == "project:webapp"
    assert {f["content"] for f in api(server, "cloud").facts(all_projects=True)} == {"Uses pnpm 9"}
