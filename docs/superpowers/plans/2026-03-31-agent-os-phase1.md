# Agent OS Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure HarnessClaw into a gateway-first agent OS with auth, policy, capability registry, MCP interface, task delegation, and memory.

**Architecture:** The FastAPI server is reorganized into `gateway/`, `runtime/`, and `api/` packages. The gateway is the kernel — every client (dashboard, PTY agents, external callers) connects through it. PTY agents get MCP tools auto-injected via a per-session `.claude/settings.json` written before spawn.

**Tech Stack:** Python 3.12, FastAPI, `mcp>=1.3.0` (FastMCP), SQLite FTS5, ptyprocess, React 18, TypeScript, xterm.js, Tailwind CSS.

---

## File Map

```
# New packages
harness_claw/gateway/__init__.py         CREATE
harness_claw/gateway/auth.py             CREATE  — TokenStore, issue_token, validate_token, revoke_token
harness_claw/gateway/policy.py           CREATE  — PolicyEngine protocol, LocalPolicyEngine
harness_claw/gateway/capability.py       CREATE  — CapabilityConnector, LocalConnector, GatewayConnector, AgentAdvertisement
harness_claw/gateway/audit.py            CREATE  — AuditEvent, AuditLogger (replaces top-level stub)
harness_claw/gateway/memory.py           CREATE  — MemoryStore protocol, MemoryEntry, SqliteMemoryStore
harness_claw/gateway/broker.py           CREATE  — Task, TaskStore, TaskDispatcher, LocalDispatcher, Broker
harness_claw/gateway/mcp_server.py       CREATE  — FastMCP server, all agent + memory tools

harness_claw/runtime/__init__.py         CREATE
harness_claw/runtime/pty_session.py      MOVE    — from harness_claw/pty_session.py (unchanged)
harness_claw/runtime/cost_poller.py      MOVE    — from harness_claw/cost_poller.py (unchanged)
harness_claw/runtime/session_store.py    MOVE    — from harness_claw/session_store.py (unchanged)
harness_claw/runtime/job_runner.py       MOVE+MODIFY — updated imports + token injection + capability registration

harness_claw/api/__init__.py             CREATE
harness_claw/api/sessions.py             CREATE  — /api/sessions routes
harness_claw/api/roles.py                CREATE  — /api/roles routes
harness_claw/api/websocket.py            CREATE  — /ws/terminal + task event broadcasting

# Modified
harness_claw/server.py                   REWRITE — gateway-first FastAPI assembly
harness_claw/role_registry.py           MODIFY  — parse scopes, caps, connectors, broker, memory, policy
harness_claw/model.py                   MODIFY  — add GatewayConfig dataclass

# Deleted
harness_claw/gateway_api.py             DELETE  — empty stub, superseded
harness_claw/audit.py                   DELETE  — empty stub, superseded

# Frontend
ui/src/types.ts                         MODIFY  — add TaskRecord, task WS events, memory types
ui/src/App.tsx                          MODIFY  — wire task WS events, pass tasks to TasksTab
ui/src/components/TasksTab.tsx          REWRITE — live task board + expandable inline xterm
ui/src/components/MemoryTab.tsx         CREATE  — browse namespaces, inspect, delete
ui/src/components/TabPanel.tsx          MODIFY  — add 'memory' tab

# Tests
tests/gateway/__init__.py               CREATE
tests/gateway/test_auth.py              CREATE
tests/gateway/test_policy.py            CREATE
tests/gateway/test_capability.py        CREATE
tests/gateway/test_audit.py             CREATE
tests/gateway/test_memory.py            CREATE
tests/gateway/test_broker.py            CREATE
tests/gateway/test_mcp.py               CREATE
tests/runtime/__init__.py               CREATE
tests/runtime/test_pty_session.py       MOVE    — from tests/test_pty_session.py (update imports)
tests/runtime/test_cost_poller.py       MOVE    — from tests/test_cost_poller.py (update imports)
tests/runtime/test_session_store.py     MOVE    — from tests/test_session_store.py (update imports)
tests/runtime/test_session.py           MOVE    — from tests/test_session.py (update imports)
tests/runtime/test_job_runner.py        MOVE    — from tests/test_job_runner.py (update imports)
tests/api/__init__.py                   CREATE
tests/api/test_sessions.py              CREATE
```

---

## Task 1: Project restructure — create packages, move files, fix imports

**Files:**
- Create: `harness_claw/gateway/__init__.py`, `harness_claw/runtime/__init__.py`, `harness_claw/api/__init__.py`
- Move+update: `harness_claw/runtime/pty_session.py`, `harness_claw/runtime/cost_poller.py`, `harness_claw/runtime/session_store.py`, `harness_claw/runtime/job_runner.py`
- Move+update: all `tests/runtime/test_*.py`
- Delete: `harness_claw/gateway_api.py`, `harness_claw/audit.py`

- [ ] **Step 1: Create package directories and `__init__.py` files**

```bash
mkdir -p harness_claw/gateway harness_claw/runtime harness_claw/api
mkdir -p tests/gateway tests/runtime tests/api
touch harness_claw/gateway/__init__.py
touch harness_claw/runtime/__init__.py
touch harness_claw/api/__init__.py
touch tests/gateway/__init__.py
touch tests/runtime/__init__.py
touch tests/api/__init__.py
```

- [ ] **Step 2: Copy runtime files to new location with updated imports**

Copy `harness_claw/pty_session.py` → `harness_claw/runtime/pty_session.py` (no import changes needed — it only imports stdlib + ptyprocess).

Copy `harness_claw/cost_poller.py` → `harness_claw/runtime/cost_poller.py` (no import changes needed).

Copy `harness_claw/session_store.py` → `harness_claw/runtime/session_store.py`. Update one import:
```python
# Change:
from harness_claw.session import Session
# To: (no change needed — session.py stays at top level)
from harness_claw.session import Session
```

Copy `harness_claw/job_runner.py` → `harness_claw/runtime/job_runner.py`. Update imports:
```python
# Change these four lines:
from harness_claw.cost_poller import CostPoller, _encode_cwd
from harness_claw.pty_session import PtySession
from harness_claw.role_registry import RoleRegistry
from harness_claw.session import Session
from harness_claw.session_store import SessionStore

# To:
from harness_claw.runtime.cost_poller import CostPoller, _encode_cwd
from harness_claw.runtime.pty_session import PtySession
from harness_claw.role_registry import RoleRegistry
from harness_claw.session import Session
from harness_claw.runtime.session_store import SessionStore
```

- [ ] **Step 3: Move test files to tests/runtime/ with updated imports**

Copy `tests/test_pty_session.py` → `tests/runtime/test_pty_session.py`:
```python
# Change:
from harness_claw.pty_session import PtySession
# To:
from harness_claw.runtime.pty_session import PtySession

# Change the mock patch path:
# "harness_claw.pty_session.select" → "harness_claw.runtime.pty_session.select"
```

Copy `tests/test_cost_poller.py` → `tests/runtime/test_cost_poller.py`:
```python
# Update any harness_claw.cost_poller imports to harness_claw.runtime.cost_poller
```

Copy `tests/test_session_store.py` → `tests/runtime/test_session_store.py`:
```python
# Update any harness_claw.session_store imports to harness_claw.runtime.session_store
```

Copy `tests/test_session.py` → `tests/runtime/test_session.py` (no import changes — session.py stays at top level).

Copy `tests/test_job_runner.py` → `tests/runtime/test_job_runner.py`:
```python
# Change:
from harness_claw.job_runner import JobRunner
from harness_claw.session_store import SessionStore
# To:
from harness_claw.runtime.job_runner import JobRunner
from harness_claw.runtime.session_store import SessionStore

# Change mock patch paths:
# "harness_claw.job_runner.PtySession" → "harness_claw.runtime.job_runner.PtySession"
# "harness_claw.job_runner.CostPoller" → "harness_claw.runtime.job_runner.CostPoller"
```

- [ ] **Step 4: Update server.py imports to point at runtime package**

Edit `harness_claw/server.py` — change these imports:
```python
# Change:
from harness_claw.session_store import SessionStore
from harness_claw.job_runner import JobRunner
# To:
from harness_claw.runtime.session_store import SessionStore
from harness_claw.runtime.job_runner import JobRunner
```

- [ ] **Step 5: Delete old files**

Copy `tests/test_role_registry.py` → `tests/runtime/test_role_registry.py` BEFORE deleting (no import changes needed).

```bash
rm harness_claw/pty_session.py
rm harness_claw/cost_poller.py
rm harness_claw/session_store.py
rm harness_claw/job_runner.py
rm harness_claw/gateway_api.py
rm harness_claw/audit.py
rm tests/test_pty_session.py
rm tests/test_cost_poller.py
rm tests/test_session_store.py
rm tests/test_session.py
rm tests/test_job_runner.py
rm tests/test_role_registry.py
```

- [ ] **Step 6: Run existing tests to confirm nothing broke**

```bash
uv run pytest tests/runtime/ -v
```

Expected: all tests pass (same tests, just moved).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: restructure into gateway/, runtime/, api/ packages"
```

---

## Task 2: Update agents.yaml schema + RoleRegistry

**Files:**
- Modify: `harness_claw/role_registry.py`
- Modify: `agents.yaml`

- [ ] **Step 1: Write the failing test**

Create `tests/runtime/test_role_registry.py`:
```python
from pathlib import Path
import tempfile
import pytest
from harness_claw.role_registry import RoleRegistry, RoleConfig, GatewayConfig

YAML = """
policy:
  engine: local

memory:
  backend: sqlite
  path: ./memory.db

broker:
  dispatcher: local

connectors:
  - type: local
  - type: gateway
    heartbeat_ttl: 30
    bootstrap_token: "testtoken"

roles:
  - id: orchestrator
    name: Orchestrator
    provider: claude-code
    model: claude-sonnet-4-6
    system_prompt: "You orchestrate."
    max_tokens: 8192
    scopes: [agent:list, agent:delegate, agent:spawn, memory:read, memory:write]
    caps: [orchestration, planning]
  - id: coder
    name: Coder
    provider: claude-code
    model: claude-sonnet-4-6
    system_prompt: "You write code."
    scopes: [agent:list, memory:read, memory:write]
    caps: [python, typescript]
"""

@pytest.fixture
def registry(tmp_path):
    p = tmp_path / "agents.yaml"
    p.write_text(YAML)
    return RoleRegistry(p)

