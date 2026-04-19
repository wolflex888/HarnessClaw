# Redaction Design

**Date:** 2026-04-19
**Status:** Approved — ready for implementation planning
**Scope:** At-rest redaction of secrets and PII across HarnessClaw's audit log and memory store.

## Goal

Scrub sensitive values from `audit.jsonl` and the memory SQLite database before they are written, so these artifacts can be reviewed, shared, backed up, or shipped to log aggregators without leaking secrets.

## Non-goals

- In-flight redaction of MCP tool arguments (breaks tool correctness; defer).
- Redaction of the PTY byte stream into `claude` stdin (would confuse the agent).
- Access control (the policy engine owns that).
- Encryption at rest (a separate concern; SQLite-level).
- Reversible redaction / vault (destructive per Q3 — agents re-fetch secrets from authoritative sources when needed).

## Decisions (from brainstorming 2026-04-19)

| # | Question | Decision |
|---|---|---|
| 1 | Surfaces | Audit log + memory store (at-rest) |
| 2 | Detection | Pluggable `Redactor` interface; regex-based default |
| 3 | Behavior | Destructive |
| 4 | Configuration | `redaction.yaml` with category toggles + custom rules |
| 5 | Existing data | Standalone `harness-claw redact-existing` CLI, dry-run default |
| 6 | Code wiring | Decorator wrappers around existing `AuditLogger` / `SqliteMemoryStore` |

## Architecture

A single module `harness_claw/gateway/redaction.py` exposes:

- `Redactor` — a `Protocol` with one method: `redact(value) -> value`.
- `RegexRedactor` — the default implementation. Ships with a curated pattern library and accepts custom rules from config.
- `RedactionConfig` — dataclass loaded from `redaction.yaml` at startup.
- `RedactingAuditLogger` — decorator wrapping `AuditLogger`.
- `RedactingMemoryStore` — decorator wrapping `MemoryStore`.

`server.py` constructs the redactor at startup and conditionally wraps the audit logger and memory store in their redacting variants. If `enabled: false` in config, the wrappers are never constructed and behavior is identical to today.

### Data flow on write

```
agent → MCP tool → GatewayMCP.memory_set(...)
                 → RedactingMemoryStore.set(value)
                 → RegexRedactor.redact(value)
                 → SqliteMemoryStore.set(redacted_value)
```

Audit path is identical with `RedactingAuditLogger.log(event)` in the middle.

Reads pass through unchanged — data is already redacted at rest.

## Components

### `Redactor` protocol

```python
from typing import Protocol, Any

class Redactor(Protocol):
    def redact(self, value: Any) -> Any: ...
```

Takes any JSON-serializable value (`str | dict | list | int | float | bool | None`) and returns the same shape with matches replaced. Recurses into dicts and lists; non-string leaves pass through unchanged. Strings are scanned by each enabled rule; matches are replaced with `[REDACTED:<category>]`.

### `RegexRedactor` — default implementation

Built-in categories and defaults:

| Category | Example match | Default |
|---|---|---|
| `anthropic_key` | `sk-ant-…` | on |
| `openai_key` | `sk-…` (distinguished from Anthropic by prefix rule) | on |
| `github_token` | `ghp_…`, `gho_…`, `ghu_…`, `ghs_…`, `ghr_…` | on |
| `aws_access_key` | `AKIA[0-9A-Z]{16}` | on |
| `aws_secret_key` | heuristic: `secret`-adjacent 40-char base64 | on |
| `gcp_key` | service account JSON markers | on |
| `slack_webhook` | `hooks.slack.com/services/…` | on |
| `jwt` | `eyJ…\.eyJ…\..+` | on |
| `ssh_private_key` | `-----BEGIN [A-Z ]+PRIVATE KEY-----` + body | on |
| `email` | standard RFC-ish pattern | off |
| `phone_us` | 10-digit with optional formatting | off |
| `credit_card` | Luhn-validated 13–19 digit | off |
| `ssn_us` | `\d{3}-\d{2}-\d{4}` | off |
| `user_path` | `/Users/<name>/…`, `/home/<name>/…` | off |

**Default rationale:** secrets are near-zero false-positive and catastrophic if leaked — always on. PII patterns have false positives (a phone number in a doc is not sensitive) and are off unless the operator flips them.

**Rule ordering:** more-specific rules run before less-specific ones. `anthropic_key` (prefix `sk-ant-`) is evaluated before `openai_key` (prefix `sk-`) so an Anthropic key is never mis-labeled. `RegexRedactor` applies rules in a fixed order defined in code; config only toggles whether each runs, not the order.

### `RedactingAuditLogger`

