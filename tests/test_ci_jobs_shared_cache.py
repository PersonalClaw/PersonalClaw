"""Local runs share one uv cache and one set of managed Pythons, and each checkout keeps its venv.

Each checkout's ``.local/run-job/cache`` held a uv cache of its own, 1.2 GB, beside a venv whose
files are clones of it: over the lanes' worktrees that was more than 100 GB. Off CI, a ``uv sync``
job now installs from one cache the account's runs share (``jobs.shared_cache_root``), and its venv
is still the checkout's own, linked from that cache. Only the install writes it: the commands after
it run with a cache of the job's own and no sync. Before its first ``uv sync`` job a run holds the
cache to its bound (``jobs.bound_shared_cache``).

The suite never reaches the account's real cache: conftest names a folder of each process's own
(``PCLAW_RUN_JOB_CACHE``), and a host made with ``HOME`` and the cache variables cleared still
resolves there, never under the real home's ``Library/Caches`` or ``.cache``.
"""

from __future__ import annotations

import dataclasses
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_ci_jobs import _git, _host, _job, _run

from tools.ci import jobs, run

_UV_VARIABLES = ("UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "UV_PROJECT_ENVIRONMENT", "UV_NO_SYNC")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A committed checkout with run-job's work folder ignored, as this tree ignores it."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".gitignore").write_text("reports/\n/.local/\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "planted")
    return root


def _dumps(tmp_path: Path, name: str) -> tuple[Path, Path]:
    """A ``uv`` stand-in that writes the uv variables its install sees to a file, and the file a
    command step writes its own to."""
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    install = tmp_path / f"{name}-install.json"
    names = ", ".join(repr(v) for v in _UV_VARIABLES)
    (stubs / "uv").write_text(
        f"#!{sys.executable}\nimport json, os\n"
        f"json.dump({{k: os.environ.get(k) for k in ({names},)}}, open({str(install)!r}, 'w'))\n"
    )
    (stubs / "uv").chmod(0o755)
    return stubs, install


def _command(out: Path) -> str:
    """A command step that writes the uv variables it sees to *out*. No braces: a step's words
    are filled in where they name a placeholder."""
    names = ", ".join(repr(v) for v in _UV_VARIABLES)
    seen = f"dict((k, os.environ.get(k)) for k in ({names},))"
    return f"import json, os; json.dump({seen}, open({str(out)!r}, 'w'))"


def _ran(
    repo: Path, tmp_path: Path, host: jobs.Host, toolchain: tuple[str, ...]
) -> tuple[dict, dict]:
    """What a job's install and its command step see of uv, run on *host*."""
    name = "-".join(toolchain)
    stubs, install = _dumps(tmp_path, name)
    out = tmp_path / f"{name}-command.json"
    job = _job(name, _command(out), toolchain=toolchain, python="3.12.15")
    host = dataclasses.replace(host, path=f"{stubs}{os.pathsep}{os.environ['PATH']}")
    outcome = _run(repo, job, tmp_path, host=host)
    assert outcome.code == run.OK, outcome.reasons
    seen = json.loads(install.read_text()) if install.exists() else {}
    return seen, json.loads(out.read_text())


def test_a_uv_sync_install_writes_the_shared_cache_and_its_commands_one_of_their_own(
    repo, tmp_path
):
    shared = tmp_path / "shared"
    install, command = _ran(repo, tmp_path, _host(repo, shared_cache=shared), ("uv-sync",))
    work = repo / jobs.WORK / "cache"
    assert install["UV_CACHE_DIR"] == str(shared / "uv")
    assert install["UV_PYTHON_INSTALL_DIR"] == str(shared / "uv-python")
    assert install["UV_NO_SYNC"] is None, "the install syncs"
    # The venv is the checkout's own: it installs the checkout's src, editable.
    assert install["UV_PROJECT_ENVIRONMENT"] == command["UV_PROJECT_ENVIRONMENT"]
    assert Path(command["UV_PROJECT_ENVIRONMENT"]).is_relative_to(work)
    # A command runs the venv the install left, with a cache of the job's own and no sync.
    assert command["UV_NO_SYNC"] == "1"
    assert not Path(command["UV_CACHE_DIR"]).is_relative_to(shared)
    assert not Path(command["UV_CACHE_DIR"]).is_relative_to(repo)
    assert command["UV_PYTHON_INSTALL_DIR"] == str(shared / "uv-python"), "the venv's Python"


def test_without_a_shared_cache_every_cache_is_the_checkouts_own_as_before(repo, tmp_path):
    install, command = _ran(repo, tmp_path, _host(repo), ("uv-sync",))
    work = repo / jobs.WORK / "cache"
    for seen in (install, command):
        assert seen["UV_CACHE_DIR"] == str(work / "uv")
        assert seen["UV_PYTHON_INSTALL_DIR"] == str(work / "uv-python")
        assert seen["UV_NO_SYNC"] is None


def test_a_job_with_no_uv_sync_install_keeps_the_checkouts_own_uv_cache(repo, tmp_path):
    """Its commands install what they use (``uv build``, ``uv pip install``), so the shared cache,
    which only an install from the lock writes, is not theirs."""
    shared = tmp_path / "shared"
    _, command = _ran(repo, tmp_path, _host(repo, shared_cache=shared), ("uv",))
    assert command["UV_CACHE_DIR"] == str(repo / jobs.WORK / "cache" / "uv")
    assert command["UV_NO_SYNC"] is None
    assert command["UV_PYTHON_INSTALL_DIR"] == str(shared / "uv-python")


def _bare(monkeypatch) -> None:
    """The runner's environment with no home and no cache or CI variable of its own."""
    for name in ("HOME", "XDG_CACHE_HOME", "CI", "GITHUB_ACTIONS"):
        monkeypatch.delenv(name, raising=False)


def _account_caches() -> list[Path]:
    home = run.real_home()
    return [home / "Library" / "Caches", home / ".cache"]


def test_a_run_with_home_and_the_cache_variables_cleared_never_reaches_the_real_cache(
    repo, tmp_path, monkeypatch
):
    """conftest names the suite's own folder (``PCLAW_RUN_JOB_CACHE``), and nothing else a run
    reads, ``HOME`` least of all, moves it under the account's real cache folders."""
    pinned = Path(os.environ[jobs.SHARED_CACHE_ENV])
    assert not any(pinned.is_relative_to(c) for c in _account_caches()), pinned
    _bare(monkeypatch)
    host = jobs.Host.this(repo)
    assert host.shared_cache == pinned
    # The planted job runs on Ubuntu's family, wherever this runs.
    install, command = _ran(
        repo, tmp_path, dataclasses.replace(host, family="ubuntu-"), ("uv-sync",)
    )
    for seen in (install, command):
        for name in ("UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR"):
            assert not any(Path(seen[name]).is_relative_to(c) for c in _account_caches()), seen


def test_without_the_suites_folder_a_run_would_resolve_the_real_cache(repo, monkeypatch):
    """The control for the test above: the pin is what keeps the suite out, since a run resolves
    the account's own folder from the user database, whatever ``HOME`` says."""
    _bare(monkeypatch)
    monkeypatch.delenv(jobs.SHARED_CACHE_ENV)
    root = jobs.shared_cache_root()
    assert root is not None and any(root.is_relative_to(c) for c in _account_caches()), root


@pytest.mark.parametrize(
    "platform, xdg, expect",
    [
        ("darwin", None, "Library/Caches/personalclaw/run-job"),
        ("linux", None, ".cache/personalclaw/run-job"),
        ("linux", "/elsewhere/caches", None),
        ("linux", "relative/caches", ".cache/personalclaw/run-job"),
    ],
    ids=["macOS", "Linux", "Linux, XDG_CACHE_HOME", "Linux, a relative XDG_CACHE_HOME"],
)
def test_the_shared_root_is_the_accounts_cache_folder_from_the_user_database(
    tmp_path, monkeypatch, platform, xdg, expect
):
    planted = tmp_path / "the-user-databases-home"
    monkeypatch.setattr(run, "real_home", lambda: planted)
    monkeypatch.setattr(jobs.sys, "platform", platform)
    environ = {"HOME": str(tmp_path / "a-home-the-caller-named")}
    if xdg:
        environ["XDG_CACHE_HOME"] = xdg
    root = jobs.shared_cache_root(environ)
    if expect is None:
        assert root == Path(xdg) / "personalclaw" / "run-job"
    else:
        assert root == planted / expect
    # The variable names another folder outright: a box that keeps everything under one.
    named = {**environ, jobs.SHARED_CACHE_ENV: str(tmp_path / "pclaw-proof" / "cache")}
    assert jobs.shared_cache_root(named) == tmp_path / "pclaw-proof" / "cache"


def test_an_account_with_no_home_keeps_each_checkouts_cache(monkeypatch):
    def no_entry() -> Path:
        raise RuntimeError("no entry")

    monkeypatch.setattr(run, "real_home", no_entry)
    assert jobs.shared_cache_root({}) is None


@pytest.mark.parametrize("variable", ["CI", "GITHUB_ACTIONS"])
def test_on_ci_no_cache_is_shared(repo, monkeypatch, variable):
    _bare(monkeypatch)
    monkeypatch.setenv(variable, "true")
    assert jobs.Host.this(repo).shared_cache is None


# ── the bound ────────────────────────────────────────────────────────────────────────────────


_KB = 64 * 1024


def _planted_cache(root: Path, packages: dict[str, int]) -> Path:
    """A uv cache whose packages were each last written at the time given, each with one 64 KiB
    archive its wheel entry names."""
    cache = root / "uv"
    for n, (name, written) in enumerate(packages.items()):
        archive = cache / "archive-v0" / f"a{n}"
        archive.mkdir(parents=True)
        (archive / "payload").write_bytes(b"x" * _KB)
        entry = cache / "wheels-v7" / "pypi" / name / "1.0-py3-none-any.http"
        entry.parent.mkdir(parents=True)
        entry.write_text(str(archive))
        os.utime(entry, (written, written))
    return cache


class _Uv:
    """``uv cache`` as the bound calls it: each call, and a clean removes its packages' archives."""

    def __init__(self, cache: Path, *, refuse: bool = False) -> None:
        self.cache = cache
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.refuse = refuse

    def __call__(self, argv, env, **_kw) -> subprocess.CompletedProcess:
        self.calls.append((list(argv), env))
        if self.refuse:
            return subprocess.CompletedProcess(argv, 2, "", "error: timed out waiting for lock")
        if argv[2] == "clean":
            for name in argv[3:]:
                entry = self.cache / "wheels-v7" / "pypi" / name / "1.0-py3-none-any.http"
                for path in Path(entry.read_text()).iterdir():
                    path.unlink()
                entry.unlink()
        return subprocess.CompletedProcess(argv, 0, "", "")


def test_a_cache_past_its_bound_is_pruned_then_cleaned_oldest_first_to_four_fifths(tmp_path):
    packages = {f"pkg{n:02d}": 1_000_000 + 100 * n for n in range(20)}
    packages["oldest"] = 1_000
    cache = _planted_cache(tmp_path, packages)
    size = jobs._blocks(cache)
    uv = _Uv(cache)
    log = io.StringIO()
    jobs.bound_shared_cache(tmp_path, bound_gb=size * 0.6 / 1e9, uv=uv, log=log)
    words = [argv[2:] for argv, _ in uv.calls]
    assert words[0] == ["prune"], words
    cleaned = [name for each in words[1:] for name in each[1:]]
    assert cleaned[0] == "oldest" and cleaned[1:] == sorted(cleaned[1:]), "oldest first"
    # Every package weighs the same: it cleans until under four fifths of the bound, and stops.
    target, each = size * 0.6 * 0.8, size / len(packages)
    left = len(packages) - len(cleaned)
    assert left * each <= target < (left + 8) * each, (left, cleaned)
    for _, env in uv.calls:
        assert env["UV_CACHE_DIR"] == str(cache) and env["UV_LOCK_TIMEOUT"] == "10"
    assert "past its" in log.getvalue()


def test_a_cache_within_its_bound_is_left_alone(tmp_path):
    cache = _planted_cache(tmp_path, {"a": 1, "b": 2})
    uv = _Uv(cache)
    jobs.bound_shared_cache(tmp_path, bound_gb=1.0, uv=uv, log=io.StringIO())
    assert uv.calls == []


def test_a_cache_another_run_holds_is_left_as_it_is_and_said(tmp_path):
    cache = _planted_cache(tmp_path, {f"p{n}": n for n in range(6)})
    size = jobs._blocks(cache)
    uv = _Uv(cache, refuse=True)
    log = io.StringIO()
    jobs.bound_shared_cache(tmp_path, bound_gb=size / 2 / 1e9, uv=uv, log=log)
    assert [argv[2:] for argv, _ in uv.calls] == [["prune"]], "nothing after a refusal"
    assert "left as it is: error: timed out waiting for lock" in log.getvalue()
    assert jobs._blocks(cache) == size


def test_another_file_system_is_said_once_and_the_bound_runs_once(
    repo, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(jobs, "_READIED", set())
    shared = tmp_path / "shared"
    devices = {shared: 1, repo: 2}
    monkeypatch.setattr(jobs, "_device", lambda path: devices[path])
    bounded: list[Path] = []
    monkeypatch.setattr(jobs, "bound_shared_cache", lambda root, **_kw: bounded.append(root))
    host = _host(repo, shared_cache=shared)
    jobs._ready_shared_cache(host)
    jobs._ready_shared_cache(host)
    err = capsys.readouterr().err
    assert err.count("is on another file system than") == 1, err
    assert bounded == [shared]
