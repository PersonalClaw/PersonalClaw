"""A run in a project reads that project's secret first, and the global one second.

Settings → Secrets saves a secret for one project (the store keeps it under the project's own key),
and nothing read it. Measured on ``integration`` before this change:

* a workflow step in the project resolved ``{{secret:NAME}}`` to the GLOBAL value (or failed "is not
  set" when there was no global one), so the project's own secret was dead weight;
* run start refused the project's run for a secret only the project held;
* the agent's shell fill (the ``bash`` tool, for a stage's agent and a loop's worker) read the
  global value, and neither a workflow stage's agent nor the agent an ``invoke-agent`` or
  ``run-prompt`` step starts knew its run's project;
* any run, automation, settings record or app could read a project's secret by spelling its stored
  key, ``{{secret:PCPROJ_<project>__NAME}}``;
* every project's secrets were copied into the gateway's environment, which every child it starts
  inherits;
* a run's record said nothing about which secret a step used.

The rule now, through one resolver (``llm.credentials.resolve_secret``): a run that belongs to a
project reads the project's secret first and the global one second; a run with no project — an
automation, a settings record, an app — reads only the global one; nothing reads a project's secret
by its stored key. The run's record says, for each secret a step read, its name and where it came
from, never the value.

Every test here writes credentials, so the home is redirected and the redirect asserted before
anything is written.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.config import credentials as cred
from personalclaw.config import loader

NAME = "GARDEN_TOKEN"
PROJECT = "p-0a1b2c3d"
OTHER = "p-9f8e7d6c"
#: A project id with no dash, so its stored key is also a well-formed name: refusing it then
#: measures the namespace rule, not the name's shape.
PLAIN_PROJECT = "garden_beds"

#: Long, unique, sharing no substring with a key name, so a match can only be a VALUE.
GLOBAL_VALUE = "gv-31c7e0d2-GLOBAL-GARDEN-VALUE"
PROJECT_VALUE = "pv-5b9a44f1-PROJECT-GARDEN-VALUE"
ALL_VALUES = (GLOBAL_VALUE, PROJECT_VALUE)


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """An isolated home. These tests write secrets, so never the real one."""
    cfg = tmp_path / "home"
    cfg.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(cfg))
    monkeypatch.setattr(loader, "config_dir", lambda: cfg)
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: cfg)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: cfg)
    monkeypatch.delenv(cred.CREDENTIAL_BACKEND_ENV, raising=False)
    for key in (
        NAME,
        _project_key(PROJECT),
        _project_key(OTHER),
        _project_key(PLAIN_PROJECT),
        "PCSECRET_EXAMPLE",
    ):
        # Registered first, so monkeypatch removes whatever a save mirrors in.
        monkeypatch.setenv(key, "x")
        monkeypatch.delenv(key)
    for key in ("__wf_depth", "__wf_run_id", "__wf_project_id", "__wf_node_id"):
        monkeypatch.delenv(key, raising=False)
    assert loader.env_path() == cfg / ".env", "the .env redirect must hold"
    return cfg


def _project_key(project_id: str, name: str = NAME) -> str:
    from personalclaw.secrets_vault import project_secret_key

    return project_secret_key(project_id, name)


async def _post_secret(name: str, value: str, project_id: str = "") -> tuple[int, dict]:
    """What Settings → Secrets does: ``POST /api/secrets``."""
    from personalclaw.dashboard.handlers.secrets import register_secrets_routes

    app = web.Application()
    register_secrets_routes(app)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/api/secrets", json={"name": name, "value": value, "project_id": project_id}
        )
        return resp.status, await resp.json()


async def _store(*, global_value: str = GLOBAL_VALUE, project_value: str = PROJECT_VALUE) -> None:
    """A global secret and this project's secret of the same name, saved as the page saves them,
    then out of this process's environment, as they are after a restart."""
    if global_value:
        status, body = await _post_secret(NAME, global_value)
        assert status == 200, body
    if project_value:
        status, body = await _post_secret(NAME, project_value, PROJECT)
        assert status == 200, body
    os.environ.pop(NAME, None)
    os.environ.pop(_project_key(PROJECT), None)


