from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] != "run":
        print("Usage: harnessclaw run")
        sys.exit(1)

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
