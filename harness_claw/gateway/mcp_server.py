from __future__ import annotations

from typing import Any

from harness_claw.gateway.audit import AuditEvent, AuditLogger
from harness_claw.gateway.auth import AuthError, TokenStore
from harness_claw.gateway.broker import Broker
from harness_claw.gateway.capability import CapabilityConnector
from harness_claw.gateway.memory import MemoryStore
from harness_claw.gateway.policy import PolicyEngine


class PermissionError(Exception):
    pass


class GatewayMCP:
    """
    Implements all MCP tool logic.
    Instantiated once at startup and shared across requests.
    The FastMCP HTTP endpoint (mounted in server.py) delegates to this class.
    """

    def __init__(
        self,
        token_store: TokenStore,
        policy: PolicyEngine,
        connectors: list[CapabilityConnector],
        broker: Broker,
        memory: MemoryStore,
        audit: AuditLogger,
        spawn_callback: Any | None = None,
    ) -> None:
        self._tokens = token_store
        self._policy = policy
        self._connectors = connectors
        self._broker = broker
        self._memory = memory
        self._audit = audit
        self._spawn_callback = spawn_callback  # async (role_id, working_dir) → session_id

    def _auth(self, token: str, operation: str) -> str:
        """Validate token and check scope. Returns subject. Raises on failure."""
        try:
            subject, scopes = self._tokens.validate(token)
        except AuthError as e:
            raise AuthError(str(e))
        decision = self._policy.check(subject=subject, scopes=scopes, operation=operation)
        if not decision.allowed:
            self._audit.log(AuditEvent(
                subject=subject, operation=operation, resource="",
                outcome="denied", details={"reason": decision.reason},
            ))
            raise PermissionError(f"policy denied: {decision.reason}")
        return subject

    # --- Agent tools ---

    async def agent_list(self, token: str, caps: list[str]) -> list[dict[str, Any]]:
        subject = self._auth(token, "agent:list")
        results = []
        for connector in self._connectors:
            results.extend(await connector.query(caps))
        self._audit.log(AuditEvent(
            subject=subject, operation="agent.list", resource="registry",
            outcome="allowed", details={"caps": caps, "count": len(results)},
        ))
        return [
            {"session_id": a.session_id, "role_id": a.role_id,
             "caps": a.caps, "status": a.status, "task_count": a.task_count}
            for a in results
        ]

    async def agent_delegate(self, token: str, caps: list[str], instructions: str) -> dict[str, Any]:
        subject = self._auth(token, "agent:delegate")
        try:
            task_id = await self._broker.delegate(
                delegated_by=subject, caps=caps, instructions=instructions
            )
        except ValueError as e:
            self._audit.log(AuditEvent(
                subject=subject, operation="agent.delegate", resource="",
                outcome="error", details={"error": str(e)},
            ))
            raise
        self._audit.log(AuditEvent(
            subject=subject, operation="agent.delegate", resource=task_id,
            outcome="allowed", details={"caps": caps},
        ))
        return {"task_id": task_id}

    async def agent_status(self, token: str, task_id: str) -> dict[str, Any]:
        subject = self._auth(token, "agent:delegate")
        task = self._broker.get_task(task_id)
        if task is None:
            raise KeyError(f"task {task_id!r} not found")
        return task.to_dict()

    async def agent_progress(self, token: str, task_id: str, pct: int, msg: str) -> dict[str, Any]:
        subject = self._auth(token, "agent:report")
        task = self._broker.update_progress(task_id, pct=pct, msg=msg)
        self._audit.log(AuditEvent(
            subject=subject, operation="agent.progress", resource=task_id,
            outcome="allowed", details={"pct": pct, "msg": msg},
        ))
        return {"task_id": task_id, "status": task.status}

    async def agent_complete(self, token: str, task_id: str, result: dict[str, Any] | str) -> dict[str, Any]:
        subject = self._auth(token, "agent:report")
        task = await self._broker.complete_task(task_id, result=result)
        self._audit.log(AuditEvent(
            subject=subject, operation="agent.complete", resource=task_id,
            outcome="allowed", details={},
        ))
        return {"task_id": task_id, "status": "completed"}

    async def agent_spawn(self, token: str, role_id: str, working_dir: str) -> dict[str, Any]:
        subject = self._auth(token, "agent:spawn")
        if self._spawn_callback is None:
            raise RuntimeError("spawn not available — no runner registered")
        session_id = await self._spawn_callback(role_id=role_id, working_dir=working_dir)
        self._audit.log(AuditEvent(
            subject=subject, operation="agent.spawn", resource=session_id,
            outcome="allowed", details={"role_id": role_id},
        ))
        return {"session_id": session_id}

    # --- Memory tools ---

    async def memory_namespaces(self, token: str) -> list[str]:
        self._auth(token, "memory:read")
        return await self._memory.namespaces()

    async def memory_list(self, token: str, namespace: str) -> list[dict[str, Any]]:
        self._auth(token, "memory:read")
        entries = await self._memory.list(namespace)
        return [
            {"key": e.key, "summary": e.summary, "tags": e.tags,
             "size_bytes": e.size_bytes, "updated_at": e.updated_at}
            for e in entries
        ]

    async def memory_get(self, token: str, namespace: str, key: str) -> dict[str, Any]:
        self._auth(token, "memory:read")
        entry = await self._memory.get(namespace, key)
        if entry is None:
            raise KeyError(f"{namespace}/{key} not found")
        return {
            "namespace": entry.namespace, "key": entry.key, "value": entry.value,
            "summary": entry.summary, "tags": entry.tags,
        }

    async def memory_search(self, token: str, namespace: str, query: str) -> list[dict[str, Any]]:
        self._auth(token, "memory:read")
        entries = await self._memory.search(namespace, query)
        return [
            {"key": e.key, "summary": e.summary, "tags": e.tags, "size_bytes": e.size_bytes}
            for e in entries
        ]

    async def memory_set(self, token: str, namespace: str, key: str, value: str,
                         summary: str | None, tags: list[str]) -> dict[str, Any]:
        subject = self._auth(token, "memory:write")
        await self._memory.set(namespace, key, value, summary=summary, tags=tags)
        self._audit.log(AuditEvent(
            subject=subject, operation="memory.set", resource=f"{namespace}/{key}",
            outcome="allowed", details={"size": len(value)},
        ))
        return {"namespace": namespace, "key": key}

    async def memory_delete(self, token: str, namespace: str, key: str) -> dict[str, Any]:
        subject = self._auth(token, "memory:write")
        await self._memory.delete(namespace, key)
        self._audit.log(AuditEvent(
            subject=subject, operation="memory.delete", resource=f"{namespace}/{key}",
            outcome="allowed", details={},
        ))
        return {"deleted": True}

    async def memory_tag(self, token: str, namespace: str, key: str, tags: list[str]) -> dict[str, Any]:
        subject = self._auth(token, "memory:write")
        entry = await self._memory.get(namespace, key)
        if entry is None:
            raise KeyError(f"{namespace}/{key} not found")
        merged_tags = list(set(entry.tags) | set(tags))
        await self._memory.set(namespace, key, entry.value, summary=entry.summary, tags=merged_tags)
        return {"namespace": namespace, "key": key, "tags": merged_tags}
