"""Headless ``personalclaw run``.

Every guard here carries a VACUITY assertion: a test that only shows the guard firing
cannot tell a working guard from one that refuses everything. So each refusal is paired
with the input that must be ACCEPTED, and each classification with the neighbouring key
that must NOT move.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw import cli_run, home_gateway
from personalclaw.dashboard.chat_runner import (
    TURN_COMPLETE,
    TURN_ERROR,
    TURN_INTERRUPTED,
    TURN_INTERRUPTED_NOTICES,
    TURN_STOPPED,
)
from personalclaw.guardrails.policy import (
    HEADLESS,
    INTERACTIVE,
    is_unattended_session,
    profile_for_session,
)
from personalclaw.restart_request import RESTARTING

# ── The command a user types actually reaches an executor ────────────────────────


def test_run_is_a_top_level_subcommand_that_dispatches_to_cli_run(monkeypatch):
    """``personalclaw run`` must reach ``cli_run._run`` — the CALL SITE, not the parser.

    A registered subcommand with no executor is this codebase's most common inert
    control, and the parser rendering `--help` proves nothing about dispatch.
    """
    import sys

    from personalclaw import cli

    seen: list[object] = []
    monkeypatch.setattr(cli_run, "_run", lambda args: seen.append(args))
    monkeypatch.setattr(sys, "argv", ["personalclaw", "run", "-p", "hello"])
    cli.main()
    assert len(seen) == 1, "cli.main did not dispatch `run` to cli_run._run"
    assert seen[0].prompt == "hello"


def test_run_subcommand_does_not_shadow_spawn_run(monkeypatch):
    """The pre-existing ``run`` verb lives under ``spawn``; both must survive.

    A second parallel entry point is the worst outcome for this change, so assert the two
    namespaces stay distinct rather than trusting that they do.
    """
    import sys

    from personalclaw import cli

    dispatched: list[str] = []
    monkeypatch.setattr(cli_run, "_run", lambda args: dispatched.append("top:" + args.prompt))
    monkeypatch.setattr(cli, "_spawn", lambda args: dispatched.append("spawn:" + args.task))

    monkeypatch.setattr(sys, "argv", ["personalclaw", "run", "-p", "x"])
    cli.main()
    monkeypatch.setattr(sys, "argv", ["personalclaw", "spawn", "run", "a task"])
    cli.main()
    assert dispatched == ["top:x", "spawn:a task"]


# ── The defaulted-field hazard ───────────────────────────────────────────────────


@pytest.mark.parametrize("prompt", ["", "   ", "\n\t "])
def test_blank_prompt_is_refused(prompt, capsys):
    """``-p ""`` must refuse. argparse accepts an empty string for a `required` flag."""
    args = _args(prompt=prompt)
    assert cli_run._run_one(args) == 2
    assert "non-empty prompt" in capsys.readouterr().err


def test_a_real_prompt_passes_the_blank_guard(monkeypatch):
    """VACUITY: the blank guard must not be refusing everything.

    Without this, ``_run_one`` returning 2 for every input would satisfy the test above.
    Drive a real prompt to the point of gateway discovery and assert it got PAST the
    prompt guard (it fails later, on transport, which is a different exit path).
    """
    reached: list[int] = []

    def no_gateway(port=None):
        reached.append(1)
        raise home_gateway.NoGatewayRunning("no gateway of this home is running")

    monkeypatch.setattr(home_gateway, "reach", no_gateway)
    monkeypatch.setattr(
        cli_run,
        "start_transient_gateway",
        lambda: (_ for _ in ()).throw(cli_run.RunError("stopped here on purpose")),
    )
    assert cli_run._run_one(_args(prompt="a real prompt")) == 1
    assert reached, "a non-blank prompt never reached gateway discovery"


# ── Session identity: the prefix is what makes the run HEADLESS ──────────────────


def test_default_session_is_a_fresh_inbound_cli_key():
    a = cli_run.session_key_for("")
    b = cli_run.session_key_for("")
    assert a.startswith(cli_run.CLI_SESSION_PREFIX)
    assert a != b, "the default session must be a fresh one-shot, not a fixed key"


def test_named_session_is_stable_and_still_prefixed():
    """``--session`` gives continuity WITHOUT giving up the unattended classification."""
    assert cli_run.session_key_for("nightly") == cli_run.session_key_for("nightly")
    assert cli_run.session_key_for("nightly") == "inbound:cli:nightly"
    assert is_unattended_session(cli_run.session_key_for("nightly")) is True


def test_session_name_is_sanitised():
    """A name is not allowed to smuggle in a different prefix or a path separator."""
    key = cli_run.session_key_for("../../dashboard:evil")
    assert key.startswith(cli_run.CLI_SESSION_PREFIX)
    assert "/" not in key
    assert key.count(":") == 2, key


# ── Guardrail classification, and the measured dashboard-wrapper gap ─────────────


def test_inbound_cli_session_resolves_to_headless():
    # `profile_for_session` layers operator config onto the base, so it returns a COPY:
    # compare the resolved profile's NAME, never object identity.
    assert profile_for_session("inbound:cli:abc123").name == HEADLESS.name


def test_dashboard_wrapped_inbound_key_also_resolves_to_headless():
    """REGRESSION for a measured gap.

    ``chat_utils._history_key_for`` wraps a chat session's key as ``dashboard:<key>`` for
    the provider/history layer, and several guardrail readers see only that wrapped form.
    Measured before the fix: ``inbound:cli:abc`` classified unattended while
    ``dashboard:inbound:cli:abc`` classified ATTENDED and resolved INTERACTIVE — so a
    headless turn presented two different postures depending on who asked.
    """
    assert is_unattended_session("dashboard:inbound:cli:abc") is True
    assert profile_for_session("dashboard:inbound:cli:abc").name == HEADLESS.name


def test_wrapper_transparency_does_not_move_an_ordinary_dashboard_session():
    """VACUITY for the fix above: stripping the wrapper must not relax normal chats.

    If ``dashboard:`` were stripped unconditionally the test above would pass for the
    wrong reason and every interactive session could drift toward HEADLESS.
    """
    for key in ("dashboard:mychat", "dashboard:cron-ish", "dashboard:inbox-notes"):
        assert is_unattended_session(key) is False, key
        assert profile_for_session(key).name == INTERACTIVE.name, key


# ── The read-only rail: the endpoint, not the lookalike field ────────────────────


def test_task_mode_for_maps_allow_to_agent_and_default_to_ask():
    assert cli_run.task_mode_for(False) == "ask"
    assert cli_run.task_mode_for(True) == "agent"


def test_run_sets_the_task_mode_through_the_task_mode_endpoint(monkeypatch):
    """The read-only rail's CALL SITE.

    🔴 This is the regression for a defect that shipped and was caught only by driving
    the command: ``run`` first set the posture via the session-create body's ``mode``
    key, which writes ``_ChatSession.mode`` — a DIFFERENT field from the ``_task_mode``
    the tool gate reads. The session was created, "read-only" was printed to stderr, and
    a write tool then created a file on disk.

    So assert the endpoint, and assert that create does NOT carry a ``mode`` key (which
    would look correct and enforce nothing).
    """
    calls = _capture_api(monkeypatch)
    _a_gateway_of_this_home_runs(monkeypatch)
    monkeypatch.setattr(cli_run, "_consume", _raise_after_setup)

    cli_run._run_one(_args(prompt="hi"))

    paths = [c[0] for c in calls]
    assert "/api/chat/task-mode" in paths, f"the read-only rail was never applied: {paths}"
    create = next(body for path, body in calls if path == "/api/chat/sessions")
    assert "mode" not in create, (
        "session-create must not carry `mode` — it writes _ChatSession.mode, not "
        "_task_mode, and reads as a rail while enforcing nothing"
    )
    tm = next(body for path, body in calls if path == "/api/chat/task-mode")
    assert tm["mode"] == "ask"
    assert tm["session"].startswith(cli_run.CLI_SESSION_PREFIX)


def test_allow_sends_agent_mode(monkeypatch):
    """VACUITY for the rail: ``--allow`` must really change what is sent."""
    calls = _capture_api(monkeypatch)
    _a_gateway_of_this_home_runs(monkeypatch)
    monkeypatch.setattr(cli_run, "_consume", _raise_after_setup)

    cli_run._run_one(_args(prompt="hi", allow=True))

    tm = next(body for path, body in calls if path == "/api/chat/task-mode")
    assert tm["mode"] == "agent"


def test_allow_trusts_the_runs_own_chat_before_the_turn(monkeypatch):
    """🔴 Before: ``--allow`` set only the task mode, so a call that asks for approval was
    declined ("needs approval but the run is unattended") and a write the grant promised never
    ran on a native agent. Its approval half is Trust on the run's own chat, set before the turn
    is posted, and for that chat alone."""
    calls = _capture_api(monkeypatch)
    _a_gateway_of_this_home_runs(monkeypatch)
    monkeypatch.setattr(cli_run, "_consume", _raise_after_setup)

    cli_run._run_one(_args(prompt="hi", allow=True))

    paths = [c[0] for c in calls]
    assert "/api/chat/mode" in paths, paths
    mode = next(body for path, body in calls if path == "/api/chat/mode")
    task = next(body for path, body in calls if path == "/api/chat/task-mode")
    assert mode == {"mode": "trust", "session": task["session"]}
    assert task["session"].startswith(cli_run.CLI_SESSION_PREFIX)
    assert paths.index("/api/chat/mode") > paths.index("/api/chat/task-mode")


def test_a_read_only_run_trusts_nothing(monkeypatch):
    calls = _capture_api(monkeypatch)
    _a_gateway_of_this_home_runs(monkeypatch)
    monkeypatch.setattr(cli_run, "_consume", _raise_after_setup)

    cli_run._run_one(_args(prompt="hi"))

    assert "/api/chat/mode" not in [c[0] for c in calls]


def test_a_refused_write_grant_stops_the_run_before_its_turn(monkeypatch, capsys):
    """Under an operator ceiling of ``ask`` the grant does not hold, so the run says so and
    never posts a turn it promised writes for."""
    posted: list[str] = []

    def _api(port, token, path, body=None):
        posted.append(path)
        if path == "/api/chat/mode":
            raise cli_run.RunError(f"{path} failed: HTTP 409 approval_grant_refused")
        return {}

    monkeypatch.setattr(cli_run, "_api", _api)
    _a_gateway_of_this_home_runs(monkeypatch)
    turned: list[int] = []

    async def _turn(*a, **k):
        turned.append(1)

    monkeypatch.setattr(cli_run, "_consume", _turn)

    assert cli_run._run_one(_args(prompt="hi", allow=True)) == 1
    assert turned == []
    assert "--allow could not grant this run's writes" in capsys.readouterr().err


def test_ask_mode_denies_a_mutating_tool_and_allows_a_read(monkeypatch):
    """The mode names ``run`` sends must mean what it claims in the shared gate.

    Asserts against ``task_modes.task_mode_denies`` — the gate the native runtime calls
    before approval — so this fails if ``ask`` ever stops being read-only.
    """
    from personalclaw.task_modes import task_mode_denies

    assert task_mode_denies("ask", "caution", "write_file", "", "{}") != ""
    assert task_mode_denies("ask", "safe", "read_file", "", "{}") == ""
    # VACUITY: `agent` (what --allow sends) must permit the same mutating call.
    assert task_mode_denies("agent", "caution", "write_file", "", "{}") == ""


def test_ask_mode_denies_an_unclassifiable_tool(monkeypatch):
    """Fail-CLOSED: a tool this codebase cannot classify is denied, not waved through —
    a shell call whose command never arrived, and a tool that declares nothing."""
    from personalclaw.task_modes import task_mode_denies

    assert task_mode_denies("ask", "", "Terminal", "execute", "") != ""
    assert task_mode_denies("ask", "", "memory_recall", "", "{}") != ""


# ── An agent CLI: read-only holds for what it asks about, and the posture line says so ────


def _bind_provider(monkeypatch, kind: str) -> None:
    class _B:
        provider = kind

    monkeypatch.setattr("personalclaw.config.loader.resolve_agent_bindings", lambda cfg, name: _B())


def _reach_the_gateway(monkeypatch) -> list[int]:
    reached: list[int] = []
    _a_gateway_of_this_home_runs(monkeypatch, reached)
    monkeypatch.setattr(cli_run, "_api", lambda *a, **k: {})
    monkeypatch.setattr(cli_run, "_consume", _raise_after_setup)
    return reached


def test_a_read_only_run_on_an_agent_cli_runs_and_says_what_holds(monkeypatch, capsys):
    """Its turn tells the CLI its asking mode, so read-only holds for every call it asks about
    and the fail-fast ends a wait nobody can answer (``test_an_unattended_agent_cli_turn_asks_
    before_it_acts``). 🔴 Before: refused outright with exit 2, because the CLI approved its own
    calls on an unattended turn."""
    _bind_provider(monkeypatch, "acp:claude-code")
    reached = _reach_the_gateway(monkeypatch)
    assert cli_run._run_one(_args(prompt="hi")) == 1
    assert reached, "a read-only run on an agent CLI was refused before gateway discovery"
    err = capsys.readouterr().err
    assert "denies every non-read-only call Claude Code asks about" in err
    assert "stops the turn if it may have changed something" in err


def test_allow_on_an_agent_cli_proceeds(monkeypatch):
    _bind_provider(monkeypatch, "acp:claude-code")
    reached = _reach_the_gateway(monkeypatch)
    assert cli_run._run_one(_args(prompt="hi", allow=True)) == 1
    assert reached, "--allow on an agent CLI was refused before gateway discovery"


def test_a_native_agents_read_only_line_names_no_cli(monkeypatch):
    """The CLI clause keys off the RUNTIME, not off read-only mode."""
    _bind_provider(monkeypatch, "native")
    assert cli_run.agent_cli_of("PersonalClaw") == ""
    _bind_provider(monkeypatch, "acp:claude-code")
    assert cli_run.agent_cli_of("helper") == "Claude Code"


# ── Auth rides the Authorization header, never the URL ───────────────────────────


@pytest.mark.asyncio
async def test_a_run_authenticates_every_request_with_the_header_and_no_url_token(monkeypatch):
    """The whole turn — the task-mode call, the socket and the chat POST — against the REAL
    token middleware, with the minted token only in ``Authorization: Bearer``.

    It used to ride ``?token=`` on every request, because the middleware would not accept it
    anywhere else; a token in a URL is in the process list, the shell history and the repr of
    every aiohttp error that names the URL. Each request's target is recorded, so "no token in
    the URL" is measured on what reached the server rather than on what the client meant.
    """
    import asyncio

    from aiohttp import web
    from aiohttp.test_utils import TestServer

    from personalclaw.dashboard import token_auth

    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)
    token_auth.use_ephemeral_secret(b"cli-run-header-contract-key-0001")
    token_auth.revoke_all_sessions()
    session = "inbound:cli:header"
    targets: list[str] = []
    chat_posted = asyncio.Event()

    async def _recorded(request: web.Request) -> web.Response:
        targets.append(str(request.rel_url))
        if request.path == "/api/chat":
            chat_posted.set()
        return web.json_response({"ok": True})

    async def _socket(request: web.Request) -> web.WebSocketResponse:
        targets.append(str(request.rel_url))
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await chat_posted.wait()
        await ws.send_json({"type": "chat_chunk", "data": {"session": session, "content": "hi"}})
        await ws.send_json({"type": "chat_done", "data": {"session": session}})
        await ws.close()
        return ws

    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=10000)])
    app.router.add_post("/api/chat/task-mode", _recorded)
    app.router.add_post("/api/chat", _recorded)
    app.router.add_get("/api/ws", _socket)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    try:
        token = token_auth.generate_token("owner", ttl_seconds=300)
        await asyncio.get_running_loop().run_in_executor(
            None, cli_run._api, server.port, token, "/api/chat/task-mode", {"mode": "read"}
        )
        collector = cli_run._Collector(session, "json")
        await cli_run._consume(server.port, token, collector, "hello", 10.0)
    finally:
        await server.close()
        token_auth.revoke_all_sessions()
        token_auth.use_persistent_secret()

    assert collector.done and collector.result_text() == "hi"
    assert sorted(t.split("?", 1)[0] for t in targets) == [
        "/api/chat",
        "/api/chat/task-mode",
        "/api/ws",
    ], f"every request must have authenticated and reached its handler: {targets}"
    assert not [t for t in targets if "token=" in t], f"a URL carried the token: {targets}"
    assert token not in "".join(targets)


# ── Output contracts ─────────────────────────────────────────────────────────────


def test_streaming_json_emits_only_the_three_named_frames(capsys):
    c = cli_run._Collector("inbound:cli:x", "streaming-json")
    c.feed({"type": "chat_chunk", "data": {"session": "inbound:cli:x", "content": "hi"}})
    c.feed({"type": "chat_status", "data": {"session": "inbound:cli:x", "status": "Thinking…"}})
    c.feed({"type": "chat_done", "data": {"session": "inbound:cli:x"}})
    lines = [json.loads(ln) for ln in capsys.readouterr().out.strip().splitlines()]
    assert [d["type"] for d in lines] == ["chat_chunk", "chat_done"]
    assert c.done is True


def test_collector_ignores_another_sessions_frames():
    """A gateway broadcasts to every WS client, so filtering is not optional."""
    c = cli_run._Collector("inbound:cli:mine", "plain")
    c.feed({"type": "chat_chunk", "data": {"session": "dashboard:someone-else", "content": "nope"}})
    c.feed({"type": "chat_chunk", "data": {"session": "inbound:cli:mine", "content": "yes"}})
    assert c.result_text() == "yes"


def test_collector_marks_a_denied_tool_not_ok():
    """``tool_calls[].ok`` must be measured. A constant True is a decorative field."""
    c = cli_run._Collector("inbound:cli:x", "plain")
    c.feed({"type": "tool_call", "data": {"session": "inbound:cli:x", "tool": "write_file"}})
    c.feed(
        {
            "type": "tool_result",
            "data": {"session": "inbound:cli:x", "output": "Ask mode — only read-only tools run"},
        }
    )
    assert c.tool_calls[0]["ok"] is False
    # VACUITY: a normal result leaves ok True.
    c2 = cli_run._Collector("inbound:cli:x", "plain")
    c2.feed({"type": "tool_call", "data": {"session": "inbound:cli:x", "tool": "read_file"}})
    c2.feed({"type": "tool_result", "data": {"session": "inbound:cli:x", "output": "contents"}})
    assert c2.tool_calls[0]["ok"] is True


def test_collector_marks_an_agent_clis_refused_call_not_ok():
    """An agent CLI's call refused at the gate ends as the CLI's own failed call: its result
    frame says ``ok: false`` and carries no refusal text. 🔴 Before: reported ``ok: true``."""
    c = cli_run._Collector("inbound:cli:x", "json")
    c.feed({"type": "tool_call", "data": {"session": "inbound:cli:x", "tool": "Write pantry.md"}})
    c.feed({"type": "tool_result", "data": {"session": "inbound:cli:x", "output": "", "ok": False}})
    assert c.tool_calls[0]["ok"] is False


def test_an_error_frame_makes_the_turn_fail(monkeypatch, capsys):
    """Exit code must track turn SUCCESS, not merely reaching the end of the stream."""
    _a_gateway_of_this_home_runs(monkeypatch)
    monkeypatch.setattr(cli_run, "_api", lambda *a, **k: {})

    async def _erroring(port, token, collector, prompt, timeout):
        collector.feed(
            {
                "type": "chat_message",
                "data": {"session": collector.session_key, "role": "error", "content": "boom"},
            }
        )
        collector.feed(
            {"type": "chat_done", "data": {"session": collector.session_key, "outcome": TURN_ERROR}}
        )

    monkeypatch.setattr(cli_run, "_consume", _erroring)
    assert cli_run._run_one(_args(prompt="hi")) == 1
    assert "boom" in capsys.readouterr().err


def test_a_retried_turn_that_finishes_succeeds(monkeypatch, capsys):
    """A retry notice is an error row, and the turn goes on: the gateway runs the message again.
    The exit code follows how the turn ENDED, which only its final ``chat_done`` says."""
    _a_gateway_of_this_home_runs(monkeypatch)
    monkeypatch.setattr(cli_run, "_api", lambda *a, **k: {})

    async def _retried(port, token, collector, prompt, timeout):
        key = collector.session_key
        collector.feed(
            {
                "type": "chat_message",
                "data": {
                    "session": key,
                    "role": "error",
                    "content": "⟳ Connection lost — retrying...",
                },
            }
        )
        collector.feed({"type": "chat_chunk", "data": {"session": key, "content": "PONG"}})
        collector.feed({"type": "chat_done", "data": {"session": key, "outcome": TURN_COMPLETE}})

    monkeypatch.setattr(cli_run, "_consume", _retried)
    assert cli_run._run_one(_args(prompt="hi")) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "PONG"
    assert "turn failed" not in captured.err


def test_a_stopped_turn_fails_and_says_it_was_stopped(monkeypatch, capsys):
    """A turn stopped before it finished (from the dashboard, or by a watchdog) is not a
    success, and it is not an error either: the CLI says which."""
    _a_gateway_of_this_home_runs(monkeypatch)
    monkeypatch.setattr(cli_run, "_api", lambda *a, **k: {})

    async def _stopped(port, token, collector, prompt, timeout):
        key = collector.session_key
        collector.feed({"type": "chat_chunk", "data": {"session": key, "content": "Half an ans"}})
        collector.feed({"type": "chat_done", "data": {"session": key, "outcome": TURN_STOPPED}})

    monkeypatch.setattr(cli_run, "_consume", _stopped)
    assert cli_run._run_one(_args(prompt="hi")) == 1
    err = capsys.readouterr().err
    assert "stopped before it finished" in err
    assert "turn failed" not in err


def test_a_turn_a_restart_cut_off_says_the_gateway_restarted(monkeypatch, capsys):
    """Neither a Stop nor a failure of the turn's: the CLI says what its error row says."""
    _a_gateway_of_this_home_runs(monkeypatch)
    monkeypatch.setattr(cli_run, "_api", lambda *a, **k: {})
    notice = TURN_INTERRUPTED_NOTICES[RESTARTING]

    async def _cut_off(port, token, collector, prompt, timeout):
        key = collector.session_key
        collector.feed(
            {"type": "chat_message", "data": {"session": key, "role": "error", "content": notice}}
        )
        collector.feed({"type": "chat_done", "data": {"session": key, "outcome": TURN_INTERRUPTED}})

    monkeypatch.setattr(cli_run, "_consume", _cut_off)
    assert cli_run._run_one(_args(prompt="hi")) == 1
    err = capsys.readouterr().err
    assert f"personalclaw run: {notice}" in err
    assert "turn failed" not in err and "stopped before it finished" not in err, err


