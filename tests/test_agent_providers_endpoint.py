"""``/api/agent-providers`` — the single source of truth for the unified
"Agent Providers" settings section (native + every ``acp:<cli>`` runtime).

Pins the contract the frontend relies on to render ONE section instead of two:

* the in-process ``native`` runtime is ALWAYS present and always ready (it has
  no model-registry entry — the handler synthesizes its row);
* an ``acp:<cli>`` entry surfaces with ``provider_id == entry.name`` (the
  canonical runtime id) — NOT re-derived from the adapter command basename,
  which would mislabel e.g. ``claude-agent-acp`` → ``acp:claude-agent-acp``;
* the bundle-declared ``extension`` is carried on each row so the UI can join
  readiness onto the matching enable/config extension card;
* a READ starts nothing: a runtime reads ``untested`` until the user's Test, then that
  Test's answer — and the Test starts exactly the runtime it names.
"""

from __future__ import annotations

import asyncio
import json
import stat

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers.providers import (
    api_agent_provider_agents,
    api_agent_provider_test,
    api_agent_providers_list,
)
from personalclaw.llm.acp_agent import ACP_AGENT_CAPABILITY
from personalclaw.llm.registry import (
    ProviderEntry,
    get_default_registry,
    reset_default_registry,
)


@pytest.fixture(autouse=True)
def _restore_registry_singletons():
    """Restore the process-wide registry + acp_agent module after each test.

    ``_fresh_registry`` reloads ``acp_agent`` (new module + new
    ``AcpAgentProvider`` class) and empties the model registry; leaving that in
    place leaks into later modules (stale class identity, missing provider
    types). Snapshot and restore everything we perturb. See test_acp_bundles for
    the same pattern (#25c).
    """
    import sys

    import personalclaw.llm as _llm_pkg
    from personalclaw.agents import registry as _agent_reg
    from personalclaw.llm import registry as _model_reg

    saved_registry = _model_reg._default_registry
    saved_module = sys.modules.get("personalclaw.llm.acp_agent")
    saved_pkg_attr = getattr(_llm_pkg, "acp_agent", None)
    saved_agent_providers = dict(_agent_reg._providers)
    try:
        yield
    finally:
        _model_reg.set_default_registry(saved_registry)
        if saved_module is not None:
            sys.modules["personalclaw.llm.acp_agent"] = saved_module
            _llm_pkg.acp_agent = saved_pkg_attr
        _agent_reg._providers.clear()
        _agent_reg._providers.update(saved_agent_providers)


def _fresh_registry():
    """Reset the default registry and re-register the ``acp_agent`` type
    capability (it registers at acp_agent import time, which reset wipes).
    Teardown restoration is handled by the autouse fixture above."""
    reset_default_registry()
    import importlib

    import personalclaw.llm.acp_agent as _acp_agent

    importlib.reload(_acp_agent)


def _call(query: str = "") -> dict:
    req = make_mocked_request("GET", "/api/agent-providers" + (f"?{query}" if query else ""))
    resp = asyncio.run(api_agent_providers_list(req))
    return json.loads(resp.body.decode())


async def _acall(query: str = "") -> dict:
    req = make_mocked_request("GET", "/api/agent-providers" + (f"?{query}" if query else ""))
    resp = await api_agent_providers_list(req)
    return json.loads(resp.body.decode())


async def _atest(runtime: str) -> tuple[int, dict]:
    req = make_mocked_request(
        "POST", f"/api/agent-providers/{runtime}/test", match_info={"id": runtime}
    )
    resp = await api_agent_provider_test(req)
    return resp.status, json.loads(resp.body.decode())


def _row(data: dict, runtime: str) -> dict:
    return next(r for r in data["agent_providers"] if r["provider_id"] == runtime)


def _installed(tmp_path, name: str) -> str:
    """An executable named *name*: an installed CLI as far as PATH can tell. Never run here."""
    path = tmp_path / name
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


def _register_runtime(name: str, command: list[str], **options) -> None:
    get_default_registry().register_entry(
        ProviderEntry(
            name=name,
            type="acp_agent",
            model="",
            options={"command": command, "dialect": "codex", **options},
            credential=None,
            declared_capabilities=ACP_AGENT_CAPABILITY.capabilities,
        )
    )


