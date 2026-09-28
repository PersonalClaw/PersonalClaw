"""P1: the public removable ``acp:<cli>`` bundles (claude-code / codex).

Proves the bundle wiring matches how ACP agents are actually selected today:

* enabling a bundle registers an ``acp_agent`` ProviderEntry named ``acp:<cli>``
  with an EXPLICIT ``options.dialect`` + resolved ``options.command`` — so the
  entry surfaces in ``/api/agent-providers`` and resolves through the
  registry-build path an ``acp:<cli>`` agent uses;
* ``get_agent_provider_class("acp:<cli>")`` resolves the ``acp`` family →
  ``AcpAgentProvider`` (dialect is NOT inferred from the command basename);
* readiness probes cleanly: a faked-present binary is detected (state != not_found),
  an absent binary → not_found, and an absent codex never raises;
* Claude runs with a config of its own unless the ``isolated_config`` setting turns it
  off: ``<PersonalClaw home>/cc-config``, which starts as ``{}`` (0600) and takes nothing
  from ``~/.claude`` or from a ``CLAUDE_CONFIG_DIR`` the operator set (apps #137).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest

from personalclaw.agents.registry import get_agent_provider_class
from personalclaw.llm.acp_agent import ACP_AGENT_CAPABILITY
from personalclaw.llm.registry import get_default_registry, reset_default_registry

# The claude-code / codex ACP bundles are standalone APPS now (apps/<name>-agent/).
# Load their provider modules the way the app loader does (from the app dir under a
# namespaced module name) so this suite keeps exercising the real bundle wiring +
# its integration with the core ACP-agent registry.
_APPS_DIR = Path(__file__).resolve().parents[2] / "apps"
if not _APPS_DIR.is_dir():  # standalone core clone — the agent-app bundles aren't present
    pytest.skip("workspace apps/ dir not present (standalone clone)", allow_module_level=True)


def _load_app_provider(app_name: str):
    path = _APPS_DIR / app_name / "provider.py"
    uniq = f"_pclaw_app_{app_name.replace('-', '_')}__provider"
    spec = importlib.util.spec_from_file_location(uniq, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[uniq] = mod
    added = str(_APPS_DIR / app_name) not in sys.path
    if added:
        sys.path.insert(0, str(_APPS_DIR / app_name))
    try:
        spec.loader.exec_module(mod)
    finally:
        if added:
            sys.path.remove(str(_APPS_DIR / app_name))
    return mod


claude_code = _load_app_provider("claude-code-agent")
codex = _load_app_provider("codex-agent")


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Each test gets a fresh default registry (re-import the acp_agent type),
    then the process-wide singletons are RESTORED on teardown.

    ``importlib.reload(acp_agent)`` builds a NEW module + a NEW
    ``AcpAgentProvider`` class and re-registers it on both the model registry
    (``register_type``) and the agent registry (``register_agent_provider``).
    If we left that in place, a later test holding the ORIGINAL class via
    ``from … import AcpAgentProvider`` would fail an ``is`` identity check, and
    the model registry — emptied by ``reset_default_registry`` — would be
    missing every other provider (bedrock/anthropic/…) since ``import
    personalclaw.llm`` is already cached and won't re-run their registration.
    So snapshot everything we perturb and put it back. (#25c)
    """
    import importlib
    import sys

    import personalclaw.llm as _llm_pkg
    from personalclaw.agents import registry as _agent_reg
    from personalclaw.llm import registry as _model_reg

    saved_registry = _model_reg._default_registry
    saved_module = sys.modules.get("personalclaw.llm.acp_agent")
    saved_pkg_attr = getattr(_llm_pkg, "acp_agent", None)
    saved_agent_providers = dict(_agent_reg._providers)

    reset_default_registry()
    import personalclaw.llm.acp_agent as _acp_agent

    importlib.reload(_acp_agent)
    try:
        yield
    finally:
        _model_reg.set_default_registry(saved_registry)
        if saved_module is not None:
            sys.modules["personalclaw.llm.acp_agent"] = saved_module
            _llm_pkg.acp_agent = saved_pkg_attr
        _agent_reg._providers.clear()
        _agent_reg._providers.update(saved_agent_providers)


