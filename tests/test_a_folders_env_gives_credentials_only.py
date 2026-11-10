"""A ``.env`` in the folder a command starts in gives that command credentials only, and says so.

Every ``personalclaw`` command reads the ``.env`` of the folder it starts in, before the home's,
so a key kept beside a project reaches the command. Such a folder may be anyone's, a repository
just cloned among them. So from it the command takes only credentials, by their names
(``…_API_KEY``, ``…_TOKEN``, ``…_SECRET``…), and never a PersonalClaw setting
(``PERSONALCLAW_*``: the sign-in mode, the bind address, the home, the project folder), nor a
name that routes or trusts traffic (a proxy, a CA bundle, a provider's base URL). It names on
stderr each name it took, and each it left out. The home's ``.env`` gives every named credential,
with the same name and value check, and says nothing unless it cannot be read as text.

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

import certifi
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
    "names": sorted(os.environ),
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


def _run(
    folder: Path,
    home: Path,
    env_file: dict[str, str] | None = None,
    *,
    lines: str = "",
    raw: bytes = b"",
    already: dict[str, str] | None = None,
) -> tuple[dict, str]:
    """`personalclaw --version` started in *folder*, with *home* as its default home (no
    ``PERSONALCLAW_*`` in its environment) and *already* in its environment: what it reports,
    and its stderr. The folder's `.env` holds *env_file*'s lines, then *lines* as written, or
    *raw*'s bytes as they are. A name *env_file* gives is not passed on from this process, which
    may set it (the libraries' settings, say): only *already* sets it beforehand."""
    if raw:
        (folder / ".env").write_bytes(raw)
    elif env_file is not None or lines:
        (folder / ".env").write_text(
            "".join(f"{k}={v}\n" for k, v in (env_file or {}).items()) + lines,
            encoding="utf-8",
        )
    names = sorted(set(FOLDER_ENV) | {"HOME_ONLY_API_KEY", "X_API_KEY", "GOOD_API_KEY"})
    child = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PERSONALCLAW_", "PYTEST_"))
        and k not in FOLDER_ENV
        and k not in (env_file or {})
    }
    child["HOME"] = str(home.parent)
    # A CA bundle of the child's own, so it starts the same on every machine: where OpenSSL has no
    # default CA file, the entry point names the system's bundle in the environment itself
    # (`_ssl_compat`), before the folder's `.env` is read, and so before the line the folder
    # gives for it.
    child["SSL_CERT_FILE"] = certifi.where()
    child.update(already or {})
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


# ── A name is checked before it is used ─────────────────────────────────────────

#: Lines whose names are not variable names: one with an escape, one with a line break inside it,
#: one with an `=` and two that end in a line break. Each ends as a credential's name would.
_NOT_NAMES = (
    "WITH\x1b_TOKEN=v1\n'TWO\nLINES_TOKEN'=v2\n'A=B_TOKEN'=v3\n"
    "'TRAILING_TOKEN\n'=v4\n'TRAILING_API_KEY\n'=v5\n"
)


def test_a_name_that_is_not_a_variable_name_is_neither_used_nor_shown(places) -> None:
    """A folder's `.env` is anyone's. A name in it is used only when it is a variable name. Other
    lines are counted, never shown, and the command starts as it would without them."""
    folder, home = places
    report, err = _run(folder, home, {"SLACK_BOT_TOKEN": "folder-token-1"}, lines=_NOT_NAMES)

    assert report["env"]["SLACK_BOT_TOKEN"] == "folder-token-1", "a plain name is still taken"
    for name in ("WITH\x1b_TOKEN", "TWO\nLINES_TOKEN", "A=B_TOKEN", "B_TOKEN"):
        assert name not in report["names"], f"{name!r} reached the environment"
    assert "\x1b" not in err and "LINES_TOKEN" not in err and "A=B" not in err, repr(err)
    assert "TRAILING" not in err, repr(err)
    assert [line for line in err.splitlines() if line.startswith("personalclaw: took")] == [
        f"personalclaw: took SLACK_BOT_TOKEN from {folder / '.env'}, the .env of the folder this "
        "command started in."
    ], err
    assert f"left out 5 names in {folder / '.env'} that are not a variable name" in err, err


