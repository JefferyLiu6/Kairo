"""Capture final orchestrator replies and independently check synthetic task state."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import hashlib
import logging
import subprocess
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import uuid

from .agent import OrchestratorConfig, run_orchestrator
from .memory import _sessions
from .telemetry import logger as telemetry_logger
from .monitor_report import summarize_logs
from assistant.personal_manager.persistence.store import TodoData, _todos_path

TITLE = "prepare interview examples"
SCENARIOS = (
    ("create", f"Add task to {TITLE}"),
    ("list", "List my todos"),
    ("complete", f"Mark the '{TITLE}' todo as done"),
)


def snapshot(config: OrchestratorConfig) -> list[dict]:
    path = Path(_todos_path(config.user_id, config.data_dir))
    # Unlike the app's forgiving loader, corrupt state is a capture failure, not empty state.
    state = TodoData.model_validate_json(path.read_text()) if path.exists() else TodoData()
    return [item.model_dump() for item in state.items]


def state_check(step: str, before: list[dict], after: list[dict]) -> bool:
    if len(after) != 1 or after[0]["title"] != TITLE:
        return False
    if step == "create":
        return before == [] and after[0]["done"] is False
    if step == "list":
        return before == after and after[0]["done"] is False
    if step == "complete":
        return (len(before) == 1 and before[0]["done"] is False and after[0]["done"] is True
                and {**before[0], "done": True} == after[0])
    raise ValueError("Unknown state check")


def capture_final(config: OrchestratorConfig, execute=run_orchestrator) -> dict:
    cases, observations = [], []
    identifier = "eval-" + uuid.uuid4().hex
    with TemporaryDirectory(prefix="kairo-final-eval-") as directory:
        isolated = replace(config, user_id=identifier, session_id=identifier,
                           data_dir=directory, vault_dir=directory)
        try:
            for step, request in SCENARIOS:
                before = snapshot(isolated)
                started = time.perf_counter()
                status, response = "ok", None
                try:
                    response = execute(request, isolated)
                    if not isinstance(response, str) or not response.strip():
                        status = "empty_response"
                except Exception:
                    status = "execution_error"  # No private exception text in artifacts.
                after = snapshot(isolated)
                passed = state_check(step, before, after)
                observation = {
                    "id": step, "execution_status": status, "state_check_passed": passed,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "before": before, "after": after,
                }
                observations.append(observation)
                if status == "ok":
                    cases.append({
                        "id": step, "category": "final_task_response", "request": request,
                        "response": response,
                        "evidence": json.dumps({"source": "independent persisted task snapshots",
                                                "before": before, "after": after}, sort_keys=True),
                        "expected": None,
                        "provenance": {"source": "final_orchestrator_capture", "review_status": "unreviewed",
                                       "state_check_passed": passed},
                    })
        finally:
            _sessions.pop((identifier, identifier), None)
    return {
        "cases": cases, "observations": observations,
        "summary": {"attempted_turns": len(SCENARIOS), "captured_replies": len(cases),
                    "state_checks_passed": sum(row["state_check_passed"] for row in observations),
                    "execution_errors": sum(row["execution_status"] != "ok" for row in observations)},
        "scope": "Sequential synthetic task scenario. State success and final response quality are separate; no response labels are inferred.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Required: invokes billable models")
    parser.add_argument("--provider", choices=["openai", "anthropic", "ollama"], default="openai")
    parser.add_argument("--model", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error("Final orchestrator capture requires --live; no simulated scores are produced")
    if args.output_dir.exists():
        parser.error("Choose a new output directory to preserve previous runs")
    config = OrchestratorConfig(
        session_id="unused", provider=args.provider, model=args.model,
        pm_provider=args.provider, pm_model=args.model,
        api_key=os.getenv("EVAL_API_KEY"), pm_api_key=os.getenv("EVAL_API_KEY"),
        base_url=os.getenv("EVAL_BASE_URL"), pm_base_url=os.getenv("EVAL_BASE_URL"),
    )
    args.output_dir.mkdir(parents=True, exist_ok=False)
    telemetry_path = args.output_dir / "telemetry.jsonl"
    with telemetry_path.open("x") as stream:
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        previous_level = telemetry_logger.level
        telemetry_logger.addHandler(handler)
        telemetry_logger.setLevel(logging.INFO)
        try:
            result = capture_final(config)
        finally:
            telemetry_logger.removeHandler(handler)
            telemetry_logger.setLevel(previous_level)
            handler.close()
    with telemetry_path.open() as source:
        monitor = summarize_logs(source)
    (args.output_dir / "monitor.json").write_text(json.dumps(monitor, indent=2) + "\n")
    result["metadata"] = {"mode": "live_final_capture", "provider": args.provider, "model": args.model,
                          "harness_provider": os.getenv("JUDGE_PROVIDER", args.provider),
                          "harness_model": os.getenv("JUDGE_MODEL", args.model),
                          "created_at": datetime.now(timezone.utc).isoformat(),
                          "scenario_sha256": hashlib.sha256(json.dumps(SCENARIOS).encode()).hexdigest(),
                          "git_revision": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
                          "working_tree_dirty": bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip())}
    (args.output_dir / "cases.json").write_text(json.dumps(result["cases"], indent=2) + "\n")
    (args.output_dir / "run.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["summary"], indent=2))
    return int(result["summary"]["execution_errors"] > 0 or result["summary"]["state_checks_passed"] != len(SCENARIOS))


if __name__ == "__main__":
    raise SystemExit(main())
