"""No test in this suite can sign git in with a credential helper of the machine's own.

``conftest`` gives every test's git a neutral environment before anything is collected
(``tests/git_helper_guard.py``): no system configuration, a global file of the test's own, and an
empty ``credential.helper`` on git's command line. Core's git reads the same files. A git that
could still sign in with a helper of the machine's own is refused before it starts, and the test
that started it fails by name: on a Mac the file Apple's git bundles names ``osxkeychain``, and a
test's own ``git clone`` in the apps suite handed a token to it and waited ten minutes on the
owner's real keychain. This suite had none of that, and it runs on developers' machines all day.

Nothing here reaches a remote or a real helper. The refusals are asked of the guard; the one git
that is refused would only have dialed a port on this machine that nothing listens on; the one
that runs asks git's credential machinery with prompts off and nothing to answer; and every
helper named is this file's own invention.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import nested_pytest
import pytest

from personalclaw.net.git import git_argv, git_env

pytestmark = pytest.mark.security


ROOT = Path(__file__).resolve().parents[1]


def _suite():
    """The git guard the suite's ``conftest`` installed (``tests/git_helper_guard.py``), as
    pytest loaded it."""
    path = str(Path(__file__).with_name("conftest.py"))
    loaded = [m for m in list(sys.modules.values()) if getattr(m, "__file__", "") == path]
    assert loaded, "the suite's conftest.py was not loaded"
    guard = getattr(loaded[0], "git_helper_guard", None)
    assert guard is not None, "the suite's conftest installs no guard on the git its tests start"
    return guard


def _guard():
    return _suite().GUARD


def _nowhere() -> str:
    """An https URL on this machine that nothing answers."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    return f"https://127.0.0.1:{port}/state.git"


def _effective(values: list[str]) -> list[str]:
    """The helpers git runs from its ``credential.helper`` values, in order: an empty one clears
    the ones before it."""
    helpers: list[str] = []
    for value in values:
        helpers = [*helpers, value] if value else []
    return helpers


def _helpers(folder: Path) -> list[str]:
    """The ``credential.helper`` values a git run in *folder* reads, in order."""
    return subprocess.run(
        ["git", "config", "--get-all", "credential.helper"],
        cwd=folder,
        capture_output=True,
        text=True,
    ).stdout.split("\n")[:-1]


# ── what every test's git runs with ─────────────────────────────────────────────────────────


def test_every_tests_git_reads_no_configuration_of_the_machines(tmp_path):
    """Asked from a folder that is no repository, git names only what its command line sets."""
    nosystem = os.environ.get("GIT_CONFIG_NOSYSTEM")
    assert nosystem == "1", nosystem  # the value alone: the environment holds the owner's secrets
    own = Path(os.environ.get("GIT_CONFIG_GLOBAL", ""))
    assert own.parent == _suite().BASE, "the global file is not the test's own"

    listed = subprocess.run(
        ["git", "config", "--show-origin", "--list"],
        cwd=tmp_path,
        env={**os.environ, "GIT_CEILING_DIRECTORIES": str(tmp_path.parent)},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()

    assert listed, "git listed nothing at all: the check below would be vacuous"
    # The test's own global file holds only the seal that sends github.com nowhere
    # (tests/remote_git_guard.py): the test's, never the machine's.
    sealed = [line for line in listed if line.split("\t", 1)[0] == f"file:{own}"]
    assert all("no-test-reaches-github" in line for line in sealed), sealed
    rest = [line for line in listed if not line.startswith("command line:") and line not in sealed]
    assert rest == [], listed
    helpers = _helpers(tmp_path)
    assert helpers and _effective(helpers) == [], helpers


def test_each_test_has_a_global_git_file_of_its_own():
    """The file is this test's, not the one the collection read or another test's."""
    named = os.environ["GIT_CONFIG_GLOBAL"]
    assert named != str(_suite().BASE / "collect.gitconfig")
    assert named != _suite().own_global_config(), "two tests would share one"


def test_cores_git_signs_a_test_in_with_no_helper_of_the_machines():
    env = git_env(site="a-test", remote=True)
    argv = git_argv(["ls-remote", _nowhere()])

    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == os.environ["GIT_CONFIG_GLOBAL"]
    named = [
        setting.split("=", 1)[1]
        for flag, setting in zip(argv, argv[1:])
        if flag == "-c" and setting.lower().startswith("credential.helper=")
    ]
    assert named and _effective(named) == [], argv
    assert _guard().refusal(argv, env) == ""


# ── the guard, as a test meets it ───────────────────────────────────────────────────────────


def test_a_git_that_could_sign_in_with_the_machines_helper_is_refused_and_named():
    with pytest.raises(PermissionError) as refused:
        subprocess.run(
            ["git", "-c", "credential.helper=pc-fixture-machine-helper", "ls-remote", _nowhere()],
            capture_output=True,
            timeout=30,
        )

    assert "refused by the test suite" in str(refused.value)
    [taken] = _guard().take()  # taken here, so this test is not the one failed for it
    assert "test_a_git_that_could_sign_in_with_the_machines_helper_is_refused_and_named" in taken
    assert taken.endswith(
        "signs in with a credential helper of the machine's own (pc-fixture-machine-helper)"
    )


def test_a_git_that_is_allowed_really_runs_and_no_helper_answers(tmp_path):
    """The guard lets a neutral git start: git's own credential machinery, asked for a sign-in
    with nothing to answer it and prompts off, gives up without a password."""
    assert _effective(_helpers(tmp_path)) == [], "not neutral, so git credential is not started"

    asked = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=example.invalid\n\n",
        cwd=tmp_path,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert asked.returncode != 0 and "password=" not in asked.stdout, asked
    assert "terminal prompts disabled" in asked.stderr
    assert _guard().take() == []


# ── every way a git could reach the machine's helper ────────────────────────────────────────

_FIXTURE = "/tmp/pc-fixture"
_NEUTRAL = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": f"{_FIXTURE}/gitconfig",
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "credential.helper",
    "GIT_CONFIG_VALUE_0": "",
}
_OWN_HELPER = f"{_FIXTURE}/helper.sh"
_TOKEN_HELPER = '!f() { test "$1" = get || exit 0; printf \'password=%s\\n\' "$T"; }; f'


