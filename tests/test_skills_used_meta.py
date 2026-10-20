"""The two additive data contracts the
run/loop panel's "used N skills" chip and the learned-chip tap-target need.

Both ride surfaces the frontend already receives, so the change's "zero new WS/SSE
channels" clause holds by construction rather than by promise:

* ``meta["skills_used"]`` on the turn's USER message — the message each skill was
  attached to, which every turn has — plus the same list on an ``activity_event`` of kind
  ``skills`` the moment the turn is put together. It used to ride the assistant message whose
  text settled, so a turn that only called tools, or was stopped before it said a word, never
  showed which skill had joined it. Only ADMITTED and REDUCED decisions are in it — a
  REFUSED skill was NAMED to the agent but none of its content loaded, which is the same
  reading ``SkillAllocation.loaded`` takes for the turn-time use counter. Counting a
  refusal as a use would make the chip claim work the model never saw.
* an ``origin`` discriminator on the existing ``activity_event
  {kind: "learned"}``. All three captures in ``chat_runner`` emitted an identical
  payload, so a tap on the chip could not be routed to the surface that approves or
  edits THAT artifact. ``kind`` stays ``"learned"`` because live consumers key on it.

The "no new channel" clause is driven: every emitter of either contract runs for real, and
what it sends must be frames a turn already sends.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from chat_test_helpers import _make_state

from personalclaw.context_engine import AssembledContext
from personalclaw.context_headroom import HeadroomState
from personalclaw.dashboard import chat_runner
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.skills.allocation import SkillLoadState

# ── The turn harness: a real _ChatSession driven through run_chat ──────────────────
#
# Deliberately NOT a unit test of a private helper. The reset, the metadata read and the
# meta stamp live at three different points of one turn, and the contract is what a
# finished turn LEAVES ON THE MESSAGE — a per-site unit test would pass with the reset
# deleted.


def _decision(name: str, state: SkillLoadState, loaded_tokens: int) -> dict:
    """One row shaped exactly like ``SkillDecision.to_dict()``."""
    return {
        "name": name,
        "state": state.value,
        "tier": "standard",
        "cap_tokens": 4000,
        "body_tokens": 4200,
        "loaded_tokens": loaded_tokens,
        "reason": "" if state is SkillLoadState.ADMITTED else "over the tier cap",
        "forced": False,
    }


def _mock_client() -> AsyncMock:
    client = AsyncMock()
    client.context_usage_pct = MagicMock(return_value=10.0)

    async def _stream(msg):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="done")
        yield LLMEvent(kind=EVENT_COMPLETE)

    client.stream = _stream
    client.stream_command = _stream
    return client


@pytest.fixture
def turn(tmp_path, monkeypatch):
    """Returns ``run(metadata) -> session`` — one full turn with that assembled metadata.

    Calling it twice on the returned session runs a SECOND turn on the same session,
    which is how the per-turn reset becomes observable.
    """
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    # Truthy: the assembly branch this contract reads from is `elif state.context_builder:`.
    state.context_builder = MagicMock()
    state.consolidator = None
    state._hook_store = None
    import personalclaw.trust_mode as _tm

    _tm.disable_yolo()

    # The headroom verdict is not what is under test; a real check would need a bound
    # model and a window lookup.
    def _fits(_assembled, **_kw):
        return SimpleNamespace(state=HeadroomState.FITS, notice=lambda: "")

    monkeypatch.setattr(chat_runner, "check_headroom", _fits)
    state.sessions.get_or_create = AsyncMock(return_value=(_mock_client(), True, False))

    session = state.get_or_create_session("s-lv2")

    async def _run(metadata: dict, *, role: str | None = "user", text: str = "hello") -> object:
        monkeypatch.setattr(
            chat_runner,
            "assemble_context",
            lambda *_a, **_k: AssembledContext(message=text, metadata=dict(metadata)),
        )
        # As a dispatcher does: the turn's own message is on the session before its turn runs —
        # the user's from the chat or a channel, a `nudge` from a loop. `role=None` is the one
        # dispatcher that appends none: a subagent's report delivered to an idle chat.
        if role:
            session.append(role, text, "msg msg-u" if role == "user" else f"msg msg-{role}")
        await chat_runner.run_chat(state, session, text)
        return session

    _run.state = state  # type: ignore[attr-defined]
    _run.session = session  # type: ignore[attr-defined]
    return _run


def _last_meta(session, role: str) -> dict:
    msgs = [m for m in session.messages if m.get("role") == role]
    assert msgs, f"the turn produced no {role} message — the harness, not the contract, broke"
    meta = msgs[-1].get("meta")
    return meta if isinstance(meta, dict) else {}


def _skills_events(state) -> list[dict]:
    return [
        c.args[1]
        for c in state.broadcast_ws.call_args_list
        if c.args and c.args[0] == "activity_event" and c.args[1].get("kind") == "skills"
    ]


# ── skills_used on the turn's USER message ────────────────────────────────────────


@pytest.mark.asyncio
async def test_skills_used_carries_admitted_and_reduced_but_never_refused(turn):
    """The chip counts what REACHED the prompt, in the allocator's own order."""
    await turn(
        {
            "skill_decisions": [
                _decision("git-hygiene", SkillLoadState.ADMITTED, 900),
                _decision("release-notes", SkillLoadState.REFUSED, 0),
                _decision("code-review", SkillLoadState.REDUCED, 140),
            ]
        }
    )
    used = _last_meta(turn.session, "user")["skills_used"]
    # Allocation order preserved: the hover list reads in the order they were admitted.
    assert [r["name"] for r in used] == ["git-hygiene", "code-review"]
    assert [r["state"] for r in used] == ["admitted", "reduced"]
    assert [r["loaded_tokens"] for r in used] == [900, 140]
    # The refused skill is absent by NAME, not merely by state — it was never used.
    assert "release-notes" not in {r["name"] for r in used}
    # Exactly the three keys the frontend was briefed on: the allocator's bookkeeping
    # (tier / cap_tokens / body_tokens / reason / forced) is not the chip's business.
    assert all(set(r) == {"name", "state", "loaded_tokens"} for r in used)
    # One record per turn: the assistant message does not carry a second copy.
    assert "skills_used" not in _last_meta(turn.session, "assistant")


