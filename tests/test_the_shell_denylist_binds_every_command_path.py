"""A command the shell denylist refuses is refused on every path that runs one, before anyone is
asked about it, and a run nobody watches says so where its owner looks.

Settings → Security → Shell denylist said its patterns were "matched against every command the agent
runs". One path asked them: the native bash tool, and only after the owner had approved the call. A
loop's check, a bash action started by Run now, a workflow's verify gate and its steps, an app's
setup hook, and a command an agent CLI asked the host to run all ran a command the owner had added a
pattern for. The approval card and Tools → Try it asked the owner to confirm a command the tool
then refused.

Every one of those paths now asks ``security.denied_command``, the one check, before it runs
anything, and ``test_every_command_path_asks_the_denylist.py`` keeps it so. The added pattern names
a program that cannot resolve (``pcfixture-cloudctl``), so a path that still ran it ran nothing.
Each family has its control: an ordinary command still runs, or is still asked about.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent

ADDED = "pcfixture-cloudctl"
DENIED = f"{ADDED} status"
#: How every surface names an added pattern's rule.
RULE = "a pattern added to the shell denylist under Settings → Security"


def _add_pattern(pattern: str = ADDED, **sections: Any) -> None:
    """Add *pattern* under Settings → Security → Shell denylist, in the home this test runs in."""
    from personalclaw.config.loader import config_dir

    doc = {"security": {"denied_commands": [pattern]}, **sections}
    (config_dir() / "config.json").write_text(json.dumps(doc), encoding="utf-8")


@pytest.fixture
def spawns(monkeypatch) -> list[list[str]]:
    """Every child the sandbox spawner is asked to start, passed on to the real one."""
    from personalclaw import sandbox

    real = sandbox.create_subprocess_limited
    seen: list[list[str]] = []

    async def spy(*argv: Any, **kwargs: Any):
        seen.append([str(a) for a in argv])
        return await real(*argv, **kwargs)

    monkeypatch.setattr(sandbox, "create_subprocess_limited", spy)
    return seen


def _ran(seen: list[list[str]]) -> bool:
    """Whether any spawn named the refused program."""
    return any(ADDED in " ".join(argv) for argv in seen)


# ── the one check ──────────────────────────────────────────────────────────────────────────────


def test_the_one_check_names_the_rule_and_whose_rule_it_is():
    from personalclaw.security import denied_command

    _add_pattern()
    added = denied_command(DENIED)
    assert added is not None and added.pattern == ADDED and added.added
    assert added.refusal() == f"Blocked: the command matches `{ADDED}`, {RULE}. It was not run."
    builtin = denied_command("curl http://169.254.169.254/latest/meta-data/")
    assert builtin is not None and not builtin.added
    assert "one of the shell denylist's built-in patterns" in builtin.why()
    assert denied_command("echo hello") is None


def test_an_action_is_matched_whatever_case_its_pattern_is_written_in():
    """The action seam lowercased the command and matched the pattern as written, so a pattern
    with a capital (an added `PcFixture-CloudCtl`, the built-in `DROP TABLE.*`) never matched an
    action, while the bash tool matched both."""
    from personalclaw.guardrails.denylist import check_action

    _add_pattern("PcFixture-CloudCtl")
    added = check_action("bash", {"command": DENIED})
    assert added.blocked and RULE in added.reason
    builtin = check_action("bash", {"command": "psql -c 'drop table users'"})
    assert builtin.blocked and "built-in" in builtin.reason
    assert not check_action("bash", {"command": "echo hello"}).blocked


# ── loops: the check every cycle runs, and the plan that names it ──────────────────────────────


@pytest.mark.asyncio
async def test_a_loop_check_the_denylist_refuses_never_runs(tmp_path, spawns):
    from personalclaw.loop.gates import run_verify_command

    _add_pattern()
    assert await run_verify_command(DENIED, str(tmp_path)) is None
    assert not _ran(spawns)
    assert await run_verify_command("true", str(tmp_path)) is True
    assert spawns, "the control was spawned"


@pytest.mark.asyncio
async def test_a_refused_command_is_one_refused_row_and_one_warning_with_its_credential_masked(
    tmp_path, spawns, caplog
):
    """One row, `refused`, naming the control and its rule, and one WARNING line. The row keeps
    the command as its evidence, and a command can carry what its run was handed: it is written
    as any text a person reads is, masked."""
    import logging

    from personalclaw.config.loader import config_dir
    from personalclaw.loop.gates import run_verify_command

    _add_pattern()
    login = "owner:pcfixture-pass-8841"
    command = f"{DENIED} --endpoint https://{login}@cloud.example.com/v1"
    with caplog.at_level(logging.WARNING):
        assert await run_verify_command(command, str(tmp_path)) is None
    lines = (config_dir() / "security_events.jsonl").read_text(encoding="utf-8").splitlines()
    rows = [r for r in map(json.loads, lines) if r.get("event_type") == "command_refused"]
    assert [(r["source"], r["outcome"]) for r in rows] == [("loop_gate", "refused")]
    assert (rows[0]["metadata"]["control"], rows[0]["metadata"]["rule"]) == (
        "shell_denylist",
        ADDED,
    )
    assert RULE in rows[0]["resources"]
    assert "cloud.example.com" in rows[0]["metadata"]["command"]
    assert "pcfixture-pass-8841" not in json.dumps(rows), "the audit row kept the credential"
    said = [r.getMessage() for r in caplog.records if "refused by shell_denylist" in r.getMessage()]
    assert len(said) == 1 and ADDED in said[0] and "pcfixture-pass-8841" not in said[0]
    assert not _ran(spawns)


def test_a_loop_whose_check_is_refused_pauses_and_says_which_rule(monkeypatch, tmp_path, spawns):
    """It cycled on toward its budget, flagging the check "unavailable" each cycle, while the
    command it named could never run."""
    from test_loop_watchdog import _FakeSession, _run, _running, _wd, _write_finding

    from personalclaw.loop import files as loop_files
    from personalclaw.loop import manager, store
    from personalclaw.loop.loop import LoopStatus

    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    _add_pattern()
    loop = _running(kind_config={"goal_type": "verifiable", "verify_command": DENIED})
    wd = _wd()
    events: list[str] = []
    wd._publish = lambda lid, event, data=None: events.append(event)  # type: ignore[method-assign]
    key = manager.session_key(loop.id)
    wd._state._sessions[key] = _FakeSession(key)
    _run(wd._poll_once())
    _write_finding(loop.id, 1)
    _run(wd._poll_once())

    assert store.get(loop.id).status == LoopStatus.NEEDS_INPUT.value
    asked = loop_files.pending_question(loop.id) or {}
    assert RULE in asked.get("question", "") and "Change the check command" in asked["question"]
    assert "needs_input" in events and "judge_error" not in events
    assert not _ran(spawns)


@pytest.mark.asyncio
async def test_a_code_loop_whose_proving_check_is_refused_pauses_instead_of_completing(
    monkeypatch, tmp_path, spawns
):
    """A refused check proved nothing, and the free-running code loop completed on it."""
    from test_loop_watchdog import _running

    from personalclaw.loop import files as loop_files
    from personalclaw.loop import store
    from personalclaw.loop.kinds.sdlc import CodeKind
    from personalclaw.loop.loop import LoopStatus

    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    _add_pattern()
    published: list[str] = []
    ctx = SimpleNamespace(publish=lambda lid, event, data=None: published.append(event))
    refused = _running(kind="code", kind_config={"verify_command": DENIED})
    assert await CodeKind()._no_stage_done(refused, [{"cycle": 1}], ctx) is False
    assert store.get(refused.id).status == LoopStatus.NEEDS_INPUT.value
    assert RULE in (loop_files.pending_question(refused.id) or {}).get("question", "")
    assert published == ["needs_input"] and not _ran(spawns)

    proven = _running(kind="code", kind_config={"verify_command": "true"})
    assert await CodeKind()._no_stage_done(proven, [{"cycle": 1}], ctx) is True


def test_a_stage_gate_check_is_judged_only_where_the_stage_runs_it():
    from personalclaw.loop.kinds.sdlc import _refused_stage_check
    from personalclaw.loop.loop import Loop

    _add_pattern()
    loop = Loop(id="l1", name="L", kind="code", task="t", kind_config={"verify_command": DENIED})
    assert RULE in _refused_stage_check(loop, {"exit_criteria": ["it builds"]}, "implementation")
    assert _refused_stage_check(loop, {"exit_criteria": []}, "implementation") == ""


@pytest.mark.parametrize(
    "kind, config",
    [
        ("general", {"verify_command": DENIED}),
        ("goal", {"goal_type": "verifiable", "verify_command": DENIED}),
        ("code", {"test_command": DENIED}),
    ],
)
def test_a_loop_plan_naming_a_refused_check_is_refused_when_it_is_saved(kind, config):
    from personalclaw.loop import kinds

    kinds.ensure_loaded()
    _add_pattern()
    errors, _ = kinds.get(kind).validate_config({"kind_config": config})
    assert any(RULE in e for e in errors), errors
    ordinary = {k: "make test" if k.endswith("_command") else v for k, v in config.items()}
    assert not any(
        "command rejected" in e
        for e in kinds.get(kind).validate_config({"kind_config": ordinary})[0]
    )


# ── workflows: a verify gate, a step, a teardown ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_workflow_check_the_denylist_refuses_fails_its_gate_with_the_rule(
    monkeypatch, tmp_path, spawns
):
    """The gate failed as a check "could not be determined (verifier did not run)", and told the
    owner to check the command exists."""
    from test_workflows_a_failed_check_ends_the_run import (
        _check,
        _judge_says,
    )
    from test_workflows_a_failed_check_ends_the_run import _run as run_workflow
    from test_workflows_a_failed_check_ends_the_run import (
        _spec,
        _started,
        _transform,
    )

    from personalclaw.workflows.controller import EngineServices
    from personalclaw.workflows.models import RunStatus
    from personalclaw.workflows.verify import run_verify_block

    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: runs)
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: runs, raising=False)
    _add_pattern()
    check = _check("verify", "verify_command", verify={"command": DENIED, "label": "tests"})
    services = EngineServices(completion=_judge_says("PASS"), verify=run_verify_block)
    run = await run_workflow(_spec(check, _transform("handoff")), services=services)

    assert run.run.status == RunStatus.FAILED
    assert run.run.error_message == (
        f"“verify” failed: refused before it ran: the command matches `{ADDED}`, {RULE}, so "
        "nothing after it ran."
    )
    assert "handoff" not in _started(run.run.id, control="verify")
    assert not _ran(spawns)


@pytest.mark.asyncio
async def test_a_workflow_step_or_teardown_the_denylist_refuses_never_runs(tmp_path, spawns):
    from personalclaw.workflows.effects import run_teardown
    from personalclaw.workflows.provisioning import run_step

    _add_pattern()
    ok, detail = await run_step(DENIED, tmp_path)
    assert ok is False and RULE in detail
    ok, detail = await run_teardown(f"{ADDED} delete", "resource-1")
    assert ok is False and RULE in detail
    assert not _ran(spawns)
    assert (await run_step("true", tmp_path))[0] is True


# ── bash actions, however they start ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_bash_action_the_denylist_refuses_never_runs(spawns):
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.bash_provider import BashActionProvider

    _add_pattern()
    refused = await BashActionProvider().execute({"command": DENIED}, ActionContext(event="run"))
    assert refused.success is False and RULE in refused.error
    assert not _ran(spawns)
    ran = await BashActionProvider().execute({"command": "echo hello"}, ActionContext(event="run"))
    assert ran.success and ran.stdout == "hello"


@pytest.mark.asyncio
async def test_a_command_a_bash_action_runs_from_its_payload_is_judged_as_what_runs(spawns):
    """A workflow hands its check command to a bash node as a variable (`sh -c "$PC_VERIFY_CMD"`
    in the bundled code project), so the command text named nothing the denylist matched."""
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.bash_provider import BashActionProvider

    _add_pattern()
    ctx = ActionContext(event="workflow_node", payload={"PC_CHECK": DENIED})
    refused = await BashActionProvider().execute({"command": 'sh -c "$PC_CHECK"'}, ctx)
    assert refused.success is False and RULE in refused.error
    assert spawns == []
    # A value the command only prints is data: it runs, whatever words it holds.
    note = ActionContext(event="run", payload={"NOTE": f"remember {DENIED}"})
    shown = await BashActionProvider().execute({"command": 'printf "%s" "$NOTE"'}, note)
    assert shown.success and shown.stdout == f"remember {DENIED}"


@pytest.fixture
def trigger_home(tmp_path, monkeypatch) -> Path:
    import personalclaw.config.loader as loader
    from personalclaw.dashboard.handlers import triggers as T
    from personalclaw.triggers.store import TriggerStore

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    monkeypatch.setattr("personalclaw.triggers.boot_migrate.config_dir", lambda: tmp_path)
    return tmp_path


@pytest.mark.parametrize("by_you", [True, False], ids=["your-run-now", "a-run-nobody-answers"])
def test_run_now_of_a_bash_trigger_the_denylist_refuses_runs_nothing(trigger_home, spawns, by_you):
    """Run now, the restart review's Run now, a webhook fire and a view refresh all reach the
    action through this one dispatch. A run nobody answers is held to the action denylist there,
    whose last rule is this one; a run you start yourself is not, and the action itself asks."""
    from test_a_trigger_runs_only_what_it_was_granted import _row, _schedule

    from personalclaw import approval_answer
    from personalclaw.dashboard.handlers import trigger_runs

    _add_pattern()
    bash = {"inline": {"provider": "bash", "config": {"command": DENIED}}}
    _schedule(trigger_home, workflow=bash, capabilities={"providers": ["bash"]})
    ran, note = asyncio.run(
        trigger_runs._dispatch_store_action(
            _row(trigger_home, "nightly"),
            {"trigger_id": "nightly", "manual": True},
            runs_for=approval_answer.YOU if by_you else approval_answer.trigger("nightly"),
        )
    )
    assert ran is False and RULE in note
    assert not _ran(spawns)


def test_a_dry_run_of_a_bash_trigger_the_denylist_refuses_says_a_real_run_is_refused(trigger_home):
    """An automation saved before its command matched a pattern: the dry run listed the command a
    real run would dispatch, and never said the denylist would refuse it."""
    from test_a_trigger_runs_only_what_it_was_granted import _schedule

    from personalclaw.triggers import tools as automation_tools
    from personalclaw.triggers.store import TriggerStore

    bash = {"inline": {"provider": "bash", "config": {"command": DENIED}}}
    _schedule(trigger_home, workflow=bash, capabilities={"providers": ["bash"]})
    store = TriggerStore(base_dir=trigger_home)
    before = automation_tools.run(store, trigger_id="nightly", dry_run=True)
    assert "a real run is refused" not in before.text
    _add_pattern()
    preview = automation_tools.run(store, trigger_id="nightly", dry_run=True)
    assert preview.ok and "nothing was executed" in preview.text
    assert "a real run is refused" in preview.text and RULE in preview.text


def test_saving_a_bash_automation_whose_command_is_refused_is_refused_before_any_consent(
    trigger_home,
):
    """The Triggers page asked "Allow what this trigger runs?" for a command no run could run."""
    from test_a_trigger_runs_only_what_it_was_granted import _body, _req

    from personalclaw.dashboard.handlers import triggers as T
    from personalclaw.triggers.tools import denied_command_refusal

    _add_pattern()
    body = {
        "trigger_type": "schedule",
        "name": "Cloud status",
        "cron": "0 3 1 1 *",
        "action": {"provider": "bash", "config": {"command": DENIED}},
    }
    resp = asyncio.run(
        T.api_trigger_create(_req("POST", "/api/triggers", body=body, match_info={}))
    )
    assert resp.status == 400, _body(resp)
    error = _body(resp)["error"]
    assert error["code"] == "invalid_request" and RULE in error["message"]
    # The chat's and the CLI's door asks the same question.
    refused = denied_command_refusal({"inline": body["action"]})
    assert refused is not None and RULE in refused.text
    ordinary = {"inline": {"provider": "bash", "config": {"command": "echo hello"}}}
    assert denied_command_refusal(ordinary) is None


def test_a_scheduled_bash_fire_the_denylist_refuses_records_the_rule(trigger_home, spawns):
    from personalclaw.gateway import GatewayOrchestrator
    from personalclaw.schedule_history import ScheduleRunStore

    _add_pattern("PcFixture-CloudCtl")
    trigger = SimpleNamespace(
        id="file:notes",
        kind="file",
        workflow={"inline": {"provider": "bash", "config": {"command": DENIED}}},
        capabilities={"providers": ["bash"]},
    )
    asyncio.run(
        object.__new__(GatewayOrchestrator)._fire_store_trigger(
            trigger, {"trigger_id": trigger.id, "kind": trigger.kind}, event="file.changed"
        )
    )
    runs, total = asyncio.run(ScheduleRunStore(trigger_home).list_for_job("file:notes", 0, 20))
    assert total == 1 and RULE in runs[0]["error"]
    assert not _ran(spawns)


# ── an app's setup hook ────────────────────────────────────────────────────────────────────────


def test_an_app_hook_the_denylist_refuses_fails_with_the_rule(tmp_path, monkeypatch):
    import subprocess

    from personalclaw.apps import app_manager

    _add_pattern()
    ran: list[Any] = []

    def _run(cmd, **_kw):
        ran.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _run)
    with pytest.raises(app_manager.AppLifecycleError, match="postInstall hook refused"):
        app_manager._run_hook(f"{ADDED} setup", cwd=tmp_path, timeout=5, env_name="postInstall")
    assert ran == []
    app_manager._run_hook("echo ready", cwd=tmp_path, timeout=5, env_name="postInstall")
    assert ran == ["echo ready"]


# ── Tools → Try it, and a cron script's tool call ──────────────────────────────────────────────


@asynccontextmanager
async def _tools_route(tmp_path: Path, monkeypatch) -> AsyncIterator[Any]:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.handlers.tools import api_tool_invoke
    from personalclaw.tool_providers import registry as tool_registry

    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(workspace))
    monkeypatch.setattr(tool_registry, "_providers", {})
    monkeypatch.setattr(tool_registry, "_provider_app", {})
    app = web.Application()
    app.router.add_post("/api/tools/invoke", api_tool_invoke)
    async with TestClient(TestServer(app)) as http:
        yield http


async def _invoke(http: Any, **body: Any) -> tuple[int, dict]:
    resp = await http.post("/api/tools/invoke", json=body)
    return resp.status, await resp.json()


@pytest.mark.asyncio
async def test_try_it_refuses_a_command_the_denylist_refuses_before_any_confirmation(
    tmp_path, monkeypatch, spawns
):
    """It answered 403 risk_confirmation_required, so the inspector asked the owner to type the
    tool's name, and the tool refused the command after she had."""
    _add_pattern()
    call = {"tool": "bash", "arguments": {"command": DENIED}}
    async with _tools_route(tmp_path, monkeypatch) as http:
        status, body = await _invoke(http, **call)
        assert status == 200, body
        assert body["ok"] is False and body["not_run"] == "refused_by_tool"
        assert RULE in body["error"]
        _status, confirmed = await _invoke(http, **call, confirm_risk="destructive")
        assert confirmed["ok"] is False and RULE in confirmed["error"]
        _status, checked = await _invoke(http, **call, dry_run=True)
        assert checked["ok"] is False and checked["dry_run"] is True and RULE in checked["error"]
    assert not _ran(spawns)


