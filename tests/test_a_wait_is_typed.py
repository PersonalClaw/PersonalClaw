"""A chat that waits on the owner says on what, as a typed record, never from the model's words.

A chat's run state said it was waiting (its phase ``approval`` or ``input``, and a line), but not on
which ask, nor what would end the wait, nor where that was known from: every surface that wanted
the ask read the registries again and matched them to the chat its own way. Now the run state
carries ``waiting`` (the waiting list's ids of the asks this chat's own turn raised),
``helpers_waiting`` (those its helpers raised) and ``wait``, a :class:`~personalclaw.work_wait.Wait`
naming why it waits, what resumes it, by when, and the registry that says so. A wait is built only
from a registry: its constructor refuses any other source, and a model's words by name.

Driven through the real chat runner, the real approval and question registries and the waiting
list's own items (``waiting``); only the runtime is a fake.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from test_work_phase_classifier import CHAT, _answer, _call, _result, _scripted, _World

from personalclaw import waiting as waiting_list
from personalclaw.approval_answer import YOU
from personalclaw.instants import utc_iso
from personalclaw.llm.events import EVENT_PERMISSION_REQUEST, AgentEvent
from personalclaw.owner_questions import normalize
from personalclaw.subagent import tool_approval_id
from personalclaw.work_wait import EVIDENCE, MODEL_TEXT, REASONS, RESUMES, Resume, Wait


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)


# ── the record ─────────────────────────────────────────────────────────────────────────────


def test_each_wait_reason_names_its_evidence():
    """Each reason is known from its own registries and resumed its own ways, and from no other."""
    assert set(REASONS) == {"owner_answer", "input", "permission", "children", "dependency"}
    for reason, (sources, resumes) in REASONS.items():
        for source in sources:
            for kind in resumes:
                made = Wait(reason, Resume(kind, "approval:a1"), None, source)
                assert made.as_dict() == {
                    "reason": reason,
                    "resume_condition": {"kind": kind, "ref": "approval:a1"},
                    "deadline_at": None,
                    "evidence_source": source,
                }
        for source in set(EVIDENCE) - set(sources):
            with pytest.raises(ValueError, match=source):
                Wait(reason, Resume(next(iter(resumes)), "x"), None, source)
        for kind in set(RESUMES) - set(resumes):
            with pytest.raises(ValueError, match=kind):
                Wait(reason, Resume(kind, "x"), None, next(iter(sources)))
    with pytest.raises(ValueError, match="busy"):
        Wait("busy", Resume("answered", "x"), None, "approval_registry")
    with pytest.raises(ValueError, match="answered"):
        Wait("owner_answer", Resume("answered", ""), None, "approval_registry")


def test_a_wait_from_model_text_is_refused():
    with pytest.raises(ValueError, match="model's words"):
        Wait("input", Resume("answered", "question:q1"), None, MODEL_TEXT)
    with pytest.raises(ValueError, match="nowhere"):
        Wait("input", Resume("answered", "question:q1"), None, "nowhere")


# ── the chat's run state ───────────────────────────────────────────────────────────────────


def _listed(w: _World) -> set[str]:
    """The waiting list's own ids for what names the chat (``GET /api/waiting``'s items)."""
    snap = waiting_list.taken(w.state)
    items = waiting_list._approvals(snap.approvals, snap.window, waiting_list._now())
    items += waiting_list._questions(snap.questions, snap.window, waiting_list._now())
    return {item["id"] for item in items if item["session"] == CHAT}


def _asks_once() -> AsyncIterator[AgentEvent]:
    return _scripted(
        _call("shell", "t1", tool_input={"command": "rm -rf build"}),
        AgentEvent(
            kind=EVENT_PERMISSION_REQUEST,
            title="shell",
            request_id="r1",
            tool_call_id="t1",
            tool_input='{"command": "rm -rf build"}',
        ),
        _result("t1"),
        *_answer(),
    )()


@pytest.mark.asyncio
async def test_an_answered_ask_clears_the_wait(tmp_path):
    w = _World(tmp_path, _asks_once)
    w.session._trust = False
    task = w.start()
    asked = await w.until("approval")

    (entry,) = w.state._pending_approvals.values()
    ref = f"approval:{entry['id']}"
    assert asked["waiting"] == [ref] and asked["helpers_waiting"] == []
    assert set(asked["waiting"]) == _listed(w), "the ids are the waiting list's own"
    window = w.state.approval_window_secs()
    assert asked["wait"] == {
        "reason": "owner_answer",
        "resume_condition": {"kind": "answered", "ref": ref},
        "deadline_at": utc_iso(float(entry["ts"]) + window) if window > 0 else None,
        "evidence_source": "approval_registry",
    }

    w.state.decide_session_approval(w.session, "r1", "approved", by=YOU)
    await asyncio.wait_for(task, timeout=10)
    after = w.phases()[w.phases().index(asked) + 1]
    assert after["phase"] == "tool"
    assert (after["waiting"], after["helpers_waiting"], after["wait"]) == ([], [], None)
    assert w.phases()[-1]["wait"] is None


@pytest.mark.asyncio
async def test_waiting_names_this_turns_approvals_and_questions(tmp_path):
    """The turn's own question is ``waiting``; a helper's approval is ``helpers_waiting``, and the
    phase and its wait are the newest ask's."""
    gate = asyncio.Event()
    questions = normalize(
        {"questions": [{"question": "Which branch?", "options": [{"label": "main"}]}]}
    )

    async def stream() -> AsyncIterator[AgentEvent]:
        yield _call("ask_user", "t1")
        await w.state.owner_questions.ask(f"dashboard:{CHAT}", questions, asked_by="The agent")
        await gate.wait()
        yield _result("t1")
        for event in _answer():
            yield event

    w = _World(tmp_path, stream)
    task = w.start()
    asked = await w.until("input")
    (card,) = w.state.owner_questions.pending_for(CHAT)
    own = f"question:{card['id']}"
    assert asked["waiting"] == [own] and asked["helpers_waiting"] == []
    assert asked["wait"]["reason"] == "input"
    assert asked["wait"]["resume_condition"] == {"kind": "answered", "ref": own}
    assert asked["wait"]["evidence_source"] == "question_registry"
    window = w.state.approval_window_secs()
    assert asked["wait"]["deadline_at"] == utc_iso(float(card["ts"]) + window)

    helper_ask = tool_approval_id("h1", "r9")
    answered = asyncio.ensure_future(
        w.state.request_approval(helper_ask, "subagent", "shell", session=CHAT)
    )
    both = await w.until("approval")
    assert both["waiting"] == [own]
    assert both["helpers_waiting"] == [f"approval:{helper_ask}"]
    assert set(both["waiting"] + both["helpers_waiting"]) == _listed(w)
    assert both["wait"]["reason"] == "owner_answer"
    assert both["wait"]["resume_condition"]["ref"] == f"approval:{helper_ask}"

    assert w.state.resolve_approval(helper_ask, False, by=YOU)
    assert await asyncio.wait_for(answered, timeout=10) is False
    back = w.phases()[-1]
    assert back["phase"] == "input"
    assert (back["waiting"], back["helpers_waiting"]) == ([own], [])

    w.state.owner_questions.answer(CHAT, card["id"], answers=[{"selected": [0]}])
    gate.set()
    await asyncio.wait_for(task, timeout=10)
    last = w.phases()[-1]
    assert (last["waiting"], last["helpers_waiting"], last["wait"]) == ([], [], None)


@pytest.mark.asyncio
async def test_a_question_ends_unanswered_at_its_waits_deadline(tmp_path):
    """The deadline is when the question registry ends the ask: the owner's window after it was
    asked, after which it is over, and so is the wait."""
    questions = normalize(
        {"questions": [{"question": "Which branch?", "options": [{"label": "a"}]}]}
    )
    ended: list[str] = []

    async def stream() -> AsyncIterator[AgentEvent]:
        yield _call("ask_user", "t1")
        outcome = await w.state.owner_questions.ask(
            f"dashboard:{CHAT}", questions, asked_by="The agent"
        )
        ended.append(outcome.kind)
        yield _result("t1")
        for event in _answer():
            yield event

    w = _World(tmp_path, stream)
    w.state.approval_window_secs = lambda: 0.5
    task = w.start()
    asked = await w.until("input")
    (card,) = w.state.owner_questions.pending_for(CHAT)
    assert asked["wait"]["deadline_at"] == utc_iso(float(card["ts"]) + 0.5)

    await asyncio.wait_for(task, timeout=10)
    assert ended == ["expired"], "nobody answered within the window, so the ask ended"
    assert w.state.owner_questions.pending_for(CHAT) == []
    assert w.phases()[-1]["wait"] is None