@pytest.mark.asyncio
async def test_the_turn_says_which_skills_joined_it_as_it_is_put_together(turn):
    """Live, by name, on the existing activity channel — before the model says anything."""
    await turn(
        {
            "skill_decisions": [
                _decision("imported/notes/trip-research", SkillLoadState.ADMITTED, 900),
                _decision("code-review", SkillLoadState.REDUCED, 140),
            ]
        }
    )
    events = _skills_events(turn.state)
    assert len(events) == 1, events
    event = events[0]
    assert event["session"] == turn.session.key
    assert [s["name"] for s in event["skills"]] == ["imported/notes/trip-research", "code-review"]
    assert event["text"] == (
        "Using skills imported/notes/trip-research, code-review (its summary only)"
    )
    order = [c.args[0] for c in turn.state.broadcast_ws.call_args_list]
    assert order.index("activity_event") < order.index("chat_done"), "said before the turn ended"


@pytest.mark.asyncio
async def test_no_skills_omits_the_key_entirely(turn):
    """Absent, not ``[]`` — same contract as ``memory_citations``.

    An empty list is a truthy-looking payload the frontend would have to special-case
    before deciding not to render a chip.
    """
    await turn({})
    assert "skills_used" not in _last_meta(turn.session, "user")
    assert _skills_events(turn.state) == [], "a turn no skill joined announces none"


@pytest.mark.asyncio
async def test_a_later_turn_does_not_inherit_an_earlier_turns_skills(turn):
    """The per-turn reset: turn 2 loaded nothing, so turn 2 shows nothing.

    Without the reset the session slot still holds turn 1's list and turn 2's message is
    stamped with skills it never loaded — a chip that lies in the most ordinary way.
    """
    await turn({"skill_decisions": [_decision("git-hygiene", SkillLoadState.ADMITTED, 900)]})
    first = _last_meta(turn.session, "user")["skills_used"]
    assert [r["name"] for r in first] == ["git-hygiene"]

    await turn({})  # second turn, no allocation at all
    assert "skills_used" not in _last_meta(turn.session, "user")
    assert turn.session._skills_used == []


