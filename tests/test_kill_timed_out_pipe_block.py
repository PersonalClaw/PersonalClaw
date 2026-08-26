"""A timeout that waits for the grandchild is not a timeout (PEP-9 / DC-4 defect class).

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
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import os
import signal
import time
from pathlib import Path

import pytest

from personalclaw.loop import gates

# The grandchild outlives the bound by a wide margin so a slow CI host cannot flip the
# comparison: the fixed path returns in ~1s, the unfixed one in ~GRANDCHILD_SECS.
GRANDCHILD_SECS = 8
BOUND_SECS = 5.0
FORKING_CMD = f"sleep {GRANDCHILD_SECS} & wait"  # sh forks, then waits -> real grandchild

_SRC = Path(gates.__file__).resolve().parents[1]


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
async def test_control_the_replaced_shape_blows_the_same_bound():
    """VACUITY: BOUND_SECS discriminates. The replaced shape must fail the same check.

    Spawned with ``start_new_session=True`` but killed by **pid** — that isolates the
    variable to *which* thing is signalled, and lets this test clean up the group it
    deliberately orphans.
    """
    proc = await asyncio.create_subprocess_exec(
        "/bin/sh",
        "-c",
        FORKING_CMD,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    pgid = os.getpgid(proc.pid)
    try:
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

# From the kill-site census. A spawn earns its own session ONLY when the child can fork a
# grandchild that inherits a live pipe. Everything else is git plumbing that never forks;
# giving it a session buys nothing and widens the blast radius of a group signal.
_UPDATES_GROUP_LED = {
    "proc",  # git fetch      -> forks git-remote-https / ssh
    "pip_up",  # pip -U         -> forks build backends / compilers
    "pull",  # git pull       -> forks fetch's remote helper + merge
    "pip_install",  # pip install -e -> forks build backends
}
_UPDATES_LEAF = {
    "local",  # git rev-parse HEAD
    "remote",  # git rev-parse @{u}
    "show",  # git show <sha>:./pyproject.toml
    "diff",  # git diff <range> -- CHANGELOG.md
    "dirty",  # git status --porcelain
}


def _spawns_by_target(source: str, callee: str) -> dict[str, set[str]]:
    """Map assignment-target name -> set of keyword names, for each *callee* spawn."""
    found: dict[str, set[str]] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        value = node.value
        if isinstance(value, ast.Await):
            value = value.value
        if not isinstance(target, ast.Name) or not isinstance(value, ast.Call):
            continue
        fn = value.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name != callee:
            continue
        found[target.id] = {kw.arg for kw in value.keywords if kw.arg}
    return found


def test_only_the_censused_spawns_lead_their_own_group():
    """Both directions: the four forking spawns opt in, the five leaves stay out."""
    src = (_SRC / "dashboard" / "handlers" / "updates.py").read_text()
    spawns = _spawns_by_target(src, "create_subprocess_exec")

    missing = {n for n in _UPDATES_GROUP_LED if "start_new_session" not in spawns.get(n, set())}
    assert not missing, (
        f"{sorted(missing)} in updates.py can fork a grandchild that inherits its pipe, "
        "but no longer asks for its own session — kill_timed_out will fall back to a "
        "single-pid signal and the grandchild will hold the pipe open"
    )
    crept = {n for n in _UPDATES_LEAF if "start_new_session" in spawns.get(n, set())}
    assert not crept, (
        f"{sorted(crept)} are leaf git plumbing that never forks. Giving them their own "
        "session is a blanket sweep, not a fix: it widens what a group signal can reach "
        "for no benefit. Re-run the census before adding one."
    )


def _timeout_kill_style(source: str) -> dict[str, str]:
    """Map killed-variable name -> ``"pid"`` or ``"group"``, per timeout handler.

    Keyed by NAME, not line, so ordinary edits above a handler don't churn the rail.
    """
    style: dict[str, str] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if "TimeoutError" not in (ast.dump(node.type) if node.type else ""):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            fn = inner.func
            if isinstance(fn, ast.Attribute) and fn.attr == "kill":
                if isinstance(fn.value, ast.Name):
                    style[fn.value.id] = "pid"
            elif isinstance(fn, ast.Name) and fn.id == "kill_timed_out":
                if inner.args and isinstance(inner.args[0], ast.Name):
                    style[inner.args[0].id] = "group"
    return style


def test_updates_timeout_handlers_match_the_census_exactly():
    """The four forking spawns kill their GROUP; the five leaves still kill by pid.

    Bidirectional on purpose. A leaf drifting to ``group`` means someone blanket-swept
    and gave a non-forking child a session it doesn't need; a forking spawn drifting to
    ``pid`` means the grandchild is holding the pipe again.
    """
    src = (_SRC / "dashboard" / "handlers" / "updates.py").read_text()
    style = _timeout_kill_style(src)

    assert {n for n, s in style.items() if s == "group"} == _UPDATES_GROUP_LED, (
        "the set of group-killed spawns in updates.py drifted from the census; "
        f"found {sorted(n for n, s in style.items() if s == 'group')}"
    )
    assert {n for n, s in style.items() if s == "pid"} == _UPDATES_LEAF, (
        "the set of pid-killed spawns in updates.py drifted from the census; "
        f"found {sorted(n for n, s in style.items() if s == 'pid')}"
    )


def test_the_loop_gate_kills_its_shells_group():
    """gates.py has ONE spawn — an arbitrary ``/bin/sh -c``, so it always forks."""
    src = (_SRC / "loop" / "gates.py").read_text()
    assert _timeout_kill_style(src) == {"proc": "group"}
    spawns = _spawns_by_target(src, "create_subprocess_limited")
    assert "start_new_session" in spawns["proc"], (
        "the loop gate's shell no longer leads its own session, so kill_timed_out "
        "falls back to a single-pid signal and a test runner survives the 180s bound"
    )


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
