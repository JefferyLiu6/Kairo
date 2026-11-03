import json
from pathlib import Path
import pytest
from assistant.orchestrator.agent import OrchestratorConfig
from assistant.orchestrator.eval_report import combine
from assistant.orchestrator.final_eval import capture_suite, capture_final, TITLE
from assistant.orchestrator.judge_eval import content_hash, summarize
from assistant.orchestrator.runtime_eval import DEFAULT_CASES, run
from assistant.personal_manager.persistence.store import _todos_path, todo_add


def test_outage_cannot_look_like_successful_negative_grading():
    result = summarize([dict(expected='fail', status='provider_error', label='abstain', duration_ms=1)])
    assert result['false_pass_rate'] == 0
    assert result['false_pass_rate_scored'] is None
    assert result['negative_grade_coverage'] == 0
    assert result['judge_abstentions'] == 0
    assert result['grading_failures'] == 1


def test_deliberate_abstention_is_separate_from_outage():
    result = summarize([dict(expected='abstain', status='ok', label='abstain', duration_ms=1)])
    assert result['judge_abstentions'] == 1
    assert result['grading_failures'] == 0


def test_runtime_contract_includes_policy_and_failure_cases():
    result = run(json.loads(DEFAULT_CASES.read_text()))
    assert result['summary']['decision_agreement'] == 1
    assert result['summary']['blocked_retries'] == 3
    assert result['summary']['provider_errors'] == 1
    assert result['summary']['invalid'] == 1


def test_runtime_live_invoke_does_not_use_replay_answer():
    cases = json.loads(DEFAULT_CASES.read_text())[:1]
    result = run(cases, lambda *_: json.dumps(dict(verdict='fallback', confidence=.9,
                 reason='Test disagreement', suggested_fix='', failure_type='null')))
    assert result['summary']['decision_agreement'] == 0


def test_seeded_scenarios_survive_failed_lifecycle_creation():
    seen = set()
    def execute(request, config):
        seen.add(config.user_id)
        return 'No change'
    result = capture_suite(OrchestratorConfig(session_id='x'), execute)
    assert len(result['scenarios']) == 6
    assert result['summary']['attempted_turns'] == 9
    identity = next(r for r in result['observations'] if r['id'] == 'target_identity/complete')
    assert len(identity['before']) == 2
    assert identity['state_check_passed'] is False
    assert len(seen) == 6


def test_wrong_task_completion_fails_identity_check():
    def execute(request, config):
        path = Path(_todos_path(config.user_id, config.data_dir))
        if path.exists():
            data = json.loads(path.read_text())
            for item in data['items']:
                if item['title'] != TITLE:
                    item['done'] = True
            path.write_text(json.dumps(data))
        return 'Done'
    result = capture_suite(OrchestratorConfig(session_id='x'), execute)
    row = next(r for r in result['observations'] if r['id'] == 'target_identity/complete')
    assert row['state_check_passed'] is False


def failed_capture():
    def execute(request, config):
        if request.startswith('Add'):
            todo_add(TITLE, None, config.user_id, config.data_dir)
            raise TimeoutError('after write')
        return 'No change'
    return capture_final(OrchestratorConfig(session_id='x'), execute)


def judge_for(capture):
    return {'metadata': {'mode': 'live', 'repeats': 1,
                        'case_content_sha256': content_hash(capture['cases'])},
            'rows': [dict(id=c['id'], repeat=0, expected=None, category=c['category'],
                          label='pass', status='ok', duration_ms=1) for c in capture['cases']]}


def test_combined_report_retains_failure_in_denominator():
    capture = failed_capture()
    report = combine(capture, judge_for(capture))
    assert report['attempted_turns'] == 3
    assert report['execution_errors'] == 1
    assert report['capture_coverage'] == pytest.approx(2/3)
    assert report['judge_passes_per_attempted_turn_repeat'] == pytest.approx(2/3)
    assert report['turns'][0]['state_check_passed'] is True
    assert report['turns'][0]['grades'] == []
    assert report['judge_summary']['agreement_all_cases'] is None


@pytest.mark.parametrize('change', ['hash', 'missing', 'duplicate', 'error_pass'])
def test_combined_report_rejects_unrelated_or_incomplete_grading(change):
    capture = failed_capture()
    judge = judge_for(capture)
    if change == 'hash':
        judge['metadata']['case_content_sha256'] = 'other'
    elif change == 'missing':
        judge['rows'].pop()
    elif change == 'duplicate':
        judge['rows'].append(judge['rows'][0])
    else:
        judge['rows'][0]['status'] = 'provider_error'
    with pytest.raises(ValueError):
        combine(capture, judge)
