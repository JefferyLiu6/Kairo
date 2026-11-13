"""
Judging one turn of the blind eval from the stores before and after it.

Outcomes, checked in this order:

    right        what is stored (or shown, or queued for approval) is what was asked
    wrong_save   something was stored that should not have been, or stored wrongly
    trapped      the same pending question was asked again instead of handling the message
    false_claim  nothing was stored but the reply says it was, or describes something not stored
    blocked      an earlier turn failed, so this one could not be attempted fairly
    unscored     open-ended advice or conversation; only the guardrails above apply
    miss         nothing wrong was stored and nothing was claimed, but the request was not done

`schedule`, `todos`, `habits` and `journal` are strict: any change the turn did
not expect is a wrong save. `memory` is soft: an expected memory must be there,
but extra facts learned along the way are not failures unless the scenario
forbids them with `never`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Sequence

from evals.blind.replies import asserts, claims_success, names
from evals.blind.snapshot import STORES, Item, Snapshot
from evals.blind.spec import ApprovalClause, Expect, ItemClause, Pattern, Turn

STRICT_STORES = ("schedule", "todos", "habits", "journal")
_OPERATORS = {"has", "lacks", "any", "set", "prefix", "between"}
_CLAIM_CHECKED = ("write", "approval", "refuse")   # kinds where "done" with nothing stored is a false claim
_INVENTION_CHECKED = ("read", "open")              # kinds whose reply describes state

# The quality bar agreed in docs/ideas/kairo-2.0-idea-refine.md (decision 8).
GATE = {"right_rate_min": 0.70, "wrong_but_reported_done_rate_max": 0.02, "trapped_max": 0}


class Outcome(str, Enum):
    RIGHT = "right"
    WRONG_SAVE = "wrong_save"
    TRAPPED = "trapped"
    FALSE_CLAIM = "false_claim"
    BLOCKED = "blocked"
    UNSCORED = "unscored"
    MISS = "miss"


@dataclass(frozen=True)
class TurnScore:
    outcome: Outcome
    reason: str = ""


# ── Patterns ──────────────────────────────────────────────────────────────────

def _get(value: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(value, Mapping):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit():
            value = value[int(part)] if int(part) < len(value) else None
        else:
            return None
    return value


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _same(actual: Any, expected: Any) -> bool:
    if expected is None:
        return _empty(actual)
    if isinstance(actual, str) and isinstance(expected, str):
        return actual.strip() == expected.strip()
    return actual == expected


def _contains(actual: Any, needle: Any) -> bool:
    if isinstance(actual, (list, tuple)):
        return any(str(needle).casefold() == str(element).casefold() for element in actual)
    return not _empty(actual) and str(needle).casefold() in str(actual).casefold()


def _field_problem(actual: Any, expected: Any) -> Optional[str]:
    """None if `actual` fits `expected`, otherwise what was expected, in words."""
    if not isinstance(expected, dict):
        return None if _same(actual, expected) else f"expected {expected!r}"
    unknown = set(expected) - _OPERATORS
    if unknown or not expected:
        raise ValueError(f"unknown pattern operator(s) {sorted(unknown)}; use one of {sorted(_OPERATORS)}")
    if "any" in expected and not any(_same(actual, option) for option in expected["any"]):
        return f"expected one of {expected['any']!r}"
    if "has" in expected and not all(_contains(actual, needle) for needle in expected["has"]):
        return f"expected it to contain {expected['has']!r}"
    if "lacks" in expected and any(_contains(actual, needle) for needle in expected["lacks"]):
        return f"expected it not to contain any of {expected['lacks']!r}"
    if "set" in expected and (not isinstance(actual, list) or set(actual) != set(expected["set"])):
        return f"expected exactly {sorted(expected['set'])!r}"
    if "prefix" in expected and not str(actual or "").startswith(expected["prefix"]):
        return f"expected it to start with {expected['prefix']!r}"
    if "between" in expected:
        low, high = expected["between"]
        if not isinstance(actual, str) or not low <= actual <= high:
            return f"expected between {low!r} and {high!r}"
    return None


def mismatched_fields(item: Mapping[str, Any], pattern: Pattern) -> dict[str, str]:
    """Each field of `item` that does not fit `pattern`, mapped to 'is X, expected Y'."""
    problems = {}
    for path, expected in pattern.items():
        actual = _get(item, path)
        problem = _field_problem(actual, expected)
        if problem:
            problems[path] = f"is {actual!r}, {problem}"
    return problems


def mismatches(item: Mapping[str, Any], pattern: Pattern) -> list[str]:
    """The same, as sentences: 'field is X, expected Y'."""
    return [f"{path} {problem}" for path, problem in mismatched_fields(item, pattern).items()]


def matches(item: Mapping[str, Any], pattern: Pattern) -> bool:
    return not mismatches(item, pattern)


def _fits_any(item: Mapping[str, Any], patterns: Sequence[Pattern]) -> bool:
    return any(matches(item, pattern) for pattern in patterns)


def _label(item: Mapping[str, Any]) -> str:
    return str(next((item[key] for key in ("title", "name", "body", "text") if item.get(key)), ""))


def _brief(item: Mapping[str, Any]) -> str:
    shown = {key: value for key, value in item.items() if key != "id" and not _empty(value)}
    return ", ".join(f"{key}={value!r}" for key, value in shown.items())


# ── What changed ──────────────────────────────────────────────────────────────

@dataclass
class Delta:
    added: dict[str, list[Item]] = field(default_factory=dict)
    removed: dict[str, list[Item]] = field(default_factory=dict)
    changed: dict[str, list[tuple[Item, Item]]] = field(default_factory=dict)  # (before, after)

    def strict_changes(self) -> bool:
        return any(self.added.get(s) or self.removed.get(s) or self.changed.get(s) for s in STRICT_STORES)


def diff(before: Snapshot, after: Snapshot) -> Delta:
    delta = Delta()
    for store in STORES:
        old, new = before.stores.get(store, {}), after.stores.get(store, {})
        delta.added[store] = [new[key] for key in new if key not in old]
        delta.removed[store] = [old[key] for key in old if key not in new]
        delta.changed[store] = [(old[key], new[key]) for key in new if key in old and old[key] != new[key]]
    return delta


def assign(accepts: list[list[bool]]) -> Optional[list[int]]:
    """Give each clause a different candidate it accepts (accepts[clause][candidate]), or None."""
    def place(clause: int, taken: frozenset[int]) -> Optional[list[int]]:
        if clause == len(accepts):
            return []
        for candidate, ok in enumerate(accepts[clause]):
            if ok and candidate not in taken:
                rest = place(clause + 1, taken | {candidate})
                if rest is not None:
                    return [candidate] + rest
        return None
    return place(0, frozenset())


def _pair_up(what: str, store: str, clauses: list, candidates: list, accepts, describe, explain) -> list[str]:
    """Problems matching expected `clauses` to actual `candidates`; unexpected candidates fail strict stores."""
    table = [[accepts(clause, candidate) for candidate in candidates] for clause in clauses]
    assignment = assign(table)
    if assignment is None:
        problems = []
        for clause, row in zip(clauses, table):
            if not any(row):
                near = "; ".join(explain(clause, candidate) for candidate in candidates) or "nothing was " + what
                problems.append(f"{store}: expected {what} item not found ({near})")
        return problems or [f"{store}: the {what} items do not cover everything expected"]
    if store in STRICT_STORES:
        used = set(assignment)
        return [f"{store}: unexpected {what} item ({describe(candidate)})"
                for index, candidate in enumerate(candidates) if index not in used]
    return []


def _state_problems(expect: Expect, delta: Delta) -> list[str]:
    problems: list[str] = []
    for store in STORES:
        problems += _pair_up(
            "added", store, [c for c in expect.adds if c.store == store], delta.added[store],
            accepts=lambda clause, item: _fits_any(item, clause.patterns()),
            describe=_brief,
            explain=lambda clause, item: f"{_label(item)!r}: " + ", ".join(mismatches(item, clause.patterns()[0])),
        )
        problems += _pair_up(
            "changed", store, [c for c in expect.changes if c.store == store], delta.changed[store],
            accepts=lambda clause, pair: matches(pair[0], clause.where) and matches(pair[1], clause.to),
            describe=lambda pair: f"{_brief(pair[0])} -> {_brief(pair[1])}",
            explain=lambda clause, pair: f"{_label(pair[1])!r}: " + ", ".join(
                mismatches(pair[0], clause.where) + mismatches(pair[1], clause.to)),
        )
        problems += _pair_up(
            "removed", store, [c for c in expect.removes if c.store == store], delta.removed[store],
            accepts=lambda clause, item: matches(item, clause.where),
            describe=_brief,
            explain=lambda clause, item: f"{_label(item)!r}: " + ", ".join(mismatches(item, clause.where)),
        )
    return problems


# ── One expectation against one turn ──────────────────────────────────────────

def _approval_fits(approval: Mapping[str, Any], clause: ApprovalClause) -> bool:
    return approval.get("action") in clause.actions() and matches(approval.get("payload") or {}, clause.payload)


def _new_question(before: Snapshot, after: Snapshot) -> bool:
    return after.question is not None and (before.question is None or after.question.topic != before.question.topic)


def _shown(expect: Expect, snapshot: Snapshot) -> list[Item]:
    return [item for clause in expect.shows for item in snapshot.items(clause.store)
            if _fits_any(item, clause.patterns())]


def _applies(expect: Expect, before: Snapshot) -> bool:
    for given in expect.given:
        clause = given.item()
        if clause is not None and not any(_fits_any(item, clause.patterns()) for item in before.items(clause.store)):
            return False
        if given.approval is not None and not any(
                _approval_fits(approval, given.approval) for approval in before.approvals.values()):
            return False
    return not (expect.kind == "read" and expect.shows and not _shown(expect, before))


def _problems(expect: Expect, before: Snapshot, after: Snapshot, delta: Delta, reply: str) -> list[str]:
    """Why this expectation is not met; empty when it is."""
    problems = _state_problems(expect, delta)
    new_approvals = [approval for key, approval in after.approvals.items() if key not in before.approvals]

    if expect.kind == "approval":
        fitting = [approval for approval in new_approvals if _approval_fits(approval, expect.approval)]
        if len(fitting) != 1 or len(new_approvals) != 1:
            seen = "; ".join(
                f"{approval.get('action')}: " + (", ".join(mismatches(approval.get("payload") or {},
                                                                    expect.approval.payload)) or "wrong action")
                for approval in new_approvals) or "no approval was requested"
            problems.append(f"expected one new approval for {'/'.join(expect.approval.actions())} ({seen})")
    if expect.kind == "ask" and not _new_question(before, after):
        problems.append("expected a new question to the user")
    if expect.kind == "refuse":
        if new_approvals:
            problems.append("an approval was requested for something that should be declined")
        if _new_question(before, after):
            problems.append("a question was asked for something that should be declined")
        if claims_success(reply):
            problems.append("the reply says it was done")
    if expect.kind == "read":
        missing = [_label(item) for item in _shown(expect, after) if not names(reply, _label(item))]
        if missing:
            problems.append(f"the reply does not name {missing!r}")
    if expect.approvals_pending_max is not None and len(after.approvals) > expect.approvals_pending_max:
        problems.append(f"{len(after.approvals)} approval(s) still pending, expected at most "
                        f"{expect.approvals_pending_max}")
    for wanted in expect.mentions:
        options = [wanted] if isinstance(wanted, str) else wanted
        if not any(option.casefold() in reply.casefold() for option in options):
            problems.append(f"the reply does not mention {' or '.join(options)!r}")
    return problems


# ── The verdict ───────────────────────────────────────────────────────────────

def _invented(reply: str, entities: Iterable[str], after: Snapshot, say: str) -> Optional[str]:
    """A scenario entity the reply describes although it is neither stored nor in the user's message."""
    known = after.text() + " " + say.casefold()
    return next((e for e in entities if e.casefold() not in known and asserts(reply, e)), None)


