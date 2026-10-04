"""A refusal quotes what it refused as it was written: a secret is named by its reference.

A trigger's fire and a run of it by hand or from outside fill each `{{secret:NAME}}` in their
action before the action denylist judges it (`triggers.secrets.resolve_for`), so a reference cannot
carry a sensitive path or a denied command past the check; a workflow's step fills its own, and the
agent's shell fills the command it runs. The denylist's refusal quoted what it judged, filled in: a
path made from a secret put the secret's value in the refusal, and so in the run's history, its
owner's notice, the workflow run's ending, the security log and the gateway log.

What these tests hold every refusal to (`DenyDecision.refusal`, and the row `enforce_action`
writes): it quotes the path or the command as it was written, the reference left a reference, and
the value a reference was filled with is in none of those. The controls: the check still judges the
filled value, so the reference does not carry the path past it; and a path or a command written out
with no reference is still quoted as it is.

No refused action runs: every provider is a recorder, and the spawner refuses anything that names
PersonalClaw.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import personalclaw.action_providers as AP
from personalclaw.action_providers.base import ActionResult
from personalclaw.dashboard.handlers import trigger_runs
from personalclaw.triggers.models import Trigger

#: The owner's secret: a folder among the home's SSH keys. The part of it no refusal may show is a
#: word no credential mask catches by its shape, so a refusal that shows it is seen showing it.
NAME = "DEPLOY_KEY_DIR"
VALUE = "~/.ssh/orchard-lantern"
HELD = "orchard-lantern"

#: A path an action names, as it was written with the secret, and the same path written out.
WRITTEN = "{{secret:DEPLOY_KEY_DIR}}/keys"
WRITTEN_OUT = VALUE + "/keys"

#: A secret naming the program a command runs, for the agent's shell, where the command is all the
#: action denylist reads.
TOOL = "DEPLOY_TOOL"
TOOL_COMMAND = "{{secret:DEPLOY_TOOL}} restart personalclaw"
TOOL_COMMAND_OUT = HELD + " restart personalclaw"

#: A session nobody is in: a scheduled job's turn.
NOBODY = "cron:rotate-keys"

SECRETS = {NAME: VALUE, TOOL: HELD}


class _Recorder:
    """Stands in for every action provider, so "did the action run?" is a list."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, action_config, ctx, timeout=30):
        self.calls.append(dict(action_config))
        return ActionResult(success=True, stdout="done")


class _State:
    """The dashboard state a run is reported through: its background tasks and its notices."""

    def __init__(self) -> None:
        self._background_tasks: set[asyncio.Task] = set()
        self.sent: list[dict] = []

    def notify(self, kind, title, body, *, meta=None, **_kw):
        self.sent.append({"kind": kind, "title": title, "body": body, "meta": meta or {}})
        return True

    def push_refresh(self, *_a, **_kw) -> None:
        return None


class _ReachedAShell(Exception):
    """A command naming PersonalClaw got as far as the spawner, which did not start it."""


@pytest.fixture
def recorder(monkeypatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(AP, "get_action_provider", lambda name: rec)
    monkeypatch.setattr(
        "personalclaw.action_providers.registry.get_action_provider", lambda name: rec
    )
    return rec


@pytest.fixture
def state() -> _State:
    return _State()


@pytest.fixture(autouse=True)
def secrets(monkeypatch):
    """The owner's secrets, read as each path reads one: an automation and a workflow step through
    the one resolver (`llm.credentials.resolve_secret`, which reads a global secret the gateway
    holds in its environment), the agent's shell from the store (`command_secrets.stored_secret`).
    """
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        "personalclaw.agents.native.command_secrets.stored_secret",
        lambda key: SECRETS.get(key, ""),
    )