def test_a_clean_turn_exits_zero(monkeypatch, capsys):
    """VACUITY for the exit code: it must not be 1 for every turn."""
    _a_gateway_of_this_home_runs(monkeypatch)
    monkeypatch.setattr(cli_run, "_api", lambda *a, **k: {})
    monkeypatch.setattr(cli_run, "_token_total", lambda key: 7)

    async def _clean(port, token, collector, prompt, timeout):
        collector.feed(
            {"type": "chat_chunk", "data": {"session": collector.session_key, "content": "PONG"}}
        )
        collector.feed(
            {
                "type": "chat_done",
                "data": {"session": collector.session_key, "outcome": TURN_COMPLETE},
            }
        )

    monkeypatch.setattr(cli_run, "_consume", _clean)
    assert cli_run._run_one(_args(prompt="hi", fmt="json")) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["result"] == "PONG"
    assert doc["turns"] == 1
    assert doc["tokens"] == 7
    assert doc["session"].startswith(cli_run.CLI_SESSION_PREFIX)
    assert isinstance(doc["duration_ms"], int)


@pytest.mark.parametrize("ended", [TURN_COMPLETE, TURN_STOPPED, TURN_ERROR, TURN_INTERRUPTED])
def test_the_json_report_names_how_the_turn_ended(ended, monkeypatch, capsys):
    """The exit code says whether the turn completed; the report says how it ended, in the words
    its ``chat_done`` says it in."""
    _a_gateway_of_this_home_runs(monkeypatch)
    monkeypatch.setattr(cli_run, "_api", lambda *a, **k: {})

    async def _ends(port, token, collector, prompt, timeout):
        collector.feed(
            {"type": "chat_done", "data": {"session": collector.session_key, "outcome": ended}}
        )

    monkeypatch.setattr(cli_run, "_consume", _ends)
    code = cli_run._run_one(_args(prompt="hi", fmt="json"))

    assert json.loads(capsys.readouterr().out)["outcome"] == ended
    assert code == (0 if ended == TURN_COMPLETE else 1)


