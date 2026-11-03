from assistant.orchestrator.interview_demo import demonstrate


def test_demo_verifies_real_state_and_write_recovery():
    result = demonstrate()
    assert result['status'] == 'passed'
    assert result['api_calls'] == 0
    assert result['target_resolution']['state'] == [
        {'title': 'technical interview', 'done': True},
        {'title': 'behavioural interview', 'done': False},
    ]
    assert result['recovery']['pm_calls'] == result['recovery']['persisted_tasks'] == 1
