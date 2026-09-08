"""Capture real deterministic PM responses and independent before/after task state."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from assistant.personal_manager.agent import PMConfig
from assistant.personal_manager.workflow import run_typed_pm_turn
from assistant.personal_manager.persistence.store import load_todos


def capture() -> list[dict]:
    """Synthetic local data only. Never access a real user store or external calendar."""
    requests = [
        ("create_task", "Add task to prepare interview examples"),
        ("read_tasks", "List my todos"),
        ("complete_task", "Mark the 'prepare interview examples' todo as done"),
    ]
    cases = []
    with TemporaryDirectory(prefix="kairo-eval-") as directory:
        config = PMConfig(provider="offline", model="unused", data_dir=directory,
                          vault_dir=directory, session_id="pm-capture-eval")

        def snapshot():
            # Omit unstable generated IDs; read state independently of reply wording.
            return [{"title": item.title, "done": item.done, "due": item.due}
                    for item in load_todos(config.session_id, directory).items]

        for case_id, request in requests:
            before = snapshot()
            reply = run_typed_pm_turn(request, config)
            if reply is None:
                raise RuntimeError("Capture scenario did not use the deterministic workflow")
            after = snapshot()
            cases.append({
                "id": case_id, "category": "captured_task_workflow",
                "request": request, "response": reply,
                "evidence": json.dumps({"source": "independent local task store snapshot",
                                        "before": before, "after": after}, sort_keys=True),
                "expected": None,
                "provenance": {"source": "deterministic_pm_capture", "review_status": "unreviewed",
                               "scope": "PM workflow output, not final orchestrator humanization"},
            })
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = capture()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(cases, indent=2) + "\n")
    print(f"Captured {len(cases)} real workflow responses; labels await review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
