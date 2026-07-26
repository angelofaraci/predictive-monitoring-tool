"""No-execution guard (spec: fase-5-mcp.md, Threat Matrix / Testing Strategy).

The whole `mcp_server` package proposes actions; it never executes them.
This test statically scans every `.py` module under the package for common
ways to reach `subprocess`, `os.system`/`os.popen`, or `shutil.rmtree` —
direct imports, aliased imports (`import os as o`), and `from`-imports
followed by a bare call (`from shutil import rmtree; rmtree(...)`) — and
fails the build if one is ever introduced.

This is a best-effort static guard, not an absolute guarantee: it does not
(and cannot, without a full dataflow/taint analysis) catch `getattr(os,
"system")(...)`, `__import__("subprocess")`, `importlib.import_module(...)`,
`eval`/`exec`, or execution reached indirectly through a third-party
dependency. It exists as defense-in-depth alongside the actual design
guarantee, which is that no code in this package calls an execution
primitive in the first place — verified by direct code review, not by this
test alone.
"""

from __future__ import annotations

import ast
from pathlib import Path

import predictive_monitoring_tool.mcp_server as mcp_server_package

BANNED_IMPORT_MODULES = {"subprocess"}
BANNED_MODULE_ATTRS = {
    "os": {"system", "popen"},
    "shutil": {"rmtree"},
}


def _iter_package_source_files() -> list[Path]:
    package_dir = Path(mcp_server_package.__file__).parent
    return sorted(package_dir.rglob("*.py"))


def _find_violations(source_path: Path) -> list[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    violations: list[str] = []

    # alias (local name) -> real module name, e.g. {"o": "os"} for `import os as o`
    module_aliases: dict[str, str] = {}
    # local name -> real module, e.g. {"rmtree": "shutil"} for `from shutil import rmtree`
    imported_attr_names: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in BANNED_IMPORT_MODULES:
                    violations.append(f"{source_path}: import {alias.name}")
                if alias.name in BANNED_MODULE_ATTRS:
                    module_aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module in BANNED_IMPORT_MODULES:
                violations.append(f"{source_path}: from {node.module} import ...")
            elif node.module in BANNED_MODULE_ATTRS:
                for alias in node.names:
                    if alias.name in BANNED_MODULE_ATTRS[node.module]:
                        local_name = alias.asname or alias.name
                        violations.append(
                            f"{source_path}: from {node.module} import {alias.name}"
                        )
                        imported_attr_names[local_name] = node.module
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name):
                real_module = module_aliases.get(node.value.id, node.value.id)
                if node.attr in BANNED_MODULE_ATTRS.get(real_module, set()):
                    violations.append(f"{source_path}: {node.value.id}.{node.attr}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in imported_attr_names:
                violations.append(f"{source_path}: bare call to {node.func.id}(...)")

    return violations


class TestNoExecutionGuard:
    """No module under `mcp_server/` may import or call an execution primitive."""

    def test_package_has_source_files_to_scan(self):
        source_files = _iter_package_source_files()

        assert len(source_files) > 0

    def test_no_module_imports_or_calls_execution_primitives(self):
        all_violations: list[str] = []
        for source_path in _iter_package_source_files():
            all_violations.extend(_find_violations(source_path))

        assert all_violations == []

    def test_detects_from_import_bare_call(self, tmp_path):
        source_path = tmp_path / "bad.py"
        source_path.write_text("from shutil import rmtree\nrmtree('/tmp/x')\n")

        violations = _find_violations(source_path)

        assert violations

    def test_detects_aliased_module_attribute_call(self, tmp_path):
        source_path = tmp_path / "bad.py"
        source_path.write_text("import os as o\no.system('rm -rf /')\n")

        violations = _find_violations(source_path)

        assert violations