@pytest.mark.asyncio
async def test_a_turn_a_loop_starts_keeps_its_record_on_its_own_nudge(turn):
    """Every loop cycle runs on a `nudge`, and an automation's or a subagent's turn on a row of
    its own: none has a user message, and the latest one belongs to an EARLIER turn.

    Stamped on "the latest user message", a cycle's skills overwrote the record of the turn
    before it — which then showed skills it never loaded — and the cycle itself kept none, so the
    loop's page showed nothing.
    """
    await turn({"skill_decisions": [_decision("git-hygiene", SkillLoadState.ADMITTED, 900)]})
    await turn(
        {"skill_decisions": [_decision("release-notes", SkillLoadState.ADMITTED, 700)]},
        role="nudge",
        text="[auto-nudge cycle 2]\nkeep going",
    )
    earlier = _last_meta(turn.session, "user")["skills_used"]
    assert [r["name"] for r in earlier] == ["git-hygiene"], "an earlier turn's record was rewritten"
    cycle = _last_meta(turn.session, "nudge").get("skills_used") or []
    assert [r["name"] for r in cycle] == ["release-notes"]


@pytest.mark.asyncio
async def test_a_turn_with_no_message_of_its_own_marks_no_other(turn):
    """A subagent's report delivered to an idle chat starts a turn with no row of its own: its
    skills are still announced, and no earlier message takes them."""
    await turn({"skill_decisions": [_decision("git-hygiene", SkillLoadState.ADMITTED, 900)]})
    await turn(
        {"skill_decisions": [_decision("release-notes", SkillLoadState.ADMITTED, 700)]},
        role=None,
        text="The research subagent finished: three sources, summary attached.",
    )
    earlier = _last_meta(turn.session, "user")["skills_used"]
    assert [r["name"] for r in earlier] == ["git-hygiene"], "an earlier turn's record was rewritten"
    stamped = [m for m in turn.session.messages if (m.get("meta") or {}).get("skills_used")]
    assert len(stamped) == 1, "exactly one message carries a record: the first turn's own"
    announced = _skills_events(turn.state)
    assert [[s["name"] for s in e["skills"]] for e in announced] == [
        ["git-hygiene"],
        ["release-notes"],
    ]


# ── The three learned-chip origins ────────────────────────────────────────────────


def _learning_state():
    """Minimal state for the two capture entry points: they only broadcast and read memory."""
    events: list[tuple[str, dict]] = []
    return SimpleNamespace(
        context_builder=SimpleNamespace(
            get_memory_for=lambda *_a, **_k: SimpleNamespace(vector_store=None),
            skills=SimpleNamespace(list_skills=lambda: []),
        ),
        broadcast_ws=lambda name, payload: events.append((name, payload)),
        _background_tasks=set(),
        events=events,
    )


def _learning_session():
    return SimpleNamespace(
        key="dashboard:chat-lv2",
        workspace_dir=None,
        memory_store=None,
        _ephemeral=False,
    )


def _learned(events: list[tuple[str, dict]]) -> list[dict]:
    return [p for n, p in events if n == "activity_event" and p.get("kind") == "learned"]