@pytest.fixture
def counted_probe(monkeypatch):
    """A fresh registry, and a probe that counts every start (and can stall, or be ready)."""
    from personalclaw.agents.provider import ReadinessStatus
    from personalclaw.agents.registry import get_agent_provider_class

    _fresh_registry()
    calls: dict[str, int] = {}
    stall = {"secs": 0.0}
    answer = {"status": None}

    async def fake_probe(cls, options):
        key = options["command"][0].rsplit("/", 1)[-1]
        calls[key] = calls.get(key, 0) + 1
        await asyncio.sleep(stall["secs"])
        return answer["status"] or ReadinessStatus(
            ready=False, state="needs_login", detail=f"{key}: sign in", login_command=[key, "login"]
        )

    monkeypatch.setattr(get_agent_provider_class("acp"), "probe_readiness", classmethod(fake_probe))
    try:
        yield calls, stall, answer
    finally:
        reset_default_registry()


@pytest.mark.asyncio
async def test_an_installed_runtime_nobody_tested_reads_untested_and_no_read_starts_it(
    counted_probe, tmp_path
):
    """A plain read never starts the CLI — not the first read, not a stale one, not a
    ``?refresh=1`` from an old client. It used to start one in the background on first
    sight, and re-probe inline once an answer aged, so a Providers visit started the CLI."""
    calls, _stall, _answer = counted_probe
    _register_runtime("acp:codex", [_installed(tmp_path, "codex-acp")])

    queries = ("", "", "refresh=1", "refresh=1&runtime=acp:codex")
    rows = [_row(await _acall(q), "acp:codex") for q in queries]

    assert calls == {}, "a read started the runtime's CLI"
    for row in rows:
        assert row["state"] == "untested" and row["ready"] is False
        assert row["tested_at"] is None
        assert "press Test" in row["detail"], "the row must say plainly it has not been tried"


@pytest.mark.asyncio
async def test_a_runtime_whose_cli_is_gone_reads_not_found_and_is_not_started(counted_probe):
    calls, _stall, _answer = counted_probe
    _register_runtime("acp:codex", ["/nonexistent/codex-acp"])
    row = _row(await _acall(), "acp:codex")
    assert row["state"] == "not_found" and calls == {}


@pytest.mark.asyncio
async def test_the_test_starts_the_one_runtime_it_names_and_its_answer_is_what_reads_show(
    counted_probe, tmp_path
):
    calls, _stall, _answer = counted_probe
    _register_runtime("acp:codex", [_installed(tmp_path, "codex-acp")])
    _register_runtime("acp:other", [_installed(tmp_path, "other-acp")])

    status, data = await _atest("acp:codex")

    assert status == 200
    assert calls == {"codex-acp": 1}, f"a Test of one runtime started {sorted(calls)}"
    tested = data["agent_provider"]
    assert tested["provider_id"] == "acp:codex" and tested["state"] == "needs_login"
    assert tested["login_command"] == ["codex-acp", "login"] and tested["tested_at"]
    # Every read after it answers from that Test, and still starts nothing.
    listed = await _acall()
    assert _row(listed, "acp:codex")["state"] == "needs_login"
    assert _row(listed, "acp:codex")["tested_at"] == tested["tested_at"]
    assert _row(listed, "acp:other")["state"] == "untested"
    assert calls == {"codex-acp": 1}


@pytest.mark.asyncio
async def test_two_presses_while_a_test_runs_start_the_cli_once(counted_probe, tmp_path):
    calls, stall, _answer = counted_probe
    _register_runtime("acp:codex", [_installed(tmp_path, "codex-acp")])
    stall["secs"] = 0.3

    (s1, d1), (s2, d2) = await asyncio.gather(_atest("acp:codex"), _atest("acp:codex"))

    assert s1 == s2 == 200
    assert calls == {"codex-acp": 1}, "two presses started the CLI twice"
    assert d1["agent_provider"]["tested_at"] == d2["agent_provider"]["tested_at"]


@pytest.mark.asyncio
async def test_a_test_of_an_absent_cli_starts_nothing_and_records_nothing(counted_probe, tmp_path):
    """Nothing to start is a fact about now: once the CLI is installed its card reads
    "not tried", never the old not-found."""
    calls, _stall, _answer = counted_probe
    missing = tmp_path / "codex-acp"
    _register_runtime("acp:codex", [str(missing)])

    status, data = await _atest("acp:codex")
    assert status == 200 and data["agent_provider"]["state"] == "not_found" and calls == {}

    _installed(tmp_path, "codex-acp")
    assert _row(await _acall(), "acp:codex")["state"] == "untested"


