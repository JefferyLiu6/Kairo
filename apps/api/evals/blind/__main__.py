"""
Run the blind eval.

    python -m evals.blind --path pm
    python -m evals.blind --path orchestrator --usd-in 0.15 --usd-out 0.60
    python -m evals.blind --path pm --scenario 01_freelancer --no-save
    python -m evals.blind --path orchestrator --record "why this run was made"
    python -m evals.blind --history

Exit code is 0 unless `--gate` is given and the quality bar is missed.
"""
from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from evals.blind.adapters import PATHS, PathUnavailable
from evals.blind.ledger import LEDGER, append, entry_from_report, load, render_history
from evals.blind.runner import default_report_path, render_summary, run_blind_eval, save_report
from evals.blind.spec import DEFAULT_SET, load_blind_set
from evals.paths import API_ROOT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.blind", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", choices=sorted(PATHS), help="which path to score")
    parser.add_argument("--scenario", action="append", metavar="ID", help="run only this scenario (repeatable)")
    parser.add_argument("--set", default=str(DEFAULT_SET), help="the eval set file")
    parser.add_argument("--output", help="where to write the JSON report (default: artifacts/blind/)")
    parser.add_argument("--no-save", action="store_true", help="print the summary only")
    parser.add_argument("--gate", action="store_true", help="exit 1 if the quality bar is missed")
    parser.add_argument("--usd-in", type=float, help="price per million input tokens, to report cost")
    parser.add_argument("--usd-out", type=float, help="price per million output tokens, to report cost")
    parser.add_argument("--record", metavar="NOTE", help="add this run to the scores ledger, with why it was made")
    parser.add_argument("--history", action="store_true", help="print the scores ledger and exit")
    parser.add_argument("--ledger", default=str(LEDGER), help="the scores ledger file")
    args = parser.parse_args(argv)
    if args.history:
        print(render_history(load(args.ledger)))
        return 0
    if not args.path:
        parser.error("--path is required")
    if (args.usd_in is None) != (args.usd_out is None):
        parser.error("give both --usd-in and --usd-out, or neither")
    if args.record is not None and args.scenario:
        parser.error("--record needs the whole set; a run of selected scenarios cannot be compared with others")

    load_dotenv(API_ROOT / ".env", override=False)  # model credentials, as the API process would see them

    def progress(scenario, record) -> None:
        print(f"  {scenario.id} {record['turn']:>2} {record['outcome']:<11} {record['seconds']:>6.2f}s  "
              f"{record['say'][:70]}", file=sys.stderr, flush=True)

    try:
        report = run_blind_eval(
            load_blind_set(args.set), PATHS[args.path](), only=args.scenario, on_turn=progress,
            usd_per_mtok=None if args.usd_in is None else (args.usd_in, args.usd_out),
        )
    except PathUnavailable as exc:
        print(f"Cannot run path '{args.path}': {exc}", file=sys.stderr)
        return 2

    print(render_summary(report))
    saved = None
    if not args.no_save:
        saved = save_report(report, args.output or default_report_path(report))
        print(f"\nReport: {saved}")
    if args.record is not None:
        append(entry_from_report(report, args.record, report_file=saved.name if saved else None), args.ledger)
        print(f"Recorded in {args.ledger}")
    return 1 if args.gate and not report["gate"]["passed"] else 0


if __name__ == "__main__":
    sys.exit(main())