@pytest.mark.parametrize(
    ("facet", "lesson", "worthwhile", "expected_origin"),
    [
        # Facet capture runs unconditionally on a permitted turn — below the expensive
        # gate, so `worthwhile=False` isolates it from the lesson path.
        (("facet", "keep your answers short", "pref.facet.style.k"), None, False, "facet"),
        # A veto the preference detector finds is written as a LESSON, so its chip says so
        # and its tap lands where lessons are reviewed, not on the learned preferences.
        (("lesson", "never force-push to main", ""), None, False, "lesson"),
        # …and the expensive review's correction→lesson, with the facet path silent.
        (None, "always run make lint before pushing", True, "lesson"),
    ],
)
def test_after_turn_review_origins(monkeypatch, facet, lesson, worthwhile, expected_origin):
    """The two synchronous captures are distinguishable, and both stay ``kind: learned``."""
    import personalclaw.after_turn_review as atr
    import personalclaw.learning as learning

    learned = atr.Learned(*facet) if facet else None
    monkeypatch.setattr(atr, "capture_preference_facet", lambda *_a, **_k: learned)
    monkeypatch.setattr(atr, "run_after_turn_review", lambda **_k: lesson)
    monkeypatch.setattr(atr, "is_correction_signal", lambda *_a, **_k: True)
    monkeypatch.setattr(atr, "record_procedural_outcomes", lambda *_a, **_k: None)
    monkeypatch.setattr("personalclaw.memory_service.service_for", lambda _m: object())
    # A denial row is written for the not-worthwhile turn; the row is not under test and
    # its writer would reach for a real store.
    monkeypatch.setattr(learning, "record_denial", lambda *_a, **_k: None)

    state = _learning_state()
    chat_runner._maybe_after_turn_review(
        state,
        _learning_session(),
        user_message="no, use tabs",
        assistant_text="understood",
        tool_calls=6,
        provider=None,
        decision=SimpleNamespace(permitted=True, worthwhile=worthwhile, allowed=True),
    )

    chips = _learned(state.events)
    assert len(chips) == 1, f"expected exactly one learned chip, got {state.events}"
    assert chips[0]["kind"] == "learned"  # unchanged: live FE consumers key on this
    assert chips[0]["origin"] == expected_origin


def _stub_captures(monkeypatch, facet):
    import personalclaw.after_turn_review as atr
    import personalclaw.learning as learning

    monkeypatch.setattr(atr, "capture_preference_facet", lambda *_a, **_k: facet)
    monkeypatch.setattr(atr, "capture_glossary_term", lambda *_a, **_k: None)
    monkeypatch.setattr("personalclaw.memory_service.service_for", lambda _m: object())
    monkeypatch.setattr(learning, "record_denial", lambda *_a, **_k: None)


def test_what_a_turn_learned_is_kept_on_the_turn(monkeypatch):
    """The chip rode the socket only, so a reload or a restart lost it and a preference was
    saved with nothing on the page saying so. The turn now keeps the record, with the key the
    chat's "Forget it" needs, and says so, because its caller has to save once more."""
    import personalclaw.after_turn_review as atr

    _stub_captures(
        monkeypatch, atr.Learned("facet", "keep your answers short", "pref.facet.style.k")
    )
    session = _learning_session()
    session.messages = [
        {"role": "user", "content": "From now on, keep your answers short."},
        {"role": "assistant", "content": "Will do."},
    ]
    state = _learning_state()

    kept = chat_runner._maybe_after_turn_review(
        state,
        session,
        user_message="From now on, keep your answers short.",
        assistant_text="Will do.",
        tool_calls=0,
        decision=SimpleNamespace(permitted=True, worthwhile=False, allowed=True),
    )

    assert kept is True
    assert session.messages[-1]["meta"]["learned"] == [
        {"origin": "facet", "text": "keep your answers short", "ref": "pref.facet.style.k"}
    ]
    (chip,) = _learned(state.events)
    assert chip["text"] == "Learned: keep your answers short"
    assert chip["ref"] == "pref.facet.style.k"


def test_a_turn_that_learned_nothing_keeps_no_record(monkeypatch):
    _stub_captures(monkeypatch, None)
    session = _learning_session()
    session.messages = [{"role": "assistant", "content": "Here is the summary."}]

    kept = chat_runner._maybe_after_turn_review(
        _learning_state(),
        session,
        user_message="Summarize the notes.",
        assistant_text="Here is the summary.",
        tool_calls=0,
        decision=SimpleNamespace(permitted=True, worthwhile=False, allowed=True),
    )

    assert kept is False
    assert "meta" not in session.messages[-1]


