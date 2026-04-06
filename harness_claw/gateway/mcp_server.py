from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness_claw.gateway.audit import AuditEvent, AuditLogger
from harness_claw.gateway.auth import AuthError, TokenStore
from harness_claw.gateway.broker import Broker
from harness_claw.gateway.capability import CapabilityConnector
from harness_claw.gateway.event_bus import EventBus
from harness_claw.gateway.memory import MemoryStore
from harness_claw.gateway.policy import PolicyEngine
from harness_claw.gateway.tool_registry import ToolRegistry
from harness_claw.gateway.tool_store import ToolDef
from harness_claw.runtime.plan_executor import PlanExecutor


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
        workflow_engine: Any | None = None,
        event_bus: EventBus | None = None,
        tool_registry: ToolRegistry | None = None,
        runner: Any | None = None,
        pool_connector: Any | None = None,
        registry: Any | None = None,
    ) -> None:
        self._tokens = token_store
        self._policy = policy
        self._connectors = connectors
        self._broker = broker
        self._memory = memory
        self._audit = audit
        self._spawn_callback = spawn_callback  # async (role_id, working_dir) → session_id
        self._workflow_engine = workflow_engine
        self._event_bus = event_bus
        self._tool_registry = tool_registry
        self._runner = runner
        self._pool_connector = pool_connector
        self._registry = registry

    def _auth(self, token: str, operation: str) -> str:
        """Validate token and check scope. Returns subject. Raises on failure."""
        try:
            subject, scopes = self._tokens.validate(token)
        except AuthError as e:
            self._audit.log(AuditEvent(
                subject="unknown", operation=operation, resource="",
                outcome="error", details={"reason": str(e)},
            ))
            raise
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

    async def agent_delegate(
        self,
        token: str,
        caps: list[str],
        instructions: str,
        context: dict[str, Any] | None = None,
        callback: bool = False,
        priority: int = 2,
        max_cost: float | None = None,
        max_time: int | None = None,
        allowed_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        subject = self._auth(token, "agent:delegate")
        task_id = await self._broker.delegate(
            delegated_by=subject,
            caps=caps,
            instructions=instructions,
            context=context,
            callback=callback,
            priority=priority,
            max_cost=max_cost,
            max_time=max_time,
            allowed_tools=allowed_tools,
        )
        task = self._broker.get_task(task_id)
        self._audit.log(AuditEvent(
            subject=subject, operation="agent.delegate", resource=task_id,
            outcome="allowed", details={
                "caps": caps,
                "callback": callback,
                "priority": priority,
                "delegated_to": task.delegated_to if task else None,
                "max_cost": max_cost,
                "max_time": max_time,
            },
        ))
        return {"task_id": task_id}

    async def agent_status(self, token: str, task_id: str) -> dict[str, Any]:
        subject = self._auth(token, "agent:delegate")
        task = self._broker.get_task(task_id)
        if task is None:
            self._audit.log(AuditEvent(
                subject=subject, operation="agent.status", resource=task_id,
                outcome="error", details={"reason": "task not found"},
            ))
            raise KeyError(f"task {task_id!r} not found")
        self._audit.log(AuditEvent(
            subject=subject, operation="agent.status", resource=task_id,
            outcome="allowed", details={"status": task.status, "progress_pct": task.progress_pct},
        ))
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
            outcome="allowed", details={
                "delegated_by": task.delegated_by,
                "result_type": type(task.result).__name__,
            },
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

    # --- Workflow tools ---

    async def workflow_run(self, token: str, workflow_id: str, input: str) -> dict[str, Any]:
        subject = self._auth(token, "agent:delegate")
        if self._workflow_engine is None:
            raise RuntimeError("workflow engine not configured")
        run_id = await self._workflow_engine.start(
            workflow_id=workflow_id,
            input=input,
            initiated_by=subject,
        )
        self._audit.log(AuditEvent(
            subject=subject, operation="workflow.run", resource=workflow_id,
            outcome="allowed", details={"run_id": run_id},
        ))
        return {"run_id": run_id}

    async def execute_plan(self, token: str, plan_path: str) -> str:
        subject = self._auth(token, "agent:delegate")

        if not Path(plan_path).exists():
            raise FileNotFoundError(f"Plan file not found: {plan_path!r}")

        if self._event_bus is None:
            raise RuntimeError("execute_plan requires event_bus — not configured")

        progress_log: list[str] = []

        async def on_progress(msg: str) -> None:
            progress_log.append(msg)

        pool = None
        if (
            self._runner is not None
            and self._pool_connector is not None
            and self._registry is not None
        ):
            from harness_claw.runtime.agent_pool import AgentPool
            _git = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(Path(plan_path).parent),
                capture_output=True,
                text=True,
            )
            working_dir = _git.stdout.strip() if _git.returncode == 0 else str(Path(plan_path).parent)
            pool = AgentPool(
                runner=self._runner,
                connector=self._pool_connector,
                registry=self._registry,
                working_dir=working_dir,
            )

        executor = PlanExecutor(
            broker=self._broker,
            event_bus=self._event_bus,
            on_progress=on_progress,
            pool=pool,
        )

        try:
            summary = await executor.execute(plan_path)
            self._audit.log(AuditEvent(
                subject=subject, operation="execute_plan", resource=plan_path,
                outcome="allowed", details={"plan_path": plan_path},
            ))
        except Exception as exc:
            self._audit.log(AuditEvent(
                subject=subject, operation="execute_plan", resource=plan_path,
                outcome="error", details={"reason": str(exc)},
            ))
            raise
        finally:
            if pool is not None:
                await pool.release_all()

        progress_output = "\n".join(progress_log)
        return f"{progress_output}\n\n---\n\n{summary}"

    async def tool_register(
        self,
        token: str,
        id: str,
        name: str,
        description: str,
        input_schema: dict,
        output_schema: dict,
        adapter: str,
        adapter_config: dict,
        cap_allowlist: list[str],
        allowed_roles: list[str],
        required_scopes: list[str],
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        subject = self._auth(token, "tool:write")

        if self._tool_registry is None:
            raise RuntimeError("tool_register requires tool_registry — not configured")

        now = datetime.now(timezone.utc).isoformat()
        tool = ToolDef(
            id=id,
            name=name,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            adapter=adapter,
            adapter_config=adapter_config,
            cap_allowlist=cap_allowlist,
            allowed_roles=allowed_roles,
            required_scopes=required_scopes,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )
        self._tool_registry.save(tool)

        self._audit.log(AuditEvent(
            subject=subject, operation="tool.register", resource=id,
            outcome="allowed", details={"adapter": adapter},
        ))
        return {"id": id, "status": "registered"}

    # --- Memory tools ---

    async def memory_namespaces(self, token: str) -> list[str]:
        subject = self._auth(token, "memory:read")
        namespaces = await self._memory.namespaces()
        self._audit.log(AuditEvent(
            subject=subject, operation="memory.namespaces", resource="",
            outcome="allowed", details={"count": len(namespaces)},
        ))
        return namespaces

    async def memory_list(self, token: str, namespace: str) -> list[dict[str, Any]]:
        subject = self._auth(token, "memory:read")
        entries = await self._memory.list(namespace)
        self._audit.log(AuditEvent(
            subject=subject, operation="memory.list", resource=namespace,
            outcome="allowed", details={"count": len(entries)},
        ))
        return [
            {"key": e.key, "summary": e.summary, "tags": e.tags,
             "size_bytes": e.size_bytes, "updated_at": e.updated_at}
            for e in entries
        ]

    async def memory_get(self, token: str, namespace: str, key: str) -> dict[str, Any]:
        subject = self._auth(token, "memory:read")
        entry = await self._memory.get(namespace, key)
        if entry is None:
            self._audit.log(AuditEvent(
                subject=subject, operation="memory.get", resource=f"{namespace}/{key}",
                outcome="error", details={"reason": "not found"},
            ))
            raise KeyError(f"{namespace}/{key} not found")
        self._audit.log(AuditEvent(
            subject=subject, operation="memory.get", resource=f"{namespace}/{key}",
            outcome="allowed", details={"size_bytes": entry.size_bytes},
        ))
        return {
            "namespace": entry.namespace, "key": entry.key, "value": entry.value,
            "summary": entry.summary, "tags": entry.tags,
        }

    async def memory_search(self, token: str, namespace: str, query: str) -> list[dict[str, Any]]:
        subject = self._auth(token, "memory:read")
        entries = await self._memory.search(namespace, query)
        self._audit.log(AuditEvent(
            subject=subject, operation="memory.search", resource=namespace,
            outcome="allowed", details={"query": query, "hits": len(entries)},
        ))
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
        entry = await self._memory.get(namespace, key)
        await self._memory.delete(namespace, key)
        self._audit.log(AuditEvent(
            subject=subject, operation="memory.delete", resource=f"{namespace}/{key}",
            outcome="allowed", details={"size_bytes": entry.size_bytes if entry else None},
        ))
        return {"deleted": True}

    async def memory_tag(self, token: str, namespace: str, key: str, tags: list[str]) -> dict[str, Any]:
        subject = self._auth(token, "memory:write")
        entry = await self._memory.get(namespace, key)
        if entry is None:
            self._audit.log(AuditEvent(
                subject=subject, operation="memory.tag", resource=f"{namespace}/{key}",
                outcome="error", details={"reason": "not found"},
            ))
            raise KeyError(f"{namespace}/{key} not found")
        merged_tags = list(set(entry.tags) | set(tags))
        await self._memory.set(namespace, key, entry.value, summary=entry.summary, tags=merged_tags)
        self._audit.log(AuditEvent(
            subject=subject, operation="memory.tag", resource=f"{namespace}/{key}",
            outcome="allowed", details={"tags_added": tags, "tags_total": merged_tags},
        ))
        return {"namespace": namespace, "key": key, "tags": merged_tags}
