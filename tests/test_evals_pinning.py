"""EVALUATION-SUBSTRATE ES-2 — the versioned scenario library + the RunPin.

The two halves this atom has to prove, and how each is proved here:

* **The library moved and is versioned.** ``eval/scenarios/*.json`` no longer exists;
  the shipped set lives in ``evals/library/`` and INSTALLS into
  ``~/.personalclaw/evals/scenarios/`` through an idempotent, data-keyed backfill.
  Tests cover fresh install, a shipped-version bump, a locally edited scenario that
  must NOT be clobbered, and a user's own scenario surviving with ``origin: local``.
* **The pin is written by the real run path.** ``run_matrix`` is driven with the
  child-spawn boundary faked (no LLM, no real child) and the assertions are on what
  the RUN persisted: ``matrices/<id>/pin.json``, a per-cell ``pin.json`` carrying the
  cell's model override, and a ``results.tsv`` row whose pin columns match. A pin
  that cannot be completed makes the run refuse to start, and the ledger refuses the
  row — so a score can never be unattributable.

Every test runs against an isolated ``PERSONALCLAW_HOME`` (``tmp_path``): the real
home is never read or written.
"""

from __future__ import annotations

import json
import os
import types

import pytest

from personalclaw.evals import pinning
from personalclaw.evals import runner as runner_mod
from personalclaw.evals import scenarios as scenario_lib
from personalclaw.evals import store
from personalclaw.evals.child import render_result_line, resolve_scenario, seed_fixture_home
from personalclaw.evals.matrix import PASSED, MatrixSpec
from personalclaw.evals.runner import run_matrix
from personalclaw.evals.scenarios import ScenarioLibraryError

