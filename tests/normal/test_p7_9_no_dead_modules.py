"""
Normal Tests for Phase 7.9: no abandoned modules.

Every module under ``src/onyxsi`` must be reachable by following imports from a
real entrypoint (``onyxsi.server``, the package itself, or ``python -m onyxsi``).
A module that nothing imports is either dead weight or a wiring bug.
"""

import ast
import os
from pathlib import Path

import onyxsi

PACKAGE_ROOT = Path(os.path.abspath(onyxsi.__file__)).parent
SRC_ROOT = PACKAGE_ROOT.parent
ROOTS = {"onyxsi", "onyxsi.server", "onyxsi.__main__"}


def _module_name(path: Path) -> str:
    rel = path.relative_to(SRC_ROOT)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _absolute_imports(tree: ast.AST) -> set:
    found: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "onyxsi" or alias.name.startswith("onyxsi."):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if base == "onyxsi" or base.startswith("onyxsi."):
                found.add(base)
                for alias in node.names:
                    if alias.name != "*":
                        found.add(f"{base}.{alias.name}")
    return found


def _resolve(target: str, modules: dict) -> str:
    while target:
        if target in modules:
            return target
        if "." not in target:
            break
        target = target.rsplit(".", 1)[0]
    return ""


def test_every_module_is_reachable_from_an_entrypoint():
    modules = {
        _module_name(path): path for path in PACKAGE_ROOT.rglob("*.py")
    }
    assert "onyxsi.server" in modules

    imports = {}
    for name, path in modules.items():
        with open(path, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=str(path))
        imports[name] = {
            resolved
            for resolved in (
                _resolve(target, modules) for target in _absolute_imports(tree)
            )
            if resolved
        }

    reachable: set = set()
    queue = list(ROOTS)
    while queue:
        name = queue.pop()
        if name in reachable or name not in modules:
            continue
        reachable.add(name)
        queue.extend(imports.get(name, ()))

    # Importing ``onyxsi.gateway.meta_router`` executes its package __init__.
    for name in list(reachable):
        parts = name.split(".")
        for depth in range(1, len(parts)):
            parent = ".".join(parts[:depth])
            if parent in modules:
                reachable.add(parent)

    dead = sorted(set(modules) - reachable)
    assert dead == [], f"unreachable modules: {dead}"


def test_package_exposes_a_version():
    assert isinstance(onyxsi.__version__, str)
    assert onyxsi.__version__.count(".") >= 1
