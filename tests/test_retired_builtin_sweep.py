"""A retired built-in app does not linger as an undeletable broken card (issues 334, 368).

Measured on a home upgraded across six `origin/main` SHAs. The ScheduleService retirement deleted
`create_schedule_provider` from core and stopped bundling `personalclaw-schedule-tools`, but nothing
removed it from an existing install, so:

    every boot   ERROR Failed to enable extension personalclaw-schedule-tools
                 AttributeError: module 'personalclaw.tool_providers.registry' has no attribute
                 'create_schedule_provider'
    GET /api/apps    enabled: true, origin: "builtin", native: true — no error, no status
    Apps → Native    renders beside 28 working built-ins with an "Installed" badge
    POST …/disable   400 disable failed
    DELETE …         404 app not installed        ← while the list says it IS installed

Three states disagreed: the source was deleted, the install survived, and the seed marker still
claimed the name. `seed_builtin_apps()` seeds FORWARD over what the wheel ships and had no reverse
pass, so the orphan was invisible to it. The only escape was hand-editing the home.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.apps import app_manager as AM

RETIRED = "personalclaw-schedule-tools"
DECORED = "ollama-models"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """Every write in this file lands under tmp_path. Both bindings are patched, and the redirect
    is asserted, because a destructive sweep that escaped to the real home would delete apps."""
    import personalclaw.apps.manager as manager
    import personalclaw.config.loader as cfg

    # BOTH bindings: `app_dir`/`apps_dir` live in `apps.manager` and read ITS `config_dir`, while
    # the loader's copy is what everything else resolves through.
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    assert AM.app_dir("probe").is_relative_to(tmp_path), "the app dir must be redirected"
    return tmp_path


def _install(name: str, *, implementation: str = "", native: bool = True, data: str = "") -> Path:
    """An installed app on disk, the way seeding leaves one: origin=builtin + a native manifest."""
    root = AM.app_dir(name)
    (root / AM._APP_DATA_DIRNAME).mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "name": name,
        "version": "1.0.0",
        "displayName": name,
        "description": "d",
        "native": native,
    }
    if implementation:
        manifest["provider"] = {"providerType": "tool", "implementation": implementation}
    (root / AM.APP_MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
    AM._write_installed(
        name,
        AM.InstalledApp(
            name=name,
            version="1.0.0",
            displayName=name,
            enabled=True,
            installedAt=AM._now_iso(),
            updatedAt=AM._now_iso(),
            source="builtin",
            origin="builtin",
        ),
    )
    if data:
        (root / AM._APP_DATA_DIRNAME / "config.json").write_text(data, encoding="utf-8")
    return root


# ── the dead built-in: its core factory is gone ────────────────────────────────────────────


def test_a_dead_builtin_is_removed_from_disk():
    # The reported state: the app's provider names a core factory that no longer exists.
    root = _install(
        RETIRED, implementation="personalclaw.tool_providers.registry:create_schedule_provider"
    )
    assert root.exists()
    AM.retire_orphaned_builtins({RETIRED}, set())
    assert not root.exists(), "a retired builtin must not survive the next boot"


def test_the_name_leaves_the_seed_marker():
    # Otherwise the next seed still believes it owns an app whose source is gone.
    _install(
        RETIRED, implementation="personalclaw.tool_providers.registry:create_schedule_provider"
    )
    seeded = {RETIRED, "keep-me"}
    # `keep-me` is still SHIPPED, so it is not an orphan — that is what `present` means.
    AM.retire_orphaned_builtins(seeded, {"keep-me"})
    assert seeded == {"keep-me"}
    assert RETIRED not in AM._read_seed_marker()


def test_a_dead_builtin_holding_user_DATA_is_unlocked_rather_than_deleted():
    # A retirement must not destroy something the user may want. Unlocked + disabled instead, which
    # is removable in one click — the issue's own stated minimum.
    root = _install(
        RETIRED,
        implementation="personalclaw.tool_providers.registry:create_schedule_provider",
        data='{"mine": true}',
    )
    AM.retire_orphaned_builtins({RETIRED}, set())
    assert root.exists(), "user data must survive"
    meta = AM._read_installed(RETIRED)
    assert meta is not None and meta.origin == "local" and meta.enabled is False


def test_a_dead_builtin_stops_being_locked_native():
    # `_is_native()` reads `origin`, and while it says "builtin" the user can neither disable nor
    # uninstall — the exact trap that made the card undeletable.
    _install(
        RETIRED,
        implementation="personalclaw.tool_providers.registry:create_schedule_provider",
        data='{"mine": true}',
    )
    assert AM._is_native(RETIRED) is True
    AM.retire_orphaned_builtins({RETIRED}, set())
    assert AM._is_native(RETIRED) is False


# ── the de-cored app: source unbundled, implementation still its own ────────────────────────


def test_a_decored_app_is_unlocked_but_left_running():
    # `ollama-models`' history, now the general rule rather than a hardcoded one-shot: the packaged
    # source is gone but the app still works, so it keeps running as an ordinary local app.
    root = _install(DECORED, implementation="ollama_models.provider:create")
    AM.retire_orphaned_builtins({DECORED}, set())
    assert root.exists(), "a working app must not be deleted"
    meta = AM._read_installed(DECORED)
    assert meta is not None and meta.origin == "local" and meta.enabled is True


def test_a_manifest_with_no_provider_at_all_is_treated_as_decored():
    # Nothing to prove dead, so take the gentler branch.
    root = _install("some-retired-ui-app")
    AM.retire_orphaned_builtins({"some-retired-ui-app"}, set())
    assert root.exists()
    assert AM._read_installed("some-retired-ui-app").origin == "local"


def test_a_core_factory_that_still_EXISTS_is_not_dead():
    # The discriminator must be the code, not the marker. `_audit` lives in app_manager and is real.
    _install("still-alive", implementation="personalclaw.apps.app_manager:seed_builtin_apps")
    assert AM._core_factory_is_gone("still-alive") is False
    root = AM.app_dir("still-alive")
    AM.retire_orphaned_builtins({"still-alive"}, set())
    assert root.exists(), "an app whose factory resolves is de-cored, not dead"


def test_a_missing_core_module_counts_as_gone():
    _install("ghost", implementation="personalclaw.definitely_not_a_module:make")
    assert AM._core_factory_is_gone("ghost") is True


# ── what must NOT happen ───────────────────────────────────────────────────────────────────


def test_an_app_the_wheel_STILL_ships_is_untouched():
    # Vacuity guard, and the one that matters most: the sweep sees every seeded name, so a bug here
    # would retire working built-ins.
    root = _install(
        "personalclaw-task-tools", implementation="personalclaw.apps.app_manager:app_dir"
    )
    seeded = {"personalclaw-task-tools"}
    assert AM.retire_orphaned_builtins(seeded, {"personalclaw-task-tools"}) == []
    assert root.exists() and seeded == {"personalclaw-task-tools"}
    assert AM._read_installed("personalclaw-task-tools").origin == "builtin"


def test_it_is_idempotent():
    _install(
        RETIRED, implementation="personalclaw.tool_providers.registry:create_schedule_provider"
    )
    seeded = {RETIRED}
    assert AM.retire_orphaned_builtins(seeded, set()) == [RETIRED]
    assert AM.retire_orphaned_builtins(seeded, set()) == []


def test_a_seeded_name_with_no_install_left_still_leaves_the_marker():
    # A half-cleaned home (directory gone by hand, marker intact) must converge too.
    seeded = {RETIRED}
    assert AM.retire_orphaned_builtins(seeded, set()) == [RETIRED]
    assert seeded == set()


# ── the wiring ─────────────────────────────────────────────────────────────────────────────


def test_seeding_runs_the_reverse_sweep_and_keeps_no_second_mechanism():
    import inspect

    src = inspect.getsource(AM.seed_builtin_apps)
    assert "retire_orphaned_builtins(seeded, _bundled_native_names())" in src
    whole = inspect.getsource(AM)
    # The one-shot is a special case of the sweep: deleted, not kept beside it.
    assert "_OLLAMA_MIGRATION_NAME" not in whole
