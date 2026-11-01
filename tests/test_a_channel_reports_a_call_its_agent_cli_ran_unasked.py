"""A channel app reports a call its agent CLI ran without asking the way every host in core does.

An agent CLI decides which of its calls ask the host first, so a channel that runs a conversation on
one learns of a call the CLI's own settings let run only at its result, when no permission request
came before it. Core's own hosts (a chat, a room member, a background turn) judge such a call by
core's record of what the CLI asks about, audit it ``ungated``, say so, and stop the turn when their
posture allows no change. A channel app had none of that: the SDK published no way to report one,
so a channel could only hide the call's card, after the call had run. The SDK now
publishes the one report core's hosts use (``personalclaw.sdk.channel.report_ungated_call``, core
feature ``ungated-calls``), with the call it judged, the answer a channel gives, and the words core
says it in.

The SDK is where an app's reach ends, so the report is held to the session its runtime is: the row
names the app whose code asked for that session, never what an argument says, and a session that
is not the runtime's (another chat's) or not an app's (core's own) is refused.

Driven through the published SDK names, from code loaded as an app's, over a real session manager
whose runtime names itself an agent CLI.
"""

from __future__ import annotations

import asyncio
import importlib.util

import pytest

from personalclaw.llm.events import EVENT_TOOL_CALL, AgentEvent

APP = "fixture-channel"
OTHER = "fixture-other-channel"
COMMAND = "pcfixture-notes --list"
THREAD = "1712345678.000100"

#: What the app's own code does: open its session's runtime, and report on it.
APP_CODE = """
import asyncio

from personalclaw.sdk.channel import report_ungated_call


async def open_session(sessions, key):
    provider, _is_new, _resumed = await sessions.get_or_create(key)
    sessions.release(key)
    return provider


async def report(provider, card, key, on_ungated=None):
    return await report_ungated_call(provider, card, session_key=key, on_ungated=on_ungated)


def report_in_a_task(provider, card, key):
    return asyncio.create_task(report_ungated_call(provider, card, session_key=key))
"""


@pytest.fixture
def audit(monkeypatch) -> list:
    from personalclaw.sel import SecurityEventLog

    rows: list = []
    monkeypatch.setattr(SecurityEventLog, "log", lambda self, event: rows.append(event))
    return rows


class _AgentCli:
    def __init__(self, runtime: str) -> None:
        self.provider_id = runtime
        self.cancelled: list[float] = []

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> None:
        self.cancelled.append(wait_ack_timeout)


@pytest.fixture
def apps(tmp_path, monkeypatch) -> dict:
    """Two channels' code, each loaded from a directory claimed as that app's."""
    from personalclaw import app_code

    monkeypatch.setattr(app_code, "_roots", dict(app_code._roots))
    monkeypatch.setattr(app_code, "_loaded", app_code._loaded)
    monkeypatch.setattr(app_code, "_released", set(app_code._released))
    loaded = {}
    for name in (APP, OTHER):
        root = tmp_path / "apps" / name
        root.mkdir(parents=True)
        (root / "channel.py").write_text(APP_CODE, encoding="utf-8")
        app_code.claim(name, root)
        spec = importlib.util.spec_from_file_location(f"_pcfixture_{name}", root / "channel.py")
        assert spec is not None and spec.loader is not None
        loaded[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loaded[name])
    return loaded


@pytest.fixture
def app(apps):
    return apps[APP]


def _sessions(runtime: str = "acp:claude-code"):
    from personalclaw.config import AppConfig
    from personalclaw.session import SessionManager

    cfg = AppConfig()
    cfg.session.pool_size = 0
    return SessionManager(cfg, provider_factory=lambda key, **_kw: _AgentCli(runtime))


def _card() -> AgentEvent:
    return AgentEvent(
        kind=EVENT_TOOL_CALL,
        tool_call_id="t1",
        title="Terminal",
        tool_kind="execute",
        tool_input=f'{{"command": "{COMMAND}"}}',
    )


def _rows(audit: list) -> list:
    return [e for e in audit if e.event_type == "tool_invocation" and e.operation == "Terminal"]


def test_a_channel_reports_a_call_its_cli_ran_unasked_and_core_audits_it(app, audit):
    """🔴 Red before: the SDK had no way to report one."""
    from personalclaw.sdk.channel import HostAnswer

    told: list = []

    def answer(call):
        told.append(call)
        return HostAnswer(stop=False, posture={"channel": "slack", "provider": "core"})

    async def scenario():
        cli = await app.open_session(_sessions(), THREAD)
        return cli, await app.report(cli, _card(), THREAD, on_ungated=answer)

    cli, result = asyncio.run(scenario())
    (call,) = told
    assert (call.acp_cli, call.title, call.who) == ("claude-code", "Terminal", "Claude Code")
    assert result is not None and result.stop is False
    (row,) = _rows(audit)
    assert row.outcome == "ungated"
    assert row.caller_identity == THREAD
    assert row.source == APP, "the row names the app that asked for the session"
    assert row.metadata["channel"] == "slack"
    assert row.metadata["provider"] == "claude-code", "a posture key never overwrites core's"
    assert row.resources == COMMAND, "the row keeps what it ran"
    assert cli.cancelled == [], "the channel's answer did not stop the turn"


