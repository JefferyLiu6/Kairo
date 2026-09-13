import json
from pathlib import Path
import pytest
from assistant.orchestrator.judge_eval import summarize, run, DEFAULT_CASES
from assistant.orchestrator.judge_metrics import repeat_metrics
from assistant.orchestrator.quality_judge import PROMPT_EXAMPLES, parse_quality_verdict


def row(expected, label, status='ok', **extra):
    return dict(expected=expected, label=label, status=status, duration_ms=1, **extra)


@pytest.mark.parametrize('label,balanced,recall,acceptance', [
    ('pass', .5, 0, 1), ('fail', .5, 1, 0), ('abstain', 0, 0, 0)])
def test_trivial_judges_do_not_look_perfect(label, balanced, recall, acceptance):
    result = summarize([row('pass', label)]*9 + [row('fail', label)])
    assert result['balanced_decision_accuracy'] == balanced
    assert result['failure_detection_recall'] == recall
    assert result['good_answer_acceptance'] == acceptance
    assert result['metric_details']['failure_detection_recall']['denominator'] == 1


def test_outage_is_not_successful_uncertainty_detection():
    result = summarize([row('abstain', 'abstain', 'provider_error')])
    assert result['uncertainty_recall'] == 0
    assert result['usable_verdict_rate'] == 0
    assert result['balanced_decision_accuracy'] is None


def test_unreviewed_and_reference_abstain_are_not_binary_failures():
    result = summarize([row('abstain', 'pass'), row(None, 'fail')])
    assert result['failure_detection_recall'] is None
    assert result['good_answer_acceptance'] is None
    assert result['unnecessary_abstention_rate'] is None
    assert result['usable_verdict_rate'] == 1


def test_errors_abstentions_and_rejections_are_distinct():
    result = summarize([row('pass', 'fail'), row('pass', 'abstain'),
                        row('pass', 'abstain', 'invalid'), row('pass', 'pass')])
    assert result['false_rejection_rate'] == .25
    assert result['good_answer_acceptance'] == .25
    assert result['unnecessary_abstention_rate'] == .25
    assert result['usable_verdict_rate'] == .75


def test_score_drift_detected_even_when_both_labels_fail():
    rows = [row('fail', 'fail', id='x', scores=dict(groundedness=0,relevance=2,completeness=1)),
            row('fail', 'fail', id='x', scores=dict(groundedness=1,relevance=2,completeness=1))]
    result = repeat_metrics(rows)
    assert result['outcome_disagreement_rate'] == 0
    assert result['score_disagreement_rate'] == 1


def test_status_changes_not_hidden_by_abstain_label():
    result = repeat_metrics([row('abstain','abstain',id='x'),
                             row('abstain','abstain','provider_error',id='x')])
    assert result['outcome_disagreement_rate'] == 1
    assert result['score_disagreement_rate'] is None
    assert repeat_metrics([])['outcome_disagreement_rate'] is None


@pytest.mark.parametrize('title,payload,verdict', PROMPT_EXAMPLES)
def test_worked_examples_obey_real_parser_and_label(title, payload, verdict):
    parsed = parse_quality_verdict(json.dumps(verdict),payload['response'],payload['tool_evidence'])
    assert parsed.label == title.split(':')[0].lower()
    fixtures = json.loads(Path(DEFAULT_CASES).read_text())
    assert payload['request'] not in {c['request'] for c in fixtures}


def test_explanations_present_in_replay_and_category_reports():
    result = run(json.loads(Path(DEFAULT_CASES).read_text()), repeats=2)
    assert result['summary']['failure_detection_recall'] == 1
    assert result['stability']['outcome_disagreement_rate'] == 0
    for category in result['by_category'].values():
        for spec in category['metric_details'].values():
            assert spec['question'] and spec['limitation']
