"""A message PersonalClaw writes for her to send as her own states no detail nobody gave.

Measured before this: a reply said a swim class's time was not in her notes and asked her for it
("tell me the time and I'll add it to …"), and a follow-up chip under it read "Lina's swim class is
Saturday at 10:00 AM". The chip's send glyph sends it with one click as her message, right after
the agent offered to file what she says, so a time nobody gave would have gone into her notes as
hers. The follow-ups prompt asked for messages "phrased from the USER's point of view" with no rule
against details she never gave, and nothing checked what the model wrote.

What holds now, for the follow-up chips, the new-chat suggestions and a prompt rewrite:

* each is written under rules, whatever prompt is bound, that it states no fact, time, date, name,
  number, place or decision the text it is written from does not give, and that a chip never
  answers what the reply asked her;
* what the model wrote is checked: one that states a time, number, name, path or file its source
  does not give is left out (a chip, a suggestion) or not used (a rewrite, whose answer names what
  it added), and one whose details its source gives is kept.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from chat_test_helpers import _make_state

from personalclaw.dashboard.chat_followups import _maybe_followups
from personalclaw.given_details import keep_given, ungiven

#: The last exchange of the measured chat: she will look the time up herself, and the reply asks
#: her for it.
USER = "Sorry, I meant Lina. I'll check the registration email myself."
REPLY = (
    "Sounds good. Nothing in your notes mentions a swim class, and the time isn't in them, so the "
    "email is the right place to look.\n\nIf you want it on file, tell me the time and I'll add "
    "it to `~/Notes/Home/school-term.md`."
)
#: The chip the model wrote: a day and a time nobody gave.
INVENTED = "Lina's swim class is Saturday at 10:00 AM"
#: A chip whose one detail, the file, the reply names.
GROUNDED = "Add it to the school-term.md file"


class _Model:
    """A scripted chore model: answers in turn, keeping every prompt it was sent."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def __call__(self, prompt: str, **_kwargs) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else "[]"


@pytest.fixture
def chat(tmp_path, monkeypatch):
    """A chat whose last exchange is *user* then *reply*, and the events it broadcasts."""
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(state, "broadcast_ws", lambda t, d: events.append((t, d)), raising=True)

    def make(user: str = USER, reply: str = REPLY):
        session = state.get_or_create_session("s1")
        session.append("user", user, "msg msg-u", broadcast=False)
        session.append("assistant", reply, "msg msg-a", broadcast=False)
        session.drain()
        return session

    return SimpleNamespace(state=state, events=events, make=make)


def _chips(events: list[tuple[str, dict]]) -> list[list[str]]:
    return [d["items"] for t, d in events if t == "chat_followups"]


# ── follow-up chips ───────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_follow_up_is_asked_for_under_the_rule_against_details_nobody_gave(
    chat, monkeypatch
):
    model = _Model(json.dumps([GROUNDED]))
    monkeypatch.setattr("personalclaw.chores.run_chore", model)

    await _maybe_followups(chat.state, chat.make())

    (prompt,) = model.prompts
    assert USER in prompt and "tell me the time" in prompt, "the exchange is still what it reads"
    assert (
        "never states a fact, time, date, name, number, place or decision for them that neither "
        "their message nor the reply above gives" in prompt
    )
    assert "a follow-up never answers it for them, not even as a guess" in prompt


@pytest.mark.asyncio
async def test_the_rule_holds_whatever_prompt_is_bound_for_follow_ups(chat, monkeypatch):
    """The bound prompt may be her own edit, or a copy seeded before the rule existed: a seeded
    prompt is never rewritten on an existing install, so the rule rides after it in code."""
    monkeypatch.setattr(
        "personalclaw.prompt_providers.runtime.render_use_case_prompt",
        lambda use_case, values: f"My own follow-ups prompt.\n{values['exchange']}",
    )
    model = _Model(json.dumps([GROUNDED]))
    monkeypatch.setattr("personalclaw.chores.run_chore", model)

    await _maybe_followups(chat.state, chat.make())

    (prompt,) = model.prompts
    assert prompt.startswith("My own follow-ups prompt.")
    assert "Rules for these follow-ups, whatever is above:" in prompt
    assert "never answers it for them" in prompt


