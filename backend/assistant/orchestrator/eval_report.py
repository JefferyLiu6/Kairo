"""Join attempted agent turns and optional content-bound judge results without hiding failures."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .judge_eval import content_hash, summarize


def combine(run, judge=None):
    observations, cases = run['observations'], run['cases']
    ids = [r['id'] for r in observations]
    case_ids = [c['id'] for c in cases]
    if len(ids) != len(set(ids)) or len(case_ids) != len(set(case_ids)):
        raise ValueError('Duplicate capture IDs')
    if set(case_ids) != {r['id'] for r in observations if r['execution_status'] == 'ok'}:
        raise ValueError('Captured replies must match successful execution rows')
    attempted = len(observations)
    rows = []
    repeats = None
    if judge is not None:
        metadata = judge['metadata']
        if metadata.get('mode') != 'live' or metadata.get('case_content_sha256') != content_hash(cases):
            raise ValueError('Judge must be live and grade exactly these captured responses and evidence')
        repeats = metadata.get('repeats')
        if type(repeats) is not int or repeats < 1:
            raise ValueError('Invalid repeat count')
        rows = judge['rows']
        pairs = [(r['id'], r['repeat']) for r in rows]
        if len(pairs) != len(set(pairs)) or set(pairs) != {(i, n) for i in case_ids for n in range(repeats)}:
            raise ValueError('Missing, extra, or duplicate judge rows')
        categories = {c['id']: c['category'] for c in cases}
        for row in rows:
            if row['category'] != categories[row['id']] or row['expected'] not in {'pass', 'fail', 'abstain', None}:
                raise ValueError('Invalid category or reference label')
            if row['status'] not in {'ok', 'invalid', 'provider_error'} or row['label'] not in {'pass', 'fail', 'abstain'}:
                raise ValueError('Invalid judge row')
            if row['status'] != 'ok' and row['label'] != 'abstain':
                raise ValueError('Grading errors cannot pass')
    def count(key, value):
        return sum(r[key] == value for r in observations)
    passed = sum(r['status'] == 'ok' and r['label'] == 'pass' for r in rows)
    return {
        'attempted_turns': attempted,
        'execution_successes': count('execution_status', 'ok'),
        'execution_errors': count('execution_status', 'execution_error'),
        'empty_responses': count('execution_status', 'empty_response'),
        'captured_replies': len(cases),
        'capture_coverage': len(cases)/attempted if attempted else None,
        'state_checks_passed': count('state_check_passed', True),
        'state_success_rate': count('state_check_passed', True)/attempted if attempted else None,
        'judge_status': 'attached' if judge is not None else 'not_run',
        'judge_summary': summarize(rows) if judge is not None else None,
        'judge_passes_per_attempted_turn_repeat': passed/(attempted*repeats) if attempted and repeats else None,
        'turns': [{**r, 'grades': [g for g in rows if g['id'] == r['id']]} for r in observations],
        'interpretation': 'State correctness and response quality are separate. Missing replies remain in attempted-turn denominators. Judge pass is not human-verified success. Repeats are not new independent cases.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--judge', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Choose a new output file')
    try:
        result = combine(json.loads(args.run.read_text()), json.loads(args.judge.read_text()) if args.judge else None)
    except (KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
