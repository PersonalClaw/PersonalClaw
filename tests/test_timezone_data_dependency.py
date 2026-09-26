"""The base install carries the IANA data that ``zoneinfo`` needs on minimal Linux."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

import pytest
from packaging.requirements import Requirement

from personalclaw import timezones as tzmod
from personalclaw.timezones import TimeZoneDatabaseUnavailable, UnknownTimeZone, zone_or_raise

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_tzdata_is_an_unconditional_runtime_dependency() -> None:
    """A transitive or platform-marked dependency does not protect a minimal Linux install."""
    with (_REPO_ROOT / "pyproject.toml").open("rb") as handle:
        declared = tomllib.load(handle)["project"]["dependencies"]
    matches = [Requirement(spec) for spec in declared if Requirement(spec).name == "tzdata"]
    assert len(matches) == 1
    assert matches[0].marker is None, "tzdata must install on minimal Linux, not only one platform"
    assert str(matches[0].specifier) == ">=2024.1"


def test_named_zone_uses_packaged_data_when_system_paths_are_unavailable(tmp_path) -> None:
    """A fresh interpreter with an empty TZPATH must resolve data from the wheel dependency.

    The invalid-zone negative control matters: a test that only loads one known name could
    pass through a cache or an accidental fallback while the authoring gate classified every
    failure as a missing database.

    The child gets its own `PERSONALCLAW_HOME`: this suite's home isolation patches the
    current process, and a subprocess is outside it.
    """
    script = """
import importlib.metadata
import zoneinfo

from personalclaw.timezones import UnknownTimeZone, zone_or_raise

assert zoneinfo.TZPATH == ()
assert importlib.metadata.version("tzdata")
zone = zone_or_raise("America/Los_Angeles")
assert zone.key == "America/Los_Angeles"
try:
    zone_or_raise("Invalid/Timezone")
except UnknownTimeZone:
    pass
else:
    raise AssertionError("a truly invalid zone was accepted")
"""
    env = os.environ.copy()
    env["PYTHONTZPATH"] = ""
    env["PERSONALCLAW_HOME"] = str(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"


def test_missing_database_is_not_reported_as_invalid_user_input(monkeypatch) -> None:
    """When no key can be checked, the only honest verdict is capability unavailable."""

    def unavailable(key: str):
        raise ZoneInfoNotFoundError(f"No time zone found with key {key}")

    monkeypatch.setattr(tzmod, "ZoneInfo", unavailable)
    with pytest.raises(TimeZoneDatabaseUnavailable) as exc:
        zone_or_raise("America/Los_Angeles", where="config.timezone")
    assert not isinstance(exc.value, UnknownTimeZone)
    assert "database is unavailable" in str(exc.value)