def _make_exec(path: Path) -> None:
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _fake_on_path(monkeypatch, tmp_path, name: str) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    target = bindir / name
    _make_exec(target)
    monkeypatch.setenv("PATH", str(bindir))
    return target


# ── discovery ──────────────────────────────────────────────────────────────


def test_public_bundles_exist_as_apps():
    # The bundles are standalone apps now (moved out of core bundled/).
    for name in ("claude-code-agent", "codex-agent"):
        assert (_APPS_DIR / name / "app.json").is_file(), f"{name} app.json missing"
        assert (_APPS_DIR / name / "provider.py").is_file(), f"{name} provider.py missing"


def test_manifests_are_agent_type_with_acp_capability():
    for name in ("claude-code-agent", "codex-agent"):
        m = json.loads((_APPS_DIR / name / "app.json").read_text())
        prov = m["provider"]
        assert prov["type"] == "agent"
        assert "acp" in prov["capabilities"]
        # app-local entrypoint form (module:function), resolved from the app dir
        assert prov["implementation"] == "provider:create_provider"


# ── claude-code wiring + dialect explicitness ────────────────────────────────


def test_claude_registers_entry_with_explicit_dialect(monkeypatch, tmp_path):
    _fake_on_path(monkeypatch, tmp_path, "claude-agent-acp")
    monkeypatch.delenv("CLAUDE_CODE_ACP_BIN", raising=False)

    claude_code.create_provider({"model": "claude-opus-4-8"})

    entry = get_default_registry().get_entry("acp:claude-code")
    assert entry.type == "acp_agent"
    assert entry.model == "claude-opus-4-8"
    assert entry.options["dialect"] == "claude-code"
    assert entry.options["command"][0].endswith("claude-agent-acp")
    assert entry.declared_capabilities == ACP_AGENT_CAPABILITY.capabilities


def test_claude_dialect_explicit_even_when_command_is_npx(monkeypatch, tmp_path):
    """The provider_id pitfall: an npx launch must NOT make dialect=npx."""
    # No claude-agent-acp anywhere → resolver returns the npx fallback argv.
    # Isolate HOME + PERSONALCLAW_HOME to an empty dir so neither the node-manager
    # globs NOR the managed adapter prefix (~/.personalclaw/acp-adapters) — both
    # rooted at the real home — leak a resolvable binary into this test.
    empty = tmp_path / "empty-home"
    empty.mkdir()
    monkeypatch.setenv("HOME", str(empty))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(empty / ".personalclaw"))
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("CLAUDE_CODE_ACP_BIN", raising=False)

    claude_code.create_provider({})

    entry = get_default_registry().get_entry("acp:claude-code")
    assert "npx" in entry.options["command"][0] or entry.options["command"][0].endswith("npx")
    # Dialect stays the explicit claude-code, not derived from the npx basename.
    assert entry.options["dialect"] == "claude-code"


def test_acp_family_resolves_to_acp_agent_provider():
    from personalclaw.llm.acp_agent import AcpAgentProvider

    assert get_agent_provider_class("acp:claude-code") is AcpAgentProvider
    assert get_agent_provider_class("acp:codex") is AcpAgentProvider


# ── readiness proofs ─────────────────────────────────────────────────────────