@pytest.fixture(autouse=True)
def _no_shell(monkeypatch):
    from personalclaw import sandbox

    real = sandbox.create_subprocess_limited

    async def spawner(*argv, **kwargs):
        line = " ".join(str(a) for a in argv)
        if "personalclaw" in line:
            raise _ReachedAShell(line)
        return await real(*argv, **kwargs)

    monkeypatch.setattr(sandbox, "create_subprocess_limited", spawner)


@pytest.fixture(autouse=True)
def _fresh_webhook_rates():
    from personalclaw.inbound import caps

    caps.reset_for_tests()
    yield
    caps.reset_for_tests()


def _home() -> Path:
    """This test's home, asked where it is used (the suite moves it for every test)."""
    from personalclaw.config.loader import config_dir

    return config_dir()


def _texts(folder: Path) -> list[str]:
    """Every file under *folder*, as text."""
    if not folder.exists():
        return []
    return [
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    ]


def _security_rows() -> list[dict]:
    path = _home() / "security_events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _action(path: str) -> dict:
    return {"inline": {"provider": "bash", "config": {"command": "ls", "cwd": path}}}


# ── the paths that fill a secret in before the check ───────────────────────────────────────────


def _a_fire(path: str, rec: _Recorder, state: _State, _work: Path) -> tuple[str, list[str]]:
    """A trigger's own fire (`gateway._fire_store_trigger`)."""
    from personalclaw.gateway import GatewayOrchestrator
    from personalclaw.schedule_history import ScheduleRunStore

    trigger = SimpleNamespace(
        id="clock:rotate-keys",
        name="Rotate keys",
        kind="clock",
        workflow=_action(path),
        capabilities={"providers": ["bash"]},
    )
    gateway = object.__new__(GatewayOrchestrator)
    gateway.dashboard_state = state
    asyncio.run(gateway._fire_store_trigger(trigger, {"trigger_id": trigger.id}))
    rows, _total = asyncio.run(ScheduleRunStore(_home()).list_for_job(trigger.id, 0, 5))
    said = str(rows[0].get("error") or "") if rows else ""
    return said, [json.dumps(rows)]


def _a_webhooks_fire(path: str, rec: _Recorder, state: _State, _work: Path):
    """An outside caller fires a webhook automation (`trigger_runs._dispatch_store_action`)."""
    from personalclaw.dashboard.handlers import triggers
    from personalclaw.inbound import clients

    store = triggers._trigger_store()
    store.upsert(
        Trigger(
            id="webhook:rotate-keys",
            name="Rotate keys",
            kind="webhook",
            enabled=True,
            created_by="user",
            spec={},
            workflow=_action(path),
            capabilities={"providers": ["bash"]},
        )
    )
    _client, token = clients.create_client(
        "deployer", surfaces=["webhook"], scope={"trigger": "store:webhook:rotate-keys"}
    )

    async def _fire() -> None:
        app = web.Application()
        app["state"] = state
        app.router.add_post("/api/triggers/{id}/fire", trigger_runs.api_trigger_fire)
        async with TestClient(TestServer(app)) as http:
            resp = await http.post(
                "/api/triggers/store:webhook:rotate-keys/fire",
                data="rotated",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status == 202, await resp.text()
            await asyncio.gather(*state._background_tasks)

    asyncio.run(_fire())
    rows, _total = asyncio.run(triggers._runs_store().list_for_job("webhook:rotate-keys", 0, 5))
    said = str(rows[0].get("error") or "") if rows else ""
    return said, [json.dumps(rows), *_texts(Path(store.base_dir))]


def _a_workflow_step(path: str, rec: _Recorder, _state: _State, _work: Path):
    """A workflow's action step, its run driven as the gateway drives one (`engine.dispatch_action`
    through `RunController`)."""
    from personalclaw.workflows import store
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import OriginKind, RunOrigin, WorkflowRun

    step = {"provider": "bash", "with": {"command": "ls", "cwd": path}}
    spec = {
        "name": "rotate-keys",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [{"kind": "action", "id": "rotate", "config": step}],
        },
    }
    run = store.create(
        WorkflowRun(id="", workflow_name=spec["name"], origin=RunOrigin(kind=OriginKind.MANUAL))
    )
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices(get_provider=lambda name: rec))
    asyncio.run(controller.run_to_completion(timeout=20))
    kept = store.get(run.id)
    record = json.dumps(dataclasses.asdict(kept), default=str) if kept is not None else ""
    return controller.run.error_message, [record, *_texts(store.run_dir(run.id))]