class _Result:
    """An ``ActionResult`` as a provider returns it."""

    success = True
    stdout = '{"ok": true}'
    outcome = ""
    error = ""
    exit_code = 0
    stderr = ""
    agent_error = None
    failure_class = ""
    retry_after = 0.0


class _Recorder:
    """An action provider that keeps the config it is handed."""

    def __init__(self, *, model_turn: bool = False) -> None:
        self.sent: list[dict] = []
        self.hands_config_to_a_model = model_turn

    async def execute(self, cfg, ctx, timeout=30):
        self.sent.append(json.loads(json.dumps(cfg, default=str)))
        return _Result()


class _Info:
    """What ``SubagentManager.spawn`` answers: an agent that ends at its first lookup."""

    def __init__(self, agent_id: str) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = "done"
        self.reaped = False
        self.agent = ""


class _Subagents:
    """The agent service, recording what each agent is started with."""

    def __init__(self) -> None:
        self.spawned: list[dict] = []
        self.infos: dict[str, _Info] = {}

    def spawn(self, **kw: Any) -> _Info:
        self.spawned.append(kw)
        info = _Info(f"sub{len(self.infos) + 1}")
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None:
            info.done = True
        return info


_STAGE = {"kind": "stage", "id": "work", "config": {"prompt": "use {{secret:GARDEN_TOKEN}}"}}
#: An app's action whose action is a model turn: what reads its config is the app's, not the run's.
_MODEL_TURN = {
    "kind": "action",
    "id": "ask",
    "config": {"provider": "an-apps-model", "with": {"task": "sync with {{secret:GARDEN_TOKEN}}"}},
}


async def _run_stage(
    home: Path, *, project_id: str, model_turn: _Recorder | None = None
) -> tuple[Any, Any, _Subagents]:
    """Run a stage — and, with *model_turn*, an action whose provider is a model turn after it —
    to its end as a run of *project_id*, the stage's agent faked."""
    from personalclaw.workflows import store
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    children = [_STAGE, _MODEL_TURN] if model_turn is not None else [_STAGE]
    spec = {"name": "garden-stage", "root": {"kind": "sequence", "id": "s", "children": children}}
    subagents = _Subagents()
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], project_id=project_id))
    store.write_spec(run.id, spec)
    services = EngineServices(subagents=subagents, cwd=str(home))
    if model_turn is not None:
        services = EngineServices(
            subagents=subagents, cwd=str(home), get_provider=lambda _name: model_turn
        )
    controller = RunController(run, spec, services=services)
    status = await controller.run_to_completion(timeout=20)
    return status, controller, subagents


def _spec(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "garden-sync",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [{"kind": "action", "id": "send", "config": config}],
        },
    }


_USES_IT = {"provider": "notify", "with": {"token": "Bearer {{secret:GARDEN_TOKEN}}"}}


async def _run(
    spec: dict[str, Any], *, project_id: str, provider: _Recorder | None = None
) -> tuple[Any, Any, _Recorder]:
    """Run *spec* to its end as a run of *project_id* ("" for none)."""
    from personalclaw.workflows import store
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    recorder = provider or _Recorder()
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], project_id=project_id))
    store.write_spec(run.id, spec)
    controller = RunController(
        run, spec, services=EngineServices(get_provider=lambda _name: recorder)
    )
    status = await controller.run_to_completion(timeout=20)
    return status, controller, recorder


def _in_env(key: str) -> bool:
    """Whether *key* is in this process's environment. A bool, so a failing assertion prints no
    part of the environment."""
    return key in os.environ


def _secret_rows(run_id: str) -> list[dict]:
    """The run's own record of the secrets its steps used."""
    from personalclaw.workflows import journal

    return [e for e in journal.ledger(run_id) if e.get("kind") == "secret_read"]


def _failure_of(controller: Any) -> str:
    inst = controller.instances["root.children[0]"]
    failure = inst.failure
    return f"{failure.cause_plain} {failure.remediation}" if failure else ""