@pytest.mark.asyncio
async def test_try_it_checks_an_ordinary_command_without_running_it_then_runs_it(
    tmp_path, monkeypatch, spawns
):
    _add_pattern()
    call = {"tool": "bash", "arguments": {"command": "echo hello"}}
    async with _tools_route(tmp_path, monkeypatch) as http:
        assert await _invoke(http, **call, dry_run=True) == (200, {"ok": True, "dry_run": True})
        assert spawns == [], "a check runs nothing"
        status, body = await _invoke(http, **call)
        assert status == 200 and body["ok"] is True and "hello" in body["output"]


# ── attended: no card for a call that cannot run ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_attended_bash_call_the_denylist_refuses_gets_no_card(tmp_path):
    """The card read "Safe · Reads only" for the command; allowed, the tool then refused it."""
    from test_a_call_its_tool_will_refuse_asks_nobody import (
        _asks,
        _call,
        _drive,
        _files,
        _refused_unasked,
        _results,
    )

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _add_pattern()
    seen = await _drive(
        [_files(workspace)], [_call("b1", "bash", {"command": DENIED})], cwd=workspace
    )
    assert _asks(seen) == []
    [result] = _results(seen)
    assert RULE in str(result.tool_output)
    _refused_unasked(result, "shell_denylist")

    asked = await _drive(
        [_files(workspace)],
        [_call("b2", "bash", {"command": "echo hello"})],
        cwd=workspace,
        answer="reject",
    )
    assert [a.title for a in _asks(asked)] == ["bash"]