@pytest.mark.asyncio
async def test_a_chip_stating_a_time_nobody_gave_is_left_out_and_one_from_the_turns_is_kept(
    chat, monkeypatch
):
    monkeypatch.setattr("personalclaw.chores.run_chore", _Model(json.dumps([INVENTED, GROUNDED])))

    await _maybe_followups(chat.state, chat.make())

    assert _chips(chat.events) == [[GROUNDED]]


@pytest.mark.asyncio
async def test_a_chip_whose_time_name_and_day_the_turns_give_is_kept(chat, monkeypatch):
    reply = "Your notes say Lina's swim class is on Saturdays at 10am, at the pool on Elm Street."
    chip = "Add Lina's 10:00 AM Saturday class to school-term.md"
    session = chat.make(user="When is Lina's swim class? Put it in school-term.md", reply=reply)
    monkeypatch.setattr("personalclaw.chores.run_chore", _Model(json.dumps([chip])))

    await _maybe_followups(chat.state, session)

    assert _chips(chat.events) == [[chip]]


@pytest.mark.asyncio
async def test_when_every_chip_states_what_nobody_gave_none_are_offered(chat, monkeypatch):
    invented = [INVENTED, "Remind me at 9pm to check the email"]
    monkeypatch.setattr("personalclaw.chores.run_chore", _Model(json.dumps(invented)))

    await _maybe_followups(chat.state, chat.make())

    assert _chips(chat.events) == []


@pytest.mark.asyncio
async def test_chips_with_no_detail_in_them_are_all_kept(chat, monkeypatch):
    """The check reads details, not words: an ordinary next request is new words by design."""
    plain = ["Where else might the time be written down?", "What else is in that note?"]
    monkeypatch.setattr("personalclaw.chores.run_chore", _Model(json.dumps(plain)))

    await _maybe_followups(chat.state, chat.make())

    assert _chips(chat.events) == [plain]


# ── the new-chat suggestions ──────────────────────────────────────────────────────────────────

CONTEXT = (
    "## Recent Sessions\n- **Swim class**\n  - User: When does Lina's swim class start on "
    "Saturdays? I'll find the registration email."
)


@pytest.fixture
def suggest(monkeypatch):
    """``generate_suggestions`` over :data:`CONTEXT` at a fixed time, and the model it asks."""
    from personalclaw import suggestions

    monkeypatch.setattr(suggestions, "_build_context", lambda _state: CONTEXT)
    monkeypatch.setattr(
        suggestions, "_time_context", lambda: "## Current Time\nWednesday, September 02 2026"
    )

    async def run(*answers: str) -> tuple[list[str], _Model]:
        model = _Model(*answers)
        monkeypatch.setattr("personalclaw.chores.run_chore", model)
        return await suggestions.generate_suggestions(SimpleNamespace()), model

    return run


@pytest.mark.asyncio
async def test_a_suggestion_stating_a_time_nobody_gave_is_left_out(suggest):
    out, model = await suggest(
        json.dumps(["Book Lina's swim class for Saturday at 9:30", "Find the registration email"])
    )

    assert out == ["Find the registration email"]
    (prompt,) = model.prompts
    assert "Rules for these suggestions, whatever is above:" in prompt
    assert (
        "never states a fact, time, date, name, number, place or decision for them that the "
        "context above does not give" in prompt
    )


@pytest.mark.asyncio
async def test_when_every_suggestion_states_what_nobody_gave_the_fallback_list_is_offered(suggest):
    from personalclaw.suggestions import _FALLBACK_SUGGESTIONS

    out, _ = await suggest(json.dumps(["Book Lina's swim class for 9:30", "Pay the $40 swim fee"]))

    assert out == _FALLBACK_SUGGESTIONS


# ── a prompt rewrite ──────────────────────────────────────────────────────────────────────────


async def _optimize(prompt: str, reply: str, context: str = "") -> dict:
    """What ``POST /api/optimizer/optimize`` answers when its model rewrites *prompt* as *reply*."""
    from personalclaw.dashboard.handlers.optimizer import handle_optimize
    from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK

    async def stream(_full_prompt):
        yield MagicMock(kind=EVENT_TEXT_CHUNK, text=reply)
        yield MagicMock(kind=EVENT_COMPLETE)

    client = AsyncMock()
    client.stream = stream
    sessions = MagicMock()
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    sessions.release = MagicMock()
    sessions.reset = AsyncMock()
    request = MagicMock()
    request.json = AsyncMock(return_value={"prompt": prompt, "context": context})
    request.app = {"state": MagicMock(sessions=sessions)}
    return json.loads((await handle_optimize(request)).body)