# ── a workflow step ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("project_id", "value", "scope"),
    [
        (PROJECT, PROJECT_VALUE, "project"),
        (OTHER, GLOBAL_VALUE, "global"),
        ("", GLOBAL_VALUE, "global"),
    ],
    ids=["the-projects-run", "another-projects-run", "a-run-with-no-project"],
)
async def test_a_project_run_reads_its_projects_secret_and_every_other_run_the_global_one(
    home, project_id, value, scope
):
    from personalclaw.workflows.models import RunStatus

    await _store()

    status, controller, recorder = await _run(_spec(_USES_IT), project_id=project_id)

    assert status == RunStatus.COMPLETE, _failure_of(controller)
    assert recorder.sent and recorder.sent[0]["token"] == f"Bearer {value}", recorder.sent
    rows = _secret_rows(controller.run.id)
    assert [(r.get("name"), r.get("scope")) for r in rows] == [(NAME, scope)], rows
    assert rows[0].get("instance_path") == "root.children[0]", rows


@pytest.mark.asyncio
async def test_with_no_global_secret_only_the_projects_own_run_has_one(home):
    from personalclaw.workflows.models import RunStatus

    await _store(global_value="")

    status, _ctl, recorder = await _run(_spec(_USES_IT), project_id=PROJECT)
    assert status == RunStatus.COMPLETE
    assert recorder.sent[0]["token"] == f"Bearer {PROJECT_VALUE}"

    for elsewhere in (OTHER, ""):
        status, controller, recorder = await _run(_spec(_USES_IT), project_id=elsewhere)
        assert status == RunStatus.FAILED, elsewhere
        assert recorder.sent == [], "a step whose secret did not resolve must not run"
        assert NAME in _failure_of(controller), _failure_of(controller)
        assert PROJECT_VALUE not in _failure_of(controller)


@pytest.mark.asyncio
async def test_the_value_is_in_no_record_and_no_log(home, caplog):
    from personalclaw.workflows import store

    caplog.set_level(logging.DEBUG)
    await _store()

    _status, controller, recorder = await _run(_spec(_USES_IT), project_id=PROJECT)

    assert recorder.sent[0]["token"] == f"Bearer {PROJECT_VALUE}", "vacuity: the step got it"
    # The record says which secret and where it came from …
    rows = _secret_rows(controller.run.id)
    assert [(r["name"], r["scope"]) for r in rows] == [(NAME, "project")], rows
    assert "this project's secrets" in rows[0].get("rationale", ""), rows
    # … and nothing the run wrote, nor any log line, holds a value.
    run_dir = store.run_dir(controller.run.id)
    files = [p for p in run_dir.rglob("*") if p.is_file()]
    assert files, "vacuity: the run wrote its record"
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for value in ALL_VALUES:
            assert value not in text, f"{value} reached {path.relative_to(run_dir)}"
    for value in ALL_VALUES:
        assert value not in caplog.text, "a secret's value reached the log"
    assert NAME in caplog.text, "the log names which secret a step read"


