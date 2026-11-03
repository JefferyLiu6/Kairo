"""PM model calls: JSON in, validated contracts out, soft but recorded failures."""
from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import get_args

import pytest

import kairo.agents.personal_manager.extractors.model as pm_model
from kairo.agents.personal_manager import agent as pm_agent
from kairo.agents.personal_manager.application import extraction
from kairo.agents.personal_manager.application import semantic_memory
from kairo.agents.personal_manager.contracts import MemoryCandidateOut, ModelPlan, ModelRequest, ModelTask, PassiveFactOut
from kairo.core.domain.types import PMIntent
from kairo.core.llm_env import message_text

CONFIG = SimpleNamespace(provider="ollama", model="test-model", api_key=None, base_url=None)


class FakeChat:
    """Stands in for a LangChain chat model: replays canned replies, records what it was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent = []

    def invoke(self, messages, config=None):
        self.sent.append((messages, config))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(content=reply)


@pytest.fixture
def chat(monkeypatch):
    def install(*replies):
        fake = FakeChat(*replies)
        monkeypatch.setattr(pm_model, "build_llm", lambda *_a, **_k: fake)
        return fake
    return install


_TASK = dict(task_id="task-1", intent="CREATE_TODO", entities={"title": "call mom"}, confidence=0.9,
             missing_fields=[], depends_on=[], source="model_structured", reasoning_summary="todo")
_PLAN = dict(tasks=[_TASK], global_missing_fields=[], confidence=0.9, source="model_structured")


def test_plan_extraction_sends_json_and_returns_a_typed_plan(chat):
    fake = chat(json.dumps(_PLAN))

    plan = extraction._extract_pm_plan_with_model("remind me to call mom", CONFIG)

    assert plan.tasks[0].intent == PMIntent.CREATE_TODO
    assert plan.source == "model_structured"
    messages, config = fake.sent[0]
    assert json.loads(messages[1].content) == {"message": "remind me to call mom"}
    assert "remind me to call mom" not in messages[0].content  # the message is data, not prompt text
    assert config == {"run_name": "plan_extraction"}


def test_missing_confidence_is_repaired_not_defaulted_to_certain(chat):
    no_confidence = {k: v for k, v in _PLAN.items() if k != "confidence"}
    fake = chat(json.dumps(no_confidence), json.dumps(_PLAN))

    plan = extraction._extract_pm_plan_with_model("remind me to call mom", CONFIG)

    assert plan is not None and len(fake.sent) == 2
    assert "confidence" in fake.sent[1][0][1].content


def test_unusable_reply_returns_none_and_is_logged(chat, caplog):
    chat("sure, I'll add that", "still prose")
    assert extraction._extract_pm_request_with_model("add milk", CONFIG) is None
    assert "request_extraction reply failed its contract" in caplog.text


def test_provider_error_returns_none_and_logs_only_its_type(chat, caplog):
    chat(TimeoutError("upstream said: secret details"))
    assert extraction._extract_pm_plan_with_model("add milk", CONFIG) is None
    assert "plan_extraction model call failed: TimeoutError" in caplog.text
    assert "secret details" not in caplog.text


def test_memory_candidates_are_validated_and_cleaned(chat):
    candidate = dict(memory_type="generic_interest", subject="User", predicate="likes", object="Basketball",
                     qualifiers={}, polarity="positive", confidence=0.9, stability="stable",
                     scheduling_relevance="none", sensitivity="low", evidence="")
    chat(json.dumps({"candidates": [candidate]}))

    [saved] = semantic_memory._interpret_with_model("I like basketball", CONFIG)

    assert (saved.subject, saved.predicate, saved.source) == ("user", "likes", "model_memory_interpreter")
    assert saved.evidence == "I like basketball"


def test_memory_rejects_invented_predicates(chat):
    bad = dict(memory_type="goal", predicate="adores", object="cats", polarity="positive", confidence=0.9)
    chat(json.dumps({"candidates": [bad]}), json.dumps({"candidates": [bad]}))
    assert semantic_memory._interpret_with_model("I adore cats", CONFIG) == []


def test_passive_fact_below_threshold_is_not_saved(chat, monkeypatch, tmp_path):
    chat(json.dumps(dict(should_save=True, confidence=0.5, fact="User likes jazz", storage="profile", reasoning="")))
    saved = []
    monkeypatch.setattr(pm_agent, "execute_memory_action", lambda *a, **k: saved.append(a))
    config = SimpleNamespace(**vars(CONFIG), vault_dir=str(tmp_path), session_id="pm-x", data_dir=str(tmp_path))

    assert pm_agent._try_passive_fact_save("I really like jazz", config) is None
    assert saved == []


def test_message_text_joins_anthropic_content_parts():
    reply = SimpleNamespace(content=[{"type": "text", "text": '{"a":'}, {"type": "text", "text": "1}"}])
    assert message_text(reply) == '{"a":\n1}'


# ── Prompts and schemas cannot drift apart ────────────────────────────────────

def _listed_keys(prompt: str, header: str) -> set[str]:
    section = prompt.split(header, 1)[1].split("\n\n", 1)[0]
    return set(re.findall(r"^- ([a-z_]+):", section, re.M))


def test_plan_prompt_lists_exactly_the_contract_keys():
    prompt = pm_model._format_model_plan_prompt()
    assert _listed_keys(prompt, "Return one JSON object with exactly these keys:") == set(ModelPlan.model_fields)
    assert _listed_keys(prompt, "Each task object must contain exactly these keys:") == set(ModelTask.model_fields)


def test_request_prompt_lists_exactly_the_contract_keys():
    prompt = pm_model._format_model_extraction_prompt()
    assert _listed_keys(prompt, "Return one JSON object with exactly these keys:") == set(ModelRequest.model_fields)


def test_memory_prompt_example_matches_the_contract():
    prompt = semantic_memory._format_memory_interpreter_prompt()
    example = dict(re.findall(r'^\s*"([a-z_]+)": "([^"]*)"', prompt, re.M))
    keys = set(re.findall(r'^\s*"([a-z_]+)":', prompt, re.M)) - {"candidates"}
    assert keys == set(MemoryCandidateOut.model_fields)
    for field in ("memory_type", "predicate", "polarity", "stability", "scheduling_relevance", "sensitivity"):
        listed = [v.strip() for v in example[field].split("|")]
        allowed = list(get_args(MemoryCandidateOut.model_fields[field].annotation))
        assert listed == allowed, field


def test_passive_prompt_example_matches_the_contract():
    keys = set(re.findall(r'^\s*"([a-z_]+)":', pm_agent._PASSIVE_EXTRACTION_PROMPT, re.M))
    assert keys == set(PassiveFactOut.model_fields)
