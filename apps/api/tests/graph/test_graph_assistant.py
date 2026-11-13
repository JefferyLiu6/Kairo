"""Conversation, planning in words and web search: the assistant agent, which is also where anything unplaced ends up."""
from __future__ import annotations

from kairo.domains import assistant as assistant_domain
from kairo.storage.store import ScheduleData, ScheduleEntry, TodoData, TodoItem, save_schedule, save_todos

from tests.graph.conftest import THREAD, USER, call, todos

TO_ASSISTANT = call("hand_off", domain="assistant")


def say(text):
    return call("answer", text=text)


def test_the_assistant_answers_in_words_from_everything_that_is_stored(chat):
    save_todos(TodoData(items=[TodoItem(id="t1", title="send investor update", due="2026-11-13")]), USER, chat.data_dir)
    save_schedule(ScheduleData(entries=[
        ScheduleEntry(id="sara0001", title="1:1 with Sara", date="2026-11-13", start="10:00", end="11:00")]),
        USER, chat.data_dir)
    chat.model.extend([TO_ASSISTANT, say("Tomorrow: your 1:1 with Sara at 10, then the investor update.")])

    assert chat("Plan my day tomorrow") == "Tomorrow: your 1:1 with Sara at 10, then the investor update."
    coordinator, agent = chat.asked
    assert agent["prompt"] == "assistant_agent" and agent["tools"] == ["web_search", "answer", "ask_user"]
    assert "- send investor update — due Fri 13 Nov" in agent["system"]
    assert "[sara0001] 10:00–11:00 1:1 with Sara" in agent["system"]
    assert "No habits are tracked yet." in agent["system"]


def test_the_assistant_cannot_say_it_changed_something(chat):
    claim = say("I've added a focus block for you tomorrow morning.")
    chat.model.extend([TO_ASSISTANT, claim, claim])

    reply = chat("Plan my day tomorrow")

    assert "added" not in reply and "haven't changed anything" in reply
    assert [asked["prompt"] for asked in chat.asked] == ["coordinator", "assistant_agent", "assistant_agent"]


def test_a_web_search_is_shown_to_the_assistant_which_then_answers(chat, monkeypatch):
    searched = []
    monkeypatch.setattr(assistant_domain, "web_search", lambda query: searched.append(query) or "Toronto: 4°C, light rain.")
    chat.model.extend([TO_ASSISTANT, call("web_search", query="weather Toronto today"),
                       say("It's about 4°C with light rain in Toronto today.")])

    assert chat("what's the weather like today?") == "It's about 4°C with light rain in Toronto today."
    assert searched == ["weather Toronto today"]
    assert "Toronto: 4°C, light rain." in chat.asked[2]["turns"][-1][1]     # the result came back to the agent


def test_a_search_that_would_expose_something_private_is_not_run(chat, monkeypatch):
    searched = []
    monkeypatch.setattr(assistant_domain, "web_search", lambda query: searched.append(query) or "results")
    chat.model.extend([TO_ASSISTANT, call("web_search", query="is passport number X1234567 valid"),
                       say("I can't look that up without sending your passport number to a search engine.")])

    reply = chat("can you check if my passport number X1234567 is valid")

    assert searched == [] and reply.startswith("I can't look that up")
    assert "private" in chat.asked[2]["turns"][-1][1]


def test_searching_again_and_again_ends_with_an_honest_reply(chat, monkeypatch):
    monkeypatch.setattr(assistant_domain, "web_search", lambda query: "nothing useful")
    again = call("web_search", query="best pizza")
    chat.model.extend([TO_ASSISTANT, again, again, again])

    assert chat("where's the best pizza") == "I couldn't finish that. Could you say it another way?"
    assert len(chat.asked) == 4


def test_whatever_no_one_can_place_goes_to_the_assistant(chat):
    chat.model.extend([call("launch_rocket", when="now"), say("Hello! What can I do for you?")])
    assert chat("hello there") == "Hello! What can I do for you?"
    assert [asked["prompt"] for asked in chat.asked] == ["coordinator", "assistant_agent"]


def test_an_assistant_that_cannot_answer_either_says_so_plainly(chat):
    chat.model.extend([TO_ASSISTANT, call("launch_rocket", when="now")])
    assert chat("hello there") == "I couldn't finish that. Could you say it another way?"


def test_a_question_the_previous_assistant_left_open_is_closed_on_the_first_message(chat):
    from kairo.agents.personal_manager.application import clarification
    from kairo.agents.personal_manager.application.pending_reply import load_open_question
    from kairo.core.domain.types import PMIntent

    clarification._save_pending(THREAD, chat.data_dir, PMIntent.CREATE_SCHEDULE_EVENT, {"title": "gym"},
                                ["start"], user_id=USER)
    chat.model.append(call("add_todos", items=[{"title": "buy milk", "due_date": None, "due_time": None}]))

    assert chat("add todo buy milk") == "Added task: buy milk — no due date."
    assert load_open_question(THREAD, chat.data_dir, user_id=USER) is None      # it cannot capture a later message
    assert "waiting for an answer" not in chat.asked[0]["system"] and todos(chat) == [("buy milk", None, False)]