SCENARIO = {
    "name": "s",
    "version": 1,
    "fixture_home": "empty",
    "sessions": [{"name": "s1", "turns": [{"user": "hi"}]}],
}


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """An isolated home. Nothing in this module touches ``~/.personalclaw``."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


def _write_scenario(home, name, data):
    path = store.scenarios_dir() / f"{name}.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _pinnable(home, *, model="Acme:m1"):
    """Config + model binding + one scenario: the minimum a pinned run needs."""
    (home / "config.json").write_text(
        json.dumps({"providers": [{"name": "Acme"}]}), encoding="utf-8"
    )
    (home / "active_models.json").write_text(json.dumps({"chat": [model]}), encoding="utf-8")
    _write_scenario(home, "s", SCENARIO)
    return home


def _fake_spawn(monkeypatch, *, score=1.0):
    """Replace the child-spawn boundary with a canned PASSED result."""
    calls: list[dict] = []

    def _run(args, *, env, timeout, capture_output, text):
        calls.append({"args": list(args), "env": dict(env)})
        payload = {"ok": True, "passed": True, "score": score}
        return types.SimpleNamespace(returncode=0, stdout=render_result_line(payload), stderr="")

    monkeypatch.setattr(runner_mod.subprocess, "run", _run)
    return calls


# ── the packaged library actually moved (clean break, no old path) ─────────────


def test_old_packaged_scenarios_dir_is_gone():
    """ES-2 is a clean break: the pre-ES-2 read path must not exist to fall back to."""
    import personalclaw.eval as eval_pkg

    old = scenario_lib.Path(eval_pkg.__file__).resolve().parent / "scenarios"
    assert not old.exists()


def test_shipped_library_declares_version_and_fixture_home():
    shipped = sorted(scenario_lib.packaged_library_dir().glob("*.json"))
    assert shipped, "the wheel must ship the Loop-1 library"
    for path in shipped:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert int(data["version"]) >= 1
        # A fixture that doesn't ship would make the scenario unrunnable.
        assert scenario_lib.resolve_fixture_home(path) == data["fixture_home"]


# ── install_library: an idempotent backfill keyed on the data ─────────────────


def test_install_backfills_shipped_scenarios_into_the_home(home):
    manifest = scenario_lib.install_library()
    installed = sorted(p.name for p in store.scenarios_dir().glob("*.json"))
    shipped = sorted(p.name for p in scenario_lib.packaged_library_dir().glob("*.json"))
    assert installed == shipped
    assert set(manifest["scenarios"]) == {p.rsplit(".", 1)[0] for p in shipped}
    assert manifest["library_version"] == scenario_lib.LIBRARY_VERSION
    for entry in manifest["scenarios"].values():
        assert entry["origin"] == "shipped"
        assert len(entry["sha256"]) == 64


def test_install_is_idempotent(home):
    first = scenario_lib.install_library()
    smoke = store.scenarios_dir() / "smoke_test.json"
    before = smoke.read_bytes()
    assert scenario_lib.install_library() == first
    assert smoke.read_bytes() == before


def test_install_upgrades_a_lower_installed_version(home):
    scenario_lib.install_library()
    smoke = store.scenarios_dir() / "smoke_test.json"
    stale = json.loads(smoke.read_text(encoding="utf-8"))
    stale["version"] = 0
    stale["description"] = "stale local copy"
    smoke.write_text(json.dumps(stale), encoding="utf-8")

    manifest = scenario_lib.install_library()
    restored = json.loads(smoke.read_text(encoding="utf-8"))
    assert restored["version"] >= 1
    assert restored["description"] != "stale local copy"
    assert manifest["scenarios"]["smoke_test"]["version"] >= 1


def test_install_never_clobbers_an_equal_or_higher_local_version(home):
    scenario_lib.install_library()
    smoke = store.scenarios_dir() / "smoke_test.json"
    edited = json.loads(smoke.read_text(encoding="utf-8"))
    edited["version"] = 99
    edited["description"] = "my edit"
    smoke.write_text(json.dumps(edited), encoding="utf-8")

    manifest = scenario_lib.install_library()
    assert json.loads(smoke.read_text(encoding="utf-8"))["description"] == "my edit"
    assert manifest["scenarios"]["smoke_test"]["version"] == 99


def test_local_only_scenario_is_kept_and_marked_local(home):
    _write_scenario(home, "mine", {**SCENARIO, "name": "mine"})
    manifest = scenario_lib.install_library()
    assert manifest["scenarios"]["mine"]["origin"] == "local"
    assert (store.scenarios_dir() / "mine.json").is_file()


def test_manifest_lives_beside_the_scenarios_dir_not_inside_it(home):
    scenario_lib.install_library()
    assert scenario_lib.manifest_path() == home / "evals" / "scenario_library.json"
    # Every reader of the dir globs scenario files; a manifest among them would parse
    # as a broken scenario.
    assert scenario_lib.manifest_path().name not in {
        p.name for p in store.scenarios_dir().iterdir()
    }


# ── scenario identity ────────────────────────────────────────────────────────


def test_scenario_sha256_ignores_formatting_but_not_content(home):
    path = _write_scenario(home, "s", SCENARIO)
    baseline = scenario_lib.scenario_sha256(path)

    path.write_text(json.dumps(SCENARIO, indent=8, sort_keys=True) + "\n\n", encoding="utf-8")
    assert scenario_lib.scenario_sha256(path) == baseline

    changed = json.loads(json.dumps(SCENARIO))
    changed["sessions"][0]["turns"][0]["user"] = "hello"
    path.write_text(json.dumps(changed), encoding="utf-8")
    assert scenario_lib.scenario_sha256(path) != baseline


def test_resolution_prefers_a_path_then_the_installed_library(home, tmp_path):
    _write_scenario(home, "s", SCENARIO)
    assert scenario_lib.resolve_scenario_path("s").name == "s.json"

    adhoc = tmp_path / "adhoc.json"
    adhoc.write_text(json.dumps(SCENARIO), encoding="utf-8")
    assert scenario_lib.resolve_scenario_path(str(adhoc)) == adhoc

    with pytest.raises(ScenarioLibraryError):
        scenario_lib.resolve_scenario_path("nope")


def test_unknown_fixture_home_is_refused_before_a_run(home):
    path = _write_scenario(home, "bad", {**SCENARIO, "fixture_home": "no-such-fixture"})
    with pytest.raises(ScenarioLibraryError) as excinfo:
        scenario_lib.resolve_fixture_home(path)
    assert "no-such-fixture" in str(excinfo.value)


# ── the pin's four parts ─────────────────────────────────────────────────────


def test_compute_pin_fills_all_four_parts(home):
    _pinnable(home)
    pin = pinning.compute_pin("s")
    assert pin.scenario_id == "s"
    assert len(pin.scenario_sha256) == 64
    assert pin.model_fingerprint == {"chat": "Acme:m1"}
    assert len(pin.prompt_pack_sha256) == 64
    assert len(pin.config_snapshot_ref) == 64
    assert pin.fixture_home == "empty"
    assert pin.is_complete() and pin.missing_parts() == []


def test_pin_roundtrips_through_dict(home):
    _pinnable(home)
    pin = pinning.compute_pin("s")
    assert pinning.RunPin.from_dict(pin.to_dict()) == pin


def test_prompt_pack_hash_moves_when_a_prompt_is_edited(home):
    baseline = pinning.prompt_pack_sha256()
    prompts = home / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "chat.md").write_text("user-edited system prompt\n", encoding="utf-8")
    assert pinning.prompt_pack_sha256() != baseline


def test_config_snapshot_ref_moves_when_the_evals_config_changes(home):
    _pinnable(home)
    baseline = pinning.config_snapshot_ref()
    cfg = json.loads((home / "config.json").read_text(encoding="utf-8"))
    cfg["evals"] = {"enabled": True, "study_default_k": 9}
    (home / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    assert pinning.config_snapshot_ref() != baseline


def test_cell_override_repins_the_model_but_not_the_scenario(home):
    _pinnable(home)
    pin = pinning.compute_pin("s")
    overridden = pin.with_model_override("Acme:m2")
    assert overridden.model_fingerprint == {"chat": "Acme:m2"}
    assert overridden.scenario_sha256 == pin.scenario_sha256
    assert overridden.model_fp() != pin.model_fp()
    assert pin.with_model_override(None) is pin


# ── the REAL run path persists the pin (not merely defines it) ────────────────


def test_run_matrix_persists_the_pin_and_the_pinned_ledger_row(home, monkeypatch):
    _pinnable(home)
    calls = _fake_spawn(monkeypatch)
    result = run_matrix(MatrixSpec(subject="s", trial_count=1), matrix_id="m-1")
    assert result.aggregates["counts"][PASSED] == 1

    pin = pinning.matrix_pin("m-1")
    assert pin is not None and pin.is_complete()
    assert pin.scenario_id == "s" and pin.fixture_home == "empty"

    cell_pin = pinning.read_pin(store.matrix_dir("m-1") / "cell-0000")
    assert cell_pin is not None and cell_pin.scenario_sha256 == pin.scenario_sha256

    row = store.read_results()[0]
    assert row["study_id"] == "m-1"
    assert row["scenario_sha256"] == pin.scenario_sha256
    assert row["model_fp"] == pin.model_fp() != ""
    assert row["prompt_pack_sha256"] == pin.prompt_pack_sha256
    assert row["config_snapshot_ref"] == pin.config_snapshot_ref
    assert row["fixture_home"] == "empty"
    assert row["scenario_id"] == "s"
    assert calls, "the run must have reached the child-spawn boundary"


def test_rebinding_the_model_yields_a_new_fingerprint_for_the_same_scenario(home, monkeypatch):
    """The amendment's own acceptance sentence, driven end to end."""
    _pinnable(home, model="Acme:m1")
    _fake_spawn(monkeypatch)
    run_matrix(MatrixSpec(subject="s", trial_count=1), matrix_id="m-1")

    # The user rebinds the chat model in Settings → Models.
    (home / "active_models.json").write_text(json.dumps({"chat": ["Acme:m2"]}), encoding="utf-8")
    run_matrix(MatrixSpec(subject="s", trial_count=1), matrix_id="m-2")

    rows = store.read_results()
    assert len(rows) == 2
    assert rows[0]["scenario_sha256"] == rows[1]["scenario_sha256"]
    assert rows[0]["model_fp"] != rows[1]["model_fp"]

    diff = pinning.pin_diff()
    assert len(diff) == 1
    assert sorted(diff[0]["fingerprints"]) == sorted({rows[0]["model_fp"], rows[1]["model_fp"]})


