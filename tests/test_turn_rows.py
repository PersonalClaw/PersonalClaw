"""Today's transcript rows read as the typed turn (shared contracts §3.10), over a fixture corpus.

``dashboard.turn_rows.from_rows`` reads a chat's rows, as its file or its buffer holds them, as
``turns.Message`` values: a turn from each row that starts one (``turn_source.by_turn``), its
answer as attempts of typed parts, each tool call in the state its rows say it reached. The corpus
in ``tests/fixtures/transcripts/`` holds every role and shape a chat's rows take today. A path
under the test's home and the notices the chat runner writes are filled in as the corpus is read
(``@HOME@``, ``@INTERRUPTED@``, ``@CUT_SHORT@``), so it never copies a notice's words.

``turns.dialogue`` gives the typed messages back as text, and over the corpus it is what
``history.model_view`` gives today, entry for entry, so a background summary's digest and every
history a model is handed stay the same. The one shape where the two differ is pinned on its own
(:func:`test_a_switched_variant_of_a_multi_segment_answer_reads_as_its_own_attempt`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from personalclaw import ids, turns
from personalclaw.dashboard import turn_rows
from personalclaw.history import model_view
from personalclaw.http_errors import HTTP_ERROR_CODES
from personalclaw.turn_source import by_turn, starts_a_turn

CORPUS = Path(__file__).parent / "fixtures" / "transcripts"
SESSION = "dashboard:corpus"
FIXTURES = sorted(p.name for p in CORPUS.glob("*.jsonl"))
#: The one shape whose dialogue is not today's model view (see its test).
DIFFERS = "switched_variant.jsonl"

#: What each tool of the corpus declares, as a runtime's definitions would say.
DECLARED = {
    "read_file": "safe",
    "grep": "safe",
    "ask_user": "safe",
    "write_file": "caution",
    "edit_file": "caution",
    "send_message": "caution",
    "bash": "destructive",
}


def _declared(title: str, _kind: str, _input: object) -> object:
    return DECLARED.get(title, "")


def _load(name: str) -> list[dict[str, Any]]:
    from personalclaw.config.loader import config_dir
    from personalclaw.dashboard.chat_runner import TURN_CUT_SHORT_NOTICE, TURN_INTERRUPTED_NOTICES

    text = (CORPUS / name).read_text(encoding="utf-8")
    filled = {
        "@HOME@": str(config_dir()),
        "@INTERRUPTED@": next(iter(TURN_INTERRUPTED_NOTICES.values())),
        "@CUT_SHORT@": TURN_CUT_SHORT_NOTICE,
    }
    for mark, value in filled.items():
        text = text.replace(mark, json.dumps(value)[1:-1])
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _read(name: str, declared: turn_rows.Declared | None = None) -> list[turns.Message]:
    return turn_rows.from_rows(_load(name), session_key=SESSION, declared=declared)


def _shape(message: turns.Message) -> tuple[str, str, tuple[str, ...]]:
    """A message as the shape tests pin it: its role, what started it (a user message) or its
    attempt (``*`` when shown), and its parts' kinds."""
    said = message.dispatch if message.role == "user" else f"{message.attempt}{'*' * message.shown}"
    return (message.role, said, tuple(p.KIND for p in message.parts))


def _texts(message: turns.Message) -> list[str]:
    return [p.text for p in message.parts if isinstance(p, turns.TextPart)]


def _tools(messages: list[turns.Message]) -> dict[str, turns.ToolPart]:
    return {p.tool_call_id: p for m in messages for p in m.parts if isinstance(p, turns.ToolPart)}