@pytest.mark.asyncio
async def test_skill_ladder_origin_is_proposal():
    """The ladder's chip is a PROPOSAL — its tap belongs on the approve/edit surface for a
    proposed skill, not on a facet or a lesson."""
    import personalclaw.after_turn_review as atr

    async def _summary(**_k):
        return "Proposed skill: tighten the release checklist"

    original = atr.run_skill_ladder_review
    atr.run_skill_ladder_review = _summary  # type: ignore[assignment]
    try:
        state = _learning_state()
        chat_runner._maybe_skill_ladder_review(
            state,
            _learning_session(),
            user_message="that was wrong",
            assistant_text="fixed",
            tool_calls=6,
            decision=SimpleNamespace(permitted=True, worthwhile=True, allowed=True),
        )
        # The ladder is a forked-LLM pass scheduled as a background task on purpose.
        assert state._background_tasks, "the ladder scheduled nothing — the gate, not the origin"
        await asyncio.gather(*list(state._background_tasks))
    finally:
        atr.run_skill_ladder_review = original  # type: ignore[assignment]

    chips = _learned(state.events)
    assert len(chips) == 1, f"expected exactly one learned chip, got {state.events}"
    assert chips[0]["kind"] == "learned"
    assert chips[0]["origin"] == "proposal"


def test_the_three_origins_are_distinct_and_closed():
    """A discriminator whose values collide routes two captures to one surface.

    Read from every spelling an origin is written in: a literal on the event, a literal handed
    to ``_announce_learned``, and the ``Learned`` records the preference capture returns.
    """
    import personalclaw.after_turn_review as atr

    runner = Path(chat_runner.__file__).read_text()
    origins = set(re.findall(r'"origin":\s*"([a-z]+)"', runner))
    origins |= set(re.findall(r'_announce_learned\(\s*state,\s*session,\s*"([a-z]+)"', runner))
    origins |= set(re.findall(r'Learned\(\s*"([a-z]+)"', Path(atr.__file__).read_text()))
    assert origins == {"facet", "lesson", "proposal"}


# ── The change's "zero new WS/SSE channels" clause ────────────────────────────────

#: The frames a turn already sends: the runner's own, the ring's reading said through the state
#: (``context_usage``), a steer it did not take, requeued (``queue_push``, from ``running_turn``),
#: its phase (``work_phase``, from ``chat_phase``) and the follow-ups suggested after it
#: (``chat_followups``, from ``chat_followups``). Both contracts above are payload on names
#: already here (``meta`` on the turn's own message; a ``kind`` and an ``origin`` on
#: ``activity_event``), so none of their emitters may send a name outside it.
_TURN_WS_EVENTS = {
    "activity_event",
    "chat_chunk",
    "chat_done",
    "chat_followups",
    "chat_message",
    "chat_segment",
    "chat_thinking",
    "chat_user_message",
    "chat_variant_switch",
    "context_usage",
    "queue_pop",
    "queue_push",
    "session_agent_switch",
    "session_clear",
    "token_usage",
    "tool_call",
    "tool_result",
    "work_phase",
}


#: What starts a turn, by the role of the row it puts on the session (``None``: a subagent's report
#: delivered to an idle chat, which puts none).
_STARTED_BY = {
    "user": "hello",
    "nudge": "[auto-nudge cycle 2]\nkeep going",
    None: "The research subagent finished: three sources, summary attached.",
}


async def _a_turn(turn, role):
    """A real turn three skills joined (one refused), started by *role*'s message."""
    turn.state.broadcast_ws.reset_mock()
    await turn(
        {
            "skill_decisions": [
                _decision("git-hygiene", SkillLoadState.ADMITTED, 900),
                _decision("release-notes", SkillLoadState.REFUSED, 0),
                _decision("code-review", SkillLoadState.REDUCED, 140),
            ]
        },
        role=role,
        text=_STARTED_BY[role],
    )
    return [(c.args[0], c.args[1]) for c in turn.state.broadcast_ws.call_args_list if c.args]


def _a_review(monkeypatch, facet, lesson, worthwhile):
    """The real after-turn review, with what its captures find set by the row."""
    import personalclaw.after_turn_review as atr
    import personalclaw.learning as learning

    found = atr.Learned(*facet) if facet else None
    monkeypatch.setattr(atr, "capture_preference_facet", lambda *_a, **_k: found)
    monkeypatch.setattr(atr, "capture_glossary_term", lambda *_a, **_k: None)
    monkeypatch.setattr(atr, "run_after_turn_review", lambda **_k: lesson)
    monkeypatch.setattr(atr, "is_correction_signal", lambda *_a, **_k: True)
    monkeypatch.setattr(atr, "record_procedural_outcomes", lambda *_a, **_k: None)
    monkeypatch.setattr("personalclaw.memory_service.service_for", lambda _m: object())
    monkeypatch.setattr(learning, "record_denial", lambda *_a, **_k: None)
    state = _learning_state()
    chat_runner._maybe_after_turn_review(
        state,
        _learning_session(),
        user_message="no, use tabs",
        assistant_text="understood",
        tool_calls=6,
        provider=None,
        decision=SimpleNamespace(permitted=True, worthwhile=worthwhile, allowed=True),
    )
    return state.events


