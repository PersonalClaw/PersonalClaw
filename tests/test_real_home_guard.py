"""The real-home guard must be able to FAIL — and fail the RIGHT test (CRE-8).

Nothing here touches the developer's real ``~/.personalclaw``: proving a leak detector works
by leaking is not a proof, it is the defect the detector exists to prevent. The unit half
drives a :class:`real_home_guard.Guard` over a throwaway root, feeding its hook audit events
shaped exactly as CPython raises them. The end-to-end half runs a real pytest session in a
child process whose ``HOME`` is fake, so real ``open``, ``sqlite3.connect``, ``mkdir`` and
thread calls go through the real hook, the real charging and the real reports.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
import types
from pathlib import Path

import pytest
import real_home_guard
from real_home_guard import Guard, RealHomeAccessError

# Imported at MODULE level on purpose: that is collection time, the window in which these
# modules freeze a home into a constant. See test_no_import_time_constant_holds_the_real_home.
import personalclaw.agent as _agent
import personalclaw.dashboard.handlers.hooks as _hooks_handlers
import personalclaw.dashboard.handlers.mcp as _mcp_handlers

_TESTS_DIR = Path(__file__).resolve().parent


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A stand-in real home: ``<fake $HOME>/.personalclaw``, existing like a real one."""
    home = tmp_path / "home" / ".personalclaw"
    home.mkdir(parents=True)
    return home


@pytest.fixture
def guard(root: Path) -> Guard:
    return Guard(root)


def _refused(guard: Guard, event: str, *args) -> bool:
    try:
        guard.audit(event, args)
    except RealHomeAccessError:
        return True
    return False


def _in_thread(fn) -> None:
    thread = threading.Thread(target=fn)
    thread.start()
    thread.join()


# ── What is refused ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("event", "shape"),
    [
        ("open", lambda r: (str(r / "config.json"), "r", os.O_RDONLY)),  # a READ is refused too
        ("open", lambda r: (str(r / "security_events.jsonl"), "a", os.O_WRONLY | os.O_APPEND)),
        ("open", lambda r: (str(r / "x"), None, os.O_WRONLY | os.O_CREAT)),  # os.open's shape
        ("open", lambda r: ((r / "x").as_posix().encode(), "rb", os.O_RDONLY)),  # a bytes path
        ("sqlite3.connect", lambda r: (str(r / "session_search.db"),)),
        ("sqlite3.connect", lambda r: (f"file:{r / 'memory.db'}?mode=ro",)),  # the URI form
        ("os.mkdir", lambda r: (str(r / "sessions"), 0o777, -1)),
        ("os.mkdir", lambda r: (str(r), 0o777, -1)),  # the root itself
        ("os.rename", lambda r: ("/tmp/elsewhere", str(r / "config.json"), -1, -1)),  # INTO it
        ("os.rename", lambda r: (str(r / "config.json"), "/tmp/elsewhere", -1, -1)),  # out of it
        ("os.remove", lambda r: (str(r / "x"), -1)),
        ("os.rmdir", lambda r: (str(r / "d"), -1)),
        ("os.link", lambda r: (str(r / "x"), "/tmp/hard", -1, -1, True)),
        ("os.symlink", lambda r: ("/tmp/target", str(r / "link"), -1)),
        ("os.utime", lambda r: (str(r / "x"), None, None, -1)),
        ("os.chmod", lambda r: (str(r / "x"), 0o600, -1)),
        ("os.truncate", lambda r: (str(r / "x"), 0)),
        ("os.listdir", lambda r: (str(r),)),
        ("os.scandir", lambda r: (str(r / "skills"),)),
        ("shutil.rmtree", lambda r: (str(r / "apps"), None)),
    ],
)
def test_every_guarded_event_under_the_root_is_refused(guard, root, event, shape) -> None:
    assert _refused(guard, event, *shape(root))


@pytest.mark.parametrize(
    "path",
    [
        lambda r: str(r.parent / ".personalclaw-backup" / "config.json"),  # a sibling PREFIX
        lambda r: str(r.parent),  # its parent, $HOME itself
        lambda r: str(r / ".." / "elsewhere"),  # `..` walks out
        lambda r: "/tmp/unrelated.json",
        lambda r: 7,  # a file descriptor
        lambda r: None,  # os.listdir() of the cwd
    ],
)
def test_a_path_outside_the_root_passes(guard, root, path) -> None:
    assert not _refused(guard, "open", path(root), "r", os.O_RDONLY)


