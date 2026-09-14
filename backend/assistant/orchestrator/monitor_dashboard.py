"""Build a local, content-minimized monitoring snapshot from saved reports and logs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html import escape
import json
import math
from pathlib import Path

from .judge_eval import percentile
from .monitor_report import summarize_logs

LABELS = {'pass', 'fail', 'abstain'}
STATUSES = {'ok', 'invalid', 'provider_error'}


def ratio(numerator: int, denominator: int) -> dict:
    return {'numerator': numerator, 'denominator': denominator,
            'value': numerator / denominator if denominator else None}


def report_summary(report: dict, name: str) -> dict:
    """Recompute from row evidence; never trust a supplied aggregate summary."""
    if not isinstance(report, dict) or not isinstance(report.get('rows'), list):
        raise ValueError('Expected a judge report with rows')
    rows = report['rows']
    for row in rows:
        if not isinstance(row, dict) or row.get('status') not in STATUSES:
            raise ValueError('Unknown or missing row status')
        if row.get('label') not in LABELS or row.get('expected') not in LABELS | {None}:
            raise ValueError('Unknown row label')
        if 'model_label' in row and row['model_label'] not in LABELS:
            raise ValueError('Unknown original model label')
        if 'label_overridden' in row and type(row['label_overridden']) is not bool:
            raise ValueError('Override flag must be boolean')
        if row.get('label_overridden') and ('model_label' not in row or 'calendar_guard' not in row):
            raise ValueError('Override requires original label and calendar guard')
        if 'calendar_guard' in row and 'model_label' not in row:
            raise ValueError('Calendar guard requires original model label')
    metadata = report.get('metadata') or {}
    if not isinstance(metadata, dict):
        raise ValueError('Metadata must be an object')
    mode = str(metadata.get('mode', 'unknown'))
    live = mode.startswith('live')
    kind = 'Live judge run' if live else ('Replay / offline' if 'replay' in mode else 'Other / unverified timing')
    # Postprocessed rows retain old latency and are not a new live run.
    if 'postprocess' in mode:
        kind = 'Saved-output postprocessing'
        live = False
    labeled = [r for r in rows if r.get('expected') in LABELS]
    ok = [r for r in rows if r['status'] == 'ok']
    negatives = [r for r in labeled if r['expected'] == 'fail']
    positives = [r for r in labeled if r['expected'] == 'pass']
    unknowns = [r for r in labeled if r['expected'] == 'abstain']
    assessable = [r for r in labeled if r['expected'] != 'abstain']
    durations = [r['duration_ms'] for r in rows if type(r.get('duration_ms')) in (int, float)
                 and math.isfinite(r['duration_ms']) and r['duration_ms'] >= 0]
    final_match = sum(r['status'] == 'ok' and r['label'] == r['expected'] for r in labeled)
    raw_match = sum(r['status'] == 'ok' and r.get('model_label', r['label']) == r['expected'] for r in labeled)
    eligible = [r for r in ok if 'calendar_guard' in r]
    overrides = sum(r['label'] != r['model_label'] for r in eligible)
    detail = [{'case': i + 1, 'reference': r.get('expected') or 'unreviewed',
               'model': r.get('model_label', r['label']) if r['status'] == 'ok' else 'No grade',
               'final': r['label'] if r['status'] == 'ok' else 'No grade', 'status': r['status'],
               'guard': 'Applied' if 'calendar_guard' in r else '—'} for i, r in enumerate(rows)]
    return {'name': name, 'kind': kind, 'mode': mode, 'live_latency': live,
            'model': str(metadata.get('model', 'Not recorded')),
            'rubric': str(metadata.get('rubric_version', 'Not recorded')),
            'rubric_hash': str(metadata.get('rubric_sha256', 'Not recorded')),
            'recorded_at': str(metadata.get('created_at', 'Not recorded')),
            'cases': len(rows), 'label_coverage': ratio(len(labeled), len(rows)),
            'model_agreement': ratio(raw_match, len(labeled)), 'final_agreement': ratio(final_match, len(labeled)),
            'failure_detection': ratio(sum(r['status'] == 'ok' and r['label'] == 'fail' for r in negatives), len(negatives)),
            'good_acceptance': ratio(sum(r['status'] == 'ok' and r['label'] == 'pass' for r in positives), len(positives)),
            'uncertainty_recall': ratio(sum(r['status'] == 'ok' and r['label'] == 'abstain' for r in unknowns), len(unknowns)),
            'unnecessary_abstention': ratio(sum(r['status'] == 'ok' and r['label'] == 'abstain' for r in assessable), len(assessable)),
            'false_passes': ratio(sum(r['status'] == 'ok' and r['label'] == 'pass' for r in negatives), len(negatives)),
            'semantic_abstentions': sum(r['label'] == 'abstain' for r in ok),
            'provider_errors': sum(r['status'] == 'provider_error' for r in rows),
            'invalid_outputs': sum(r['status'] == 'invalid' for r in rows),
            'valid_outputs': ratio(len(ok), len(rows)), 'guard_matches': len(eligible), 'guard_overrides': overrides,
            'latency_samples': len(durations), 'p50_ms': percentile(durations, .5), 'p95_ms': percentile(durations, .95),
            'decisions': detail}


def build_snapshot(paths: list[Path], telemetry: Path | None = None) -> dict:
    reports, seen = [], set()
    duplicates = 0
    for path in paths:
        data = json.loads(path.read_text())
        summary = report_summary(data, f"{path.parent.name}/{path.name}")
        fingerprint = hashlib.sha256(json.dumps({'rows': data['rows'], 'metadata': data.get('metadata')}, sort_keys=True).encode()).hexdigest()
        if fingerprint in seen:
            duplicates += 1
            continue
        seen.add(fingerprint)
        summary['source_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        reports.append(summary)
    runtime = None
    if telemetry:
        with telemetry.open() as source:
            runtime = summarize_logs(source)
    return {'schema_version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(),
            'reports': reports, 'duplicate_reports_skipped': duplicates, 'runtime': runtime,
            'scope': 'Local snapshot, not continuous monitoring. Reports stay separate; no pooled accuracy or release gate. No prompts, responses, evidence, rationales or case identifiers are copied.'}


def fraction(metric: dict) -> str:
    if metric['value'] is None:
        return 'Unavailable (0 references)'
    return f"{metric['numerator']}/{metric['denominator']} · {metric['value']:.0%}"


def render(snapshot: dict) -> str:
    def e(value):
        return escape(str(value), quote=True)
    cards = []
    definitions = [
        ('model_agreement', 'Model agreement', 'Original model verdicts matching human response-quality labels.'),
        ('final_agreement', 'Final agreement', 'After calendar rules. Same labels and denominator as model agreement.'),
        ('failure_detection', 'Failure detection', 'Human-failed answers rejected. Errors and abstentions count as misses.'),
        ('good_acceptance', 'Good-answer acceptance', 'Human-passed answers accepted. Prevents an always-fail judge looking useful.'),
        ('uncertainty_recall', 'Uncertainty recall', 'Human-abstain answers correctly abstained on. Task labels do not count here.'),
        ('false_passes', 'False passes', 'Human-failed answers incorrectly passed. Inspect failure detection too.'),
        ('valid_outputs', 'Usable grades', 'Schema-valid grades, including deliberate abstention. Not correctness.'),
        ('label_coverage', 'Human-label coverage', 'Labeled rows / all rows. Agreement uses labeled rows only.'),
    ]
    for i, run in enumerate(snapshot['reports']):
        metric_cards = ''.join(f'<div class="metric"><h3>{title}</h3><strong>{e(fraction(run[key]))}</strong><p>{desc}</p></div>' for key, title, desc in definitions)
        decisions = ''.join('<tr>'+''.join(f'<td>{e(row[k])}</td>' for k in ['case', 'reference', 'model', 'final', 'status', 'guard'])+'</tr>' for row in run['decisions'])
        latency_name = 'Observed judge-attempt latency' if run['live_latency'] else 'Offline / unverified duration — not live latency'
        timing = 'Unavailable' if run['p95_ms'] is None else f"p50 {run['p50_ms']:.0f} ms · p95 {run['p95_ms']:.0f} ms"
        cards.append(f'''<section class="run" id="run-{i}"><div class="eyebrow">{e(run['kind'])}</div>
<h2>{e(run['name'])}</h2><p class="meta">{e(run['model'])} · {e(run['rubric'])}<br>Recorded: {e(run['recorded_at'])}</p>
<div class="grid">{metric_cards}</div>
<div class="strip"><div><b>Provider errors: {run['provider_errors']}</b><br>Invalid outputs: {run['invalid_outputs']}<br>Deliberate abstentions: {run['semantic_abstentions']}</div>
<div><b>Calendar rule matches: {run['guard_matches']}</b><br>Label overrides: {run['guard_overrides']}<br>Rules do not authorize actions.</div>
<div><b>{latency_name}</b><br>{timing}<br>{run['latency_samples']}/{run['cases']} rows have usable timing.</div></div>
<details><summary>Inspect {run['cases']} decisions</summary><div class="scroll"><table><thead><tr><th>Row</th><th>Human response label</th><th>Model</th><th>Final</th><th>Status</th><th>Calendar rule</th></tr></thead><tbody>{decisions}</tbody></table></div></details>
<details><summary>Source fingerprints</summary><p class="hash">Report: {e(run['source_sha256'])}<br>Rubric: {e(run['rubric_hash'])}</p></details></section>''')
    runtime = snapshot['runtime']
    runtime_html = '<p>No runtime log supplied. Traffic, uptime, and production health are unknown.</p>'
    if runtime is not None:
        fallback = 'Unavailable' if runtime['fallback_rate'] is None else f"{runtime['fallback_rate']:.1%}"
        rows = ''.join(f"<tr><td>{e(name)}</td><td>{v['samples']}</td><td>{v['p50_ms']:.0f} ms</td><td>{v['p95_ms']:.0f} ms</td></tr>" for name, v in runtime['stages'].items())
        runtime_html = f'''<p>Observed turn spans: <b>{runtime['observed_turn_spans']}</b> · Fallback rate among observed turns: <b>{fallback}</b> · Judge invalid/provider-error events: <b>{runtime['judge_invalid_or_provider_errors']}</b></p>
<p>Malformed records: {runtime['malformed_records']} · Duplicates skipped: {runtime['duplicate_records']} · Unmatched fallback traces: {runtime['unmatched_fallback_traces']}</p>
<table><thead><tr><th>Stage</th><th>Samples</th><th>p50</th><th>p95</th></tr></thead><tbody>{rows}</tbody></table>
<p>Best-effort log observations, not a complete request census. A completed turn is not proof of task completion. Missing logs do not mean healthy service.</p>'''
    options = ''.join(f'<option value="run-{i}">{e(r["name"])}</option>' for i,r in enumerate(snapshot['reports']))
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kairo evaluation monitor</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f4f5f0;color:#172d30;font:16px/1.5 system-ui,sans-serif}header{background:#153d40;color:#fff;padding:42px max(24px,calc((100vw - 1180px)/2))}h1{font-size:38px;margin:8px 0}header p{max-width:850px;color:#d8e8e3}.eyebrow{text-transform:uppercase;letter-spacing:.14em;font-size:12px;font-weight:700;color:#537d68}header .eyebrow{color:#a9dec6}main{max-width:1230px;margin:auto;padding:24px}section{background:white;border:1px solid #d7dfd7;border-radius:14px;padding:26px;margin:24px 0}h2{margin:4px 0;font-size:25px;overflow-wrap:anywhere}.meta{color:#617371;font-size:14px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:25px}.metric{background:#f4f7f2;border-radius:10px;padding:18px}.metric h3{margin:0 0 10px;font-size:14px}.metric strong{font-size:23px}.metric p{font-size:12px;color:#5b6c65}.strip{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;padding:23px 0;font-size:14px}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:12px;border-bottom:1px solid #e1e6df}details{border-top:1px solid #e1e6df;padding:12px 0}summary{cursor:pointer;font-weight:600}.hash{font-family:monospace;overflow-wrap:anywhere;font-size:12px}select{max-width:100%;padding:10px;margin:0 12px;border:1px solid #aaa;border-radius:6px;font:inherit}footer{font-size:13px;color:#60736a;padding-bottom:30px}.note{border-left:4px solid #ba8c3b;padding:12px 18px;background:#fff9e9}@media(max-width:850px){.grid{grid-template-columns:repeat(2,1fr)}.strip{grid-template-columns:1fr}h1{font-size:30px}}@media(max-width:450px){.grid{grid-template-columns:1fr}}</style></head><body>
<header><div class="eyebrow">Kairo / local monitoring</div><h1>Decisions you can inspect.</h1><p>Human agreement, judge reliability, and calendar rules. Each run stands on its own. Task completion is a separate outcome.</p></header><main>''' + f'''
<p class="meta">Snapshot generated {e(snapshot['generated_at'])} · {len(snapshot['reports'])} reports · {snapshot['duplicate_reports_skipped']} duplicate reports skipped</p>
<p class="note">Saved reports, not a live feed. Different datasets or policies do not establish an accuracy trend. Small development samples are not production benchmarks.</p>
<label for="runs">Show run</label><select id="runs"><option value="all">All reports</option>{options}</select>
{''.join(cards) if cards else '<section><h2>No judge reports</h2><p>Quality and judge health are unknown. Supply reports to populate this snapshot.</p></section>'}
<section><div class="eyebrow">Runtime observations</div><h2>Application telemetry</h2>{runtime_html}</section>
<footer>Reports omit prompts, candidate responses, evidence, rationales and case identifiers. No external assets, API calls, tracking, automatic alerts, or deployment. Regenerate the snapshot when new reports are available.</footer></main>
<script>document.getElementById('runs').addEventListener('change',function(){{document.querySelectorAll('.run').forEach(el=>{{el.hidden=this.value!=='all'&&el.id!==this.value;}});}});</script></body></html>'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='*', type=Path)
    parser.add_argument('--telemetry', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--summary', type=Path)
    args = parser.parse_args()
    inputs = [p.resolve() for p in args.reports] + ([args.telemetry.resolve()] if args.telemetry else [])
    outputs = [args.output.resolve()] + ([args.summary.resolve()] if args.summary else [])
    if len(set(outputs)) != len(outputs) or any(p in inputs for p in outputs):
        parser.error('Output files must be distinct and must not overwrite inputs')
    try:
        snapshot = build_snapshot(args.reports, args.telemetry)
    except (ValueError, TypeError, OSError) as exc:
        parser.error(f'Cannot build snapshot ({type(exc).__name__}); check report schema and paths')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(snapshot))
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(snapshot, indent=2, allow_nan=False)+'\n')
    print(f'Monitoring snapshot saved: {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
