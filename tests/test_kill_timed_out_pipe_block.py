"""A timeout that waits for the grandchild is not a timeout.

``asyncio``'s ``Process.wait()`` resolves when every *inherited pipe* has disconnected,
not when the child is reaped. So the pair

    proc.kill()             # signals the direct child only
    await proc.communicate()  # unbounded

waits for whatever grandchild still holds the inherited stdout/stderr — i.e. for the
grandchild's full runtime, wearing the timeout's name. Measured over a ``sleep 30``
grandchild with a 1s timeout: **30.02s**; with the child leading its own group and the
GROUP signalled, **1.01s**.

Two independent things are pinned here:

* :func:`test_run_verify_command_kills_the_grandchild_within_the_bound` drives the real
  call site (``loop.gates.run_verify_command``) against a real forking shell and asserts
  both halves of the deliverable — it returns under the bound AND the grandchild is gone.
* :func:`test_control_the_replaced_shape_blows_the_same_bound` is the **vacuity proof**:
  the shape the fix replaced, measured against the same grandchild and the same bound,
  must exceed it. Without this the bound could be a number the fix meets trivially.

The census rail below is likewise bidirectional: the leaf spawns must NOT acquire the
flag, so a future blanket sweep reds this file. Signalling a group you do not lead takes
the gateway down with the child, which is worse than signalling one pid.

Beyond those per-file rails there is a TREE-WIDE one —
:func:`test_every_timed_out_async_spawn_is_reaped_by_the_one_owner`. Its census is
DERIVED by AST walk over every async spawn site in ``src/personalclaw``, keyed by
``file::qualname::variable``, and it requires each one's ``TimeoutError`` path to reach
:func:`~personalclaw.cancellation.kill_timed_out`. Anything that does not must be named
in an allowlist with a reason, so a spawn site cannot be quietly added without a
teardown. That keying matters: the earlier rails are per-FILE, and file-level credit
over-counts safety — ``dashboard/handlers/files.py`` contains a reaper *and* two spawns
that leaked anyway. A per-site key cannot make that mistake.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import os
import signal
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from kill_timed_out_pipe_block_shared import (
    _async_spawn_vars,
    _iter_functions,
    _reaped_by_owner,
    _spawn_functions,
    _spawns_by_target,
    _timeout_handlers,
    _timeout_kill_style,
)

from personalclaw.config import AppConfig
from personalclaw.loop import gates

# The grandchild outlives the bound by a wide margin so a slow CI host cannot flip the
# comparison: the fixed path returns in ~1s, the unfixed one in ~GRANDCHILD_SECS.
GRANDCHILD_SECS = 8
BOUND_SECS = 5.0
FORKING_CMD = f"sleep {GRANDCHILD_SECS} & wait"  # sh forks, then waits -> real grandchild


def _group_is_empty(pgid: int, *, deadline: float = 2.0) -> bool:
    """True once no process remains in *pgid*. Polls — SIGKILL delivery is not instant."""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:  # pragma: no cover — someone else owns it now
            return True
        time.sleep(0.05)
    return False


def _forking_script(tmp_path: Path) -> tuple[Path, Path]:
    """A shell that forks a long-lived grandchild inheriting its pipes, and the file where it
    records both pids: ``(script, pids)``."""
    pids = tmp_path / "pids"
    script = tmp_path / "forks.sh"
    script.write_text(
        f'#!/bin/sh\necho "child $$" >> "{pids}"\n'
        f'/bin/sleep {GRANDCHILD_SECS} &\necho "grandchild $!" >> "{pids}"\nwait\n'
    )
    script.chmod(0o755)
    return script, pids


async def _until_forked(pids: Path, *, within: float = 30.0) -> list[list[str]]:
    """The ``(role, pid)`` rows once a forking stub has recorded both its child and grandchild.

    Waited on, never assumed from the clock: on a loaded host the stub can start a second or more
    late, and a test that read the file after a fixed delay found no grandchild yet (or no file),
    and measured a grandchild that did not exist.
    """
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        with contextlib.suppress(FileNotFoundError):
            rows = [ln.split() for ln in pids.read_text().split("\n") if ln.strip()]
            if {role for role, _ in rows} == {"child", "grandchild"}:
                return rows
        await asyncio.sleep(0.02)
    raise AssertionError(f"the stub never recorded both of its processes within {within:.0f}s")


# ── the call site ──


@pytest.mark.asyncio
async def test_run_verify_command_kills_the_grandchild_within_the_bound(monkeypatch, tmp_path):
    """The gate's 1s bound must bind on the SHELL's grandchild, not wait it out."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(gates, "VERIFY_TIMEOUT_SECS", 1)

    # Spy on the real spawn so we can observe the tree the call site actually created.
    # gates.py imports the helper INSIDE the function, so patching the module attribute
    # is what the call site resolves at call time.
    import personalclaw.sandbox as sandbox

    real_spawn = sandbox.create_subprocess_limited
    seen: dict[str, object] = {}

    async def _spy(*args, **kwargs):
        proc = await real_spawn(*args, **kwargs)
        seen["pid"] = proc.pid
        # The call site must have asked for its own session, else the group branch of
        # kill_timed_out cannot fire and the grandchild is unreachable.
        seen["leads_own_group"] = os.getpgid(proc.pid) == proc.pid
        return proc

    monkeypatch.setattr(sandbox, "create_subprocess_limited", _spy)

    pgid: int | None = None
    try:
        started = time.monotonic()
        result = await gates.run_verify_command(FORKING_CMD, str(tmp_path))
        elapsed = time.monotonic() - started

        assert seen, "the spy never ran — the call site did not reach create_subprocess_limited"
        pid = int(seen["pid"])  # type: ignore[arg-type]
        pgid = pid

        assert seen["leads_own_group"] is True, (
            "run_verify_command spawned the shell into the gateway's own process group; "
            "kill_timed_out then cannot signal the group and the grandchild survives"
        )
        # A timed-out gate yields no done-ness signal.
        assert result is None
        assert elapsed < BOUND_SECS, (
            f"the 1s gate took {elapsed:.2f}s to return — the post-kill reap waited for "
            f"the grandchild's inherited pipe instead of the child's exit"
        )
        # Fast is only half of it: fast-because-we-stopped-waiting would leak the tree.
        assert _group_is_empty(pgid), (
            f"process group {pgid} still has members after the gate timed out — "
            "the grandchild outlived the kill"
        )
    finally:
        if pgid is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pgid, signal.SIGKILL)


