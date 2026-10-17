"""With no home chosen, every seam names the test's own home: the session map, the trigger
stores, the single-flight locks, and the answer to where the home is.

Each of these once had an autouse fixture of its own that pointed it at a separate per-test
folder, because each once reached the developer's real home: the session map rewrote the real
``session_map.json``, the boot migration and the Triggers page wrote the real ``triggers.json``,
and two workers contended on one real lock file. Every one of those seams is now a function that
asks the loader's ``config_dir()`` when it is called (``session_map.config_dir``,
``concurrency.config_dir``, ``triggers.boot_migrate.config_dir``,
``dashboard.handlers.triggers.config_dir``, ``gateway.config_dir``), so the one redirect in
``_isolate_real_home_writers`` already reaches them, and the separate fixtures were removed.

These are that redirect's controls for those seams: drop the loader patch from
``_isolate_real_home_writers``, or make its resolver answer the default home, and the tests here
fail. Each asks where its seam resolves BEFORE it writes anything, so a run without the redirect
fails on the path and never writes there.

The last test holds the condition the folder around each home relies on: the loader makes that
folder with the default mode, which is safe only while the run's base temporary folder is 0700
and this user's own.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def home(_isolate_real_home_writers, monkeypatch) -> Path:
    """The test's own home, as ``_isolate_real_home_writers`` names it, with no home chosen."""
    monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)
    return _isolate_real_home_writers / "home"


def test_the_session_map_is_kept_in_the_tests_home(home: Path) -> None:
    from personalclaw import session_map

    assert session_map.config_dir() == home
    mapping = session_map.SessionMap()
    mapping.set("dashboard:probe", "sid-1")
    assert (home / "session_map.json").is_file()


def test_the_single_flight_locks_are_kept_in_the_tests_home(home: Path) -> None:
    from personalclaw import concurrency

    assert concurrency.config_dir() == home
    path = concurrency.lock_path("consolidate:probe")
    assert path.parent == home / concurrency.LOCKS_DIR_NAME
    with concurrency.single_flight("consolidate:probe") as acquired:
        assert acquired is True
        assert path.is_file()


def test_the_boot_migration_reads_and_writes_the_tests_home(home: Path) -> None:
    from personalclaw.triggers import boot_migrate

    assert boot_migrate.config_dir() == home
    (home / "crons.json").write_text('{"jobs": []}', encoding="utf-8")
    report = boot_migrate.migrate_and_arm()
    assert isinstance(report, dict), report


def test_the_triggers_page_keeps_its_stores_in_the_tests_home(home: Path) -> None:
    from personalclaw.dashboard.handlers import triggers

    assert triggers.config_dir() == home
    store = triggers._trigger_store()
    assert store.base_dir == home
    store.save_all([])
    assert store.path.is_file()
    assert Path(triggers._runs_store()._dir).is_relative_to(home)


def test_the_gateway_builds_its_trigger_store_in_the_tests_home(home: Path) -> None:
    from personalclaw import gateway

    assert gateway.config_dir() == home


def test_where_the_home_is_names_the_tests_home_and_makes_nothing(home: Path) -> None:
    """``resolve_config_dir()`` is asked where the home is without making it (``config_path``,
    ``env_path`` and ``apps_dir`` are built from it), so it answers the test's home too, and the
    home is not made by the asking."""
    from personalclaw.config import loader

    assert loader.resolve_config_dir() == home
    assert loader.config_path().parent == home
    assert not home.exists()


def test_the_folder_around_each_home_is_private_to_this_user(tmp_path_factory) -> None:
    """The loader makes each test's ``pclaw-home-<n>`` folder as its home's parent, with the
    default mode, and only the home itself 0700. That is safe only while the folder holding them,
    the run's base temporary folder, is this user's own and open to no one else."""
    import os
    import stat

    base = tmp_path_factory.getbasetemp()
    info = base.stat()
    assert info.st_uid == os.getuid()
    assert stat.S_IMODE(info.st_mode) == 0o700, oct(stat.S_IMODE(info.st_mode))