def _without(*names: str) -> dict[str, str]:
    return {k: v for k, v in _NEUTRAL.items() if k not in names}


def _at_the_real_home(value: str) -> str:
    """*value* with a leading ``~`` as the guard reads the machine's home: from the user database,
    never from ``$HOME``. A run with a scratch ``$HOME`` (``tools/ci`` run-job's every step) still
    names the real home's ``.gitconfig`` here, the one the guard must refuse."""
    real = _suite().REAL_HOME
    if value == "~":
        return real
    if value.startswith("~/"):
        return os.path.join(real, value[2:])
    return value


@pytest.mark.parametrize(
    ("argv", "env", "says"),
    [
        (["git", "fetch"], _without("GIT_CONFIG_NOSYSTEM"), "system git configuration"),
        (["git", "fetch"], {**_NEUTRAL, "GIT_CONFIG_NOSYSTEM": "0"}, "system git configuration"),
        (["git", "push"], {**_NEUTRAL, "GIT_CONFIG_GLOBAL": "~/.gitconfig"}, "global git"),
        (["git", "clone", "x"], {**_without("GIT_CONFIG_GLOBAL"), "HOME": "~"}, "global git"),
        (
            ["git", "-c", "credential.helper=", "-c", "credential.helper=osxkeychain", "fetch"],
            _NEUTRAL,
            "(osxkeychain)",
        ),
        (
            ["git", "-c", "credential.helper=/usr/local/bin/git-credential-manager", "pull"],
            _NEUTRAL,
            "machine's own (/usr/local/bin/git-credential-manager)",
        ),
        (
            ["git", "-c", "credential.https://example.invalid.helper=store", "fetch"],
            _NEUTRAL,
            "(store)",
        ),
        (
            ["git", "--config-env=credential.helper=PC_FIXTURE_HELPER", "fetch"],
            {**_NEUTRAL, "PC_FIXTURE_HELPER": "cache"},
            "(cache)",
        ),
        (["/usr/bin/env", "GIT_CONFIG_NOSYSTEM=0", "git", "fetch"], _NEUTRAL, "system git"),
        (["/usr/bin/env", "-i", "PATH=/usr/bin", "git", "ls-remote", "x"], _NEUTRAL, "system"),
        (["git", "credential-osxkeychain", "get"], _NEUTRAL, "credential helper itself"),
    ],
    ids=[
        "no-nosystem",
        "nosystem-off",
        "real-global",
        "real-home",
        "bare-helper",
        "helper-outside-own-folders",
        "url-helper",
        "config-env-helper",
        "env-undoes-it",
        "env-empties-it",
        "helper-run-directly",
    ],
)
def test_each_way_a_git_could_reach_the_machines_helper_is_refused(argv, env, says):
    env = {
        k: _at_the_real_home(v) if k in ("HOME", "GIT_CONFIG_GLOBAL") else v for k, v in env.items()
    }
    why = _guard().refusal(argv, env)
    assert says in why, why


