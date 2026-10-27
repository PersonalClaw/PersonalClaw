"""The browser run's gateway runs no update check of its own, so its log has no line of one.

The run's gateway starts on the home ``web/e2e/fixtures/home/`` seeds, and is a source checkout.
Its first update check is the one it makes when it starts (``update_status(asked=False,
at_start=True)``), which on a checkout is a ``git fetch`` of its origin. On #5618's run that
fetch timed out, its warning landed in Settings › Diagnostics' log panel, and the panel's pinned
tree read it in place of "Waiting for log entries…". Under the seed's home the same call starts no
process, asks GitHub for nothing and logs nothing. Under a home without the seed's setting it runs
the fetch: the control. That the gateway starts with this call is the seed rail's to hold
(``tests/rails/test_e2e_home_seed_matches_the_boot_tree.py``).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

import pytest

from personalclaw import self_update
from personalclaw.dashboard.handlers import updates

SEED = Path(__file__).resolve().parents[1] / "web" / "e2e" / "fixtures" / "home"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home of the test's own, holding the seed's settings, as the browser run's does."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    (tmp_path / "workspace").mkdir()
    shutil.copy(SEED / "config.json", tmp_path / "config.json")
    from personalclaw.config.loader import config_dir as _resolved

    assert _resolved() == tmp_path, "the home redirect did not take: refusing to touch a real home"
    return tmp_path


@pytest.fixture()
def spied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """The check's two ways out, recorded and never taken: a process (the ``git fetch``) and the
    releases list (``build_update_status(fetch=True)``)."""
    calls: dict[str, list[Any]] = {"processes": [], "fetch": []}

    async def no_process(*argv: Any, **_kw: Any) -> Any:
        calls["processes"].append(argv)
        raise OSError("this test starts no process")

    async def status(_current: str, *, fetch: bool) -> dict[str, object]:
        calls["fetch"].append(fetch)
        return {}

    monkeypatch.setattr(updates.asyncio, "create_subprocess_exec", no_process)
    monkeypatch.setattr(self_update, "build_update_status", status)
    # The run's gateway is a checkout, and its schedule has never run.
    monkeypatch.setattr(self_update, "detect_install_kind", lambda: "git")
    monkeypatch.setattr(self_update, "source_checkout", lambda: str(tmp_path))
    monkeypatch.setattr(updates, "_last_update_check", 0.0)
    return calls


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == updates.logger.name and r.levelno >= logging.WARNING
    ]


def test_the_runs_first_update_check_starts_nothing_and_logs_nothing(
    home: Path, spied: dict[str, list[Any]], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=updates.logger.name)
    status = asyncio.run(updates.update_status(asked=False, at_start=True))
    assert spied["processes"] == []
    assert spied["fetch"] == [False]
    assert status["check_enabled"] is False and "checked_now" not in status
    assert _warnings(caplog) == []


def test_without_the_seeds_setting_the_same_check_runs_git_fetch(
    home: Path, spied: dict[str, list[Any]], caplog: pytest.LogCaptureFixture
) -> None:
    (home / "config.json").write_text('{"dashboard":{"user_name":"Keyur"}}', encoding="utf-8")
    asyncio.run(updates.update_status(asked=False, at_start=True))
    assert [a for a in spied["processes"] if "fetch" in a], spied["processes"]
    assert spied["fetch"] == [True]
