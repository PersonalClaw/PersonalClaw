"""A call an agent CLI ran without asking is audited as one, and said where its owner reads it.

An agent CLI decides which of its calls ask the host first, so the host learns of one its own
settings let run only when its result lands. A chat says so on the call's own card and audits it
``ungated`` (``dashboard.ungated_calls``). A room member on an agent CLI, and every other turn the
background helper runs on one (``llm_helpers.stream_and_collect``), audited the same call as the
generic "nobody was asked" outcome, ``invoked``, under no session at all, and the room showed
nothing of it. Now the call is judged and audited once for every host, as the chat audits it:

* its row reads ``ungated`` (``ungated_declared`` for a tool the CLI is known never to ask about)
  and names the member and its room;
* the room says on its transcript which member ran what without asking you;
* and a member whose tools do not cover the call (a read-only one) has its turn stopped when the
  call was not a read, as a chat in Ask or Plan mode is, so it cannot chain more of them.

Driven through the shipped ``rooms.turn.run_member_turn`` and ``stream_and_collect`` with a
scripted agent-CLI provider that emits the frames an ACP runtime emits. No CLI is launched.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from personalclaw.rooms import store, turn

MEMBER = "coder"
COMMAND = "pcfixture-notes --list"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    from personalclaw.guardrails.budgets import reset_meter
    from personalclaw.guardrails.ceiling import reset_ceiling

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    reset_ceiling()
    reset_meter()
    yield tmp_path
    reset_ceiling()
    reset_meter()


@pytest.fixture
def enabled(monkeypatch):
    from personalclaw.config.loader import AgentProfile, AppConfig

    cfg = AppConfig()
    cfg.rooms.enabled = True
    cfg.agents = {MEMBER: AgentProfile(provider="acp:claude-code", provider_agent="default")}
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls, *a, **k: cfg))
    return cfg


@pytest.fixture
def audit(monkeypatch) -> list:
    from personalclaw.sel import SecurityEventLog

    rows: list = []
    monkeypatch.setattr(SecurityEventLog, "log", lambda self, event: rows.append(event))
    return rows


class _AgentCli:
    """The frames an ACP runtime emits for a turn whose CLI ran one call without asking."""

    def __init__(self, runtime: str, title: str, kind: str, tool_input: str, *, asks: bool = False):
        self.provider_id = runtime
        self.title, self.kind, self.tool_input, self.asks = title, kind, tool_input, asks
        self.cancelled: list[float] = []
        self.approved: list[object] = []
        self.rejected: list[object] = []

    async def stream(self, message: str):
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Looking at the notes. ")
        yield AgentEvent(
            kind=EVENT_TOOL_CALL,
            tool_call_id="t1",
            title=self.title,
            tool_kind=self.kind,
            tool_input=self.tool_input,
        )
        if self.asks:
            yield AgentEvent(
                kind=EVENT_PERMISSION_REQUEST,
                tool_call_id="t1",
                request_id=7,
                title=self.title,
                tool_kind=self.kind,
                tool_input=self.tool_input,
            )
        yield AgentEvent(
            kind=EVENT_TOOL_RESULT, tool_call_id="t1", title=self.title, tool_output="3 notes"
        )
        if not self.cancelled:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="There are three notes.")
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="cancelled" if self.cancelled else "")

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> None:
        self.cancelled.append(wait_ack_timeout)

    async def approve_tool(self, request_id) -> None:
        self.approved.append(request_id)

    async def reject_tool(self, request_id) -> None:
        self.rejected.append(request_id)


class _Sessions:
    def __init__(self, provider: _AgentCli) -> None:
        self.provider = provider

    async def get_or_create(self, key, agent=None, **kwargs):
        return self.provider, True, False

    def release(self, key, *, cleanup=False):
        return None


def _shell() -> _AgentCli:
    return _AgentCli("acp:claude-code", "Terminal", "execute", f'{{"command": "{COMMAND}"}}')


def _read() -> _AgentCli:
    return _AgentCli("acp:claude-code", "Read File", "read", '{"path": "notes/plan.md"}')


def _drive(cli: _AgentCli, *, tier: str = "") -> tuple[str, str]:
    room = store.create_room("What is in the notes folder?")
    store.add_member(room.id, MEMBER, profile_narrowing={"tool_grants": tier} if tier else None)
    store.append_message(room.id, role="user", content="List the notes.", speaker="")
    reply = asyncio.run(turn.run_member_turn(_Sessions(cli), room.id, MEMBER))
    return room.id, reply


def _notes(room_id: str) -> list[str]:
    return [m["content"] for m in store.read_messages(room_id) if m["role"] == store.ROOM_NOTE_ROLE]


def _rows(audit: list, tool: str) -> list:
    return [e for e in audit if e.event_type == "tool_invocation" and e.operation == tool]


# ── audited as the chat audits it ────────────────────────────────────────────────────────────────


def test_a_members_unasked_call_is_audited_as_ungated_under_the_member(enabled, audit):
    """🔴 Before: one ``invoked`` row ("no_approval_needed") under no session and no agent."""
    room_id, _reply = _drive(_read())

    (row,) = _rows(audit, "Read File")
    assert row.outcome == "ungated"
    assert row.caller_identity == turn.session_key(room_id, MEMBER)
    assert row.agent == MEMBER
    assert row.source == "room"
    assert row.metadata["provider"] == "claude-code"
    assert row.metadata["reason"] == "no session/request_permission for this tool_call"
    assert row.metadata["tool_grants"] == "read"
    assert "aborted_turn" not in row.metadata


def test_the_room_says_the_member_ran_it_without_asking_you(enabled, audit):
    room_id, reply = _drive(_read())

    assert _notes(room_id) == [
        f"{MEMBER} ran Read File without asking you — allowed by Claude Code's own settings."
    ]
    assert "There are three notes." in reply, "a read run unasked does not stop the turn"


def test_a_read_only_members_unasked_change_stops_its_turn(enabled, audit):
    cli = _shell()
    room_id, reply = _drive(cli)

    assert cli.cancelled == [0.0], "the CLI's turn was not stopped"
    (row,) = _rows(audit, "Terminal")
    assert row.outcome == "ungated" and row.metadata.get("aborted_turn") is True
    assert row.resources == COMMAND, "the row keeps what it ran"
    assert _notes(room_id) == [
        f"{MEMBER} ran Terminal without asking you — allowed by Claude Code's own settings. "
        "Its tools are read-only, and Terminal is not one of them, so its turn was stopped."
    ]
    assert "There are three notes." not in reply


def test_a_member_whose_tools_cover_the_call_carries_on(enabled, audit):
    """Positive control: the stop is the tier's, so a read-and-write member is not stopped."""
    cli = _shell()
    room_id, reply = _drive(cli, tier="read_write")

    assert cli.cancelled == []
    (row,) = _rows(audit, "Terminal")
    assert row.outcome == "ungated" and "aborted_turn" not in row.metadata
    assert row.metadata["tool_grants"] == "read_write"
    assert _notes(room_id) == [
        f"{MEMBER} ran Terminal without asking you — allowed by Claude Code's own settings."
    ]
    assert "There are three notes." in reply