def test_the_real_home_cases_name_the_real_home_under_a_scratch_home(monkeypatch):
    """Under run-job a step's ``$HOME`` is its scratch home, and the guard still names the real
    one; so do these cases (merge.yml run 40053059413 read ``~`` as the scratch home, and the
    guard rightly let the job's own ``.gitconfig`` through)."""
    monkeypatch.setenv("HOME", str(Path(_suite().REAL_HOME) / "a-scratch-home"))
    assert _at_the_real_home("~") == _suite().REAL_HOME
    assert _at_the_real_home("~/.gitconfig") == os.path.join(_suite().REAL_HOME, ".gitconfig")
    assert _at_the_real_home("/elsewhere/.gitconfig") == "/elsewhere/.gitconfig"
    why = _guard().refusal(
        ["git", "push"], {**_NEUTRAL, "GIT_CONFIG_GLOBAL": _at_the_real_home("~/.gitconfig")}
    )
    assert "global git" in why, why


def _config(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _repository(tmp_path: Path, config: str) -> Path:
    """A repository whose own ``.git/config`` is *config*, made by writing it: no git runs."""
    repo = tmp_path / "repo"
    for part in ("objects", "refs"):
        (repo / ".git" / part).mkdir(parents=True)
    _config(repo / ".git" / "HEAD", "ref: refs/heads/main\n")
    _config(repo / ".git" / "config", config)
    return repo


def test_a_helper_a_global_file_names_is_refused_when_nothing_clears_it(tmp_path):
    named = _config(tmp_path / "global", "[credential]\n\thelper = osxkeychain\n")
    env = {**_without("GIT_CONFIG_COUNT"), "GIT_CONFIG_GLOBAL": str(named)}
    assert _guard().refusal(["git", "fetch"], env, cwd=tmp_path).endswith("(osxkeychain)")
    assert _guard().refusal(["git", "fetch"], {**env, "GIT_CONFIG_COUNT": "1"}, cwd=tmp_path) == ""


def test_a_helper_a_file_the_global_one_includes_names_is_refused(tmp_path):
    _config(tmp_path / "more", '[credential "https://example.invalid"]\n\thelper = cache\n')
    named = _config(tmp_path / "global", "[include]\n\tpath = more\n")
    env = {**_without("GIT_CONFIG_COUNT"), "GIT_CONFIG_GLOBAL": str(named)}
    assert _guard().refusal(["git", "pull"], env, cwd=tmp_path).endswith("(cache)")


def test_a_helper_a_repositorys_own_configuration_names_is_refused(tmp_path):
    repo = _repository(tmp_path, "[credential]\n\thelper = store\n")
    (repo / "sub").mkdir()
    env = _without("GIT_CONFIG_COUNT")
    assert _guard().refusal(["git", "fetch"], env, cwd=repo / "sub").endswith("(store)")
    assert _guard().refusal(["git", "-C", str(repo), "push"], env, cwd=tmp_path).endswith("(store)")
    assert (
        _guard().refusal(["git", "fetch"], _NEUTRAL, cwd=repo) == ""
    ), "the clear on the command line"


def test_a_file_that_cant_be_read_is_refused_rather_than_guessed(tmp_path):
    unreadable = _config(tmp_path / "global", "[credential]\n\thelper = osxkeychain\n")
    unreadable.chmod(0)
    try:
        if os.access(unreadable, os.R_OK):
            pytest.skip("this user reads a file with no permissions at all")
        env = {**_without("GIT_CONFIG_COUNT"), "GIT_CONFIG_GLOBAL": str(unreadable)}
        assert "which can't be read" in _guard().refusal(["git", "fetch"], env, cwd=tmp_path)
    finally:
        unreadable.chmod(0o600)


@pytest.mark.parametrize(
    ("argv", "env"),
    [
        (["git", "fetch"], _NEUTRAL),
        (["git", "fetch"], _without("GIT_CONFIG_COUNT")),
        (
            ["git", "-c", "credential.helper=", "-c", f"credential.helper={_OWN_HELPER}", "push"],
            _without("GIT_CONFIG_COUNT"),
        ),
        (["git", "-c", f"credential.helper={_TOKEN_HELPER}", "clone", "x"], _NEUTRAL),
        (["git", "commit", "-m", "x"], {}),
        (["git", "http-backend"], {"HOME": _FIXTURE}),
        (["git", "version"], {}),
        (["rsync", "-a", "src/", "dst/"], {}),
    ],
    ids=[
        "neutral",
        "nothing-named-nothing-cleared",
        "own-helper",
        "token-helper",
        "no-sign-in",
        "server-side",
        "version",
        "not-git",
    ],
)
def test_a_git_that_cannot_reach_the_machines_helper_is_let_through(argv, env, tmp_path):
    assert _guard().refusal(argv, env, cwd=tmp_path) == ""


def test_a_helper_a_file_names_that_the_test_wrote_is_let_through(tmp_path):
    helper = tmp_path / "helper.sh"
    named = _config(tmp_path / "global", f"[credential]\n\thelper = {helper}\n")
    env = {**_without("GIT_CONFIG_COUNT"), "GIT_CONFIG_GLOBAL": str(named)}
    assert _guard().refusal(["git", "fetch"], env, cwd=tmp_path) == ""


def test_the_guard_the_apps_suite_installs_is_the_same_one():
    """The apps suite installs the guard through the SDK (``refuse_git_helpers``) as this suite
    does, so the two can't drift apart."""
    from personalclaw.sdk.testing import GitGuard

    assert isinstance(_guard(), GitGuard)


# ── a core whose git would still reach the machine's helper ─────────────────────────────────


def test_a_core_whose_git_would_name_the_machines_helper_stops_the_run(tmp_path):
    """``tests/git_helper_guard.py`` asks core's git before any test runs: a core that still reads
    the machine's own configuration, and so signs in with its keychain helper, stops the run
    rather than letting a test's git reach it. Played by a core whose owner sign-in is replaced
    before pytest starts (``sitecustomize``), in a run of one test of this file."""
    plant = tmp_path / "plant"
    plant.mkdir()
    (plant / "sitecustomize.py").write_text(
        "import personalclaw.net.git as g\n"
        "g._owner_auth_settings = lambda: ['credential.helper=pc-fixture-machine-helper']\n",
        encoding="utf-8",
    )
    path = os.pathsep.join(p for p in (str(plant), os.environ.get("PYTHONPATH", "")) if p)
    env = {**os.environ, "PYTHONPATH": path}
    env.pop("PYTEST_CURRENT_TEST", None)
    one = f"{Path(__file__)}::test_the_guard_the_apps_suite_installs_is_the_same_one"
    run = [
        *nested_pytest.argv(tmp_path),
        "-q",
        "-p",
        "no:cacheprovider",
        "-n",
        "0",
    ]

    ran = subprocess.run(
        [*run, "--no-cov", "--color=no", one],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )

    said = ran.stdout + ran.stderr
    assert ran.returncode != 0, said
    assert "core's git would sign a test in with the machine's own credential helper" in said
    assert "(pc-fixture-machine-helper)" in said
    assert " passed" not in said, "a test ran after the check said to stop"


# ── the real home is the user database's, never $HOME ───────────────────────────────────────


#: Loads the suite's git guard in a child whose user database names ``PC_PLANTED_REAL_HOME``, and
#: prints what it guards beside what a guard built from ``$HOME``, as it was before, guards, and
#: why each refuses a git run with this ``$HOME``.
_LOAD_BESIDE_THE_OLD_GUARD = """\
import json, os, pwd, sys

_real = pwd.getpwuid


def _planted(uid):
    fields = list(_real(uid))
    fields[5] = os.environ["PC_PLANTED_REAL_HOME"]
    return pwd.struct_passwd(fields)


pwd.getpwuid = _planted
sys.path.insert(0, sys.argv[1])
import git_helper_guard
from personalclaw.sdk.testing import GitGuard

try:
    def folders(guard):
        return [guard.real_home, guard.real_xdg, sorted(guard.real_global)]

    argv = ["git", "ls-remote", "https://example.invalid/r.git"]
    env = {"GIT_CONFIG_NOSYSTEM": "1", "HOME": os.environ["HOME"]}
    before = os.path.realpath(os.path.expanduser("~"))
    old = GitGuard(real_home=before)
    now = git_helper_guard.GUARD
    print(json.dumps({
        "now": [git_helper_guard.REAL_HOME, folders(now), now.refusal(argv, env)],
        "before": [before, folders(old), old.refusal(argv, env)],
    }))
finally:
    git_helper_guard.close()
"""


def _child_env(tmp_path, home: Path | str, planted: Path | str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "GIT_CONFIG"))}
    env.update(
        HOME=str(home),
        PC_PLANTED_REAL_HOME=str(planted),
        PERSONALCLAW_HOME=str(tmp_path / "pclaw-home"),
    )
    return env