def _the_command_ends(exit_code: int, tmp_path: Path) -> int:
    """How a process running ``personalclaw run`` ends when its run returned *exit_code*: its
    return code as the parent reads it (minus the signal number when a signal ended it)."""
    import os
    import subprocess
    import sys

    program = (
        "from personalclaw import cli_run\n"
        f"cli_run._run_one = lambda args: {exit_code}\n"
        "cli_run._run(None)\n"
    )
    env = {**os.environ, "HOME": str(tmp_path), "PERSONALCLAW_HOME": str(tmp_path / "home")}
    return subprocess.run([sys.executable, "-c", program], env=env, timeout=60).returncode


@pytest.mark.parametrize("signame", ["SIGINT", "SIGTERM"])
def test_a_run_a_stop_signal_ended_exits_as_that_signal_ends_a_program(signame, tmp_path):
    """The run stops its turn first, then ends as the signal asked, so the shell or the script
    that ran it reads that signal's ending and stops too, rather than going on to its next line."""
    import signal as _signal

    signum = int(getattr(_signal, signame))
    assert _the_command_ends(128 + signum, tmp_path) == -signum


def test_a_run_no_signal_ended_exits_with_its_code(tmp_path):
    """VACUITY: any other exit is the code itself."""
    assert _the_command_ends(1, tmp_path) == 1


