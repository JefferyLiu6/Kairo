"""
Model date test: can a model fill typed tool arguments from date and time phrasing?

    python -m evals.datefill                    configured model, both arms, 3 repeats
    python -m evals.datefill --model gpt-4o     another model for comparison

Kairo 2.0 replaces regex extraction with a model that fills a typed tool call.
That only works if the model gets dates and times right, so this checks that
one assumption in isolation: one message in, one tool call out, compared with
the expected arguments. Nothing is stored and no Kairo code path is involved.

Two arms show what the model needs to be told: `date_only` gives today's date,
`calendar` adds the weekday and the next fourteen days spelt out.

Modules: tools (the candidate tool contract and prompts), probe (cases,
judging, a run and its report).
"""
