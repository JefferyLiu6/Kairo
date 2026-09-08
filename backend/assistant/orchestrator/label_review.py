"""Export a blind label-review packet and validate completed annotations."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .judge_eval import validate_cases

FIELDS = ("id", "category", "request", "response", "evidence")
REVIEW_FIELDS = ("request", "response", "evidence")


def digest(cases: list[dict]) -> str:
    # Labels are intentionally excluded; exact examples, including ordering, are bound.
    content = [{field: case[field] for field in FIELDS} for case in cases]
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def export_packet(cases: list[dict]) -> dict:
    validate_cases(cases, replay=False)
    return {
        "schema_version": 1, "examples_sha256": digest(cases),
        "instructions": "Review each response against the request and evidence. Label pass, fail, or abstain (insufficient evidence). Add your rationale and reviewer name/alias. Prior labels and judge outputs are intentionally omitted. This is response quality, not merely task completion.",
        "rubric": {
            "pass": "Fully grounded, relevant, and complete for the supplied evidence; truthful failure or clarification may pass.",
            "fail": "Unsupported claims, wrong answer, or missing required information.",
            "abstain": "Evidence is insufficient to establish response quality.",
        },
        "cases": [{"id": f"case-{index:04d}", **{field: case[field] for field in REVIEW_FIELDS},
                   "label": None, "rationale": "", "reviewer": ""} for index, case in enumerate(cases)],
    }


def apply_packet(cases: list[dict], packet: dict) -> list[dict]:
    validate_cases(cases, replay=False)
    if not isinstance(packet, dict) or packet.get("schema_version") != 1:
        raise ValueError("Unsupported review packet")
    if packet.get("examples_sha256") != digest(cases):
        raise ValueError("Review packet does not match source examples")
    entries = packet.get("cases")
    if not isinstance(entries, list) or len(entries) != len(cases):
        raise ValueError("Every source example must be reviewed")
    by_id = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise ValueError("Invalid review entry")
        if entry["id"] in by_id:
            raise ValueError("Duplicate review ID")
        by_id[entry["id"]] = entry
    result = copy.deepcopy(cases)
    for index, case in enumerate(result):
        entry = by_id.get(f"case-{index:04d}")
        if entry is None or any(entry.get(field) != case[field] for field in REVIEW_FIELDS):
            raise ValueError("Reviewed example content changed or is missing")
        if entry.get("label") not in ("pass", "fail", "abstain"):
            raise ValueError("Every example requires a completed label")
        for field in ("reviewer", "rationale"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise ValueError(f"Every example requires {field}")
        case["expected"] = entry["label"]
        # Replay output is a contract fixture, not the human's judgment. Avoid stale leakage.
        case.pop("replay_verdict", None)
        provenance = case.setdefault("provenance", {})
        if not isinstance(provenance, dict):
            raise ValueError("Case provenance must be an object")
        provenance["review_status"] = "review_record_supplied"
        provenance["review"] = {
            "reviewer": entry["reviewer"].strip(), "rationale": entry["rationale"].strip(),
            "examples_sha256": packet["examples_sha256"],
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "identity_verified": False,
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["export", "apply"])
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new path to preserve review history")
    try:
        cases = json.loads(args.cases.read_text())
        if args.action == "apply":
            if args.packet is None:
                parser.error("apply requires --packet")
            output = apply_packet(cases, json.loads(args.packet.read_text()))
        else:
            output = export_packet(cases)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as destination:
        destination.write(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
    print(f"Saved {args.action} output. Reviewer identity and independence are not verified by this tool.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