async def _a_ladder(monkeypatch):
    """The real skill-ladder review, its background pass run to the end."""
    import personalclaw.after_turn_review as atr

    async def _summary(**_k):
        return "Proposed skill: tighten the release checklist"

    monkeypatch.setattr(atr, "run_skill_ladder_review", _summary)
    state = _learning_state()
    chat_runner._maybe_skill_ladder_review(
        state,
        _learning_session(),
        user_message="that was wrong",
        assistant_text="fixed",
        tool_calls=6,
        decision=SimpleNamespace(permitted=True, worthwhile=True, allowed=True),
    )
    assert state._background_tasks, "the ladder scheduled nothing"
    await asyncio.gather(*list(state._background_tasks))
    return state.events


def _a_stumble(monkeypatch):
    """The real stumble arm, on a turn a skill joined and that went wrong."""
    import personalclaw.after_turn_review as atr
    from personalclaw.skills import refine

    monkeypatch.setattr(
        atr,
        "detect_stumble",
        lambda **_k: SimpleNamespace(trigger="correction", detail="use uv, not pip"),
    )
    monkeypatch.setattr(refine, "refine_target", lambda *_a: "git-hygiene")
    monkeypatch.setattr(
        refine, "propose_refinement", lambda **_k: SimpleNamespace(slug="git-hygiene")
    )
    state = _learning_state()
    session = _learning_session()
    session._skills_used = [{"name": "git-hygiene", "state": "admitted", "loaded_tokens": 900}]
    chat_runner._maybe_refine_stumble(
        state,
        session,
        "No, use uv instead of pip.",
        "Ran pip install.",
        [],
        SimpleNamespace(skill_ladder=True, surface_chip=True),
    )
    return state.events


#: Every emitter of the two contracts: ``(case, drive, the activity kind its payload rides, the
#: learned chip's origin)``. *drive* runs the real emitter and returns the frames it sent.
_EMITTERS = [
    ("a turn the user started", lambda t, _mp: _a_turn(t, "user"), "skills", None),
    ("a loop cycle's turn, on its nudge", lambda t, _mp: _a_turn(t, "nudge"), "skills", None),
    ("a turn with no message of its own", lambda t, _mp: _a_turn(t, None), "skills", None),
    (
        "a standing preference, kept as a facet",
        lambda _t, mp: _a_review(mp, ("facet", "keep it short", "pref.facet.k"), None, False),
        "learned",
        "facet",
    ),
    (
        "a veto, kept as a lesson",
        lambda _t, mp: _a_review(mp, ("lesson", "never force-push to main", ""), None, False),
        "learned",
        "lesson",
    ),
    (
        "a correction the review keeps as a lesson",
        lambda _t, mp: _a_review(mp, None, "always run make lint first", True),
        "learned",
        "lesson",
    ),
    ("a skill the ladder proposes", lambda _t, mp: _a_ladder(mp), "learned", "proposal"),
    ("a refinement a stumble proposes", lambda _t, mp: _a_stumble(mp), "learned", "proposal"),
]


