"""
Snapshot of every prompt's version: tests/fixtures/prompt_versions.json.

    python -m evals.prompt_versions          show what changed (exit 1 if anything did)
    python -m evals.prompt_versions --write  accept the current versions (make prompt-versions)

The snapshot makes each prompt edit visible in review as a one-line diff, and
tests/test_prompt_versions.py fails until the snapshot is updated.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys

from evals.paths import FIXTURES
from kairo.core.prompt import prompt_versions

SNAPSHOT = FIXTURES / "prompt_versions.json"

# Every module that declares a prompt. tests/test_prompt_versions.py checks this list is complete.
PROMPT_MODULES = (
    "kairo.agents.graph.prompts",
    "kairo.agents.orchestrator.prompts",
    "kairo.agents.personal_manager.agent",
    "kairo.agents.personal_manager.extractors.model",
    "kairo.agents.personal_manager.application.semantic_memory",
    "kairo.domains.habits",
    "kairo.domains.journal",
    "kairo.domains.memory",
    "kairo.domains.schedule",
    "kairo.domains.tasks",
    "kairo.tools.memory.reconciliation",
)


def current() -> dict[str, str]:
    for module in PROMPT_MODULES:
        importlib.import_module(module)
    return prompt_versions()


def saved() -> dict[str, str]:
    return json.loads(SNAPSHOT.read_text()) if SNAPSHOT.exists() else {}


def changes(old: dict[str, str], new: dict[str, str]) -> list[str]:
    lines = [f"  changed  {name}: {old[name]} -> {new[name]}" for name in sorted(old.keys() & new.keys()) if old[name] != new[name]]
    lines += [f"  added    {name}: {new[name]}" for name in sorted(new.keys() - old.keys())]
    lines += [f"  removed  {name}: {old[name]}" for name in sorted(old.keys() - new.keys())]
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="accept the current prompt versions")
    args = parser.parse_args()
    new, old = current(), saved()
    diff = changes(old, new)
    if args.write:
        SNAPSHOT.write_text(json.dumps(new, indent=2) + "\n")
        print("\n".join(diff) if diff else "prompt versions unchanged")
        return 0
    if diff:
        print("Prompts changed since the snapshot:\n" + "\n".join(diff) + "\nRun `make prompt-versions` to accept.")
        return 1
    print(f"{len(new)} prompts, all match the snapshot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
