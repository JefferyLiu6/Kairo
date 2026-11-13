"""The package layout's one rule: imports point down.

    api  ->  agents  ->  domains  ->  tools  ->  storage / core  ->  utils

A module may import from its own layer or any layer below it, never above.
Nothing in `kairo` imports the eval tools in `evals/`.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

KAIRO = Path(__file__).resolve().parents[1] / "kairo"
LAYER_RANK = {"api": 5, "agents": 4, "domains": 3, "tools": 2, "storage": 1, "core": 1, "utils": 0}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    return names


MODULES = sorted(p for p in KAIRO.rglob("*.py") if len(p.relative_to(KAIRO).parts) > 1)


def test_every_module_belongs_to_a_known_layer():
    layers = {p.relative_to(KAIRO).parts[0] for p in MODULES}
    assert layers <= set(LAYER_RANK), layers - set(LAYER_RANK)


@pytest.mark.parametrize("path", MODULES, ids=lambda p: str(p.relative_to(KAIRO)))
def test_imports_point_down(path: Path):
    layer = path.relative_to(KAIRO).parts[0]
    upward = []
    for module in _imports(path):
        parts = module.split(".")
        if parts[0] == "evals":
            upward.append(f"{module} (eval tooling is not part of the app)")
        elif parts[0] == "kairo" and len(parts) > 1 and parts[1] in LAYER_RANK:
            if LAYER_RANK[parts[1]] > LAYER_RANK[layer]:
                upward.append(f"{module} ({layer} may not import {parts[1]})")
    assert not upward, f"{path.relative_to(KAIRO)} imports upward: {upward}"
