"""The memory server: MCP at /mcp for models, a small JSON API at /v1 for hooks and the CLI.

Every request needs `Authorization: Bearer <token>` from one of the clients in
config.toml. The token decides who wrote a fact and whether private facts are
visible.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from harnessclaw import __version__
from harnessclaw.assistant.agent import Assistant
from harnessclaw.assistant.conversations import ConversationStore
from harnessclaw.assistant.models import ChatModel
from harnessclaw.channels.telegram import TelegramChannel
from harnessclaw.config import Client, Config, ConfigWatcher, ModelSpec
from harnessclaw.llm import make_chat
from harnessclaw.memory.embeddings import Embedder, SemanticIndex, VectorIndex
from harnessclaw.memory.facts import USER, Fact
from harnessclaw.memory.service import MemoryService, render_context
from harnessclaw.memory.store import MemoryStore

log = logging.getLogger(__name__)

UPKEEP_EVERY = 600.0  # seconds between background index/tidy checks

INSTRUCTIONS = """Long-term memory about the user, shared by every assistant they use.
Use `recall` to look up what you know about the user before answering questions where their
preferences, people, plans or history matter. Use `remember` to save a short, durable fact
when you learn one (a preference, a person, a plan, a routine); don't save one-off requests,
passwords or payment details. When a fact you were shown has changed, call `remember` with
`replaces` set to its id instead of adding a second one."""


class ClientRegistry:
    """Resolves bearer tokens to the clients in config.toml (re-read when it changes)."""

    def __init__(self, watcher: ConfigWatcher) -> None:
        self._watcher = watcher

    def resolve(self, authorization: str | None) -> Client | None:
        if not authorization or not authorization.lower().startswith("bearer "):
            return None
        return self._watcher.current.by_token(authorization[7:].strip())


class TokenAuth:
    """Rejects requests without a known bearer token (except /health)."""

    def __init__(self, app: ASGIApp, clients: ClientRegistry) -> None:
        self.app = app
        self.clients = clients

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == "/health":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        client = self.clients.resolve(headers.get("authorization"))
        if client is None:
            await JSONResponse(
                {"error": "missing or unknown token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return
        scope.setdefault("state", {})["client"] = client
        await self.app(scope, receive, send)


def build_service(config: Config) -> MemoryService:
    store = MemoryStore(config.home)
    semantic = None
    if config.embeddings_base_url and config.embeddings_model:
        semantic = SemanticIndex(
            Embedder(
                config.embeddings_base_url,
                config.embeddings_model,
                config.embeddings_api_key,
                query_instruction=config.embeddings_query_instruction,
            ),
            VectorIndex(config.home / "index.db"),
        )
    tidy_model = make_chat(
        config.tidy_provider, config.tidy_model, config.tidy_base_url, config.tidy_api_key
    )
    return MemoryService(store, semantic, tidy_model, recall_limit=config.recall_limit)


def fact_json(fact: Fact) -> dict[str, Any]:
    return fact.to_dict()


def create_app(
    config: Config,
    service: MemoryService | None = None,
    model_factory: Callable[[ModelSpec], ChatModel] | None = None,
) -> ASGIApp:
    service = service or build_service(config)
    watcher = ConfigWatcher(config)
    clients = ClientRegistry(watcher)
    assistant = Assistant(
        lambda: watcher.current, service, ConversationStore(config.home / "conversations"),
        **({"model_factory": model_factory} if model_factory else {}),
    )
    mcp = MCPServer("harnessclaw-memory", version=__version__, instructions=INSTRUCTIONS)

    def caller(ctx: Context) -> tuple[Client, str]:
        client = clients.resolve((ctx.headers or {}).get("authorization"))
        if client is None:  # TokenAuth already checked; this only guards misuse
            raise PermissionError("unknown token")
        params = ctx.session.client_params
        info = params.client_info.name if params is not None and params.client_info else ""
        # A client named "user" is you; anything else is recorded by its token name,
        # falling back to the MCP client's own name.
        if client.name == USER or not info or info.lower().startswith(client.name.lower()):
            by = client.name
        else:
            by = f"{client.name}/{info}"
        return client, by

    @mcp.tool()
    async def recall(query: str, ctx: Context, project: str | None = None, limit: int = 10) -> str:
        """Search long-term memory for facts about the user related to `query`.

        Args:
            query: What you want to know, in a few words ("dietary preferences", "trip to Japan").
            project: The current project name, to include facts saved for that project.
            limit: Most facts to return.
        """
        client, _ = caller(ctx)
        facts = await service.recall(
            query, project=project, limit=max(1, min(limit, 50)),
            include_private=client.private, pad_with_recent=False,
        )
        if not facts:
            return "No matching memories."
        return "\n".join(f"- {f.content} [{f.id}, {f.category}]" for f in facts)

    @mcp.tool()
    async def remember(
        content: str,
        ctx: Context,
        category: str = "general",
        project: str | None = None,
        replaces: str | None = None,
        private: bool = False,
        model: str | None = None,
    ) -> str:
        """Save one short, durable fact about the user for future sessions.

        Good: "Prefers window seats on flights", "Partner Mei is vegetarian", "Works at Acme
        in Taipei (UTC+8)". Not: one-off requests, passwords, payment details.

        Args:
            content: The fact, one short sentence.
            category: One word: profile, preference, person, plan, routine, work, health, or another.
            project: Set only for a fact that belongs to the current project rather than the user.
            replaces: Id (m_xxxxxxxx) of a remembered fact this one corrects or updates.
            private: True for sensitive facts that should only be shown to local models.
            model: Your model name, if you know it, so the user can see who wrote the fact.
        """
        client, by = caller(ctx)
        try:
            fact, changed = await service.remember(
                content, category=category, project=project, private=private, by=by,
                model=model or "", replaces=replaces, include_private=client.private,
            )
        except (KeyError, ValueError) as exc:
            return f"Not saved: {exc}"
        if not changed:
            return f"Already remembered [{fact.id}]."
        return f"{'Updated' if replaces else 'Remembered'} [{fact.id}]."

    @mcp.tool()
    async def forget(id: str, ctx: Context) -> str:
        """Forget one remembered fact, by its id (m_xxxxxxxx). Use when the user asks you to
        forget something or a fact is wrong and has no replacement."""
        client, by = caller(ctx)
        fact = service.forget(id, by=by, include_private=client.private)
        return f"Forgot: {fact.content}" if fact else f"No memory {id}."

    # ------------------------------------------------------------------ JSON API

    def route(path: str, methods: list[str]):
        def wrap(fn: Callable[[Request, Client], Awaitable[Response]]):
            async def endpoint(request: Request) -> Response:
                try:
                    return await fn(request, request.state.client)
                except PermissionError as exc:
                    return JSONResponse({"error": str(exc)}, status_code=403)
                except (KeyError, ValueError) as exc:
                    return JSONResponse({"error": str(exc).strip("'\"")}, status_code=400)
                except RuntimeError as exc:
                    return JSONResponse({"error": str(exc)}, status_code=409)

            mcp.custom_route(path, methods=methods)(endpoint)
            return fn

        return wrap

    async def body(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except json.JSONDecodeError as exc:
            raise ValueError("request body is not JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return data

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> Response:
        return JSONResponse({"ok": True, "version": __version__})

    @route("/v1/status", ["GET"])
    async def status(_: Request, client: Client) -> Response:
        return JSONResponse({**service.status(), "client": client.name})

    @route("/v1/recall", ["POST"])
    async def recall_api(request: Request, client: Client) -> Response:
        data = await body(request)
        query, project = str(data.get("query", "")), data.get("project")
        facts = await service.recall(
            query, project=project, limit=data.get("limit"), include_private=client.private,
        )
        return JSONResponse({
            "facts": [fact_json(f) for f in facts],
            "context": render_context(facts, project),
        })

    @route("/v1/remember", ["POST"])
    async def remember_api(request: Request, client: Client) -> Response:
        data = await body(request)
        fact, changed = await service.remember(
            str(data["content"]), category=data.get("category"), project=data.get("project"),
            private=bool(data.get("private", False)), by=client.name,
            model=str(data.get("model") or ""), replaces=data.get("replaces"),
            include_private=client.private,
        )
        return JSONResponse({"fact": fact_json(fact), "changed": changed})

    @route("/v1/forget", ["POST"])
    async def forget_api(request: Request, client: Client) -> Response:
        data = await body(request)
        fact = service.forget(str(data["id"]), by=client.name, include_private=client.private)
        if fact is None:
            return JSONResponse({"error": f"no memory {data['id']}"}, status_code=404)
        return JSONResponse({"forgot": fact_json(fact)})

    @route("/v1/facts", ["GET"])
    async def facts_api(request: Request, client: Client) -> Response:
        project = request.query_params.get("project")
        if request.query_params.get("all") == "1":
            facts = service.store.facts(include_private=client.private)
        else:
            facts = service.visible(project, client.private)
        return JSONResponse({"facts": [fact_json(f) for f in facts]})

    @route("/v1/history", ["GET"])
    async def history_api(request: Request, client: Client) -> Response:
        _require_private(client)
        limit = int(request.query_params.get("limit", "50"))
        return JSONResponse({"changes": service.store.history(limit)})

    @route("/v1/undo", ["POST"])
    async def undo_api(request: Request, client: Client) -> Response:
        _require_private(client)
        data = await body(request)
        change = service.store.undo(str(data["id"]), by=client.name)
        if change is not None:
            await service.refresh_index()
        return JSONResponse({"change": change.to_dict() if change else None})

    @route("/v1/tidy", ["POST"])
    async def tidy_api(request: Request, client: Client) -> Response:
        _require_private(client)
        data = await body(request)
        report = await service.tidy(dry_run=bool(data.get("dry_run", False)))
        if report.change_ids:
            await service.refresh_index()
        return JSONResponse(report.to_dict())

    @route("/v1/chat", ["POST"])
    async def chat_api(request: Request, client: Client) -> Response:
        # Replies can include private memories, so only private-access clients may chat.
        _require_private(client)
        data = await body(request)
        reply = await assistant.handle(f"api-{client.name}", str(data["text"]), channel="api")
        return JSONResponse({"reply": reply})

    app = mcp.streamable_http_app(host=config.host)
    inner_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(a: Starlette) -> AsyncIterator[None]:
        async with inner_lifespan(a):
            tasks = [asyncio.create_task(_upkeep(service))]
            telegram = None
            if config.telegram_token:
                telegram = TelegramChannel(
                    config.telegram_token,
                    lambda key, text: assistant.handle(key, text, channel="telegram"),
                    lambda: set(watcher.current.telegram_allowed_users),
                )
                tasks.append(asyncio.create_task(telegram.run()))
            try:
                yield
            finally:
                for task in tasks:
                    task.cancel()
                for task in tasks:
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                if telegram is not None:
                    await telegram.close()

    app.router.lifespan_context = lifespan
    app.state.service = service
    app.state.assistant = assistant
    return TokenAuth(app, clients)


def _require_private(client: Client) -> None:
    if not client.private:
        raise PermissionError("this needs a client with private access")


async def _upkeep(service: MemoryService) -> None:
    """Keep embeddings current and run the tidy-up when it's due."""
    while True:
        try:
            await service.refresh_index()
            if service.tidy_due():
                report = await service.tidy()
                if report.applied:
                    log.info("tidy-up applied %d change(s)", len(report.applied))
                    await service.refresh_index()
                for err in report.errors:
                    log.warning("tidy-up failed: %s", err)
        except Exception:  # noqa: BLE001 - upkeep must never stop the server
            log.exception("memory upkeep failed")
        await asyncio.sleep(UPKEEP_EVERY)
