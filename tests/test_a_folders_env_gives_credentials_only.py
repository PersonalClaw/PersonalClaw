"""A ``.env`` in the folder a command starts in gives that command credentials only, and says so.

Every ``personalclaw`` command reads the ``.env`` of the folder it starts in, before the home's,
so a key kept beside a project reaches the command. Such a folder may be anyone's, a repository
just cloned among them. So from it the command takes only credentials, by their names
(``…_API_KEY``, ``…_TOKEN``, ``…_SECRET``…), and never a PersonalClaw setting
(``PERSONALCLAW_*``: the sign-in mode, the bind address, the home, the project folder), nor a
name that routes or trusts traffic (a proxy, a CA bundle, a provider's base URL). It names on
stderr each name it took, and each it left out. The home's ``.env`` is read as it always was.

Driven through the real entry point (``cli.main``) in a child process that starts in such a
folder, with a home of its own, then reads what its environment came to and the sign-in mode a
gateway started from it would run in (``AuthConfig.from_env``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.security

#: What the child runs: the real entry point as `personalclaw --version` (which answers after the
#: `.env` files are read), then a report of the environment it came to.
_CHILD = """
import json, os, sys
from personalclaw import cli
sys.argv = ["personalclaw", "--version"]
try:
    cli.main()
except SystemExit:
    pass
from personalclaw.auth.modes import AuthConfig
from personalclaw.config.loader import resolve_config_dir
print("REPORT " + json.dumps({
    "home": str(resolve_config_dir()),
    "mode": AuthConfig.from_env().mode.value,
    "env": {k: os.environ.get(k) for k in NAMES},
}))
"""

#: A folder's `.env` as a cloned repository could carry it.
FOLDER_ENV = {
    "PERSONALCLAW_AUTH_MODE": "none",
    "PERSONALCLAW_BIND_HOST": "0.0.0.0",
    "PERSONALCLAW_BYPASS_LOCAL_NETWORKS": "1",
    "PERSONALCLAW_HOME": "/tmp/somebody-elses-home",
    "PERSONALCLAW_PROJECT_DIR": "/tmp/somebody-elses-checkout",
    "HTTPS_PROXY": "http://127.0.0.1:9",
    "REQUESTS_CA_BUNDLE": "/tmp/somebody-elses-ca.pem",
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
    "ANTHROPIC_API_KEY": "folder-key-1",
    "SLACK_BOT_TOKEN": "folder-token-1",
}
TAKEN = {"ANTHROPIC_API_KEY", "SLACK_BOT_TOKEN"}


def _run(folder: Path, home: Path, env_file: dict[str, str] | None = None) -> tuple[dict, str]:
    """`personalclaw --version` started in *folder*, with *home* as its default home (no
    ``PERSONALCLAW_*`` in its environment): what it reports, and its stderr."""
    if env_file is not None:
        (folder / ".env").write_text(
            "".join(f"{k}={v}\n" for k, v in env_file.items()), encoding="utf-8"
        )
    names = sorted(set(FOLDER_ENV) | {"HOME_ONLY_API_KEY"})
    child = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PERSONALCLAW_", "PYTEST_")) and k not in FOLDER_ENV
    }
    child["HOME"] = str(home.parent)
    src = Path(__file__).resolve().parents[1] / "src"
    child["PYTHONPATH"] = os.pathsep.join([str(src), child.get("PYTHONPATH", "")])
    done = subprocess.run(
        [sys.executable, "-c", _CHILD.replace("NAMES", repr(names))],
        cwd=folder,
        env=child,
        capture_output=True,
        text=True,
        timeout=120,
    )
    lines = [line for line in done.stdout.splitlines() if line.startswith("REPORT ")]
    assert lines, f"no report: {done.returncode}\n{done.stdout}\n{done.stderr}"
    return json.loads(lines[-1][len("REPORT ") :]), done.stderr


@pytest.fixture
def places(tmp_path) -> tuple[Path, Path]:
    folder = tmp_path / "cloned-repository"
    home = tmp_path / "home" / ".personalclaw"
    folder.mkdir()
    home.mkdir(parents=True)
    return folder, home


def test_a_folders_env_sets_no_personalclaw_setting(places) -> None:
    folder, home = places
    report, err = _run(folder, home, FOLDER_ENV)

    assert report["mode"] == "local_token", "a gateway started here keeps its sign-in"
    for name in FOLDER_ENV:
        if name not in TAKEN:
            # Some of them the command sets itself (the project folder, to the checkout it runs
            # from); none to what the folder's `.env` says.
            assert report["env"][name] != FOLDER_ENV[name], f"{name} was taken from the folder"
    assert report["home"] == str(home), "the home stays the one it would be anywhere else"


def test_what_it_takes_and_leaves_is_named_on_stderr(places) -> None:
    folder, home = places
    report, err = _run(folder, home, FOLDER_ENV)

    assert {n for n in TAKEN if report["env"][n]} == TAKEN, "a credential still reaches it"
    took = next(line for line in err.splitlines() if "took" in line)
    left = next(line for line in err.splitlines() if "left out" in line)
    for name in TAKEN:
        assert name in took and name not in left, err
    for name in set(FOLDER_ENV) - TAKEN:
        assert name in left and name not in took, err
    assert str(folder / ".env") in took, "it says which file"
    assert "folder-key-1" not in err and "folder-token-1" not in err, "no value is printed"


def test_the_homes_env_is_read_as_before(places) -> None:
    """The home's `.env` gives every named credential, with nothing said, from any folder."""
    folder, home = places
    (home / ".env").write_text("HOME_ONLY_API_KEY=home-key-1\n", encoding="utf-8")
    report, err = _run(folder, home)

    assert report["env"]["HOME_ONLY_API_KEY"] == "home-key-1"
    assert "took" not in err and "left out" not in err, err


def test_started_in_the_home_its_env_is_the_homes(places) -> None:
    """A command started in the home folder reads its `.env` as the home's, not a folder's."""
    _folder, home = places
    (home / ".env").write_text("HOME_ONLY_API_KEY=home-key-1\n", encoding="utf-8")
    report, err = _run(home, home)

    assert report["env"]["HOME_ONLY_API_KEY"] == "home-key-1"
    assert "took" not in err and "left out" not in err, err
