"""A chat turn's typed model: messages that own parts, and one state machine per part (§3.3-§3.5).

``personalclaw.turns`` is the one definition. A tool part moves only along shared contracts §3.4's
edges, through ``advance``; the approval, question, sign-in and helper parts move by their own
tables; a terminal part never moves. Every type round-trips through ``to_dict`` and a strict
``from_dict`` that names a key it does not know. §3.5's definitions and the ending summary read
the parts the same way for every surface.
"""

from __future__ import annotations

import dataclasses
import itertools
import typing

import pytest
from table_rows import row

from personalclaw import turns

T0 = "2026-10-04T15:17:09+00:00"
T1 = "2026-10-04T15:17:10+00:00"
TOOL_STATES = typing.get_args(turns.ToolState)

#: §3.4's edges, written out again here so the table in the module is checked, not copied.
LEGAL = {
    ("pending", "running"),
    ("pending", "refused"),
    ("pending", "cancelled"),
    ("pending", "unknown"),
    ("running", "completed"),
    ("running", "failed"),
    ("running", "unknown"),
    ("running", "cancelled"),
}
#: The two edges that hold for one effect only: a pending call ends unknown only as a write (an
#: agent CLI may have started it), and a running call ends cancelled only as a read.
ONLY_FOR = {("pending", "unknown"): "write", ("running", "cancelled"): "read"}


def _tool(state: str = "pending", effect: str = "write", **more: typing.Any) -> turns.ToolPart:
    """A tool part already in *state*, built through ``advance`` as any writer must."""
    part = turns.ToolPart(
        id="p_1",
        at=T0,
        updated_at=T0,
        tool_call_id="call-1",
        tool="write_file",
        title="Write notes.md",
        input="notes.md",
        risk="caution" if effect == "write" else "safe",
        effect=typing.cast(turns.Effect, effect),
        **more,
    )
    path = {
        "pending": [],
        "running": ["running"],
        "refused": ["refused"],
        "cancelled": ["cancelled"],
        "unknown": ["running", "unknown"],
    }.get(state, ["running", state])
    for step in path:
        part = turns.advance(part, step, now=T0)
    return part


def test_a_tool_part_moves_only_along_its_edges(subtests) -> None:
    for (here, there), effect in itertools.product(
        itertools.product(TOOL_STATES, TOOL_STATES), ("read", "write")
    ):
        with row(subtests, edge=f"{here}->{there}", effect=effect):
            part = _tool(here, effect)
            legal = (here, there) in LEGAL and ONLY_FOR.get((here, there), effect) == effect
            if legal:
                moved = turns.advance(part, there, now=T1)
                assert moved.state == there and moved.updated_at == T1
                assert part.state == here, "advance returns a new part and leaves the old one"
            else:
                with pytest.raises(turns.IllegalTransition) as err:
                    turns.advance(part, there, now=T1)
                assert (err.value.from_state, err.value.to_state) == (here, there)


def test_a_terminal_part_never_moves(subtests) -> None:
    for state in turns.TOOL_TERMINAL:
        for there in TOOL_STATES:
            with row(subtests, state=state, to=there):
                with pytest.raises(turns.IllegalTransition):
                    turns.advance(_tool(state), there)
                assert turns.is_terminal(_tool(state))


def test_the_other_parts_move_by_their_own_tables(subtests) -> None:
    made = {"id": "p_9", "at": T0, "updated_at": T0}
    for part, endings in (
        (turns.ApprovalPart(**made), ("approved", "rejected", "expired", "cancelled")),
        (turns.QuestionPart(**made), ("answered", "skipped", "expired", "cancelled")),
        (turns.SignInPart(**made), ("signed_in", "skipped", "expired", "cancelled")),
    ):
        for ending in endings:
            with row(subtests, kind=part.KIND, to=ending):
                ended = turns.advance(part, ending, now=T1)
                assert ended.state == ending and turns.is_terminal(ended)
                with pytest.raises(turns.IllegalTransition):
                    turns.advance(ended, "pending")
        with row(subtests, kind=part.KIND, to="running"):
            with pytest.raises(turns.IllegalTransition):
                turns.advance(part, "running")
    helper = turns.HelperPart(**made, work="subagent:a1")
    waiting = turns.advance(turns.advance(helper, "running", now=T0), "waiting_on_limit", now=T1)
    running = turns.advance(waiting, "running", now=T1)
    assert running.started_at == T0, "a helper that waited and runs again keeps when it began"
    stopped = turns.advance(running, "stopped", now=T1)
    assert stopped.ended_at == T1 and turns.is_terminal(stopped)
    with pytest.raises(turns.IllegalTransition):
        turns.advance(stopped, "running")


