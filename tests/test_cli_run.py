"""Headless ``personalclaw run`` — EA-9 (EXTERNAL-ACCESS §9.5).

Every guard here carries a VACUITY assertion: a test that only shows the guard firing
cannot tell a working guard from one that refuses everything. So each refusal is paired
with the input that must be ACCEPTED, and each classification with the neighbouring key
that must NOT move.
"""

from __future__ import annotations

import json
import socket
import urllib.error
from pathlib import Path

import pytest

from personalclaw import cli_run
from personalclaw.guardrails.policy import (
    HEADLESS,
    INTERACTIVE,
    is_unattended_session,
    profile_for_session,
)

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

    A second parallel entry point is the worst outcome for this atom, so assert the two
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
    monkeypatch.setattr(cli_run, "probe_gateway", lambda *a, **k: reached.append(1) or False)
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
    monkeypatch.setattr(cli_run, "probe_gateway", lambda *a, **k: True)
    monkeypatch.setattr(cli_run, "mint_local_token", lambda *a, **k: "tok")
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
    monkeypatch.setattr(cli_run, "probe_gateway", lambda *a, **k: True)
    monkeypatch.setattr(cli_run, "mint_local_token", lambda *a, **k: "tok")
    monkeypatch.setattr(cli_run, "_consume", _raise_after_setup)

    cli_run._run_one(_args(prompt="hi", allow=True))

    tm = next(body for path, body in calls if path == "/api/chat/task-mode")
    assert tm["mode"] == "agent"


def test_ask_mode_denies_a_mutating_tool_and_allows_a_read(monkeypatch):
    """The mode names ``run`` sends must mean what it claims in the shared gate.

    Asserts against ``task_modes.task_mode_denies`` — the gate the native runtime calls
    before approval — so this fails if ``ask`` ever stops being read-only.
    """
    from personalclaw.task_modes import task_mode_denies

    assert task_mode_denies("ask", "write_file", "edit", "{}") != ""
    assert task_mode_denies("ask", "read_file", "read", "{}") == ""
    # VACUITY: `agent` (what --allow sends) must permit the same mutating call.
    assert task_mode_denies("agent", "write_file", "edit", "{}") == ""


def test_ask_mode_denies_an_unclassifiable_tool(monkeypatch):
    """Fail-CLOSED: a tool this codebase cannot classify is denied, not waved through."""
    from personalclaw.task_modes import task_mode_denies

    assert task_mode_denies("ask", "Terminal", "execute", "") != ""


# ── ACP: refuse a posture that cannot be enforced ───────────────────────────────


def _bind_provider(monkeypatch, kind: str) -> None:
    class _B:
        provider = kind

    monkeypatch.setattr("personalclaw.config.loader.resolve_agent_bindings", lambda cfg, name: _B())


def test_readonly_run_on_an_acp_agent_is_refused(monkeypatch, capsys):
    """An ACP runtime never receives the task mode, and an unattended ACP turn runs with
    permissions bypassed — so the read-only rail cannot hold. Refuse rather than promise."""
    _bind_provider(monkeypatch, "acp:claude-code")
    assert cli_run._run_one(_args(prompt="hi")) == 2
    err = capsys.readouterr().err
    assert "refusing a read-only headless turn" in err
    assert "--allow" in err, "the refusal must name the way forward"


def test_allow_on_an_acp_agent_proceeds(monkeypatch):
    """VACUITY 1: the ACP refusal is scoped to the read-only posture, not to ACP."""
    _bind_provider(monkeypatch, "acp:claude-code")
    reached: list[int] = []
    monkeypatch.setattr(cli_run, "probe_gateway", lambda *a, **k: reached.append(1) or True)
    monkeypatch.setattr(cli_run, "mint_local_token", lambda *a, **k: "tok")
    monkeypatch.setattr(cli_run, "_api", lambda *a, **k: {})
    monkeypatch.setattr(cli_run, "_consume", _raise_after_setup)
    assert cli_run._run_one(_args(prompt="hi", allow=True)) == 1
    assert reached, "--allow on an ACP agent was refused before gateway discovery"


def test_readonly_run_on_a_native_agent_proceeds(monkeypatch):
    """VACUITY 2: the refusal keys off the RUNTIME, not off read-only mode."""
    _bind_provider(monkeypatch, "native")
    assert cli_run.acp_readonly_refusal("PersonalClaw") == ""


# ── The liveness probe must not misread a slow gateway as an absent one ──────────