@pytest.mark.asyncio
@pytest.mark.parametrize("project_id", [PROJECT, ""], ids=["the-projects-run", "no-project"])
async def test_a_reference_by_the_projects_stored_key_is_refused_in_every_run(home, project_id):
    from personalclaw.workflows.models import RunStatus

    await _store()
    spelled = {"provider": "notify", "with": {"token": "{{secret:" + _project_key(PROJECT) + "}}"}}

    status, controller, recorder = await _run(_spec(spelled), project_id=project_id)

    assert status == RunStatus.FAILED
    assert recorder.sent == [], "the value must not reach the step"
    said = _failure_of(controller)
    assert "project" in said and "{{secret:GARDEN_TOKEN}}" in said, said
    assert PROJECT_VALUE not in said


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "project_id", [PROJECT, OTHER, ""], ids=["a-project", "another-project", "no-project"]
)
async def test_a_global_only_secret_still_resolves_in_every_run(home, project_id):
    """The positive control: a secret saved for every project reaches every run."""
    from personalclaw.workflows.models import RunStatus

    await _store(project_value="")

    status, _ctl, recorder = await _run(_spec(_USES_IT), project_id=project_id)

    assert status == RunStatus.COMPLETE
    assert recorder.sent[0]["token"] == f"Bearer {GLOBAL_VALUE}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("project_id", "scope", "words"),
    [(PROJECT, "project", "this project's secrets"), ("", "global", "the global secrets")],
    ids=["the-projects-run", "no-project"],
)
async def test_a_stage_records_where_its_agents_tools_read_a_reference_it_hands_on(
    home, project_id, scope, words
):
    """A stage's prompt keeps ``{{secret:NAME}}`` as the name, and PersonalClaw's bash tool fills
    it in for the stage's agent with the run's project. Its record says so, with the scope that
    tool reads in this run.

    An app's action whose config is a model's text keeps the reference as the name too, but the
    app decides what reads it — a model with no tools fills nothing — so the record claims nothing
    for that step."""
    from personalclaw.workflows.models import RunStatus

    await _store()
    model_turn = _Recorder(model_turn=True)

    status, controller, subagents = await _run_stage(
        home, project_id=project_id, model_turn=model_turn
    )

    assert status == RunStatus.COMPLETE, status
    assert "{{secret:GARDEN_TOKEN}}" in subagents.spawned[0]["task"], "kept as the name"
    assert GLOBAL_VALUE not in subagents.spawned[0]["task"]
    assert PROJECT_VALUE not in subagents.spawned[0]["task"]
    assert model_turn.sent[0]["task"] == "sync with {{secret:GARDEN_TOKEN}}", "kept as the name"
    rows = _secret_rows(controller.run.id)
    assert [(r["instance_path"], r["name"], r["scope"], r.get("handed_on")) for r in rows] == [
        ("root.children[0]", NAME, scope, True)
    ]
    assert words in rows[0]["rationale"], rows