def _asked_again(before: Snapshot, after: Snapshot) -> bool:
    return (before.question is not None and after.question is not None
            and after.question.topic == before.question.topic and after.question.stamp != before.question.stamp)


def score_turn(
    turn: Turn,
    before: Snapshot,
    after: Snapshot,
    reply: str,
    *,
    entities: Iterable[str] = (),
    never: Iterable[ItemClause] = (),
) -> TurnScore:
    alternatives = turn.alternatives()
    applicable = [expect for expect in alternatives if _applies(expect, before)]
    delta = diff(before, after)
    findings = [(expect, _problems(expect, before, after, delta, reply)) for expect in applicable]
    met = next((expect for expect, problems in findings if not problems), None)
    judged = met or (applicable or alternatives)[0]
    # Explain a failure against the alternative that came closest.
    why = "; ".join(min((problems for _, problems in findings), key=len)) if findings else ""

    for clause in never:
        stored = next((item for item in after.items(clause.store) if _fits_any(item, clause.patterns())), None)
        if stored is not None:
            return TurnScore(Outcome.WRONG_SAVE, f"stored something this scenario says must never be stored: "
                                                 f"{clause.store} {_brief(stored)}")
    if met is None and delta.strict_changes():
        return TurnScore(Outcome.WRONG_SAVE, why or "; ".join(_state_problems(Expect(kind="noop"), delta)))

    invented = _invented(reply, entities, after, turn.say) if judged.kind in _INVENTION_CHECKED else None
    if met is not None and met.kind != "open" and invented is None:
        return TurnScore(Outcome.RIGHT)
    if _asked_again(before, after):
        return TurnScore(Outcome.TRAPPED, f"asked the pending question again ({after.question.topic})")
    if invented is not None:
        return TurnScore(Outcome.FALSE_CLAIM, f"the reply describes '{invented}', which is not stored")
    if met is None and judged.kind in _CLAIM_CHECKED and claims_success(reply):
        return TurnScore(Outcome.FALSE_CLAIM, f"the reply says it was done, but {why or 'nothing was stored'}")
    if not applicable:
        return TurnScore(Outcome.BLOCKED, "an earlier turn did not leave the state this turn needs")
    if met is not None:
        return TurnScore(Outcome.UNSCORED)

    instead = ""
    if judged.kind != "approval" and any(key not in before.approvals for key in after.approvals):
        instead = "asked for approval instead; "
    elif judged.kind != "ask" and _new_question(before, after):
        instead = "asked a question instead; "
    return TurnScore(Outcome.MISS, instead + why)