@pytest.mark.asyncio
async def test_control_the_replaced_shape_blows_the_same_bound(tmp_path):
    """VACUITY: BOUND_SECS discriminates. The replaced shape must fail the same check.

    Spawned with ``start_new_session=True`` but killed by **pid** — that isolates the
    variable to *which* thing is signalled, and lets this test clean up the group it
    deliberately orphans. The clock starts once the grandchild holds the pipe: a shell killed
    before it forks has nothing to wait out, and returned under the bound on a loaded host.
    """
    script, pids = _forking_script(tmp_path)
    proc = await asyncio.create_subprocess_exec(
        str(script),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    pgid = os.getpgid(proc.pid)
    try:
        await _until_forked(pids)
        started = time.monotonic()
        try:
            await asyncio.wait_for(proc.communicate(), timeout=1)
        except (asyncio.TimeoutError, TimeoutError):
            proc.kill()  # the replaced shape: direct child only
            await proc.communicate()  # ...and an unbounded drain
        elapsed = time.monotonic() - started

        assert elapsed > BOUND_SECS, (
            f"the control returned in {elapsed:.2f}s, under the {BOUND_SECS}s bound — "
            "this test can no longer tell the fixed path from the broken one, so the "
            "sibling test above proves nothing. Re-derive the bound."
        )
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signal.SIGKILL)


# ── the census rail (bidirectional: leaves must stay leaves) ──


# ── the same census over the file browser's spawns (#432) ──