def test_advance_stamps_when_a_part_started_and_ended() -> None:
    running = turns.advance(_tool(), "running", now=T0)
    assert (running.started_at, running.ended_at, running.updated_at) == (T0, None, T0)
    done = turns.advance(running, "completed", now=T1, result=turns.ToolResult(text="ok"))
    assert (done.started_at, done.ended_at, done.updated_at) == (T0, T1, T1)
    assert done.result == turns.ToolResult(text="ok")


def test_advance_sets_only_a_parts_own_facts() -> None:
    with pytest.raises(ValueError, match="no fact 'answer'"):
        turns.advance(_tool(), "running", answer="approved")
    with pytest.raises(ValueError, match="no fact 'id'"):
        turns.advance(_tool(), "running", id="p_2")
    with pytest.raises(turns.IllegalTransition):
        turns.advance(turns.TextPart(id="p_3", at=T0, updated_at=T0), "running")


def _every_kind() -> turns.Message:
    """A message holding one part of every kind, each with a field set away from its default."""
    made = {"at": T0, "updated_at": T1}
    ref = turns.Ref(kind="attachment", id="att_1")
    parts = (
        turns.TextPart(id="p_a", text="Here is the plan.", **made),
        turns.ReasoningPart(id="p_b", text="thinking", **made),
        _tool("completed", "read", detail="one file", notes=("checked",)),
        turns.ApprovalPart(id="p_c", request_id="req-1", answers=({"key": "approved"},), **made),
        turns.QuestionPart(id="p_d", question_id="q-1", questions=({"text": "Which?"},), **made),
        turns.SignInPart(id="p_e", need="more_access", scopes=("mail.read",), **made),
        turns.NoticePart(id="p_f", notice="cut_short", text="Cut short.", level="warn", **made),
        turns.FilePart(id="p_g", ref=ref, direction="out", name="a.png", size=12, **made),
        turns.MentionPart(
            id="p_h", target="skill", ref="pdf", outcome="refused", why="budget", **made
        ),
        turns.PlanPart(id="p_i", entries=({"text": "one", "state": "pending"},), **made),
        turns.HelperPart(id="p_j", work="subagent:a1", mode="background", **made),
        turns.CompactionPart(id="p_k", form="digest", summarized=3, **made),
    )
    return turns.Message(
        id="msg_1",
        session="dashboard:chat-1",
        role="assistant",
        ts=T0,
        parts=parts,
        parent_id="msg_0",
        attempt=1,
        shown=True,
        outcome="complete",
        usage=turns.Usage(provider="p", model="m", input_tokens=3, cost_usd=0.5),
        facts=turns.MessageFacts(summary="Wrote a plan.", file_changes=({"path": "a"},)),
    )


def test_every_kind_round_trips_through_its_dict() -> None:
    message = _every_kind()
    assert {p.KIND for p in message.parts} == set(turns.PART_KINDS)
    data = message.to_dict()
    assert turns.Message.from_dict(data) == message
    assert [p["kind"] for p in data["parts"]] == [p.KIND for p in message.parts]


def test_an_empty_optional_field_is_left_out() -> None:
    data = turns.TextPart(id="p_1", at=T0, updated_at=T0, text="").to_dict()
    assert data == {"kind": "text", "id": "p_1", "at": T0, "updated_at": T0, "text": ""}
    tool = _tool().to_dict()
    assert "result" not in tool and "notes" not in tool and "detail" not in tool
    assert tool["started_at"] is None, "a required field is written even while it is empty"