def test_claude_readiness_present_is_not_not_found(monkeypatch, tmp_path):
    """Faked-present binary → binary IS detected (the fake can't speak ACP, so
    the handshake fails → 'error', but crucially NOT 'not_found').

    The adapter delegates to the ``claude`` engine, so fake that on PATH too —
    otherwise the delegate gate (correctly) reports ``not_found`` for the missing
    engine before the handshake is ever attempted."""
    bindir = _fake_on_path(monkeypatch, tmp_path, "claude-agent-acp").parent
    _make_exec(bindir / "claude")  # the delegate engine the adapter needs
    monkeypatch.delenv("CLAUDE_CODE_ACP_BIN", raising=False)
    claude_code.create_provider({})
    entry = get_default_registry().get_entry("acp:claude-code")

    cls = get_agent_provider_class("acp:claude-code")
    status = asyncio.run(cls.probe_readiness(entry.options))
    assert status.state != "not_found"
    assert status.ready is False  # the fake isn't a real ACP agent


def test_probe_uses_configured_dialect_not_default(monkeypatch, tmp_path):
    """Regression: probe_readiness must build its client with the options' dialect.

    A claude/codex adapter expects int protocolVersion; if the probe falls back
    to the default date-string shape, the adapter rejects initialize with
    -32602 and a healthy CLI looks broken. Pin that the dialect threads through."""
    from personalclaw.llm.acp_agent import AcpAgentProvider

    fake = _fake_on_path(monkeypatch, tmp_path, "claude-agent-acp")
    captured = {}
    real_init = AcpAgentProvider.__init__

    def spy_init(self, **kw):
        captured["dialect"] = kw.get("dialect")
        real_init(self, **kw)

    monkeypatch.setattr(AcpAgentProvider, "__init__", spy_init)
    # start() will fail (the fake binary can't speak ACP) — we only assert the
    # constructor received the configured dialect. command must point at the
    # faked binary so the which() gate passes and the provider is constructed.
    options = {"command": [str(fake)], "dialect": "claude-code"}
    asyncio.run(AcpAgentProvider.probe_readiness(options))
    assert captured["dialect"] == "claude-code"


def test_claude_readiness_absent_is_not_found(monkeypatch):
    """A configured command whose binary is absent → not_found, no raise."""
    cls = get_agent_provider_class("acp:claude-code")
    options = {"command": ["/nonexistent/claude-agent-acp"], "dialect": "claude-code"}
    status = asyncio.run(cls.probe_readiness(options))
    assert status.state == "not_found"
    assert status.ready is False


def test_codex_absent_probes_not_ready_without_raising(monkeypatch):
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("CODEX_ACP_BIN", raising=False)
    codex.create_provider({})
    # codex-acp absent → resolver falls back to npx; probe gates on `which npx`.
    entry = get_default_registry().get_entry("acp:codex")
    cls = get_agent_provider_class("acp:codex")
    status = asyncio.run(cls.probe_readiness(entry.options))  # must not raise
    assert status.ready is False
    assert status.state in ("not_found", "error", "needs_login", "timeout")


def test_npx_fallback_without_node_ge_is_clean_not_found(monkeypatch, tmp_path):
    """The durable-fix gate: an ``npx -y <pkg>`` command + NO Node >= 20 must
    report a clean ``not_found`` ("adapter not installed, can't provision")
    instead of spawning npx and dying on a raw EBADENGINE/fetch error."""
    # npx must exist so the earlier `which(command[0])` check passes and we reach
    # the provisioning gate (not the plain "binary absent" branch).
    npx = tmp_path / "npx"
    npx.write_text("#!/bin/sh\nexit 0\n")
    npx.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    # No Node >= 20 anywhere.
    monkeypatch.setattr("personalclaw.acp.cli_resolve.resolve_node_ge", lambda *a, **k: None)
    cls = get_agent_provider_class("acp:codex")
    options = {"command": [str(npx), "-y", "@agentclientprotocol/codex-acp"], "dialect": "codex"}
    status = asyncio.run(cls.probe_readiness(options))
    assert status.ready is False
    assert status.state == "not_found"
    assert "Node >= 20" in status.detail or "not installed" in status.detail