#: The ways a ``$HOME`` can name the folder the user database names as the real home.
_SPELLINGS = [
    "as the user database does",
    "with a slash",
    "by a link",
    "by a link, and the user database by another",
    "in another letter case",
]


def _the_real_home_spelled(tmp_path: Path, spelling: str) -> tuple[str, str]:
    """A ``$HOME`` and a user database entry under *tmp_path* that name the same folder, the
    ``$HOME`` spelled as *spelling* says. Skips a spelling this file system can't give."""
    real = tmp_path / "real-home"
    real.mkdir()
    if spelling == "as the user database does":
        return str(real), str(real)
    if spelling == "with a slash":
        return str(real) + os.sep, str(real)
    if spelling == "by a link":
        (tmp_path / "home-link").symlink_to(real)
        return str(tmp_path / "home-link"), str(real)
    if spelling == "by a link, and the user database by another":
        (tmp_path / "home-link").symlink_to(real)
        (tmp_path / "entry-link").symlink_to(real)
        return str(tmp_path / "home-link"), str(tmp_path / "entry-link")
    assert spelling == "in another letter case", spelling
    other = tmp_path / "REAL-HOME"
    if not (other.exists() and os.path.samefile(other, real)):
        pytest.skip("this file system tells letter case apart, so no other case names the home")
    return str(other), str(real)