SHAPES = {
    "chat.jsonl": [
        ("user", "message", ("text",)),
        ("assistant", "1*", ("text",)),
        ("user", "message", ("text",)),
        ("assistant", "1*", ("text",)),
        ("assistant", "2", ("text",)),
        ("user", "message", ("text",)),
        ("assistant", "1", ("text",)),
        ("assistant", "2", ("text",)),
        ("assistant", "3*", ("text",)),
    ],
    "tools.jsonl": [
        ("user", "message", ("text",)),
        (
            "assistant",
            "1*",
            ("text", "tool", "tool", "tool", "tool", "tool", "approval", "tool", "approval")
            + ("tool", "tool", "approval", "tool", "tool", "question", "tool", "approval", "tool")
            + ("notice", "text"),
        ),
    ],
    "endings.jsonl": [
        ("user", "message", ("text",)),
        ("assistant", "1*", ("text", "notice")),
        ("user", "message", ("text",)),
        ("assistant", "1*", ("notice", "tool")),
        ("user", "message", ("text",)),
        ("assistant", "1*", ("text", "notice")),
        ("user", "message", ("text",)),
        ("assistant", "1*", ("tool", "notice")),
        ("user", "message", ("text",)),
        ("assistant", "1*", ("notice", "notice", "text")),
        ("user", "message", ("text",)),
        ("assistant", "1*", ("text",)),
    ],
    "dispatch.jsonl": [
        ("user", "message", ("text",)),
        ("assistant", "1*", ("text", "tool", "text")),
        ("user", "message", ("text",)),
        ("user", "inject", ("text",)),
        ("assistant", "1*", ("text",)),
        ("user", "subagent", ("text",)),
        ("assistant", "1*", ("text",)),
        ("user", "message", ("text",)),
        ("assistant", "1*", ("text",)),
        ("user", "goal", ("text",)),
        ("assistant", "1*", ("text",)),
        ("user", "message", ("text",)),
        ("assistant", "1*", ("text",)),
        ("user", "message", ("text",)),
        ("assistant", "1*", ("text",)),
        ("user", "message", ("text",)),
        ("assistant", "1*", ("text",)),
    ],
    "files.jsonl": [
        ("user", "message", ("text", "file", "file", "mention", "mention", "mention")),
        ("assistant", "1*", ("text", "file", "text")),
        ("user", "message", ("text",)),
        ("assistant", "1*", ("text",)),
    ],
    "imported.jsonl": [
        ("assistant", "1*", ("text",)),
        ("user", "message", ("text",)),
        ("assistant", "1*", ("text",)),
    ],
    "switched_variant.jsonl": [
        ("user", "message", ("text",)),
        ("assistant", "1*", ("text",)),
        ("assistant", "2", ("text", "tool", "text")),
    ],
}


def test_from_rows_reads_every_role() -> None:
    roles = {row.get("role") for name in FIXTURES for row in _load(name)} - {None}
    assert roles == turn_rows.ROLES, "the corpus holds every role the reading reads, and no other"
    assert set(SHAPES) == set(FIXTURES)
    for name in FIXTURES:
        messages = _read(name, _declared)
        assert [_shape(m) for m in messages] == SHAPES[name], name
        for message in messages:
            data = json.loads(json.dumps(message.to_dict()))
            assert turns.Message.from_dict(data) == message, (name, message.id)


def test_stable_ids_are_the_same_on_every_read() -> None:
    for name in FIXTURES:
        first, again = _read(name), _read(name, _declared)
        made = [(m.id, [p.id for p in m.parts]) for m in first]
        assert made == [(m.id, [p.id for p in m.parts]) for m in again], name
        every = [m.id for m in first] + [p.id for m in first for p in m.parts]
        assert len(every) == len(set(every)), f"{name}: an id is given twice"
        assert all(ids.is_id("msg", m.id) for m in first), name
        assert all(ids.is_id("p", p.id) for m in first for p in m.parts), name
    sent = _read("dispatch.jsonl")[0]
    assert sent.id == "msg_01jab3c4d5e6f7g8h9jkmnpqrs", "a row's own message id is its id"


def test_an_unrecorded_effect_reads_from_the_declarations() -> None:
    tools = _tools(_read("tools.jsonl", _declared))
    said = {cid: (p.state, p.effect, p.risk) for cid, p in tools.items()}
    assert said == {
        "c1": ("completed", "read", "safe"),  # its row records them
        "c2": ("completed", "write", "caution"),
        "c3": ("failed", "write", "caution"),  # read from what write_file declares
        "c4": ("unknown", "write", "destructive"),  # never done: a write's outcome is unknown
        "c5": ("cancelled", "read", "safe"),  # never done: a read did nothing
        "c6": ("completed", "write", "caution"),
        "c7": ("unknown", "write", "caution"),
        "c8": ("refused", "write", "unchecked"),  # her Deny ended it; the screen cannot vouch
        "c9": ("refused", "write", "caution"),  # a gate's line says it was not run
        "c10": ("completed", "read", "safe"),
        "c11": ("completed", "write", "caution"),
        "c12": ("completed", "read", "safe"),  # its approval card established it reads
    }
    assert tools["c2"].notes == ("Same edit as the step before.",)
    assert tools["c9"].notes == ("Not run: the chat's task mode is Ask.",)
    assert tools["c8"].title == "bash" and tools["c8"].input == '{"command": "git push --force"}'
    assert tools["c3"].error == {"code": "ERR_PERMISSION", "what": "todo.md is read-only"}
    assert tools["c1"].result == turns.ToolResult(text="# Notes")
    assert tools["c1"].purpose == "Read the notes"
    assert tools["c11"].ungated.startswith("Ran without asking")
    assert tools["c1"].family == "read" and tools["c4"].family == "command"


