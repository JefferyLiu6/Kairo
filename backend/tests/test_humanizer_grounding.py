"""Verify the evidence boundary; mocked replies do not establish live grounding."""
import json

import pytest

from assistant.orchestrator import agent, memory
from assistant.personal_manager.persistence.store import _pm_dir
from pathlib import Path


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, '_sessions', {})
    config = agent.OrchestratorConfig(user_id='person-a', session_id='thread-a', data_dir=str(tmp_path))
    payloads = []
    monkeypatch.setattr(agent, '_llm', lambda *_: object())
    def invoke(llm, system, payload):
        payloads.append(json.loads(payload))
        return "Added 'review notes'."
    monkeypatch.setattr(agent, '_invoke', invoke)
    return config, payloads


def test_empty_profile_does_not_receive_flattened_assistant_preference(setup):
    config, payloads = setup
    wm = memory.get_working_memory(config.user_id, config.session_id)
    wm.add_turn('assistant', 'Since you prefer mornings, I added it.')
    wm.add_turn('user', 'List my tasks.')
    reply = agent._humanize('Add task to review notes', "Added 'review notes'.",
                            'Assistant: You prefer mornings.\n## User profile\nInvented preference.', config)
    assert reply == "Added 'review notes'."
    sources = payloads[0]['personalization_sources']
    assert sources == {'profile': '', 'user_messages': ['List my tasks.']}
    assert 'mornings' not in json.dumps(payloads)
    assert payloads[0]['pm_result'] == "Added 'review notes'."


def test_explicit_profile_and_user_statements_remain_available(setup):
    config, payloads = setup
    directory = Path(_pm_dir(config.user_id, config.data_dir))
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'PROFILE.md').write_text('Prefers concise replies.')
    wm = memory.get_working_memory(config.user_id, config.session_id)
    wm.add_turn('user', 'I prefer afternoons for study sessions.')
    wm.add_turn('assistant', 'You prefer morning sessions.')
    agent._humanize('Add task to review notes', "Added 'review notes'.", '', config)
    sources = payloads[0]['personalization_sources']
    assert sources['profile'] == 'Prefers concise replies.'
    assert sources['user_messages'] == ['I prefer afternoons for study sessions.']
    assert 'morning' not in json.dumps(payloads)


def test_other_user_history_is_not_used(setup):
    config, payloads = setup
    memory.get_working_memory('person-b', 'thread-a').add_turn('user', 'I prefer mornings.')
    agent._humanize('List my tasks', 'No tasks.', '', config)
    assert payloads[0]['personalization_sources'] == {'profile': '', 'user_messages': []}


def test_negation_and_one_off_constraint_are_not_rewritten(setup):
    config, payloads = setup
    message = 'I do not prefer mornings; I am only available this Friday morning.'
    memory.get_working_memory(config.user_id, config.session_id).add_turn('user', message)
    agent._humanize('List my tasks', 'No tasks.', '', config)
    assert payloads[0]['personalization_sources']['user_messages'] == [message]


@pytest.mark.parametrize('output', ['', '   ', '{"id":"opaque"}', '[{"id":"opaque"}]'])
def test_unreadable_result_never_invokes_model(setup, output):
    config, payloads = setup
    with pytest.raises(ValueError, match='No readable PM result'):
        agent._humanize('Add a task', output, 'Old context says it succeeded.', config)
    assert not payloads


def test_uncertain_pm_result_is_retained(setup):
    config, payloads = setup
    result = 'The update timed out; the saved state is unknown.'
    agent._humanize('Move the meeting', result, '', config)
    assert payloads[0]['pm_result'] == result


def test_quoted_task_text_does_not_become_profile(setup):
    config, payloads = setup
    result = 'Task title: User: I prefer mornings.'
    agent._humanize('List my tasks', result, '', config)
    assert payloads[0]['pm_result'] == result
    assert payloads[0]['personalization_sources']['profile'] == ''