def test_codex_forwards_underlying_cli(monkeypatch, tmp_path):
    """codex bundle resolves the host `codex` CLI and forwards it as CODEX_PATH —
    the EXACT env var the codex-acp adapter reads (it spawns `<CODEX_PATH ??
    "codex"> app-server` and inherits that codex's own auth). It does NOT read
    CODEX_EXECUTABLE, so forwarding under that name would be silently dropped and
    the adapter would fall back to its bundled OpenAI-auth codex → "Authentication
    required". Regression for that env-var-name mismatch."""
    _fake_on_path(monkeypatch, tmp_path, "codex-acp")  # adapter resolvable
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("#!/bin/sh\n")
    fake_codex.chmod(0o755)
    monkeypatch.setenv("CODEX_PATH", str(fake_codex))
    monkeypatch.delenv("CODEX_ACP_BIN", raising=False)
    codex.create_provider({})
    entry = get_default_registry().get_entry("acp:codex")
    assert entry.options.get("env", {}).get("CODEX_PATH") == str(fake_codex)
    # Never forward the ignored var — that was the bug.
    assert "CODEX_EXECUTABLE" not in entry.options.get("env", {})


def test_codex_declares_engine_requirement(monkeypatch, tmp_path):
    """codex bundle records its delegate engine as ``requires_executable`` so the
    vendor-neutral probe can enforce it (vendor knowledge stays in the bundle).
    The declared env_var is CODEX_PATH — the override the adapter actually honors."""
    _fake_on_path(monkeypatch, tmp_path, "codex-acp")
    monkeypatch.delenv("CODEX_ACP_BIN", raising=False)
    monkeypatch.delenv("CODEX_PATH", raising=False)
    codex.create_provider({})
    req = get_default_registry().get_entry("acp:codex").options.get("requires_executable")
    assert req and req["label"] == "codex" and req["env_var"] == "CODEX_PATH"


def test_delegate_gate_absent_engine_is_not_found(monkeypatch, tmp_path):
    """A passing ACP handshake is NOT sufficient: when the adapter is present but
    its declared engine CLI is absent, the probe reports not_found UP FRONT (no
    spawn) instead of a hollow 'ready' that would die on the first prompt.

    Regression for the live codex false-positive: codex-acp resolves via npx and
    handshakes fine, but no `codex` engine exists on the machine."""
    from personalclaw.llm.acp_agent import AcpAgentProvider

    fake_adapter = _fake_on_path(monkeypatch, tmp_path, "codex-acp")
    # Engine deliberately NOT created. PATH is only the fake bindir, so a live
    # `which codex` also misses.
    options = {
        "command": [str(fake_adapter)],
        "dialect": "codex",
        "requires_executable": {"label": "codex", "env_var": "CODEX_PATH", "path": ""},
    }
    spawned = {"hit": False}
    orig_init = AcpAgentProvider.__init__

    def spy_init(self, **kw):
        spawned["hit"] = True
        orig_init(self, **kw)

    monkeypatch.setattr(AcpAgentProvider, "__init__", spy_init)
    status = asyncio.run(AcpAgentProvider.probe_readiness(options))
    assert status.state == "not_found"
    assert status.ready is False
    assert "codex" in status.detail
    assert spawned["hit"] is False  # gated before any handshake spawn


def test_delegate_gate_satisfied_by_declared_path(monkeypatch, tmp_path):
    """When the bundle resolved the engine (forwarded via its env var → recorded
    as requires_executable.path), the gate is satisfied and the probe proceeds to
    the handshake (which then fails on the fake adapter → not 'not_found')."""
    from personalclaw.llm.acp_agent import AcpAgentProvider

    fake_adapter = _fake_on_path(monkeypatch, tmp_path, "codex-acp")
    fake_engine = fake_adapter.parent / "codex"
    _make_exec(fake_engine)
    options = {
        "command": [str(fake_adapter)],
        "dialect": "codex",
        "requires_executable": {
            "label": "codex",
            "env_var": "CODEX_PATH",
            "path": str(fake_engine),
        },
    }
    status = asyncio.run(AcpAgentProvider.probe_readiness(options))
    # The fake adapter can't speak ACP, so the handshake fails — but the delegate
    # gate let it THROUGH (state is not the engine-missing not_found).
    assert status.state != "not_found"


