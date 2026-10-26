"""A test that leaves process-global state changed is named, as itself (#3949).

The guard is ``tests/state_leak_guard.py``.

Planted tests each leave one thing behind: a live thread, a changed working directory, a signal
handler, a child process still running, a socket still listening, an entry in a fenced registry,
or a file written outside the run's temporary folders.
They run through this suite's own ``conftest`` in a fresh pytest, so what is read is what the real
guard writes: ``reports/state-leaks.json`` in report mode, and a failure of the leaking test under
``--strict-state``. The tests after each leaker show the working directory and the handler given
back, and the fenced entry restored by its fence after the guard saw it. The guard's own rules
(the grace a thread gets, thread pools, the allowlist) are held in-process, on a guard of their own.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path

import nested_pytest
import pytest
import state_leak_guard as slg

_TESTS_DIR = Path(__file__).resolve().parent

_LEAKS = """
    import os
    import signal
    import socket
    import subprocess
    import sys
    import threading
    import time

    START = os.getcwd()
    ORIGINAL = signal.getsignal(signal.SIGUSR1)
    LISTENERS = []


    def test_clean():
        pass


    def test_leaves_a_thread():
        threading.Thread(target=time.sleep, args=(3,), name="planted-leak").start()


    def test_changes_directory(tmp_path):
        os.chdir(tmp_path)


    def test_sees_the_directory_given_back():
        assert os.getcwd() == START


    def test_installs_a_signal_handler():
        signal.signal(signal.SIGUSR1, lambda *a: None)


    def test_sees_the_handler_given_back():
        assert signal.getsignal(signal.SIGUSR1) is ORIGINAL


    def test_leaves_a_child():
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(8)"])


    def test_leaves_a_port_listening():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen()
        LISTENERS.append(s)


    def test_registers_into_a_fenced_registry():
        from personalclaw import notification_kinds

        notification_kinds._REGISTRY[("planted", "kind")] = object()


    def test_sees_the_fenced_entry_restored():
        from personalclaw import notification_kinds

        assert ("planted", "kind") not in notification_kinds._REGISTRY


    def test_writes_outside_its_temporary_folders(tmp_path):
        (tmp_path / "inside.txt").write_text("a test's own folder")
        with open(os.path.join(START, "written-outside.txt"), "w") as handle:
            handle.write("left in the run's root")


    def test_leaves_an_event_router_attached():
        from personalclaw import event_triggers

        event_triggers.attach(lambda event: None)


    def test_sees_no_event_router():
        from personalclaw import event_triggers

        assert not event_triggers.router_attached()