def test_symlinks_are_not_followed_but_the_root_s_own_real_name_is_known(tmp_path) -> None:
    """A root that is itself a symlink is matched under BOTH names — the real target is
    where the bytes are. (A symlink elsewhere pointing in is not resolved: that would cost a
    ``realpath`` on every open the interpreter does.)"""
    target = tmp_path / "volume" / "pclaw"
    target.mkdir(parents=True)
    (tmp_path / "home").mkdir()
    link = tmp_path / "home" / ".personalclaw"
    link.symlink_to(target)
    guard = Guard(link)
    assert _refused(guard, "open", str(link / "a"), "r", os.O_RDONLY)
    assert _refused(guard, "open", str(target / "a"), "r", os.O_RDONLY)


def test_memory_and_empty_sqlite_databases_pass(guard) -> None:
    assert not _refused(guard, "sqlite3.connect", ":memory:")
    assert not _refused(guard, "sqlite3.connect", "")


def test_a_relative_path_is_resolved_against_the_cwd(guard, root, monkeypatch) -> None:
    monkeypatch.chdir(root)
    assert _refused(guard, "open", "config.json", "r", os.O_RDONLY)
    assert _refused(guard, "sqlite3.connect", "index.db")


def test_dotdot_cannot_smuggle_a_path_in(guard, root) -> None:
    assert _refused(guard, "open", str(root.parent / "x" / ".." / ".personalclaw" / "y"), "r", 0)


# ── Who is charged ───────────────────────────────────────────────────────────────────────


def test_a_refusal_is_charged_to_the_running_test_and_names_everything(guard, root) -> None:
    guard.begin("tests/test_x.py::test_leaks")
    guard.phase = "call"
    assert _refused(guard, "open", str(root / "config.json"), "w", os.O_WRONLY | os.O_CREAT)
    [access] = guard.take("tests/test_x.py::test_leaks")
    assert (access.event, access.path) == ("open", str(root / "config.json"))
    assert (access.during, access.phase) == ("tests/test_x.py::test_leaks", "call")
    assert access.thread == threading.current_thread().name
    assert "test_a_refusal_is_charged_to_the_running_test" in access.stack
    assert guard.take("tests/test_x.py::test_leaks") == [], "taken once, then gone"
    assert guard.take_session() == []


def test_a_swallowed_refusal_is_still_charged(guard, root) -> None:
    """Production code swallows it — `Path.mkdir(exist_ok=True)` eats any OSError on an
    existing directory, most persistence catches Exception — so the exception can't be
    the only signal."""
    guard.begin("t::swallows")
    try:
        guard.audit("os.mkdir", (str(root), 0o777, -1))
    except Exception:  # noqa: BLE001 — exactly the swallow being modelled
        pass
    assert [a.path for a in guard.take("t::swallows")] == [str(root)]


def test_the_error_is_not_an_oserror(guard, root) -> None:
    """An OSError would read as an ordinary filesystem condition and be degraded around."""
    with pytest.raises(RealHomeAccessError) as caught:
        guard.audit("open", (str(root / "x"), "r", 0))
    assert not isinstance(caught.value, OSError)
    assert str(root / "x") in str(caught.value)


def _started_under(guard: Guard, target) -> threading.Thread:
    """A thread whose start the guard SAW — the audit event threading.Thread.start raises."""
    thread = threading.Thread(target=target)
    # 3.13 raises `_thread.start_joinable_thread`, 3.12 `_thread.start_new_thread`; both carry
    # the bound `Thread._bootstrap` first, which is all the stamp reads.
    guard.audit("_thread.start_joinable_thread", (thread._bootstrap, 0, None))
    return thread


def test_a_thread_is_charged_to_the_test_that_started_it(guard, root) -> None:
    guard.begin("t::spawns")
    thread = _started_under(guard, lambda: _refused(guard, "os.listdir", str(root)))
    thread.start()
    thread.join()
    [access] = guard.take("t::spawns")
    assert access.started_by == "t::spawns"
    assert access.thread == thread.name


def test_a_thread_inherits_the_stamp_of_the_thread_that_started_it(guard, root) -> None:
    guard.begin("t::grandparent")
    grandchild: list[threading.Thread] = []

    def child() -> None:
        inner = _started_under(guard, lambda: _refused(guard, "os.scandir", str(root)))
        grandchild.append(inner)
        inner.start()
        inner.join()

    thread = _started_under(guard, child)
    thread.start()
    thread.join()
    [access] = guard.take("t::grandparent")
    assert access.thread == grandchild[0].name


