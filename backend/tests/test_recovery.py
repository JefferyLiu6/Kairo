"""Controlled faults with real task persistence and the production recovery loop."""
from unittest.mock import patch

import pytest

from assistant.orchestrator import agent as orch, memory
from assistant.orchestrator.harness import HarnessVerdict, evaluate_harness
from assistant.orchestrator.translator import StructuredAction
from assistant.personal_manager.persistence import store


@pytest.fixture
def setup(tmp_path, monkeypatch):
    memory._sessions.clear()
    config = orch.OrchestratorConfig(user_id='recovery-user', session_id='pm-recovery',
                                    data_dir=str(tmp_path), provider='offline', model='unused')
    monkeypatch.setattr(orch, '_route', lambda *_: True)
    monkeypatch.setattr(orch, '_humanize', lambda _m, output, *_: output)
    monkeypatch.setattr(orch, '_judge', lambda *_: HarnessVerdict('pass', 1, 'fixture', '', 'null'))
    yield config, monkeypatch
    memory._sessions.clear()


def action(setup, write=True):
    config, mp = setup
    a = StructuredAction('add_todo' if write else 'show_todos', None, [], 1,
                         'Add task to recovery probe' if write else 'List my todos', write)
    mp.setattr(orch, '_translate', lambda *_: a)
    return a


def rows(config):
    return store.load_todos(config.user_id, config.data_dir).items


def test_response_failure_after_write_returns_recovery(setup):
    config, mp = setup
    action(setup)
    calls = []
    async def pm(*_):
        calls.append(1)
        return store.todo_add('recovery probe', None, config.user_id, config.data_dir)
    mp.setattr(orch, '_call_pm', pm)
    mp.setattr(orch, '_humanize', lambda *_: (_ for _ in ()).throw(TimeoutError('SECRET')))
    reply = orch.run_orchestrator('Add a task', config)
    assert len(calls) == len(rows(config)) == 1
    assert "couldn't confirm" in reply.lower()
    assert 'SECRET' not in reply


@pytest.mark.parametrize('judge_fault', ['outage', 'malformed', 'retry'])
def test_judge_fault_cannot_replay_write(setup, judge_fault):
    config, mp = setup
    a = action(setup)
    calls = []
    async def pm(*_):
        calls.append(1)
        return store.todo_add('recovery probe', None, config.user_id, config.data_dir)
    def invoke(*_):
        if judge_fault == 'outage':
            raise TimeoutError('SECRET')
        if judge_fault == 'malformed':
            return 'not json'
        return '{"verdict":"retry","confidence":1,"reason":"retry","suggested_fix":"Add task to recovery probe","failure_type":"empty"}'
    mp.setattr(orch, '_call_pm', pm)
    mp.setattr(orch, '_judge', lambda message, _a, output, *_: evaluate_harness(message, a, output, '', invoke)[0])
    reply = orch.run_orchestrator('Add a task', config)
    assert len(calls) == len(rows(config)) == 1
    assert "couldn't confirm" in reply.lower()


def test_timeout_after_committed_write_never_replays(setup):
    config, mp = setup
    a = action(setup)
    calls = []
    async def pm(*_):
        calls.append(1)
        store.todo_add('recovery probe', None, config.user_id, config.data_dir)
        raise TimeoutError('SECRET')
    mp.setattr(orch, '_call_pm', pm)
    mp.setattr(orch, '_judge', lambda message, _a, output, *_: evaluate_harness(message, a, output, '', lambda *_: '')[0])
    reply = orch.run_orchestrator('Add a task', config)
    assert len(calls) == len(rows(config)) == 1
    assert "couldn't confirm" in reply.lower() and 'SECRET' not in reply


def test_read_retry_exhaustion_discloses_stale_cache(setup):
    config, mp = setup
    a = action(setup, False)
    memory.get_working_memory(config.user_id, config.session_id).cache_pm('todos', 'Old task snapshot')
    calls = []
    async def pm(*_):
        calls.append(1)
        raise TimeoutError('SECRET')
    mp.setattr(orch, '_call_pm', pm)
    mp.setattr(orch, '_judge', lambda message, _a, output, *_: evaluate_harness(message, a, output, '', lambda *_: '')[0])
    reply = orch.run_orchestrator('List my todos', config)
    assert len(calls) == 3
    assert 'may not reflect the latest changes' in reply
    assert 'Old task snapshot' in reply and 'SECRET' not in reply


