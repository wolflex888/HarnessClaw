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
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "run":
        _run()
    elif cmd == "attach":
        _attach(sys.argv[2:])
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
