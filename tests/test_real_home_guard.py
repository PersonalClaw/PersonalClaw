"""The real-home guard must be able to FAIL — and fail the RIGHT test.

Nothing here touches the developer's real ``~/.personalclaw``: proving a leak detector works
by leaking is not a proof, it is the defect the detector exists to prevent. The unit half
drives a :class:`real_home_guard.Guard` over a throwaway root, feeding its hook audit events
shaped exactly as CPython raises them. The end-to-end half runs a real pytest session in a
child process whose ``HOME`` is fake, so real ``open``, ``sqlite3.connect``, ``mkdir`` and
thread calls go through the real hook, the real charging and the real reports.
"""

from __future__ import annotations

import json
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

pytestmark = pytest.mark.security


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
        # Python code that loads the package resolves its home like the CLI does.
        [
            sys.executable,
            "-c",
            "import json;from personalclaw.vector_memory import VectorMemoryStore",
        ],
        ["python3", "-c", "import personalclaw"],
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
        [sys.executable, "-c", "print('personalclaw')"],  # names it, loads nothing
        ["/bin/sh", "-c", "echo from personalclaw import x"],  # not Python
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


def test_a_test_that_chose_no_home_resolves_its_own_both_ways(monkeypatch) -> None:
    """Both resolvers answer the test's own home: the one that names it (never made) and the one
    that makes it. Only the second was redirected, and a child handed a path built from the first
    made the real home: the namespace sandbox's launcher, whose owner-only fence names it."""
    from personalclaw.config.loader import config_dir, resolve_config_dir
    from personalclaw.owner_only import owner_only_paths

    monkeypatch.delenv("PERSONALCLAW_HOME", raising=False)
    named = resolve_config_dir()

    assert real_home_guard.GUARD.owns(named) is None, f"the real home, resolved: {named}"
    assert not named.exists(), "named, not made: resolving must not create the home"
    assert owner_only_paths()[0].parent == named, "the launcher's fence names the test's home"
    assert config_dir() == named and named.is_dir()


def test_a_home_an_environment_names_is_answered_as_asked(tmp_path) -> None:
    """A call that names its own environment asks where another process's home is."""
    from personalclaw.config.loader import resolve_config_dir

    elsewhere = tmp_path / "another-gateway-home"
    assert resolve_config_dir({"PERSONALCLAW_HOME": str(elsewhere)}) == elsewhere.resolve()


# ── End to end: a real pytest session, a fake $HOME ──────────────────────────────────────

#: The inner session's conftest. The user database is stubbed, before the guard is imported, to
#: name the session's planted real home (``PC_PLANTED_REAL_HOME``), so the guard reads it as it
#: reads the real one: the guard takes the real home from there, never from ``$HOME``.
_INNER_CONFTEST = """
import os, pwd

_real = pwd.getpwuid


def _planted(uid):
    fields = list(_real(uid))
    fields[5] = os.environ["PC_PLANTED_REAL_HOME"]
    return pwd.struct_passwd(fields)


pwd.getpwuid = _planted

import real_home_guard

real_home_guard.GUARD.install()


def pytest_configure(config):
    config.pluginmanager.register(real_home_guard.Plugin(real_home_guard.GUARD), "real-home-guard")
"""


def _inner_session(
    tmp_path: Path, tests: str, *, made: bool = True
) -> tuple[subprocess.CompletedProcess, Path, Path]:
    """Run ``tests`` in a child pytest whose ``$HOME`` and planted real home are a directory under
    ``tmp_path``, with its ``.personalclaw`` already there unless *made* is false."""
    home = tmp_path / "home"
    home.mkdir()
    if made:
        (home / ".personalclaw").mkdir()
    work = tmp_path / "session"
    work.mkdir()
    (work / "conftest.py").write_text(_INNER_CONFTEST)
    (work / "test_inner.py").write_text(textwrap.dedent(tests))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))}
    env["HOME"] = str(home)
    env["PC_PLANTED_REAL_HOME"] = str(home)
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


