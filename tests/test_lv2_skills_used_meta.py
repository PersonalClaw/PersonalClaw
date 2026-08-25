"""LV-2 (LEARNING-VISIBILITY T2.1 + T2.2): the two additive data contracts the
run/loop panel's "used N skills" chip and the learned-chip tap-target need.

Both ride surfaces the frontend already receives, so the atom's "zero new WS/SSE
channels" clause holds by construction rather than by promise:

* **T2.1** ``meta["skills_used"]`` on the finalized assistant message, mirroring the
  proven ``memory_citations`` seam. Only ADMITTED and REDUCED decisions are in it — a
  REFUSED skill was NAMED to the agent but none of its content loaded, which is the same
  reading ``SkillAllocation.loaded`` takes for the turn-time use counter. Counting a
  refusal as a use would make the chip claim work the model never saw.
* **T2.2** an ``origin`` discriminator on the existing ``activity_event
  {kind: "learned"}``. All three captures in ``chat_runner`` emitted an identical
  payload, so a tap on the chip could not be routed to the surface that approves or
  edits THAT artifact. ``kind`` stays ``"learned"`` because live consumers key on it.

The last test is the acceptance rail for the "no new channel" clause: it pins the whole
set of WS event names ``chat_runner`` broadcasts.
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
    """One row shaped exactly like ``SkillDecision.to_dict()`` (CE2-9)."""
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
    async def _fits(_assembled, **_kw):
        return SimpleNamespace(state=HeadroomState.FITS, notice=lambda: "")

    monkeypatch.setattr(chat_runner, "check_headroom", _fits)
    state.sessions.get_or_create = AsyncMock(return_value=(_mock_client(), True, False))

    session = state.get_or_create_session("s-lv2")

    async def _run(metadata: dict) -> object:
        monkeypatch.setattr(
            chat_runner,
            "assemble_context",
            lambda *_a, **_k: AssembledContext(message="hello", metadata=dict(metadata)),
        )
        await chat_runner.run_chat(state, session, "hello")
        return session

    _run.state = state  # type: ignore[attr-defined]
    _run.session = session  # type: ignore[attr-defined]
    return _run


def _last_assistant_meta(session) -> dict:
    msgs = [m for m in session.messages if m.get("role") == "assistant"]
    assert msgs, "the turn produced no assistant message — the harness, not the contract, broke"
    meta = msgs[-1].get("meta")
    return meta if isinstance(meta, dict) else {}


# ── T2.1 ──────────────────────────────────────────────────────────────────────────


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
    used = _last_assistant_meta(turn.session)["skills_used"]
    # Allocation order preserved: the hover list reads in the order they were admitted.
    assert [r["name"] for r in used] == ["git-hygiene", "code-review"]
    assert [r["state"] for r in used] == ["admitted", "reduced"]
    assert [r["loaded_tokens"] for r in used] == [900, 140]
    # The refused skill is absent by NAME, not merely by state — it was never used.
    assert "release-notes" not in {r["name"] for r in used}
    # Exactly the three keys the frontend was briefed on: the allocator's bookkeeping
    # (tier / cap_tokens / body_tokens / reason / forced) is not the chip's business.
    assert all(set(r) == {"name", "state", "loaded_tokens"} for r in used)


@pytest.mark.asyncio
async def test_no_skills_omits_the_key_entirely(turn):
    """Absent, not ``[]`` — same contract as ``memory_citations``.

    An empty list is a truthy-looking payload the frontend would have to special-case
    before deciding not to render a chip.
    """
    await turn({})
    assert "skills_used" not in _last_assistant_meta(turn.session)


@pytest.mark.asyncio
async def test_a_later_turn_does_not_inherit_an_earlier_turns_skills(turn):
    """The per-turn reset: turn 2 loaded nothing, so turn 2 shows nothing.

    Without the reset the session slot still holds turn 1's list and turn 2's message is
    stamped with skills it never loaded — a chip that lies in the most ordinary way.
    """
    await turn({"skill_decisions": [_decision("git-hygiene", SkillLoadState.ADMITTED, 900)]})
    first = _last_assistant_meta(turn.session)["skills_used"]
    assert [r["name"] for r in first] == ["git-hygiene"]

    await turn({})  # second turn, no allocation at all
    assert "skills_used" not in _last_assistant_meta(turn.session)
    assert turn.session._skills_used == []


# ── T2.2: the three learned-chip origins ──────────────────────────────────────────


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
        ("prefers terse replies", None, False, "facet"),
        # …and the expensive review's correction→lesson, with the facet path silent.
        (None, "always run make lint before pushing", True, "lesson"),
    ],
)
def test_after_turn_review_origins(monkeypatch, facet, lesson, worthwhile, expected_origin):
    """The two synchronous captures are distinguishable, and both stay ``kind: learned``."""
    import personalclaw.after_turn_review as atr
    import personalclaw.learning as learning

    monkeypatch.setattr(atr, "capture_preference_facet", lambda *_a, **_k: facet)
    monkeypatch.setattr(atr, "run_after_turn_review", lambda **_k: lesson)
    monkeypatch.setattr(atr, "is_correction_signal", lambda *_a, **_k: True)
    monkeypatch.setattr(atr, "record_procedural_outcomes", lambda *_a, **_k: None)
    monkeypatch.setattr("personalclaw.memory_service.service_for", lambda _m: object())
    # §3.2 writes a denial row for the not-worthwhile turn; the row is not under test and
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
    """A discriminator whose values collide routes two captures to one surface."""
    origins = set(re.findall(r'"origin":\s*"([a-z]+)"', Path(chat_runner.__file__).read_text()))
    assert origins == {"facet", "lesson", "proposal"}


# ── The atom's "zero new WS/SSE channels" clause ──────────────────────────────────

#: Every WS event name ``chat_runner`` broadcasts, pinned. Verified to cover 49/49
#: ``broadcast_ws(`` call sites in the module, so a miss here is a real new channel and
#: not a regex that stopped matching. Both contracts above are additive payload on names
#: ALREADY in this set (``meta`` on ``chat_segment``'s message; a key on
#: ``activity_event``), so landing them must leave it byte-for-byte unchanged.
#:
#: This is a shared-module baseline, so it drifts on merge with changes that have nothing
#: to do with LV-2 — and a rebaseline is only honest when the new name is provably not
#: ours. ``queue_push`` arrived from ``57194f48`` (PR2-10's ACP mid-turn steer echo) and
#: appears **zero** times in the LV-2 diff; that provenance, not the fact that the
#: assertion was red, is what licensed adding it.
_BASELINE_WS_EVENTS = {
    "activity_event",
    "approval",
    "chat_chunk",
    "chat_done",
    "chat_message",
    "chat_segment",
    "chat_status",
    "chat_thinking",
    "chat_user_message",
    "chat_variant_switch",
    "context_usage",
    "heartbeat",
    "question_card",
    "queue_pop",
    "queue_push",
    "session_agent_switch",
    "session_clear",
    "token_usage",
    "tool_call",
    "tool_result",
}

_WS_NAME_RE = re.compile(r'broadcast_ws\(\s*"([a-z_]+)"')


def test_no_new_ws_channel_was_introduced():
    src = Path(chat_runner.__file__).read_text()
    found = _WS_NAME_RE.findall(src)
    # Vacuity floor: the regex must still see EVERY call site, or an unmatched new channel
    # would read as "no new channel".
    assert len(found) == src.count("broadcast_ws("), (
        "the event-name regex no longer covers every broadcast_ws call site — fix the rail "
        "before trusting it"
    )
    assert set(found) == _BASELINE_WS_EVENTS
    # Neither LV-2 contract may mint a channel named after itself.
    assert not [n for n in set(found) if "skill" in n or "learn" in n]


@pytest.mark.asyncio
async def test_a_turn_carrying_skills_used_broadcasts_only_known_events(turn):
    """The runtime half of the same clause: the payload rides existing traffic."""
    await turn({"skill_decisions": [_decision("git-hygiene", SkillLoadState.ADMITTED, 900)]})
    names = {c.args[0] for c in turn.state.broadcast_ws.call_args_list if c.args}
    assert names, "the turn broadcast nothing — the harness, not the contract, broke"
    assert names <= _BASELINE_WS_EVENTS, f"new channel(s): {sorted(names - _BASELINE_WS_EVENTS)}"


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

    monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: tmp_path)
    loop_store.create(Loop(id="abc12345", name="n", kind="goal", task="t"))
    # Vacuity floor: BEFORE the writer runs the field is empty, so the assertion below
    # cannot be riding a hardcoded value — and this also pins that the cockpit's honest
    # answer for a never-started loop is "no key" rather than a bogus one.
    assert loop_store.get_redacted("abc12345")["session_key"] == ""

    loop_store.set_session_key("abc12345", "loop-abc12345")
    assert loop_store.get_redacted("abc12345")["session_key"] == "loop-abc12345"


def test_session_detail_does_not_clobber_skills_used_with_cls_meta():
    """`_prepare_messages` overwrites `meta` from `cls` — the assistant message must survive.

    The cockpit (and ChatPage's graft) read `skills_used` back out of this endpoint, so the
    overwrite branch sitting one line away from the payload is the risk worth pinning.
    """
    from personalclaw.dashboard.chat_utils import _prepare_messages, parse_cls_meta

    used = [{"name": "auto/release-flow", "state": "admitted", "loaded_tokens": 900}]
    out = _prepare_messages(
        [{"role": "assistant", "content": "hi", "cls": "msg msg-a", "meta": {"skills_used": used}}],
        False,
    )
    assert out[0]["meta"]["skills_used"] == used
    # Vacuity: the clobber branch is LIVE, not dead code this test merely misses. A `cls`
    # holding a JSON dict does replace `meta` wholesale — which is exactly why a plain
    # assistant message (`cls="msg msg-a"`, not JSON) has to fall through it.
    assert parse_cls_meta("msg msg-a") is None
    clobbered = _prepare_messages(
        [{"role": "assistant", "content": "hi", "cls": '{"tool": "read"}', "meta": {"gone": 1}}],
        False,
    )
    assert "gone" not in clobbered[0]["meta"], "the overwrite branch stopped firing"
