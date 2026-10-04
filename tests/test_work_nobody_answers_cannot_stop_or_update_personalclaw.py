"""Work nobody answers cannot stop, restart, update or reinstall the PersonalClaw it runs in.

The action denylist (`guardrails.denylist.check_action`) refuses unattended work a command that
would stop or replace the gateway running it (`guardrails.self_destruct`): `personalclaw stop`,
`personalclaw update`, its service under its service manager, a kill aimed at it. A trigger's fire,
a hook, a workflow's action step, a tile's refresh and the triage digest asked it. These paths run
a command or an action with nobody answering too, and did not:

* a webhook's fire from an outside caller, a view's refresh, and a Run now an agent starts
  (`automation_run`) from a session nobody is in, then all through the hand-run dispatch
  (`trigger_runs._dispatch_store_action`), ran the action as it was written (a webhook's fire and
  a view's refresh now run through the dispatch every fire runs through, which asks it);
* a loop's check and a workflow's verify gate (`loop.gates.run_verify_command`), a workflow's setup
  or teardown step (`workflows.provisioning.run_step`), an effect's teardown
  (`workflows.effects.run_teardown`) and the agent's own bash tool in a session nobody is in asked
  only the shell denylist, which reads a command's text: it let `personalclaw stop` run, and
  refused `personalclaw update` in other words than the rule's.

What these tests hold every one of them to: neither command reaches its provider or a shell, and
the path says why in the one sentence the rule composes (`DenyDecision.refusal`): the rule's code
and why. The controls: an ordinary command on every path still runs; your own Run now of the same
trigger still runs, since you are the one answering it; and an agent in a chat you are in runs it
as that chat runs its own work.

No refused command goes near a real shell: a provider is a recorder, and the spawner refuses to
start anything that names PersonalClaw, so a missing check is a recorded call instead of a stopped
or updated gateway.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace

import fire_dispatch
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

import personalclaw.action_providers as AP
from personalclaw.action_providers.base import ActionResult
from personalclaw.dashboard.handlers import trigger_runs
from personalclaw.triggers.models import Trigger

STOP = "personalclaw stop"
UPDATE = "personalclaw update"
ORDINARY = "echo nightly backup done"

#: What the refusal of each command says it would do.
WOULD = {
    STOP: "it would stop the PersonalClaw gateway that is executing it",
    UPDATE: "it would update the PersonalClaw gateway that is executing it",
}

#: A session nobody is in: a scheduled job's turn.
NOBODY = "cron:nightly-digest"
#: A chat you are in.
YOURS = "main"


def _says_why(said: str, command: str) -> bool:
    """Whether *said* is the refusal of *command*: the rule's code, then why, naming the command."""
    rule = f"self_destruct:{command.split()[-1]}"
    return (
        f"blocked by the guardrails denylist: {rule} — " in said
        and WOULD[command] in said
        and f"`{command}`" in said
    )


# ── what reached a provider, and what reached a shell ──────────────────────────────────────────


class _Recorder:
    """Stands in for every action provider, so "did the action run?" is a list, never a command."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, action_config, ctx, timeout=30):
        self.calls.append(dict(action_config))
        return ActionResult(success=True, stdout="done")

    def ran(self, command: str) -> bool:
        return any(call.get("command") == command for call in self.calls)


class _ReachedAShell(Exception):
    """A command naming PersonalClaw got as far as the spawner, which did not start it."""


class _Shell:
    """Every command the sandbox spawner was asked to start."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def ran(self, command: str) -> bool:
        return any(command in line for line in self.asked)


