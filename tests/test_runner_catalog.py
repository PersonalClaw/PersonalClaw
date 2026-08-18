"""EI-5: the BYO runner catalog — measured evidence, verbatim probe errors, and the
unattended adapter-verification gate enforced AT THE SPAWN CALL SITE.

Three claims this suite exists to keep honest:

1. **Evidence is measured or absent, never invented.** ``latency_ms`` is populated only
   when a process actually ran and was timed; ``version`` only when the CLI's own output
   carried one. A runner that was never probed reports ``health: null``, not a healthy
   default. Fabricating any of these is the specific failure mode the host's made-up
   ``0%`` context chip demonstrated — a number reads as an answer.
2. **A failed probe carries the probe's OWN text.** The surface prints the resolver's or
   the CLI's exact words, because "not found on PATH (looked for: gemini)" tells you
   which binary to install and "unavailable" does not.
3. **The refusal is enforced, not declared.** The gate is asserted through
   ``SessionManager.get_or_create`` — the thing that actually claims or spawns a runner
   — by proving the provider factory is NEVER CALLED. Its vacuity floor is the same call
   with the flag off (and the same call while interactive), which must proceed.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw.agents import runners
from personalclaw.config import AppConfig
from personalclaw.session import SessionManager

# ── helpers ───────────────────────────────────────────────────────────────────

_MISSING_BIN = "pclaw-no-such-runner-xyz"
_FAKE_ENV = "PCLAW_FAKE_RUNNER_BIN"


def _home() -> Path:
    """The ACTIVE config home, resolved at call time.

    Deliberately NOT ``from personalclaw.config.loader import config_dir`` at module
    scope: a test module is imported during collection, so a module-level binding
    freezes the UNPATCHED function and every write here lands in the user's real
    ``~/.personalclaw`` while the code under test reads the tmp home. That is exactly
    how the first run of this file put four files in the real home.
    """
    from personalclaw.config.loader import config_dir

    return config_dir()


def _write_exec(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _absent_runner() -> runners.RunnerDefinition:
    """A definition whose CLI cannot resolve — the 'removed from PATH' case."""
    return runners.RunnerDefinition(
        id="fake-runner",
        display_name="Fake Runner",
        runtime_id="acp:fake-runner",
        bin_names=(_MISSING_BIN,),
        env_var=_FAKE_ENV,
    )


def _expected_absent_error() -> str:
    """The verbatim text :func:`probe_runner` must emit for an unresolvable CLI."""
    return (
        f"'{_MISSING_BIN}' not found on PATH (looked for: {_MISSING_BIN}); "
        f"set {_FAKE_ENV} to override"
    )


def _byo(defn_dict: dict) -> Path:
    d = _home() / runners.USER_CATALOG_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{defn_dict['id']}.json"
    p.write_text(json.dumps(defn_dict), encoding="utf-8")
    return p


def _provisioned_adapter(tmp_path: Path, npm_pkg: str, *, record: bool = True) -> Path:
    """Fake a PersonalClaw-provisioned adapter: a real bin + npm's own lock record."""
    prefix = runners.managed_adapter_prefix()
    bin_dir = prefix / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    adapter = _write_exec(bin_dir / "fake-acp", "#!/bin/sh\nexit 0\n")
    (prefix / "package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    f"node_modules/{npm_pkg}": {
                        "version": "1.4.2",
                        "integrity": "sha512-AAAABBBBCCCCDDDD",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    if record:
        assert runners.record_provenance(npm_pkg) is True
    return adapter


# ── the shipped catalog ───────────────────────────────────────────────────────


def test_shipped_catalog_carries_the_four_runner_rows():
    """Claude Code / Codex / Gemini CLI / Kiro are cataloged rows, not app-conditional.

    Also the package-data claim: if the wheel ever ships without
    ``agents/runner_catalog.json`` this is what reds instead of the Store quietly
    showing an empty Runners section.
    """
    cat = runners.catalog()
    assert {"claude-code", "codex", "gemini-cli", "kiro"} <= set(cat)
    names = {d.display_name for d in cat.values()}
    assert {"Claude Code", "Codex", "Gemini CLI", "Kiro"} <= names
    for defn in cat.values():
        assert defn.runtime_id.startswith("acp:")
        assert defn.bin_names, f"{defn.id} declares no binary to look for"
        assert defn.source == "builtin"


def test_byo_definition_adds_a_row_and_can_replace_a_shipped_one(tmp_path):
    """A user drops JSON into ``runners/`` and the catalog picks it up — same id wins."""
    _byo(
        {
            "id": "my-runner",
            "display_name": "My Runner",
            "bin_names": ["my-cli"],
            "env_var": "MY_CLI_BIN",
        }
    )
    _byo({"id": "codex", "display_name": "Codex (pinned locally)", "bin_names": ["codex"]})
    cat = runners.catalog()
    assert cat["my-runner"].display_name == "My Runner"
    assert cat["my-runner"].runtime_id == "acp:my-runner"
    assert cat["my-runner"].source == "user"
    assert cat["codex"].display_name == "Codex (pinned locally)"
    assert cat["codex"].source == "user"


# ── clause 1: evidence is measured or absent ──────────────────────────────────


def test_absent_binary_records_no_latency_and_no_version():
    """Nothing ran ⇒ nothing was timed. ``latency_ms`` MUST stay None, not 0.

    A fabricated ``0`` would render as "0 ms", which reads like a measurement of an
    instant handshake — the exact shape of the fabricated-metric defect this guards.
    """
    ev = runners.probe_runner(_absent_runner())
    assert ev.ok is False
    assert ev.latency_ms is None, "latency was reported for a probe that never ran"
    assert ev.version is None
    assert ev.probe == "path"


def test_successful_probe_measures_latency_and_parses_the_reported_version(monkeypatch, tmp_path):
    """A real spawn yields a real elapsed time and the CLI's own version string."""
    bin_path = _write_exec(tmp_path / "fake-cli", "#!/bin/sh\necho 'fake-cli 3.7.1'\n")
    monkeypatch.setenv(_FAKE_ENV, str(bin_path))
    ev = runners.probe_runner(_absent_runner())
    assert ev.ok is True
    assert ev.version == "3.7.1"
    assert ev.latency_ms is not None and ev.latency_ms >= 0
    assert ev.error is None
    assert ev.resolved_command == (str(bin_path),)


def test_unparseable_version_output_reports_unknown_not_a_placeholder(monkeypatch, tmp_path):
    """The CLI answered but told us no version ⇒ ``version`` is None (unknown)."""
    bin_path = _write_exec(tmp_path / "quiet-cli", "#!/bin/sh\necho 'ready'\n")
    monkeypatch.setenv(_FAKE_ENV, str(bin_path))
    ev = runners.probe_runner(_absent_runner())
    assert ev.ok is True
    assert ev.version is None
    assert ev.latency_ms is not None  # the spawn WAS timed


def test_never_probed_runner_surfaces_null_health_not_a_default():
    """A row with no recorded evidence reports ``health: null`` — not "fine"."""
    _byo({"id": "unprobed", "display_name": "Unprobed", "bin_names": ["nope-cli"]})
    row = next(r for r in runners.runner_rows(probe=False) if r.definition.id == "unprobed")
    assert row.to_dict()["health"] is None


# ── clause 2: the verbatim probe error reaches the surface ────────────────────


def test_probe_error_is_the_probes_own_text():
    ev = runners.probe_runner(_absent_runner())
    assert ev.error == _expected_absent_error()


def test_failed_cli_error_is_the_clis_own_stderr(monkeypatch, tmp_path):
    """A non-zero exit surfaces the CLI's words, not a house summary."""
    bin_path = _write_exec(
        tmp_path / "angry-cli", "#!/bin/sh\necho 'FATAL: no credentials in keyring' >&2\nexit 7\n"
    )
    monkeypatch.setenv(_FAKE_ENV, str(bin_path))
    ev = runners.probe_runner(_absent_runner())
    assert ev.ok is False
    assert ev.error == "FATAL: no credentials in keyring"
    assert ev.latency_ms is not None


@pytest.mark.asyncio
async def test_verbatim_error_survives_to_the_api_response():
    """The exact probe text is what ``GET /api/agent-runners`` hands the UI.

    This is the end of clause 2: it is not enough that the probe knows the reason — the
    surface has to carry it. A generic 'unavailable' anywhere between here and the row
    dict reds this assertion.
    """
    from personalclaw.dashboard.handlers.providers import api_agent_runners_list

    _byo(
        {
            "id": "fake-runner",
            "display_name": "Fake Runner",
            "bin_names": [_MISSING_BIN],
            "env_var": _FAKE_ENV,
        }
    )
    request = SimpleNamespace(query={"probe": "1"})
    resp = await api_agent_runners_list(request)  # type: ignore[arg-type]
    payload = json.loads(resp.text or "{}")
    row = next(r for r in payload["runners"] if r["id"] == "fake-runner")
    assert row["health"]["ok"] is False
    assert row["health"]["error"] == _expected_absent_error()
    assert row["health"]["latency_ms"] is None


# ── adapter pin + verify ──────────────────────────────────────────────────────


def test_runner_without_an_npm_adapter_is_no_adapter():
    verdict = runners.verify_adapter(_absent_runner())
    assert verdict.state == "no_adapter"
    assert verdict.verified is True


def test_npx_fallback_is_never_verified(monkeypatch):
    """``npx -y <pkg>`` fetches at launch, so it can be neither pinned nor checksummed."""
    monkeypatch.delenv("FAKE_RUNNER_ACP_BIN", raising=False)
    defn = runners.RunnerDefinition(
        id="fake-runner",
        display_name="Fake Runner",
        runtime_id="acp:fake-runner",
        bin_names=(_MISSING_BIN,),
        adapter=runners.AdapterPin(
            npm_pkg="@fake/adapter", env_var="FAKE_ADAPTER_BIN", bin_names=("fake-acp",)
        ),
    )
    verdict = runners.verify_adapter(defn)
    assert verdict.state == "unverified"
    assert verdict.verified is False
    assert "npx" in verdict.detail


def test_adapter_without_recorded_provenance_is_unverified(monkeypatch, tmp_path):
    """On disk is not enough — an adapter PersonalClaw never provisioned is unproven."""
    adapter = _write_exec(tmp_path / "fake-acp", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("FAKE_ADAPTER_BIN", str(adapter))
    defn = runners.RunnerDefinition(
        id="fake-runner",
        display_name="Fake Runner",
        runtime_id="acp:fake-runner",
        bin_names=(_MISSING_BIN,),
        adapter=runners.AdapterPin(
            npm_pkg="@fake/adapter", env_var="FAKE_ADAPTER_BIN", bin_names=("fake-acp",)
        ),
    )
    verdict = runners.verify_adapter(defn)
    assert verdict.state == "unverified"
    assert "no recorded provenance" in verdict.detail


def test_provisioned_adapter_verifies_and_drift_unverifies(monkeypatch, tmp_path):
    """Provenance recorded at provision time; a later integrity change unverifies it."""
    adapter = _provisioned_adapter(tmp_path, "@fake/adapter")
    monkeypatch.setenv("FAKE_ADAPTER_BIN", str(adapter))
    defn = runners.RunnerDefinition(
        id="fake-runner",
        display_name="Fake Runner",
        runtime_id="acp:fake-runner",
        bin_names=(_MISSING_BIN,),
        adapter=runners.AdapterPin(
            npm_pkg="@fake/adapter", env_var="FAKE_ADAPTER_BIN", bin_names=("fake-acp",)
        ),
    )
    assert runners.verify_adapter(defn).state == "verified"

    # The adapter is swapped underneath the install: npm now reports a different
    # integrity than what was recorded. That must stop verifying.
    prefix = runners.managed_adapter_prefix()
    (prefix / "package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/@fake/adapter": {
                        "version": "1.4.2",
                        "integrity": "sha512-TAMPEREDTAMPERED",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    drifted = runners.verify_adapter(defn)
    assert drifted.state == "unverified"
    assert "integrity changed" in drifted.detail


def test_pin_mismatch_refuses_to_record_provenance(tmp_path):
    """Recording must not bless a mismatch — a wrong install stays unverifiable."""
    _provisioned_adapter(tmp_path, "@fake/adapter", record=False)
    pin = runners.AdapterPin(
        npm_pkg="@fake/adapter", version="9.9.9", integrity="sha512-SOMETHINGELSE"
    )
    assert runners.record_provenance("@fake/adapter", pin=pin) is False
    assert not runners.adapter_lock_path().exists()


# ── capability persistence from the real discovery path ───────────────────────


def test_capabilities_persist_from_the_discovery_snapshot():
    """``agents_from_snapshot`` is the one place a matrix arrives normalized off the
    wire, so it is the only source the chips are allowed to come from."""
    from personalclaw.llm.acp_agent import AcpAgentProvider

    _byo(
        {
            "id": "fake-runner",
            "display_name": "Fake Runner",
            "bin_names": [_MISSING_BIN],
            "runtime_id": "acp:fake-runner",
        }
    )
    snapshot = {
        "modes": {
            "currentModeId": "default",
            "availableModes": [
                {"id": "default", "name": "Default"},
                {"id": "acceptEdits", "name": "Accept Edits"},
            ],
        },
        "models": {
            "currentModelId": "m1",
            "availableModels": [{"modelId": "m1", "name": "Model One"}],
        },
    }
    AcpAgentProvider.agents_from_snapshot({"runtime_id": "acp:fake-runner"}, snapshot)
    caps = runners.load_capabilities("fake-runner")
    assert caps is not None
    assert caps["source"] == "initialize"
    assert "m1" in caps["models"]

    row = next(r for r in runners.runner_rows(probe=False) if r.definition.id == "fake-runner")
    assert row.to_dict()["capabilities"]["models"] == ["m1"]


# ── clause 3: the refusal, at the spawn call site ─────────────────────────────


class _FakeProvider:
    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


def _gate_fixture(monkeypatch, *, flag: bool, verified: bool, tmp_path: Path):
    """Catalog a runner, set the flag, and return (manager, calls) for get_or_create."""
    adapter: dict | None
    if verified:
        adapter_bin = _provisioned_adapter(tmp_path, "@fake/adapter")
        monkeypatch.setenv("FAKE_ADAPTER_BIN", str(adapter_bin))
        adapter = {
            "npm_pkg": "@fake/adapter",
            "env_var": "FAKE_ADAPTER_BIN",
            "bin_names": ["fake-acp"],
        }
    else:
        # Resolvable only through the npx last resort → never verified.
        monkeypatch.delenv("FAKE_ADAPTER_BIN", raising=False)
        adapter = {
            "npm_pkg": "@fake/adapter",
            "env_var": "FAKE_ADAPTER_BIN",
            "bin_names": ["definitely-not-installed-acp"],
        }
    _byo(
        {
            "id": "fake-runner",
            "display_name": "Fake Runner",
            "runtime_id": "acp:fake-runner",
            "bin_names": [_MISSING_BIN],
            "adapter": adapter,
        }
    )
    cfg = AppConfig.load()
    cfg.agent.unattended_requires_verified_adapter = flag
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda _cls: cfg))

    calls: list[dict] = []

    def factory(key, **kwargs):
        calls.append({"key": key, **kwargs})
        return _FakeProvider()

    return SessionManager(cfg, provider_factory=factory), calls


@pytest.mark.asyncio
async def test_unattended_spawn_onto_an_unverified_adapter_is_refused(monkeypatch, tmp_path):
    """Flag ON + unattended + unverified ⇒ refused BEFORE anything is spawned.

    The assertion that makes this a call-site test rather than a predicate test: the
    provider factory — the thing that launches the runner — is never called.
    """
    mgr, calls = _gate_fixture(monkeypatch, flag=True, verified=False, tmp_path=tmp_path)
    with pytest.raises(runners.UnverifiedAdapterError) as exc:
        await mgr.get_or_create(
            "unattended:ei5-refuse", provider_kind="acp:fake-runner", unattended=True
        )
    assert "not verified" in str(exc.value)
    assert calls == [], "the runner was spawned despite the refusal"


@pytest.mark.asyncio
async def test_flag_off_lets_the_same_unattended_spawn_proceed(monkeypatch, tmp_path):
    """VACUITY FLOOR. Identical call, flag off ⇒ the spawn happens.

    Without this, "refused" could pass simply because nothing was wired: an exception
    raised for an unrelated reason, or a call path that never reaches the factory at
    all, would look exactly like enforcement.
    """
    mgr, calls = _gate_fixture(monkeypatch, flag=False, verified=False, tmp_path=tmp_path)
    await mgr.get_or_create(
        "unattended:ei5-allow", provider_kind="acp:fake-runner", unattended=True
    )
    assert len(calls) == 1
    assert calls[0]["provider_kind"] == "acp:fake-runner"


@pytest.mark.asyncio
async def test_interactive_spawn_is_never_gated(monkeypatch, tmp_path):
    """Second floor: flag ON but the caller is interactive ⇒ proceeds.

    A human is present to see what launched, so the gate must not touch chat.
    """
    mgr, calls = _gate_fixture(monkeypatch, flag=True, verified=False, tmp_path=tmp_path)
    await mgr.get_or_create("chat:ei5-interactive", provider_kind="acp:fake-runner")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_verified_adapter_lets_an_unattended_spawn_proceed(monkeypatch, tmp_path):
    """Positive control: the gate is a REQUIREMENT, not a blanket "always refuse"."""
    mgr, calls = _gate_fixture(monkeypatch, flag=True, verified=True, tmp_path=tmp_path)
    await mgr.get_or_create(
        "unattended:ei5-verified", provider_kind="acp:fake-runner", unattended=True
    )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_uncataloged_runtime_fails_closed(monkeypatch, tmp_path):
    """An unknown runner cannot be verified, so with the flag on it is refused."""
    mgr, calls = _gate_fixture(monkeypatch, flag=True, verified=False, tmp_path=tmp_path)
    with pytest.raises(runners.UnverifiedAdapterError) as exc:
        await mgr.get_or_create(
            "unattended:ei5-unknown", provider_kind="acp:not-in-the-catalog", unattended=True
        )
    assert "no runner-catalog row" in str(exc.value)
    assert calls == []


# ── config round-trip: the two points test_config_roundtrip does not cover ────


def test_flag_is_in_the_editable_patch_allowlist():
    """Point 4 of the round-trip: without this a PATCH is rejected as unknown."""
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    spec = _EDITABLE_CONFIG.get("agent.unattended_requires_verified_adapter")
    assert spec == {"type": "bool"}


def test_flag_survives_a_file_round_trip(tmp_path, monkeypatch):
    """Points 2+3: the loader's explicit mapping reads it and to_dict writes it back.

    An omission in ``AppConfig.load()``'s explicit ``AgentConfig(...)`` mapping is a
    SILENT drop — the saved value reads back as the dataclass default — which is why
    this asserts the value survives the file, not just the dataclass.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    (home / "config.json").write_text(
        json.dumps({"agent": {"unattended_requires_verified_adapter": True}}), encoding="utf-8"
    )
    cfg = AppConfig.load()
    assert cfg.agent.unattended_requires_verified_adapter is True
    assert cfg.to_dict()["agent"]["unattended_requires_verified_adapter"] is True


def test_frontend_exposes_the_toggle():
    """Point 5: the field is user-facing, so it needs a control — this asserts the
    Settings → Agent defaults panel binds THIS field name, not a look-alike."""
    panel = (
        Path(__file__).resolve().parents[1]
        / "web"
        / "src"
        / "pages"
        / "settings"
        / "AgentDefaultsPanel.tsx"
    ).read_text(encoding="utf-8")
    assert 'field="unattended_requires_verified_adapter"' in panel
    assert "RunnersSection" in panel


# ── probe posture ─────────────────────────────────────────────────────────────


def test_probe_writes_only_the_sidecar(monkeypatch, tmp_path):
    """The health probe must not create workspace state — only its own sidecar.

    A spawned agent CLI's cwd escaping the configured home is a live hazard elsewhere;
    a probe that only asks for a version has no business writing anywhere, so this pins
    that the sole new file under the home is the runner sidecar.
    """
    bin_path = _write_exec(tmp_path / "fake-cli", "#!/bin/sh\necho 'fake-cli 1.0.0'\n")
    monkeypatch.setenv(_FAKE_ENV, str(bin_path))
    home = _home()
    before = {p for p in home.rglob("*") if p.is_file()}
    runners.probe_runner(_absent_runner())
    after = {p for p in home.rglob("*") if p.is_file()}
    new = {p.relative_to(home).as_posix() for p in after - before}
    assert new == {f"agent-metadata{os.sep}fake-runner.runner.json".replace(os.sep, "/")}