def test_delegate_gate_satisfied_by_live_path(monkeypatch, tmp_path):
    """No declared path, but the engine is live-resolvable on PATH → gate passes."""
    from personalclaw.llm.acp_agent import AcpAgentProvider

    fake_adapter = _fake_on_path(monkeypatch, tmp_path, "codex-acp")
    _make_exec(fake_adapter.parent / "codex")  # engine on the same (only) PATH dir
    options = {
        "command": [str(fake_adapter)],
        "dialect": "codex",
        "requires_executable": {"label": "codex", "env_var": "CODEX_PATH", "path": ""},
    }
    status = asyncio.run(AcpAgentProvider.probe_readiness(options))
    assert status.state != "not_found"


def test_no_delegate_declaration_skips_gate(monkeypatch, tmp_path):
    """A runtime whose binary IS the engine (a native CLI, not a Zed adapter
    delegating to a separate engine) declares no requires_executable, so the
    delegate gate is skipped entirely."""
    from personalclaw.llm.acp_agent import AcpAgentProvider

    fake = _fake_on_path(monkeypatch, tmp_path, "native-acp-cli")
    options = {"command": [str(fake), "acp"], "dialect": "default"}
    status = asyncio.run(AcpAgentProvider.probe_readiness(options))
    # Reaches the handshake (fake can't speak ACP) — never the engine not_found.
    assert status.state != "not_found"


def _register_default_dialect_entry(monkeypatch, tmp_path, *, model: str = ""):
    """Register a neutral ``acp:test-cli`` default-dialect entry the way a bundle
    would, without depending on any specific vendor bundle. Returns the cli id."""
    from personalclaw.acp_bundles._register import register_acp_cli_entry

    fake = _fake_on_path(monkeypatch, tmp_path, "test-cli")
    register_acp_cli_entry(
        cli="test-cli",
        dialect="default",
        command=[str(fake), "acp"],
        model=model,
        extension="test-cli-agent",
    )
    return "test-cli"


def test_factory_honors_per_session_agent_model_mode(monkeypatch, tmp_path):
    """Regression: the acp _factory MUST honor the per-session agent (modeId),
    model, and acp_mode the bridge passes — not just the global entry defaults.
    Before the fix these kwargs were dropped, so selecting an ACP agent silently
    ran the backend default and never switched the modeId."""
    _register_default_dialect_entry(monkeypatch, tmp_path)
    reg = get_default_registry()
    entry = reg.get_entry("acp:test-cli")
    # Build exactly as the bridge does: per-session agent/model/acp_mode kwargs.
    config = {"model": entry.model, **(entry.options or {})}
    config.update(agent="gpu-dev", model="claude-opus-4.8", acp_mode="plan")
    prov = reg.build("acp:test-cli", **config)
    assert prov._agent_name == "gpu-dev"  # per-session modeId, not "PersonalClaw"
    assert prov._model == "claude-opus-4.8"  # per-session model, not entry default
    assert prov._mode == "plan"  # per-session mode threaded through
    # And it reaches the protocol client.
    assert prov.client._agent == "gpu-dev"
    assert prov.client._model == "claude-opus-4.8"
    assert prov.client._mode == "plan"


def test_factory_falls_back_to_entry_defaults_without_per_session(monkeypatch, tmp_path):
    """With no per-session agent/model/mode, the factory uses the entry defaults.
    The agent axis falls back to EMPTY (no fabricated name) — ACP has no global
    default agent, so an unselected agent means the CLI uses its own built-in
    default (the dialect skips the set_mode activation for an empty agent)."""
    _register_default_dialect_entry(monkeypatch, tmp_path, model="glm-5")
    reg = get_default_registry()
    entry = reg.get_entry("acp:test-cli")
    prov = reg.build("acp:test-cli", **{"model": entry.model, **(entry.options or {})})
    assert prov._agent_name == ""
    assert prov._model == "glm-5"
    assert prov._mode == ""


