"""Problem-specific judge metrics with inspectable denominators, not a composite quality score."""
METRICS_VERSION = 'judge-metrics-v2'


def reliability_metrics(rows):
    fail = [r for r in rows if r['expected'] == 'fail']
    good = [r for r in rows if r['expected'] == 'pass']
    uncertain = [r for r in rows if r['expected'] == 'abstain']
    binary = fail + good

    def predicted(group, label):
        return sum(r['status'] == 'ok' and r['label'] == label for r in group)

    def metric(n, d, question, direction, caveat):
        return dict(value=n/d if d else None, numerator=n, denominator=d,
                    question=question, preferred_direction=direction, limitation=caveat)

    results = {
        'failure_detection_recall': metric(predicted(fail, 'fail'), len(fail),
            'Of reference-bad answers, how many did the judge correctly reject?', 'higher',
            'Errors and abstentions count as misses. Depends on reference-label quality.'),
        'good_answer_acceptance': metric(predicted(good, 'pass'), len(good),
            'Of reference-good answers, how many did the judge accept?', 'higher',
            'Stops an always-fail judge looking useful. Errors and abstentions are misses.'),
        'false_rejection_rate': metric(predicted(good, 'fail'), len(good),
            'How often does the judge incorrectly reject a good answer?', 'lower',
            'Does not include unavailable grades; inspect acceptance and coverage too.'),
        'uncertainty_recall': metric(predicted(uncertain, 'abstain'), len(uncertain),
            'Does the judge recognize genuinely unassessable evaluation records?', 'higher',
            'Provider and parser failures are not correct uncertainty decisions.'),
        'unnecessary_abstention_rate': metric(predicted(binary, 'abstain'), len(binary),
            'How often does the judge abstain on examples with a pass/fail reference?', 'lower',
            'Reference labels can be disputed; errors are reported separately.'),
        'usable_verdict_rate': metric(sum(r['status'] == 'ok' for r in rows), len(rows),
            'Does the judge return a schema-valid verdict, including deliberate abstention?', 'higher',
            'A valid verdict can still be wrong. This is operational reliability.'),
    }
    recalls = [results[k]['value'] for k in ('failure_detection_recall', 'good_answer_acceptance')]
    results['balanced_decision_accuracy'] = dict(
        value=sum(recalls)/2 if all(v is not None for v in recalls) else None,
        numerator=None, denominator=None,
        question='Does the judge recognize both good and bad answers despite class imbalance?',
        preferred_direction='higher',
        limitation='Mean of failure recall and good-answer acceptance; requires both classes. '
                   'Abstentions/errors count as misses. Excludes reference-abstain cases; not a safety gate.')
    return results


def repeat_metrics(rows):
    groups = {}
    for row in rows:
        groups.setdefault(row['id'], []).append(row)
    repeated = [g for g in groups.values() if len(g) > 1]
    valid = [g for g in repeated if all(r['status'] == 'ok' and r['label'] != 'abstain' for r in g)]
    outcome_changes = sum(len({(r['status'], r['label']) for r in g}) > 1 for g in repeated)
    score_changes = sum(len({tuple(r['scores'][k] for k in ('groundedness', 'relevance', 'completeness'))
                            for r in g}) > 1 for g in valid)
    return dict(repeated_cases=len(repeated), outcome_disagreement_cases=outcome_changes,
                outcome_disagreement_rate=outcome_changes/len(repeated) if repeated else None,
                fully_scored_repeated_cases=len(valid), score_disagreement_cases=score_changes,
                score_disagreement_rate=score_changes/len(valid) if valid else None,
                interpretation='Case-level stability, not accuracy. Score drift excludes cases with missing grades; '
                               'inspect fully_scored_repeated_cases. Repeats are not new independent examples.')
