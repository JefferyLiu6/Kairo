import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from assistant.orchestrator.offline_eval import checks, run_checks


def success(*_args, **_kwargs):
    return SimpleNamespace(returncode=0, stdout='check output', stderr='')


def test_offline_commands_have_no_live_model_flags(tmp_path):
    commands = checks(tmp_path)
    assert len(commands) == 4
    assert all('--live' not in command and '--use-model' not in command for _, command in commands)


def test_failure_is_preserved_and_later_checks_still_run(tmp_path):
    calls = []
    def execute(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1 if len(calls) == 1 else 0, stdout='result', stderr='')
    result = run_checks(tmp_path / 'run', execute=execute)
    assert len(calls) == 4
    assert result['status'] == 'failed'
    assert result['stages'][0]['returncode'] == 1
    assert result['stages'][-1]['status'] == 'passed'
    assert json.loads((tmp_path / 'run/report.json').read_text()) == result


def test_existing_report_is_not_overwritten(tmp_path):
    destination = tmp_path / 'run'
    run_checks(destination, execute=success)
    original = (destination / 'report.json').read_bytes()
    with pytest.raises(FileExistsError):
        run_checks(destination, execute=lambda *_a, **_k: pytest.fail('Should not execute'))
    assert (destination / 'report.json').read_bytes() == original


def test_timeout_retains_partial_output_and_continues(tmp_path):
    def execute(*_args, **_kwargs):
        raise subprocess.TimeoutExpired('test', 1, output=b'partial output', stderr=b'partial error')
    result = run_checks(tmp_path / 'run', execute=execute)
    assert result['status'] == 'failed'
    assert len(result['stages']) == 4
    assert all(s['status'] == 'timed_out' for s in result['stages'])
    assert (tmp_path / 'run/backend_tests.stdout.txt').read_text() == 'partial output'


def test_interruption_records_incomplete_run(tmp_path):
    def execute(*_args, **_kwargs):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        run_checks(tmp_path / 'run', execute=execute)
    report = json.loads((tmp_path / 'run/report.json').read_text())
    assert report['status'] == 'interrupted'
    assert len(report['stages']) == 1


@pytest.mark.parametrize('timeout', [0, -1, float('nan'), float('inf')])
def test_invalid_timeout_cannot_start_checks(tmp_path, timeout):
    with pytest.raises(ValueError):
        run_checks(tmp_path / 'run', timeout, execute=success)
    assert not (tmp_path / 'run').exists()


def test_output_paths_with_spaces_are_separate_arguments(tmp_path):
    destination = tmp_path / 'my report'
    paths = [command[command.index('--output') + 1] for _, command in checks(destination) if '--output' in command]
    assert all(Path(p).parent == destination for p in paths)
