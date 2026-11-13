"""
Blind eval: un-coached messages scored by what ends up stored.

    python -m evals.blind --path pm             offline typed workflow, no model
    python -m evals.blind --path orchestrator   the web-chat path, needs model credentials

The set (tests/fixtures/blind_eval_set.json) is ten personas typing the way
people text, 85 turns in all. Each turn says what should be in storage
afterwards. A turn is judged from a snapshot of the stores before and after it,
never from how confident the reply sounds, so the same set can score any path
that writes to Kairo's stores.

Modules: spec (the set's format), snapshot (reading the stores), scoring (the
judgement), replies (the few text checks), adapters (the paths), runner (a run
and its report).
"""