def test_claude_agents_from_a_test_snapshot_are_one_agent_with_its_efforts():
    """claude's ``session/new`` (what a Test's one session returns) → exactly ONE base agent;
    the backend's effort levels ride along as supported_efforts (a per-turn setting), NOT
    effort-variant agents. Pure: mapping a snapshot starts nothing."""
    from personalclaw.llm.acp_agent import AcpAgentProvider

    snew = {
        "configOptions": [
            {"id": "model", "options": [{"value": "default"}, {"value": "opus"}]},
            {
                "id": "effort",
                "options": [
                    {"value": "default", "name": "Default"},
                    {"value": "high", "name": "High"},
                    {"value": "max", "name": "Max"},
                ],
            },
        ],
    }
    agents = AcpAgentProvider.agents_from_snapshot(
        {"dialect": "claude-code", "runtime_id": "acp:claude-code", "runtime_label": "Claude"},
        snew,
    )
    # ONE agent — no effort variants in the picker.
    assert len(agents) == 1
    base = agents[0]
    assert base.id == "acp:claude-code" and base.name == "Claude"
    assert base.reasoning_effort == "" and base.models == ["default", "opus"]
    # Effort levels surface verbatim as supported_efforts (the composer's pill).
    assert base.supported_efforts == [
        {"value": "high", "label": "High"},
        {"value": "max", "label": "Max"},
    ]


def test_bundle_factories_return_none(monkeypatch, tmp_path):
    """Like native-agents, the factory returns None (config/registry-based)."""
    _fake_on_path(monkeypatch, tmp_path, "claude-agent-acp")
    assert claude_code.create_provider({}) is None
    assert codex.create_provider({}) is None


def test_enable_is_idempotent(monkeypatch, tmp_path):
    """Re-running create_provider must not raise the duplicate-name guard."""
    _fake_on_path(monkeypatch, tmp_path, "claude-agent-acp")
    claude_code.create_provider({})
    claude_code.create_provider({})  # second enable — should replace, not raise
    assert get_default_registry().get_entry("acp:claude-code") is not None


# ── claude config isolation (the E12 §6 security control) ────────────────────
#
# Since apps #137 the spawned Claude runs with a config of its own unless the
# ``isolated_config`` setting turns it off. It starts as ``{}``: nothing comes from the
# operator's ``~/.claude`` or from a ``CLAUDE_CONFIG_DIR`` they set, so none of their
# auto-approve rules come along and every Claude tool asks the host first.


def _permissive_claude(root: Path) -> dict:
    """An operator Claude config that auto-approves every tool, written to ``root``."""
    settings = {
        "awsCredentialExport": "aws configure export-credentials",
        "permissions": {
            "allow": ["Bash(*)"],
            "ask": ["Read"],
            "defaultMode": "acceptEdits",
            "deny": ["Bash(rm:*)"],
        },
        "enabledPlugins": {"x": True},
        "model": "claude-opus-4-8",
    }
    root.mkdir(parents=True)
    (root / "settings.json").write_text(json.dumps(settings))
    return settings


def _home(monkeypatch, tmp_path) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home / ".personalclaw"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_EXECUTABLE", raising=False)
    return home


def test_claude_runs_with_its_own_empty_config_by_default(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    operator = _permissive_claude(home / ".claude")

    env = claude_code._build_env()

    root = home / ".personalclaw" / "cc-config"
    assert env["CLAUDE_CONFIG_DIR"] == str(root)
    seeded = root / "settings.json"
    assert json.loads(seeded.read_text()) == {}, "nothing is copied from ~/.claude"
    assert stat.S_IMODE(seeded.stat().st_mode) == 0o600
    assert json.loads((home / ".claude" / "settings.json").read_text()) == operator


def test_a_claude_config_dir_the_operator_set_is_never_the_isolated_one(monkeypatch, tmp_path):
    """It used to become the "isolated" root and be rewritten. It is the operator's own."""
    home = _home(monkeypatch, tmp_path)
    theirs = home / "my-claude"
    operator = _permissive_claude(theirs)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(theirs))

    env = claude_code._build_env()

    root = home / ".personalclaw" / "cc-config"
    assert env["CLAUDE_CONFIG_DIR"] == str(root)
    assert json.loads((root / "settings.json").read_text()) == {}
    assert json.loads((theirs / "settings.json").read_text()) == operator