@pytest.mark.asyncio
async def test_a_result_for_another_command_reads_as_not_tried(counted_probe, tmp_path):
    """A Test is about the command it ran: after an update moves the CLI, the old verdict is
    about a different program, so the runtime reads "not tried" again."""
    _calls, _stall, _answer = counted_probe
    _register_runtime("acp:codex", [_installed(tmp_path, "codex-acp")])
    await _atest("acp:codex")
    reset_default_registry()
    _fresh_registry()
    _register_runtime("acp:codex", [_installed(tmp_path, "codex-acp-2")])
    assert _row(await _acall(), "acp:codex")["state"] == "untested"


@pytest.mark.asyncio
async def test_testing_a_runtime_nobody_set_up_is_refused(counted_probe):
    calls, _stall, _answer = counted_probe
    status, data = await _atest("acp:nothing-here")
    assert status == 404 and data["error"]["code"] == "not_found"
    assert calls == {}


def test_native_row_always_present_and_ready():
    _fresh_registry()
    try:
        data = _call()
        rows = {r["provider_id"]: r for r in data["agent_providers"]}
        assert "native" in rows, "native runtime must always be listed"
        native = rows["native"]
        assert native["ready"] is True
        assert native["state"] == "ready"
        assert native["extension"] == "native-agents"
        assert native["login_command"] is None  # in-process, no sign-in
        assert native["tested_at"] is None
    finally:
        reset_default_registry()


def _call_agents(runtime_id: str, query: str = "") -> tuple[int, dict]:
    path = f"/api/agent-providers/{runtime_id}/agents" + (f"?{query}" if query else "")
    req = make_mocked_request("GET", path, match_info={"id": runtime_id})
    resp = asyncio.run(api_agent_provider_agents(req))
    return resp.status, json.loads(resp.body.decode())


def test_discovery_native_returns_empty():
    """native has no discovered agents (its agents are PClaw's own definitions)."""
    _fresh_registry()
    try:
        status, data = _call_agents("native")
        assert status == 200
        assert data["agents"] == [] and data["permission_modes"] == []
    finally:
        reset_default_registry()


def test_discovery_unknown_runtime_404():
    _fresh_registry()
    try:
        status, data = _call_agents("acp:does-not-exist")
        assert status == 404
    finally:
        reset_default_registry()


def test_discovery_lists_the_agents_the_last_test_found_and_a_read_starts_nothing(
    monkeypatch, tmp_path
):
    """The chat picker's agents for a runtime come from the session its Test opened — one
    start, read once. Before a Test the list is empty; reading it never starts the CLI."""
    from personalclaw.agents.provider import ReadinessStatus
    from personalclaw.agents.registry import get_agent_provider_class

    _fresh_registry()
    try:
        _register_runtime(
            "acp:test-cli", [_installed(tmp_path, "test-cli"), "acp"], dialect="default"
        )
        starts = {"n": 0}
        snapshot = {
            "modes": {"availableModes": [{"id": "gpu-dev", "name": "gpu-dev"}]},
            "models": {"availableModels": [{"modelId": "auto"}]},
        }

        async def ready_probe(cls, options):
            starts["n"] += 1
            return ReadinessStatus(ready=True, state="ready", session_snapshot=snapshot)

        monkeypatch.setattr(
            get_agent_provider_class("acp"), "probe_readiness", classmethod(ready_probe)
        )

        status, before = _call_agents("acp:test-cli", query="refresh=1")
        assert status == 200 and before["agents"] == [] and before["tested_at"] is None
        assert starts["n"] == 0, "reading a runtime's agents started it"

        async def press_test():
            return await _atest("acp:test-cli")

        asyncio.run(press_test())
        assert starts["n"] == 1

        status, after = _call_agents("acp:test-cli")
        assert status == 200 and after["tested_at"]
        assert [a["id"] for a in after["agents"]] == ["acp:test-cli/gpu-dev"]
        assert after["agents"][0]["provider_agent"] == "gpu-dev"
        assert after["agents"][0]["runtime"] == "acp:test-cli"
        assert starts["n"] == 1, "reading the agents after the Test started the CLI again"
    finally:
        reset_default_registry()


