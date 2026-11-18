"""A tool call's phase frame says whether the call reads, edits or runs, and nothing more of it.

The chat's ``work_phase`` frame said ``tool`` for every call that was not a search or a plan, with
the call's card title as its line, so a surface could not tell a file being read from one being
changed or a command being run, and drew all three alike. Now the frame's ``act`` names which of
the three the call is (``read``, ``edit`` or ``run``), only while the turn is in ``tool``, and
``null`` when the call does not say. It is read from what the call declares: an agent CLI's kind,
or the name of one of PersonalClaw's own runtime's tools. It is the act alone: never a path, a
command or any other part of what the call was given.

Every turn runs through the REAL chat runner; only the runtimes are fakes.
"""

from __future__ import annotations

import asyncio

import pytest
from table_rows import row
from test_work_phase_classifier import _answer, _call, _result, _scripted, _World

from personalclaw.approval_answer import YOU
from personalclaw.llm.events import EVENT_PERMISSION_REQUEST, AgentEvent
from personalclaw.work_phase import Act, WorkPhase, act_of_call, classify_call

#: A call's card title and its declared kind, and the act it names (``None``: it does not say).
ACTS = [
    # An agent CLI declares a kind per call.
    ("Read src/app.py", "read", "read"),
    ("Fetch the release notes", "fetch", "read"),
    ("Edit src/app.py", "edit", "edit"),
    ("Delete build/", "delete", "edit"),
    ("Move a.txt to b.txt", "move", "edit"),
    ("Run: npm test", "execute", "run"),
    ("Something else", "other", None),
    # A declared kind wins over a name it happens to share with one of the own runtime's tools.
    ("read_file", "execute", "run"),
    ("bash", "edit", "edit"),
    # PersonalClaw's own runtime declares none: its tools are known by name.
    ("read_file", "", "read"),
    ("list_dir", "", "read"),
    ("repo_map", "", "read"),
    ("write_file", "", "edit"),
    ("edit_file", "", "edit"),
    ("bash", "", "run"),
    ("tool_result_get", "", None),
    ("mcp__notes__append", "", None),
]


def test_a_tool_call_names_its_act_from_what_it_declares(subtests):
    assert len(ACTS) == 17, "a call was dropped from the table"
    for title, kind, act in ACTS:
        with row(subtests, title=title, kind=kind):
            assert act_of_call(title, kind) == (Act(act) if act else None)
            assert classify_call(title, kind)[0] is WorkPhase.TOOL


def test_a_search_or_a_plan_names_no_act(subtests):
    steps = [("grep", ""), ("knowledge_search", ""), ("Find x", "search"), ("Plan", "think")]
    for title, kind in steps:
        with row(subtests, title=title, kind=kind):
            assert classify_call(title, kind)[0] is not WorkPhase.TOOL
            assert act_of_call(title, kind) is None


def test_an_act_is_one_of_three_words():
    assert {a.value for a in Act} == {"read", "edit", "run"}


@pytest.mark.asyncio
async def test_a_native_turn_says_each_calls_act_while_it_runs_and_none_after(tmp_path):
    secret_path = "/home/alice/private/notes.txt"
    w = _World(
        tmp_path,
        _scripted(
            _call("read_file", "t1", tool_input={"path": secret_path}),
            _result("t1"),
            _call("edit_file", "t2", tool_input={"path": secret_path, "old": "a", "new": "b"}),
            _result("t2"),
            _call("bash", "t3", tool_input={"command": f"cat {secret_path}"}),
            _result("t3"),
            _call("grep", "t4", tool_input={"pattern": "x"}),
            _result("t4"),
            *_answer(words=3),
        ),
    )
    await w.turn()
    said = [(f["phase"], f["line"], f["act"]) for f in w.phases()]
    steps = [s for s in said if s[0] in ("tool", "searching")]
    assert steps == [
        ("tool", "read_file", "read"),
        ("tool", "edit_file", "edit"),
        ("tool", "bash", "run"),
        ("searching", "Searching the files", None),
    ]
    assert all(act is None for phase, _, act in said if phase != "tool"), said
    # The act is the word alone: nothing the call was given rides on the frame for it.
    assert all(secret_path not in str(f["act"]) for f in w.phases())


@pytest.mark.asyncio
async def test_an_agent_cli_turn_says_its_declared_kinds_act(tmp_path):
    w = _World(
        tmp_path,
        _scripted(
            _call("Read README.md", "c1", "read"),
            _result("c1"),
            _call("Edit README.md", "c2", "edit"),
            _result("c2"),
            _call("Run: make", "c3", "execute"),
            _result("c3"),
            _call("Ask the oracle", "c4", "other"),
            _result("c4"),
            *_answer(),
        ),
        acp=True,
    )
    await w.turn()
    tools = [(f["line"], f["act"]) for f in w.phases() if f["phase"] == "tool"]
    assert tools == [
        ("Read README.md", "read"),
        ("Edit README.md", "edit"),
        ("Run: make", "run"),
        ("Ask the oracle", None),
    ]


@pytest.mark.asyncio
async def test_a_call_that_waits_on_the_owners_ok_names_no_act_while_it_waits(tmp_path):
    async def asks():
        yield _call("bash", "t1", tool_input={"command": "ls"})
        yield AgentEvent(
            kind=EVENT_PERMISSION_REQUEST,
            title="bash",
            request_id="r1",
            tool_call_id="t1",
            tool_input='{"command": "ls"}',
        )
        yield _result("t1")
        for event in _answer():
            yield event

    w = _World(tmp_path, lambda: asks())
    w.session._trust = False
    task = w.start()
    waiting = await w.until("approval")
    assert waiting["act"] is None
    w.state.decide_session_approval(w.session, "r1", "approved", by=YOU)
    await asyncio.wait_for(task, timeout=10)
    # Before the ask and once it is answered, the call is said to run; never while it waits.
    tools = [f["act"] for f in w.phases() if f["phase"] == "tool"]
    assert tools and set(tools) == {"run"}, w.phases()
    assert w.phases()[-1]["act"] is None
