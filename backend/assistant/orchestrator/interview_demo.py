"""Repeatable offline demonstration with real local state and explicit fault injection."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from .capture_eval import capture
from . import agent as orch, memory
from .translator import StructuredAction
from .harness import evaluate_harness
from assistant.personal_manager.agent import PMConfig, run_pm
from assistant.personal_manager.persistence import store


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def demonstrate() -> dict:
    # Fail closed if a scenario unexpectedly leaves the deterministic PM path.
    with patch('assistant.personal_manager.agent._run_pm_react', side_effect=RuntimeError('Demo attempted an LLM fallback')):
        captured = capture()
        final_state = json.loads(captured[-1]['evidence'])['after']
        require(len(final_state) == 1 and final_state[0]['done'], 'Create/list/complete state verification failed')
        with TemporaryDirectory(prefix='kairo-interview-') as directory:
            config = PMConfig(provider='offline', model='unused', data_dir=directory,
                              vault_dir=directory, session_id='demo-target', user_id='demo-target')
            store.save_todos(store.TodoData(items=[
                store.TodoItem(id='a0000001', title='technical interview'),
                store.TodoItem(id='a0000002', title='behavioural interview'),
            ]), config.user_id, directory)
            clarification = run_pm('Mark my interview task done', config)
            require(not any(x.done for x in store.load_todos(config.user_id, directory).items),
                    'Ambiguous request mutated state')
            selected = run_pm('The technical interview', config)
            items = store.load_todos(config.user_id, directory).items
            require([x.id for x in items if x.done] == ['a0000001'], 'Wrong target completed')
            targeting = {'request': 'Mark my interview task done', 'clarification': clarification,
                         'selection': 'The technical interview', 'response': selected,
                         'state': [{'title': x.title, 'done': x.done} for x in items]}
        with TemporaryDirectory(prefix='kairo-fault-demo-') as directory:
            config = orch.OrchestratorConfig(user_id='demo-recovery', session_id='demo-recovery',
                                            data_dir=directory, provider='offline', model='unused')
            action = StructuredAction('add_todo', None, [], 1, 'Add task to recovery probe', True)
            calls = []

            async def committed_then_timeout(*_):
                calls.append(1)
                store.todo_add('recovery probe', None, config.user_id, directory)
                raise TimeoutError('Injected timeout after local commit')

            # Isolate demo working memory; restore process state on exit.
            with patch.object(memory, '_sessions', {}), \
                 patch.object(orch, '_route', return_value=True), \
                 patch.object(orch, '_translate', return_value=action), \
                 patch.object(orch, '_call_pm', side_effect=committed_then_timeout), \
                 patch.object(orch, '_humanize', side_effect=RuntimeError('Unexpected humanizer call')), \
                 patch.object(orch, '_judge', side_effect=lambda message, act, output, *_: evaluate_harness(
                     message, act, output, '', lambda *_: '')[0]):
                response = orch.run_orchestrator('Add task to recovery probe', config)
            items = store.load_todos(config.user_id, directory).items
            require(len(calls) == len(items) == 1, 'Uncertain write replayed or lost')
            require("couldn't confirm" in response.lower(), 'Recovery did not disclose uncertainty')
            recovery = {'fault': 'Injected timeout after a real local task write',
                        'pm_calls': len(calls), 'persisted_tasks': len(items), 'response': response,
                        'scope': 'Production recovery loop; routing/translation, PM call and judge response are controlled fixtures, not live LLM behavior.'}
    return {'status': 'passed', 'mode': 'offline_interview_demo', 'api_calls': 0,
            'scope': 'Synthetic temporary local state; deterministic PM workflows plus explicit fault injection. Not live-model or cloud validation.',
            'workflow_capture': captured, 'target_resolution': targeting, 'recovery': recovery}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Choose a new output path to preserve previous evidence')
    result = demonstrate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print('PASS: create/list/complete verified through independent state reads.')
    print('PASS: ambiguous target clarified; only the selected task changed.')
    print('PASS: timeout after write disclosed; one PM call and one persisted task.')
    print(f'Offline demonstration saved: {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