def test_probe_retries_a_timeout_and_reports_the_live_gateway(monkeypatch):
    """REGRESSION for a measured defect.

    At a 2s single-shot, a busy-but-alive gateway answered ``/api/healthz`` in >2s: three
    probes read False and the fourth returned True in 0.67s. Reading absent is expensive
    — ``run`` then boots a second gateway on the same home, whose startup overwrites the
    single shared ``.local_secret`` and leaves the ORIGINAL gateway unable to mint a
    token (observed as ``token mint failed: HTTP Error 403``).
    """
    attempts: list[int] = []

    def _flaky(req, timeout=0):
        attempts.append(1)
        if len(attempts) < 3:
            raise urllib.error.URLError(socket.timeout("timed out"))
        return _FakeResp(200)

    monkeypatch.setattr(urllib.request, "urlopen", _flaky)
    assert cli_run.probe_gateway(1234, attempts=3) is True
    assert len(attempts) == 3, "the probe did not retry the ambiguous timeouts"


def test_probe_reports_absent_when_every_attempt_times_out(monkeypatch):
    """VACUITY: retrying must not make the probe answer True unconditionally."""
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda req, timeout=0: (_ for _ in ()).throw(
            urllib.error.URLError(socket.timeout("timed out"))
        ),
    )
    assert cli_run.probe_gateway(1234, attempts=2) is False


def test_probe_short_circuits_a_refused_connection(monkeypatch):
    """A refused connection is unambiguous — don't spend the retry budget on it."""
    attempts: list[int] = []

    def _refused(req, timeout=0):
        attempts.append(1)
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))

    monkeypatch.setattr(urllib.request, "urlopen", _refused)
    assert cli_run.probe_gateway(1234, attempts=3) is False
    assert len(attempts) == 1, "a refused connection should not be retried"


# ── Auth rides the query string, not a Bearer header ─────────────────────────────


def test_token_goes_in_the_query_string():
    """``token_auth`` reads primary owner auth from ``?token=`` or the cookie ONLY.

    Its ``Authorization: Bearer`` branch narrows an ALREADY-authenticated request to an
    app scope; it never authenticates. Measured: a Bearer-only request to
    ``/api/chat/sessions`` answered ``403 {"error": "Token required"}``.
    """
    assert cli_run._authed("/api/chat", "T") == "/api/chat?token=T"
    assert cli_run._authed("/api/chat?ws=1", "T") == "/api/chat?ws=1&token=T"


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


def test_an_error_frame_makes_the_turn_fail(monkeypatch, capsys):
    """Exit code must track turn SUCCESS, not merely reaching the end of the stream."""
    monkeypatch.setattr(cli_run, "probe_gateway", lambda *a, **k: True)
    monkeypatch.setattr(cli_run, "mint_local_token", lambda *a, **k: "tok")
    monkeypatch.setattr(cli_run, "_api", lambda *a, **k: {})

    async def _erroring(port, token, collector, prompt, timeout):
        collector.feed(
            {
                "type": "chat_message",
                "data": {"session": collector.session_key, "role": "error", "content": "boom"},
            }
        )
        collector.feed({"type": "chat_done", "data": {"session": collector.session_key}})

    monkeypatch.setattr(cli_run, "_consume", _erroring)
    assert cli_run._run_one(_args(prompt="hi")) == 1
    assert "boom" in capsys.readouterr().err


def test_a_clean_turn_exits_zero(monkeypatch, capsys):
    """VACUITY for the exit code: it must not be 1 for every turn."""
    monkeypatch.setattr(cli_run, "probe_gateway", lambda *a, **k: True)
    monkeypatch.setattr(cli_run, "mint_local_token", lambda *a, **k: "tok")
    monkeypatch.setattr(cli_run, "_api", lambda *a, **k: {})
    monkeypatch.setattr(cli_run, "_token_total", lambda key: 7)

    async def _clean(port, token, collector, prompt, timeout):
        collector.feed(
            {"type": "chat_chunk", "data": {"session": collector.session_key, "content": "PONG"}}
        )
        collector.feed({"type": "chat_done", "data": {"session": collector.session_key}})

    monkeypatch.setattr(cli_run, "_consume", _clean)
    assert cli_run._run_one(_args(prompt="hi", fmt="json")) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["result"] == "PONG"
    assert doc["turns"] == 1
    assert doc["tokens"] == 7
    assert doc["session"].startswith(cli_run.CLI_SESSION_PREFIX)
    assert isinstance(doc["duration_ms"], int)


# ── The posture is always announced ──────────────────────────────────────────────


def test_the_posture_is_printed_for_both_modes():
    """A read-only default that says nothing is indistinguishable from no posture."""
    assert "read-only" in cli_run.grant_notice("inbound:cli:x", "ask")
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
    """§9.5's budget clause, asserted at the binding site.

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


class _FakeResp:
    def __init__(self, status: int) -> None:
        self.status = status

    def read(self) -> bytes:
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


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