@pytest.mark.asyncio
async def test_each_contract_rides_a_frame_a_turn_already_sends(turn, subtests):
    """The clause, driven: each place a skills record or a learned chip is sent, run for real,
    sends no name a turn did not already send, none named after either contract, and its payload
    is on ``activity_event`` with the kind (and, for a chip, the origin) a client keys on."""
    from table_rows import row

    assert len(_EMITTERS) == 8, "an emitter was dropped from the table"
    for case, drive, kind, origin in _EMITTERS:
        with row(subtests, case=case), pytest.MonkeyPatch.context() as mp:
            sent = drive(turn, mp)
            if asyncio.iscoroutine(sent):
                sent = await sent
            names = {name for name, _payload in sent}
            new = sorted(names - _TURN_WS_EVENTS)
            assert not new, f"{case}: new channel(s) {new}"
            assert not [n for n in names if "skill" in n or "learn" in n], f"{case}: {names}"
            carried = [p for n, p in sent if n == "activity_event" and p.get("kind") == kind]
            assert len(carried) == 1, f"{case}: the payload rode no frame a turn sends: {sent}"
            if kind == "skills":
                assert [s["name"] for s in carried[0]["skills"]] == ["git-hygiene", "code-review"]
            else:
                assert carried[0]["origin"] == origin, f"{case}: {carried[0]}"


# ── The LOOP half's two unguarded seams ───────────────────────────────────────────
#
# The chat chip reads `meta["skills_used"]` off a message the page already holds. The LOOP
# cockpit cannot: its live stream carries no message meta, so it reads the worker transcript
# over `GET /api/chat/sessions/{key}` and finds that key on `GET /api/loops/{id}`. That makes
# the cockpit chip depend on TWO backend contracts that no test named, and both fail SILENTLY
# — a dropped `session_key` leaves `workerKey` empty and a clobbered `meta` leaves the list
# empty, and the chip's own "absent when the run loaded nothing" rule renders each as simply
# no chip. Nothing would go red. These two pin them.


def test_the_loop_detail_view_serves_the_session_key_the_cockpit_reads(monkeypatch, tmp_path):
    """`workerKey` comes from the redacted loop view — if it stops, the chip goes inert.

    Drives ``get_redacted``, the function ``api_loop_get`` actually calls, rather than the
    ``_redact_loop`` helper underneath it: a first draft of this test asserted on the helper
    and stayed GREEN when the endpoint's own view dropped the key, which is precisely the
    regression it is here to catch. Isolated to ``tmp_path`` — it writes a loop row.
    """
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.loop import Loop

    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    loop_store.create(Loop(id="abc12345", name="n", kind="goal", task="t"))
    # Vacuity floor: BEFORE the writer runs the field is empty, so the assertion below
    # cannot be riding a hardcoded value — and this also pins that the cockpit's honest
    # answer for a never-started loop is "no key" rather than a bogus one.
    assert loop_store.get_redacted("abc12345")["session_key"] == ""

    loop_store.set_session_key("abc12345", "loop-abc12345")
    assert loop_store.get_redacted("abc12345")["session_key"] == "loop-abc12345"


def test_session_detail_does_not_clobber_skills_used_with_cls_meta():
    """`_prepare_messages` overwrites `meta` from `cls` — the record's carriers must survive.

    The cockpit and a chat's reload read `skills_used` back out of this endpoint, off the
    message that started the turn: the user's, or a loop cycle's nudge. So the overwrite branch
    sitting one line away from the payload is the risk worth pinning, for both.
    """
    from personalclaw.dashboard.chat_utils import _prepare_messages, parse_cls_meta

    used = [{"name": "auto/release-flow", "state": "admitted", "loaded_tokens": 900}]
    out = _prepare_messages(
        [
            {"role": "user", "content": "hi", "cls": "msg msg-u", "meta": {"skills_used": used}},
            {
                "role": "nudge",
                "content": "[auto-nudge cycle 2]\nkeep going",
                "cls": "msg msg-nudge",
                "meta": {"skills_used": used},
            },
        ],
        False,
    )
    assert [m["meta"]["skills_used"] for m in out] == [used, used]
    # Vacuity: the clobber branch is LIVE, not dead code this test merely misses. A `cls`
    # holding a JSON dict does replace `meta` wholesale — which is exactly why the carriers
    # (`cls="msg msg-u"` / `"msg msg-nudge"`, not JSON) have to fall through it.
    assert parse_cls_meta("msg msg-u") is None
    assert parse_cls_meta("msg msg-nudge") is None
    clobbered = _prepare_messages(
        [{"role": "assistant", "content": "hi", "cls": '{"tool": "read"}', "meta": {"gone": 1}}],
        False,
    )
    assert "gone" not in clobbered[0]["meta"], "the overwrite branch stopped firing"
