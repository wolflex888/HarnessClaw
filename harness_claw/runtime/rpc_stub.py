"""RPC stub module.

This file is copied into a sandbox cwd as ``harnessclaw_tools.py`` by
``harness_claw.runtime.code_exec.run_rpc_script``. The sandboxed Python
process imports it and uses ``_call(tool_name, args)`` to invoke HarnessClaw
tools over a unix domain socket.

Keep imports to stdlib only. Connect lazily — the env var may not be set at
import time during test collection.
"""
import json
import os
import socket


class _RPCClient:
    def __init__(self) -> None:
        self._sock: socket.socket | None = None
        self._buf = b""

    def _connect(self) -> None:
        path = os.environ["HARNESSCLAW_RPC_SOCKET"]
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(path)

    def _readline(self) -> bytes:
        assert self._sock is not None
        while b"\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("RPC server closed connection")
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line

    def call(self, name, args=None):
        if self._sock is None:
            self._connect()
        assert self._sock is not None
        req = json.dumps({"tool": name, "args": {} if args is None else args}) + "\n"
        self._sock.sendall(req.encode())
        line = self._readline()
        resp = json.loads(line)
        if "error" in resp:
            raise RuntimeError(resp["error"])
        return resp["result"]


_client = _RPCClient()


def _call(name, args=None):
    """Invoke a HarnessClaw tool via RPC. Returns the tool's result."""
    return _client.call(name, args)