def _real_hooks() -> MagicMock:
    """A context builder whose hooks are the host's real ones, with nothing configured."""
    from personalclaw.hooks import HookManager, HooksConfig

    builder = MagicMock()
    builder.hooks = HookManager(HooksConfig())
    builder.build_message.return_value = ("hello", None)
    return builder


def _asks_to_run(title: str, command: str) -> LLMEvent:
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        title=title,
        tool_kind="execute",
        request_id="req-1",
        tool_input=json.dumps({"command": command}),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("title", [f"Running: {DENIED}", "unknown"])
async def test_a_command_an_agent_cli_asks_the_host_to_run_is_refused_with_no_card(tmp_path, title):
    """Both shapes a CLI asks in: a title carrying the command, and one ("unknown", a bare tool
    name) where only the input does."""
    from test_acp_permission_authority import _drive as drive_chat
    from test_acp_permission_authority import _make_state, _session, _set_stream, _tool_texts

    _add_pattern()
    state, client = _make_state(tmp_path, context_builder=_real_hooks())
    session = _session(task_mode="agent", trust=False)
    _set_stream(client, [_asks_to_run(title, DENIED), LLMEvent(kind=EVENT_COMPLETE)])
    await drive_chat(state, session, answer="approved")

    assert not any(m.get("role") == "permission" for m in session.messages), "a card was raised"
    client.reject_tool.assert_awaited_once_with("req-1")
    client.approve_tool.assert_not_awaited()
    assert any(RULE in text for text in _tool_texts(session)), _tool_texts(session)