def test_editing_the_scenario_yields_a_new_scenario_hash(home, monkeypatch):
    _pinnable(home)
    _fake_spawn(monkeypatch)
    run_matrix(MatrixSpec(subject="s", trial_count=1), matrix_id="m-1")

    edited = json.loads(json.dumps(SCENARIO))
    edited["sessions"][0]["turns"][0]["user"] = "different question"
    _write_scenario(home, "s", edited)
    run_matrix(MatrixSpec(subject="s", trial_count=1), matrix_id="m-2")

    rows = store.read_results()
    assert rows[0]["scenario_sha256"] != rows[1]["scenario_sha256"]
    assert len(pinning.pin_diff()) == 2


def test_model_axis_repins_each_cell(home, monkeypatch):
    _pinnable(home)
    _fake_spawn(monkeypatch)
    run_matrix(
        MatrixSpec(subject="s", axes={"model": ["Acme:m1", "Acme:m2"]}, trial_count=1),
        matrix_id="m-1",
    )
    fps = [
        pinning.read_pin(store.matrix_dir("m-1") / f"cell-{i:04d}").model_fingerprint
        for i in (0, 1)
    ]
    assert fps == [{"chat": "Acme:m1"}, {"chat": "Acme:m2"}]


def test_run_refuses_to_start_without_a_bound_model(home, monkeypatch):
    """No binding ⇒ no fingerprint ⇒ no pin ⇒ the run never burns a model call."""
    _pinnable(home)
    (home / "active_models.json").unlink()
    calls = _fake_spawn(monkeypatch)
    with pytest.raises(store.PinRequiredError) as excinfo:
        run_matrix(MatrixSpec(subject="s", trial_count=1), matrix_id="m-1")
    assert "model_fingerprint" in str(excinfo.value)
    assert calls == []
    assert store.read_results() == []