def test_role_scopes_parsed(registry):
    role = registry.get("orchestrator")
    assert role.scopes == ["agent:list", "agent:delegate", "agent:spawn", "memory:read", "memory:write"]

def test_role_caps_parsed(registry):
    role = registry.get("coder")
    assert role.caps == ["python", "typescript"]

def test_all_agents_get_report_scope(registry):
    # agent:report is injected automatically for all roles
    role = registry.get("coder")
    assert "agent:report" in role.scopes

def test_gateway_config_parsed(registry):
    cfg = registry.gateway_config
    assert cfg.policy_engine == "local"
    assert cfg.memory_backend == "sqlite"
    assert cfg.dispatcher == "local"
    assert cfg.gateway_bootstrap_token == "testtoken"
    assert cfg.gateway_heartbeat_ttl == 30
```

- [ ] **Step 2: Run to confirm it fails**

```bash
uv run pytest tests/runtime/test_role_registry.py -v
```

Expected: FAIL — `GatewayConfig` not defined, `role.scopes` attribute missing.

- [ ] **Step 3: Update RoleConfig and RoleRegistry**

Edit `harness_claw/role_registry.py`:
```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class RoleConfig:
    id: str
    name: str
    provider: str
    model: str
    system_prompt: str
    max_tokens: int = 8192
    scopes: list[str] = field(default_factory=list)
    caps: list[str] = field(default_factory=list)


@dataclass
class GatewayConfig:
    policy_engine: str = "local"
    memory_backend: str = "sqlite"
    memory_path: str = "./memory.db"
    dispatcher: str = "local"
    gateway_bootstrap_token: str = ""
    gateway_heartbeat_ttl: int = 30


_DEFAULT_SCOPES = ["agent:report"]


class RoleRegistry:
    def __init__(self, path: Path) -> None:
        self._roles: dict[str, RoleConfig] = {}
        data = yaml.safe_load(path.read_text())

        # Parse gateway config sections
        policy = data.get("policy", {})
        memory = data.get("memory", {})
        broker = data.get("broker", {})
        gateway_connector = next(
            (c for c in data.get("connectors", []) if c.get("type") == "gateway"),
            {}
        )
        self.gateway_config = GatewayConfig(
            policy_engine=policy.get("engine", "local"),
            memory_backend=memory.get("backend", "sqlite"),
            memory_path=memory.get("path", "./memory.db"),
            dispatcher=broker.get("dispatcher", "local"),
            gateway_bootstrap_token=gateway_connector.get("bootstrap_token", ""),
            gateway_heartbeat_ttl=gateway_connector.get("heartbeat_ttl", 30),
        )

        for item in data.get("roles", []):
            scopes = list(item.get("scopes", []))
            # agent:report is granted to every role by default
            if "agent:report" not in scopes:
                scopes.append("agent:report")
            role = RoleConfig(
                id=item["id"],
                name=item["name"],
                provider=item.get("provider", "claude-code"),
                model=item["model"],
                system_prompt=item["system_prompt"],
                max_tokens=item.get("max_tokens", 8192),
                scopes=scopes,
                caps=list(item.get("caps", [])),
            )
            self._roles[role.id] = role

    def all(self) -> list[RoleConfig]:
        return list(self._roles.values())

    def get(self, role_id: str) -> RoleConfig | None:
        return self._roles.get(role_id)
```

- [ ] **Step 4: Update agents.yaml**

```yaml
policy:
  engine: local

memory:
  backend: sqlite
  path: ./memory.db

broker:
  dispatcher: local

connectors:
  - type: local
  - type: gateway
    heartbeat_ttl: 30
    bootstrap_token: "changeme"

roles:
  - id: orchestrator
    name: Orchestrator
    provider: claude-code
    model: claude-opus-4-6
    system_prompt: "You are an orchestrator agent. You break complex tasks into subtasks and delegate them to specialist agents."
    max_tokens: 8192
    scopes: [agent:list, agent:delegate, agent:spawn, memory:read, memory:write]
    caps: [orchestration, planning]

  - id: code-writer
    name: Code Writer
    provider: claude-code
    model: claude-sonnet-4-6
    system_prompt: "You write clean, well-tested code."
    max_tokens: 8192
    scopes: [agent:list, memory:read, memory:write]
    caps: [python, typescript, testing]
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/runtime/test_role_registry.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add harness_claw/role_registry.py agents.yaml tests/runtime/test_role_registry.py
git commit -m "feat: extend RoleRegistry with scopes, caps, and GatewayConfig"
```

---

## Task 3: Auth module

**Files:**
- Create: `harness_claw/gateway/auth.py`
- Create: `tests/gateway/test_auth.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/gateway/test_auth.py`:
```python
from __future__ import annotations
import pytest
from harness_claw.gateway.auth import TokenStore, AuthError


def test_issue_and_validate_token():
    store = TokenStore()
    token = store.issue("session-1", ["agent:list", "agent:delegate"])
    subject, scopes = store.validate(token)
    assert subject == "session-1"
    assert "agent:list" in scopes
    assert "agent:delegate" in scopes


def test_validate_unknown_token_raises():
    store = TokenStore()
    with pytest.raises(AuthError, match="invalid"):
        store.validate("not-a-real-token")


def test_revoke_makes_token_invalid():
    store = TokenStore()
    token = store.issue("session-1", ["agent:list"])
    store.revoke(token)
    with pytest.raises(AuthError, match="invalid"):
        store.validate(token)


def test_has_scope_returns_true_when_scope_present():
    store = TokenStore()
    token = store.issue("s1", ["agent:list", "memory:read"])
    _, scopes = store.validate(token)
    assert "agent:list" in scopes
    assert "memory:write" not in scopes


def test_issue_returns_unique_tokens():
    store = TokenStore()
    t1 = store.issue("s1", [])
    t2 = store.issue("s2", [])
    assert t1 != t2
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/gateway/test_auth.py -v
```

Expected: FAIL — `harness_claw.gateway.auth` not found.

- [ ] **Step 3: Implement auth module**

Create `harness_claw/gateway/auth.py`:
```python
from __future__ import annotations

import secrets


class AuthError(Exception):
    pass


class TokenStore:
    """In-memory token store. Tokens are revoked when the session ends."""

    def __init__(self) -> None:
        # token → (subject, scopes)
        self._tokens: dict[str, tuple[str, list[str]]] = {}

    def issue(self, subject: str, scopes: list[str]) -> str:
        token = secrets.token_urlsafe(32)
        self._tokens[token] = (subject, list(scopes))
        return token

    def validate(self, token: str) -> tuple[str, list[str]]:
        """Return (subject, scopes) or raise AuthError."""
        entry = self._tokens.get(token)
        if entry is None:
            raise AuthError("invalid or expired token")
        return entry

    def revoke(self, token: str) -> None:
        self._tokens.pop(token, None)

    def revoke_by_subject(self, subject: str) -> None:
        """Revoke all tokens for a given subject (session_id)."""
        to_remove = [t for t, (s, _) in self._tokens.items() if s == subject]
        for t in to_remove:
            del self._tokens[t]
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/gateway/test_auth.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/gateway/auth.py tests/gateway/test_auth.py
git commit -m "feat: add in-memory TokenStore with issue/validate/revoke"
```

---

## Task 4: Policy engine

**Files:**
- Create: `harness_claw/gateway/policy.py`
- Create: `tests/gateway/test_policy.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/gateway/test_policy.py`:
```python
from __future__ import annotations
import pytest
from harness_claw.gateway.policy import LocalPolicyEngine
from harness_claw.model import PolicyDecision


def test_allowed_when_scope_present():
    engine = LocalPolicyEngine()
    decision = engine.check(
        subject="s1",
        scopes=["agent:list", "agent:delegate"],
        operation="agent:list",
    )
    assert decision.allowed is True


def test_denied_when_scope_missing():
    engine = LocalPolicyEngine()
    decision = engine.check(
        subject="s1",
        scopes=["agent:list"],
        operation="agent:delegate",
    )
    assert decision.allowed is False
    assert "agent:delegate" in decision.reason


def test_denied_with_empty_scopes():
    engine = LocalPolicyEngine()
    decision = engine.check(subject="s1", scopes=[], operation="memory:write")
    assert decision.allowed is False


def test_allowed_with_wildcard_scope():
    engine = LocalPolicyEngine()
    decision = engine.check(subject="s1", scopes=["*"], operation="agent:spawn")
    assert decision.allowed is True
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/gateway/test_policy.py -v
```

Expected: FAIL — `harness_claw.gateway.policy` not found.

- [ ] **Step 3: Implement policy module**

Create `harness_claw/gateway/policy.py`:
```python
from __future__ import annotations

from typing import Protocol

from harness_claw.model import PolicyDecision


class PolicyEngine(Protocol):
    def check(self, subject: str, scopes: list[str], operation: str) -> PolicyDecision:
        ...


class LocalPolicyEngine:
    """Scope-based policy enforcement. Phase 1 default."""

    def check(self, subject: str, scopes: list[str], operation: str) -> PolicyDecision:
        if "*" in scopes or operation in scopes:
            return PolicyDecision(allowed=True)
        return PolicyDecision(
            allowed=False,
            reason=f"scope '{operation}' required but not granted to subject '{subject}'",
        )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/gateway/test_policy.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/gateway/policy.py tests/gateway/test_policy.py
git commit -m "feat: add PolicyEngine protocol and LocalPolicyEngine"
```

---

## Task 5: Capability registry

**Files:**
- Create: `harness_claw/gateway/capability.py`
- Create: `tests/gateway/test_capability.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/gateway/test_capability.py`:
```python
from __future__ import annotations
import pytest
from harness_claw.gateway.capability import LocalConnector, AgentAdvertisement


def make_agent(session_id: str, caps: list[str], task_count: int = 0) -> AgentAdvertisement:
    return AgentAdvertisement(
        session_id=session_id,
        role_id="coder",
        caps=caps,
        status="idle",
        task_count=task_count,
        connector="local",
    )


async def test_register_and_query():
    conn = LocalConnector()
    agent = make_agent("s1", ["python", "typescript"])
    await conn.register(agent)
    results = await conn.query(["python"])
    assert any(a.session_id == "s1" for a in results)


async def test_query_requires_all_caps():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    await conn.register(make_agent("s2", ["python", "typescript"]))
    results = await conn.query(["python", "typescript"])
    assert len(results) == 1
    assert results[0].session_id == "s2"


async def test_deregister_removes_agent():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    await conn.deregister("s1")
    results = await conn.query(["python"])
    assert results == []