def test_end_to_end_a_loop_step_left_on_its_executor_names_the_test_whose_loop_ran_it(
    tmp_path,
) -> None:
    """The breadth canary leg's shape (#4768): a test's gateway loop handed a step to the loop's
    default executor, the loop's task was cancelled as the test ended, and the loop was closed
    the way pytest-asyncio closes it, which does not wait for the executor. The executor's
    thread (``asyncio_0``) ran the step on into the next test's setup."""
    proc, home, work = _inner_session(
        tmp_path,
        """
        import asyncio, os, time
        from pathlib import Path

        HOME = Path.home() / ".personalclaw"

        def _step():
            time.sleep(0.5)
            try:
                os.mkdir(HOME)
            except Exception:
                pass

        def test_its_loop_hands_a_step_to_the_executor():
            loop = asyncio.new_event_loop()

            async def tick():
                await loop.run_in_executor(None, _step)

            task = loop.create_task(tick())
            loop.run_until_complete(asyncio.sleep(0.05))
            task.cancel()
            loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
            loop.close()

        def test_bystander():
            time.sleep(2.0)
        """,
        made=False,
    )
    out = proc.stdout
    assert "2 passed" in out, "neither test is blamed: the bystander is innocent" + out[-3000:]
    assert proc.returncode == 1, "…and the SESSION fails, because a refusal belongs to no test"
    report = (work / real_home_guard.REPORT_RELPATH).read_text()
    for text in (report, out):
        assert "os.mkdir:" in text
        assert (
            "on thread 'asyncio_0', started by "
            "test_inner.py::test_its_loop_hands_a_step_to_the_executor" in text
        ), text[-3000:]
        assert "of test_inner.py::test_bystander, which did not start this thread" in text
    assert not home.exists(), "refused, so the step made no home"


def test_end_to_end_a_clean_session_still_writes_its_verdict(tmp_path) -> None:
    proc, _home, work = _inner_session(tmp_path, "def test_fine():\n    assert True\n")
    assert proc.returncode == 0, proc.stdout[-2000:]
    assert "1 test ran; none touched" in (work / real_home_guard.REPORT_RELPATH).read_text()


def test_end_to_end_a_child_that_makes_the_home_fails_the_session_naming_its_test(tmp_path) -> None:
    """No hook in this process sees what a child does. A home that did not exist as the run
    started and exists as it ends fails the session, naming the test during which it appeared and
    the child that test started: here a shell, which no spawn check counts as PersonalClaw."""
    proc, home, work = _inner_session(
        tmp_path,
        """
        import subprocess

        def test_innocent():
            assert True

        def test_a_child_makes_the_home():
            subprocess.run(["/bin/sh", "-c", 'mkdir "$HOME/.personalclaw"'], check=True)

        def test_after_it():
            assert True
        """,
        made=False,
    )
    out = proc.stdout
    assert "3 passed" in out, "no test is failed for it: a child's act has no stack" + out[-3000:]
    assert proc.returncode == 1, "…and the SESSION fails, because the home is there now"
    assert home.is_dir(), "control: the child did make the home"
    report = (work / real_home_guard.REPORT_RELPATH).read_text()
    for text in (report, out):
        assert "appeared during this run" in text
        assert "test_inner.py::test_a_child_makes_the_home, which started: /bin/sh -c" in text
        assert "test_innocent" not in text and "test_after_it" not in text


def test_end_to_end_a_home_removed_again_before_the_end_still_fails_the_session(tmp_path) -> None:
    """Whoever finds the stray folder removes it: the test during which it appeared is still
    named, and the session still fails."""
    proc, home, work = _inner_session(
        tmp_path,
        """
        import subprocess

        def test_a_child_makes_the_home():
            subprocess.run(["/bin/sh", "-c", 'mkdir "$HOME/.personalclaw"'], check=True)

        def test_someone_removes_it():
            subprocess.run(["/bin/sh", "-c", 'rmdir "$HOME/.personalclaw"'], check=True)
        """,
        made=False,
    )
    out = proc.stdout
    assert "2 passed" in out, out[-3000:]
    assert not home.exists(), "control: the home is gone again as the run ends"
    assert proc.returncode == 1, "…and the SESSION fails, because it appeared during a test"
    report = (work / real_home_guard.REPORT_RELPATH).read_text()
    for text in (report, out):
        assert "appeared during this run" in text
        assert "test_inner.py::test_a_child_makes_the_home, which started: /bin/sh -c" in text
        assert "test_someone_removes_it" not in text


