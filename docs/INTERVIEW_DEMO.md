# Kairo interview demo — 8 minutes, local and repeatable

## Preparation

From the repository's backend directory, activate your existing Python environment, then run:

```bash
python -m assistant.orchestrator.interview_demo --output ../artifacts/interview-demo-01.json
```

Use a new output filename for each run. Existing evidence is never overwritten. The demo needs no API key. It uses disposable synthetic stores and stops if a deterministic scenario unexpectedly falls back to an LLM. Keep the generated JSON open beside the monitoring dashboard.

## Walkthrough

**0:00–1:00 — Problem.** “An assistant can mutate the wrong task, repeat a write after a timeout, or claim success without evidence. I test state changes separately from the wording of its response.”

**1:00–2:00 — Successful workflow.** Show the captured add/list/complete turns in `workflow_capture`. Each has an independent before/after state read. Explain that the verification examines persisted state, not just a friendly success sentence. This is the real deterministic PM workflow, not final LLM humanization.

**2:00–3:00 — Ambiguous target.** Show `target_resolution`: two interview tasks, no mutation after the ambiguous request, followed by only the selected technical task becoming complete. Explain the choice to clarify instead of guessing.

**3:00–4:30 — Recovery after uncertain write.** Show `recovery`: a real local write succeeds, then the controlled PM call raises a timeout. The production recovery loop discloses uncertainty and does not repeat the write. Verify `pm_calls=1` and `persisted_tasks=1`. Be explicit: routing/translation and the fault-producing PM/judge behavior are fixtures; this is controlled fault injection, not an actual model or provider outage. Manual user resubmission and concurrency are separate problems, not solved by this test.

**4:30–6:00 — Evaluation.** Show the frozen v6 fresh result: 11/12 agreement, 5/6 failure detection, and 5/5 good-answer acceptance. Explain task completion versus response quality. An honest failure can pass response quality. Show the false pass instead of hiding it. V7's measured status must be checked in its result report; offline tests alone do not establish improvement.

**6:00–7:00 — Monitoring.** Open `Kairo_Monitoring_Dashboard/index.html` from Desktop in your browser. Show raw model versus final scores, label coverage, errors, semantic abstention, latency and rule overrides. It is a snapshot regenerated from saved reports, not live production monitoring. Runtime health is unknown without logs.

**7:00–8:00 — Decisions and limits.** Explain why deterministic rules handle supported date arithmetic while the judge handles semantic claims. Show versioned prompts and preserved human labels. Note that the sets are synthetic and guided; the sample size is too small for production accuracy claims. Azure hosting and live telemetry deployment remain your learning exercise.

## Questions to rehearse

- Why not use one overall score? It hides whether failure came from execution or misleading reporting.
- Why retain raw model scores? Rules can improve the final decision while masking model errors.
- Why not retry every failure? A timed-out write may already have committed.
- Why human calibration? Labels expose policy disagreements; an apparent model error can reflect an ambiguous rubric.
- What does 11/12 establish? Agreement on this frozen synthetic sample, not general production reliability.
- What remains? Focused live verification, broader operational experience, and user-led Azure deployment.

The generated demo artifact is the evidence; this script is a presentation guide. Do not describe fixture-backed portions as live LLM behavior.