def test_an_undeclared_call_is_a_write() -> None:
    tools = _tools(_read("tools.jsonl"))
    assert (tools["c3"].risk, tools["c3"].effect, tools["c3"].state) == (
        "unchecked",
        "write",
        "failed",
    )
    assert (tools["c5"].risk, tools["c5"].effect, tools["c5"].state) == (
        "unchecked",
        "write",
        "unknown",
    ), "a read nothing vouches for is a write whose outcome is unknown"
    assert (tools["c1"].effect, tools["c12"].effect) == ("read", "read"), "what rows record holds"


def test_approvals_and_questions_read_from_their_rows() -> None:
    answer = _read("tools.jsonl", _declared)[1]
    approvals = {p.request_id: p for p in answer.parts if isinstance(p, turns.ApprovalPart)}
    tools = _tools([answer])
    assert {r: (a.state, a.answer, a.decided_by) for r, a in approvals.items()} == {
        "r6": ("approved", "trust", "you"),
        "r7": ("pending", "", ""),
        "r8": ("rejected", "rejected", "you"),
        "r12": ("approved", "approved", "you"),
    }
    assert approvals["r6"].tool_part_id == tools["c6"].id
    assert tools["c6"].approval_part_id == approvals["r6"].id
    assert approvals["r6"].brief == {
        "risk": "caution",
        "reach": "In notes/",
        "confirms": ["Write notes/plan.md"],
    }
    assert (approvals["r6"].grant_agent, approvals["r7"].deny_effect) == (
        "personal",
        "Denying ends the turn.",
    )
    (question,) = [p for p in answer.parts if isinstance(p, turns.QuestionPart)]
    assert (question.question_id, question.state, question.tool_part_id) == (
        "q1",
        "answered",
        tools["c10"].id,
    )
    assert question.answers == ({"selected": [0], "other": ""},)
    (notice,) = [p for p in answer.parts if isinstance(p, turns.NoticePart)]
    assert notice.text.startswith("The loop breaker"), "a line about no call is said on the turn"


def test_a_turns_ending_reads_from_its_rows() -> None:
    from personalclaw.dashboard.chat_runner import TURN_CUT_SHORT_NOTICE

    answers = [m for m in _read("endings.jsonl") if m.role == "assistant"]
    assert [m.outcome for m in answers] == [
        "interrupted",
        "error",
        "stopped",
        "stopped",
        "complete",
        "interrupted",
    ]
    interrupted, failed, stopped, cut, complete, recorded = answers
    assert [p.notice for p in interrupted.parts if isinstance(p, turns.NoticePart)] == [
        "interrupted"
    ]
    assert failed.error == turns.MessageError(
        code="turn_failed",
        message="The model provider failed: 500 Internal Server Error",
        retryable=True,
    )
    assert "turn_failed" in HTTP_ERROR_CODES, "a turn's error carries a registered wire code"
    (earlier,) = [p for p in failed.parts if isinstance(p, turns.NoticePart)]
    assert (earlier.text, earlier.level) == ("The model was busy; trying again.", "warn")
    assert [p.notice for p in stopped.parts if isinstance(p, turns.NoticePart)] == ["stopped"]
    (said,) = [p for p in cut.parts if isinstance(p, turns.NoticePart)]
    assert (said.notice, said.text) == ("cut_short", TURN_CUT_SHORT_NOTICE)
    assert turns.left_work_behind(cut), "cut short after a write completed"
    assert [p.notice for p in complete.parts if isinstance(p, turns.NoticePart)] == [
        "info",
        "info",
    ]
    assert recorded.outcome == "interrupted", "the start row's own record of how it ended"
    assert all(m.settled_at for m in answers)
    every_text = [t for m in _read("endings.jsonl") for t in _texts(m)]
    assert "One more thing" not in every_text, "a queued placeholder is dropped"