def test_what_the_operator_adds_to_the_isolated_config_is_kept(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    root = home / ".personalclaw" / "cc-config"
    root.mkdir(parents=True)
    (root / "settings.json").write_text(json.dumps({"model": "claude-sonnet-4-5"}))

    claude_code._build_env()

    assert json.loads((root / "settings.json").read_text()) == {"model": "claude-sonnet-4-5"}


def test_the_setting_turns_isolation_off_and_sign_in_follows_it(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    _fake_on_path(monkeypatch, tmp_path, "claude-agent-acp")
    root = home / ".personalclaw" / "cc-config"

    claude_code.create_provider({})
    isolated = get_default_registry().get_entry("acp:claude-code").options
    assert isolated["env"]["CLAUDE_CONFIG_DIR"] == str(root)
    assert isolated["login_command"] == ["env", f"CLAUDE_CONFIG_DIR={root}", "claude", "/login"]

    claude_code.create_provider({"isolated_config": False})
    shared = get_default_registry().get_entry("acp:claude-code").options
    assert "CLAUDE_CONFIG_DIR" not in shared.get("env", {})
    assert shared["login_command"] == ["claude", "/login"]


def test_bundle_options_flow_into_client_dialect(monkeypatch, tmp_path):
    """The bundle's options.dialect must reach the AcpClient and drive the
    handshake — the load-bearing seam. Build the provider through the real
    factory (the path an acp:<cli> agent resolves) and assert the client got
    the claude-code dialect (int protocolVersion, set_config_option model)."""
    from personalclaw.acp.dialect import ClaudeCodeDialect
    from personalclaw.llm.acp_agent import _factory
    from personalclaw.llm.registry import ProviderEntry

    _fake_on_path(monkeypatch, tmp_path, "claude-agent-acp")
    claude_code.create_provider({})
    entry: ProviderEntry = get_default_registry().get_entry("acp:claude-code")

    provider = _factory(entry=entry)
    dialect = provider.client._dialect
    assert isinstance(dialect, ClaudeCodeDialect)
    # The CC handshake divergences the client will emit:
    assert dialect.protocol_version() == 1  # int, not the default dialect date-string
    sm = dialect.set_model_request(session_id="s", model="claude-opus-4-8", default_model="")
    assert sm is not None and sm.method == "session/set_config_option"
    assert sm.params == {"sessionId": "s", "configId": "model", "value": "claude-opus-4-8"}
    # No set_mode for the Zed adapter (agent bound at launch).
    assert dialect.activate_agent_request(session_id="s", agent="x") is None
    # Permission options read the public-spec optionId/name shape.
    parsed = dialect.parse_permission_options(
        [{"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"}]
    )
    assert parsed == [{"id": "allow_once", "label": "Allow once", "kind": "allow_once"}]


def test_codex_options_flow_into_client_dialect(monkeypatch, tmp_path):
    from personalclaw.acp.dialect import CodexDialect
    from personalclaw.llm.acp_agent import _factory

    _fake_on_path(monkeypatch, tmp_path, "codex-acp")
    monkeypatch.delenv("CODEX_ACP_BIN", raising=False)
    codex.create_provider({})
    entry = get_default_registry().get_entry("acp:codex")
    provider = _factory(entry=entry)
    assert isinstance(provider.client._dialect, CodexDialect)
    assert provider.client._dialect.protocol_version() == 1