def test_end_to_end_a_session_that_makes_no_home_passes(tmp_path) -> None:
    proc, home, work = _inner_session(tmp_path, "def test_fine():\n    assert True\n", made=False)
    assert proc.returncode == 0, proc.stdout[-2000:]
    assert not home.exists()
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


# ── The real home is the user database's, never $HOME ────────────────────────────────────


def _planted_passwd(monkeypatch, home: Path | str) -> None:
    """Make the user database name *home* as this account's home, for the test's duration."""
    import pwd

    real = pwd.getpwuid

    def planted(uid: int):
        fields = list(real(uid))
        fields[5] = str(home)
        return pwd.struct_passwd(fields)

    monkeypatch.setattr(pwd, "getpwuid", planted)


def test_the_real_home_is_the_user_databases_entry_whatever_HOME_says(tmp_path, monkeypatch):
    planted = tmp_path / "real-home"
    _planted_passwd(monkeypatch, planted)
    monkeypatch.setenv("HOME", str(tmp_path / "scratch-home"))
    assert real_home_guard.passwd_home() == planted


#: Loads the guard in a child whose user database names ``PC_PLANTED_REAL_HOME`` (or has no entry
#: when ``PC_NO_ENTRY`` is set), and prints what it guards beside what a guard built as it was
#: before this took the home from the user database (``Path.home() / ".personalclaw"``) guards,
#: and which of the paths ``PC_ASK_OWNS`` lists the guard owns.
_LOAD_WITH_A_PLANTED_ENTRY = """\
import json, os, pwd, sys
from pathlib import Path

_real = pwd.getpwuid


def _planted(uid):
    if os.environ.get("PC_NO_ENTRY"):
        raise KeyError(f"getpwuid(): uid not found: {uid}")
    fields = list(_real(uid))
    fields[5] = os.environ["PC_PLANTED_REAL_HOME"]
    return pwd.struct_passwd(fields)


pwd.getpwuid = _planted
sys.path.insert(0, sys.argv[1])
import real_home_guard


def folders(guard):
    return [str(guard.root), sorted(guard._prefixes), sorted(guard._needles), guard._home]


before = Path.home() / ".personalclaw"
asked = json.loads(os.environ.get("PC_ASK_OWNS", "[]"))
print(json.dumps({
    "now": [str(real_home_guard.REAL_HOME), folders(real_home_guard.GUARD)],
    "before": [str(before), folders(real_home_guard.Guard(before))],
    "owns": [real_home_guard.GUARD.owns(path) for path in asked],
}))
"""


def _loaded_with(
    tmp_path: Path, home: str, planted: str | None, *, owns: tuple[str, ...] = ()
) -> subprocess.CompletedProcess:
    """Load the guard in a child whose ``$HOME`` is *home* and whose user database names
    *planted*, or has no entry when *planted* is ``None``."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))}
    env.update(HOME=home, PC_ASK_OWNS=json.dumps(list(owns)))
    if planted is None:
        env["PC_NO_ENTRY"] = "1"
    else:
        env["PC_PLANTED_REAL_HOME"] = planted
    return subprocess.run(
        [sys.executable, "-c", _LOAD_WITH_A_PLANTED_ENTRY, str(_TESTS_DIR)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


#: The ways a ``$HOME`` can name the folder the user database names as the real home.
_SPELLINGS = [
    "as the user database does",
    "with a slash",
    "by a link",
    "by a link, and the user database by another",
    "in another letter case",
    "by a link, to a home not made yet",
]


def _the_real_home_spelled(tmp_path: Path, spelling: str) -> tuple[str, str]:
    """A ``$HOME`` and a user database entry under *tmp_path* that name the same folder, the
    ``$HOME`` spelled as *spelling* says. Skips a spelling this file system can't give."""
    real = tmp_path / "real-home"
    if spelling == "by a link, to a home not made yet":
        (tmp_path / "homes").mkdir()
        (tmp_path / "homes-link").symlink_to(tmp_path / "homes")
        return str(tmp_path / "homes-link" / "real-home"), str(tmp_path / "homes" / "real-home")
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
def test_where_HOME_is_the_real_home_the_guard_watches_what_it_did_before(tmp_path, spelling):
    """Unchanged where it already worked: a run whose ``$HOME`` names the real home, however it
    spells it, guards byte for byte the folders it guarded when it took them from ``$HOME``."""
    home, entry = _the_real_home_spelled(tmp_path, spelling)
    ran = _loaded_with(tmp_path, home, entry)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    said = json.loads(ran.stdout)
    assert said["now"] == said["before"]