# ── Totals ────────────────────────────────────────────────────────────────────

def summarize(outcomes: Iterable[Outcome]) -> dict[str, Any]:
    outcomes = list(outcomes)
    count = {outcome: outcomes.count(outcome) for outcome in Outcome}
    turns = len(outcomes)
    scoreable = turns - count[Outcome.BLOCKED] - count[Outcome.UNSCORED]
    bad = count[Outcome.WRONG_SAVE] + count[Outcome.FALSE_CLAIM]
    return {
        "turns": turns,
        "scoreable": scoreable,
        "right": count[Outcome.RIGHT],
        "right_rate": round(count[Outcome.RIGHT] / scoreable, 4) if scoreable else None,
        "wrong_save": count[Outcome.WRONG_SAVE],
        "false_claim": count[Outcome.FALSE_CLAIM],
        "wrong_but_reported_done": bad,
        "wrong_but_reported_done_rate": round(bad / turns, 4) if turns else None,
        "trapped": count[Outcome.TRAPPED],
        "miss": count[Outcome.MISS],
        "blocked": count[Outcome.BLOCKED],
        "unscored": count[Outcome.UNSCORED],
    }


def gate(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Whether a summary clears the quality bar, and which limits it misses."""
    failed = []
    if summary["right_rate"] is None or summary["right_rate"] < GATE["right_rate_min"]:
        failed.append(f"right_rate {summary['right_rate']} is below {GATE['right_rate_min']}")
    if (summary["wrong_but_reported_done_rate"] or 0) > GATE["wrong_but_reported_done_rate_max"]:
        failed.append(f"wrong_but_reported_done_rate {summary['wrong_but_reported_done_rate']} is above "
                      f"{GATE['wrong_but_reported_done_rate_max']}")
    if summary["trapped"] > GATE["trapped_max"]:
        failed.append(f"trapped {summary['trapped']} is above {GATE['trapped_max']}")
    return {"passed": not failed, "failed": failed}