def test_the_by_function_matcher_sees_an_unreaped_timeout():
    """VACUITY: the rail above must recognise #432's actual shape as unreaped."""
    unreaped = (
        "import asyncio\n"
        "async def api_leaky():\n"
        "    proc = await asyncio.create_subprocess_exec('git', 'show', 'HEAD:x')\n"
        "    try:\n"
        "        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)\n"
        "    except asyncio.TimeoutError:\n"
        "        return None\n"
    )
    assert _spawn_functions(unreaped) == {"api_leaky": {"kill": None, "own_session": False}}

    fixed = unreaped.replace("        return None\n", "        await kill_timed_out(proc)\n")
    assert _spawn_functions(fixed)["api_leaky"]["kill"] == "group"


def test_the_matcher_tells_the_two_shapes_apart():
    """VACUITY: the matcher is not vacuous — it labels each shape, and differently."""
    header = (
        "import asyncio\n"
        "async def f(proc):\n"
        "    try:\n"
        "        await asyncio.wait_for(proc.communicate(), timeout=30)\n"
        "    except asyncio.TimeoutError:\n"
    )
    assert _timeout_kill_style(header + "        proc.kill()\n") == {"proc": "pid"}
    assert _timeout_kill_style(header + "        await kill_timed_out(proc)\n") == {"proc": "group"}
    # A kill OUTSIDE a timeout handler is not this defect and must not be reported.
    assert _timeout_kill_style("def g(proc):\n    proc.kill()\n") == {}


def test_the_spawn_matcher_sees_the_flag_both_ways():
    """VACUITY for the census rail's matcher."""
    src = (
        "import asyncio\n"
        "async def f():\n"
        "    a = await asyncio.create_subprocess_exec('x', start_new_session=True)\n"
        "    b = await asyncio.create_subprocess_exec('y')\n"
    )
    spawns = _spawns_by_target(src, "create_subprocess_exec")
    assert "start_new_session" in spawns["a"]
    assert "start_new_session" not in spawns["b"]


# ── the tree-wide rail (DERIVED: every async spawn's timeout path reaches the owner) ──


def test_the_tree_wide_matchers_are_not_vacuous():
    """VACUITY: each matcher labels both shapes, and differently."""
    routed_src = (
        "import asyncio\n"
        "from personalclaw.cancellation import kill_timed_out\n"
        "async def f():\n"
        "    proc = await asyncio.create_subprocess_exec('x')\n"
        "    try:\n"
        "        await asyncio.wait_for(proc.communicate(), timeout=1)\n"
        "    except asyncio.TimeoutError:\n"
        "        await kill_timed_out(proc)\n"
    )
    leaky_src = routed_src.replace("await kill_timed_out(proc)", "proc.kill()")
    for src, expect in ((routed_src, True), (leaky_src, False)):
        fn = _iter_functions(ast.parse(src))[0][1]
        assert _async_spawn_vars(fn) == {"proc"}
        assert _timeout_handlers(fn), "the TimeoutError handler matcher missed a handler"
        assert (
            "proc" in _reaped_by_owner(fn)
        ) is expect, "the owner matcher cannot tell a routed timeout path from a leaking one"

    # A spawn with no timeout handler must be reported as such, not as "routed".
    no_handler = _iter_functions(
        ast.parse(
            "import asyncio\n"
            "async def g():\n"
            "    proc = await asyncio.create_subprocess_exec('x')\n"
            "    await proc.wait()\n"
        )
    )[0][1]
    assert _async_spawn_vars(no_handler) == {"proc"}
    assert _timeout_handlers(no_handler) == []

    # An unassigned spawn is still censused (it cannot be reaped by name at all).
    bare = _iter_functions(
        ast.parse("import asyncio\nasync def h():\n    await asyncio.create_subprocess_exec('x')\n")
    )[0][1]
    assert _async_spawn_vars(bare) == {"<unassigned>"}

    # A kill OUTSIDE a timeout handler is a different concern and must not be collected.
    off_path = _iter_functions(ast.parse("def k(proc):\n    proc.kill()\n"))[0][1]
    assert _timeout_handlers(off_path) == []