def test_a_thread_that_outlives_its_test_blames_no_bystander(guard, root) -> None:
    """The defect the walk-based rail could not attribute: the running test is innocent."""
    guard.begin("t::starts_a_worker")
    go, done = threading.Event(), threading.Event()

    def worker() -> None:
        go.wait(5)
        _refused(guard, "sqlite3.connect", str(root / "session_search.db"))
        done.set()

    thread = _started_under(guard, worker)
    thread.start()
    guard.end()
    guard.begin("t::innocent_bystander")
    go.set()
    done.wait(5)
    thread.join()
    assert guard.take("t::innocent_bystander") == []
    assert guard.take("t::starts_a_worker") == []
    [access] = guard.take_session()
    assert (access.started_by, access.during) == ("t::starts_a_worker", "t::innocent_bystander")
    rendered = access.render()
    assert "started by t::starts_a_worker" in rendered
    assert "of t::innocent_bystander, which did not start this thread" in rendered


def test_an_access_before_any_test_is_a_session_refusal(guard, root) -> None:
    assert _refused(guard, "os.mkdir", str(root), 0o777, -1)
    [access] = guard.take_session()
    assert (access.during, access.phase) == (None, "collection")
    assert "while no test was running (collection)" in access.render()


def test_another_guards_stamp_is_invisible(guard, root, tmp_path) -> None:
    """Two guards in one process (the suite's, and one a test drives) keep separate stamps."""
    other = Guard(tmp_path / "other" / ".personalclaw")
    other.begin("t::other")
    guard.begin("t::mine")
    thread = _started_under(other, lambda: _refused(guard, "os.listdir", str(root)))
    thread.start()
    thread.join()
    assert guard.take("t::mine") == [], "a thread this guard never saw start is not its test's"
    [access] = guard.take_session()
    assert access.started_by is None


# ── Child processes ──────────────────────────────────────────────────────────────────────


def _popen(argv, env) -> tuple:
    """``subprocess.Popen``'s audit shape: (executable, args, cwd, env)."""
    return (argv[0] if isinstance(argv, list) else argv, argv, None, env)


@pytest.mark.parametrize(
    "argv",
    [
        [sys.executable, "-m", "personalclaw", "gateway", "--help"],
        [sys.executable, "-m", "personalclaw.seed_local_model"],
        ["/usr/local/bin/personalclaw", "doctor"],
        "personalclaw gateway --port 0",  # a shell string
    ],
)
def test_a_personalclaw_child_with_no_isolated_home_is_refused(guard, root, argv) -> None:
    env = {"HOME": str(root.parent), "PATH": "/usr/bin"}
    assert _refused(guard, "subprocess.Popen", *_popen(argv, env))


@pytest.mark.parametrize(
    "env",
    [
        lambda r, t: {"HOME": str(r.parent), "PERSONALCLAW_HOME": str(t / "isolated")},
        lambda r, t: {"HOME": str(t / "fake-home")},
    ],
)
def test_a_personalclaw_child_given_its_own_home_passes(guard, root, tmp_path, env) -> None:
    argv = [sys.executable, "-m", "personalclaw", "gateway", "--help"]
    assert not _refused(guard, "subprocess.Popen", *_popen(argv, env(root, tmp_path)))


@pytest.mark.parametrize(
    "env",
    [
        lambda r: {"HOME": "/elsewhere", "PERSONALCLAW_HOME": str(r)},  # pointed AT the home
        lambda r: {"HOME": str(r.parent), "PERSONALCLAW_HOME": "~/.personalclaw"},  # via ~
        lambda r: {"PATH": "/usr/bin"},  # no HOME at all: the passwd entry, i.e. the real one
        lambda r: {b"HOME": str(r.parent).encode()},  # a bytes-keyed environment
    ],
)
def test_a_home_that_resolves_back_to_the_root_is_not_isolation(guard, root, env) -> None:
    argv = [sys.executable, "-m", "personalclaw"]
    assert _refused(guard, "subprocess.Popen", *_popen(argv, env(root)))


def test_an_inherited_environment_is_the_parent_s(guard, root, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(root.parent))
    monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)
    argv = [sys.executable, "-m", "personalclaw"]
    assert _refused(guard, "subprocess.Popen", *_popen(argv, None))


@pytest.mark.parametrize(
    "argv",
    [
        ["git", "-C", "/repo/src/personalclaw", "status"],  # names the PACKAGE dir, runs git
        [sys.executable, "-m", "pytest", "tests/"],
        [sys.executable, "-c", "print(1)"],
    ],
)
def test_other_children_are_not_this_guard_s_business(guard, root, argv) -> None:
    env = {"HOME": str(root.parent)}
    assert not _refused(guard, "subprocess.Popen", *_popen(argv, env))


