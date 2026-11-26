from evals.judge_eval import summarize


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
