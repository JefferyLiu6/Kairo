"""One-command local regression checks. No live judge or deployment step is included."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import uuid

BACKEND = Path(__file__).resolve().parents[2]


def checks(output):
    python = sys.executable
    return [
        ('backend_tests', [python, '-m', 'pytest', '-q', '-p', 'no:cacheprovider']),
        ('workflow_eval', [python, '-m', 'assistant.personal_manager.evals.runner', '--json']),
        ('quality_contract', [python, '-m', 'assistant.orchestrator.judge_eval',
                              '--repeats', '2', '--max-calls', '32',
                              '--output', str(output / 'quality.json')]),
        ('runtime_contract', [python, '-m', 'assistant.orchestrator.runtime_eval',
                              '--output', str(output / 'runtime.json')]),
    ]


def _text(value):
    return value.decode(errors='replace') if isinstance(value, bytes) else value or ''


def save_report(output, report):
    # Replace the manifest atomically so a reader sees the last completed stage.
    temporary = output / 'report.tmp'
    temporary.write_text(json.dumps(report, indent=2) + '\n')
    temporary.replace(output / 'report.json')
    lines = ['# Kairo offline development checks', '',
             f"Status: **{report['status']}**", '',
             'Scope: regression and authored-output contracts. Live judge quality is not measured.', '',
             '| Check | Result | Seconds | Output |', '|---|---|---:|---|']
    for stage in report['stages']:
        lines.append(f"| {stage['name']} | {stage['status']} | {stage['duration_seconds']} | "
                     f"[{stage['name']}.stdout.txt]({stage['name']}.stdout.txt) |")
    lines += ['', 'Logs may contain test/fixture text; keep artifacts local unless reviewed.',
              'A successful run does not establish human-label agreement, live model quality, or cloud health.']
    (output / 'README.md').write_text('\n'.join(lines) + '\n')


def run_checks(output: Path, timeout=180.0, execute=subprocess.run):
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Timeout must be positive and finite')
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {
        'schema_version': 1, 'mode': 'offline_development', 'status': 'running',
        'created_at': datetime.now(timezone.utc).isoformat(), 'stages': [],
        'scope': 'Backend regression, deterministic workflows, quality replay and runtime replay. '
                 'No live model quality or deployed service is measured.',
    }
    save_report(output, report)
    for name, command in checks(output):
        started = time.perf_counter()
        status, code, stdout, stderr = 'failed', None, '', ''
        try:
            result = execute(command, cwd=BACKEND, capture_output=True, text=True, timeout=timeout)
            code, stdout, stderr = result.returncode, result.stdout, result.stderr
            status = 'passed' if code == 0 else 'failed'
        except subprocess.TimeoutExpired as exc:
            status, stdout, stderr = 'timed_out', _text(exc.stdout), _text(exc.stderr)
        except OSError:
            status, stderr = 'launch_error', 'Could not start the local check.'
        except KeyboardInterrupt:
            report['status'] = 'interrupted'
            report['stages'].append({'name': name, 'status': 'interrupted', 'returncode': None,
                                     'duration_seconds': round(time.perf_counter() - started, 3)})
            save_report(output, report)
            raise
        (output / f'{name}.stdout.txt').write_text(_text(stdout))
        (output / f'{name}.stderr.txt').write_text(_text(stderr))
        report['stages'].append({'name': name, 'status': status, 'returncode': code,
                                'duration_seconds': round(time.perf_counter() - started, 3)})
        save_report(output, report)
    report['status'] = 'passed' if all(s['status'] == 'passed' for s in report['stages']) else 'failed'
    save_report(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    parser.add_argument('--output-dir', type=Path, default=BACKEND.parent / 'artifacts' / ('offline-' + stamp))
    parser.add_argument('--timeout', type=float, default=180, help='Seconds allowed per check')
    args = parser.parse_args()
    try:
        report = run_checks(args.output_dir, timeout=args.timeout)
    except (ValueError, FileExistsError) as exc:
        parser.error(str(exc))
    print(f"Offline checks: {report['status']}. Report: {args.output_dir.resolve() / 'README.md'}")
    return int(report['status'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())