@pytest.mark.parametrize("spelling", _SPELLINGS)
def test_where_HOME_is_the_real_home_the_git_guard_watches_what_it_did_before(tmp_path, spelling):
    """Unchanged where it already worked: a run whose ``$HOME`` names the real home, however it
    spells it, guards the folders it guarded when it took them from ``$HOME``, and refuses a git
    that would read that home's global git file as it did."""
    home, entry = _the_real_home_spelled(tmp_path, spelling)
    ran = subprocess.run(
        [sys.executable, "-c", _LOAD_BESIDE_THE_OLD_GUARD, str(ROOT / "tests")],
        cwd=tmp_path,
        env=_child_env(tmp_path, home, entry),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    said = json.loads(ran.stdout)
    assert said["now"] == said["before"]
    assert said["now"][2].startswith("reads the global git configuration of whoever runs the tests")


_LOAD_UNDER_A_SCRATCH_HOME = """\
import os, pwd, sys

_real = pwd.getpwuid


def _planted(uid):
    fields = list(_real(uid))
    fields[5] = os.environ["PC_PLANTED_REAL_HOME"]
    return pwd.struct_passwd(fields)


pwd.getpwuid = _planted
sys.path.insert(0, sys.argv[1])
import git_helper_guard

try:
    env = {"GIT_CONFIG_NOSYSTEM": "1", "HOME": os.environ["PC_PLANTED_REAL_HOME"]}
    print(git_helper_guard.REAL_HOME)
    argv = ["git", "ls-remote", "https://example.invalid/r.git"]
    print(git_helper_guard.GUARD.refusal(argv, env))
finally:
    git_helper_guard.close()
"""


def test_a_run_started_with_a_scratch_HOME_still_guards_the_real_homes_git_file(tmp_path):
    """A run whose ``$HOME`` is a scratch folder (a job ``tools/ci/run.py`` starts) loads the guard
    with the planted real home the user database names, so a git that would read that home's
    global file is refused, not the scratch home's."""
    real = tmp_path / "real-home"
    real.mkdir()
    scratch_home = tmp_path / "scratch-home"
    scratch_home.mkdir()
    ran = subprocess.run(
        [sys.executable, "-c", _LOAD_UNDER_A_SCRATCH_HOME, str(ROOT / "tests")],
        cwd=tmp_path,
        env=_child_env(tmp_path, scratch_home, real),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    home, why = ran.stdout.splitlines()
    assert home == os.path.realpath(real)
    assert why == (
        "reads the global git configuration of whoever runs the tests "
        f"({os.path.join(str(real), '.gitconfig')})"
    )
