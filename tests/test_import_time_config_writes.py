"""Importing a module must never WRITE the config — the leak no fixture can reach.

`tests/conftest.py::_isolate_real_home_writers` redirects the two home-resolution seams and
re-points every module-level binding of `config_dir` it can find in `sys.modules`. Its own
docstring names the shape it cannot cover: *"a home resolved into a module-level constant at
import time … If a new leak appears here, check for that shape first."*

This is that check, promoted from a docstring warning to an executable rail, after a **fourth**
instance of the shape reached the real home on CI:

    tests/test_aap9_project_stamping.py:27  import personalclaw.mcp_artifacts
      → mcp_artifacts.py:14                 from personalclaw.mcp_core import ...
        → mcp_core.py:111                   _API = _resolve_api_base()      # module level
          → mcp_core.py:106                 cfg = AppConfig.load()
            → config/loader.py:~5657        cfg.save()                      # migration write-back
              → atomic_write(config_path()) # the REAL ~/.personalclaw/config.json

Two things made that maximally hard to catch, and both are why the rail below is shaped the
way it is:

* **It ran during pytest COLLECTION**, in the `importtestmodule` path — before a single
  fixture exists. No amount of fixture work can intercept it.
* **`AppConfig.load()` is not a pure read.** It performs a migration write-back, so a module
  that merely *reads* config at import time can rewrite the user's file. `load()` also copies
  the old file aside with `shutil.copy2`, which **preserves mtime**, so the `.bak` looks older
  than the run and the real-home rail reports only ONE changed entry — hiding half the
  evidence.

It was invisible on developer machines for a third reason: the write only happens when
`config.json` exists AND needs migration. A developer's own config is already migrated, so
`needs_migration` is False and nothing is written. A fresh CI runner's is not.

Deliberately a SUBPROCESS test. The import must be the module's *first* in that interpreter or
there is nothing to observe (`sys.modules` makes a re-import a no-op), and the whole point is
that this must hold with no conftest fixture in play. That also makes it deterministic —
it does not depend on xdist worker assignment or on which test happened to import first,
which is exactly how the original defect hid.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

#: Modules whose import must not write the config. `mcp_core` is the one that regressed;
#: `mcp_artifacts` is the importer that dragged it in during collection, so it pins the real
#: entry path rather than only the module that happened to own the constant.
_IMPORT_MUST_NOT_WRITE = ("personalclaw.mcp_core", "personalclaw.mcp_artifacts")

#: A config.json that is genuinely PRE-migration, so `AppConfig.load()` has a reason to
#: write. `default_agent` missing from `agents` is one of the conditions `load()` repairs
#: (`loader.py`: "if not cfg.default_agent or cfg.default_agent not in cfg.agents").
#: A test seeded with an already-migrated config would pass while the defect was present.
_PRE_MIGRATION_CONFIG = {"default_agent": "a-name-no-agent-has", "agents": {}}


def _seed_home(tmp_path: Path) -> Path:
    home = tmp_path / "pclaw-home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps(_PRE_MIGRATION_CONFIG), encoding="utf-8")
    return home


def _run_snippet(home: Path, snippet: str) -> subprocess.CompletedProcess:
    """Run `snippet` in a fresh interpreter with PERSONALCLAW_HOME at `home`.

    `PERSONALCLAW_HOME` is how a subprocess is pointed at a home; `config_dir()` gives it
    precedence over `Path.home()`.
    """
    return subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        timeout=120,
        env={
            "PERSONALCLAW_HOME": str(home),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src"),
            "HOME": str(home.parent),
        },
    )


def _config_state(home: Path) -> tuple[int, str]:
    """(mtime_ns, contents) — mtime alone would miss a same-size rewrite, and contents alone
    would miss a byte-identical rewrite that still moved the file."""
    p = home / "config.json"
    return (p.stat().st_mtime_ns, p.read_text(encoding="utf-8"))


@pytest.mark.parametrize("module", _IMPORT_MUST_NOT_WRITE)
def test_importing_a_module_does_not_write_the_config(tmp_path: Path, module: str) -> None:
    """The rail. Importing `module` in a fresh interpreter must leave config.json untouched."""
    home = _seed_home(tmp_path)
    before = _config_state(home)
    proc = _run_snippet(home, f"import {module}")
    assert proc.returncode == 0, (
        f"importing {module} failed, so this test proved nothing about writes:\n"
        f"{proc.stderr[-3000:]}"
    )
    after = _config_state(home)
    assert after == before, (
        f"importing {module} REWROTE config.json (mtime and/or contents changed). A module "
        f"that resolves the home — or calls AppConfig.load(), which performs a migration "
        f"write-back — at import time writes the user's real config merely by being "
        f"imported, and does it during pytest COLLECTION where no fixture can intercept it. "
        f"Resolve it at CALL time instead (see mcp_core._api_base)."
    )


def test_the_probe_can_see_a_write(tmp_path: Path) -> None:
    """VACUITY LEG — the rail above is a same/same comparison, so it would pass if the probe
    were blind (wrong home, unwritable seed, a subprocess that silently no-ops).

    This drives the exact write the rail forbids and requires it to be OBSERVED. If this
    fails, the parametrized rail above is not evidence of anything.

    RE-POINTED by PHF-15. It used to drive `AppConfig.load()`, which was the mechanism the
    original defect used — but `load()` is now a pure read, so driving it here would assert
    that a write happens which no longer does, and this leg would fail for the very reason the
    fix is correct. It now drives `load_and_persist_migrations()`, the explicit entry point
    that PHF-15 made the ONLY writer. The leg's purpose is unchanged: a real write, observed.
    """
    home = _seed_home(tmp_path)
    before = _config_state(home)
    proc = _run_snippet(
        home,
        "from personalclaw.config.migrations import load_and_persist_migrations; "
        "load_and_persist_migrations()",
    )
    assert proc.returncode == 0, f"the positive control itself failed:\n{proc.stderr[-3000:]}"
    after = _config_state(home)
    assert after != before, (
        "load_and_persist_migrations() on a PRE-MIGRATION config did NOT rewrite "
        "config.json, so this probe cannot observe the write it is meant to detect and the "
        "rail above is vacuous. Either the seed is no longer pre-migration (check the "
        "conditions the migration repairs) or the subprocess is not resolving "
        "PERSONALCLAW_HOME."
    )


def test_the_module_level_constant_stays_retired() -> None:
    """A cheap structural ratchet beside the behavioural rail: the specific constant that
    regressed must not come back. The subprocess tests catch ANY import-time writer; this one
    names the shape, so a reviewer reintroducing `_API = ...` sees why it is refused."""
    body = (
        Path(__file__).resolve().parent.parent / "src" / "personalclaw" / "mcp_core.py"
    ).read_text(encoding="utf-8")
    assert body.strip(), "mcp_core.py is empty — this scan would pass by being blind"
    assert (
        "def _api_base(" in body
    ), "mcp_core._api_base is gone — re-anchor this rail rather than deleting it"
    offenders = [
        line
        for line in body.splitlines()
        if line.startswith("_API") and "=" in line and "def " not in line
    ]
    assert not offenders, (
        f"mcp_core re-introduced a module-level API constant: {offenders}. Resolving the API "
        f"base at import time calls AppConfig.load() at import time, which writes the real "
        f"config during pytest collection. Call _api_base() at the use site instead."
    )