async def test_query_returns_least_loaded_first():
    conn = LocalConnector()
    await conn.register(make_agent("busy", ["python"], task_count=5))
    await conn.register(make_agent("idle", ["python"], task_count=0))
    results = await conn.query(["python"])
    assert results[0].session_id == "idle"


async def test_query_empty_caps_returns_all():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    await conn.register(make_agent("s2", ["typescript"]))
    results = await conn.query([])
    assert len(results) == 2
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/gateway/test_capability.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement capability module**

Create `harness_claw/gateway/capability.py`:
```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class AgentAdvertisement:
    session_id: str
    role_id: str
    caps: list[str]
    status: str          # idle | busy | killed
    task_count: int
    connector: str       # "local" | "gateway" | custom


class CapabilityConnector(Protocol):
    async def register(self, agent: AgentAdvertisement) -> None: ...
    async def deregister(self, session_id: str) -> None: ...
    async def query(self, caps: list[str]) -> list[AgentAdvertisement]: ...


class LocalConnector:
    """Tracks HarnessClaw's own PTY sessions."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentAdvertisement] = {}

    async def register(self, agent: AgentAdvertisement) -> None:
        self._agents[agent.session_id] = agent

    async def deregister(self, session_id: str) -> None:
        self._agents.pop(session_id, None)

    async def query(self, caps: list[str]) -> list[AgentAdvertisement]:
        cap_set = set(caps)
        matches = [
            a for a in self._agents.values()
            if cap_set.issubset(set(a.caps))
        ]
        return sorted(matches, key=lambda a: a.task_count)

    def update_task_count(self, session_id: str, delta: int) -> None:
        if session_id in self._agents:
            self._agents[session_id].task_count += delta

    def set_status(self, session_id: str, status: str) -> None:
        if session_id in self._agents:
            self._agents[session_id].status = status
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/gateway/test_capability.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/gateway/capability.py tests/gateway/test_capability.py
git commit -m "feat: add CapabilityConnector protocol and LocalConnector"
```

---

## Task 6: Audit log

**Files:**
- Create: `harness_claw/gateway/audit.py`
- Create: `tests/gateway/test_audit.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/gateway/test_audit.py`:
```python
from __future__ import annotations
import json
import pytest
from harness_claw.gateway.audit import AuditLogger, AuditEvent


def test_log_writes_jsonl(tmp_path):
    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(path)
    logger.log(AuditEvent(
        subject="s1",
        operation="agent.delegate",
        resource="task-123",
        outcome="allowed",
        details={"caps": ["python"]},
    ))
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["subject"] == "s1"
    assert event["operation"] == "agent.delegate"
    assert event["outcome"] == "allowed"
    assert "event_id" in event
    assert "timestamp" in event


def test_log_appends_multiple_events(tmp_path):
    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(path)
    logger.log(AuditEvent(subject="s1", operation="op1", resource="r1", outcome="allowed", details={}))
    logger.log(AuditEvent(subject="s2", operation="op2", resource="r2", outcome="denied", details={}))
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2


def test_log_creates_file_if_missing(tmp_path):
    path = tmp_path / "subdir" / "audit.jsonl"
    logger = AuditLogger(path)
    logger.log(AuditEvent(subject="s1", operation="op", resource="r", outcome="allowed", details={}))
    assert path.exists()
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/gateway/test_audit.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement audit module**

Create `harness_claw/gateway/audit.py`:
```python
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class AuditEvent:
    subject: str
    operation: str
    resource: str
    outcome: str          # "allowed" | "denied" | "error"
    details: dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class AuditLogger:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: AuditEvent) -> None:
        with self._path.open("a") as f:
            f.write(json.dumps(asdict(event)) + "\n")
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/gateway/test_audit.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/gateway/audit.py tests/gateway/test_audit.py
git commit -m "feat: add AuditLogger writing append-only JSONL"
```

---

## Task 7: Memory store

**Files:**
- Create: `harness_claw/gateway/memory.py`
- Create: `tests/gateway/test_memory.py`
- Modify: `pyproject.toml` (no new deps needed — sqlite3 is stdlib)

- [ ] **Step 1: Write the failing tests**

Create `tests/gateway/test_memory.py`:
```python
from __future__ import annotations
import pytest
from harness_claw.gateway.memory import SqliteMemoryStore, MemoryEntry


@pytest.fixture
def store(tmp_path):
    return SqliteMemoryStore(tmp_path / "memory.db")


async def test_set_and_get(store):
    await store.set("project:test", "key1", "value1", summary="a note", tags=["tag1"])
    entry = await store.get("project:test", "key1")
    assert entry is not None
    assert entry.value == "value1"
    assert entry.summary == "a note"
    assert "tag1" in entry.tags


async def test_get_missing_returns_none(store):
    result = await store.get("project:test", "missing")
    assert result is None


async def test_delete_removes_entry(store):
    await store.set("project:test", "k", "v", summary=None, tags=[])
    await store.delete("project:test", "k")
    assert await store.get("project:test", "k") is None


async def test_list_returns_entries_in_namespace(store):
    await store.set("ns1", "a", "va", summary=None, tags=[])
    await store.set("ns1", "b", "vb", summary=None, tags=[])
    await store.set("ns2", "c", "vc", summary=None, tags=[])
    entries = await store.list("ns1")
    keys = [e.key for e in entries]
    assert "a" in keys
    assert "b" in keys
    assert "c" not in keys


async def test_search_finds_by_content(store):
    await store.set("project:x", "notes", "authentication token design", summary=None, tags=[])
    await store.set("project:x", "other", "unrelated content", summary=None, tags=[])
    results = await store.search("project:x", "authentication")
    assert any(e.key == "notes" for e in results)


async def test_namespaces_lists_used_namespaces(store):
    await store.set("ns-a", "k", "v", summary=None, tags=[])
    await store.set("ns-b", "k", "v", summary=None, tags=[])
    ns = await store.namespaces()
    assert "ns-a" in ns
    assert "ns-b" in ns


async def test_set_updates_existing_entry(store):
    await store.set("ns", "k", "original", summary=None, tags=[])
    await store.set("ns", "k", "updated", summary="new summary", tags=[])
    entry = await store.get("ns", "k")
    assert entry.value == "updated"
    assert entry.summary == "new summary"
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/gateway/test_memory.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement memory module**

Create `harness_claw/gateway/memory.py`:
```python
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


@dataclass
class MemoryEntry:
    namespace: str
    key: str
    value: str
    summary: str | None
    tags: list[str]
    size_bytes: int
    created_at: str
    updated_at: str


class MemoryStore(Protocol):
    async def set(self, namespace: str, key: str, value: str, summary: str | None, tags: list[str]) -> None: ...
    async def get(self, namespace: str, key: str) -> MemoryEntry | None: ...
    async def list(self, namespace: str) -> list[MemoryEntry]: ...
    async def search(self, namespace: str, query: str) -> list[MemoryEntry]: ...
    async def delete(self, namespace: str, key: str) -> None: ...
    async def namespaces(self) -> list[str]: ...


class SqliteMemoryStore:
    """SQLite-backed memory store with FTS5 full-text search."""

    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS memory (
                namespace TEXT NOT NULL,
                key       TEXT NOT NULL,
                value     TEXT NOT NULL,
                summary   TEXT,
                tags      TEXT NOT NULL DEFAULT '[]',
                size_bytes INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (namespace, key)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                namespace UNINDEXED,
                key UNINDEXED,
                value,
                summary,
                content='memory',
                content_rowid='rowid'
            );
            CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory BEGIN
                INSERT INTO memory_fts(rowid, namespace, key, value, summary)
                VALUES (new.rowid, new.namespace, new.key, new.value, new.summary);
            END;
            CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, namespace, key, value, summary)
                VALUES ('delete', old.rowid, old.namespace, old.key, old.value, old.summary);
            END;
            CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memory BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, namespace, key, value, summary)
                VALUES ('delete', old.rowid, old.namespace, old.key, old.value, old.summary);
                INSERT INTO memory_fts(rowid, namespace, key, value, summary)
                VALUES (new.rowid, new.namespace, new.key, new.value, new.summary);
            END;
        """)
        self._conn.commit()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _row_to_entry(self, row: sqlite3.Row) -> MemoryEntry:
        return MemoryEntry(
            namespace=row["namespace"],
            key=row["key"],
            value=row["value"],
            summary=row["summary"],
            tags=json.loads(row["tags"]),
            size_bytes=row["size_bytes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def set(self, namespace: str, key: str, value: str, summary: str | None, tags: list[str]) -> None:
        now = self._now()
        existing = self._conn.execute(
            "SELECT created_at FROM memory WHERE namespace=? AND key=?", (namespace, key)
        ).fetchone()
        created_at = existing["created_at"] if existing else now
        self._conn.execute(
            """INSERT OR REPLACE INTO memory (namespace, key, value, summary, tags, size_bytes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (namespace, key, value, summary, json.dumps(tags), len(value.encode()), created_at, now),
        )
        self._conn.commit()

    async def get(self, namespace: str, key: str) -> MemoryEntry | None:
        row = self._conn.execute(
            "SELECT * FROM memory WHERE namespace=? AND key=?", (namespace, key)
        ).fetchone()
        return self._row_to_entry(row) if row else None

    async def list(self, namespace: str) -> list[MemoryEntry]:
        rows = self._conn.execute(
            "SELECT * FROM memory WHERE namespace=? ORDER BY updated_at DESC", (namespace,)
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    async def search(self, namespace: str, query: str) -> list[MemoryEntry]:
        rows = self._conn.execute(
            """SELECT m.* FROM memory m
               JOIN memory_fts f ON m.rowid = f.rowid
               WHERE f.memory_fts MATCH ? AND m.namespace = ?
               ORDER BY rank""",
            (query, namespace),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    async def delete(self, namespace: str, key: str) -> None:
        self._conn.execute("DELETE FROM memory WHERE namespace=? AND key=?", (namespace, key))
        self._conn.commit()

    async def namespaces(self) -> list[str]:
        rows = self._conn.execute("SELECT DISTINCT namespace FROM memory ORDER BY namespace").fetchall()
        return [r["namespace"] for r in rows]
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/gateway/test_memory.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/gateway/memory.py tests/gateway/test_memory.py
git commit -m "feat: add SqliteMemoryStore with FTS5 full-text search"
```

---

## Task 8: Task model and Broker