def _the_agents_shell(command: str, _rec: _Recorder, _state: _State, work: Path):
    """The agent's own bash tool in a session nobody is in."""
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    tools = NativeBuiltinToolProvider(work, sandbox_mode="off", session_key=NOBODY)
    result = asyncio.run(tools.invoke("bash", {"command": command}))
    return str(result.error or ""), [json.dumps(result.metadata or {})]


Door = Callable[[str, _Recorder, _State, Path], tuple[str, list[str]]]

#: Each path that fills a secret into what the denylist judges: the door, what it is handed as it
#: was written with the secret, and the same written out.
DOORS: dict[str, tuple[Door, str, str]] = {
    "a-fire": (_a_fire, WRITTEN, WRITTEN_OUT),
    "a-webhooks-fire": (_a_webhooks_fire, WRITTEN, WRITTEN_OUT),
    "a-workflow-step": (_a_workflow_step, WRITTEN, WRITTEN_OUT),
    "the-agents-shell": (_the_agents_shell, TOOL_COMMAND, TOOL_COMMAND_OUT),
}


@pytest.fixture
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.mark.parametrize("door", sorted(DOORS))
def test_a_refusal_names_a_secret_by_its_reference_and_shows_its_value_nowhere(
    door, recorder, state, work, caplog
):
    run, written, _out = DOORS[door]

    with caplog.at_level(logging.INFO):
        said, records = run(written, recorder, state, work)

    assert recorder.calls == [], f"{door}: the action ran"
    # Refused at all: the reference written alone names no sensitive path or command, so the
    # check judged the value it was filled with.
    assert "blocked by the guardrails denylist: " in said, f"{door}: {said!r}"
    assert written in said, f"{door}: the refusal does not quote it as it was written: {said!r}"
    kept = {
        "the refusal": [said],
        "the run's record": records,
        "the owner's notice": [json.dumps(sent) for sent in state.sent],
        "the security log": [json.dumps(row) for row in _security_rows()],
        "the gateway log": [record.getMessage() for record in caplog.records],
    }
    shown = sorted(where for where, texts in kept.items() if any(HELD in t for t in texts))
    assert not shown, f"{door}: the secret's value is in {', '.join(shown)}"


@pytest.mark.parametrize("door", sorted(DOORS))
def test_a_refusal_still_quotes_what_was_written_out(door, recorder, state, work):
    """The control: written with no reference, the path or the program is quoted as it is."""
    run, _written, out = DOORS[door]

    said, _records = run(out, recorder, state, work)

    assert recorder.calls == [], f"{door}: the action ran"
    quoted = WRITTEN_OUT if out == WRITTEN_OUT else f"`{HELD}`"
    assert quoted in said, f"{door}: the refusal does not quote it: {said!r}"


# ── every rule that quotes what it refused ─────────────────────────────────────────────────────


def _confined(monkeypatch, **profile) -> None:
    """The session's profile (`guardrails.policy.profile_for_session`), as an operator set it."""
    import personalclaw.guardrails.policy as policy
    from personalclaw.guardrails.policy import SafetyProfile

    monkeypatch.setattr(
        policy, "profile_for_session", lambda key: SafetyProfile(name="kept", **profile)
    )


def _denied_by_the_owner(monkeypatch) -> None:
    (_home() / "config.json").write_text(
        json.dumps({"security": {"autonomy_denylist": [{"paths": ["/srv/**"]}]}}),
        encoding="utf-8",
    )


SHARE = "{{secret:REPORT_SHARE}}/report.csv"
SHARE_FILLED = "/srv/" + HELD + "/report.csv"