"""

#: The kind each leaker's record carries, whether strict mode fails it, and whether a fence
#: restores it.
_EXPECTED = {
    "test_leaves_a_thread": ("thread", True, False),
    "test_changes_directory": ("working directory", True, False),
    "test_installs_a_signal_handler": ("signal handler", True, False),
    "test_leaves_a_child": ("child process", True, False),
    "test_leaves_a_port_listening": ("listening socket", True, False),
    "test_registers_into_a_fenced_registry": ("fenced registry", False, True),
    "test_writes_outside_its_temporary_folders": ("file written", False, False),
    "test_leaves_an_event_router_attached": ("fenced registry", False, True),
}


def _pytest(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """This suite's conftest, as a plugin, over the planted files in *tmp_path*."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(_TESTS_DIR), env.get("PYTHONPATH", "")]).rstrip(
        os.pathsep
    )
    # A temporary folder of the run's own inside the planted root, so the root itself is outside
    # every temporary folder and a file written there is one the guard names.
    (tmp_path / "tmp").mkdir(exist_ok=True)
    env["TMPDIR"] = str(tmp_path / "tmp")
    return subprocess.run(
        [
            *nested_pytest.argv(tmp_path / "tmp"),
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            "-p",
            "conftest",
            "-q",
            "--color=no",
            *args,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _report(tmp_path: Path) -> dict:
    return json.loads((tmp_path / slg.REPORT).read_text(encoding="utf-8"))


def _by_test(report: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for leak in report["leaks"]:
        out.setdefault(leak["nodeid"].rsplit("::", 1)[-1], []).append(leak)
    return out


def test_report_mode_names_each_leaker_and_fails_none(tmp_path: Path) -> None:
    (tmp_path / "test_leaks.py").write_text(textwrap.dedent(_LEAKS))
    proc = _pytest(tmp_path, "-p", "no:xdist", "test_leaks.py")
    out = proc.stdout[-4000:] + proc.stderr[-2000:]
    assert proc.returncode == 0, out
    assert "13 passed" in proc.stdout and "error" not in proc.stdout.lower(), out
    report = _report(tmp_path)
    assert report["strict"] is False
    found = _by_test(report)
    assert set(found) == set(_EXPECTED), sorted(found)
    for name, (kind, strict, fenced) in _EXPECTED.items():
        [leak] = found[name]
        assert leak["kind"] == kind, leak
        assert leak["strict"] is strict, leak
        assert leak["fenced"] is fenced, leak
        assert leak["worker"] == "main"
        assert leak["detail"], leak
    # The fenced entry shows, though its fence restored it before the next test (which passed).
    assert "notification_kinds" in found["test_registers_into_a_fenced_registry"][0]["detail"]
    assert "event_router" in found["test_leaves_an_event_router_attached"][0]["detail"]
    # The file in the run's root is named; the one in the test's own folder is not.
    [written] = found["test_writes_outside_its_temporary_folders"]
    assert "written-outside.txt" in written["detail"] and "inside.txt" not in written["detail"]
    # Each test's position in its process's order: 1 for the first, as the file lists them.
    order = list(dict.fromkeys(name for name in _names_in(_LEAKS)))
    for name, leaks in found.items():
        assert leaks[0]["position"] == order.index(name) + 1, (name, leaks[0])
    assert report["tests"] == 13 and report["guard_seconds"] >= 0
    assert "state leaks: 8 recorded" in proc.stdout, out


def _names_in(source: str) -> list[str]:
    return [
        line.split("(")[0][4:]
        for line in textwrap.dedent(source).splitlines()
        if line.startswith("def test_")
    ]


def test_strict_mode_fails_each_unfenced_leaker_but_an_allowlisted_one(tmp_path: Path) -> None:
    (tmp_path / "test_leaks.py").write_text(textwrap.dedent(_LEAKS))
    allow = tmp_path / "allow.toml"
    allow.write_text(
        '[[entry]]\nnodeid = "test_leaks.py::test_installs_a_signal_handler"\nissue = 4242\n'
    )
    proc = _pytest(
        tmp_path,
        "-p",
        "no:xdist",
        "--strict-state",
        "--state-leak-allowlist",
        str(allow),
        "-rE",
        "test_leaks.py",
    )
    out = proc.stdout[-6000:] + proc.stderr[-2000:]
    assert proc.returncode == 1, out
    assert "13 passed" in proc.stdout and "4 errors" in proc.stdout, out
    for name in (
        "test_leaves_a_thread",
        "test_changes_directory",
        "test_leaves_a_child",
        "test_leaves_a_port_listening",
    ):
        assert f"ERROR test_leaks.py::{name}" in proc.stdout, (name, out)
    for name in (
        "test_installs_a_signal_handler",
        "test_registers_into_a_fenced_registry",
        "test_leaves_an_event_router_attached",
        "test_writes_outside_its_temporary_folders",
        "test_clean",
    ):
        assert f"ERROR test_leaks.py::{name}" not in proc.stdout, (name, out)
    report = _report(tmp_path)
    assert report["strict"] is True
    [allowed] = _by_test(report)["test_installs_a_signal_handler"]
    assert allowed["allowlisted"] == 4242


def test_each_record_names_its_real_xdist_worker_and_place_in_its_order(tmp_path: Path) -> None:
    lines = ["import os", "", "", "def test_a(tmp_path):", "    os.chdir(tmp_path)", ""]
    for n in range(1, 6):
        lines += [f"def test_b{n}(tmp_path):", "    os.chdir(tmp_path)", ""]
    (tmp_path / "test_spread.py").write_text("\n".join(lines))
    proc = _pytest(tmp_path, "-n", "2", "test_spread.py")
    out = proc.stdout[-4000:] + proc.stderr[-2000:]
    assert proc.returncode == 0, out
    leaks = _report(tmp_path)["leaks"]
    assert len(leaks) == 6, leaks
    workers = {leak["worker"] for leak in leaks}
    assert workers <= {"gw0", "gw1"} and workers, workers
    for worker in workers:
        positions = sorted(leak["position"] for leak in leaks if leak["worker"] == worker)
        assert positions == list(range(1, len(positions) + 1)), (worker, positions)


# ── the guard's own rules, on a guard of their own ─────────────────────────────────────────────


def _guard(**kw) -> slg.Guard:
    kw.setdefault("listening", lambda: frozenset())
    kw.setdefault("fenced", lambda: {})
    return slg.Guard(**kw)


def _leaks(guard: slg.Guard) -> list[slg.Leak]:
    return list(guard.leaks)


def test_a_thread_that_ends_within_the_grace_is_no_leak() -> None:
    guard = _guard()
    guard.start("t::quick", 1)
    before = guard.before()
    threading.Thread(target=time.sleep, args=(0.05,), name="quick").start()
    assert guard.after(before, strict=False, allowlist={}) == ""
    assert _leaks(guard) == []


def test_a_thread_pool_is_recorded_but_never_fails() -> None:
    guard = _guard()
    guard.start("t::pool", 1)
    before = guard.before()
    done = threading.Event()
    pool = threading.Thread(target=done.wait, name="ThreadPoolExecutor-9_0")
    pool.start()
    try:
        assert guard.after(before, strict=True, allowlist={}) == ""
        [leak] = _leaks(guard)
        assert leak.kind == "thread pool" and leak.strict is False
    finally:
        done.set()
        pool.join()


def test_a_changed_directory_is_given_back(tmp_path: Path) -> None:
    guard = _guard()
    guard.start("t::cd", 1)
    start = os.getcwd()
    before = guard.before()
    os.chdir(tmp_path)
    failure = guard.after(before, strict=True, allowlist={})
    assert os.getcwd() == start
    assert "working directory" in failure and str(tmp_path) in failure


def test_a_signal_handler_is_given_back() -> None:
    guard = _guard()
    guard.start("t::sig", 1)
    original = signal.getsignal(signal.SIGUSR2)
    before = guard.before()
    signal.signal(signal.SIGUSR2, lambda *a: None)
    assert "signal handler" in guard.after(before, strict=True, allowlist={})
    assert signal.getsignal(signal.SIGUSR2) is original


def test_an_allowlisted_test_is_recorded_and_not_failed(tmp_path: Path) -> None:
    guard = _guard()
    guard.start("t::listed", 1)
    before = guard.before()
    os.chdir(tmp_path)
    assert guard.after(before, strict=True, allowlist={"t::listed": 7}) == ""
    [leak] = _leaks(guard)
    assert leak.allowlisted == 7


def test_a_fenced_change_is_recorded_as_fenced() -> None:
    state = {"kinds": frozenset({"a"})}
    guard = _guard(fenced=lambda: dict(state))
    guard.start("t::fenced", 1)
    before = guard.fenced_before()
    state["kinds"] = frozenset({"a", "b"})
    guard.fenced_after(before)
    [leak] = _leaks(guard)
    assert leak.fenced and not leak.strict and "kinds" in leak.detail and "b" in leak.detail


def test_a_new_listening_port_is_a_leak() -> None:
    ports = {"now": frozenset({8080})}
    guard = _guard(listening=lambda: ports["now"])
    guard.start("t::port", 1)
    before = guard.before()
    ports["now"] = frozenset({8080, 9099})
    assert "9099" in guard.after(before, strict=True, allowlist={})


def test_a_thread_a_fixture_started_is_not_the_tests() -> None:
    """A module's or a session's fixture may start a thread and keep it: the reading taken once
    setup is done counts it as already there."""
    guard = _guard()
    guard.start("t::fixture-thread", 1)
    before = guard.before()
    done = threading.Event()
    kept = threading.Thread(target=done.wait, name="a-fixtures-server")
    kept.start()
    try:
        settled = guard.settled(before)
        assert guard.after(settled, strict=True, allowlist={}) == ""
        assert _leaks(guard) == []
    finally:
        done.set()
        kept.join()


def test_a_child_started_outside_a_test_is_not_held() -> None:
    """Only a child started while a test runs is held, so the guard keeps no reference to a
    process between tests."""
    children = slg.Children()
    children.install()
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert children._started == []
    children.open()
    inside = subprocess.Popen([sys.executable, "-c", "pass"])
    assert inside in children._started
    inside.wait()
    assert children.close() == []


# ── the files a test writes ──────────────────────────────────────────────────────────────────


def _writes(tmp_path: Path) -> tuple[slg.Writes, Path, Path]:
    inside, outside = tmp_path / "temporary", tmp_path / "elsewhere"
    inside.mkdir()
    outside.mkdir()
    writes = slg.Writes([str(inside)])
    writes.install()
    return writes, inside, outside


def test_each_write_outside_the_roots_is_named_once_and_none_inside(tmp_path: Path) -> None:
    writes, inside, outside = _writes(tmp_path)
    (outside / "kept.txt").write_text("before the window")
    writes.open()
    (inside / "mine.txt").write_text("a test's own")
    (outside / "a.txt").write_text("one")
    (outside / "a.txt").write_text("again")
    with open(outside / "b.log", "a") as handle:
        handle.write("appended")
    fd = os.open(outside / "c.bin", os.O_WRONLY | os.O_CREAT)
    os.close(fd)
    (outside / "made").mkdir()
    os.replace(outside / "kept.txt", outside / "moved.txt")
    (outside / "moved.txt").unlink()
    (outside / "kept.txt").exists()
    (inside / "mine.txt").read_text()
    names = [Path(p).name for p in writes.close()]
    assert names == ["a.txt", "b.log", "c.bin", "made", "kept.txt", "moved.txt"], names


def test_nothing_is_kept_while_no_window_is_open(tmp_path: Path) -> None:
    writes, _inside, outside = _writes(tmp_path)
    (outside / "a.txt").write_text("no window")
    writes.open()
    assert writes.close() == []
    (outside / "b.txt").write_text("closed again")
    writes.open()
    assert writes.close() == []


def test_a_childs_writes_are_the_childs_and_a_threads_are_its_process(tmp_path: Path) -> None:
    """The hook is the process's own: a child it starts writes unseen, and so no xdist worker's
    write is charged to another worker's test. A thread of the process is the process's."""
    writes, _inside, outside = _writes(tmp_path)
    writes.open()
    code = f"open({str(outside / 'child.txt')!r}, 'w').write('x')"
    subprocess.run([sys.executable, "-c", code], check=True)
    thread = threading.Thread(target=(outside / "thread.txt").write_text, args=("x",))
    thread.start()
    thread.join()
    assert [Path(p).name for p in writes.close()] == ["thread.txt"]


def test_a_root_reached_through_a_link_is_still_the_root(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "linked").symlink_to(real)
    writes = slg.Writes([str(tmp_path / "linked")])
    writes.install()
    writes.open()
    (real / "a.txt").write_text("through the real path")
    (tmp_path / "linked" / "b.txt").write_text("through the link")
    assert writes.close() == []


def test_a_guard_records_the_writes_but_never_fails_them(tmp_path: Path) -> None:
    writes, _inside, outside = _writes(tmp_path)
    guard = _guard(writes=writes)
    guard.start("t::writes", 1)
    before = guard.settled(guard.before())
    for n in range(slg.SHOWN_PATHS + 2):
        (outside / f"f{n}.txt").write_text("x")
    assert guard.after(before, strict=True, allowlist={}) == ""
    [leak] = _leaks(guard)
    assert (leak.kind, leak.strict, leak.fenced) == ("file written", False, False)
    assert f"wrote {slg.SHOWN_PATHS + 2} path(s)" in leak.detail and "and 2 more" in leak.detail


def test_the_run_may_write_its_reports_its_cache_and_its_temporary_folders(tmp_path: Path) -> None:
    class _Config:
        rootpath = tmp_path

        @staticmethod
        def getoption(name, default=None):
            return str(tmp_path / "named-basetemp") if name == "basetemp" else default

    roots = slg.temporary_roots(_Config())
    for want in ("reports", ".pytest_cache", "named-basetemp"):
        assert str(tmp_path / want) in roots, roots
    assert os.path.dirname(os.devnull) in roots and roots[0] == tempfile.gettempdir()


# ── the allowlist ────────────────────────────────────────────────────────────────────────────


def _entry(nodeid: str = "tests/test_x.py::test_y", issue: object = 12) -> str:
    return f'[[entry]]\nnodeid = "{nodeid}"\nissue = {json.dumps(issue)}\n'


def test_the_shipped_allowlist_reads_and_is_within_its_cap() -> None:
    listed = slg.load_allowlist(slg.ALLOWLIST)
    assert len(listed) <= slg.CAP


@pytest.mark.parametrize(
    ("text", "why"),
    [
        pytest.param(_entry() * 2, "twice", id="a test listed twice"),
        pytest.param(
            "".join(_entry(f"tests/test_x.py::test_{n}") for n in range(slg.CAP + 1)),
            f"at most {slg.CAP}",
            id="one entry over the cap",
        ),
        pytest.param('[[entry]]\nnodeid = "tests/test_x.py::t"\n', "names no issue", id="no issue"),
        pytest.param(_entry(issue=0), "issue", id="issue zero"),
        pytest.param(_entry(issue="12"), "issue", id="an issue that is text"),
        pytest.param(_entry(issue=True), "issue", id="an issue that is true"),
        pytest.param(_entry(nodeid=""), "nodeid", id="an empty node id"),
        pytest.param(_entry() + 'why = "x"\n', "exactly", id="an unknown key"),
        pytest.param("[other]\n", "unknown", id="an unknown table"),
        pytest.param("[[entry]\n", "TOML", id="not TOML"),
    ],
)
def test_an_allowlist_that_breaks_its_rules_is_refused(tmp_path: Path, text: str, why: str) -> None:
    path = tmp_path / "allow.toml"
    path.write_text(text)
    with pytest.raises(ValueError, match=why):
        slg.load_allowlist(path)


def test_a_missing_allowlist_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no allowlist"):
        slg.load_allowlist(tmp_path / "none.toml")