def test_a_runs_token_lasts_as_long_as_the_run_may(monkeypatch):
    """The stop a run sends once its timeout passes signs in with the token the run was minted:
    a run that may last longer than an hour gets one that lasts that long, and no longer than the
    gateway signs any."""
    from personalclaw.auth.lifetimes import MAX_LIFETIME_SECS, lifetime_seconds

    assert cli_run._token_ttl(600) == cli_run._TOKEN_TTL
    lasts = lifetime_seconds(cli_run._token_ttl(7200)) or 0
    assert lasts >= 7200 + cli_run._STOP_ANSWER_SECS + cli_run._STOP_WAIT_SECS
    assert lasts < 7200 + 3600
    assert lifetime_seconds(cli_run._token_ttl(10 * MAX_LIFETIME_SECS)) == MAX_LIFETIME_SECS

    minted: list[str] = []
    monkeypatch.setattr(
        home_gateway,
        "reach",
        lambda port=None: home_gateway.HomeGateway(
            home=Path("/home/user/.personalclaw"), port=1, pid=0
        ),
    )
    monkeypatch.setattr(
        cli_run, "mint_local_token", lambda gateway, *, ttl: minted.append(ttl) or "tok"
    )
    monkeypatch.setattr(cli_run, "_api", lambda *a, **k: {})
    monkeypatch.setattr(cli_run, "_consume", _raise_after_setup)
    args = _args()
    args.timeout = 7200.0
    cli_run._run_one(args)
    assert minted == [cli_run._token_ttl(7200)]