@pytest.mark.asyncio
async def test_an_ordinary_command_an_agent_cli_asks_to_run_still_gets_its_card(tmp_path):
    from test_acp_permission_authority import _drive as drive_chat
    from test_acp_permission_authority import _make_state, _session, _set_stream

    _add_pattern()
    state, client = _make_state(tmp_path, context_builder=_real_hooks())
    session = _session(task_mode="agent", trust=False)
    _set_stream(
        client, [_asks_to_run("Running: echo hello", "echo hello"), LLMEvent(kind=EVENT_COMPLETE)]
    )
    await drive_chat(state, session, answer="approved")

    assert any(m.get("role") == "permission" for m in session.messages)
    client.approve_tool.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_command_a_background_call_asks_to_run_is_refused_before_anyone_is_asked():
    from personalclaw.hooks import HookManager, HooksConfig
    from personalclaw.llm_helpers import ToolApprovalPolicy, _resolve_permission

    _add_pattern()
    hooks = HookManager(HooksConfig())
    for command, asked_about in ((DENIED, False), ("echo hello", True)):
        provider, owner = AsyncMock(), AsyncMock(return_value=True)
        await _resolve_permission(
            provider,
            _asks_to_run("unknown", command),
            ToolApprovalPolicy.HOOK_BASED,
            hooks,
            on_tool_approval=owner,
        )
        assert owner.await_count == int(asked_about), command
        if not asked_about:
            provider.reject_tool.assert_awaited_once_with("req-1")


@pytest.mark.asyncio
async def test_a_command_a_subagent_cli_asks_to_run_is_refused_before_its_owner_is_asked():
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw.hooks import HookManager, HooksConfig
    from personalclaw.subagent import SubagentInfo, SubagentManager

    _add_pattern(agent={"approval_mode": "interactive"})
    sessions = _mock_sessions()
    sessions.get_approval_policy = MagicMock(return_value="")
    client = sessions.get_or_create.return_value[0]

    async def _stream(*_a, **_kw):
        yield _asks_to_run("Bash", DENIED)
        yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    client.stream = MagicMock(side_effect=lambda *a, **kw: _stream())
    ctx = _mock_ctx_builder()
    ctx.hooks = HookManager(HooksConfig())
    owner = AsyncMock(return_value=True)
    manager = SubagentManager(
        sessions=sessions, ctx_builder=ctx, is_yolo=lambda: False, on_tool_approval=owner
    )
    info = SubagentInfo(id="denied001", task="look it up", parent_session_key="")
    manager._agents[info.id] = info
    manager._running_count = 1
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        await manager._run(info)

    owner.assert_not_awaited()
    client.reject_tool.assert_awaited()
    client.approve_tool.assert_not_awaited()