#: The two steps that start an agent, each with the task that names the secret.
_AGENT_STEPS = {
    "invoke-agent": {"task_template": "water the beds with {{secret:GARDEN_TOKEN}}"},
    "run-prompt": {"message": "water the beds with {{secret:GARDEN_TOKEN}}"},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", sorted(_AGENT_STEPS))
@pytest.mark.parametrize(
    ("project_id", "scope", "words"),
    [(PROJECT, "project", "this project's secrets"), ("", "global", "the global secrets")],
    ids=["the-projects-run", "a-run-with-no-project"],
)
async def test_an_agent_a_step_starts_works_for_the_runs_project(
    home, monkeypatch, provider_name, project_id, scope, words
):
    """An Invoke Agent or Run Prompt step starts its agent for the run's project, so PersonalClaw's
    bash tool there reads the project's secrets, and the run's record says so. The project is the
    run's own (``ActionContext.project_id``): a step's payload naming another project changes
    nothing."""
    import importlib
    from types import SimpleNamespace

    from personalclaw.workflows.models import RunStatus

    module = importlib.import_module(
        "personalclaw.action_providers." + provider_name.replace("-", "_") + "_provider"
    )
    subagents = _Subagents()
    monkeypatch.setattr(module, "get_action_services", lambda: SimpleNamespace(subagents=subagents))
    await _store()
    step = {
        "provider": provider_name,
        "with": _AGENT_STEPS[provider_name],
        "payload": {"project_id": OTHER},
    }

    status, controller, _ = await _run(
        _spec(step), project_id=project_id, provider=module.create_provider()
    )

    assert status == RunStatus.COMPLETE, _failure_of(controller)
    assert [kw.get("project_id") for kw in subagents.spawned] == [project_id]
    task = subagents.spawned[0]["task"]
    assert "{{secret:GARDEN_TOKEN}}" in task, "kept as the name"
    assert not any(value in task for value in ALL_VALUES), "the value never reaches the agent"
    rows = _secret_rows(controller.run.id)
    assert [(r["instance_path"], r["name"], r["scope"], r.get("handed_on")) for r in rows] == [
        ("root.children[0]", NAME, scope, True)
    ], rows
    assert words in rows[0]["rationale"], rows


def test_an_agents_session_is_its_projects():
    """The agent's session is built for its project, which is what its tools read."""
    from personalclaw.subagent import SubagentInfo
    from personalclaw.subagent_session import session_kwargs

    _model, in_project = session_kwargs(
        SubagentInfo(id="a1", task="t", project_id=PROJECT), unattended=False
    )
    _model, no_project = session_kwargs(SubagentInfo(id="a2", task="t"), unattended=False)

    assert in_project.get("project_id") == PROJECT
    assert "project_id" not in no_project


@pytest.mark.asyncio
async def test_run_start_checks_the_runs_own_project(home, monkeypatch):
    """Run start's preflight asks the run's project: a secret only the project holds lets the
    project's run start, and refuses every other run before it costs anything."""
    from personalclaw.workflows import defs as defs_mod
    from personalclaw.workflows import service

    await _store(global_value="")
    spec = _spec(_USES_IT)

    class _Defs(defs_mod.WorkflowDefProvider):
        @property
        def name(self) -> str:
            return "project-secret-defs"

        @property
        def readonly(self) -> bool:
            return True

        async def list_defs(self, *, limit: int = 200, offset: int = 0):
            return [spec], 1

        async def get_def(self, name: str):
            return spec if name == spec["name"] else None

    monkeypatch.setattr(defs_mod, "_providers", {})
    defs_mod.register_provider(_Defs())

    started = await service.start_run(name=spec["name"], project_id=PROJECT, supervisor=None)
    # Past preflight: the run exists, and only the missing supervisor stops it here.
    assert started.get("code") == "WF_NO_SUPERVISOR", started

    for elsewhere in (OTHER, ""):
        refused = await service.start_run(name=spec["name"], project_id=elsewhere, supervisor=None)
        assert refused.get("code") == "WF_RUN_PREFLIGHT_FAILED", refused
        assert NAME in json.dumps(refused), refused


@pytest.mark.asyncio
async def test_a_stage_of_a_project_run_is_spawned_with_the_runs_project(home):
    """A stage's agent fills a reference with its run's project, which its spawn carries in the
    stage's lineage (``__wf_project_id``), read from the run — not from the step's config."""
    from personalclaw.workflows.models import RunStatus

    status, controller, subagents = await _run_stage(home, project_id=PROJECT)

    assert status == RunStatus.COMPLETE, status
    assert subagents.spawned, "vacuity: the stage spawned its agent"
    env = subagents.spawned[0].get("extra_env") or {}
    lineage = {k: env.get(k) for k in ("__wf_project_id", "__wf_run_id")}
    assert lineage == {"__wf_project_id": PROJECT, "__wf_run_id": controller.run.id}, lineage


# ── the agent's shell fill ───────────────────────────────────────────────────────


def _shell_fill(tmp_path: Path, command: str) -> Any:
    """What the ``bash`` tool would run for *command*, or its refusal — nothing is run."""
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    return NativeBuiltinToolProvider(cwd=tmp_path)._bash_command({"command": command})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("project_id", "value"),
    [(PROJECT, PROJECT_VALUE), (OTHER, GLOBAL_VALUE), ("", GLOBAL_VALUE)],
    ids=["the-projects-turn", "another-projects-turn", "no-project"],
)
async def test_a_shell_fill_reads_the_project_of_its_turn(home, tmp_path, project_id, value):
    """A chat in a project and a loop's worker bind their project for each tool call."""
    from personalclaw.agents.native.builtin_tools import bind_tool_context, reset_tool_context

    await _store()
    tokens = bind_tool_context(cwd=tmp_path, project_id=project_id)
    try:
        filled = _shell_fill(tmp_path, "printf %s {{secret:GARDEN_TOKEN}}")
    finally:
        reset_tool_context(tokens)

    assert isinstance(filled, tuple), filled
    command, handed = filled
    assert command == f"printf %s {value}"
    assert value in handed, "a value handed to the command is masked out of what it prints"


@pytest.mark.asyncio
async def test_a_shell_fill_in_a_workflow_step_reads_its_runs_project(home, tmp_path):
    """A stage's agent is no project's session: its run's project comes from its lineage."""
    from personalclaw import mcp_shared

    await _store()
    token = mcp_shared.bind_leaf_lineage({"__wf_run_id": "r1", "__wf_project_id": PROJECT})
    try:
        filled = _shell_fill(tmp_path, "printf %s {{secret:GARDEN_TOKEN}}")
    finally:
        mcp_shared.reset_leaf_lineage(token)

    assert isinstance(filled, tuple), filled
    assert filled[0] == f"printf %s {PROJECT_VALUE}"


