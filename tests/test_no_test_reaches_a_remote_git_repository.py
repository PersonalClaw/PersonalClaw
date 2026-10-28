"""No test's git reaches a remote repository over the network (tests/remote_git_guard.py).

#5654's run timed out ``test_local_source_apps_surface_in_catalog`` at 30 s: ``available_catalog()``
cloned github.com/PersonalClaw/PersonalClawApps, the shipped app source, which is on by default.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest
import remote_git_guard

from personalclaw.apps import catalog

_SHIPPED = "https://github.com/PersonalClaw/PersonalClawApps.git"


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)


def test_the_catalog_with_its_defaults_reaches_no_remote() -> None:
    assert _SHIPPED not in catalog.list_git_sources()
    catalog.available_catalog()
    assert remote_git_guard.GUARD.take() == []


def test_a_remote_clone_in_a_test_is_refused_at_once_and_names_the_test(tmp_path: Path) -> None:
    started = time.monotonic()
    with pytest.raises(PermissionError, match="reaches no remote over the network"):
        _git("clone", "--depth", "1", "--", _SHIPPED, str(tmp_path / "apps"))
    assert time.monotonic() - started < 5
    charged = remote_git_guard.GUARD.take()
    assert charged == [
        "tests/test_no_test_reaches_a_remote_git_repository.py::"
        "test_a_remote_clone_in_a_test_is_refused_at_once_and_names_the_test -> "
        f"git clone {_SHIPPED}"
    ]


def test_a_test_that_turns_the_shipped_source_on_still_reaches_no_remote(
    shipped_app_sources: None,
) -> None:
    assert _SHIPPED in catalog.list_git_sources()
    started = time.monotonic()
    try:
        catalog.available_catalog()
    except PermissionError:
        pass  # the scan's clone was refused before it started
    assert time.monotonic() - started < 10
    charged = remote_git_guard.GUARD.take()
    assert charged and all(f"git clone {_SHIPPED}" in line for line in charged), charged


@pytest.mark.parametrize(
    "spelling",
    [
        "https://github.com/",
        "http://github.com/",
        "ssh://git@github.com/",
        "git@github.com:",
        "github.com:",
        "git://github.com/",
    ],
)
def test_a_git_another_program_starts_reaches_no_github(spelling: str) -> None:
    """The second layer: a git this process did not start itself (here, a shell's) reads the
    test's sealed global file, which sends github.com, each way git spells it, to a folder that is
    not there. Were the seal to miss one, ssh, git's own protocol and http all fail here on this
    machine (``false``, and a proxy at a closed loopback port), so the test still reaches nothing:
    it fails on the missing word instead."""
    started = time.monotonic()
    done = subprocess.run(
        [
            "/bin/sh",
            "-c",
            f"git -c http.proxy=http://127.0.0.1:9 ls-remote {spelling}PersonalClaw/apps.git",
        ],
        env=dict(os.environ, GIT_SSH_COMMAND="false", GIT_PROXY_COMMAND="false"),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert time.monotonic() - started < 5
    assert done.returncode != 0
    assert "no-test-reaches-github" in done.stderr, done.stderr
    assert remote_git_guard.GUARD.take() == []  # not this process's git: the file stopped it


def test_a_remote_planted_on_disk_is_reached_as_ever(tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    assert _git("init", "--bare", "-q", str(origin)).returncode == 0
    work = tmp_path / "work"
    assert _git("clone", "-q", str(origin), str(work)).returncode == 0
    assert _git("fetch", "-q", "origin", cwd=work).returncode == 0
    assert _git("ls-remote", str(origin)).returncode == 0
    assert remote_git_guard.GUARD.take() == []


#: ``(url, off this machine)``: a host is judged by its name as written, never resolved.
_HOSTS = [
    (_SHIPPED, True),
    ("git@github.com:PersonalClaw/PersonalClawApps.git", True),
    ("ssh://git@example.com/x.git", True),  # documentation's name, and it does resolve
    ("https://gitlab.com/x/x.git", True),
    ("https://GitHub.com./x.git", True),
    ("https://example.invalid.attacker.com/x.git", True),  # reserved only at its end
    ("https://localtest.me/x.git", True),  # resolves to 127.0.0.1, and is judged by its name
    ("https://127.0.0.1.nip.io/x.git", True),  # a name, not an address
    ("http://192.168.1.10/x.git", True),  # a private address is another machine's
    ("https://[2001:db8::1]/x.git", True),
    ("ssh://git@[2001:db8::1]/x.git", True),
    ("https::https://gitlab.com/x/x.git", True),  # a remote helper's address is what it reaches
    ("file:///tmp/x.git", False),
    ("/tmp/x.git", False),
    ("../x.git", False),
    ("ssh://example.invalid/tmp/origin.git", False),
    ("https://listing.test/evil.git", False),
    ("https://rebind.example/x.git", False),
    ("https://app.localhost/x.git", False),
    ("https://127.0.0.1:58514/state.git", False),
    ("http://127.9.9.9/x.git", False),
    ("https://[::1]:8443/x.git", False),
    ("ssh://git@localhost/x.git", False),
    ("https://LOCALHOST./x.git", False),
    ("ext::/tmp/plant-ext-transport.sh", False),  # a command on this machine
]


@pytest.mark.parametrize(("url", "off"), _HOSTS)
def test_a_url_on_the_command_line_is_judged_by_its_host(
    tmp_path: Path, url: str, off: bool
) -> None:
    for args in (
        ["clone", "--depth", "1", url, "dest"],
        ["ls-remote", url],
        ["fetch", url, "main"],
        ["push", url, "main:main"],
        ["submodule", "add", url, "apps"],
    ):
        why = remote_git_guard.refusal(["git", *args], dict(os.environ), cwd=tmp_path)
        assert bool(why) is off, (args, why)
        assert not why or url in why


@pytest.mark.parametrize(("url", "off"), _HOSTS)
def test_a_named_remote_is_judged_by_its_url(tmp_path: Path, url: str, off: bool) -> None:
    work = tmp_path / "work"
    assert _git("init", "-q", str(work)).returncode == 0
    assert _git("remote", "add", "origin", url, cwd=work).returncode == 0
    for args in (
        ["fetch"],
        ["fetch", "--depth", "1", "origin"],
        ["pull", "--rebase"],
        ["push", "origin", "HEAD:refs/heads/main"],
        ["ls-remote", "origin"],
        ["remote", "show", "origin"],
        ["remote", "prune", "origin"],
        ["remote", "update"],
    ):
        why = remote_git_guard.refusal(["git", *args], dict(os.environ), cwd=work)
        assert bool(why) is off, (args, why)
        assert not why or url in why


def test_a_host_is_never_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_lookup(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"the guard looked a name up: {args}")

    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        monkeypatch.setattr(socket, name, no_lookup)
    for url, off in _HOSTS:
        why = remote_git_guard.refusal(["git", "ls-remote", url], dict(os.environ), cwd=tmp_path)
        assert bool(why) is off, url


def test_ssh_host_path_with_no_user_is_judged_where_git_reads_it_so(tmp_path: Path) -> None:
    """``clone`` and ``ls-remote`` read ``host:path`` as ssh, and so does a remote's URL; a
    refspec after ``push``'s or ``fetch``'s repository has the same shape and is not one."""
    bare = "gitlab.com:x/x.git"
    for args in (["clone", bare, "dest"], ["ls-remote", bare], ["submodule", "add", bare]):
        assert remote_git_guard.refusal(["git", *args], dict(os.environ), cwd=tmp_path), args
    work = tmp_path / "work"
    assert _git("init", "-q", str(work)).returncode == 0
    assert _git("remote", "add", "origin", bare, cwd=work).returncode == 0
    assert remote_git_guard.refusal(["git", "fetch"], dict(os.environ), cwd=work)
    assert (
        _git("remote", "set-url", "origin", str(tmp_path / "origin.git"), cwd=work).returncode == 0
    )
    for args in (["push", "origin", "v1.0:v1.0"], ["fetch", "origin", "v1.0:v1.0"]):
        assert remote_git_guard.refusal(["git", *args], dict(os.environ), cwd=work) == "", args


def test_a_command_that_names_no_remote_is_judged_by_every_remote(tmp_path: Path) -> None:
    work = tmp_path / "work"
    assert _git("init", "-q", str(work)).returncode == 0
    assert _git("remote", "add", "origin", str(tmp_path / "origin.git"), cwd=work).returncode == 0
    assert remote_git_guard.refusal(["git", "fetch"], dict(os.environ), cwd=work) == ""
    assert _git("remote", "add", "upstream", _SHIPPED, cwd=work).returncode == 0
    for args in (["fetch"], ["fetch", "--all"], ["pull"], ["push"], ["remote", "update"]):
        why = remote_git_guard.refusal(["git", *args], dict(os.environ), cwd=work)
        assert why == f"git {args[0]} reaches {_SHIPPED}", args
    for args in (["fetch", "origin"], ["fetch", "../x.git"], ["push", ".", "HEAD:x"]):
        assert remote_git_guard.refusal(["git", *args], dict(os.environ), cwd=work) == "", args
    assert remote_git_guard.refusal(["git", "fetch", "upstream"], dict(os.environ), cwd=work)


def test_a_submodule_update_is_judged_by_the_urls_its_submodules_give(tmp_path: Path) -> None:
    work = tmp_path / "work"
    assert _git("init", "-q", str(work)).returncode == 0
    modules = work / ".gitmodules"
    modules.write_text(f'[submodule "apps"]\n\tpath = apps\n\turl = {_SHIPPED}\n')
    assert (
        remote_git_guard.refusal(
            ["git", "submodule", "update", "--init"], dict(os.environ), cwd=work
        )
        == f"git submodule reaches {_SHIPPED}"
    )
    for action in (["status"], ["init"], ["sync"], ["foreach", "true"]):
        why = remote_git_guard.refusal(["git", "submodule", *action], dict(os.environ), cwd=work)
        assert why == "", action
    modules.write_text(f'[submodule "apps"]\n\tpath = apps\n\turl = {tmp_path / "apps.git"}\n')
    assert (
        remote_git_guard.refusal(["git", "submodule", "update"], dict(os.environ), cwd=work) == ""
    )


def test_git_is_found_through_env_and_through_the_executable_given(tmp_path: Path) -> None:
    env = dict(os.environ)
    clone = ["clone", _SHIPPED, "dest"]
    assert remote_git_guard.refusal(["env", "-u", "X", "A=b", "git", *clone], env, cwd=tmp_path)
    git = shutil.which("git")
    assert git
    assert remote_git_guard.refusal(["anything", *clone], env, executable=git, cwd=tmp_path)
    assert remote_git_guard.refusal(["git", "-C", str(tmp_path), *clone], env, cwd="/")


def test_a_local_subcommand_and_a_remote_left_unreached_are_not_charged(tmp_path: Path) -> None:
    for args in (
        ["status"],
        ["-c", "remote.x.url=https://example.com", "log"],
        ["remote", "add", "origin", _SHIPPED],
        ["remote", "set-url", "origin", _SHIPPED],
        ["remote", "get-url", "origin"],
        ["remote", "show"],
        ["remote", "-v"],
    ):
        assert remote_git_guard.refusal(["git", *args], dict(os.environ), cwd=tmp_path) == "", args


def test_a_git_that_cannot_start_is_not_charged(tmp_path: Path) -> None:
    """There is no such program, or no folder to run it in, so it reaches nothing: the test's
    own FileNotFoundError, or git's own refusal, is what it gets."""
    work = tmp_path / "work"
    assert _git("init", "-q", str(work)).returncode == 0
    assert _git("remote", "add", "origin", _SHIPPED, cwd=work).returncode == 0
    assert remote_git_guard.refusal(["git", "fetch"], dict(os.environ), cwd=work)
    for args, cwd in (
        (["/nonexistent/pc-fixture/git", "-C", str(work), "fetch", "origin", "main"], tmp_path),
        (["git", "fetch"], tmp_path / "gone"),
        (["git", "-C", "gone", "fetch", "origin", "main"], work),
    ):
        assert remote_git_guard.refusal(args, dict(os.environ), cwd=cwd) == "", (args, cwd)


def test_the_census_seals_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(remote_git_guard.MODE_ENV, "report")
    assert remote_git_guard.seal(str(tmp_path / "g.gitconfig")) == str(tmp_path / "g.gitconfig")
    assert not (tmp_path / "g.gitconfig").exists()
    monkeypatch.delenv(remote_git_guard.MODE_ENV)
    remote_git_guard.seal(str(tmp_path / "g.gitconfig"))
    assert (tmp_path / "g.gitconfig").read_text() == remote_git_guard.SEALED


def test_each_tests_global_git_file_is_sealed() -> None:
    sealed = Path(os.environ["GIT_CONFIG_GLOBAL"]).read_text(encoding="utf-8")
    assert sealed.startswith(remote_git_guard.SEALED)