def test_another_chats_session_is_refused_and_nothing_is_recorded(app, audit):
    """The key must be the one the runtime is held under: an app cannot write a row about
    another chat, or stop a turn that is not its own."""
    from personalclaw.sdk.channel import HostAnswer

    async def scenario():
        sessions = _sessions()
        cli = await app.open_session(sessions, THREAD)
        await app.open_session(sessions, "1712345678.000200")
        for key in ("dashboard:chat-1", "1712345678.000200", ""):
            with pytest.raises(PermissionError):
                await app.report(cli, _card(), key, on_ungated=lambda c: HostAnswer(stop=True))
        return cli

    cli = asyncio.run(scenario())
    assert _rows(audit) == [] and cli.cancelled == []


def test_a_runtime_no_app_asked_for_is_refused(app, audit):
    """A session core opened is core's: an app cannot write a row as core's."""

    async def scenario():
        sessions = _sessions()
        cli, _new, _resumed = await sessions.get_or_create("dashboard:chat-1")
        sessions.release("dashboard:chat-1")
        with pytest.raises(PermissionError):
            await app.report(cli, _card(), "dashboard:chat-1")
        with pytest.raises(PermissionError):
            await app.report(_AgentCli("acp:claude-code"), _card(), THREAD)  # no session's

    asyncio.run(scenario())
    assert _rows(audit) == []


def test_another_apps_code_cannot_report_on_the_session_awaited_or_in_a_task(apps, audit):
    """A task started on the report runs on core's frames alone, so whose call it is cannot be
    told from them: it is refused, as another app's awaited call is, and nothing is written."""
    app, other = apps[APP], apps[OTHER]

    async def scenario():
        cli = await app.open_session(_sessions(), THREAD)
        with pytest.raises(PermissionError, match=f"app '{APP}'"):
            await other.report(cli, _card(), THREAD)
        with pytest.raises(PermissionError, match="no app's code made this call"):
            await other.report_in_a_task(cli, _card(), THREAD)
        return cli

    cli = asyncio.run(scenario())
    assert _rows(audit) == [] and cli.cancelled == []


@pytest.mark.parametrize("given", ["coroutine", "none"])
def test_an_answer_that_is_no_host_answer_stops_the_turn(app, audit, given):
    """An async answer's coroutine, or ``None``, holds the call to nothing: fail closed, and the
    call is still written down."""

    async def later(call):
        from personalclaw.sdk.channel import HostAnswer

        return HostAnswer(stop=False)

    answer = later if given == "coroutine" else (lambda call: None)

    async def scenario():
        cli = await app.open_session(_sessions(), THREAD)
        result = await app.report(cli, _card(), THREAD, on_ungated=answer)
        return cli, result

    cli, result = asyncio.run(scenario())
    assert result is not None and result.stop is True
    assert cli.cancelled == [0.0]
    (row,) = _rows(audit)
    assert row.outcome == "ungated" and row.metadata.get("aborted_turn") is True


def test_a_channel_whose_answer_stops_the_turn_has_it_stopped(app, audit):
    from personalclaw.sdk.channel import HostAnswer

    async def scenario():
        cli = await app.open_session(_sessions(), THREAD)
        await app.report(cli, _card(), THREAD, on_ungated=lambda call: HostAnswer(stop=True))
        return cli

    cli = asyncio.run(scenario())
    assert cli.cancelled == [0.0]
    (row,) = _rows(audit)
    assert row.metadata.get("aborted_turn") is True


def test_an_answer_that_fails_stops_the_turn(app, audit):
    """Fail closed: what the channel would have held the call to can't be told, and it ran."""

    def broken(call):
        raise RuntimeError("the channel could not answer")

    async def scenario():
        cli = await app.open_session(_sessions(), THREAD)
        await app.report(cli, _card(), THREAD, on_ungated=broken)
        return cli

    assert asyncio.run(scenario()).cancelled == [0.0]


def test_a_runtime_that_is_no_agent_cli_is_judged_nothing(app, audit):
    """A native runtime asks about every call, so a result without a request is not one of these."""

    async def scenario():
        cli = await app.open_session(_sessions("native"), THREAD)
        return await app.report(cli, _card(), THREAD)

    assert asyncio.run(scenario()) is None
    assert _rows(audit) == []


def test_the_note_is_cores_own_words():
    from personalclaw.sdk.channel import ungated_call_note

    assert ungated_call_note("Claude Code", excused=False) == (
        "Ran without asking you — allowed by Claude Code's own settings."
    )


def test_the_report_is_a_named_core_feature():
    from personalclaw.sdk.features import CORE_FEATURES, UNGATED_CALLS, core_has

    assert UNGATED_CALLS == "ungated-calls"
    assert UNGATED_CALLS in CORE_FEATURES and core_has(UNGATED_CALLS)
