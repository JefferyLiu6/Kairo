"""
Run the model date test against a real model.

    python -m evals.datefill                       the configured model, both arms, 3 repeats
    python -m evals.datefill --model gpt-4o        another model from the same provider
    python -m evals.datefill --arm calendar --repeats 1 --no-save

Exit code is 0 unless `--gate` is given and the decision is not "go".
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from evals.datefill.probe import ARTIFACTS, DEFAULT_CASES, load_cases, model_asker, render, run_probe, save_report
from evals.datefill.tools import ARMS
from evals.paths import API_ROOT
from kairo.core.llm_env import load_default_llm_from_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.datefill", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", help="model name (default: MODEL from the environment)")
    parser.add_argument("--arm", action="append", choices=ARMS, help="run only this arm (repeatable)")
    parser.add_argument("--repeats", type=int, default=3, help="answers per case per arm (default 3)")
    parser.add_argument("--workers", type=int, default=8, help="requests in flight at once (default 8)")
    parser.add_argument("--cases", default=str(DEFAULT_CASES), help="the case file")
    parser.add_argument("--output", help="where to write the JSON report (default: artifacts/datefill/)")
    parser.add_argument("--no-save", action="store_true", help="print the summary only")
    parser.add_argument("--gate", action="store_true", help="exit 1 unless the decision is go")
    args = parser.parse_args(argv)

    load_dotenv(API_ROOT / ".env", override=False)  # model credentials, as the API process would see them
    env = load_default_llm_from_env()
    if not env:
        print("No model credentials found (OPENAI_API_KEY, ANTHROPIC_API_KEY or an Ollama setting).", file=sys.stderr)
        return 2
    model = args.model or env["model"]
    with open(args.cases, encoding="utf-8") as f:
        now = datetime.fromisoformat(json.load(f)["now"])

    report = run_probe(
        load_cases(args.cases), model_asker(env["provider"], model, env.get("api_key"), env.get("base_url")),
        now=now, repeats=args.repeats, arms=tuple(args.arm or ARMS), workers=args.workers,
        model={"provider": env["provider"], "model": model},
    )
    print(render(report))
    if not args.no_save:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        saved = save_report(report, args.output or ARTIFACTS / f"{model.replace('/', '_')}-{stamp}.json")
        print(f"\nReport: {saved}")
    return 1 if args.gate and report["decision"] != "go" else 0


if __name__ == "__main__":
    sys.exit(main())