def test_the_write_grant_is_given_last_just_before_the_turn(monkeypatch, tmp_path):
    """A setup step that fails after the grant would leave it standing on a chat no turn ran in,
    so the grant is the last thing set before the turn is posted."""
    calls = _capture_api(monkeypatch)
    _a_gateway_of_this_home_runs(monkeypatch)
    monkeypatch.setattr(cli_run, "_consume", _raise_after_setup)
    args = _args(allow=True)
    args.cwd = str(tmp_path)

    cli_run._run_one(args)

    paths = [path for path, _body in calls]
    assert paths[-1] == "/api/chat/mode", paths
    assert any(path.endswith("/workspace-dir") for path in paths), paths


# ── The posture is always announced ──────────────────────────────────────────────


def test_the_posture_is_printed_for_both_modes():
    """A read-only default that says nothing is indistinguishable from no posture."""
    assert "read-only" in cli_run.grant_notice("inbound:cli:x", "ask")
    assert "read-only" in cli_run.grant_notice("inbound:cli:x", "ask", agent_cli="Claude Code")
    assert "WRITE GRANT" in cli_run.grant_notice("inbound:cli:x", "agent")


# ── Token accounting reads the key the ledger actually writes ────────────────────


def test_token_total_queries_the_dashboard_wrapped_key(monkeypatch, tmp_path):
    """REGRESSION: the ledger keys rows by ``dashboard:<session>``.

    Querying the bare key matched nothing and printed a confident ``"tokens": 0`` for a
    turn that had really billed 22,979 tokens.
    """
    seen: list[str] = []

    def _totals(*, session_key="", **kw):
        seen.append(session_key)
        return {"input_tokens": 10, "output_tokens": 5}

    monkeypatch.setattr("personalclaw.usage_ledger.totals", _totals)
    assert cli_run._token_total("inbound:cli:abc") == 15
    assert seen == ["dashboard:inbound:cli:abc"], seen


