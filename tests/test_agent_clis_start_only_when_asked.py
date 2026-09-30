"""PersonalClaw never starts another agent's CLI unless the user asks for that specific action.

The rail for that promise, driven with a STUB standing in for an agent CLI: a real executable on
PATH that appends one line to a file each time it runs, so "was it started?" is read off disk,
not taken from a mock. Every path the user does not take to start it — a gateway start, each read
of the agent runtimes and runners, ``personalclaw doctor`` — must leave that file empty. The two
paths that ARE the user asking — a runtime's Test and a runner's Check — must write to it, and
that is also the positive control: it proves the stub can see a start at all.

What used to start it with nobody asking: the first read of ``/api/agent-providers`` (a
background probe), every gateway start (a readiness warm and a live warmed connection per
runtime, respawned by a health loop and refreshed every ten minutes, and re-warmed behind every
chat that took one), the chat picker's agent list (a probe and a throwaway session), and
``personalclaw doctor``. And Settings → Agent defaults → Re-check runners ran ``--version`` for
every catalogued CLI on PATH, whether or not an app was installed for it.

No real agent CLI runs here: PATH holds the stubs and the system directories only, and the
registry holds only the stubs' entries.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
from pathlib import Path

import pytest
from aiohttp.test_utils import make_mocked_request

import personalclaw
from personalclaw.llm.acp_agent import ACP_AGENT_CAPABILITY, AcpAgentProvider
from personalclaw.llm.registry import ProviderEntry, get_default_registry

#: The process-wide provider registry and the runtime class every other module imported, as this
#: file found them: nothing here may swap the one or re-import the other.
_PROCESS_REGISTRY = get_default_registry()
_RUNTIME_CLASS = AcpAgentProvider


@pytest.fixture(autouse=True)
def _only_the_stubs_entries():
    """The process-wide registry holds only the entries each test registers, and gets back after it
    whatever it held before.

    Only ours because a doctor run with ``--start-agent-clis`` starts EVERY registered agent CLI:
    an entry left there by anything else could point anywhere. The registry itself stays in place,
    and ``acp_agent`` is never imported again. The gateway boot below loads the bundled apps, and
    each registers its provider type into the registry of the moment when its module is first
    imported, then stays cached: a registry swapped in for the test would leave with their types
    while their modules stayed, and every later test on the worker would find no loaded app
    providing ``ollama``. A re-import of ``acp_agent`` builds a second ``AcpAgentProvider``, which
    every module that imported the first one no longer recognises.
    """
    registry = get_default_registry()
    others = registry.list_entries()
    for entry in others:
        registry.unregister_entry(entry.name)
    try:
        yield
    finally:
        for entry in registry.list_entries():
            registry.unregister_entry(entry.name)
        for entry in others:
            registry.register_entry(entry)


class _Stubs:
    def __init__(self, root: Path) -> None:
        self.bin = root / "bin"
        self.bin.mkdir()
        self.log = root / "started.log"

    def make(self, name: str) -> Path:
        """An executable *name* that records ``<name> <args>`` each time it runs, then exits."""
        path = self.bin / name
        path.write_text(f"#!/bin/sh\necho \"{name} $*\" >> '{self.log}'\nexit 0\n")
        path.chmod(0o755)
        return path

    def starts(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []


@pytest.fixture
def stubs(tmp_path, monkeypatch):
    s = _Stubs(tmp_path)
    # The stubs and the system's own directories only: nothing here can reach a real agent CLI.
    monkeypatch.setenv("PATH", os.pathsep.join([str(s.bin), "/usr/bin", "/bin"]))
    return s


def _register(name: str, command: list[str]) -> ProviderEntry:
    entry = ProviderEntry(
        name=name,
        type="acp_agent",
        model="",
        # `sandbox_mode: off` so the stub may write its log wherever the test put it; the probe
        # budget is short because the stub speaks no ACP and exits at once.
        options={
            "command": command,
            "dialect": "default",
            "sandbox_mode": "off",
            "probe_timeout_secs": 10,
            "extension": f"{name.split(':', 1)[-1]}-app",
        },
        credential=None,
        declared_capabilities=ACP_AGENT_CAPABILITY.capabilities,
    )
    get_default_registry().register_entry(entry)
    return entry


def _runner(runner_id: str, bin_name: str, *, allowed: bool = True) -> None:
    """A runner-catalog row of the owner's own (``<home>/runners/<id>.json``), which they allowed
    here unless *allowed* is False — as one a sync brought, or an agent wrote, waits."""
    from personalclaw.agents import runner_grants, runners

    folder = runners.user_catalog_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{runner_id}.json").write_text(
        json.dumps(
            {
                "id": runner_id,
                "display_name": runner_id.replace("-", " ").title(),
                "runtime_id": f"acp:{runner_id}",
                "bin_names": [bin_name],
                "version_args": ["--version"],
            }
        ),
        encoding="utf-8",
    )
    if allowed:
        runner_grants.give(runners.catalog()[runner_id])


async def _get(handler, path: str, **match) -> tuple[int, dict]:
    resp = await handler(make_mocked_request("GET", path, match_info=match))
    return resp.status, json.loads(resp.body.decode())


async def _post(handler, path: str, body: dict | None = None, **match) -> tuple[int, dict]:
    request = make_mocked_request("POST", path, match_info=match)
    request["user"] = "owner"

    async def _json() -> dict:
        return body or {}

    request.json = _json  # type: ignore[method-assign]
    resp = await handler(request)
    return resp.status, json.loads(resp.body.decode())


@pytest.mark.asyncio
async def test_no_read_of_the_agent_runtimes_or_runners_starts_a_cli(stubs):
    from personalclaw.dashboard.handlers.providers import (
        api_agent_provider_agents,
        api_agent_providers_list,
        api_agent_runners_list,
    )

    _register("acp:stub-agent", [str(stubs.make("stub-agent")), "acp"])
    _runner("stub-agent", "stub-agent")

    for path in ("/api/agent-providers", "/api/agent-providers?refresh=1"):
        status, listed = await _get(api_agent_providers_list, path)
        assert status == 200
    row = next(r for r in listed["agent_providers"] if r["provider_id"] == "acp:stub-agent")
    status, agents = await _get(
        api_agent_provider_agents, "/api/agent-providers/acp:stub-agent/agents", id="acp:stub-agent"
    )
    assert status == 200 and agents["agents"] == []
    status, catalog = await _get(api_agent_runners_list, "/api/agent-runners?probe=1")
    assert status == 200

    assert stubs.starts() == [], "a read started an agent CLI"
    # …and each read says so rather than implying an answer it never measured.
    assert row["state"] == "untested" and row["tested_at"] is None
    assert "press Test" in row["detail"]
    stub_row = next(r for r in catalog["runners"] if r["id"] == "stub-agent")
    assert stub_row["health"] is None and stub_row["set_up"] is True


@pytest.mark.asyncio
async def test_a_gateway_start_starts_no_agent_cli(stubs, monkeypatch):
    """The REAL gateway boots with an agent runtime set up: every startup hook runs, and the
    background work they start gets time to run too. It used to probe each runtime and keep a
    live connection to it, respawned when it died and refreshed every ten minutes."""
    from unittest.mock import MagicMock

    from personalclaw.acp.connection_pool import get_acp_pool, set_acp_pool
    from personalclaw.dashboard.server import start_dashboard

    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", "none")  # loopback only
    # A runtime of its own, so nothing another test left for "acp:stub-agent" (an answer cached
    # by runtime) can stand in for a start this boot would make.
    _register("acp:boot-stub", [str(stubs.make("boot-stub")), "acp"])
    runner, _state = await start_dashboard(sessions=MagicMock(count=0), port=0)
    try:
        # The boot this measures ran its startup hooks: the one that installs the pool is one.
        assert get_acp_pool() is not None
        await asyncio.sleep(1.5)
        assert stubs.starts() == [], "a gateway start started an agent CLI"
    finally:
        await runner.cleanup()
        pool = get_acp_pool()
        if pool is not None:
            await pool.shutdown()
        set_acp_pool(None)


def test_doctor_reports_without_starting_and_starts_only_with_its_flag(stubs, capsys):
    from personalclaw.cli_doctor import _doctor_providers, _GatewayReading

    _register("acp:stub-agent", [str(stubs.make("stub-agent")), "acp"])
    no_gateway = _GatewayReading(why="no gateway of this home is running")

    issues = _doctor_providers(no_gateway)
    out = capsys.readouterr().out
    assert stubs.starts() == [], "doctor started an agent CLI nobody asked it to"
    assert "installed, not started" in out and "--start-agent-clis" in out
    assert issues == [], "a CLI nobody started is not a failed check"

    _doctor_providers(no_gateway, start_agent_clis=True)
    assert stubs.starts() == ["stub-agent acp"], "the flag is the ask: it starts each CLI once"


@pytest.mark.asyncio
async def test_the_test_starts_the_one_runtime_it_names_and_reads_show_its_answer(stubs):
    """The positive control for every "nothing started" above: the Test is seen starting it."""
    from personalclaw.dashboard.handlers.providers import (
        api_agent_provider_test,
        api_agent_providers_list,
    )

    _register("acp:stub-agent", [str(stubs.make("stub-agent")), "acp"])
    _register("acp:other-agent", [str(stubs.make("other-agent")), "acp"])

    status, body = await _post(
        api_agent_provider_test, "/api/agent-providers/acp:stub-agent/test", id="acp:stub-agent"
    )

    assert status == 200
    assert stubs.starts() == ["stub-agent acp"], "the Test did not start exactly the one it named"
    tested = body["agent_provider"]
    # The stub speaks no ACP, so the Test's honest answer is a failure — recorded, with its time.
    assert tested["state"] in ("error", "timeout") and tested["ready"] is False
    assert tested["tested_at"]
    _status, listed = await _get(api_agent_providers_list, "/api/agent-providers")
    rows = {r["provider_id"]: r for r in listed["agent_providers"]}
    assert rows["acp:stub-agent"]["state"] == tested["state"]
    assert rows["acp:other-agent"]["state"] == "untested"
    assert stubs.starts() == ["stub-agent acp"], "reading the answer started a CLI again"


@pytest.mark.asyncio
async def test_a_runner_check_runs_its_version_for_that_runner_and_never_a_cli_nothing_set_up(
    stubs,
):
    from personalclaw.dashboard.handlers.providers import api_agent_runner_check

    _register("acp:stub-agent", [str(stubs.make("stub-agent")), "acp"])
    _runner("stub-agent", "stub-agent")
    # On PATH, in the catalog, and set up by nothing: a CLI the owner never had an app drive.
    stubs.make("loose-agent")
    _runner("loose-agent", "loose-agent")

    status, body = await _post(
        api_agent_runner_check, "/api/agent-runners/loose-agent/check", id="loose-agent"
    )
    assert status == 409 and body["error"]["code"] == "runner_not_set_up"
    assert stubs.starts() == [], "a runner nothing set up was run"

    status, body = await _post(
        api_agent_runner_check, "/api/agent-runners/stub-agent/check", id="stub-agent"
    )
    assert status == 200
    assert stubs.starts() == ["stub-agent --version"], "the Check ran more than its own --version"
    assert body["runner"]["id"] == "stub-agent" and body["runner"]["health"]["ok"] is True


@pytest.mark.asyncio
async def test_a_runner_definition_that_waits_runs_nothing_until_you_allow_it(stubs):
    """🔴 Red before: a definition written into ``runners/`` by anyone — a device sync, an agent's
    shell — ran its CLI as the file said. It now waits for the owner's Allow, sealed to what it
    runs, and Allow asks first, with what it runs, and names the revision the page read."""
    from personalclaw.agents import runner_grants
    from personalclaw.dashboard.handlers.providers import (
        api_agent_runner_allow,
        api_agent_runner_check,
        api_agent_runners_list,
    )

    _register("acp:stub-agent", [str(stubs.make("stub-agent")), "acp"])
    _runner("stub-agent", "stub-agent", allowed=False)
    _status, listed = await _get(api_agent_runners_list, "/api/agent-runners")
    row = next(r for r in listed["runners"] if r["id"] == "stub-agent")
    assert row["waiting"] is True and row["allow_revision"]

    check = "/api/agent-runners/stub-agent/check"
    status, body = await _post(api_agent_runner_check, check, id="stub-agent")
    assert status == 409 and body["error"]["code"] == "runner_waiting"
    assert stubs.starts() == [], "a runner definition nobody here allowed was run"

    allow = "/api/agent-runners/stub-agent/allow"
    status, body = await _post(
        api_agent_runner_allow, allow, {"revision": row["allow_revision"]}, id="stub-agent"
    )
    assert status == 400 and body["error"]["code"] == "confirmation_required"
    assert "“stub-agent”" in body["error"]["detail"]["consent"]
    assert body["error"]["detail"]["title"] == runner_grants.CONSENT_TITLE
    status, body = await _post(
        api_agent_runner_allow,
        allow,
        {"revision": "not-what-was-read", "confirm": True},
        id="stub-agent",
    )
    assert status == 409 and body["error"]["code"] == "stale_write"
    assert stubs.starts() == []

    status, body = await _post(
        api_agent_runner_allow,
        allow,
        {"revision": row["allow_revision"], "confirm": True},
        id="stub-agent",
    )
    assert status == 200 and body["runner"]["waiting"] is False
    assert stubs.starts() == [], "Allow runs nothing"
    status, body = await _post(api_agent_runner_check, check, id="stub-agent")
    assert status == 200 and stubs.starts() == ["stub-agent --version"]


@pytest.mark.asyncio
async def test_an_edit_to_what_an_allowed_runner_runs_waits_for_you_again(stubs):
    """Another machine's edit, or anyone's, to a definition the owner allowed: a new question."""
    from personalclaw.agents import runners
    from personalclaw.dashboard.handlers.providers import api_agent_runner_check

    _register("acp:stub-agent", [str(stubs.make("stub-agent")), "acp"])
    stubs.make("other-bin")
    _runner("stub-agent", "stub-agent")
    path = runners.user_catalog_dir() / "stub-agent.json"
    definition = json.loads(path.read_text())
    path.write_text(json.dumps({**definition, "bin_names": ["other-bin"]}))

    status, body = await _post(
        api_agent_runner_check, "/api/agent-runners/stub-agent/check", id="stub-agent"
    )
    assert status == 409 and body["error"]["code"] == "runner_waiting"
    assert stubs.starts() == []
    # CONTROL: the edit back to what was allowed runs again without asking.
    path.write_text(json.dumps(definition))
    status, _body = await _post(
        api_agent_runner_check, "/api/agent-runners/stub-agent/check", id="stub-agent"
    )
    assert status == 200 and stubs.starts() == ["stub-agent --version"]


def test_apps_cannot_start_an_agent_cli():
    """Every start, and every yes to one, is the owner's: no app token reaches them, whatever it
    declared."""
    from personalclaw.apps.permissions import owner_only_api_reason

    for method, route, path in (
        ("POST", "/api/agent-providers/{id}/test", "/api/agent-providers/acp:x/test"),
        ("POST", "/api/agent-runners/{id}/check", "/api/agent-runners/x/check"),
        ("POST", "/api/agent-runners/{id}/allow", "/api/agent-runners/x/allow"),
    ):
        assert owner_only_api_reason(path, method=method, route=route), route


def test_a_test_here_runs_on_the_process_wide_registry_and_runtime_class():
    """What the fixture above leaves alone, read from inside a test: the registry every loaded app
    registered its type into, and the one ``AcpAgentProvider`` class the product builds and the
    rest of the suite checks against. Either replaced here outlives the file on its worker."""
    from personalclaw.agents.registry import get_agent_provider_class
    from personalclaw.llm import acp_agent

    assert get_default_registry() is _PROCESS_REGISTRY
    assert acp_agent.AcpAgentProvider is _RUNTIME_CLASS
    assert get_agent_provider_class("acp") is _RUNTIME_CLASS


# ── the census: who may call the checks that start a CLI ─────────────────────────────────────

#: The checks that start another agent's CLI, and the only functions allowed to reach them — each
#: the handling of an action the user took. A new caller (a boot hook, a background refresh, a
#: read route) reds this, which is the point: it would be a start nobody asked for.
_STARTS = {
    "probe_readiness": {"agents/runtime_tests.py::_test"},
    "probe_handshake": {
        "llm/acp_agent.py::AcpAgentProvider.probe_readiness",
        "llm/acp_agent.py::AcpAgentProvider.probe_handshake",
    },
    "run_test": {
        "dashboard/handlers/providers.py::api_agent_provider_test",
        "cli_doctor.py::_report_acp_agent",
    },
    "probe_runner": {"dashboard/handlers/providers.py::api_agent_runner_check"},
}


def _references() -> dict[str, set[str]]:
    """Every place a name in :data:`_STARTS` is referenced (called or handed on), by function."""
    src = Path(personalclaw.__file__).resolve().parent
    found: dict[str, set[str]] = {name: set() for name in _STARTS}

    def walk(node: ast.AST, rel: str, scope: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(child, rel, [*scope, child.name])
                continue
            name = None
            if isinstance(child, ast.Attribute) and child.attr in _STARTS:
                name = child.attr
            elif isinstance(child, ast.Name) and child.id in _STARTS:
                name = child.id
            if name is not None:
                found[name].add(f"{rel}::{'.'.join(scope) or '<module>'}")
            walk(child, rel, scope)

    for path in sorted(src.rglob("*.py")):
        rel = path.relative_to(src).as_posix()
        walk(ast.parse(path.read_text(encoding="utf-8")), rel, [])
    return found


def test_only_what_the_user_pressed_reaches_a_check_that_starts_an_agent_cli():
    found = _references()
    # VACUITY FLOOR: the census must see the sanctioned callers, or it proves nothing.
    for name, allowed in _STARTS.items():
        assert allowed <= found[name], f"the census no longer sees {name} in {sorted(allowed)}"
    extra = {name: sorted(found[name] - allowed) for name, allowed in _STARTS.items()}
    assert not any(extra.values()), (
        "these reach a check that STARTS another agent's CLI, and none is an action the user "
        f"took to start it: {extra}"
    )


def test_a_runtimes_test_record_stays_in_its_folder_whatever_its_id(tmp_path, monkeypatch):
    """The record's file is named from the runtime id, through the one resolver from a record id
    to a path inside its store: an id holding a separator, a parent segment or an absolute path
    still names a file in the agent metadata folder, and so does one longer than a file name
    may be, cut rather than refused so one runtime cannot fail the whole listing."""
    from personalclaw import agent_metadata
    from personalclaw.agents import runtime_tests

    monkeypatch.setattr(agent_metadata, "metadata_dir", lambda: tmp_path)
    for runtime_id in (
        "acp:demo-cli",
        "acp:../../../etc/passwd",
        "/tmp/elsewhere",
        "..",
        "",
        "acp:" + "x" * 400,
    ):
        path = runtime_tests.record_path(runtime_id)
        assert path.parent == tmp_path, (runtime_id, path)
        assert path.name.endswith(".runtime-test.json"), path.name
    assert runtime_tests.record_path("acp:demo-cli").name == "acp-demo-cli.runtime-test.json"
