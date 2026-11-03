"""State-based regressions for the reproduced task targeting failures."""
from unittest.mock import patch

import pytest

from assistant.personal_manager.agent import PMConfig, run_pm
from assistant.personal_manager.application.clarification import _load_pending
from assistant.personal_manager.persistence.store import TodoData, TodoItem, load_todos, save_todos


@pytest.fixture
def pm(tmp_path):
    config = PMConfig(provider='offline', model='unused', data_dir=str(tmp_path),
                      vault_dir=str(tmp_path), session_id='pm-target-thread', user_id='owner')
    save_todos(TodoData(items=[TodoItem(id='a0000001', title='technical interview'),
                              TodoItem(id='a0000002', title='behavioural interview'),
                              TodoItem(id='a0000003', title='portfolio review')]), 'owner', str(tmp_path))
    with patch('assistant.personal_manager.agent._run_pm_react', return_value='OFFLINE_FALLBACK'):
        yield config


def done(pm):
    return [i.id for i in load_todos(pm.user_id, pm.data_dir).items if i.done]


@pytest.mark.parametrize('selection', ['The technical interview', 'a0000001', 'the first one'])
def test_ambiguous_target_retains_choices(pm, selection):
    reply = run_pm('Mark my interview task done', pm)
    assert 'which one' in reply.lower()
    assert not done(pm)
    assert _load_pending(pm.session_id, pm.data_dir, user_id=pm.user_id)
    run_pm(selection, pm)
    assert done(pm) == ['a0000001']


def test_correction_replaces_pending_target(pm):
    run_pm('Complete the interview task', pm)
    run_pm('Actually, complete the portfolio review task instead', pm)
    assert done(pm) == ['a0000003']


def test_ordinal_uses_displayed_list(pm):
    run_pm('List my todos', pm)
    run_pm('Complete the second one', pm)
    assert done(pm) == ['a0000002']


@pytest.mark.parametrize('command', ['Add a task', 'Add task', 'Create a todo'])
def test_missing_title_does_not_create_task(pm, command):
    reply = run_pm(command, pm)
    assert len(load_todos(pm.user_id, pm.data_dir).items) == 3
    assert '?' in reply
    assert _load_pending(pm.session_id, pm.data_dir, user_id=pm.user_id)
    assert 'cancelled' in run_pm('Never mind, cancel that', pm).lower()
    run_pm('yes', pm)
    assert len(load_todos(pm.user_id, pm.data_dir).items) == 3


def test_cancel_discards_real_target_selection(pm):
    run_pm('Complete the interview task', pm)
    assert _load_pending(pm.session_id, pm.data_dir, user_id=pm.user_id)
    assert 'cancelled' in run_pm('Never mind, cancel that', pm).lower()
    run_pm('a0000001', pm)
    assert not done(pm)


@pytest.mark.parametrize('choice', ['99', 'yes', 'interview'])
def test_unclear_selection_never_mutates(pm, choice):
    run_pm('Complete the interview task', pm)
    reply = run_pm(choice, pm)
    assert 'which one' in reply.lower()
    assert not done(pm)


@pytest.mark.parametrize('change', ['rename', 'remove', 'complete'])
def test_changed_candidate_is_rejected(pm, change):
    run_pm('List my todos', pm)
    data = load_todos(pm.user_id, pm.data_dir)
    if change == 'rename':
        data.items[1].title = 'different task'
    elif change == 'remove':
        data.items.pop(1)
    else:
        data.items[1].done = True
    save_todos(data, pm.user_id, pm.data_dir)
    before = data.model_dump()
    assert 'changed or was removed' in run_pm('Complete the second one', pm)
    assert load_todos(pm.user_id, pm.data_dir).model_dump() == before


def test_list_reordering_keeps_displayed_id(pm):
    run_pm('List my todos', pm)
    data = load_todos(pm.user_id, pm.data_dir)
    data.items.reverse()
    save_todos(data, pm.user_id, pm.data_dir)
    run_pm('Complete the first one', pm)
    assert done(pm) == ['a0000001']


@pytest.mark.parametrize('scope', ['session_id', 'user_id'])
def test_selection_isolated_by_thread_and_owner(pm, scope):
    from dataclasses import replace
    run_pm('Complete the interview task', pm)
    other = replace(pm, **{scope: 'pm-other'})
    run_pm('a0000001', other)
    assert not done(pm)
    run_pm('a0000001', pm)
    assert done(pm) == ['a0000001']


def test_expired_selection_cannot_execute(pm):
    from assistant.personal_manager.persistence.working_memory import _conn
    run_pm('Complete the interview task', pm)
    with _conn(pm.session_id, pm.data_dir, user_id=pm.user_id) as conn:
        conn.execute("UPDATE working_memory SET expires_at = '2000-01-01T00:00:00+00:00'")
    run_pm('a0000001', pm)
    assert not done(pm)


def test_removal_selection_still_requires_approval(pm):
    run_pm('Remove the interview task', pm)
    reply = run_pm('a0000001', pm)
    assert 'approv' in reply.lower()
    assert len(load_todos(pm.user_id, pm.data_dir).items) == 3


def test_bare_list_choice_does_not_imply_action(pm):
    run_pm('List my todos', pm)
    run_pm('the first one', pm)
    assert not done(pm)


@pytest.mark.parametrize('title', ['mark as done', 'review instead', "review Alice's task"])
def test_quoted_titles_preserved(pm, title):
    data = load_todos(pm.user_id, pm.data_dir)
    data.items[0].title = title
    save_todos(data, pm.user_id, pm.data_dir)
    run_pm(f'Mark the "{title}" todo as done', pm)
    assert done(pm) == ['a0000001']


def test_title_followup_creates_only_requested_task(pm):
    run_pm('Add a task', pm)
    run_pm('Review my portfolio', pm)
    items = load_todos(pm.user_id, pm.data_dir).items
    assert len(items) == 4
    assert items[-1].title == 'Review my portfolio'


def test_missing_removal_target_creates_no_approval(pm):
    from assistant.personal_manager.persistence.control_store import list_approval_requests
    reply = run_pm('Remove the nonexistent task', pm)
    assert "Couldn't find" in reply
    assert not list_approval_requests(pm.user_id, pm.data_dir)
    assert len(load_todos(pm.user_id, pm.data_dir).items) == 3