# ── Spend scope binding ──────────────────────────────────────────────────────────


def test_inbound_budget_is_the_headless_profile_budget():
    from personalclaw.guardrails.budgets import safety_budget_for_inbound
    from personalclaw.guardrails.policy import safety_profile_for

    assert safety_budget_for_inbound() == safety_profile_for(HEADLESS).budget


@pytest.mark.asyncio
async def test_a_cli_turn_binds_the_cli_spend_scope(monkeypatch):
    """The budget scope, asserted at the binding site.

    Nothing on the chat path bound a run scope before this: ``set_current_run_key`` had a
    single production caller (the trigger-fire seam), so every chat turn charged with an
    empty run key.
    """
    from personalclaw.dashboard import chat_handlers
    from personalclaw.guardrails.budgets import current_run_key

    observed: list[str] = []

    async def _fake_run_chat(state, session, message):
        observed.append(current_run_key())

    monkeypatch.setattr(chat_handlers, "run_chat", _fake_run_chat)
    await chat_handlers._run_chat_scoped(None, _FakeSession("inbound:cli:abc"), "hi")
    assert observed == [cli_run.CLI_RUN_KEY]


@pytest.mark.asyncio
async def test_a_dashboard_turn_binds_no_spend_scope(monkeypatch):
    """VACUITY: the binding is scoped to inbound turns.

    If it bound unconditionally the test above would pass while every interactive turn's
    accounting silently changed.
    """
    from personalclaw.dashboard import chat_handlers
    from personalclaw.guardrails.budgets import current_run_key

    observed: list[str] = []

    async def _fake_run_chat(state, session, message):
        observed.append(current_run_key())

    monkeypatch.setattr(chat_handlers, "run_chat", _fake_run_chat)
    await chat_handlers._run_chat_scoped(None, _FakeSession("my-chat"), "hi")
    assert observed == [""]