@pytest.mark.parametrize("where", ["beside the real home", "under the real home"])
def test_where_HOME_is_a_scratch_folder_the_guard_watches_the_user_databases_home(tmp_path, where):
    """``under the real home`` is the runner's own layout: a job's scratch home is under the
    runner's temporary folder (``/home/runner/work/_temp``), which is under the real home."""
    planted = tmp_path / "real-home"
    planted.mkdir()
    scratch_home = {
        "beside the real home": tmp_path / "scratch-home",
        "under the real home": planted.joinpath(
            "repo", ".local", "run-job", "r1", "jobs", "j", "home"
        ),
    }[where]
    scratch_home.mkdir(parents=True)
    leak = planted / ".personalclaw" / "config.json"
    own = scratch_home / ".personalclaw" / "config.json"
    ran = _loaded_with(tmp_path, str(scratch_home), str(planted), owns=(str(leak), str(own)))
    assert ran.returncode == 0, ran.stdout + ran.stderr
    said = json.loads(ran.stdout)
    real_home, (root, prefixes, _needles, home) = said["now"]
    assert real_home == root == str(planted / ".personalclaw")
    assert home == str(planted)
    assert not any(str(scratch_home) in prefix for prefix in prefixes)
    # A write to the real home by its absolute path is the guard's; one to the job's own is not.
    assert said["owns"] == [str(leak), None]


#: What an account's entry can hold that names no home: none at all, an empty home, a relative one.
_NO_HOME = [None, "", "relative/home"]
_NO_HOME_IDS = ["no entry", "an empty home", "a relative home"]


@pytest.mark.parametrize("entry", _NO_HOME, ids=_NO_HOME_IDS)
def test_a_run_whose_user_database_names_no_home_stops_as_the_guard_loads(tmp_path, entry):
    """No silent fallback to ``$HOME``, nor to the folder the run started in: the guard can't be
    loaded at all."""
    scratch_home = tmp_path / "scratch-home"
    scratch_home.mkdir()
    ran = _loaded_with(tmp_path, str(scratch_home), entry)
    assert ran.returncode != 0, ran.stdout
    assert "and $HOME can't stand in for it (tests/real_home_guard.py)" in ran.stderr
    assert ran.stdout == ""


@pytest.mark.parametrize("entry", _NO_HOME, ids=_NO_HOME_IDS)
def test_an_account_whose_user_database_entry_names_no_home_stops_the_run(
    tmp_path, monkeypatch, entry
) -> None:
    import pwd

    def no_entry(uid: int):
        raise KeyError(f"getpwuid(): uid not found: {uid}")

    if entry is None:
        monkeypatch.setattr(pwd, "getpwuid", no_entry)
    else:
        _planted_passwd(monkeypatch, entry)
    # A guard that fell back to $HOME would find one, and one that took a relative home as it
    # stands would guard a folder under wherever the run started.
    monkeypatch.setenv("HOME", str(tmp_path / "scratch-home"))
    monkeypatch.chdir(tmp_path)
    why = "no entry in the user database" if entry is None else f"names {entry!r} as user id"
    with pytest.raises(RuntimeError, match=rf"{why}.*\$HOME can't stand in for it"):
        real_home_guard.passwd_home()