**Files:**
- Create: `harness_claw/gateway/broker.py`
- Create: `tests/gateway/test_broker.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/gateway/test_broker.py`:
```python
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock
from harness_claw.gateway.broker import Broker, Task, TaskStore, LocalDispatcher
from harness_claw.gateway.capability import LocalConnector, AgentAdvertisement


def make_agent(session_id: str, caps: list[str]) -> AgentAdvertisement:
    return AgentAdvertisement(
        session_id=session_id, role_id="coder",
        caps=caps, status="idle", task_count=0, connector="local",
    )


async def test_delegate_creates_task():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))

    dispatcher = AsyncMock()
    broker = Broker(connectors=[conn], dispatcher=dispatcher)

    task_id = await broker.delegate(
        delegated_by="orchestrator-1",
        caps=["python"],
        instructions="Write a hello world function",
    )
    assert task_id is not None
    task = broker.get_task(task_id)
    assert task is not None
    assert task.delegated_by == "orchestrator-1"
    assert task.delegated_to == "s1"
    assert task.status == "running"
    dispatcher.dispatch.assert_called_once()


async def test_delegate_raises_when_no_agent_matches():
    conn = LocalConnector()
    dispatcher = AsyncMock()
    broker = Broker(connectors=[conn], dispatcher=dispatcher)

    with pytest.raises(ValueError, match="no agent"):
        await broker.delegate("orch-1", caps=["nonexistent-cap"], instructions="do it")


async def test_update_progress():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    broker = Broker(connectors=[conn], dispatcher=dispatcher)

    task_id = await broker.delegate("orch-1", ["python"], "do it")
    broker.update_progress(task_id, pct=50, msg="halfway there")

    task = broker.get_task(task_id)
    assert task.progress_pct == 50
    assert task.progress_msg == "halfway there"
    assert task.status == "running"


async def test_complete_task():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    broker = Broker(connectors=[conn], dispatcher=dispatcher)

    task_id = await broker.delegate("orch-1", ["python"], "do it")
    broker.complete_task(task_id, result="done!")

    task = broker.get_task(task_id)
    assert task.status == "completed"
    assert task.result == "done!"


async def test_list_tasks_returns_all():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    await conn.register(make_agent("s2", ["typescript"]))
    dispatcher = AsyncMock()
    broker = Broker(connectors=[conn], dispatcher=dispatcher)

    await broker.delegate("orch-1", ["python"], "task 1")
    await broker.delegate("orch-1", ["typescript"], "task 2")

    tasks = broker.list_tasks()
    assert len(tasks) == 2
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/gateway/test_broker.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement broker module**

Create `harness_claw/gateway/broker.py`:
```python
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from harness_claw.gateway.capability import AgentAdvertisement, CapabilityConnector


@dataclass
class Task:
    task_id: str
    delegated_by: str
    delegated_to: str
    instructions: str
    caps_requested: list[str]
    status: str = "queued"       # queued | running | completed | failed
    progress_pct: int = 0
    progress_msg: str = ""
    result: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "delegated_by": self.delegated_by,
            "delegated_to": self.delegated_to,
            "instructions": self.instructions,
            "caps_requested": self.caps_requested,
            "status": self.status,
            "progress_pct": self.progress_pct,
            "progress_msg": self.progress_msg,
            "result": self.result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class TaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    def save(self, task: Task) -> None:
        task.updated_at = datetime.now(timezone.utc).isoformat()
        self._tasks[task.task_id] = task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def all(self) -> list[Task]:
        return list(self._tasks.values())


class TaskDispatcher(Protocol):
    async def dispatch(self, task: Task, agent: AgentAdvertisement) -> None: ...
    async def cancel(self, task_id: str) -> None: ...


class LocalDispatcher:
    """Writes task instructions to the target agent's PTY via a registered write callback."""

    def __init__(self) -> None:
        # session_id → write callback (bytes → None)
        self._writers: dict[str, Any] = {}

    def register_writer(self, session_id: str, write_fn: Any) -> None:
        self._writers[session_id] = write_fn

    def unregister_writer(self, session_id: str) -> None:
        self._writers.pop(session_id, None)

    async def dispatch(self, task: Task, agent: AgentAdvertisement) -> None:
        write_fn = self._writers.get(agent.session_id)
        if write_fn is None:
            raise RuntimeError(f"No writer registered for session {agent.session_id!r}")
        # Prefix with task_id so the subagent can reference it in progress/complete calls
        payload = (
            f"\n[HARNESS_TASK:{task.task_id}]\n"
            f"{task.instructions}\n"
        ).encode()
        write_fn(payload)

    async def cancel(self, task_id: str) -> None:
        pass  # PTY cancellation handled by kill_session


class Broker:
    """Routes delegation requests to capability-matched agents."""

    def __init__(
        self,
        connectors: list[CapabilityConnector],
        dispatcher: TaskDispatcher,
    ) -> None:
        self._connectors = connectors
        self._dispatcher = dispatcher
        self._store = TaskStore()
        # Callbacks to notify on task events: (event_type, task_dict) → None
        self._listeners: list[Any] = []

    def add_listener(self, fn: Any) -> None:
        self._listeners.append(fn)

    def remove_listener(self, fn: Any) -> None:
        try:
            self._listeners.remove(fn)
        except ValueError:
            pass

    async def _notify(self, event: str, task: Task) -> None:
        for fn in list(self._listeners):
            await fn(event, task.to_dict())

    async def delegate(self, delegated_by: str, caps: list[str], instructions: str) -> str:
        # Find best-matched agent across all connectors
        candidates: list[AgentAdvertisement] = []
        for connector in self._connectors:
            candidates.extend(await connector.query(caps))

        if not candidates:
            raise ValueError(f"no agent found matching caps {caps}")

        agent = candidates[0]  # already sorted by task_count in LocalConnector

        task = Task(
            task_id=str(uuid.uuid4()),
            delegated_by=delegated_by,
            delegated_to=agent.session_id,
            instructions=instructions,
            caps_requested=caps,
            status="running",
        )
        self._store.save(task)
        await self._dispatcher.dispatch(task, agent)
        await self._notify("task.created", task)
        return task.task_id

    def update_progress(self, task_id: str, pct: int, msg: str) -> Task:
        task = self._store.get(task_id)
        if task is None:
            raise KeyError(f"task {task_id!r} not found")
        task.progress_pct = pct
        task.progress_msg = msg
        self._store.save(task)
        return task

    def complete_task(self, task_id: str, result: str) -> Task:
        task = self._store.get(task_id)
        if task is None:
            raise KeyError(f"task {task_id!r} not found")
        task.status = "completed"
        task.progress_pct = 100
        task.result = result
        self._store.save(task)
        return task

    def fail_task(self, task_id: str, reason: str) -> Task:
        task = self._store.get(task_id)
        if task is None:
            raise KeyError(f"task {task_id!r} not found")
        task.status = "failed"
        task.result = reason
        self._store.save(task)
        return task

    def get_task(self, task_id: str) -> Task | None:
        return self._store.get(task_id)

    def list_tasks(self) -> list[Task]:
        return self._store.all()
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/gateway/test_broker.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/gateway/broker.py tests/gateway/test_broker.py
git commit -m "feat: add Task model, TaskStore, LocalDispatcher, and Broker"
```

---

## Task 9: Add mcp dependency + MCP server

**Files:**
- Modify: `pyproject.toml`
- Create: `harness_claw/gateway/mcp_server.py`
- Create: `tests/gateway/test_mcp.py`

- [ ] **Step 1: Add mcp dependency**

Edit `pyproject.toml` — add `"mcp>=1.3.0"` to dependencies:
```toml
dependencies = [
    "fastapi>=0.111.0",
    "uvicorn[standard]>=0.29.0",
    "pydantic>=2.7.0",
    "pyyaml>=6.0.1",
    "anthropic>=0.30.0",
    "ptyprocess>=0.7.0",
    "mcp>=1.3.0",
]
```

```bash
uv sync
```

- [ ] **Step 2: Write the failing tests**

Create `tests/gateway/test_mcp.py`:
```python
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock
from harness_claw.gateway.auth import TokenStore
from harness_claw.gateway.policy import LocalPolicyEngine
from harness_claw.gateway.capability import LocalConnector, AgentAdvertisement
from harness_claw.gateway.broker import Broker, LocalDispatcher
from harness_claw.gateway.memory import SqliteMemoryStore
from harness_claw.gateway.audit import AuditLogger
from harness_claw.gateway.mcp_server import GatewayMCP


@pytest.fixture
def token_store():
    return TokenStore()


@pytest.fixture
def connector():
    return LocalConnector()


@pytest.fixture
def dispatcher():
    d = LocalDispatcher()
    return d


@pytest.fixture
def broker(connector, dispatcher):
    return Broker(connectors=[connector], dispatcher=dispatcher)


@pytest.fixture
def memory(tmp_path):
    return SqliteMemoryStore(tmp_path / "memory.db")


@pytest.fixture
def audit(tmp_path):
    return AuditLogger(tmp_path / "audit.jsonl")


@pytest.fixture
def gateway(token_store, connector, broker, memory, audit):
    return GatewayMCP(
        token_store=token_store,
        policy=LocalPolicyEngine(),
        connectors=[connector],
        broker=broker,
        memory=memory,
        audit=audit,
    )


async def test_agent_list_requires_valid_token(gateway):
    with pytest.raises(Exception, match="invalid|unauthorized"):
        await gateway.agent_list(token="bad-token", caps=[])


async def test_agent_list_requires_agent_list_scope(gateway, token_store):
    token = token_store.issue("s1", ["memory:read"])  # missing agent:list
    with pytest.raises(Exception, match="denied|scope"):
        await gateway.agent_list(token=token, caps=[])


async def test_agent_list_returns_matching_agents(gateway, token_store, connector):
    await connector.register(AgentAdvertisement(
        session_id="s1", role_id="coder", caps=["python"],
        status="idle", task_count=0, connector="local",
    ))
    token = token_store.issue("orch", ["agent:list"])
    results = await gateway.agent_list(token=token, caps=["python"])
    assert any(a["session_id"] == "s1" for a in results)


async def test_memory_set_and_get(gateway, token_store):
    token = token_store.issue("s1", ["memory:read", "memory:write"])
    await gateway.memory_set(token=token, namespace="project:test", key="k1", value="v1", summary=None, tags=[])
    result = await gateway.memory_get(token=token, namespace="project:test", key="k1")
    assert result["value"] == "v1"


async def test_memory_get_requires_read_scope(gateway, token_store):
    token = token_store.issue("s1", ["memory:write"])  # missing memory:read
    with pytest.raises(Exception, match="denied|scope"):
        await gateway.memory_get(token=token, namespace="ns", key="k")