def test_a_tool_the_cli_never_asks_about_is_audited_as_declared(enabled, audit):
    cli = _AgentCli("acp:kiro-cli", "Creating task list: notes", "other", "{}")
    room_id, _reply = _drive(cli)

    (row,) = _rows(audit, "Creating task list: notes")
    assert row.outcome == "ungated_declared"
    assert cli.cancelled == [], "an accepted residual does not stop the turn"
    assert _notes(room_id) == [
        f"{MEMBER} ran Creating task list: notes without asking you — Kiro Cli never asks about "
        "this tool."
    ]


def test_a_call_the_cli_asked_about_is_no_unasked_call(enabled, audit):
    cli = _AgentCli("acp:claude-code", "Read File", "read", '{"path": "notes/plan.md"}', asks=True)
    room_id, _reply = _drive(cli, tier="read_write")

    assert [r.outcome for r in _rows(audit, "Read File")] == [
        "rejected"
    ], "it was asked about, and with nobody to ask it was refused"
    assert not any("without asking you" in n for n in _notes(room_id))


# ── the log lines that name a member's call ──────────────────────────────────────────────────────

#: A credential shape the masker knows (the documented example access key), in a shell call's title
#: as an agent CLI sends it: the command, with a second line after it.
KEY = "AKIAIOSFODNN7EXAMPLE"
KEYED = f"Running: curl -H 'X-Api-Key: {KEY}' https://api.example.com/v1/notes\ncat notes/plan.md"
KEYED_INPUT = '{"command": "curl https://api.example.com/v1/notes"}'


def _logged(caplog, words: str) -> list[str]:
    lines = [r.getMessage() for r in caplog.records if words in r.getMessage()]
    assert lines, f"no log line says {words!r}: the test would pass without the line it reads"
    return lines


def _masked(lines: list[str]) -> bool:
    return all(KEY not in line and "[REDACTED" in line and "\n" not in line for line in lines)


def test_the_log_line_of_a_members_unasked_call_writes_its_title_masked(enabled, audit, caplog):
    with caplog.at_level(logging.WARNING):
        _drive(_AgentCli("acp:claude-code", KEYED, "execute", KEYED_INPUT))

    assert _masked(_logged(caplog, "without asking the host"))


def test_the_log_line_of_a_members_refused_call_writes_its_title_masked(enabled, audit, caplog):
    cli = _AgentCli("acp:claude-code", KEYED, "execute", KEYED_INPUT, asks=True)
    with caplog.at_level(logging.WARNING):
        _drive(cli)

    assert cli.rejected == [7], "a read-only member's shell call is refused by its tier"
    assert _masked(_logged(caplog, "was refused"))


# ── every turn the background helper runs on an agent CLI ───────────────────────────────────────


def test_a_background_turn_on_an_agent_cli_audits_its_unasked_call_as_ungated(audit):
    """The heartbeat and a subagent's result injection run agent CLIs through the same helper."""
    from personalclaw.llm_helpers import stream_and_collect

    cli = _shell()
    asyncio.run(stream_and_collect(cli, "go"))

    (row,) = _rows(audit, "Terminal")
    assert row.outcome == "ungated"
    assert row.metadata["provider"] == "claude-code"
    assert cli.cancelled == [], "nothing that is not the member's tier stops a background turn"


def test_a_native_runtimes_unasked_call_keeps_its_own_outcome(audit):
    """Positive control: only an agent CLI's call is an ungated one. PersonalClaw's own runtime
    gates every call before it runs, so its result says what decided it."""
    from personalclaw.llm_helpers import stream_and_collect

    class _Native(_AgentCli):
        pass

    cli = _Native("", "read_file", "read", '{"path": "notes/plan.md"}')
    asyncio.run(stream_and_collect(cli, "go"))

    assert [r.outcome for r in _rows(audit, "read_file")] == ["invoked"]
