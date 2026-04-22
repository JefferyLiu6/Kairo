from __future__ import annotations

from assistant.personal_manager.evals import runner as eval_runner
import assistant.personal_manager.workflow as pm_workflow
from assistant.personal_manager.domain.types import PMExtraction, PMIntent


def _case_by_input(text: str) -> eval_runner.PMEvalCase:
    for case in eval_runner.load_eval_cases():
        if case.input == text:
            return case
    raise AssertionError(f"missing eval case: {text}")


def test_eval_runner_loads_fixture_schema():
    cases = eval_runner.load_eval_cases()

    assert cases
    assert all(case.expected_intent for case in cases)
    assert all(case.expected_action_type for case in cases)
    assert all(0 <= case.confidence_min <= case.confidence_max <= 1 for case in cases)


def test_eval_runner_scores_passing_extraction_without_workflow(tmp_path):
    case = _case_by_input("Add task to call John tomorrow")

    result = eval_runner.run_eval_case(
        case,
        eval_runner.EvalPMConfig(data_dir=str(tmp_path)),
        include_workflow=False,
    )

    assert result.passed
    assert result.extraction.intent == PMIntent.CREATE_TODO
    assert result.extraction.entities["title"] == "call John"


def test_eval_runner_scores_schedule_created_mutation(tmp_path):
    case = _case_by_input("I NEED TO EAT BREAKFAST AT 8 AM TMR")

    result = eval_runner.run_eval_case(
        case,
        eval_runner.EvalPMConfig(data_dir=str(tmp_path)),
        include_workflow=True,
    )

    assert result.passed
    assert result.actual_action_type == "schedule_add"
    assert result.extraction.entities["title"] == "Eat breakfast"


def test_eval_runner_reports_bad_expected_entity(tmp_path):
    case = eval_runner.PMEvalCase(
        input="Add task to call John tomorrow",
        expected_intent="CREATE_TODO",
        expected_entities={"title": "email Alice"},
        confidence_min=0.7,
        confidence_max=1.0,
        expected_action_type="todo_add",
        requires_approval=False,
        expected_mutation="todo_created",
    )

    result = eval_runner.run_eval_case(
        case,
        eval_runner.EvalPMConfig(data_dir=str(tmp_path)),
        include_workflow=False,
    )

    assert not result.passed
    assert any(check.name == "extraction.entities.title" for check in result.failures())


def test_eval_runner_scores_approval_without_preapproval_mutation(tmp_path):
    case = _case_by_input("Delete my dentist appointment")

    result = eval_runner.run_eval_case(
        case,
        eval_runner.EvalPMConfig(data_dir=str(tmp_path)),
        include_workflow=True,
    )

    assert result.passed
    assert result.actual_action_type == "schedule_remove"


def test_eval_runner_supports_fake_model_extractor_for_messy_phrase(tmp_path, monkeypatch):
    case = _case_by_input("delete the thing with Alex")

    def fake_model_extract(message, _config):
        if message != case.input:
            return None
        return PMExtraction(
            intent=PMIntent.REMOVE_SCHEDULE_EVENT,
            entities={"query": "Alex"},
            confidence=0.9,
            missing_fields=[],
            reasoning_summary="Vague Alex item maps to schedule",
            source="model_structured",
        )

    monkeypatch.setattr(pm_workflow, "_extract_pm_request_with_model", fake_model_extract)

    result = eval_runner.run_eval_case(
        case,
        eval_runner.EvalPMConfig(data_dir=str(tmp_path)),
        include_workflow=True,
    )

    assert result.passed
    assert result.extraction.source == "model_structured"
    assert result.actual_action_type == "schedule_remove"