```

- [ ] **Step 3: Run to confirm failure**

```bash
uv run pytest tests/gateway/test_mcp.py -v
```

Expected: FAIL — `GatewayMCP` not found.

- [ ] **Step 4: Implement MCP server**

Create `harness_claw/gateway/mcp_server.py`:
```python
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

    async def agent_complete(self, token: str, task_id: str, result: str) -> dict[str, Any]:
        subject = self._auth(token, "agent:report")
        task = self._broker.complete_task(task_id, result=result)
        self._audit.log(AuditEvent(
            subject=subject, operation="agent.complete", resource=task_id,
            outcome="allowed", details={},
        ))
        return {"task_id": task_id, "status": "completed"}

    async def agent_spawn(self, token: str, role_id: str, working_dir: str) -> dict[str, Any]:
        subject = self._auth(token, "agent:spawn")
        # Delegate actual spawn to the runner via a registered callback
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
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/gateway/test_mcp.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml harness_claw/gateway/mcp_server.py tests/gateway/test_mcp.py
git commit -m "feat: add GatewayMCP implementing all agent and memory tool logic"
```

---

## Task 10: Wire gateway into JobRunner (token injection + capability registration)

**Files:**
- Modify: `harness_claw/runtime/job_runner.py`
- Modify: `harness_claw/runtime/pty_session.py`

The `PtySession.start()` needs an optional `env` parameter and a way to write MCP config. `JobRunner.start_session()` issues a token, writes `.claude/settings.json`, and registers the agent in the capability registry.

- [ ] **Step 1: Extend PtySession to accept extra env vars**

Edit `harness_claw/runtime/pty_session.py` — update `start()` signature:
```python
async def start(self, system_prompt: str, model: str, cwd: str,
                extra_env: dict[str, str] | None = None) -> None:
    if self._proc is not None:
        raise RuntimeError(f"PtySession {self.session_id!r} is already started")
    cwd_expanded = os.path.expanduser(cwd)
    cmd = ["claude", "--system-prompt", system_prompt, "--model", model]

    # Merge extra env vars into current environment
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)

    self._proc = ptyprocess.PtyProcess.spawn(
        cmd, cwd=cwd_expanded, dimensions=(24, 80), env=env
    )
    self._read_task = asyncio.create_task(self._read_loop())
```

- [ ] **Step 2: Update pty_session test to cover extra_env**

Add to `tests/runtime/test_pty_session.py`:
```python
async def test_start_passes_extra_env(mock_proc):
    with patch("ptyprocess.PtyProcess.spawn", return_value=mock_proc) as mock_spawn:
        pty = PtySession("sess-env")
        await pty.start("sys", "model", "/tmp", extra_env={"HARNESS_TOKEN": "tok123"})
        call_kwargs = mock_spawn.call_args[1]
        assert call_kwargs.get("env", {}).get("HARNESS_TOKEN") == "tok123"
        pty.kill()
```

Run: `uv run pytest tests/runtime/test_pty_session.py -v` — all PASS.

- [ ] **Step 3: Update JobRunner to accept gateway dependencies**

Edit `harness_claw/runtime/job_runner.py` — update `__init__` and `start_session`:
```python
from __future__ import annotations

import base64
import inspect
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Awaitable

from harness_claw.runtime.cost_poller import CostPoller, _encode_cwd
from harness_claw.runtime.pty_session import PtySession
from harness_claw.role_registry import RoleRegistry
from harness_claw.session import Session
from harness_claw.runtime.session_store import SessionStore

_logger = logging.getLogger(__name__)

Send = Callable[[dict[str, Any]], Awaitable[None]]


async def _call_send(send: Send, msg: dict[str, Any]) -> None:
    result = send(msg)
    if inspect.isawaitable(result):
        await result


class JobRunner:
    def __init__(
        self,
        registry: RoleRegistry,
        store: SessionStore,
        token_store: Any | None = None,
        connector: Any | None = None,
        dispatcher: Any | None = None,
        mcp_base_url: str = "http://localhost:8000",
    ) -> None:
        self._registry = registry
        self._store = store
        self._token_store = token_store
        self._connector = connector
        self._dispatcher = dispatcher
        self._mcp_base_url = mcp_base_url
        self._pty_sessions: dict[str, PtySession] = {}
        self._cost_pollers: dict[str, CostPoller] = {}
        self._session_tokens: dict[str, str] = {}  # session_id → token
        self._senders: set[Send] = set()

    def add_sender(self, send: Send) -> None:
        self._senders.add(send)

    def remove_sender(self, send: Send) -> None:
        self._senders.discard(send)

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        for send in list(self._senders):
            await _call_send(send, msg)

    def _write_mcp_config(self, cwd: str, token: str) -> None:
        """Write .claude/settings.json so claude picks up our MCP server."""
        cwd_expanded = os.path.expanduser(cwd)
        claude_dir = Path(cwd_expanded) / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        settings_path = claude_dir / "settings.json"

        # Preserve existing settings if any
        existing: dict[str, Any] = {}
        if settings_path.exists():
            try:
                existing = json.loads(settings_path.read_text())
            except Exception:
                pass

        mcp_servers = existing.get("mcpServers", {})
        mcp_servers["harnessclaw"] = {
            "type": "sse",
            "url": f"{self._mcp_base_url}/mcp/sse?token={token}",
        }
        existing["mcpServers"] = mcp_servers
        settings_path.write_text(json.dumps(existing, indent=2))

    async def start_session(self, session: Session) -> None:
        session_id = session.session_id

        if session_id in self._pty_sessions:
            _logger.warning("start_session called for already-running session %s; ignoring", session_id)
            return

        role = self._registry.get(session.role_id)
        if role is None:
            _logger.error("start_session: role %r not found for session %s", session.role_id, session_id)
            return

        _logger.info("Starting PTY session %s (role=%s)", session_id, session.role_id)

        # Issue token and write MCP config
        extra_env: dict[str, str] = {}
        if self._token_store is not None:
            token = self._token_store.issue(session_id, role.scopes)
            self._session_tokens[session_id] = token
            extra_env["HARNESS_TOKEN"] = token
            self._write_mcp_config(session.working_dir, token)

        pty = PtySession(session_id)

        async def on_output(data: bytes) -> None:
            await self._broadcast({
                "type": "output",
                "session_id": session_id,
                "data": base64.b64encode(data).decode(),
            })

        pty.add_output_callback(on_output)
        await pty.start(role.system_prompt, role.model, session.working_dir,
                        extra_env=extra_env if extra_env else None)
        self._pty_sessions[session_id] = pty

        # Register in capability registry
        if self._connector is not None:
            from harness_claw.gateway.capability import AgentAdvertisement
            await self._connector.register(AgentAdvertisement(
                session_id=session_id,
                role_id=session.role_id,
                caps=role.caps,
                status="idle",
                task_count=0,
                connector="local",
            ))

        # Register write callback with dispatcher
        if self._dispatcher is not None:
            self._dispatcher.register_writer(session_id, pty.write)

        session.status = "running"
        self._store.save(session)
        await self._broadcast({
            "type": "session_update", "session_id": session_id,
            "status": "running", "name": session.name,
        })

        async def on_cost_update(sid: str, cost: float, input_tokens: int, output_tokens: int) -> None:
            s = self._store.get(sid)
            if s:
                s.input_tokens = input_tokens
                s.output_tokens = output_tokens
                self._store.save(s)
            await self._broadcast({
                "type": "cost_update",
                "session_id": sid,
                "cost_usd": cost,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            })

        poller = CostPoller(session_id, session.working_dir, on_cost_update)
        poller.start()
        self._cost_pollers[session_id] = poller

    def write(self, session_id: str, data: bytes) -> None:
        pty = self._pty_sessions.get(session_id)
        if pty:
            pty.write(data)

    def resize(self, session_id: str, cols: int, rows: int) -> None:
        pty = self._pty_sessions.get(session_id)
        if pty:
            pty.resize(cols=cols, rows=rows)

    def kill_session(self, session_id: str) -> None:
        _logger.info("Killing session %s", session_id)
        pty = self._pty_sessions.pop(session_id, None)
        if pty:
            pty.kill()
        poller = self._cost_pollers.pop(session_id, None)
        if poller:
            poller.stop()
        # Revoke token
        if self._token_store is not None:
            token = self._session_tokens.pop(session_id, None)
            if token:
                self._token_store.revoke(token)
        # Deregister from capability registry
        if self._connector is not None:
            import asyncio
            asyncio.create_task(self._connector.deregister(session_id))
        # Unregister writer from dispatcher
        if self._dispatcher is not None:
            self._dispatcher.unregister_writer(session_id)
        session = self._store.get(session_id)
        if session:
            session.status = "killed"
            self._store.save(session)

    def delete_session(self, session_id: str) -> None:
        _logger.info("Deleting session %s", session_id)
        self.kill_session(session_id)
        session = self._store.get(session_id)
        if session:
            self._delete_claude_session(session)
        self._store.delete(session_id)

    def _delete_claude_session(self, session: Session) -> None:
        cwd = os.path.expanduser(session.working_dir)
        encoded = _encode_cwd(cwd)
        claude_dir = Path.home() / ".claude" / "projects" / encoded
        if claude_dir.exists():
            for f in claude_dir.glob("*.jsonl"):
                f.unlink(missing_ok=True)
```

- [ ] **Step 4: Update job_runner tests for new constructor signature**

Edit `tests/runtime/test_job_runner.py` — update `make_runner` to use keyword args (the new params have defaults so existing tests still pass):
```python
def make_runner(sessions=None):
    registry = MagicMock(spec=RoleRegistry)
    role = MagicMock()
    role.system_prompt = "You are helpful."
    role.model = "claude-sonnet-4-6"
    role.scopes = ["agent:list"]
    role.caps = []
    role.system_prompt = "You are helpful."
    registry.get.return_value = role

    store = MagicMock(spec=SessionStore)
    store.get.return_value = sessions[0] if sessions else make_session()
    store.all.return_value = sessions or []

    return JobRunner(registry, store), registry, store
```

Also update the patch path in `test_start_session_spawns_pty`:
```python
    with patch("harness_claw.runtime.job_runner.PtySession") as MockPty:
        mock_pty = MagicMock()
        mock_pty.start = AsyncMock()
        MockPty.return_value = mock_pty

        with patch("harness_claw.runtime.job_runner.CostPoller"):
            await runner.start_session(session)

        MockPty.assert_called_once_with("s1")
        mock_pty.start.assert_called_once_with("You are helpful.", "claude-sonnet-4-6", "/tmp", extra_env=None)