@pytest.mark.asyncio
async def test_a_rewrite_that_adds_a_time_she_left_open_is_not_used_and_says_what_it_added():
    data = await _optimize("book the swim class", "Book the swim class for Saturday at 10:00 AM.")

    assert data == {
        "optimized": "book the swim class",
        "changed": False,
        "added": ["Saturday", "10:00 AM"],
    }


@pytest.mark.asyncio
async def test_a_rewrite_whose_file_and_number_her_context_gives_is_used():
    context = "assistant: The retry loop lives in src/net/client.py — it backs off 3 times."
    reply = (
        "Add a test for the retry loop in src/net/client.py covering all 3 backoff attempts. "
        "Preserve existing behavior."
    )

    data = await _optimize("add a test for that file from earlier", reply, context)

    assert data == {"optimized": reply, "changed": True}


@pytest.mark.asyncio
async def test_a_rewrite_numbering_its_steps_and_naming_a_term_is_used():
    reply = (
        "Clean up the service and add retry logic:\n1. Identify unclear sections.\n2. Add retry "
        "with backoff.\nLeave the public API unchanged."
    )

    data = await _optimize("maybe clean up the service and also add retry logic", reply)

    assert data["changed"] is True


# ── what a detail is, and what gives it ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "given", "unsaid"),
    [
        # A time is given by a time that can be the same one, never by a bare number.
        ("It starts at 10:00 AM", "it starts at 10am", []),
        ("It starts at 10 PM", "it starts at 10:00", []),
        ("It starts at 10:00 AM", "I searched 10 notes", ["10:00 AM"]),
        ("It starts at 10 AM", "it starts at 10 pm", ["10 AM"]),
        ("Let's meet at noon", "lunch?", ["noon"]),
        # A number by the same number.
        ("Pay the $1,200 deposit", "the deposit is 1200", []),
        ("Pay the $40 fee", "the fee is due", ["40"]),
        ("1. Find the file\n2. Fix it", "fix the file", []),
        # A name by the same word in any case or number; the first word only as a possessive.
        ("Lina's class is on Saturday", "lina's class, on saturdays", []),
        ("Sam's coming too", "who is coming?", ["Sam's"]),
        ("Show me an example", "explain it", []),
        ("Let's check the registration email", "the email", []),
        ("Check the API docs", "check the docs", []),
        ("It starts on Sat", "class on Saturday", []),
        ("It starts on Sunday", "class on Sun", []),
        ("It starts on Sunday", "the sun is out", ["Sunday"]),
        ("Ask Dr. Patel about it", "ask the doctor", ["Dr", "Patel"]),
        # A path, file or address by the same text anywhere in the source.
        ("Save it to school-term.md", "add it to `~/Notes/Home/school-term.md`", []),
        ("Apply the same fix to config.py", "fix the parser", ["config.py"]),
        ("Mail it to sam@example.org", "mail it", ["sam@example.org"]),
        ("Use the logs and/or metrics", "the logs", []),
    ],
)
def test_what_a_detail_is_and_what_gives_it(text, given, unsaid):
    assert ungiven(text, given) == unsaid


def test_keep_given_keeps_the_order_and_says_how_many_it_left_out(caplog):
    """Said at warning, the gateway log's level: a line below it is a line nobody reads."""
    import logging

    items = [GROUNDED, INVENTED, "What else is in that note?"]
    with caplog.at_level(logging.DEBUG, logger="personalclaw.given_details"):
        kept = keep_given(items, f"user: {USER}\nassistant: {REPLY}", what="Follow-ups")

    assert kept == [GROUNDED, "What else is in that note?"]
    said = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert said == [
        "Follow-ups: left out 1 of 3 the model wrote: each states a detail its source does not give"
    ]
    assert "10:00 AM" not in " ".join(said), "the details are words written for her: debug only"
