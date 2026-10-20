"""The programs a test starts keep their files in the test's own folder, and the libraries it loads
are told what every ``personalclaw`` command tells them.

A sweep of this suite left the developer's home with black's cache, npm's logs, mise's lockfiles,
shell history and the code map's grammars in it: each child ran with the developer's own ``HOME``
and nothing told it otherwise, and the suite unset ``library_env`` in every test, so a library
loaded here behaved as PersonalClaw tells it not to. ``tests/tool_homes.py`` is the mechanism;
these hold it, with each program driven for real where it is installed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import tool_homes

from personalclaw.library_env import LIBRARY_ENV_NAMES, library_env


def _inside(path: str | Path, folder: str | Path) -> bool:
    real, root = os.path.realpath(path), os.path.realpath(folder)
    return real == root or real.startswith(root + os.sep)


def test_every_programs_setting_names_this_tests_own_folder(_isolate_real_home_writers):
    """Each program's setting names a folder inside this test's own (the one beside its home), and
    names it only: nothing is made until the program writes there."""
    root = _isolate_real_home_writers
    for name in tool_homes.PROGRAM_FOLDERS:
        value = os.environ.get(name, "")
        assert value, f"{name} is not set, so the program writes under the developer's HOME"
        assert _inside(value, root / "programs"), f"{name}={value} is not this test's own"
        assert not os.path.exists(value), f"{value} was made before anything wrote there"
    for name, value in tool_homes.PROGRAM_VALUES.items():
        assert os.environ.get(name) == value, name


def test_the_libraries_are_told_what_every_command_tells_them(request, _isolate_real_home_writers):
    """Every setting ``library_env`` names is set, for this test's own home; the code map's grammars
    are the run's, which it fetches once for every test, in the run's downloads folder."""
    told = library_env()
    assert _inside(
        told["TREE_SITTER_LANGUAGE_PACK_CACHE_DIR"], _isolate_real_home_writers / "home"
    ), "control: library_env answers for this test's own home"
    for name in LIBRARY_ENV_NAMES:
        assert name in os.environ, f"{name} is not set in a test, though every command sets it"
        if name not in tool_homes.GRAMMAR_SETTINGS:
            assert os.environ[name] == told[name], name
    grammars = tool_homes.downloads(request.config, tool_homes.GRAMMARS)
    for name in tool_homes.GRAMMAR_SETTINGS:
        assert _inside(os.environ[name].removeprefix("file://"), grammars), os.environ[name]


def test_the_downloads_are_kept_where_the_setting_says_else_in_pytests_cache(tmp_path, monkeypatch):
    """A folder the setting names holds the run's downloads, and is only named, since a run may only
    read it; without the setting they are in pytest's cache, kept between runs, or for a run without
    that cache in a folder of the run's own."""
    cached = SimpleNamespace(cache=SimpleNamespace(mkdir=lambda name: tmp_path / "cache" / name))
    uncached = SimpleNamespace(cache=None)
    monkeypatch.setenv(tool_homes.DOWNLOADS_SETTING, str(tmp_path / "filled"))

    for config in (cached, uncached):
        assert tool_homes.downloads(config, tool_homes.GRAMMARS) == (
            tmp_path / "filled" / tool_homes.GRAMMARS
        )
    assert not (tmp_path / "filled").exists(), "a folder the setting names was written"

    monkeypatch.delenv(tool_homes.DOWNLOADS_SETTING)
    assert tool_homes.downloads(cached, tool_homes.WEIGHT) == tmp_path / "cache" / tool_homes.WEIGHT
    assert tool_homes.downloads(uncached, tool_homes.WEIGHT) == tool_homes.BASE / tool_homes.WEIGHT


def test_a_child_a_test_starts_gets_them():
    names = [*tool_homes.PROGRAM_FOLDERS, *tool_homes.PROGRAM_VALUES, *LIBRARY_ENV_NAMES]
    code = f"import json, os; print(json.dumps({{n: os.environ.get(n) for n in {names!r}}}))"
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert json.loads(done.stdout) == {name: os.environ.get(name) for name in names}


def test_black_keeps_its_cache_in_the_tests_folder(tmp_path):
    """black, run the way a test runs it, keeps its cache where the test's setting says."""
    pytest.importorskip("black")
    source = tmp_path / "formatted.py"
    source.write_text("x  =  1\n", encoding="utf-8")

    subprocess.run([sys.executable, "-m", "black", "-q", str(source)], check=True, timeout=120)

    cache = Path(os.environ["BLACK_CACHE_DIR"])
    assert source.read_text(encoding="utf-8") == "x = 1\n", "control: black formatted the file"
    assert [p for p in cache.rglob("*") if p.is_file()], f"black kept no cache in {cache}"


@pytest.mark.skipif(shutil.which("npm") is None, reason="npm is not installed here")
def test_npm_keeps_its_cache_and_logs_in_the_tests_folder():
    """npm, asked where it keeps its cache (its debug logs live there), names the test's own
    folder, and it does not ask the registry whether a newer npm exists."""

    def npm(key: str) -> str:
        return subprocess.run(
            ["npm", "config", "get", key], capture_output=True, text=True, check=True, timeout=60
        ).stdout.strip()

    assert os.path.realpath(npm("cache")) == os.path.realpath(os.environ["npm_config_cache"])
    assert npm("update-notifier") == "false"


def test_collection_gets_a_folder_of_the_runs_own(tmp_path, monkeypatch):
    """What runs as a module is imported, before any test, is told the same, for a folder of the
    run's own; and when the run ends the environment is put back and that folder removed."""
    environ = {
        name: value
        for name, value in os.environ.items()
        if name not in {*tool_homes.PROGRAM_FOLDERS, *tool_homes.PROGRAM_VALUES, *LIBRARY_ENV_NAMES}
    }
    monkeypatch.setattr(os, "environ", environ)
    base, grammars = tmp_path / "run", tmp_path / "grammars"
    base.mkdir()

    undo = tool_homes.for_collection(base, grammars)

    for name in tool_homes.PROGRAM_FOLDERS:
        assert _inside(environ[name], base / "programs"), name
    for name in LIBRARY_ENV_NAMES:
        value = environ[name].removeprefix("file://")
        if name in tool_homes.GRAMMAR_SETTINGS:
            assert _inside(value, grammars), (name, value)
        elif value != "1":
            assert _inside(value, base / "home"), (name, value)
    assert "PERSONALCLAW_HOME" not in environ, "asking for the settings left a home named"

    undo()
    assert not base.exists()
    assert not any(name in environ for name in tool_homes.PROGRAM_FOLDERS)