```

Run: `uv run pytest tests/runtime/ -v` — all PASS.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/runtime/pty_session.py harness_claw/runtime/job_runner.py tests/runtime/
git commit -m "feat: inject HARNESS_TOKEN and MCP config into PTY sessions at start"
```

---

## Task 11: Rewrite server.py as gateway-first + create api/ routes

**Files:**
- Create: `harness_claw/api/sessions.py`
- Create: `harness_claw/api/roles.py`
- Create: `harness_claw/api/websocket.py`
- Rewrite: `harness_claw/server.py`

- [ ] **Step 1: Create api/sessions.py**

Create `harness_claw/api/sessions.py`:
```python
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from harness_claw.role_registry import RoleRegistry
from harness_claw.runtime.session_store import SessionStore
from harness_claw.runtime.job_runner import JobRunner
from harness_claw.session import Session

router = APIRouter(prefix="/api")


class CreateSessionRequest(BaseModel):
    role_id: str
    working_dir: str


def make_router(registry: RoleRegistry, store: SessionStore, runner: JobRunner) -> APIRouter:
    @router.get("/sessions")
    def list_sessions() -> dict[str, list[dict[str, Any]]]:
        grouped = store.grouped_by_dir()
        return {
            wd: [s.to_dict() for s in sessions]
            for wd, sessions in grouped.items()
        }

    @router.post("/sessions", status_code=201)
    async def create_session(req: CreateSessionRequest) -> dict[str, Any]:
        role = registry.get(req.role_id)
        if role is None:
            raise HTTPException(status_code=404, detail=f"Role {req.role_id!r} not found")
        session = Session(
            role_id=req.role_id,
            working_dir=req.working_dir,
            model=role.model,
        )
        store.save(session)
        await runner.start_session(session)
        return session.to_dict()

    @router.delete("/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: str) -> None:
        if store.get(session_id) is None:
            raise HTTPException(status_code=404, detail="Session not found")
        runner.delete_session(session_id)
        await runner._broadcast({"type": "session_deleted", "session_id": session_id})

    return router
```

- [ ] **Step 2: Create api/roles.py**

Create `harness_claw/api/roles.py`:
```python
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from harness_claw.role_registry import RoleRegistry

router = APIRouter(prefix="/api")


def make_router(registry: RoleRegistry) -> APIRouter:
    @router.get("/roles")
    def list_roles() -> list[dict[str, Any]]:
        return [
            {
                "id": r.id, "name": r.name, "provider": r.provider,
                "model": r.model, "system_prompt": r.system_prompt,
                "max_tokens": r.max_tokens,
            }
            for r in registry.all()
        ]

    return router
```

- [ ] **Step 3: Create api/websocket.py**

Create `harness_claw/api/websocket.py`:
```python
from __future__ import annotations

import asyncio
import base64
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from harness_claw.runtime.job_runner import JobRunner
from harness_claw.runtime.session_store import SessionStore

router = APIRouter()


def make_router(runner: JobRunner, store: SessionStore) -> APIRouter:
    @router.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def send(msg: dict[str, Any]) -> None:
            await queue.put(msg)

        runner.add_sender(send)

        async def sender() -> None:
            while True:
                msg = await queue.get()
                await ws.send_json(msg)

        sender_task = asyncio.create_task(sender())
        try:
            while True:
                data = await ws.receive_json()
                msg_type = data.get("type")

                if msg_type == "input":
                    raw = base64.b64decode(data["data"])
                    runner.write(data["session_id"], raw)
                elif msg_type == "resize":
                    runner.resize(data["session_id"], cols=data["cols"], rows=data["rows"])
                elif msg_type == "cancel":
                    session_id = data["session_id"]
                    runner.kill_session(session_id)
                    session = store.get(session_id)
                    if session:
                        await runner._broadcast({
                            "type": "session_update",
                            "session_id": session_id,
                            "status": "killed",
                            "name": session.name,
                        })
        except WebSocketDisconnect:
            pass
        finally:
            runner.remove_sender(send)
            sender_task.cancel()

    return router
```

- [ ] **Step 4: Rewrite server.py**

Create `harness_claw/server.py`:
```python
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.fastmcp import FastMCP

from harness_claw.gateway.audit import AuditLogger
from harness_claw.gateway.auth import TokenStore
from harness_claw.gateway.broker import Broker, LocalDispatcher
from harness_claw.gateway.capability import LocalConnector
from harness_claw.gateway.memory import SqliteMemoryStore
from harness_claw.gateway.mcp_server import GatewayMCP
from harness_claw.gateway.policy import LocalPolicyEngine
from harness_claw.role_registry import RoleRegistry
from harness_claw.runtime.job_runner import JobRunner
from harness_claw.runtime.session_store import SessionStore
from harness_claw.api import sessions as sessions_api
from harness_claw.api import roles as roles_api
from harness_claw.api import websocket as ws_api

_root = Path(__file__).parent.parent
_agents_yaml = _root / "agents.yaml"
_sessions_json = _root / "sessions.json"
_audit_jsonl = _root / "audit.jsonl"
_memory_db = _root / "memory.db"

# --- Shared state ---
registry = RoleRegistry(_agents_yaml)
cfg = registry.gateway_config

store = SessionStore(_sessions_json)
token_store = TokenStore()
policy = LocalPolicyEngine()
connector = LocalConnector()
dispatcher = LocalDispatcher()
broker = Broker(connectors=[connector], dispatcher=dispatcher)
memory = SqliteMemoryStore(_memory_db)
audit = AuditLogger(_audit_jsonl)

gateway_mcp = GatewayMCP(
    token_store=token_store,
    policy=policy,
    connectors=[connector],
    broker=broker,
    memory=memory,
    audit=audit,
)

runner = JobRunner(
    registry=registry,
    store=store,
    token_store=token_store,
    connector=connector,
    dispatcher=dispatcher,
    mcp_base_url="http://localhost:8000",
)

# --- FastMCP server ---
mcp = FastMCP("harnessclaw")


def _token_from_request(request: Request) -> str:
    token = request.query_params.get("token", "")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    return token


@mcp.tool(name="agent_list", description="List agents in the registry, filtered by caps")
async def mcp_agent_list(caps: list[str] = []) -> list[dict[str, Any]]:
    # token injected via HARNESS_TOKEN env — passed as query param in SSE URL
    # This is resolved in the HTTP layer; placeholder here for FastMCP registration
    return []


# --- FastAPI app ---
app = FastAPI(title="HarnessClaw Gateway")


@app.on_event("startup")
async def startup() -> None:
    for session in store.all():
        if session.status != "killed":
            await runner.start_session(session)


# Mount MCP SSE endpoint
# FastMCP exposes /sse and /messages/ — we mount it under /mcp
mcp_app = mcp.get_asgi_app()
app.mount("/mcp", mcp_app)


# Custom MCP HTTP handler that routes tool calls through GatewayMCP with token auth
@app.post("/mcp/tools/call")
async def mcp_tool_call(request: Request) -> JSONResponse:
    body = await request.json()
    token = _token_from_request(request)
    tool = body.get("name")
    args = body.get("arguments", {})

    handlers = {
        "agent.list": lambda a: gateway_mcp.agent_list(token=token, **a),
        "agent.delegate": lambda a: gateway_mcp.agent_delegate(token=token, **a),
        "agent.status": lambda a: gateway_mcp.agent_status(token=token, **a),
        "agent.progress": lambda a: gateway_mcp.agent_progress(token=token, **a),
        "agent.complete": lambda a: gateway_mcp.agent_complete(token=token, **a),
        "memory.namespaces": lambda a: gateway_mcp.memory_namespaces(token=token),
        "memory.list": lambda a: gateway_mcp.memory_list(token=token, **a),
        "memory.get": lambda a: gateway_mcp.memory_get(token=token, **a),
        "memory.search": lambda a: gateway_mcp.memory_search(token=token, **a),
        "memory.set": lambda a: gateway_mcp.memory_set(token=token, **a),
        "memory.delete": lambda a: gateway_mcp.memory_delete(token=token, **a),
        "memory.tag": lambda a: gateway_mcp.memory_tag(token=token, **a),
    }

    handler = handlers.get(tool)
    if handler is None:
        return JSONResponse({"error": f"unknown tool {tool!r}"}, status_code=404)
    try:
        result = await handler(args)
        return JSONResponse({"result": result})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# REST API routes
app.include_router(sessions_api.make_router(registry, store, runner))
app.include_router(roles_api.make_router(registry))
app.include_router(ws_api.make_router(runner, store))

# Audit log endpoint
@app.get("/api/audit")
def get_audit(limit: int = 100) -> list[dict[str, Any]]:
    if not _audit_jsonl.exists():
        return []
    lines = _audit_jsonl.read_text().strip().splitlines()
    import json as _json
    events = [_json.loads(l) for l in lines[-limit:]]
    return list(reversed(events))


# SPA
_dist = _root / "ui" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        return FileResponse(str(_dist / "index.html"))
```

- [ ] **Step 5: Verify the server starts**

```bash
uv run uvicorn harness_claw.server:app --port 8000 --reload
```

Expected: server starts without errors. Ctrl-C to stop.

- [ ] **Step 6: Run all backend tests**

```bash
uv run pytest tests/ -v
```

Expected: all existing tests PASS, new gateway tests PASS.

- [ ] **Step 7: Commit**

```bash
git add harness_claw/server.py harness_claw/api/
git commit -m "feat: rewrite server.py as gateway-first FastAPI app with api/ routes"
```

---

## Task 12: WebSocket task events

**Files:**
- Modify: `harness_claw/gateway/broker.py`
- Modify: `harness_claw/api/websocket.py`
- Modify: `harness_claw/server.py`

The broker already has a listener system. Wire it to broadcast task events over WebSocket.

- [ ] **Step 1: Wire broker events into the WS broadcaster**

Edit `harness_claw/server.py` — add after broker is created:
```python
# Wire broker task events into WebSocket broadcast
async def on_task_event(event: str, task_dict: dict[str, Any]) -> None:
    await runner._broadcast({"type": event, "task": task_dict})

broker.add_listener(on_task_event)
```

Also update the broker's `complete_task` and `update_progress` to fire async listeners. Currently `Broker._notify` is async but called from sync `update_progress`/`complete_task`. Fix `broker.py`:

```python
# In Broker.update_progress — return task and schedule notification
def update_progress(self, task_id: str, pct: int, msg: str) -> Task:
    task = self._store.get(task_id)
    if task is None:
        raise KeyError(f"task {task_id!r} not found")
    task.progress_pct = pct
    task.progress_msg = msg
    self._store.save(task)
    import asyncio
    asyncio.create_task(self._notify("task.updated", task))
    return task

def complete_task(self, task_id: str, result: str) -> Task:
    task = self._store.get(task_id)
    if task is None:
        raise KeyError(f"task {task_id!r} not found")
    task.status = "completed"
    task.progress_pct = 100
    task.result = result
    self._store.save(task)
    import asyncio
    asyncio.create_task(self._notify("task.completed", task))
    return task
```

- [ ] **Step 2: Verify task events flow end-to-end**

```bash
uv run uvicorn harness_claw.server:app --port 8000
```

In another terminal, connect a WebSocket and call `agent.delegate` via the MCP endpoint. The WS client should receive `task.created`, then `task.updated`/`task.completed` events.

- [ ] **Step 3: Commit**

```bash
git add harness_claw/server.py harness_claw/gateway/broker.py
git commit -m "feat: broadcast task.created/updated/completed events over WebSocket"
```

---

## Task 13: GatewayConnector (external agent self-registration)

**Files:**
- Modify: `harness_claw/gateway/capability.py`
- Modify: `harness_claw/server.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/gateway/test_capability.py`:
```python
import asyncio
from harness_claw.gateway.capability import GatewayConnector


async def test_gateway_connector_register_and_query():
    conn = GatewayConnector(bootstrap_token="secret", heartbeat_ttl=2)
    session_id = await conn.register_external(
        bootstrap_token="secret",
        caps=["python"],
        role_id="external-coder",
    )
    assert session_id is not None
    results = await conn.query(["python"])
    assert any(a.session_id == session_id for a in results)


async def test_gateway_connector_wrong_token_rejected():
    conn = GatewayConnector(bootstrap_token="secret", heartbeat_ttl=2)
    with pytest.raises(ValueError, match="bootstrap"):
        await conn.register_external(bootstrap_token="wrong", caps=[], role_id="r")


async def test_gateway_connector_heartbeat_deregisters_on_timeout():
    conn = GatewayConnector(bootstrap_token="secret", heartbeat_ttl=1)
    session_id = await conn.register_external("secret", ["python"], "coder")
    # No heartbeat — wait for TTL
    await asyncio.sleep(1.5)
    conn._expire_stale()
    results = await conn.query(["python"])
    assert not any(a.session_id == session_id for a in results)
```

Run: `uv run pytest tests/gateway/test_capability.py::test_gateway_connector_register_and_query -v` — FAIL.

- [ ] **Step 2: Implement GatewayConnector**

Add to `harness_claw/gateway/capability.py`:
```python
import time
import uuid


class GatewayConnector:
    """External agents self-register via bootstrap token and heartbeat to stay alive."""

    def __init__(self, bootstrap_token: str, heartbeat_ttl: int = 30) -> None:
        self._bootstrap_token = bootstrap_token
        self._heartbeat_ttl = heartbeat_ttl
        self._agents: dict[str, AgentAdvertisement] = {}
        self._last_seen: dict[str, float] = {}

    async def register_external(self, bootstrap_token: str, caps: list[str], role_id: str) -> str:
        if bootstrap_token != self._bootstrap_token:
            raise ValueError("invalid bootstrap_token")
        session_id = str(uuid.uuid4())
        agent = AgentAdvertisement(
            session_id=session_id,
            role_id=role_id,
            caps=caps,
            status="idle",
            task_count=0,
            connector="gateway",
        )
        self._agents[session_id] = agent
        self._last_seen[session_id] = time.monotonic()
        return session_id

    async def heartbeat(self, session_id: str) -> None:
        if session_id in self._agents:
            self._last_seen[session_id] = time.monotonic()

    def _expire_stale(self) -> None:
        now = time.monotonic()
        stale = [sid for sid, ts in self._last_seen.items()
                 if now - ts > self._heartbeat_ttl]
        for sid in stale:
            self._agents.pop(sid, None)
            self._last_seen.pop(sid, None)

    async def register(self, agent: AgentAdvertisement) -> None:
        self._agents[agent.session_id] = agent
        self._last_seen[agent.session_id] = time.monotonic()

    async def deregister(self, session_id: str) -> None:
        self._agents.pop(session_id, None)
        self._last_seen.pop(session_id, None)

    async def query(self, caps: list[str]) -> list[AgentAdvertisement]:
        self._expire_stale()
        cap_set = set(caps)
        matches = [a for a in self._agents.values() if cap_set.issubset(set(a.caps))]
        return sorted(matches, key=lambda a: a.task_count)
```

- [ ] **Step 3: Add REST endpoints for external registration**

Add to `harness_claw/server.py`:
```python
from harness_claw.gateway.capability import GatewayConnector

# After connector = LocalConnector():
gateway_connector = GatewayConnector(
    bootstrap_token=cfg.gateway_bootstrap_token,
    heartbeat_ttl=cfg.gateway_heartbeat_ttl,
)
broker = Broker(connectors=[connector, gateway_connector], dispatcher=dispatcher)

# Add these routes:
class ExternalRegisterRequest(BaseModel):
    bootstrap_token: str
    caps: list[str]
    role_id: str

@app.post("/gateway/agents/register", status_code=201)
async def register_external_agent(req: ExternalRegisterRequest) -> dict[str, Any]:
    try:
        session_id = await gateway_connector.register_external(
            bootstrap_token=req.bootstrap_token,
            caps=req.caps,
            role_id=req.role_id,
        )
        return {"session_id": session_id}
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))

@app.post("/gateway/agents/{session_id}/heartbeat", status_code=204)
async def agent_heartbeat(session_id: str) -> None:
    await gateway_connector.heartbeat(session_id)

@app.delete("/gateway/agents/{session_id}", status_code=204)
async def deregister_external_agent(session_id: str) -> None:
    await gateway_connector.deregister(session_id)
```

- [ ] **Step 4: Run all gateway tests**

```bash
uv run pytest tests/gateway/ -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/gateway/capability.py harness_claw/server.py tests/gateway/
git commit -m "feat: add GatewayConnector with heartbeat-based external agent registration"
```

---

## Task 14: Frontend — TasksTab live task board with expandable xterm

**Files:**
- Modify: `ui/src/types.ts`
- Modify: `ui/src/App.tsx`
- Rewrite: `ui/src/components/TasksTab.tsx`

- [ ] **Step 1: Add task types to types.ts**

Edit `ui/src/types.ts` — add task types and update WSIncoming:
```typescript
// Task record (from WS task events)
export interface TaskRecord {
  task_id: string
  delegated_by: string
  delegated_to: string
  instructions: string
  caps_requested: string[]
  status: 'queued' | 'running' | 'completed' | 'failed'
  progress_pct: number
  progress_msg: string
  result: string | null
  created_at: string
  updated_at: string
}

// Add to WSIncoming union:
export type WSIncoming =
  | { type: 'output'; session_id: string; data: string }
  | { type: 'cost_update'; session_id: string; cost_usd: number; input_tokens: number; output_tokens: number }
  | { type: 'session_update'; session_id: string; name: string; status: 'idle' | 'running' | 'killed' }
  | { type: 'session_deleted'; session_id: string }
  | { type: 'task.created'; task: TaskRecord }
  | { type: 'task.updated'; task: TaskRecord }
  | { type: 'task.completed'; task: TaskRecord }
```

- [ ] **Step 2: Wire task events in App.tsx**

Edit `ui/src/App.tsx` — add task state and wire WS messages:
```typescript
// Add to state:
const [tasks, setTasks] = useState<Record<string, TaskRecord>>({})

// Add to handleWsMessage:
} else if (msg.type === 'task.created' || msg.type === 'task.updated' || msg.type === 'task.completed') {
  setTasks(prev => ({ ...prev, [msg.task.task_id]: msg.task }))
}

// Update TasksTab usage:
{tab === 'tasks' && (
  <TasksTab
    tasks={Object.values(tasks)}
    sessions={sessions}
    terminalWriters={terminalWriters}
    onInput={(sessionId, data) => wsRef.current?.send({ type: 'input', session_id: sessionId, data })}
    onResize={(sessionId, cols, rows) => wsRef.current?.send({ type: 'resize', session_id: sessionId, cols, rows })}
  />
)}
```

Also remove the old `{tab === 'tasks' && <TasksTab jobs={[]} />}` line.

- [ ] **Step 3: Rewrite TasksTab.tsx**