def test_run_refuses_an_unresolvable_scenario(home, monkeypatch):
    _pinnable(home)
    calls = _fake_spawn(monkeypatch)
    with pytest.raises(ScenarioLibraryError):
        run_matrix(MatrixSpec(subject="ghost", trial_count=1), matrix_id="m-1")
    assert calls == []


# ── the named seeded fixture home ────────────────────────────────────────────


def test_cell_gets_its_own_home_in_the_child_env_only(home, monkeypatch):
    _pinnable(home)
    parent_home_before = os.environ["PERSONALCLAW_HOME"]
    calls = _fake_spawn(monkeypatch)
    run_matrix(MatrixSpec(subject="s", trial_count=1), matrix_id="m-1")

    child_env = calls[0]["env"]
    assert child_env["PERSONALCLAW_HOME"] != parent_home_before
    assert child_env["PERSONALCLAW_HOME"].endswith("/home")
    assert child_env["PERSONALCLAW_WORKSPACE"].endswith("/workspace")
    # §1.3: the parent's env is untouched by the cell's overrides.
    assert os.environ["PERSONALCLAW_HOME"] == parent_home_before

    descriptor = json.loads(
        (store.matrix_dir("m-1") / "cell-0000" / "descriptor.json").read_text(encoding="utf-8")
    )
    assert descriptor["fixture_home"] == "empty"
    # The parent resolves the scenario; the child must not re-resolve a bare name
    # against its throwaway home.
    assert descriptor["scenario_path"].endswith("/evals/scenarios/s.json")
    assert descriptor["pin"]["scenario_sha256"] == pinning.matrix_pin("m-1").scenario_sha256


def test_child_seeds_the_named_fixture_into_its_home(tmp_path, monkeypatch):
    cell_home = tmp_path / "cell-home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(cell_home))
    seed_fixture_home("empty")
    assert (cell_home / "fixture.yaml").is_file()


def test_child_refuses_a_descriptor_without_a_resolved_scenario_path():
    with pytest.raises(FileNotFoundError):
        resolve_scenario({"subject": "s"})
    with pytest.raises(FileNotFoundError):
        resolve_scenario({"scenario_path": "/nonexistent/s.json"})


def test_child_loads_exactly_the_parent_resolved_file(home):
    path = _write_scenario(home, "s", SCENARIO)
    scenario = resolve_scenario({"scenario_path": str(path)})
    assert scenario.name == "s"


# ── the eval CLI reads the installed library, not the packaged one ────────────


class _Stop(RuntimeError):
    """Sentinel: stop ``_run_eval`` the moment it has resolved a scenario file."""


def test_eval_cli_resolves_scenarios_from_the_installed_library(home, monkeypatch):
    """``personalclaw eval`` runs the SAME files the matrix runner pins.

    Before ES-2 it read the packaged dir, so a user scenario was invisible to it and a
    library edit was invisible to the runner. The user's own ``mine.json`` resolving
    here is the proof that both now read one library.
    """
    import asyncio

    from personalclaw import cli_commands

    _write_scenario(home, "mine", {**SCENARIO, "name": "mine"})
    resolved: list = []

    def _load(path):
        resolved.append(path)
        raise _Stop()

    monkeypatch.setattr(cli_commands, "load_scenario", _load)
    args = types.SimpleNamespace(all_scenarios=False, scenarios=["mine"])
    with pytest.raises(_Stop):
        asyncio.run(cli_commands._run_eval(args))
    assert resolved == [store.scenarios_dir() / "mine.json"]


# ── the pin-diff query ───────────────────────────────────────────────────────


def test_pin_diff_groups_fingerprints_under_one_scenario_hash():
    rows = [
        {"scenario_id": "s", "scenario_sha256": "a" * 64, "model_fp": "fp1"},
        {"scenario_id": "s", "scenario_sha256": "a" * 64, "model_fp": "fp2"},
        {"scenario_id": "s", "scenario_sha256": "a" * 64, "model_fp": "fp1"},
        {"scenario_id": "t", "scenario_sha256": "b" * 64, "model_fp": "fp1"},
        {"scenario_id": "", "scenario_sha256": "", "model_fp": "fp9"},  # pre-pin junk row
    ]
    diff = pinning.pin_diff(rows)
    assert [e["scenario_sha256"] for e in diff] == ["a" * 64, "b" * 64]
    assert diff[0]["fingerprints"] == ["fp1", "fp2"]
    assert diff[1]["fingerprints"] == ["fp1"]