@pytest.mark.asyncio
async def test_a_shell_fill_refuses_a_projects_stored_key(home, tmp_path):
    from personalclaw.agents.native.builtin_tools import bind_tool_context, reset_tool_context

    await _store()
    tokens = bind_tool_context(cwd=tmp_path, project_id=PROJECT)
    try:
        refused = _shell_fill(tmp_path, "printf %s {{secret:" + _project_key(PROJECT) + "}}")
    finally:
        reset_tool_context(tokens)

    assert not isinstance(refused, tuple), "the stored key must not fill"
    assert not refused.success
    assert "Nothing was run" in refused.error and "{{secret:GARDEN_TOKEN}}" in refused.error
    assert PROJECT_VALUE not in refused.error


# ── automations, settings and apps: no project ───────────────────────────────────


@pytest.mark.asyncio
async def test_an_automation_reads_the_global_secret_and_refuses_a_projects_stored_key(home):
    from personalclaw.triggers import cannot_run
    from personalclaw.triggers import secrets as trigger_secrets

    await _store()

    resolved = trigger_secrets.resolve_for(object(), {"command": "echo {{secret:GARDEN_TOKEN}}"})
    assert resolved == {"command": f"echo {GLOBAL_VALUE}"}

    with pytest.raises(trigger_secrets.UnresolvedSecret) as refused:
        trigger_secrets.resolve_for(
            object(), {"command": "echo {{secret:" + _project_key(PROJECT) + "}}"}
        )
    assert refused.value.refused is not None
    said = cannot_run.missing_secret(refused.value)
    assert "project" in said and "{{secret:GARDEN_TOKEN}}" in said, said
    assert PROJECT_VALUE not in said


@pytest.mark.asyncio
async def test_a_settings_reference_to_a_projects_stored_key_is_refused(home):
    from personalclaw.config import secret_refs

    await _store()
    owner = secret_refs.provider_owner("garden-provider")

    with pytest.raises(secret_refs.ForeignSecretReference) as refused:
        secret_refs.resolve({"api_key": "{{secret:" + _project_key(PROJECT) + "}}"}, owner=owner)
    assert "project" in str(refused.value), str(refused.value)
    # A settings record is in no project, so the name reads the global secret.
    assert secret_refs.resolve({"api_key": "{{secret:GARDEN_TOKEN}}"}, owner=owner) == {
        "api_key": GLOBAL_VALUE
    }


@pytest.mark.asyncio
async def test_an_app_reading_a_projects_stored_key_is_refused(home):
    """What ``personalclaw.sdk.credentials.CredentialStore`` hands an app: global secrets only."""
    from personalclaw.llm.credentials import CredentialStore

    await _store()
    store = CredentialStore(home)

    with pytest.raises(KeyError):
        store.resolve(_project_key(PROJECT))
    assert store.resolve(NAME).secret == GLOBAL_VALUE


# ── the store ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_project_secret_never_enters_the_gateways_environment(home):
    """The gateway's environment is what every child it starts inherits: an MCP server, a cron
    script, an agent CLI of any project. A project's secret is read only by its own runs."""
    status, _ = await _post_secret(NAME, PROJECT_VALUE, PROJECT)
    assert status == 200
    status, _ = await _post_secret(NAME, GLOBAL_VALUE)
    assert status == 200

    assert _in_env(_project_key(PROJECT)) is False
    assert _in_env(NAME) is True, "vacuity: a global secret is mirrored"

    os.environ.pop(NAME, None)
    loader.AppConfig().load_credentials()  # what the gateway does at boot
    assert _in_env(_project_key(PROJECT)) is False
    assert _in_env(NAME) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    ["PCSECRET_EXAMPLE", _project_key(PLAIN_PROJECT)],
    ids=["a-settings-key", "a-projects-key"],
)
async def test_a_name_in_a_store_namespace_is_refused_by_the_secrets_page(home, name):
    from personalclaw.dashboard.handlers.secrets import register_secrets_routes
    from personalclaw.secrets_vault import valid_key_name

    assert valid_key_name(name), "vacuity: the name's shape alone would pass"
    cred.save_credential(name, "kept-as-it-was")
    app = web.Application()
    register_secrets_routes(app)
    async with TestClient(TestServer(app)) as client:
        put = await client.post("/api/secrets", json={"name": name, "value": "overwritten"})
        put_code = (await put.json()).get("error", {}).get("code")
        assert (put.status, put_code) == (400, "secret_name_reserved")
        gone = await client.delete(f"/api/secrets?name={name}")
        gone_code = (await gone.json()).get("error", {}).get("code")
        assert (gone.status, gone_code) == (400, "secret_name_reserved")

    assert cred.get_credential(name) == "kept-as-it-was"