def test_a_nudge_row_reads_as_a_goal_cycle() -> None:
    starts = [m for m in _read("dispatch.jsonl") if m.role == "user" and not m.facts.steered_into]
    by_text = {_texts(m)[0]: m for m in starts}
    assert by_text["Keep going toward the goal."].dispatch == "goal"
    inject = by_text["Daily digest: three new items."]
    assert (inject.dispatch, inject.facts.injected_label) == ("inject", "Daily digest")
    report = by_text["Helper report: the survey is summarized."]
    assert report.dispatch == "subagent"
    assert report.facts.asked_for_by == {
        "source_thread": "T042",
        "source_user": "U0JONAS",
        "source_channel": "team-chat",
    }
    assert by_text["Your helpers failed: the survey could not be read."].dispatch == "message"
    merged = by_text["Two notes\n\nand a third"]
    assert merged.source == turns.Source()
    assert [m["source"] for m in merged.facts.merged] == [
        {"source_thread": "dashboard", "source_user": "dashboard"},
        {"source_thread": "T042", "source_user": "U0JONAS", "source_channel": "team-chat"},
    ]
    outside = by_text["The nightly run found two failing checks."]
    assert outside.facts.from_outside == {
        "what": "A scheduled run's result",
        "source": "schedule:nightly",
        "source_type": "schedule",
        "source_id": "run-7",
    }
    channel = by_text["what's on today?"]
    assert channel.source == turns.Source(
        source_thread="T042", source_user="U0JONAS", source_channel="team-chat"
    )


def test_a_steer_row_reads_as_a_steer() -> None:
    messages = _read("dispatch.jsonl")
    start, answer, steer = messages[0], messages[1], messages[2]
    assert (steer.role, steer.dispatch, steer.facts.steered_into) == ("user", "message", start.id)
    assert steer.facts.after_step == 1, "it was taken after the answer's first words"
    assert steer.facts.pastes == ({"seq": 1, "lines": 1, "content": "totals"},)
    assert answer.parent_id == start.id
    assert _texts(answer) == ["Working on it.", "Totals only, then."]
    assert not any(m.parent_id == steer.id for m in messages), "a steer has no reply of its own"


def test_turns_match_by_turn() -> None:
    for name in FIXTURES:
        rows = [r for r in _load(name) if r.get("role") and r.get("role") != "queued"]
        expected = [
            (
                str(turn[0].get("content")) if starts_a_turn(turn[0]) else None,
                sum(1 for r in turn[1:] if r.get("role") == "user"),
            )
            for turn in by_turn(rows)
        ]
        messages = _read(name)
        keys: list[str] = []
        for m in messages:
            key = m.parent_id if m.role == "assistant" else (m.facts.steered_into or m.id)
            if key not in keys:
                keys.append(key)
        starts = {m.id: m for m in messages if m.role == "user" and not m.facts.steered_into}
        got = [
            (
                _texts(starts[key])[0] if key in starts else None,
                sum(1 for m in messages if m.facts.steered_into == key and key),
            )
            for key in keys
        ]
        assert got == expected, name


def test_an_attachment_is_a_file_part_and_a_workspace_file_a_mention() -> None:
    from personalclaw.config.loader import config_dir

    message = _read("files.jsonl")[0]
    files = [p for p in message.parts if isinstance(p, turns.FilePart)]
    assert [(f.direction, f.ref, f.name) for f in files] == [
        ("in", turns.Ref(kind="attachment", id=f"{config_dir()}/uploads/chart.png"), "chart.png"),
        ("in", turns.Ref(kind="attachment", id=f"{config_dir()}/screenshots/snip.png"), "snip.png"),
    ]
    mentions = [p for p in message.parts if isinstance(p, turns.MentionPart)]
    assert [(m.target, m.ref, m.label, m.tokens, m.outcome) for m in mentions] == [
        ("file", "/work/project/src/main.py", "main.py", None, "attached"),
        ("knowledge", "kn_budget", "kn_budget", None, "attached"),
        ("artifact", "quarterly-report", "quarterly-report", None, "attached"),
    ]
    facts = message.facts
    assert facts.image_delivery == {"chart.png": "image", "snip.png": "text"}
    assert facts.image_delivery_reason == "The model takes one image a turn."
    assert facts.skills_used == ({"name": "pdf", "state": "loaded", "loaded_tokens": 900},)
    assert facts.ran_prompt == {"name": "compare", "text": "Compare {{a}} and {{b}}"}
    assert (facts.original, facts.ui_label) == ("compare em", "Compare")