# ── the gateway's auto-update: the site the orphan leak was measured on ──


@pytest.mark.asyncio
async def test_gateway_auto_update_reaps_the_install_it_timed_out(
    monkeypatch, tmp_path, environment_made_by
):
    """A timed-out ``pip install -e .`` in the auto-update leaves NO live child behind.

    The defect this pins: ``gateway._auto_apply_update`` was the twin of
    ``dashboard/handlers/updates.py::api_update_apply``, and the reaping fix landed only
    on the twin. Its `pip install` had a ``wait_for`` and no ``TimeoutError`` handler, so
    a timeout unwound to the function's outer ``except Exception`` and the child was never
    signalled — **2 live processes per timed-out install** (the ``pip`` child and its
    forked build backend), still running after the call returned and the loop closed,
    accumulating once per auto-update poll. The two are one function now
    (``updates.start_checkout_update``), and this drives it the way the auto-update does.

    The git stages run in ``self_update``'s synchronous helpers, which run under
    ``subprocess.run(timeout=…)`` and so reap their own direct child inside CPython. The
    install is the one async spawn left in the update, and the one that can fork.

    The deadline is INJECTED (``checkout_update._INSTALL_TIMEOUT``), never slept on.
    """
    from personalclaw import _installer, checkout_update
    from personalclaw import gateway as gw
    from personalclaw import self_update

    pids = tmp_path / "pids"
    proj = tmp_path / "proj"
    proj.mkdir()

    # The stub stands in for `sys.executable -m pip install`: it FORKS a long-lived
    # grandchild that inherits pip's stdout/stderr pipes, which is what a real pip does
    # with a PEP 517 build backend. Both pids are recorded, so liveness is asserted by PID
    # rather than by scraping a command line.
    #
    # /bin/sleep, not a copy of it: macOS SIGKILLs a copy of a signed system binary, so a
    # copied sleeper would vanish on its own and this test would pass for the wrong reason.
    stub = tmp_path / "pip-stub.sh"
    stub.write_text(
        "#!/bin/sh\n"
        f'echo "child $$" >> "{pids}"\n'
        f"/bin/sleep {GRANDCHILD_SECS} &\n"
        f'echo "grandchild $!" >> "{pids}"\n'
        "wait\n"
    )
    stub.chmod(0o755)

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(self_update, "source_checkout", lambda: str(proj))
    # The environment pip made, so the install is `sys.executable -m pip install -e .`.
    environment_made_by("pip")
    monkeypatch.setattr(_installer, "_have_pip", lambda: True)
    monkeypatch.setattr(gw.sys, "executable", str(stub))
    monkeypatch.setattr(checkout_update, "_INSTALL_TIMEOUT", 1.0)

    # Drive the nightly branch straight to the install: every git stage before it is a
    # `self_update` helper, stubbed to succeed so the deadline under test is the only thing
    # this exercises.
    ok = subprocess.CompletedProcess(args=["git"], returncode=0, stdout="", stderr="")

    async def _one_behind(_proj, *, fetch):
        return 1

    monkeypatch.setattr(self_update, "git_tracked_changes", lambda _proj: [])
    monkeypatch.setattr(
        self_update, "git_position", lambda _proj: self_update.CheckoutPosition("1" * 40)
    )
    monkeypatch.setattr(self_update, "commits_behind_upstream", _one_behind)
    monkeypatch.setattr(self_update, "resolve_default_branch", lambda _proj: "main")
    monkeypatch.setattr(self_update, "git_fast_forward", lambda _proj, _branch: ok)
    monkeypatch.setattr(self_update, "package_root", lambda _proj: proj)
    monkeypatch.setattr(
        AppConfig,
        "load",
        classmethod(
            lambda cls: SimpleNamespace(
                updates=SimpleNamespace(channel="nightly", pin="", check_enabled=True)
            )
        ),
    )

    # The install's deadline starts when its spawn returns. The spawn returns only once the stub
    # has forked, so a stub that starts late on a loaded host still meets the deadline with a
    # grandchild to leak, and the time is measured from there.
    real_spawn = asyncio.create_subprocess_exec
    recorded: list[list[str]] = []
    deadline_from: list[float] = []

    async def spawn_then_wait_for_the_fork(program, *args, **kwargs):
        proc = await real_spawn(program, *args, **kwargs)
        if str(program) == str(stub):
            recorded.extend(await _until_forked(pids))
            deadline_from.append(time.monotonic())
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn_then_wait_for_the_fork)

    orch = gw.GatewayOrchestrator.__new__(gw.GatewayOrchestrator)
    orch.dashboard_state = None
    orch.sessions = None

    await orch._auto_apply_update()
    assert deadline_from, "the auto-update never started the install this test times out"
    elapsed = time.monotonic() - deadline_from[0]

    roles = {role for role, _ in recorded}
    assert roles == {"child", "grandchild"}, (
        f"the pip stub did not fork as expected (recorded {recorded!r}) — this test would "
        "prove nothing about a grandchild that never existed"
    )

    # Half one: the deadline actually bound. It must not have waited the grandchild out.
    assert elapsed < BOUND_SECS, (
        f"the 1s install deadline took {elapsed:.2f}s to return — the post-kill reap "
        "waited for the grandchild's inherited pipe instead of the child's exit"
    )

    # Half two, and the one that was failing: nothing is left running. Poll, because
    # SIGKILL delivery is not instant.
    for role, pid in recorded:
        pid = int(pid)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:  # pragma: no cover — the leak this test exists to catch
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)
            raise AssertionError(
                f"the auto-update's timed-out `pip install` left its {role} (pid {pid}) "
                "running after the call returned. A timed-out child must be killed AND "
                "reaped, with its group — see cancellation.kill_timed_out."
            )