def _shimmed(*target: str) -> list[str]:
    """The argv ``personalclaw.sandbox.spawn_shim_argv`` builds around a real command."""
    policy = '{"limits":{"RLIMIT_NOFILE":["hard","hard"]}}'
    return [sys.executable, "-m", real_home_guard.EXEC_SHIM_MODULE, policy, "--", *target]


def test_the_ceiling_shim_is_judged_by_the_command_it_wraps(guard, root) -> None:
    """The shim touches no home and execs its target, so the target is the child that counts.
    Found on the first targeted run: judging the wrapper refused a loop worktree's `git`."""
    env = {"HOME": str(root.parent)}
    assert not _refused(
        guard, "subprocess.Popen", *_popen(_shimmed("git", "worktree", "prune"), env)
    )
    wrapped_cli = _shimmed(sys.executable, "-m", "personalclaw", "acp")
    assert _refused(guard, "subprocess.Popen", *_popen(wrapped_cli, env))
    assert not _refused(guard, "subprocess.Popen", *_popen(_shimmed(), env)), "no target, no child"


def test_the_shim_name_is_the_one_the_sandbox_uses() -> None:
    from personalclaw import sandbox

    assert real_home_guard.EXEC_SHIM_MODULE == sandbox._SHIM_MODULE


def test_posix_spawn_is_checked_too(guard, root) -> None:
    argv = [sys.executable, "-m", "personalclaw"]
    assert _refused(guard, "os.posix_spawn", argv[0], argv, {"HOME": str(root.parent)})


# ── The driver that raises no audit events ──────────────────────────────────────────────


def test_a_driver_without_audit_events_is_wrapped(guard, root, monkeypatch) -> None:
    """pysqlite3 — CI's SQLite driver — raises no audit event, so its connect is wrapped."""
    calls: list[str] = []
    fake = types.ModuleType("pysqlite3")
    fake.connect = lambda database, *a, **k: calls.append(str(database)) or "conn"
    fake.dbapi2 = fake
    monkeypatch.setitem(sys.modules, "pysqlite3", fake)
    real_home_guard._wrap_driver(guard)
    with pytest.raises(RealHomeAccessError):
        fake.connect(str(root / "knowledge.db"))
    assert calls == [], "the refused connection never reached the driver"
    elsewhere = str(root.parent / "elsewhere.db")
    assert fake.connect(":memory:") == "conn"
    assert fake.connect(database=elsewhere) == "conn"
    real_home_guard._wrap_driver(guard)  # idempotent: a second pass does not wrap the wrapper
    assert fake.connect(":memory:") == "conn"
    assert calls == [":memory:", elsewhere, ":memory:"]


# ── The suite's own guard ────────────────────────────────────────────────────────────────


def test_the_suite_runs_under_the_guard(pytestconfig) -> None:
    assert real_home_guard.GUARD.root == Path(os.path.normpath(real_home_guard.REAL_HOME))
    assert real_home_guard.GUARD._installed, "conftest must install the guard before any test"
    assert pytestconfig.pluginmanager.get_plugin("real-home-guard") is not None


def test_no_import_time_constant_holds_the_real_home() -> None:
    """Six modules freeze a home into a constant at import — collection time, before any
    fixture — and this module imported three of them at ITS import. Measured before the
    import window in conftest: every one of these was the developer's real home, and 150+
    tests read the owner's skills, agent hooks and mcp.json through them."""
    real = str(real_home_guard.REAL_HOME)
    frozen = {
        "agent._USER_DIR": _agent._USER_DIR,
        "agent._DEFAULT_HOOKS_DIR": _agent._DEFAULT_HOOKS_DIR,
        "dashboard.handlers.hooks._HOOK_STORE_PATH": _hooks_handlers._HOOK_STORE_PATH,
        "dashboard.handlers.mcp._GLOBAL_MCP_JSON": _mcp_handlers._GLOBAL_MCP_JSON,
    }
    leaked = {name: str(path) for name, path in frozen.items() if str(path).startswith(real)}
    assert not leaked, f"frozen at import to the real home: {leaked}"


# ── End to end: a real pytest session, a fake $HOME ──────────────────────────────────────

_INNER_CONFTEST = """
import real_home_guard

real_home_guard.GUARD.install()


def pytest_configure(config):
    config.pluginmanager.register(real_home_guard.Plugin(real_home_guard.GUARD), "real-home-guard")
"""


