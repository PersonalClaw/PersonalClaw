"""``AppConfig.load()`` must be a PURE READ, and the migration must still run at boot.

The defect. ``load()`` used to back ``config.json`` aside with ``shutil.copy2`` and call
``cfg.save()`` inline whenever the parsed config needed migrating. So *any* caller that
merely read config rewrote the user's file and dropped a ``.bak`` beside it — including a
module that resolves config into a module-level constant at import time, which on this
repo's CI happened during pytest **collection**::

    import personalclaw.mcp_artifacts
      → mcp_core.py:111   _API = _resolve_api_base()   # module level
        → mcp_core.py:106 cfg = AppConfig.load()
          → the migration write-back → the REAL ~/.personalclaw/config.json

Three things made it maximally hard to catch, and each one shapes a test below.

* **No fixture can reach it.** It ran in the ``importtestmodule`` path, before a single
  fixture exists. So the purity rail is a SUBPROCESS test: the import must be that
  interpreter's first (``sys.modules`` makes a re-import a no-op) and the property must
  hold with no conftest in play. That also makes it deterministic instead of dependent on
  which xdist worker imported what first — which is exactly how it hid.
* **It is invisible on a developer machine.** The write only fires when ``config.json``
  exists AND is pre-migration; a developer's own config is already migrated, so
  ``needs_migration`` is False and nothing is written however broken the code is. Every
  test here therefore seeds a genuinely pre-migration config. One that seeded an
  already-migrated config would have passed throughout the defect's life.
* **It under-reported.** ``copy2`` preserves the source mtime, so the ``.bak`` looked
  older than the run and the real-home rail named only ONE of the two changed entries.
  ``tests/test_real_home_guard.py`` owns that half.

The pairing is the point. "``load()`` writes nothing" is trivially satisfiable by deleting
the migration, which would silently strand every existing config. So the purity rails here
are worth nothing without :func:`test_the_gateway_boot_path_persists_the_migration` and
:func:`test_load_still_applies_the_migration_in_memory` beside them. Ship both or neither.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from personalclaw.config.loader import AppConfig, config_path
from personalclaw.config.migrations import apply_config_migrations, load_and_persist_migrations

#: A config.json that is genuinely PRE-migration, so the migration has a reason to write.
#: ``default_agent`` naming an agent that does not exist is one of the conditions the
#: migration repairs ("if not cfg.default_agent or cfg.default_agent not in cfg.agents"),
#: and an empty ``agents`` map is another (the built-in profiles get seeded).
_PRE_MIGRATION_CONFIG = {"default_agent": "a-name-no-agent-has", "agents": {}}

#: Modules whose *import* must not write the config. ``mcp_core`` is the one that regressed
#: — it still resolves its API base into a module-level constant, which calls
#: ``AppConfig.load()`` at import time. That is fine now precisely because ``load()`` is
#: pure; this rail is what keeps it fine. ``mcp_artifacts`` is the importer that dragged it
#: in during collection, so it pins the real entry path rather than only the owning module.
_IMPORT_MUST_NOT_WRITE = ("personalclaw.mcp_core", "personalclaw.mcp_artifacts")


def _seed_pre_migration_home(root: Path) -> Path:
    """A home holding a pre-migration ``config.json``. Returns the home."""
    home = root / "pclaw-home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps(_PRE_MIGRATION_CONFIG), encoding="utf-8")
    return home


def _config_state(home: Path) -> tuple[int, str]:
    """``(mtime_ns, contents)``.

    mtime alone would miss a same-mtime rewrite (``copy2``/``copystat`` back-date it) and
    contents alone would miss a byte-identical rewrite that still replaced the file.
    """
    p = home / "config.json"
    return (p.stat().st_mtime_ns, p.read_text(encoding="utf-8"))


def _run_snippet(home: Path, snippet: str) -> subprocess.CompletedProcess[str]:
    """Run ``snippet`` in a FRESH interpreter with ``PERSONALCLAW_HOME`` at ``home``.

    ``PERSONALCLAW_HOME`` is how a subprocess is pointed at a home — ``config_dir()`` gives
    it precedence over ``Path.home()``. ``HOME`` is repointed too so that anything reaching
    for ``Path.home()`` directly lands in the throwaway tree rather than the developer's.
    """
    return subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        timeout=180,
        env={
            "PERSONALCLAW_HOME": str(home),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src"),
            "HOME": str(home.parent),
        },
    )


# --------------------------------------------------------------------------------------
# The purity rails — fresh interpreter, no fixture in play.
# --------------------------------------------------------------------------------------


def test_load_writes_nothing_on_a_pre_migration_config(tmp_path: Path) -> None:
    """THE RAIL. ``AppConfig.load()`` on a pre-migration config must leave the file byte-
    and mtime-identical, and must not drop a ``.bak`` beside it."""
    home = _seed_pre_migration_home(tmp_path)
    before = _config_state(home)

    proc = _run_snippet(
        home,
        "from personalclaw.config.loader import AppConfig\n" "cfg = AppConfig.load()\n"
        # Prove the load actually did the work whose write-back we are forbidding: if the
        # migration had been deleted rather than made pure, this would fail here instead of
        # passing the assertions below for the wrong reason.
        "assert cfg.default_agent in cfg.agents, cfg.default_agent\n",
    )
    assert proc.returncode == 0, (
        "AppConfig.load() failed, so this test proved nothing about writes:\n"
        f"{proc.stderr[-3000:]}"
    )

    assert _config_state(home) == before, (
        "AppConfig.load() REWROTE config.json (mtime and/or contents changed). load() is a "
        "pure read: it applies pending migrations in memory only. The persisting "
        "counterpart is config.migrations.load_and_persist_migrations(), called from the "
        "gateway boot path — a library reader must never reach it."
    )
    assert not (home / "config.json.bak").exists(), (
        "AppConfig.load() created config.json.bak. The migration backup belongs to the "
        "explicit write-back entry point, not to a read. Note that copy2 preserves the "
        "source mtime, so this .bak is invisible to an mtime-only real-home check — which "
        "is why it is asserted by name here."
    )


@pytest.mark.parametrize("module", _IMPORT_MUST_NOT_WRITE)
def test_importing_a_module_that_reads_config_writes_nothing(tmp_path: Path, module: str) -> None:
    """The delivery mechanism the defect actually used: a module-level ``AppConfig.load()``.

    Kept as a rail over the real modules rather than a synthetic one, because the value is
    in pinning the *entry path* that reached CI. Any module may read config at import time;
    what must never happen is that reading it writes.
    """
    home = _seed_pre_migration_home(tmp_path)
    before = _config_state(home)

    proc = _run_snippet(home, f"import {module}")
    assert proc.returncode == 0, (
        f"importing {module} failed, so this test proved nothing about writes:\n"
        f"{proc.stderr[-3000:]}"
    )

    assert _config_state(home) == before, (
        f"importing {module} REWROTE config.json. A module that resolves config into a "
        f"module-level constant writes the user's real config merely by being imported, "
        f"and does it during pytest COLLECTION where no fixture can intercept it."
    )
    assert not (home / "config.json.bak").exists(), f"importing {module} created a .bak"


def test_the_explicit_entry_point_can_be_seen_to_write(tmp_path: Path) -> None:
    """VACUITY LEG for both rails above.

    They are same/same comparisons, so they would pass if the probe were blind — wrong
    home, unwritable seed, a subprocess that silently no-ops, or a seed that stopped being
    pre-migration. This drives the exact write they forbid, through the migration's new
    explicit entry point, in the same subprocess shape, and requires it to be OBSERVED.

    If this fails, nothing above is evidence of anything.
    """
    home = _seed_pre_migration_home(tmp_path)
    before = _config_state(home)

    proc = _run_snippet(
        home,
        "from personalclaw.config.migrations import load_and_persist_migrations\n"
        "load_and_persist_migrations()\n",
    )
    assert proc.returncode == 0, f"the positive control itself failed:\n{proc.stderr[-3000:]}"

    assert _config_state(home) != before, (
        "load_and_persist_migrations() on a PRE-MIGRATION config did NOT rewrite "
        "config.json, so this probe cannot observe the write it exists to observe and the "
        "purity rails above are vacuous. Either the seed is no longer pre-migration (check "
        "the conditions apply_config_migrations repairs) or the subprocess is not resolving "
        "PERSONALCLAW_HOME."
    )
    assert (
        home / "config.json.bak"
    ).exists(), "the explicit write-back did not back the original aside"


# --------------------------------------------------------------------------------------
# The migration must still run — the half that makes purity mean something.
# --------------------------------------------------------------------------------------


def test_load_still_applies_the_migration_in_memory(monkeypatch, tmp_path: Path) -> None:
    """Purity must not have been bought by deleting the migration.

    ``load()`` returns a config of the CURRENT shape even though it wrote nothing, so every
    existing reader keeps seeing a repaired config. Proven against a real pre-migration
    seed, not against defaults.
    """
    home = _seed_pre_migration_home(tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    assert config_path() == home / "config.json", "the redirect did not take"

    cfg = AppConfig.load()

    assert cfg.agents, "load() returned a config with no agents — the migration is gone"
    assert cfg.default_agent in cfg.agents, (
        "load() left default_agent pointing at a nonexistent agent. It must still repair "
        "the parsed config in memory; only the WRITE moved out."
    )


def test_load_does_not_call_save_even_when_a_migration_applies(monkeypatch, tmp_path: Path) -> None:
    """Behavioural, not textual: make ``save()`` fatal and require ``load()`` to succeed.

    A text scan for ``cfg.save()`` inside ``load()`` reads comments and docstrings too, and
    would pass the moment someone renamed the call. Detonating the writer cannot be fooled.
    """
    home = _seed_pre_migration_home(tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))

    def _explode(self: AppConfig) -> None:
        raise AssertionError("AppConfig.load() called save() — it must be a pure read")

    monkeypatch.setattr(AppConfig, "save", _explode)

    cfg = AppConfig.load()  # must not raise
    assert cfg.default_agent in cfg.agents

    # And the control: save() really is fatal, so the pass above is not because the patch
    # missed its target.
    with pytest.raises(AssertionError, match="pure read"):
        AppConfig().save()


def test_the_gateway_boot_path_persists_the_migration(monkeypatch, tmp_path: Path) -> None:
    """A pre-migration config IS migrated on disk when the gateway boots.

    Drives the real startup seam — ``cli_server._boot_config()``, the only caller of the
    writing entry point — with the home pointed at ``tmp_path``. The original is preserved
    as ``config.json.bak`` so a bad migration is recoverable.
    """
    from personalclaw.cli_server import _boot_config

    home = _seed_pre_migration_home(tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    original = (home / "config.json").read_text(encoding="utf-8")

    cfg = _boot_config()

    assert cfg.default_agent in cfg.agents
    on_disk = json.loads((home / "config.json").read_text(encoding="utf-8"))
    assert on_disk["default_agent"] in on_disk["agents"], (
        "the gateway booted and left a pre-migration config.json on disk. The migration is "
        "load-bearing for real upgrades; making load() pure must not have retired it."
    )
    backup = home / "config.json.bak"
    assert backup.exists(), "boot migrated the config without backing the original aside"
    assert backup.read_text(encoding="utf-8") == original, (
        "config.json.bak does not hold the pre-migration bytes, so it is not a recovery "
        "point — the copy must happen BEFORE the save."
    )


def test_a_second_boot_writes_nothing(monkeypatch, tmp_path: Path) -> None:
    """One-shot. An already-current config must not be rewritten on every restart, or the
    ``.bak`` becomes a copy of itself and the real-home rail reds on every gateway start."""
    home = _seed_pre_migration_home(tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    from personalclaw.cli_server import _boot_config

    _boot_config()
    (home / "config.json.bak").unlink()
    settled = _config_state(home)

    _boot_config()

    assert _config_state(home) == settled, "a second boot rewrote an already-migrated config"
    assert not (
        home / "config.json.bak"
    ).exists(), "a second boot re-created the backup, so the migration is not one-shot"


def test_apply_config_migrations_is_idempotent_and_reports_no_change(tmp_path: Path) -> None:
    """The flag ``load_and_persist_migrations`` gates the write on. A second application on
    the same object must report False — that is what makes the write one-shot."""
    cfg = AppConfig()
    assert apply_config_migrations(cfg) is True, "a bare default needs the built-ins seeded"
    assert (
        apply_config_migrations(cfg) is False
    ), "re-applying reported a change, so every boot would rewrite the config"


def test_a_corrupt_config_is_never_rewritten_by_boot(monkeypatch, tmp_path: Path) -> None:
    """Unparseable config.json is left exactly as the user left it — no migration, no
    ``.bak``, no overwrite. Rewriting it would destroy the only copy of their settings."""
    home = tmp_path / "pclaw-home"
    home.mkdir()
    (home / "config.json").write_text("{not json at all", encoding="utf-8")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    before = _config_state(home)

    cfg = load_and_persist_migrations()

    assert isinstance(cfg, AppConfig), "a corrupt config must still yield usable defaults"
    assert _config_state(home) == before, "boot overwrote an unparseable config.json"
    assert not (home / "config.json.bak").exists()