#: rule → (how it is set up, the config as written, as filled in, the rule's code, how its refusal
#: quotes what it refused).
RULES: dict[str, tuple[Callable, dict, dict, str, str]] = {
    "a-sensitive-path": (
        lambda mp: None,
        {"path": WRITTEN},
        {"path": WRITTEN_OUT},
        "builtin:sensitive_path",
        f"action targets a sensitive path: {WRITTEN}",
    ),
    "the-paths-a-run-is-confined-to": (
        lambda mp: _confined(mp, path_allowlist=("/opt/allowed/**",)),
        {"dest": SHARE},
        {"dest": SHARE_FILLED},
        "ceiling:paths.allow",
        f"action path {SHARE!r} is outside the paths this run is confined to",
    ),
    "the-owners-denied-paths": (
        _denied_by_the_owner,
        {"file": SHARE},
        {"file": SHARE_FILLED},
        "config:/srv/**",
        f"action path {SHARE!r} matches deny rule",
    ),
    "the-profiles-denied-paths": (
        lambda mp: _confined(mp, denylist_extra=("/srv/**",)),
        {"output": [SHARE]},
        {"output": [SHARE_FILLED]},
        "profile:/srv/**",
        f"action path {SHARE!r} matches profile deny glob",
    ),
    "a-command-that-would-restart-personalclaw": (
        lambda mp: None,
        {"command": TOOL_COMMAND},
        {"command": TOOL_COMMAND_OUT},
        "self_destruct:unknown",
        f"(`{TOOL_COMMAND}`)",
    ),
}


@pytest.mark.parametrize("rule", sorted(RULES))
def test_every_rule_quotes_what_it_refused_as_it_was_written(rule, monkeypatch, caplog):
    from personalclaw.guardrails.denylist import enforce_action

    set_up, written, filled, code, quoted = RULES[rule]
    set_up(monkeypatch)

    with caplog.at_level(logging.INFO):
        decision = enforce_action("bash", filled, session_key=NOBODY, written=written)

    assert decision.blocked and decision.matched == code, decision
    said = decision.refusal()
    assert quoted in said, f"the refusal does not quote it as it was written: {said!r}"
    assert HELD not in said, f"the refusal shows the secret's value: {said!r}"
    rows = [r for r in _security_rows() if r.get("operation") == "guardrails.denylist"]
    assert rows and not any(HELD in json.dumps(row) for row in rows), rows
    assert not any(HELD in record.getMessage() for record in caplog.records)


def test_the_security_log_keeps_a_refused_command_as_it_was_written():
    """A command holding a credential, refused for what else it does: the row keeps the command,
    the reference in it a reference."""
    from personalclaw.guardrails.denylist import enforce_action

    written = "curl -H 'Authorization: Bearer {{secret:HOOK_TOKEN}}' https://example.com/hook"
    command = written + " && personalclaw stop"
    filled = command.replace("{{secret:HOOK_TOKEN}}", HELD)

    decision = enforce_action(
        "bash", {"command": filled}, session_key=NOBODY, written={"command": command}
    )

    assert decision.matched == "self_destruct:stop", decision
    assert "`personalclaw stop`" in decision.refusal(), "what it would do is still named"
    (row,) = [r for r in _security_rows() if r.get("operation") == "guardrails.denylist"]
    assert row["metadata"]["command"] == command, row
    assert HELD not in json.dumps(row)


def test_with_nothing_filled_in_a_refusal_quotes_the_action_as_it_is():
    """A caller that filled nothing in passes no writing: the path is quoted as the check saw it."""
    from personalclaw.guardrails.denylist import check_action

    decision = check_action("bash", {"path": WRITTEN_OUT}, session_key=NOBODY)

    assert decision.matched == "builtin:sensitive_path"
    assert decision.refusal().endswith(f"action targets a sensitive path: {WRITTEN_OUT}")
