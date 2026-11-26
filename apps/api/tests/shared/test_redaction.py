from __future__ import annotations

import json
import logging

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from kairo.core.observability.agent_trace import log_react_trace
from kairo.utils.redaction import redact, redact_text


@pytest.mark.parametrize("text, expected", [
    ("email sam.lee+work@example.co.uk about lunch", "email [email] about lunch"),
    ("call 416-555-0199 at noon", "call [phone] at noon"),
    ("call (416) 555-0199 at noon", "call [phone] at noon"),
    ("call +1 416 555 0199 at noon", "call [phone] at noon"),
    ("join https://meet.example.com/abc?token=xyz at 3", "join [url] at 3"),
    ("card 4111 1111 1111 1111 on file", "card [card-number] on file"),
    ("number is 123-45-6789 ok", "number is [id-number] ok"),
    ("number is 123 456 789 ok", "number is [id-number] ok"),
    ("it is X1234567 ok", "it is [id-number] ok"),
    ("parcel to M5V 2T6 today", "parcel to [postal-code] today"),
    ("ref 123456789 ok", "ref [number] ok"),
])
def test_identifiers_are_replaced_by_labels(text, expected):
    assert redact_text(text) == expected


@pytest.mark.parametrize("text", [
    "add golf on 2026-11-26 at 10:00",
    "move it from 2026-11-26 09:30 to 2026-11-27 14:00",
    "approve 82700625",
    "delete todo a1b2c3d4",
    "3 tasks due, 2 done",
    "",
])
def test_dates_times_and_item_ids_stay_readable(text):
    assert redact_text(text) == text


def test_a_line_on_a_private_topic_is_withheld_whole():
    out = redact_text("remember my therapist is Dr. Okafor")
    assert out == "[withheld: sensitive, 35 chars]"


def test_only_the_private_lines_of_a_listing_are_withheld():
    listing = "09:00 standup\n11:00 doctor appointment\n15:00 golf"
    assert redact_text(listing).split("\n") == [
        "09:00 standup", "[withheld: sensitive, 24 chars]", "15:00 golf",
    ]


def test_structures_keep_their_keys_and_non_text_values():
    args = {"title": "Lunch", "attendees": ["sam@example.com"], "duration": 60, "notes": None}
    assert redact(args) == {
        "title": "Lunch", "attendees": ["[email]"], "duration": 60, "notes": None,
    }


def test_json_text_is_redacted_value_by_value():
    raw = json.dumps({"events": [{"title": "Standup", "note": "bring diagnosis letter"}]})
    assert json.loads(redact_text(raw)) == {
        "events": [{"title": "Standup", "note": "[withheld: sensitive, 22 chars]"}],
    }


def test_redaction_is_stable_when_applied_twice():
    once = redact_text("mail sam@example.com\nmy bank login\nplain line")
    assert redact_text(once) == once


def test_trace_log_never_carries_the_raw_values(caplog, monkeypatch):
    monkeypatch.setenv("AGENT_TRACE", "1")
    messages = [
        HumanMessage(content="invite sam@example.com and call 416-555-0199"),
        AIMessage(content="", tool_calls=[{
            "name": "add_event", "id": "call-1",
            "args": {"title": "Sync", "attendee": "sam@example.com", "date": "2026-11-26"},
        }]),
        ToolMessage(content="Added Sync. My passport number is X1234567.",
                    name="add_event", tool_call_id="call-1"),
        AIMessage(content="Done, I told sam@example.com."),
    ]
    with caplog.at_level(logging.INFO, logger="kairo.agent"):
        log_react_trace("personal_manager", messages, session_id="thread-1")

    logged = "\n".join(record.getMessage() for record in caplog.records)
    for secret in ("sam@example.com", "416-555-0199", "X1234567"):
        assert secret not in logged
    # What makes the log worth keeping is still there.
    assert "tool_call add_event" in logged
    assert '"date": "2026-11-26"' in logged
    assert "session=thread-1" in logged