#: Loads the suite's own conftest, as pytest does, in a child whose ``$HOME`` is a scratch folder
#: and whose user database names ``PC_PLANTED_REAL_HOME``, and prints the home the run started
#: with and whether a test chose a home: one that chose none, one that chose one by ``$HOME``,
#: and one that chose one by ``$PERSONALCLAW_HOME``.
_LOAD_THE_SUITES_CONFTEST = """\
import json, os, pwd, sys

_real = pwd.getpwuid


def _planted(uid):
    fields = list(_real(uid))
    fields[5] = os.environ["PC_PLANTED_REAL_HOME"]
    return pwd.struct_passwd(fields)


pwd.getpwuid = _planted
sys.path.insert(0, sys.argv[1])
import conftest

try:
    started = os.environ["HOME"]
    said = {
        "the home the run started with": str(conftest._STARTING_HOME),
        "chose none": conftest._caller_chose_a_home(),
    }
    os.environ["HOME"] = sys.argv[2]
    said["chose one by $HOME"] = conftest._caller_chose_a_home()
    os.environ["HOME"] = started
    os.environ["PERSONALCLAW_HOME"] = sys.argv[3]
    said["chose one by $PERSONALCLAW_HOME"] = conftest._caller_chose_a_home()
    print(json.dumps(said))
finally:
    conftest.git_helper_guard.close()
"""


def test_a_run_started_with_a_scratch_HOME_tells_a_test_that_chose_a_home_from_one_that_did_not(
    tmp_path,
):
    """conftest's ``_caller_chose_a_home`` judges ``$HOME`` against the home the run started with,
    not the guard's: in a run started with a scratch ``$HOME`` and a user database naming another
    home, as a pytest job ``tools/ci/run.py`` starts is, a home no test chose is still redirected,
    and one a test chose by ``$HOME`` or ``$PERSONALCLAW_HOME`` passes through."""
    planted = tmp_path / "real-home"
    planted.mkdir()
    scratch_home = tmp_path / "scratch-home"
    scratch_home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))}
    env.update(HOME=str(scratch_home), PC_PLANTED_REAL_HOME=str(planted))
    ran = subprocess.run(
        [sys.executable, "-B", "-c", _LOAD_THE_SUITES_CONFTEST, str(_TESTS_DIR)]
        + [str(tmp_path / "a-home-of-its-own"), str(tmp_path / "a-personalclaw-home-of-its-own")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr[-3000:]
    assert json.loads(ran.stdout.splitlines()[-1]) == {
        "the home the run started with": str(scratch_home),
        "chose none": False,
        "chose one by $HOME": True,
        "chose one by $PERSONALCLAW_HOME": True,
    }


def test_end_to_end_a_scratch_HOME_does_not_retarget_the_guard(tmp_path) -> None:
    """A run whose ``$HOME`` is a scratch folder still guards the real home, and a test that
    writes there through its absolute path is refused, and fails by name."""
    real = tmp_path / "real-home"
    (real / ".personalclaw").mkdir(parents=True)
    scratch_home = tmp_path / "scratch-home"
    scratch_home.mkdir()
    work = tmp_path / "session"
    work.mkdir()
    (work / "conftest.py").write_text(_INNER_CONFTEST)
    (work / "test_inner.py").write_text(textwrap.dedent(f"""
            from pathlib import Path

            REAL = Path({str(real / ".personalclaw")!r})

            def test_writes_the_real_home_by_its_path():
                (REAL / "leak.txt").write_text("x")

            def test_writes_its_own_home():
                (Path.home() / "fine.txt").write_text("x")
            """))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))}
    env["HOME"] = str(scratch_home)
    env["PC_PLANTED_REAL_HOME"] = str(real)
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
    out = proc.stdout
    assert proc.returncode == 1, out[-3000:]
    assert "FAILED test_inner.py::test_writes_the_real_home_by_its_path" in out, out[-3000:]
    assert "1 failed, 1 passed" in out, out[-3000:]
    assert f"refused open under the real home {real / '.personalclaw' / 'leak.txt'}" in out
    # REFUSED, not merely reported: the planted real home is as it was.
    assert list((real / ".personalclaw").iterdir()) == []
    assert (scratch_home / "fine.txt").read_text() == "x"