```python
class RedactingAuditLogger:
    def __init__(self, inner: AuditLogger, redactor: Redactor) -> None:
        self._inner = inner
        self._redactor = redactor

    def log(self, event: AuditEvent) -> None:
        scrubbed = AuditEvent(
            subject=event.subject,
            operation=event.operation,
            resource=event.resource,
            outcome=event.outcome,
            details=self._redactor.redact(event.details),
        )
        self._inner.log(scrubbed)
```

Only `details` is redacted. Structural fields (`subject`, `operation`, `resource`, `outcome`) stay intact so audit trails remain queryable.

### `RedactingMemoryStore`

```python
class RedactingMemoryStore:
    def __init__(self, inner: MemoryStore, redactor: Redactor) -> None:
        self._inner = inner
        self._redactor = redactor

    async def set(self, namespace: str, key: str, value: str,
                  summary: str | None, tags: list[str]) -> None:
        scrubbed_value = self._redactor.redact(value)
        scrubbed_summary = self._redactor.redact(summary) if summary else summary
        await self._inner.set(namespace, key, scrubbed_value,
                              summary=scrubbed_summary, tags=tags)

    async def get(self, namespace, key): return await self._inner.get(namespace, key)
    async def list(self, namespace): return await self._inner.list(namespace)
    async def search(self, namespace, query): return await self._inner.search(namespace, query)
    async def delete(self, namespace, key): return await self._inner.delete(namespace, key)
    async def namespaces(self): return await self._inner.namespaces()
```

Only `value` and `summary` are redacted. `namespace`, `key`, and `tags` are preserved — they are structural identifiers. Agents writing secrets into a key or tag is a separate bug, not something redaction should mask.

## Configuration

### `redaction.yaml` — project-root file, loaded at server startup

```yaml
enabled: true

categories:
  anthropic_key: on
  openai_key: on
  github_token: on
  aws_access_key: on
  aws_secret_key: on
  gcp_key: on
  slack_webhook: on
  jwt: on
  ssh_private_key: on
  email: off
  phone_us: off
  credit_card: off
  ssn_us: off
  user_path: off

custom_rules:
  - name: internal_api_token
    pattern: 'ACME-[A-Z0-9]{32}'
    replacement: '[REDACTED:internal_api_token]'
```

### `RedactionConfig` dataclass

```python
@dataclass
class RedactionConfig:
    enabled: bool = True
    categories: dict[str, bool] = field(default_factory=_default_categories)
    custom_rules: list[CustomRule] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None = None) -> "RedactionConfig":
        path = path or Path("redaction.yaml")
        if not path.exists():
            return cls()
        data = yaml.safe_load(path.read_text()) or {}
        return cls(
            enabled=data.get("enabled", True),
            categories={**_default_categories(), **data.get("categories", {})},
            custom_rules=[CustomRule(**r) for r in data.get("custom_rules", [])],
        )
```

- Missing file → all defaults (secure by default, zero-config).
- Partial file merges with defaults.
- Unknown category key → warning + skip.
- Malformed regex in `custom_rules` → fail fast at startup with `re.error` and the rule name.

### Wiring diff in `server.py`

```python
_redaction_config = RedactionConfig.load()
_redactor = RegexRedactor(_redaction_config)

if _redaction_config.enabled:
    audit = RedactingAuditLogger(AuditLogger(audit_path), _redactor)
    memory = RedactingMemoryStore(SqliteMemoryStore(memory_path), _redactor)
else:
    audit = AuditLogger(audit_path)
    memory = SqliteMemoryStore(memory_path)
```

`enabled: false` is an explicit escape hatch — useful for local debug and test fixtures.

## Backfill CLI

### `harness-claw redact-existing`

**Location:** `harness_claw/cli/redact_existing.py`, wired as a subcommand in the existing CLI entrypoint.

**Invocation:**

```
harness-claw redact-existing [--config redaction.yaml]
                             [--audit audit.jsonl]
                             [--memory memory.db]
                             [--apply]
                             [--json]
                             [--force]
```

**Default mode — dry run.** Loads the same `RegexRedactor` the server uses, scans both targets, reports what would change. No files modified.

**With `--apply`:** commits changes atomically:

- `audit.jsonl`: read line-by-line → redact each record's `details` → write `audit.jsonl.tmp` → `os.replace()`. Original preserved as `audit.jsonl.bak` until rename succeeds.
- `memory.db`: open SQLite, `BEGIN TRANSACTION`, scan entries, `UPDATE` any rows whose `value` or `summary` change, commit. FTS5 / vector indexes refresh via the same path `SqliteMemoryStore.set()` uses for normal writes — the implementation plan verifies whether triggers or explicit re-indexing is required.

**Human-readable output:**