def test_a_file_row_is_an_output_file_part() -> None:
    from personalclaw.config.loader import config_dir

    answer = _read("files.jsonl")[1]
    (made,) = [p for p in answer.parts if isinstance(p, turns.FilePart)]
    assert (made.direction, made.ref, made.name, made.mime, made.size) == (
        "out",
        turns.Ref(kind="path", id=f"{config_dir()}/outbox/diff.txt"),
        "diff.txt",
        "text/plain",
        2048,
    )


def test_an_answers_stamps_are_its_facts() -> None:
    chat = _read("chat.jsonl")
    asked, answer = chat[0], chat[1]
    assert (asked.facts.own_words, asked.facts.input_origin) == ("Plan the week", "typed")
    assert asked.facts.pastes == ({"seq": 1, "lines": 2, "content": "mon\ntue"},)
    facts = answer.facts
    assert facts.memory_citations == ({"n": 1, "id": "mem-1"},)
    assert (facts.summary, facts.finish_reason) == ("Planned the week", "length")
    assert facts.turn_telemetry == {"input_tokens": 120, "output_tokens": 40}
    assert facts.learned == ({"origin": "preference", "text": "Mornings first", "state": "kept"},)
    assert facts.context_fed == {"kind": "context", "text": "Injected 12 chars of context"}
    assert facts.model_substitution == "Ran on another model"
    tidied = _read("tools.jsonl")[1].facts.file_changes
    assert tidied == ({"path": "notes.md", "before": "# Notes", "after": "# Notes, tidied"},)


def test_regenerated_answers_are_attempts_of_one_parent() -> None:
    chat = _read("chat.jsonl")
    shorter = [m for m in chat if m.role == "assistant" and m.parent_id == chat[2].id]
    assert [(m.attempt, m.shown, _texts(m)) for m in shorter] == [
        (1, True, ["Mon: write. Tue: read."]),
        (2, False, ["Write Monday, read Tuesday."]),
    ]
    wednesday = [m for m in chat if m.role == "assistant" and m.parent_id == chat[5].id]
    assert [(m.attempt, m.shown, _texts(m)) for m in wednesday] == [
        (1, False, ["Walk."]),
        (2, False, ["Read."]),
        (3, True, ["Rest."]),
    ]


@pytest.mark.parametrize("name", [n for n in FIXTURES if n != DIFFERS])
def test_dialogue_equals_todays_turns(name: str) -> None:
    rows = _load(name)
    assert turns.dialogue(turn_rows.from_rows(rows, session_key=SESSION)) == model_view(rows, None)


def test_a_switched_variant_of_a_multi_segment_answer_reads_as_its_own_attempt() -> None:
    """Regenerate keeps an answer's earlier versions on the first row of its newest one, so a
    switch back to an earlier version puts that text in place of the first of several rows. The
    model view reads the row's new text with the newest attempt's other rows; the typed turn reads
    the earlier attempt, which is the one shown, and dialogue gives its words alone."""
    rows = _load(DIFFERS)
    messages = turn_rows.from_rows(rows, session_key=SESSION)
    shown, newest = messages[1], messages[2]
    assert (shown.shown, _texts(shown)) == (True, ["It is the off-by-one in the loop."])
    assert (newest.shown, _texts(newest)) == (
        False,
        ["Let me read the loop.", "The loop stops one short."],
    )
    said = [entry["content"] for entry in turns.dialogue(messages)]
    assert said == ["Find the bug", "It is the off-by-one in the loop."]
    assert [entry["content"] for entry in model_view(rows, None)] == [
        *said,
        "The loop stops one short.",
    ]


def test_a_rewound_tail_is_kept_beside_the_message_it_was_cut_at() -> None:
    rows = _load("files.jsonl")
    messages = turn_rows.from_rows(rows, session_key=SESSION)
    (rewind,) = turn_rows.rewinds(rows, session_key=SESSION)
    assert rewind.message_id == messages[2].id and rewind.ts == "2026-10-05T14:04:59+00:00"
    assert [(_shape(m)[:2], _texts(m)) for m in rewind.messages] == [
        (("user", "message"), ["Now the code"]),
        (("assistant", "1*"), ["The code is fine."]),
        (("user", "message"), ["And the tests?"]),
        (("assistant", "1*"), ["They pass."]),
    ]
    kept = {m.id for m in messages}
    assert not kept & {m.id for m in rewind.messages}, "a kept message is not a live one"
    assert not any("Now the code" in e["content"] for e in turns.dialogue(messages))