def test_a_libraries_own_setting_is_left_out(places) -> None:
    """A name PersonalClaw sets for a library (``library_env``) ends as a credential's can, and
    is still not a credential: it is left out, and named."""
    folder, home = places
    report, err = _run(folder, home, {"HF_HUB_DISABLE_IMPLICIT_TOKEN": "0"})

    left = next(line for line in err.splitlines() if "left out" in line)
    assert "HF_HUB_DISABLE_IMPLICIT_TOKEN" in left, err
    assert "took" not in err, err


def test_a_name_the_environment_already_sets_is_named(places) -> None:
    """The environment's value is the one used. A credential the folder's `.env` sets as well is
    named on a line of its own; any other such name is left out, as it would be anyway."""
    folder, home = places
    report, err = _run(
        folder,
        home,
        {"ANTHROPIC_API_KEY": "folder-key-1", "HTTPS_PROXY": "http://127.0.0.1:9"},
        already={"ANTHROPIC_API_KEY": "parent-key-1", "HTTPS_PROXY": "http://127.0.0.1:8"},
    )

    assert report["env"]["ANTHROPIC_API_KEY"] == "parent-key-1"
    kept = next(line for line in err.splitlines() if "kept" in line)
    assert "ANTHROPIC_API_KEY" in kept and str(folder / ".env") in kept, err
    left = next(line for line in err.splitlines() if "left out" in line)
    assert "HTTPS_PROXY" in left and "ANTHROPIC_API_KEY" not in left, err
    assert "took" not in err, err
    assert "key-1" not in err and "127.0.0.1" not in err, "no value is printed"


def test_the_folders_own_name_is_shown_as_text(tmp_path) -> None:
    """The folder's name is the one part of those lines the folder chooses."""
    folder = tmp_path / "clone\x1b"
    home = tmp_path / "home" / ".personalclaw"
    folder.mkdir()
    home.mkdir(parents=True)
    report, err = _run(folder, home, {"SLACK_BOT_TOKEN": "folder-token-1"})

    assert "\x1b" not in err, repr(err)
    assert "clone\\x1b" in err, err


def test_a_value_the_environment_cannot_hold_is_left_out(places) -> None:
    """A value with a NUL in it cannot be set in the environment: it is counted, and the command
    starts."""
    folder, home = places
    report, err = _run(folder, home, lines="X_API_KEY=a\x00b\n")

    assert "X_API_KEY" not in report["names"]
    assert f"left out 1 name in {folder / '.env'} whose value cannot be set" in err, err


def test_a_folders_env_that_is_not_text_gives_nothing(places) -> None:
    """A `.env` that cannot be read as text is said, and the command starts."""
    folder, home = places
    report, err = _run(folder, home, raw=b"X_API_KEY=\xff\n")

    assert "X_API_KEY" not in report["names"]
    assert f"could not read {folder / '.env'} as text" in err, err


def test_the_homes_env_gets_the_same_name_check(places) -> None:
    """A line of the home's `.env` whose name is not a variable name, or whose value the
    environment cannot hold, is skipped, and the rest of the file is read."""
    folder, home = places
    (home / ".env").write_text(
        "'A=B_TOKEN'=v\nNUL_API_KEY=a\x00b\nGOOD_API_KEY=g\n", encoding="utf-8"
    )
    report, err = _run(folder, home)

    assert report["env"]["GOOD_API_KEY"] == "g"
    assert "NUL_API_KEY" not in report["names"]