def test_retry_suggestion_cannot_switch_read_to_write(setup):
    config, mp = setup
    action(setup, False)
    calls = []
    async def pm(prompt, *_):
        calls.append(prompt)
        if prompt.startswith('Add'):
            store.todo_add('unwanted', None, config.user_id, config.data_dir)
        return 'unhelpful output'
    mp.setattr(orch, '_call_pm', pm)
    mp.setattr(orch, '_judge', lambda *_: HarnessVerdict('retry', 1, 'fixture', 'Add task to unwanted', 'irrelevant'))
    orch.run_orchestrator('List my todos', config)
    assert len(calls) == 1
    assert not rows(config)


def test_corrupt_store_is_not_empty_and_cannot_be_overwritten(tmp_path):
    path = store._todos_path('owner', str(tmp_path))
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{broken')
    with pytest.raises(Exception):
        store.todo_add('new task', None, 'owner', str(tmp_path))
    assert p.read_text() == '{broken'


def test_failed_replacement_preserves_previous_file(tmp_path):
    from pathlib import Path
    store.todo_add('original', None, 'owner', str(tmp_path))
    p = Path(store._todos_path('owner', str(tmp_path)))
    before = p.read_bytes()
    with patch.object(store.os, 'replace', side_effect=OSError('disk fault')):
        with pytest.raises(OSError):
            store.todo_add('new task', None, 'owner', str(tmp_path))
    assert p.read_bytes() == before
    assert list(p.parent.glob('*.tmp')) == []


def test_repeated_completion_keeps_one_task(tmp_path):
    store.todo_add('original', None, 'owner', str(tmp_path))
    task = store.load_todos('owner', str(tmp_path)).items[0]
    store.todo_complete(task.id, 'owner', str(tmp_path))
    store.todo_complete(task.id, 'owner', str(tmp_path))
    data = store.load_todos('owner', str(tmp_path))
    assert len(data.items) == 1 and data.items[0].done


def test_empty_response_after_write_falls_back_and_invalidates_cache(setup):
    config, mp = setup
    action(setup)
    wm = memory.get_working_memory(config.user_id, config.session_id)
    wm.cache_pm('todos', 'obsolete list')
    calls = []
    async def pm(*_):
        calls.append(1)
        return store.todo_add('saved', None, config.user_id, config.data_dir)
    mp.setattr(orch, '_call_pm', pm)
    mp.setattr(orch, '_humanize', lambda *_: '  ')
    reply = orch.run_orchestrator('Add a task', config)
    assert len(calls) == len(rows(config)) == 1
    assert "couldn't confirm" in reply.lower()
    assert wm.get_cached_pm('todos') is None


def test_read_recovers_once_without_mutating(setup):
    config, mp = setup
    a = action(setup, False)
    calls = []
    async def pm(*_):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError('fault')
        return 'Todo list: (empty)'
    def invoke(*_):
        return '{"verdict":"pass","confidence":1,"reason":"fixture","suggested_fix":"","failure_type":"null"}'
    mp.setattr(orch, '_call_pm', pm)
    mp.setattr(orch, '_judge', lambda message, _a, output, *_: evaluate_harness(message, a, output, '', invoke)[0])
    reply = orch.run_orchestrator('List my todos', config)
    assert len(calls) == 2 and not rows(config)
    assert reply == 'Todo list: (empty)'


def test_failed_flush_preserves_old_bytes(tmp_path):
    from pathlib import Path
    store.todo_add('original', None, 'owner', str(tmp_path))
    path = Path(store._todos_path('owner', str(tmp_path)))
    before = path.read_bytes()
    with patch.object(store.os, 'fsync', side_effect=OSError('flush failed')):
        with pytest.raises(OSError):
            store.todo_add('new task', None, 'owner', str(tmp_path))
    assert path.read_bytes() == before
    assert list(path.parent.glob('*.tmp')) == []


@pytest.mark.parametrize('prompt', ['List my todos and add task to surprise',
                                   'List my todos; delete task a0000001',
                                   'Show my schedule', 'yes', 'Complete the second one'])
def test_retry_rejects_compound_wrong_resource_and_dialogue(prompt):
    from assistant.orchestrator.translator import is_safe_read_retry
    a = StructuredAction('show_todos', None, [], 1, prompt, False)
    assert not is_safe_read_retry(a)
