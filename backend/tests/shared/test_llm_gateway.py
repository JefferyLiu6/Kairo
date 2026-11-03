from __future__ import annotations

import json

import pytest

from kairo.core.llm import Contract, ContractError, call_contract, parse_contract, parse_json_object


class Echo(Contract):
    word: str
    count: int


@pytest.mark.parametrize("raw", [
    '{"word": "hi", "count": 2}',
    '```json\n{"word": "hi", "count": 2}\n```',
    'Sure! Here it is: {"word": "hi", "count": 2} Hope that helps.',
    # The old greedy regex spanned both objects and failed to parse.
    '{"word": "hi", "count": 2} and also {"other": true}',
    'not json {oops} then {"word": "hi", "count": 2}',
])
def test_parse_json_object_finds_the_first_valid_object(raw):
    assert parse_json_object(raw) == {"word": "hi", "count": 2}


@pytest.mark.parametrize("raw", ["", "no braces here", "[1, 2, 3]", "{broken"])
def test_parse_json_object_rejects_replies_without_an_object(raw):
    with pytest.raises(ContractError):
        parse_json_object(raw)


def test_parse_contract_names_the_bad_field():
    with pytest.raises(ContractError, match="count"):
        parse_contract('{"word": "hi", "count": "many"}', Echo)


def test_parse_contract_rejects_unknown_fields():
    with pytest.raises(ContractError, match="mood: Extra inputs"):
        parse_contract('{"word": "hi", "count": 2, "mood": "happy"}', Echo)


def test_call_contract_sends_json_and_repairs_once_with_the_error():
    calls = []
    replies = iter(['{"word": "hi"}', '{"word": "hi", "count": 3}'])

    def invoke(system, user):
        calls.append(user)
        return next(replies)

    result = call_contract(invoke, system="sys", data=Echo(word="in", count=1), output=Echo)

    assert result == Echo(word="hi", count=3)
    assert json.loads(calls[0]) == {"word": "in", "count": 1}
    assert "count" in calls[1] and "not valid" in calls[1]


def test_call_contract_gives_up_after_the_repair():
    calls = []

    def invoke(system, user):
        calls.append(user)
        return "still not json"

    with pytest.raises(ContractError):
        call_contract(invoke, system="sys", data=Echo(word="in", count=1), output=Echo)
    assert len(calls) == 2