def test_a_homes_env_that_is_not_text_is_said(places) -> None:
    """A home's `.env` that cannot be read as text is said, as a folder's is, and the command
    starts."""
    folder, home = places
    (home / ".env").write_bytes(b"X_API_KEY=\xff\n")
    report, err = _run(folder, home)

    assert "X_API_KEY" not in report["names"]
    assert f"could not read {home / '.env'} as text" in err, err


def test_the_homes_lines_are_read_as_written(places) -> None:
    """A `${NAME}` in a home's line is that text too, as the credential store reads it."""
    folder, home = places
    (home / ".env").write_text("X_API_KEY=a${HOME}b\n", encoding="utf-8")
    report, err = _run(folder, home)

    assert report["env"]["X_API_KEY"] == "a${HOME}b"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a file whatever its mode")
def test_a_folders_env_that_cannot_be_read_gives_nothing(places) -> None:
    """A folder's `.env` this user cannot read is said, and the command starts."""
    folder, home = places
    env = folder / ".env"
    env.write_text("SLACK_BOT_TOKEN=folder-token-1\n", encoding="utf-8")
    env.chmod(0)
    try:
        report, err = _run(folder, home)
    finally:
        env.chmod(0o600)

    assert "SLACK_BOT_TOKEN" not in report["names"]
    assert f"could not read {folder / '.env'} as text" in err, err


def test_a_folders_line_is_read_as_written(places) -> None:
    """A `${NAME}` in a folder's line is that text, never a value this command's environment
    holds: the folder's lines are read as written."""
    folder, home = places
    report, err = _run(
        folder,
        home,
        lines="X_API_KEY=${ANTHROPIC_API_KEY}\n",
        already={"ANTHROPIC_API_KEY": "parent-key-1"},
    )

    assert report["env"]["X_API_KEY"] == "${ANTHROPIC_API_KEY}"
    assert "parent-key-1" not in err


def test_a_credential_taken_over_the_homes_says_so(places) -> None:
    """A folder's credential is used over the one the home's `.env` holds, as documented, and
    the line says which it replaced."""
    folder, home = places
    (home / ".env").write_text("ANTHROPIC_API_KEY=home-key-1\n", encoding="utf-8")
    report, err = _run(folder, home, {"ANTHROPIC_API_KEY": "folder-key-1"})

    assert report["env"]["ANTHROPIC_API_KEY"] == "folder-key-1"
    took = next(line for line in err.splitlines() if "took" in line)
    assert took.endswith(", over your home's ANTHROPIC_API_KEY."), err
    assert "key-1" not in err


def test_a_home_value_the_environment_cannot_hold_is_not_replaced(places) -> None:
    """ "Over your home's" names only a credential the home's `.env` sets to a value the command
    could have used."""
    folder, home = places
    (home / ".env").write_text("ANTHROPIC_API_KEY=a\x00b\n", encoding="utf-8")
    report, err = _run(folder, home, {"ANTHROPIC_API_KEY": "folder-key-1"})

    took = next(line for line in err.splitlines() if "took" in line)
    assert "over your home's" not in took, err


def test_the_folders_name_stays_on_its_line(tmp_path) -> None:
    """A folder whose name has a line break in it is named on one line."""
    folder = tmp_path / "clone\nnext"
    home = tmp_path / "home" / ".personalclaw"
    folder.mkdir()
    home.mkdir(parents=True)
    report, err = _run(folder, home, {"SLACK_BOT_TOKEN": "folder-token-1"})

    assert "clone\\nnext" in err, err
    assert not [line for line in err.splitlines() if line.startswith("next")], err


def test_a_byte_order_mark_before_the_first_name_is_not_part_of_it(places) -> None:
    """An editor's UTF-8 byte-order mark is not part of the first name."""
    folder, home = places
    report, err = _run(folder, home, raw="\ufeffSLACK_BOT_TOKEN=folder-token-1\n".encode())

    assert report["env"]["SLACK_BOT_TOKEN"] == "folder-token-1"
    assert "not a variable name" not in err, err