@pytest.mark.asyncio
async def test_another_inbound_surface_scopes_to_its_own_name(monkeypatch):
    """An HTTP dialect's turns must be attributable without being lumped in with the CLI."""
    from personalclaw.dashboard import chat_handlers
    from personalclaw.guardrails.budgets import current_run_key

    observed: list[str] = []

    async def _fake_run_chat(state, session, message):
        observed.append(current_run_key())

    monkeypatch.setattr(chat_handlers, "run_chat", _fake_run_chat)
    await chat_handlers._run_chat_scoped(None, _FakeSession("inbound:openai:c1"), "hi")
    assert observed == ["openai"]


# ── Home isolation ───────────────────────────────────────────────────────────────


def test_this_suite_runs_against_a_redirected_home():
    """Assert the conftest redirect actually applies, rather than trusting it.

    ``conftest`` re-points every binding of ``config_dir``; a second patch on top has
    leaked stores between tests, so this asserts the existing fixture instead of adding
    one — and proves the assertion is not vacuous by naming the real home.
    """
    import os

    from personalclaw.config.loader import config_dir

    resolved = config_dir()
    if os.environ.get("PERSONALCLAW_HOME"):
        pytest.skip("caller chose a home; the conftest guard deliberately defers to it")
    assert resolved != Path.home() / ".personalclaw", (
        f"config_dir() resolved to the REAL home ({resolved}) — the isolation fixture "
        f"is not in force for this test"
    )