def test_setup_credential_and_a_pack_refuse_a_projects_namespace(home, capsys):
    from personalclaw.cli_setup import _store_named_credential
    from personalclaw.packs.connectors import ConnectorResolutionError, _save_credentials
    from personalclaw.secrets_vault import valid_key_name

    key = _project_key(PLAIN_PROJECT)
    assert valid_key_name(key), "vacuity: the name's shape alone would pass"
    assert _store_named_credential(f"{key}=from-the-cli") is False
    with pytest.raises(ConnectorResolutionError):
        _save_credentials([key], {key: "from-a-pack"})
    assert cred.get_credential(key) == ""


# ── the Secrets page ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_project_row_lists_the_workflows_its_runs_read_it_for(home, monkeypatch):
    """A workflow that refers to ``{{secret:NAME}}`` reads the project's secret when it runs in
    that project, so the project's row names it. An automation runs in no project, so it is listed
    only on the global row."""
    import personalclaw.secrets_vault as vault

    await _store()

    async def _workflows():
        return [("garden-sync", "Garden sync", [NAME])]

    monkeypatch.setattr(vault, "_workflow_references", _workflows)
    monkeypatch.setattr(vault, "_trigger_references", lambda: [("t-1", "Morning water", [NAME])])

    rows = vault.list_presence(consumers=await vault.consumers_for())

    project_row = next(r for r in rows if r.scope == "project" and r.name == NAME)
    global_row = next(r for r in rows if r.scope == "global" and r.name == NAME)
    assert [(c.kind, c.label) for c in project_row.consumers] == [("workflow", "Garden sync")]
    assert sorted(c.label for c in global_row.consumers) == ["Garden sync", "Morning water"]


# ── one resolver ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_named_secret_reader_asks_the_one_resolver(home, tmp_path, monkeypatch):
    """A workflow step, run start, the agent's shell fill, an automation and an app all read a
    named secret through ``llm.credentials.resolve_secret``, each with its own project."""
    from personalclaw.agents.native.builtin_tools import bind_tool_context, reset_tool_context
    from personalclaw.llm import credentials as llm_credentials
    from personalclaw.triggers import secrets as trigger_secrets
    from personalclaw.workflows import preflight

    await _store()
    asked: list[tuple[str, str]] = []
    real = llm_credentials.resolve_secret

    def _spy(name: str, **kw: Any):
        asked.append((name, str(kw.get("project_id", "") or "")))
        return real(name, **kw)

    monkeypatch.setattr(llm_credentials, "resolve_secret", _spy)

    await _run(_spec(_USES_IT), project_id=PROJECT)
    assert asked[-1] == (NAME, PROJECT), asked

    assert preflight.preflight(_spec(_USES_IT), project_id=PROJECT).ok
    assert asked[-1] == (NAME, PROJECT), asked

    tokens = bind_tool_context(cwd=tmp_path, project_id=PROJECT)
    try:
        _shell_fill(tmp_path, "printf %s {{secret:GARDEN_TOKEN}}")
    finally:
        reset_tool_context(tokens)
    assert asked[-1] == (NAME, PROJECT), asked

    trigger_secrets.resolve_for(object(), {"command": "{{secret:GARDEN_TOKEN}}"})
    assert asked[-1] == (NAME, ""), asked

    llm_credentials.CredentialStore(home).resolve(NAME)
    assert asked[-1] == (NAME, ""), asked