def test_from_dict_refuses_an_unknown_key(subtests) -> None:
    data = _every_kind().to_dict()
    planted = {
        "a message's": {**data, "colour": "red"},
        "a part's": {**data, "parts": [{**data["parts"][0], "colour": "red"}]},
        "a result's": {
            **data,
            "parts": [*data["parts"][:2], {**data["parts"][2], "result": {"colour": "red"}}],
        },
        "a fact's": {**data, "facts": {"colour": "red"}},
    }
    for where, bad in planted.items():
        with row(subtests, where=where), pytest.raises(ValueError, match="'colour'"):
            turns.Message.from_dict(bad)
    with pytest.raises(ValueError, match="no part kind 'sketch'"):
        turns.part_from_dict({"kind": "sketch", "id": "p_1", "at": T0, "updated_at": T0})
    with pytest.raises(ValueError, match="'shouting' is not one of"):
        turns.part_from_dict({**_tool().to_dict(), "state": "shouting"})


def test_no_part_has_a_field_of_its_own_called_kind() -> None:
    """Every part carries ``kind`` as its own kind, so a part's field of that name would be
    written over (a sign-in's need and a compaction's form are named for what they say)."""
    clashing = [
        cls.__name__
        for cls in turns.PART_KINDS.values()
        if "kind" in {f.name for f in dataclasses.fields(cls)}
    ]
    assert clashing == []


def test_the_definitions_read_a_parts_state_and_effect() -> None:
    write_done, read_done = _tool("completed", "write"), _tool("completed", "read")
    assert turns.may_have_run(write_done) and turns.completed_side_effect(write_done)
    assert not turns.completed_side_effect(read_done)
    assert turns.failed_side_effect(_tool("failed", "write"))
    assert not turns.failed_side_effect(_tool("failed", "read"))
    assert turns.outcome_unknown(_tool("unknown"))
    for state, ran in (("pending", False), ("refused", False), ("cancelled", False)):
        assert turns.may_have_run(_tool(state)) is ran
    assert all(turns.may_have_run(_tool(s)) for s in ("running", "completed", "failed", "unknown"))


def test_a_turn_left_work_behind_only_when_it_ended_badly_after_a_call_may_have_run() -> None:
    def answer(outcome: turns.Outcome, *parts: turns.Part) -> turns.Message:
        return turns.Message(
            id="msg_2", session="s", role="assistant", ts=T0, parts=parts, outcome=outcome
        )

    cut = turns.NoticePart(id="p_n", at=T0, updated_at=T0, notice="cut_short")
    assert turns.left_work_behind(answer("interrupted", _tool("completed")))
    assert turns.left_work_behind(answer("error", _tool("unknown")))
    assert turns.left_work_behind(answer("complete", cut, _tool("failed")))
    assert not turns.left_work_behind(answer("interrupted", _tool("refused")))
    assert not turns.left_work_behind(answer("complete", _tool("completed")))
    assert not turns.left_work_behind(answer("stopped", _tool("completed")))


def test_ending_summary_counts_what_survived() -> None:
    failed, unknown = _tool("failed", "write"), _tool("unknown", "write")
    message = turns.Message(
        id="msg_3",
        session="s",
        role="assistant",
        ts=T0,
        outcome="interrupted",
        parts=(
            _tool("completed", "write"),
            _tool("completed", "write"),
            _tool("completed", "read"),
            failed,
            unknown,
            _tool("refused", "write"),
            turns.TextPart(id="p_t", at=T0, updated_at=T0, text="Done with two."),
        ),
    )
    assert turns.ending_summary(message) == turns.EndingSummary(
        completed=2, failed=(failed,), unknown=(unknown,), reads=1, wrote_text=True
    )
    silent = dataclasses.replace(message, parts=(_tool("cancelled", "read"),))
    assert turns.ending_summary(silent) == turns.EndingSummary()