```
audit.jsonl: scanned 12,483 records
  - would redact 47 records across 3 categories
    anthropic_key:   12
    github_token:    30
    jwt:              5

memory.db: scanned 312 entries across 14 namespaces
  - would redact 8 entries
    (namespace/key)        categories
    dev/stripe-key         openai_key (1)
    prod/env-dump          aws_secret_key (3), anthropic_key (1)
    ...

Dry run. Re-run with --apply to commit.
```

`--json` emits the same data as JSON for scripting.

**Idempotent:** a second `--apply` finds zero changes. Replacement labels don't match any rule by construction.

**Safety rails:**
- Refuses to run if the server holds a write lock on `memory.db` (probes via `BEGIN IMMEDIATE; ROLLBACK;`). Exit code 2.
- Warns and requires `--force` if config has `enabled: false` (likely a mistake — why backfill if redaction is off?).

## Error handling

| Failure | Behavior |
|---|---|
| `redaction.yaml` malformed YAML | Startup fails fast with parser error. |
| `redaction.yaml` has unknown category | Warning logged; key skipped; startup continues. |
| `redaction.yaml` has bad `custom_rules` regex | Startup fails fast with `re.error` and rule name. |
| Redactor raises mid-write | Wrapper catches, logs diagnostic, **writes original unredacted** + emits `ERROR` audit entry. The error entry bypasses redaction (written directly to the inner `AuditLogger`) to avoid infinite recursion if the redactor is the thing that's broken. Rationale: data loss > missed redaction; error is visible. |
| CLI can't acquire `memory.db` lock | Exit 2 with message telling user to stop the server first. |
| CLI partial write interrupted | `.tmp` + `os.replace()` + `.bak` preserves original; re-run resumes. |
| Redactor applied twice | Idempotent — replacement labels don't match any rule. |

No silent failures. No swallowed exceptions.

## Testing

Five test files, all in existing test directories.

### `tests/gateway/test_redaction_redactor.py` — `RegexRedactor` unit tests

- Each built-in category has a positive and negative test.
- Nested dicts/lists recurse correctly.
- Non-string leaves (ints, bools, None) pass through untouched.
- Custom rule from a fixture `RedactionConfig` fires.
- Disabled categories don't fire.
- Idempotence: `redact(redact(x)) == redact(x)`.

### `tests/gateway/test_redaction_config.py` — loader tests

- Missing file → defaults.
- Partial file merges with defaults.
- Unknown category key logs warning, keeps loading.
- Malformed regex raises at load time.
- `enabled: false` produces a structurally valid config.

### `tests/gateway/test_redacting_audit_logger.py` — decorator tests

- `log()` scrubs `details`; preserves `subject`/`operation`/`resource`/`outcome`.
- Delegates to inner logger exactly once.

### `tests/gateway/test_redacting_memory_store.py` — decorator tests

- `set()` scrubs `value` and `summary`; preserves `namespace`/`key`/`tags`.
- `get()`/`list()`/`search()`/`delete()`/`namespaces()` straight-delegate (spy on inner).
- End-to-end against a real `SqliteMemoryStore`: write a secret, read it back, verify it's already redacted.

### `tests/cli/test_redact_existing.py` — CLI tests

- Dry run on fixture `audit.jsonl` + `memory.db` reports correct counts, mutates nothing.
- `--apply` writes `.bak`, replaces atomically, output matches expectations.
- Second `--apply` is a no-op.
- Lock-held probe surfaces as exit code 2.
- `--json` output is valid JSON matching the text summary.

Existing `tests/gateway/*.py` coverage already exercises `GatewayMCP` end-to-end through the audit/memory interfaces; the wrappers are transparent at that layer, so the wiring diff is covered without new integration tests.

## File plan

**New:**
- `harness_claw/gateway/redaction.py` — `Redactor`, `RegexRedactor`, `RedactionConfig`, `CustomRule`, `RedactingAuditLogger`, `RedactingMemoryStore`.
- `harness_claw/cli/redact_existing.py` — backfill CLI.
- `redaction.yaml` (committed example) — empty-enough to be a real default but documenting the schema.
- `tests/gateway/test_redaction_redactor.py`
- `tests/gateway/test_redaction_config.py`
- `tests/gateway/test_redacting_audit_logger.py`
- `tests/gateway/test_redacting_memory_store.py`
- `tests/cli/test_redact_existing.py`

**Modified:**
- `harness_claw/server.py` — wire the decorators at startup (3-line diff).
- `harness_claw/__main__.py` (or wherever CLI subcommands live) — register `redact-existing`.

## Out of scope — follow-on work

- In-flight redaction of MCP tool args.
- Reversible/vault-style redaction.
- Per-namespace redaction policy (YAGNI until someone asks).
- PTY byte-stream scrubbing.
- Observability for redaction events (counters, alerts) — can be added via existing audit stream if needed.