# ── helpers ──────────────────────────────────────────────────────────────────────


def _a_gateway_of_this_home_runs(monkeypatch, reached: list[int] | None = None) -> None:
    """This home's gateway answers (``home_gateway.reach``, recorded in *reached*) and signs the
    run in, so the run goes on to set up its turn."""

    def reach(port=None):
        if reached is not None:
            reached.append(1)
        return home_gateway.HomeGateway(home=Path("/home/user/.personalclaw"), port=1, pid=0)

    monkeypatch.setattr(home_gateway, "reach", reach)
    monkeypatch.setattr(cli_run, "mint_local_token", lambda *a, **k: "tok")


class _FakeSession:
    def __init__(self, key: str) -> None:
        self.key = key


def _args(*, prompt: str = "hi", allow: bool = False, fmt: str = "plain"):
    import argparse

    return argparse.Namespace(
        prompt=prompt,
        format=fmt,
        agent="",
        model="",
        session="",
        cwd="",
        allow=allow,
        timeout=5.0,
        port=1,
    )


async def _raise_after_setup(port, token, collector, prompt, timeout):
    raise cli_run.RunError("setup complete; stopping before the turn")


def _capture_api(monkeypatch) -> list[tuple[str, dict]]:
    """Record every ``_api`` call as ``(path, body)``."""
    calls: list[tuple[str, dict]] = []

    def _fake(port, token, path, body=None):
        calls.append((path, body or {}))
        return {}

    monkeypatch.setattr(cli_run, "_api", _fake)
    return calls
