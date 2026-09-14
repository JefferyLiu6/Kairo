"""Worked examples must obey the production parser and their declared label."""
import json
import pytest
from assistant.orchestrator.quality_judge import PROMPT_EXAMPLES, parse_quality_verdict


@pytest.mark.parametrize('title,payload,output', PROMPT_EXAMPLES)
def test_worked_example_obeys_output_contract(title, payload, output):
    verdict = parse_quality_verdict(json.dumps(output), payload['response'], payload['tool_evidence'])
    assert verdict.label == title.split(':')[0].lower()
