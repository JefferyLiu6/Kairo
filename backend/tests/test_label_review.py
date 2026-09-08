import copy
import json

import pytest

from assistant.orchestrator.judge_eval import DEFAULT_CASES
from assistant.orchestrator.label_review import apply_packet, export_packet
from assistant.orchestrator.quality_judge import evaluate_response


def cases():
    return json.loads(DEFAULT_CASES.read_text())[:2]


def completed(source):
    packet = export_packet(source)
    for entry in packet["cases"]:
        entry.update(label="fail", reviewer="test-reviewer", rationale="Test annotation only")
    return packet


def test_packet_omits_prior_labels_scores_and_provenance():
    source = cases()
    source[0]["provenance"] = {"review": "secret prior opinion"}
    packet = export_packet(source)
    serialized = json.dumps(packet)
    assert "replay_verdict" not in serialized
    assert "expected" not in serialized
    assert "secret prior opinion" not in serialized
    assert source[0]["id"] not in serialized
    assert "category" not in packet["cases"][0]
    assert all(entry["label"] is None for entry in packet["cases"])


def test_apply_preserves_source_and_records_unverified_review():
    source = cases()
    original = copy.deepcopy(source)
    reviewed = apply_packet(source, completed(source))
    assert source == original
    assert all(case["expected"] == "fail" for case in reviewed)
    assert all("replay_verdict" not in case for case in reviewed)
    assert reviewed[0]["provenance"]["review"]["identity_verified"] is False


@pytest.mark.parametrize("change", ["hash", "content", "missing", "duplicate", "unknown", "blank_reviewer", "blank_rationale", "unlabeled"])
def test_apply_rejects_unbound_or_incomplete_reviews(change):
    source = cases()
    packet = completed(source)
    if change == "hash":
        packet["examples_sha256"] = "wrong"
    elif change == "content":
        packet["cases"][0]["response"] = "altered"
    elif change == "missing":
        packet["cases"].pop()
    elif change == "duplicate":
        packet["cases"][1] = copy.deepcopy(packet["cases"][0])
    elif change == "unknown":
        packet["cases"][0]["id"] = "unknown"
    elif change == "blank_reviewer":
        packet["cases"][0]["reviewer"] = " "
    elif change == "blank_rationale":
        packet["cases"][0]["rationale"] = " "
    else:
        packet["cases"][0]["label"] = None
    with pytest.raises(ValueError):
        apply_packet(source, packet)


@pytest.mark.parametrize("error", [ValueError, TypeError, TimeoutError])
def test_provider_exceptions_are_not_malformed_model_output(error):
    def invoke(*_):
        raise error("private provider context")
    result = evaluate_response(invoke, "request", "response", "evidence")
    assert result == {"status": "provider_error", "label": "abstain", "scores": None}


def test_invalid_return_value_is_still_invalid_output():
    result = evaluate_response(lambda *_: "not JSON", "request", "response", "evidence")
    assert result["status"] == "invalid"