def _test_then_read_agents(monkeypatch, tmp_path, status, *, unreadable: str = ""):
    """Register a runtime, press its Test with *status* as the probe's answer, read its agents.

    *unreadable* makes the answer's snapshot fail to map, with that as the error's words."""
    from personalclaw.agents.registry import get_agent_provider_class

    _fresh_registry()
    _register_runtime("acp:test-cli", [_installed(tmp_path, "test-cli"), "acp"], dialect="default")
    cls = get_agent_provider_class("acp")

    async def probe(_cls, options):
        return status

    monkeypatch.setattr(cls, "probe_readiness", classmethod(probe))
    if unreadable:

        def refuse(_cls, options, snapshot):
            raise ValueError(unreadable)

        monkeypatch.setattr(cls, "agents_from_snapshot", classmethod(refuse))
    asyncio.run(_atest("acp:test-cli"))
    return _call_agents("acp:test-cli")


def test_a_test_that_failed_says_why_the_agents_are_unknown_not_that_there_are_none(
    monkeypatch, tmp_path
):
    """The agents are read in the session a Test opens. A Test that did not get one (here, the
    CLI needs a sign-in) knows nothing about them, and the read used to answer ``agents: []`` —
    which the Agents page shows as "No agents discovered"."""
    from personalclaw.agents.provider import ReadinessStatus
    from personalclaw.dashboard.handlers.providers import declared_efforts

    try:
        status, data = _test_then_read_agents(
            monkeypatch,
            tmp_path,
            ReadinessStatus(ready=False, state="needs_login", detail="run test-cli login first"),
        )
        assert status == 502, data
        assert data["error"]["code"] == "agent_discovery_failed"
        message = data["error"]["message"]
        assert "Test Cli" in message and "needs_login" in message
        assert "run test-cli login first" in message, "the reason is the Test's own words"
        assert declared_efforts("acp:test-cli") is None, "unknown, not a declaration of none"
    finally:
        reset_default_registry()


def test_a_test_whose_answer_could_not_be_read_says_so(monkeypatch, tmp_path):
    from personalclaw.agents.provider import ReadinessStatus

    try:
        status, data = _test_then_read_agents(
            monkeypatch,
            tmp_path,
            ReadinessStatus(ready=True, state="ready", session_snapshot={"sessionId": "s-1"}),
            unreadable="an answer shape nobody expected",
        )
        assert status == 502, data
        assert data["error"]["code"] == "agent_discovery_failed"
        assert "an answer shape nobody expected" in data["error"]["message"]
    finally:
        reset_default_registry()


def test_a_test_that_read_no_agents_is_an_empty_list(monkeypatch, tmp_path):
    """The control: a session whose answer lists no personas is the one case "none" is true."""
    from personalclaw.agents.provider import ReadinessStatus

    try:
        status, data = _test_then_read_agents(
            monkeypatch,
            tmp_path,
            ReadinessStatus(ready=True, state="ready", session_snapshot={"sessionId": "s-1"}),
        )
        assert status == 200, data
        assert data["agents"] == [] and data["tested_at"]
    finally:
        reset_default_registry()


def test_acp_entry_provider_id_is_entry_name_not_basename():
    """Regression: provider_id must be the canonical ``acp:<cli>`` entry name,
    not the adapter binary's basename (``claude-agent-acp``)."""
    _fresh_registry()
    try:
        registry = get_default_registry()
        registry.register_entry(
            ProviderEntry(
                name="acp:claude-code",
                type="acp_agent",
                model="claude-opus-4-8",
                # command[0] basename is the ADAPTER, deliberately != the cli id.
                options={
                    "command": ["/usr/local/bin/claude-agent-acp"],
                    "dialect": "claude-code",
                    "extension": "claude-code-agent",
                    "login_command": ["claude", "/login"],
                },
                credential=None,
                declared_capabilities=ACP_AGENT_CAPABILITY.capabilities,
            )
        )
        data = _call()
        rows = {r["provider_id"]: r for r in data["agent_providers"]}
        assert "acp:claude-code" in rows
        # NOT the adapter basename:
        assert "acp:claude-agent-acp" not in rows
        row = rows["acp:claude-code"]
        assert row["name"] == "acp:claude-code"
        assert row["extension"] == "claude-code-agent"
    finally:
        reset_default_registry()