@pytest.fixture
def recorder(monkeypatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(AP, "get_action_provider", lambda name: rec)
    return rec


@pytest.fixture
def shell(monkeypatch) -> _Shell:
    """The spawner every command path starts its command through. One naming PersonalClaw is
    recorded and never started; any other is started for real, so an ordinary command runs."""
    from personalclaw import sandbox

    real = sandbox.create_subprocess_limited
    seen = _Shell()

    async def spawner(*argv, **kwargs):
        line = " ".join(str(a) for a in argv)
        seen.asked.append(line)
        if "personalclaw" in line:
            raise _ReachedAShell(line)
        return await real(*argv, **kwargs)

    monkeypatch.setattr(sandbox, "create_subprocess_limited", spawner)
    return seen


@pytest.fixture
def work(tmp_path) -> Path:
    """The folder a check, a step or a shell call runs in."""
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture(autouse=True)
def _one_home(monkeypatch):
    """One home for the trigger routes and the gateway's dispatch, as a running gateway has: the
    suite gives the trigger routes a store of their own, and the dispatch every fire runs through
    writes where the gateway's home is."""
    from personalclaw.config.loader import config_dir

    home = config_dir()
    monkeypatch.setattr("personalclaw.dashboard.handlers.triggers.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.gateway.config_dir", lambda: home)


@pytest.fixture(autouse=True)
def _fresh_webhook_rates():
    from personalclaw.inbound import caps

    caps.reset_for_tests()
    yield
    caps.reset_for_tests()


# ── the paths ──────────────────────────────────────────────────────────────────────────────────


def _home() -> Path:
    """This test's home, asked where it is used (the suite moves it for every test)."""
    from personalclaw.config.loader import config_dir

    return config_dir()


def _store():
    """The trigger store the trigger routes read and write (`handlers.triggers._trigger_store`)."""
    from personalclaw.dashboard.handlers import triggers

    return triggers._trigger_store()


def _bash(command: str) -> dict:
    return {"inline": {"provider": "bash", "config": {"command": command}}}


def _trigger(tid: str, kind: str, command: str, spec: dict) -> None:
    _store().upsert(
        Trigger(
            id=tid,
            name=f"Job {tid}",
            kind=kind,
            enabled=True,
            created_by="user",
            spec=spec,
            workflow=_bash(command),
            capabilities={"providers": ["bash"]},
        )
    )


def _newest_run(tid: str, runs=None) -> str:
    """What the trigger's newest history row says: its error, or its status when it ran. In the
    run history the trigger routes write (`handlers.triggers._runs_store`), unless *runs* is
    another."""
    from personalclaw.dashboard.handlers import triggers

    runs = runs if runs is not None else triggers._runs_store()
    rows, _total = asyncio.run(runs.list_for_job(tid, 0, 5))
    if not rows:
        return ""
    return str(rows[0].get("error") or rows[0].get("status") or "")


class _State:
    """The dashboard state the fire-and-forget handlers track their tasks on, carrying the gateway's
    fire dispatch a webhook's fire and a view's refresh run through."""

    def __init__(self) -> None:
        self._background_tasks: set[asyncio.Task] = set()
        fire_dispatch.attach(self)


async def _a_webhooks_fire(command: str, rec: _Recorder, _sh: _Shell, _work: Path):
    """An outside caller fires a webhook automation with its scoped token."""
    from personalclaw.inbound import clients

    _trigger("webhook:deploy", "webhook", command, {})
    _client, token = clients.create_client(
        "deployer", surfaces=["webhook"], scope={"trigger": "store:webhook:deploy"}
    )
    state = _State()
    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/triggers/{id}/fire", trigger_runs.api_trigger_fire)
    async with TestClient(TestServer(app)) as http:
        resp = await http.post(
            "/api/triggers/store:webhook:deploy/fire",
            data="deploy finished",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status == 202, await resp.text()
        await asyncio.gather(*state._background_tasks)
    return await asyncio.to_thread(_newest_run, "webhook:deploy"), rec.ran(command)


async def _a_views_refresh(command: str, rec: _Recorder, _sh: _Shell, _work: Path):
    """A render surface opens, and the `view` automation bound to it refreshes."""
    _trigger(
        "view:status", "view", command, {"surface_binding": "artifact.status", "ttl_secs": 300}
    )
    state = _State()
    app = web.Application()
    app["state"] = state
    request = make_mocked_request("POST", "/api/triggers/view/render", app=app)
    request["user"] = "owner"

    async def _json():
        return {"surface": "artifact.status"}

    request.json = _json  # type: ignore[assignment]
    answer = json.loads((await trigger_runs.api_trigger_view_render(request)).body)
    assert answer["refreshed"] == ["view:status"], answer
    await asyncio.gather(*state._background_tasks)
    return await asyncio.to_thread(_newest_run, "view:status"), rec.ran(command)


def _run_now(*, by_agent_in: str = "") -> web.Request:
    """A Run now of `clock:nightly`: yours from the dashboard, or an agent's `automation_run`,
    which posts with the gateway's internal credential naming its session (`mcp_core._post`)."""
    headers = {"X-Internal-Secret": "pcfixture", "X-Session-Key": by_agent_in}
    app = web.Application()
    app["state"] = SimpleNamespace(push_refresh=lambda *k: None, _background_tasks=set())
    request = make_mocked_request(
        "POST",
        "/api/triggers/schedule:clock:nightly/run",
        match_info={"id": "schedule:clock:nightly"},
        app=app,
        headers=headers if by_agent_in else None,
    )
    request["user"] = "owner"

    async def _json():
        return {}

    request.json = _json  # type: ignore[assignment]
    return request


async def _run_now_by(session: str, command: str, rec: _Recorder) -> tuple[str, bool]:
    _trigger("clock:nightly", "clock", command, {"kind": "cron", "expr": "0 9 * * *"})
    answer = json.loads((await trigger_runs.api_trigger_run(_run_now(by_agent_in=session))).body)
    return str(answer.get("result") or answer.get("refused") or ""), rec.ran(command)


async def _an_unattended_agents_run_now(command: str, rec: _Recorder, _sh: _Shell, _work: Path):
    """A scheduled job's agent runs an automation by its `automation_run` tool."""
    return await _run_now_by(NOBODY, command, rec)


async def _a_loops_check(command: str, _rec: _Recorder, sh: _Shell, work: Path):
    from personalclaw.loop.gates import CheckReport, run_verify_command

    report = CheckReport()
    ok = await run_verify_command(command, str(work), label="verify", report=report)
    return (report.not_run if ok is None else f"ran: {ok}"), sh.ran(command)


async def _a_workflows_verify_gate(command: str, _rec: _Recorder, sh: _Shell, work: Path):
    from personalclaw.loop.gates import CheckRefused
    from personalclaw.workflows.verify import run_verify_block

    try:
        ok = await run_verify_block({"command": command, "label": "tests"}, default_cwd=str(work))
    except CheckRefused as refused:
        return str(refused), sh.ran(command)
    return f"ran: {ok}", sh.ran(command)


async def _a_workflows_setup_step(command: str, _rec: _Recorder, sh: _Shell, work: Path):
    from personalclaw.workflows.provisioning import run_step

    ok, detail = await run_step(command, work, run_id="run-chores")
    return ("ran" if ok else detail), sh.ran(command)


async def _an_effects_teardown(command: str, _rec: _Recorder, sh: _Shell, _work: Path):
    from personalclaw.workflows.effects import run_teardown

    ok, detail = await run_teardown(command, "resource-1")
    return ("ran" if ok else detail), sh.ran(command)


async def _an_unattended_agents_bash(command: str, _rec: _Recorder, sh: _Shell, work: Path):
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    tools = NativeBuiltinToolProvider(work, sandbox_mode="off", session_key=NOBODY)
    result = await tools.invoke("bash", {"command": command})
    return ("ran" if result.success else result.error), sh.ran(command)


Path_ = Callable[[str, _Recorder, _Shell, Path], Awaitable[tuple[str, bool]]]

#: Every door a command or an action comes through with nobody answering it.
UNATTENDED: dict[str, Path_] = {
    "webhook-fire": _a_webhooks_fire,
    "view-refresh": _a_views_refresh,
    "agent-run-now": _an_unattended_agents_run_now,
    "loop-check": _a_loops_check,
    "workflow-verify-gate": _a_workflows_verify_gate,
    "workflow-setup-step": _a_workflows_setup_step,
    "effect-teardown": _an_effects_teardown,
    "agent-bash": _an_unattended_agents_bash,
}


@pytest.mark.parametrize("command", [STOP, UPDATE], ids=["stop", "update"])
@pytest.mark.parametrize("door", sorted(UNATTENDED))
def test_nothing_unattended_stops_or_updates_personalclaw(door, command, recorder, shell, work):
    said, reached = asyncio.run(UNATTENDED[door](command, recorder, shell, work))

    assert not reached, f"{door}: {command!r} reached its provider or a shell"
    assert _says_why(said, command), f"{door} did not say why: {said!r}"


@pytest.mark.parametrize("door", sorted(UNATTENDED))
def test_an_ordinary_command_still_runs_on_every_door(door, recorder, shell, work):
    """The control: the check passes ordinary work, and does not merely exist."""
    said, reached = asyncio.run(UNATTENDED[door](ORDINARY, recorder, shell, work))

    assert reached, f"{door}: an ordinary command did not run ({said!r})"
    assert "blocked by the guardrails denylist" not in said, said


# ── who is answering decides, and nobody else ──────────────────────────────────────────────────


def test_your_own_run_now_is_not_held_to_it(recorder):
    """You pressed Run: you are answering it, so it runs as it always did (and you can stop
    PersonalClaw from your own shell or Settings → Updates in any case)."""
    said, reached = asyncio.run(_run_now_by("", STOP, recorder))

    assert reached, f"your Run now was refused: {said!r}"


def test_an_agent_in_a_chat_you_are_in_runs_it_as_that_chat_runs_its_own_work(
    recorder, shell, work
):
    """An agent's tool is not attended because an agent called it, nor refused because it did: its
    run is held as its session holds its own work. In a chat you are in, that is not the
    unattended rule, for its Run now as for its own shell."""
    said, reached = asyncio.run(_run_now_by(YOURS, STOP, recorder))
    assert reached, f"an agent's Run now in your chat was refused: {said!r}"

    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    tools = NativeBuiltinToolProvider(work, sandbox_mode="off", session_key=YOURS)
    asyncio.run(tools.invoke("bash", {"command": STOP}))
    assert shell.ran(STOP), "the bash tool in your chat refused it before it reached the spawner"


def test_the_restart_reviews_run_now_is_yours_and_not_held_to_it(recorder):
    """The restart review's Run now is a run you start yourself, as the Run button's is."""
    from personalclaw.dashboard.handlers import triggers
    from personalclaw.triggers import review

    _trigger("clock:nightly", "clock", STOP, {"kind": "cron", "expr": "0 9 * * *"})
    review.record(
        [review.ReviewCard(trigger_id="clock:nightly", kind="missed", count=1, latest=1, oldest=1)],
        base_dir=_store().base_dir,
    )
    app = web.Application()
    app["state"] = SimpleNamespace(push_refresh=lambda *k: None, _background_tasks=set())
    request = make_mocked_request("POST", "/api/triggers/review", app=app)
    request["user"] = "owner"

    async def _json():
        return {"trigger_id": "clock:nightly", "kind": "missed", "action": "run_now"}

    request.json = _json  # type: ignore[assignment]
    answer = json.loads(asyncio.run(triggers.api_trigger_review(request)).body)

    assert recorder.ran(STOP), answer


def test_a_lifecycle_hooks_refusal_says_the_rules_code_and_sentence(recorder):
    """A hook was refused in words of its own; it says what a trigger's fire says now."""
    from personalclaw.hooks import ScriptHook, run_script_hook

    hook = ScriptHook(
        id="h-release",
        name="release notes",
        event="SessionStart",
        provider="bash",
        provider_config={"command": STOP},
        capabilities={"providers": ["bash"]},
    )
    result = asyncio.run(run_script_hook(hook, "", {"event": "SessionStart"}))

    assert recorder.calls == []
    assert result.error.startswith("blocked by the guardrails denylist: self_destruct:stop — ")
    assert _says_why(result.error, STOP), result.error


def test_an_agent_in_a_subagents_turn_is_held_to_it(recorder):
    """Another session nobody is in: a subagent's."""
    said, reached = asyncio.run(_run_now_by("subagent:research-1", UPDATE, recorder))

    assert not reached and _says_why(said, UPDATE), said


# ── what each refusal leaves behind ────────────────────────────────────────────────────────────


def _security_rows() -> list[dict]:
    path = _home() / "security_events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_a_refused_webhook_fire_is_in_the_security_log_and_its_history(recorder, shell, work):
    said, _reached = asyncio.run(_a_webhooks_fire(STOP, recorder, shell, work))

    rows = [r for r in _security_rows() if r.get("operation") == "guardrails.denylist"]
    assert [(r["caller_identity"], r["outcome"]) for r in rows] == [("action:bash", "blocked")]
    assert "self_destruct:stop" in rows[0]["resources"]
    assert rows[0]["metadata"]["command"] == STOP
    assert _says_why(said, STOP), "the run's history row does not say why"


def test_a_webhook_fire_and_a_scheduled_fire_refused_by_the_rule_say_the_same(
    recorder, shell, work, monkeypatch
):
    """The same rule, the same sentence: a webhook's fire and the trigger's own clock fire."""
    from personalclaw.gateway import GatewayOrchestrator

    said, _reached = asyncio.run(_a_webhooks_fire(STOP, recorder, shell, work))
    clock = SimpleNamespace(
        id="clock:nightly",
        kind="clock",
        workflow=_bash(STOP),
        capabilities={"providers": ["bash"]},
    )
    asyncio.run(
        object.__new__(GatewayOrchestrator)._fire_store_trigger(clock, {"trigger_id": clock.id})
    )

    from personalclaw.schedule_history import ScheduleRunStore

    assert recorder.calls == []
    assert _newest_run("clock:nightly", ScheduleRunStore(_home())) == said


@pytest.mark.parametrize(
    "door, source",
    [
        ("loop-check", "loop_gate"),
        ("workflow-verify-gate", "loop_gate"),
        ("workflow-setup-step", "workflow"),
        ("effect-teardown", "workflow"),
    ],
)
def test_a_refused_command_paths_row_names_the_rule(door, source, recorder, shell, work):
    asyncio.run(UNATTENDED[door](STOP, recorder, shell, work))

    rows = [r for r in _security_rows() if r.get("event_type") == "command_refused"]
    assert [(r["source"], r["metadata"]["control"]) for r in rows] == [(source, "action_denylist")]
    assert rows[0]["metadata"]["rule"] == "self_destruct:stop"
    assert _says_why(rows[0]["resources"], STOP)
    assert rows[0]["metadata"]["command"].startswith(STOP)


def test_the_bash_tools_refusal_names_the_control_and_rule_for_its_audit_row(shell, work):
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
    from personalclaw.llm.events import TOOL_META_REFUSED_BY, TOOL_META_REFUSED_RULE

    tools = NativeBuiltinToolProvider(work, sandbox_mode="off", session_key=NOBODY)
    refused = asyncio.run(tools.preflight("bash", {"command": STOP}))

    assert refused is not None and not refused.success, "the pre-flight let it through"
    assert refused.metadata[TOOL_META_REFUSED_BY] == "action_denylist"
    assert refused.metadata[TOOL_META_REFUSED_RULE] == "self_destruct:stop"
    assert _says_why(refused.error, STOP)
    assert not shell.ran(STOP)


@pytest.mark.asyncio
async def test_a_scheduled_scripts_shell_call_is_refused_and_in_the_security_log(
    tmp_path, monkeypatch, shell
):
    """A scheduled script calls the bash tool through the gateway, naming its job's work; the
    call is refused before it runs, and the security log keeps the refusal."""
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
        resp = await http.post(
            "/api/tools/invoke",
            json={"tool": "bash", "arguments": {"command": STOP}},
            headers={"X-Session-Key": NOBODY},
        )
        body = await resp.json()

    assert resp.status == 200 and body["ok"] is False, body
    assert _says_why(body["error"], STOP), body
    rows = [
        r
        for r in _security_rows()
        if r.get("event_type") == "tool_invocation" and r.get("operation") == "bash"
    ]
    assert [r["outcome"] for r in rows] == ["refused"], rows
    meta = rows[0]["metadata"]
    assert (meta["control"], meta["rule"]) == ("action_denylist", "self_destruct:stop"), meta
    assert not shell.ran(STOP)


def test_a_loop_whose_check_would_stop_personalclaw_pauses_and_says_why(
    monkeypatch, tmp_path, shell
):
    """No later cycle changes a refusal, so the loop stops cycling toward its budget and asks its
    owner, in the rule's words."""
    from test_loop_watchdog import _FakeSession, _run, _running, _wd, _write_finding

    from personalclaw.loop import files as loop_files
    from personalclaw.loop import manager, store
    from personalclaw.loop.loop import LoopStatus

    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    loop = _running(kind_config={"goal_type": "verifiable", "verify_command": STOP})
    wd = _wd()
    events: list[str] = []
    wd._publish = lambda lid, event, data=None: events.append(event)  # type: ignore[method-assign]
    key = manager.session_key(loop.id)
    wd._state._sessions[key] = _FakeSession(key)
    _run(wd._poll_once())
    _write_finding(loop.id, 1)
    _run(wd._poll_once())

    assert store.get(loop.id).status == LoopStatus.NEEDS_INPUT.value
    asked = (loop_files.pending_question(loop.id) or {}).get("question", "")
    assert _says_why(asked, STOP), asked
    assert "needs_input" in events and "judge_error" not in events
    assert not shell.ran(STOP)


@pytest.mark.asyncio
async def test_a_workflow_whose_verify_gate_would_stop_personalclaw_ends_saying_why(
    monkeypatch, tmp_path, shell
):
    from test_workflows_a_failed_check_ends_the_run import _check, _judge_says
    from test_workflows_a_failed_check_ends_the_run import _run as run_workflow
    from test_workflows_a_failed_check_ends_the_run import _spec, _started, _transform

    from personalclaw.workflows.controller import EngineServices
    from personalclaw.workflows.models import RunStatus
    from personalclaw.workflows.verify import run_verify_block

    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: runs)
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: runs, raising=False)
    check = _check("verify", "verify_command", verify={"command": STOP, "label": "tests"})
    services = EngineServices(completion=_judge_says("PASS"), verify=run_verify_block)
    run = await run_workflow(_spec(check, _transform("handoff")), services=services)

    assert run.run.status == RunStatus.FAILED
    assert _says_why(run.run.error_message, STOP), run.run.error_message
    assert "handoff" not in _started(run.run.id, control="verify")
    assert not shell.ran(STOP)
