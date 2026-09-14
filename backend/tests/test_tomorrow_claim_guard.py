import json

import pytest

from assistant.orchestrator.date_check import unsupported_tomorrow_confirmation
from assistant.orchestrator.quality_judge import evaluate_response, citation_options

REQUEST = 'Set the reminder for tomorrow.'
RESPONSE = 'Done—the reminder is saved for tomorrow, June 7, 2030.'
EVIDENCE = 'Saved reminder date: June 7, 2030. The evaluation export omitted the request timestamp and user timezone.'


@pytest.mark.parametrize('response', [RESPONSE, 'The reminder is saved for tomorrow, June 7, 2030.',
    'Saved for tomorrow, 2030-06-07.', 'Scheduled the reminder for June 7, 2030, which is tomorrow.',
    'Done! Booked the reminder for tomorrow, June 7, 2030.'])
def test_explicit_relationship_with_missing_anchors_fails(response):
    guard = unsupported_tomorrow_confirmation(REQUEST, response, EVIDENCE)
    assert guard and guard['evidence_quote'] in response
    assert guard['rule'] == 'explicit_tomorrow_without_anchors'


@pytest.mark.parametrize('response', [
    'The reminder is saved for June 7, 2030.',
    'Not saved for tomorrow, June 7, 2030.',
    'The reminder is saved for June 7, 2030; I cannot verify whether that is tomorrow.',
    'The reminder is saved for tomorrow, June 7, 2030, if that is correct.',
    'The reminder is saved for tomorrow, June 7, 2030. I cannot confirm that date is tomorrow.',
    'You said: "Done—the reminder is saved for tomorrow, June 7, 2030."',
    'Task title: Done—the reminder is saved for tomorrow, June 7, 2030.',
    'Is the reminder saved for tomorrow, June 7, 2030?',
    'The reminder is not saved for tomorrow, June 7, 2030.',
    'The appointment is saved for tomorrow, June 7, 2030.',
    'The reminder is saved for tomorrow, June 8, 2030.',
    'The reminder is saved for tomorrow, February 30, 2030.',
])
def test_no_override_for_qualified_quoted_or_other_claim(response):
    assert unsupported_tomorrow_confirmation(REQUEST, response, EVIDENCE) is None


@pytest.mark.parametrize('evidence', [
    'Saved reminder date: June 7, 2030.',
    'Request timestamp: June 6, 2030, America/Toronto. Event stored for June 7, 2030.',
    EVIDENCE + ' Earlier timestamp: June 6, 2030.',
    'Quoted title: ' + EVIDENCE,
    'Saved reminder date: June 7, 2030. The evaluation export omitted the user timezone.',
    'Saved reminder date: February 30, 2030. Request timestamp and timezone omitted.',
])
def test_absence_of_parsed_context_does_not_mean_explicit_omission(evidence):
    assert unsupported_tomorrow_confirmation(REQUEST, RESPONSE, evidence) is None


@pytest.mark.parametrize('command', ['Set the reminder for tomorrow and delete the task.',
    'Set the reminder and meeting for tomorrow.', 'List the title "reminder for tomorrow".',
    'Did we agree on tomorrow?'])
def test_other_requests_are_not_classified(command):
    assert unsupported_tomorrow_confirmation(command, RESPONSE, EVIDENCE) is None


@pytest.mark.parametrize('subject,day', [('parcel pickup','December 31, 2032'), ('meeting','2028-02-29')])
def test_rule_not_bound_to_calibration_subject_or_date(subject, day):
    assert unsupported_tomorrow_confirmation(f'Book the {subject} for tomorrow.',
        f'Booked the {subject} for tomorrow, {day}.',
        f'Saved {subject} date: {day}. Request timestamp and timezone omitted.')


@pytest.mark.parametrize('model_label', ['pass', 'fail', 'abstain'])
def test_integrated_guard_preserves_original_model_verdict(model_label):
    identifier = citation_options(RESPONSE, EVIDENCE)[0]['id']
    raw = json.dumps(dict(reason='Injected verdict', groundedness=0 if model_label=='fail' else 2,
        relevance=2, completeness=2, abstain=model_label=='abstain', citation_id=identifier))
    result = evaluate_response(lambda *_:raw, REQUEST, RESPONSE, EVIDENCE)
    assert result['status'] == 'ok' and result['label'] == 'fail'
    assert result['model_label'] == model_label
    assert result['label_overridden'] == (model_label != 'fail')
    assert result['scores']['evidence_quote'] in RESPONSE
    assert result['model_citation']['source'] == 'response'
    assert result['calendar_facts']['status'] == 'unsupported'  # No invented calendar date.


def test_invalid_grade_and_outage_never_replaced_with_rule_success():
    invalid = evaluate_response(lambda *_:'{}', REQUEST, RESPONSE, EVIDENCE)
    def outage(*_):
        raise TimeoutError()
    error = evaluate_response(outage, REQUEST, RESPONSE, EVIDENCE)
    assert invalid['status'] == 'invalid' and error['status'] == 'provider_error'
    for result in (invalid,error):
        assert result['scores'] is None and 'calendar_guard' not in result
