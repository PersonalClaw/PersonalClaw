"""Native inference that holds the interpreter lock runs in a process of its own.

A library call that never lets go of the interpreter lock stops every other thread of its process
while it runs, the gateway's event loop included. Run from a worker thread (``asyncio.to_thread``,
``run_in_executor``), a speaker diarization that took two minutes held every request the gateway
was serving for those two minutes. ``run_once`` runs one call of an app's worker in a child
process, whose lock is its own, and a cancelled call kills the child and what it started.

The lock-holding stand-in is libc's ``usleep`` called through ``ctypes.PyDLL``, which, unlike
``ctypes.CDLL``, keeps the lock for the whole call: the shape of a C extension that never
releases it. The first test carries the control that proves it holds the lock here.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import textwrap
import time
from pathlib import Path

import pytest

from personalclaw.local_models.sidecar import SidecarCrashed, SidecarWorkerError, run_once

#: How long the stand-in holds the lock.
_HOLD_SECONDS = 1.5
#: The longest the event loop may go without a tick while the child holds its own lock: half the
#: hold, so the defect cannot pass, and generous for a loaded host.
_LOOP_BOUND = 0.75

_WORKER = textwrap.dedent('''
    """A fixture worker, stdlib only, as an app's worker is."""
    import ctypes
    import os
    import signal
    import subprocess
    import time


    def hold(seconds):
        """A C call that keeps the interpreter lock while it sleeps."""
        ctypes.PyDLL(None).usleep(int(seconds * 1_000_000))


    def call(method, payload):
        if method == "hold":
            hold(payload["seconds"])
            return {"pid": os.getpid()}
        if method == "linger":
            child = subprocess.Popen([payload["sleep"], "60"])
            with open(payload["pids"] + ".part", "w") as f:
                f.write(f"{os.getpid()} {child.pid}")
            os.replace(payload["pids"] + ".part", payload["pids"])
            time.sleep(60)
        if method == "import":
            import colorsys

            import pcfixture_app_pkg

            return {"colorsys": colorsys.__file__, "value": pcfixture_app_pkg.VALUE}
        if method == "fail":
            raise RuntimeError("the engine refused this recording")
        if method == "die":
            os.kill(os.getpid(), signal.SIGKILL)
        return {"echo": payload}
    ''')

_APP = "pc-fixture-inference"


@pytest.fixture
def worker(tmp_path: Path) -> Path:
    path = tmp_path / "worker.py"
    path.write_text(_WORKER, encoding="utf-8")
    return path


def _hold_here(seconds: float) -> None:
    """The stand-in, in THIS process: what an app running it in a thread does."""
    import ctypes

    ctypes.PyDLL(None).usleep(int(seconds * 1_000_000))


async def _worst_gap_while(awaitable):
    """Await *awaitable* while the loop ticks every 10 ms: its result, and the longest gap."""
    gaps: list[float] = []
    done = asyncio.Event()

    async def _tick() -> None:
        last = time.monotonic()
        while not done.is_set():
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    ticker = asyncio.ensure_future(_tick())
    await asyncio.sleep(0.05)
    try:
        result = await awaitable
    finally:
        done.set()
        await ticker
    return result, max(gaps)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _gone_within(pids: list[int], seconds: float) -> list[int]:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        left = [pid for pid in pids if _alive(pid)]
        if not left:
            return []
        await asyncio.sleep(0.05)
    return [pid for pid in pids if _alive(pid)]


@pytest.mark.asyncio
async def test_a_call_that_holds_the_interpreter_lock_leaves_the_event_loop_running(worker):
    """🔴 Red before: there was no way for an app to run a call in a process of its own, so a
    lock-holding engine ran in a thread of the gateway and stopped its loop for the whole call."""
    # The control: in a thread, as the apps ran it, the stand-in stops the loop for the call.
    _none, stalled = await _worst_gap_while(asyncio.to_thread(_hold_here, _HOLD_SECONDS))
    assert stalled >= _HOLD_SECONDS * 0.8, f"control: the stand-in held no lock ({stalled:.2f}s)"

    answer, worst = await _worst_gap_while(
        run_once(_APP, worker, "hold", {"seconds": _HOLD_SECONDS})
    )

    assert answer["pid"] != os.getpid()
    assert worst < _LOOP_BOUND, f"the event loop stopped for {worst:.2f}s during the call"


@pytest.mark.asyncio
async def test_cancelling_the_call_kills_the_child_and_what_it_started(worker, tmp_path):
    """A step that runs out of time, or a gateway that stops, cancels the call: the child and
    the program it started go with it, rather than running on for nobody."""
    sleep = shutil.which("sleep")
    assert sleep, "the test needs a sleep program"
    pids = tmp_path / "pids"
    call = asyncio.ensure_future(
        run_once(_APP, worker, "linger", {"sleep": sleep, "pids": str(pids)})
    )
    end = time.monotonic() + 20
    while not pids.exists():
        assert time.monotonic() < end, "the worker never started its child"
        assert not call.done(), call
        await asyncio.sleep(0.05)
    child, grandchild = (int(pid) for pid in pids.read_text().split())
    assert _alive(child) and _alive(grandchild)

    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call

    assert await _gone_within([child, grandchild], 5.0) == []


@pytest.mark.asyncio
async def test_the_child_imports_the_app_packages_after_the_interpreters_own(worker):
    """The packages apps declare (``<home>/app-python``) are importable in the child, AFTER the
    interpreter's own, as in the gateway: an app package can add a module but never replace one
    the interpreter has. Here the app packages carry a module of their own, and a stand-in for a
    standard-library module that must lose the import."""
    from personalclaw.apps import app_python

    packages = app_python.site_dirs()[0]
    packages.mkdir(parents=True, exist_ok=True)
    (packages / "pcfixture_app_pkg.py").write_text("VALUE = 'from the app packages'\n")
    (packages / "colorsys.py").write_text("raise ImportError('an app package replaced colorsys')\n")

    got = await run_once(_APP, worker, "import")

    assert got["value"] == "from the app packages"
    assert not got["colorsys"].startswith(str(packages)), got


@pytest.mark.asyncio
async def test_a_persistent_sidecar_under_the_gateways_interpreter_imports_them_too(
    worker, tmp_path
):
    """One rule for every child of the sidecar harness: a long-lived runner with no venv of its
    own runs under the gateway's interpreter, and its worker imports the app packages as well."""
    from personalclaw.apps import app_python
    from personalclaw.local_models.sidecar import SidecarRunner

    packages = app_python.site_dirs()[0]
    packages.mkdir(parents=True, exist_ok=True)
    (packages / "pcfixture_app_pkg.py").write_text("VALUE = 'from the app packages'\n")
    runner = SidecarRunner(app=_APP, worker=worker, venv=tmp_path / "no-venv", call_timeout=60.0)
    try:
        got = await runner.acall("call", {"method": "import", "payload": {}})
    finally:
        runner.stop()
    assert got["value"] == "from the app packages"


@pytest.mark.asyncio
async def test_what_the_worker_gives_back_is_the_answer(worker):
    assert await run_once(_APP, worker, "echo", {"words": ["a", 1]}) == {
        "echo": {"words": ["a", 1]}
    }


@pytest.mark.asyncio
async def test_a_worker_that_raises_is_a_worker_error_in_its_own_words(worker):
    with pytest.raises(SidecarWorkerError) as raised:
        await run_once(_APP, worker, "fail")
    assert str(raised.value) == "RuntimeError: the engine refused this recording"
    assert raised.value.reason == "worker_error"


@pytest.mark.asyncio
async def test_a_child_that_dies_before_it_answers_is_a_crash_saying_how(worker):
    with pytest.raises(SidecarCrashed) as raised:
        await run_once(_APP, worker, "die")
    assert raised.value.reason == "signal_9"
    assert raised.value.typed_reason == "sidecar_crashed:signal_9"


@pytest.mark.asyncio
async def test_a_worker_that_is_not_there_is_refused_by_the_child_naming_it(tmp_path):
    with pytest.raises(SidecarWorkerError) as raised:
        await run_once(_APP, tmp_path / "no-such-worker.py", "echo")
    assert "no-such-worker.py" in str(raised.value)


def test_the_child_reads_the_app_packages_from_the_variable_the_gateway_sets():
    """The child harness is stdlib only, so it names the variable itself: the same one the
    gateway's other Python children read."""
    from personalclaw import _app_python_child
    from personalclaw.local_models import _sidecar_child

    assert _sidecar_child.APP_PYTHON_PATH_ENV == _app_python_child.PATH_ENV