def _inner_session(tmp_path: Path, tests: str) -> tuple[subprocess.CompletedProcess, Path, Path]:
    """Run ``tests`` in a child pytest whose ``$HOME`` is a directory under ``tmp_path``."""
    home = tmp_path / "home"
    (home / ".personalclaw").mkdir(parents=True)
    work = tmp_path / "session"
    work.mkdir()
    (work / "conftest.py").write_text(_INNER_CONFTEST)
    (work / "test_inner.py").write_text(textwrap.dedent(tests))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))}
    env["HOME"] = str(home)
    env["PYTHONPATH"] = os.pathsep.join([str(_TESTS_DIR), env.get("PYTHONPATH", "")]).rstrip(
        os.pathsep
    )
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--color=no", "-p", "no:cacheprovider", str(work)],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc, home / ".personalclaw", work


def test_end_to_end_the_test_that_touches_the_home_fails_by_name(tmp_path) -> None:
    proc, home, _work = _inner_session(
        tmp_path,
        """
        import os, sqlite3, subprocess, sys, threading
        from pathlib import Path

        HOME = Path.home() / ".personalclaw"

        def test_innocent():
            assert True

        def test_writes_a_file():
            (HOME / "leak.txt").write_text("x")

        def test_swallows_a_sqlite_connect():
            try:
                sqlite3.connect(HOME / "leak.db")
            except Exception:
                pass

        def test_mkdir_exist_ok_of_the_existing_home():
            HOME.mkdir(parents=True, exist_ok=True)

        def test_a_thread_it_starts_lists_the_home():
            thread = threading.Thread(target=lambda: os.listdir(HOME))
            thread.start()
            thread.join()

        def test_spawns_personalclaw_with_the_real_home():
            subprocess.run([sys.executable, "-m", "personalclaw", "--version"], check=False)
        """,
    )
    out = proc.stdout
    assert proc.returncode == 1, out[-3000:]
    for name in (
        "test_writes_a_file",
        "test_swallows_a_sqlite_connect",
        "test_mkdir_exist_ok_of_the_existing_home",
        "test_a_thread_it_starts_lists_the_home",
        "test_spawns_personalclaw_with_the_real_home",
    ):
        assert f"FAILED test_inner.py::{name}" in out, (name, out[-3000:])
    assert "5 failed, 1 passed" in out, out[-3000:]
    assert "real-home guard: this test touched" in out
    # REFUSED, not merely reported: nothing reached the fake home.
    assert sorted(p.name for p in home.iterdir()) == []


def test_end_to_end_a_thread_that_outlives_its_test_fails_the_session_naming_it(tmp_path) -> None:
    proc, home, work = _inner_session(
        tmp_path,
        """
        import os, threading, time
        from pathlib import Path

        HOME = Path.home() / ".personalclaw"

        def test_leaves_a_worker_behind():
            def later():
                time.sleep(0.5)
                try:
                    os.listdir(HOME)
                except Exception:
                    pass
            threading.Thread(target=later, daemon=True).start()

        def test_bystander():
            time.sleep(2.0)
        """,
    )
    out = proc.stdout
    assert "2 passed" in out, "neither test is blamed: the bystander is innocent" + out[-3000:]
    assert proc.returncode == 1, "…and the SESSION fails, because a refusal belongs to no test"
    report = (work / real_home_guard.REPORT_RELPATH).read_text()
    for text in (report, out):
        assert "real-home guard FAILED" in text
        assert "started by test_inner.py::test_leaves_a_worker_behind" in text
        assert "of test_inner.py::test_bystander, which did not start this thread" in text
    assert sorted(p.name for p in home.iterdir()) == []


def test_end_to_end_a_clean_session_still_writes_its_verdict(tmp_path) -> None:
    proc, _home, work = _inner_session(tmp_path, "def test_fine():\n    assert True\n")
    assert proc.returncode == 0, proc.stdout[-2000:]
    assert "1 test ran; none touched" in (work / real_home_guard.REPORT_RELPATH).read_text()


# ── The report file ──────────────────────────────────────────────────────────────────────


def test_the_session_verdict_is_persisted_where_ci_uploads_it(tmp_path) -> None:
    rootdir = tmp_path / "rootdir"
    rootdir.mkdir()  # no reports/ yet — the writer must create it
    written = real_home_guard.write_report(rootdir, "real-home guard FAILED: ...")
    assert written == rootdir / real_home_guard.REPORT_RELPATH
    assert written.read_text() == "real-home guard FAILED: ...\n"


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores the mode bits this test relies on",
)
def test_an_unwritable_rootdir_yields_none_rather_than_raising(tmp_path) -> None:
    """A failed write must not stack a second, misleading red on the one being reported."""
    rootdir = tmp_path / "readonly"
    rootdir.mkdir()
    rootdir.chmod(0o500)
    try:
        assert real_home_guard.write_report(rootdir, "real-home guard FAILED: ...") is None
    finally:
        rootdir.chmod(0o700)
