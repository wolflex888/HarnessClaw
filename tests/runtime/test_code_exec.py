from __future__ import annotations

import ast
from pathlib import Path

import pytest

from harness_claw.runtime import rpc_stub


def test_stub_module_parses_as_python():
    src = Path(rpc_stub.__file__).read_text()
    ast.parse(src)


def test_stub_module_exposes_underscore_call():
    src = Path(rpc_stub.__file__).read_text()
    assert "def _call(" in src


def test_stub_module_uses_only_stdlib():
    src = Path(rpc_stub.__file__).read_text()
    tree = ast.parse(src)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(n.name for n in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    stdlib = {"json", "os", "socket"}
    assert set(imports) <= stdlib, f"non-stdlib import: {set(imports) - stdlib}"
