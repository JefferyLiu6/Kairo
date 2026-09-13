"""Evaluate the runtime harness separately from response-quality scoring."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from .harness import evaluate_harness, enforce_retry_policy
from .prompts import HARNESS_SYSTEM
from .translator import StructuredAction

DEFAULT_CASES = Path(__file__).parents[2] / 'tests/fixtures/runtime_judge_cases.json'


def run(cases, invoke=None):
    if not cases or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Nonempty cases with unique IDs required')
    cases = [c for c in cases if invoke is None or not c.get('contract_only', False)]
    if not cases:
        raise ValueError('No applicable cases')
    rows = []
    for case in cases:
        action = StructuredAction(**case['action'])
        if case['expected'] not in {'pass', 'retry', 'fallback'}:
            raise ValueError('Invalid reference decision')
        def replay(_system, _payload):
            if case.get('replay_error'):
                raise TimeoutError('Injected offline failure')
            return case['replay_output']
        raw, source = evaluate_harness(case['request'], action, case['output'], '', invoke or replay)
        effective = enforce_retry_policy(action, raw)
        rows.append({'id': case['id'], 'expected': case['expected'],
                     'raw_decision': raw.verdict, 'decision': effective.verdict, 'source': source,
                     'matches_reference': effective.verdict == case['expected'],
                     'retry_blocked': raw.verdict == 'retry' and effective.verdict == 'fallback'})
    semantic = [r for r in rows if r['source'] == 'llm']
    return {'rows': rows, 'summary': {
        'cases': len(rows), 'decision_agreement': sum(r['matches_reference'] for r in rows)/len(rows),
        'semantic_grades': len(semantic),
        'semantic_agreement': sum(r['matches_reference'] for r in semantic)/len(semantic) if semantic else None,
        'provider_errors': sum(r['source'] == 'provider_error' for r in rows),
        'invalid': sum(r['source'] == 'invalid' for r in rows),
        'blocked_retries': sum(r['retry_blocked'] for r in rows),
    }}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=DEFAULT_CASES)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--provider', choices=['openai', 'anthropic', 'ollama'], default='openai')
    parser.add_argument('--model')
    parser.add_argument('--max-calls', type=int, default=20)
    args = parser.parse_args()
    data = args.cases.read_bytes()
    cases = json.loads(data)
    if args.max_calls < len(cases):
        parser.error('Case count exceeds logical call ceiling')
    if args.output.exists():
        parser.error('Choose a new output file')
    invoke = None
    if args.live:
        if not args.model:
            parser.error('--live requires --model')
        from assistant.shared.llm_env import build_llm
        from langchain_core.messages import HumanMessage, SystemMessage
        llm = build_llm(args.provider, args.model, os.getenv('JUDGE_API_KEY'), os.getenv('JUDGE_BASE_URL'))
        if hasattr(llm, 'max_retries'):
            llm.max_retries = 0
        def invoke(system, payload):
            return str(llm.invoke([SystemMessage(content=system), HumanMessage(content=payload)]).content)
    result = run(cases, invoke)
    result['metadata'] = {
        'mode': 'live' if args.live else 'replay_contract_only',
        'dataset_sha256': hashlib.sha256(data).hexdigest(),
        'prompt_sha256': hashlib.sha256(HARNESS_SYSTEM.encode()).hexdigest(),
        'provider': args.provider if args.live else None, 'model': args.model if args.live else None,
        'git_revision': subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip(),
        'working_tree_dirty': bool(subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True).stdout.strip()),
        'label_source': 'Authored development decisions, not independently reviewed',
        'scope': 'One runtime decision plus retry eligibility; excludes retry exhaustion and actual tool execution',
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result['summary'], indent=2))
    # Authored error fixtures are intentional in replay; live failures must remain visible.
    return int((not args.live and result['summary']['decision_agreement'] != 1) or
               (args.live and (result['summary']['provider_errors'] or result['summary']['invalid'])))


if __name__ == '__main__':
    raise SystemExit(main())