Create `ui/src/components/TasksTab.tsx`:
```typescript
import { useState, useEffect, useRef, useCallback } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import type { TaskRecord, SessionState } from '../types'

interface Props {
  tasks: TaskRecord[]
  sessions: Record<string, SessionState>
  terminalWriters: React.MutableRefObject<Record<string, (data: Uint8Array) => void>>
  onInput: (sessionId: string, data: string) => void
  onResize: (sessionId: string, cols: number, rows: number) => void
}

function ProgressBar({ pct }: { pct: number }) {
  return (
    <div className="h-1.5 bg-gray-700 rounded-full overflow-hidden">
      <div
        className="h-full bg-blue-500 rounded-full transition-all duration-300"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

function statusBadge(status: TaskRecord['status']) {
  if (status === 'running') return '● Running'
  if (status === 'completed') return '✓ Done'
  if (status === 'failed') return '✕ Failed'
  return '◌ Queued'
}

function statusColor(status: TaskRecord['status']) {
  if (status === 'running') return 'text-blue-400'
  if (status === 'completed') return 'text-green-400'
  if (status === 'failed') return 'text-red-400'
  return 'text-gray-500'
}

function TaskTerminalPanel({ sessionId, terminalWriters }: {
  sessionId: string
  terminalWriters: React.MutableRefObject<Record<string, (data: Uint8Array) => void>>
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<Terminal | null>(null)
  const fitRef = useRef<FitAddon | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const term = new Terminal({
      theme: { background: '#111827' },
      fontSize: 12,
      convertEol: true,
      disableStdin: true,
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(containerRef.current)
    fit.fit()
    termRef.current = term
    fitRef.current = fit

    // Register a secondary writer for this session so task panel also receives output
    const existingWriter = terminalWriters.current[`task:${sessionId}`]
    terminalWriters.current[`task:${sessionId}`] = (data: Uint8Array) => {
      term.write(data)
      existingWriter?.(data)
    }

    return () => {
      delete terminalWriters.current[`task:${sessionId}`]
      term.dispose()
    }
  }, [sessionId])

  return (
    <div
      ref={containerRef}
      className="h-48 rounded bg-gray-900 overflow-hidden"
    />
  )
}

function TaskRow({ task, sessions, terminalWriters, expanded, onToggle }: {
  task: TaskRecord
  sessions: Record<string, SessionState>
  terminalWriters: React.MutableRefObject<Record<string, (data: Uint8Array) => void>>
  expanded: boolean
  onToggle: () => void
}) {
  const agentSession = sessions[task.delegated_to]
  const agentName = agentSession?.name || task.delegated_to.slice(0, 8)

  return (
    <div className="border border-gray-700 rounded-lg overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-3 p-3 bg-gray-800 hover:bg-gray-750 text-left"
      >
        <span className="text-gray-500 text-xs w-4">{expanded ? '▼' : '▶'}</span>
        <span className="text-xs text-gray-400 font-mono w-20 truncate">{task.task_id.slice(0, 8)}</span>
        <span className="text-sm text-gray-200 flex-1 truncate">{agentName}</span>
        <div className="w-24">
          {task.status === 'running' && <ProgressBar pct={task.progress_pct} />}
        </div>
        <span className={`text-xs w-20 text-right ${statusColor(task.status)}`}>
          {statusBadge(task.status)}
        </span>
      </button>

      {expanded && (
        <div className="p-3 bg-gray-900 border-t border-gray-700 flex flex-col gap-2">
          <div className="flex gap-4 text-xs text-gray-500">
            <span>from: {task.delegated_by.slice(0, 8)}</span>
            <span>caps: {task.caps_requested.join(', ')}</span>
            {task.progress_msg && <span>{task.progress_msg}</span>}
          </div>
          <TaskTerminalPanel sessionId={task.delegated_to} terminalWriters={terminalWriters} />
          {task.result && (
            <div className="text-xs text-green-400 bg-gray-800 rounded p-2 whitespace-pre-wrap">
              {task.result}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function TasksTab({ tasks, sessions, terminalWriters, onInput, onResize }: Props) {
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())

  const toggle = useCallback((id: string) => {
    setExpandedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  if (tasks.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-600 text-sm">
        No tasks yet
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto p-3 flex flex-col gap-2">
      {[...tasks].reverse().map(task => (
        <TaskRow
          key={task.task_id}
          task={task}
          sessions={sessions}
          terminalWriters={terminalWriters}
          expanded={expandedIds.has(task.task_id)}
          onToggle={() => toggle(task.task_id)}
        />
      ))}
    </div>
  )
}
```

- [ ] **Step 4: Build frontend to verify no TypeScript errors**

```bash
cd ui && npm run build
```

Expected: build succeeds with no errors.

- [ ] **Step 5: Commit**

```bash
git add ui/src/types.ts ui/src/App.tsx ui/src/components/TasksTab.tsx
git commit -m "feat: live TasksTab with expandable inline xterm output per task"
```

---

## Task 15: Frontend — Memory Tab

**Files:**
- Create: `ui/src/components/MemoryTab.tsx`
- Modify: `ui/src/components/TabPanel.tsx`
- Modify: `ui/src/App.tsx`

- [ ] **Step 1: Add Memory tab to TabPanel**

Edit `ui/src/components/TabPanel.tsx`:
```typescript
export type TabId = 'work' | 'tasks' | 'agent' | 'tools' | 'memory'

const TABS: Tab[] = [
  { id: 'work', label: 'Work' },
  { id: 'tasks', label: 'Tasks' },
  { id: 'agent', label: 'Agent' },
  { id: 'tools', label: 'Tools' },
  { id: 'memory', label: 'Memory' },
]
```

- [ ] **Step 2: Create MemoryTab.tsx**

Create `ui/src/components/MemoryTab.tsx`:
```typescript
import { useState, useEffect, useCallback } from 'react'

interface MemoryEntry {
  key: string
  summary: string | null
  tags: string[]
  size_bytes: number
  updated_at: string
}

interface MemoryEntryDetail extends MemoryEntry {
  value: string
}

export function MemoryTab() {
  const [namespaces, setNamespaces] = useState<string[]>([])
  const [activeNs, setActiveNs] = useState<string | null>(null)
  const [entries, setEntries] = useState<MemoryEntry[]>([])
  const [selected, setSelected] = useState<MemoryEntryDetail | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    fetch('/api/memory/namespaces')
      .then(r => r.json())
      .then(setNamespaces)
      .catch(console.error)
  }, [])

  const loadNamespace = useCallback(async (ns: string) => {
    setActiveNs(ns)
    setSelected(null)
    const data = await fetch(`/api/memory/${encodeURIComponent(ns)}`).then(r => r.json())
    setEntries(data)
  }, [])

  const loadEntry = useCallback(async (ns: string, key: string) => {
    setLoading(true)
    const data = await fetch(`/api/memory/${encodeURIComponent(ns)}/${encodeURIComponent(key)}`).then(r => r.json())
    setSelected(data)
    setLoading(false)
  }, [])

  const deleteEntry = useCallback(async (ns: string, key: string) => {
    await fetch(`/api/memory/${encodeURIComponent(ns)}/${encodeURIComponent(key)}`, { method: 'DELETE' })
    setSelected(null)
    if (activeNs) await loadNamespace(activeNs)
  }, [activeNs, loadNamespace])

  return (
    <div className="flex flex-1 min-h-0 overflow-hidden">
      {/* Namespace sidebar */}
      <div className="w-48 border-r border-gray-800 flex flex-col overflow-y-auto">
        <div className="p-2 text-xs text-gray-500 uppercase tracking-wide">Namespaces</div>
        {namespaces.map(ns => (
          <button
            key={ns}
            onClick={() => loadNamespace(ns)}
            className={`px-3 py-1.5 text-left text-sm truncate ${
              activeNs === ns ? 'bg-gray-800 text-white' : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            {ns}
          </button>
        ))}
        {namespaces.length === 0 && (
          <div className="px-3 py-2 text-xs text-gray-600">No namespaces yet</div>
        )}
      </div>

      {/* Entry list */}
      <div className="w-64 border-r border-gray-800 flex flex-col overflow-y-auto">
        {activeNs && (
          <>
            <div className="p-2 text-xs text-gray-500 truncate">{activeNs}</div>
            {entries.map(entry => (
              <button
                key={entry.key}
                onClick={() => loadEntry(activeNs, entry.key)}
                className={`px-3 py-2 text-left border-b border-gray-800 ${
                  selected?.key === entry.key ? 'bg-gray-800' : 'hover:bg-gray-850'
                }`}
              >
                <div className="text-sm text-gray-200 truncate">{entry.key}</div>
                {entry.summary && (
                  <div className="text-xs text-gray-500 truncate">{entry.summary}</div>
                )}
                <div className="text-xs text-gray-600">{entry.size_bytes}B</div>
              </button>
            ))}
            {entries.length === 0 && (
              <div className="px-3 py-2 text-xs text-gray-600">Empty namespace</div>
            )}
          </>
        )}
      </div>

      {/* Entry detail */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {selected ? (
          <>
            <div className="flex items-center justify-between p-3 border-b border-gray-800">
              <div>
                <div className="text-sm text-white font-mono">{selected.key}</div>
                {selected.summary && (
                  <div className="text-xs text-gray-400">{selected.summary}</div>
                )}
                {selected.tags.length > 0 && (
                  <div className="flex gap-1 mt-1">
                    {selected.tags.map(t => (
                      <span key={t} className="text-xs bg-gray-700 text-gray-300 px-1.5 py-0.5 rounded">
                        {t}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              <button
                onClick={() => activeNs && deleteEntry(activeNs, selected.key)}
                className="text-xs text-red-400 hover:text-red-300 px-2 py-1 border border-red-800 rounded"
              >
                Delete
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-3">
              <pre className="text-sm text-gray-300 whitespace-pre-wrap font-mono">{selected.value}</pre>
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-gray-600 text-sm">
            {activeNs ? 'Select an entry' : 'Select a namespace'}
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Add memory REST routes to server.py**

Add to `harness_claw/server.py`:
```python
# Memory REST endpoints (for dashboard)
@app.get("/api/memory/namespaces")
async def get_memory_namespaces() -> list[str]:
    return await memory.namespaces()

@app.get("/api/memory/{namespace:path}")
async def list_memory_entries(namespace: str) -> list[dict[str, Any]]:
    entries = await memory.list(namespace)
    return [
        {"key": e.key, "summary": e.summary, "tags": e.tags,
         "size_bytes": e.size_bytes, "updated_at": e.updated_at}
        for e in entries
    ]

@app.get("/api/memory/{namespace:path}/{key}")
async def get_memory_entry(namespace: str, key: str) -> dict[str, Any]:
    entry = await memory.get(namespace, key)
    if entry is None:
        raise HTTPException(status_code=404, detail="not found")
    return {
        "key": entry.key, "value": entry.value, "summary": entry.summary,
        "tags": entry.tags, "size_bytes": entry.size_bytes, "updated_at": entry.updated_at,
    }

@app.delete("/api/memory/{namespace:path}/{key}", status_code=204)
async def delete_memory_entry(namespace: str, key: str) -> None:
    await memory.delete(namespace, key)
```

- [ ] **Step 4: Wire MemoryTab in App.tsx**

Edit `ui/src/App.tsx`:
```typescript
import { MemoryTab } from './components/MemoryTab'

// In the tab content:
{tab === 'memory' && <MemoryTab />}
```

- [ ] **Step 5: Build and verify**

```bash
cd ui && npm run build
```

Expected: build succeeds.

- [ ] **Step 6: Commit**

```bash
git add ui/src/components/MemoryTab.tsx ui/src/components/TabPanel.tsx ui/src/App.tsx harness_claw/server.py
git commit -m "feat: add Memory tab with namespace browser, entry inspector, and delete"
```

---

## Task 16: Final integration test + run all tests

- [ ] **Step 1: Run the full test suite**

```bash
uv run pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 2: Start the server and smoke-test**

```bash
uv run uvicorn harness_claw.server:app --port 8000 --reload
```

Open `http://localhost:8000` — dashboard loads. Create a session — agent starts with `HARNESS_TOKEN` in env and `.claude/settings.json` written to the working directory.

- [ ] **Step 3: Build frontend**

```bash
cd ui && npm run build
```

Expected: clean build.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: Phase 1 agent OS gateway complete — auth, policy, caps, MCP, broker, memory"
```