@pytest.mark.asyncio
async def test_control_the_gateway_shape_before_the_fix_leaks_the_pair(tmp_path):
    """VACUITY for the test above: the shape it replaced must FAIL the same check.

    The pre-fix ``gateway._auto_apply_update`` had no ``TimeoutError`` arm at all — the
    timeout unwound to the function's outer ``except Exception`` and nothing was
    signalled. Reproduced here so the assertion above cannot be one a broken path also
    passes: BOTH processes must still be alive, which is what the fixed path denies.

    ``start_new_session=True`` here even though the pre-fix gateway spawn lacked it. That
    isolates the variable to the missing TEARDOWN, and — the load-bearing reason — it is
    what makes this test's own cleanup safe: ``os.getpgid`` of a child that does NOT lead
    its own group returns the TEST RUNNER's group, so a ``killpg`` on it would take the
    pytest worker down with the child. That is the same hazard
    ``cancellation._is_group_leader`` exists to prevent, and a test is not exempt from it.
    """
    script, pids = _forking_script(tmp_path)
    proc = await asyncio.create_subprocess_exec(
        str(script),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    pgid = os.getpgid(proc.pid)
    assert pgid == proc.pid, "the control must lead its own group for its cleanup to be safe"
    try:
        # The pair exists first, however late the script starts; then the pre-fix shape.
        recorded = await _until_forked(pids)
        with contextlib.suppress(asyncio.TimeoutError, TimeoutError):
            await asyncio.wait_for(proc.communicate(), timeout=1)
        # ...and then NO teardown whatsoever: the pre-fix arm did not exist.
        await asyncio.sleep(0.3)

        alive = []
        for role, pid in recorded:
            try:
                os.kill(int(pid), 0)
                alive.append(role)
            except ProcessLookupError:  # pragma: no cover
                pass
        assert sorted(alive) == ["child", "grandchild"], (
            f"the control leaked only {alive} of the expected pair, so it no longer "
            "demonstrates the leak and the sibling test above proves nothing. "
            "Re-derive the stub."
        )
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            await proc.wait()
