from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: harnessclaw <command>")
        print("Commands:")
        print("  run                              Start HarnessClaw server + UI")
        print("  attach --role <role> [--dir <d>] Hook this terminal into HarnessClaw")
        print("  redact-existing [--apply]        Backfill redaction over audit.jsonl + memory.db")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "run":
        _run()
    elif cmd == "attach":
        _attach(sys.argv[2:])
    elif cmd == "redact-existing":
        _redact_existing(sys.argv[2:])
    else:
        print(f"Unknown command: {cmd!r}")
        sys.exit(1)


def _run() -> None:
    root = Path(__file__).parent.parent
    ui_dir = root / "ui"

    backend = subprocess.Popen(
        ["uvicorn", "harness_claw.server:app", "--reload", "--port", "8000"],
        cwd=root,
    )
    frontend = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=ui_dir,
    )

    def _shutdown(sig: int, frame: object) -> None:
        backend.terminate()
        frontend.terminate()
        backend.wait()
        frontend.wait()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    backend.wait()
    frontend.wait()


def _attach(args: list[str]) -> None:
    import urllib.request
    import urllib.error

    role_id = "orchestrator"
    working_dir = os.getcwd()
    host = "http://localhost:8000"

    i = 0
    while i < len(args):
        if args[i] == "--role" and i + 1 < len(args):
            role_id = args[i + 1]
            i += 2
        elif args[i] == "--dir" and i + 1 < len(args):
            working_dir = args[i + 1]
            i += 2
        elif args[i] == "--host" and i + 1 < len(args):
            host = args[i + 1]
            i += 2
        else:
            i += 1

    payload = json.dumps({"role_id": role_id, "working_dir": working_dir}).encode()
    req = urllib.request.Request(
        f"{host}/api/sessions/attach",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError as e:
        print(f"Error: could not reach HarnessClaw at {host} — is it running?")
        print(f"  {e}")
        sys.exit(1)

    token = data["token"]
    session_id = data["session_id"]

    print(f"Attached session {session_id} (role={role_id})")
    print(f"Working dir: {working_dir}")
    print(f"MCP config written to {working_dir}/.claude/settings.json")
    print()

    env = dict(os.environ)
    env["HARNESS_TOKEN"] = token
    os.chdir(working_dir)
    os.execvpe("claude", ["claude"], env)


def _redact_existing(args: list[str]) -> None:
    import asyncio
    from harness_claw.gateway.redaction import RedactionConfig, RegexRedactor
    from harness_claw.redact_backfill import run_backfill

    config_path = Path("redaction.yaml")
    audit_path = Path("audit.jsonl")
    memory_path = Path("memory.db")
    apply = False
    emit_json = False
    force = False

    i = 0
    while i < len(args):
        if args[i] == "--config" and i + 1 < len(args):
            config_path = Path(args[i + 1]); i += 2
        elif args[i] == "--audit" and i + 1 < len(args):
            audit_path = Path(args[i + 1]); i += 2
        elif args[i] == "--memory" and i + 1 < len(args):
            memory_path = Path(args[i + 1]); i += 2
        elif args[i] == "--apply":
            apply = True; i += 1
        elif args[i] == "--json":
            emit_json = True; i += 1
        elif args[i] == "--force":
            force = True; i += 1
        else:
            print(f"Unknown flag: {args[i]!r}")
            sys.exit(1)

    cfg = RedactionConfig.load(config_path)
    if not cfg.enabled and not force:
        print("redaction.yaml has enabled: false — nothing to redact. Use --force to run anyway.")
        sys.exit(1)
    redactor = RegexRedactor(cfg)

    try:
        report = asyncio.run(run_backfill(
            redactor=redactor,
            audit_path=audit_path,
            memory_path=memory_path,
            apply=apply,
        ))
    except RuntimeError as e:
        print(f"error: {e}")
        sys.exit(2)

    if emit_json:
        from dataclasses import asdict
        print(json.dumps(asdict(report), indent=2))
        return

    print(f"{audit_path}: scanned {report.audit_records_scanned} records")
    print(f"  - would redact {report.audit_records_changed} records" if not apply
          else f"  - redacted {report.audit_records_changed} records")
    for cat, count in sorted(report.audit_by_category.items()):
        print(f"    {cat}: {count}")

    print()
    print(f"{memory_path}: scanned {report.memory_entries_scanned} entries")
    print(f"  - would redact {report.memory_entries_changed} entries" if not apply
          else f"  - redacted {report.memory_entries_changed} entries")
    for cat, count in sorted(report.memory_by_category.items()):
        print(f"    {cat}: {count}")

    if not apply:
        print()
        print("Dry run. Re-run with --apply to commit.")
