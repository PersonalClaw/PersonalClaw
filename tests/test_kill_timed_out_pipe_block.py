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

from personalclaw.config import AppConfig
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
# RUM-4 retired the `git pull` spawn (the git kind rides release tags now; the
# advance goes through `asyncio.to_thread(self_update.git_*)`, sync `subprocess.run`
# under one seam, not a create_subprocess_exec here), and the dashboard dirty-tree
# check moved to `asyncio.to_thread(self_update.git_tracked_changes)` — so the `pull`
# group-leader and the `dirty` leaf spawn are gone from this module's census.
_UPDATES_GROUP_LED = {
    "proc",  # git fetch      -> forks git-remote-https / ssh
    "pip_up",  # pip -U         -> forks build backends / compilers
    "pip_install",  # pip install -e -> forks build backends
}
_UPDATES_LEAF = {
    "local",  # git rev-parse HEAD
    "remote",  # git rev-parse @{u}
    "show",  # git show <sha>:./pyproject.toml
    "diff",  # git diff <range> -- CHANGELOG.md
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
    """Both directions: the three forking spawns opt in, the four leaves stay out."""
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
    """The three forking spawns kill their GROUP; the four leaves still kill by pid.

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


# ── the same census over the file browser's spawns (#432) ──
#
# files.py is where this defect class was found a second time: `api_file_git_original`
# had hand-rolled `_git`'s spawn and its timeout handler killed NOTHING (measured: two
# live processes leaked per timeout — the git child and its grandchild — accumulating
# one pair per poll of the diff view), and `_content_search_rg` had the identical shape
# for ripgrep. Keyed by FUNCTION here, not by variable name: all four of these spawns
# are called `proc`, so the by-name matcher above cannot tell them apart.
_FILES_REAPED = {
    "_git",  # git forks: fsmonitor hook, LFS filter, remote helper
    "_content_search_rg",  # rg over a huge tree blows the 15s search deadline
}
_FILES_PID_KILL = {
    "api_upload",  # osascript: the Finder dialog is the OS's window, not a child
    "api_screenshot",  # screencapture: no children
}
# Only a spawn whose child can fork needs its own session; a group signal you do not
# lead is wider than the fix (see the updates.py census above for the same rule).
_FILES_GROUP_LED = {"_git"}


def _spawn_functions(source: str) -> dict[str, dict[str, object]]:
    """Map function name -> {kill: "group"|"pid"|None, own_session: bool} per spawn site."""
    out: dict[str, dict[str, object]] = {}
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        dumped = ast.dump(fn)
        if "create_subprocess" not in dumped:
            continue
        kill: str | None = None
        for node in ast.walk(fn):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if "TimeoutError" not in (ast.dump(node.type) if node.type else ""):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                f = inner.func
                if isinstance(f, ast.Attribute) and f.attr in {"kill", "terminate"}:
                    kill = "pid"
                elif isinstance(f, ast.Name) and f.id in {"kill_timed_out", "terminate_and_reap"}:
                    kill = "group"
        out[fn.name] = {"kill": kill, "own_session": "start_new_session" in dumped}
    return out


def test_every_file_browser_spawn_reaps_its_timeout():
    """No spawn in files.py may time out and walk away, and the census must be complete.

    Three directions, because each is a way the module drifted or could drift again:
    an unclassified spawn (a NEW endpoint shelling out) reds naming itself; a censused
    reaper that stops reaping reds; and a leaf that acquires a session it doesn't need
    reds too.
    """
    src = (_SRC / "dashboard" / "handlers" / "files.py").read_text()
    sites = _spawn_functions(src)

    unclassified = set(sites) - _FILES_REAPED - _FILES_PID_KILL
    assert not unclassified, (
        f"{sorted(unclassified)} in files.py spawn a process but are not in this census. "
        "Classify each: route the timeout through kill_timed_out (the default — it "
        "group-signals when the child leads a group and falls back to the pid when it "
        "does not, and its reap is BOUNDED), or justify a bare pid kill here. #432 was "
        "exactly this: a spawn nobody had classified, whose timeout killed nothing."
    )
    stale = (_FILES_REAPED | _FILES_PID_KILL) - set(sites)
    assert not stale, f"census names {sorted(stale)}, which no longer spawn anything"

    not_reaping = {n for n in _FILES_REAPED if sites[n]["kill"] != "group"}
    assert not not_reaping, (
        f"{sorted(not_reaping)} no longer reap through kill_timed_out. A killed-but-"
        "unreaped child is a zombie holding its end of the pipe; an unkilled one is an "
        "orphan that outlives the request (#432 leaked one per timed-out read)."
    )
    drifted = {n for n in _FILES_PID_KILL if sites[n]["kill"] != "pid"}
    assert not drifted, f"{sorted(drifted)} changed kill style without moving in the census"

    assert {n for n, s in sites.items() if s["own_session"]} == _FILES_GROUP_LED, (
        "the set of files.py spawns leading their own session drifted from the census: "
        f"found {sorted(n for n, s in sites.items() if s['own_session'])}"
    )


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

_ASYNC_SPAWNS = {
    "create_subprocess_exec",
    "create_subprocess_shell",
    "create_subprocess_limited",
}

#: Async spawn sites whose timeout path does NOT route through the owner, each with the
#: reason it is still outstanding. Keyed ``file::qualname::variable`` — per SITE, not per
#: file, because a file that contains a reaper can still contain an unguarded spawn.
#:
#: This allowlist is the whole control: a new spawn site is `owner` or it is listed here
#: with a reason, and a stale entry reds just as loudly as a missing one.
_NOT_ROUTED_TO_THE_OWNER: dict[str, str] = {
    # ── operator-driven native pickers: pid-killed AND reaped, no fork ──
    # `screencapture` and the osascript file picker are single processes that do not fork,
    # so the direct-child kill already reaches everything. What they still lack is the
    # BOUND on the drain, which is why they are listed rather than called correct.
    "dashboard/handlers/files.py::api_screenshot::proc": "leaf picker: killed+reaped, no bound",
    "dashboard/handlers/files.py::api_upload::proc": "leaf picker: killed+reaped, no bound",
    # ── censused leaf git plumbing in updates.py ──
    # Deliberately pid-killed, and pinned that way BOTH ways by
    # `test_updates_timeout_handlers_match_the_census_exactly` above: these four never
    # fork, so a group signal would only widen the blast radius. Routing them through the
    # owner would red that rail, so the two rails are kept consistent here on purpose.
    "dashboard/handlers/updates.py::_do_update_check::local": "censused leaf (git rev-parse)",
    "dashboard/handlers/updates.py::_do_update_check::remote": "censused leaf (git rev-parse @{u})",
    "dashboard/handlers/updates.py::_do_update_check::show": "censused leaf (git show)",
    "dashboard/handlers/updates.py::_do_update_check::diff": "censused leaf (git diff)",
    # ── still outstanding: pid-killed and reaped, so no hang; the grandchild leaks ──
    "dashboard/handlers/_shared.py::_list_marketplace_skills::proc": (
        "outstanding: `personalclaw skills list` pid-killed, unbounded drain"
    ),
    "dashboard/handlers/mcp.py::api_mcp_remove::proc": (
        "outstanding: `personalclaw skills mcp uninstall` pid-killed, unbounded drain"
    ),
    "workflows/review_service.py::_git::proc": (
        "outstanding: run-workspace `git diff` pid-killed; forks under fsmonitor/LFS"
    ),
    "mcp_discovery.py::probe_server::proc": (
        "outstanding: two of its four deadlines tear down, one does not; the stdio "
        "lifecycle also tears down in a `finally`, so this needs its own read"
    ),
    # ── still outstanding: NO teardown at all, but every one is a leaf tmux client ──
    # `tmux -L personalclaw <verb>` against our OWN server. A hung client is a leaked
    # client, not a leaked tree, and `new_session` has 30 dependent test files — a
    # separable change, deliberately not swept in with this one.
    "tmux_substrate.py::new_session::proc": "outstanding: leaf tmux client, no teardown",
    "tmux_substrate.py::has_session::proc": "outstanding: leaf tmux client, no teardown",
    "tmux_substrate.py::list_sessions::proc": "outstanding: leaf tmux client, no teardown",
    "tmux_substrate.py::kill_session::proc": "outstanding: leaf tmux client, no teardown",
    "dashboard/handlers/terminal.py::_kill_tmux_session::proc": (
        "outstanding: leaf tmux client, no teardown"
    ),
    "dashboard/handlers/terminal.py::_list_tmux_sessions::proc": (
        "outstanding: leaf tmux client, no teardown"
    ),
}

#: Async spawn sites with NO ``TimeoutError`` handler in their function at all. Listed so
#: the distinction is a decision rather than an omission: a site here has no deadline to
#: leak on, or its teardown lives in a different lifecycle.
_NO_TIMEOUT_PATH: dict[str, str] = {
    "sandbox.py::create_subprocess_limited::<unassigned>": "the ceiling helper — caller waits",
    "sandbox_providers/none.py::_NoneHandle.exec::<unassigned>": "provider handle — caller waits",
    "sandbox_providers/docker.py::_DockerHandle.exec::<unassigned>": "provider — caller waits",
    "sandbox_providers/lima.py::_LimaHandle.exec::<unassigned>": "provider — caller waits",
    "knowledge/pipeline/nodes/media_nodes.py::_run_cmd::proc": "ffmpeg: awaited with no deadline",
    "transcribe.py::_transcribe_segmented::proc": "ffmpeg: awaited with no deadline",
    "transcribe.py::_transcribe_segmented_detailed::proc": "ffmpeg: awaited with no deadline",
    "dashboard/handlers/terminal.py::api_terminal_ws::proc": (
        "long-lived interactive PTY — torn down by `_kill_pty_session` on close, not by a "
        "per-command deadline"
    ),
}


def _iter_functions(tree: ast.AST):
    """Yield ``(qualname, node)`` for every function in *tree*."""
    out: list[tuple[str, ast.AST]] = []

    class V(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def _fn(self, n):
            self.stack.append(n.name)
            out.append((".".join(self.stack), n))
            self.generic_visit(n)
            self.stack.pop()

        visit_FunctionDef = _fn
        visit_AsyncFunctionDef = _fn

        def visit_ClassDef(self, n):
            self.stack.append(n.name)
            self.generic_visit(n)
            self.stack.pop()

    V().visit(tree)
    return out


def _callee(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _async_spawn_vars(fn: ast.AST) -> set[str]:
    """Names assigned from an async spawn in *fn*; ``<unassigned>`` for a bare call."""
    named: set[str] = set()
    assigned: set[int] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            v = node.value
            if isinstance(v, ast.Await):
                v = v.value
            if (
                isinstance(v, ast.Call)
                and _callee(v) in _ASYNC_SPAWNS
                and isinstance(node.targets[0], ast.Name)
            ):
                named.add(node.targets[0].id)
                assigned.add(id(v))
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _callee(node) in _ASYNC_SPAWNS:
            if id(node) not in assigned:
                named.add("<unassigned>")
    return named


def _timeout_handlers(fn: ast.AST) -> list[ast.ExceptHandler]:
    return [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.ExceptHandler)
        and n.type is not None
        and "TimeoutError" in ast.dump(n.type)
    ]


def _reaped_by_owner(fn: ast.AST) -> set[str]:
    """Variables handed to the OWNER inside a ``TimeoutError`` handler of *fn*."""
    reaped: set[str] = set()
    for handler in _timeout_handlers(fn):
        for inner in ast.walk(handler):
            if not isinstance(inner, ast.Call):
                continue
            if _callee(inner) not in {"kill_timed_out", "terminate_and_reap"}:
                continue
            if inner.args and isinstance(inner.args[0], ast.Name):
                reaped.add(inner.args[0].id)
    return reaped


def _src_files() -> list[Path]:
    return sorted(_SRC.rglob("*.py"))


def _async_spawn_census() -> tuple[dict[str, bool], dict[str, str]]:
    """``key -> routed?`` for spawns on a timeout path, plus ``key -> reason-less`` for
    spawns with no timeout path at all."""
    routed: dict[str, bool] = {}
    no_path: dict[str, str] = {}
    for path in _src_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover
            continue
        rel = path.relative_to(_SRC).as_posix()
        for qual, fn in _iter_functions(tree):
            spawn_vars = _async_spawn_vars(fn)
            if not spawn_vars:
                continue
            handlers = _timeout_handlers(fn)
            owned = _reaped_by_owner(fn)
            for var in sorted(spawn_vars):
                key = f"{rel}::{qual}::{var}"
                if not handlers:
                    no_path[key] = ""
                else:
                    routed[key] = var in owned
    return routed, no_path


def test_every_timed_out_async_spawn_is_reaped_by_the_one_owner():
    """DERIVED, per SITE: a timed-out async child reaches ``kill_timed_out``, or is named.

    ``asyncio``'s ``Process.wait()`` resolving on pipe disconnect is what makes this the
    async spawn's problem specifically: the sync ``subprocess.run(timeout=…)`` path reaps
    its own direct child inside CPython, so it can only leak a grandchild — it cannot
    hang. Every async site therefore either routes to the owner or says why not.
    """
    routed, no_path = _async_spawn_census()

    unrouted = sorted(k for k, ok in routed.items() if not ok)
    unexplained = [k for k in unrouted if k not in _NOT_ROUTED_TO_THE_OWNER]
    assert not unexplained, (
        "async spawn site(s) whose TimeoutError path does not reach "
        "cancellation.kill_timed_out. A timed-out child must be killed AND reaped, with "
        "its group when it can fork. Route it through the owner, or add it to "
        "_NOT_ROUTED_TO_THE_OWNER with the reason:\n" + "\n".join(f"  {k}" for k in unexplained)
    )

    # Bidirectional: an entry that is now routed (or gone) must be dropped, so the
    # allowlist cannot quietly outlive the defect it describes.
    stale = sorted(k for k in _NOT_ROUTED_TO_THE_OWNER if k not in routed or routed.get(k) is True)
    assert not stale, (
        "stale _NOT_ROUTED_TO_THE_OWNER entr(y/ies) — the site now routes through the "
        "owner, or no longer exists. Remove it:\n" + "\n".join(f"  {k}" for k in stale)
    )

    missing_no_path = sorted(k for k in no_path if k not in _NO_TIMEOUT_PATH)
    assert not missing_no_path, (
        "async spawn site(s) with NO TimeoutError handler at all. Either they have no "
        "deadline to leak on (record that in _NO_TIMEOUT_PATH) or a deadline was added "
        "without a teardown:\n" + "\n".join(f"  {k}" for k in missing_no_path)
    )
    stale_no_path = sorted(k for k in _NO_TIMEOUT_PATH if k not in no_path)
    assert not stale_no_path, (
        "stale _NO_TIMEOUT_PATH entr(y/ies) — the site grew a timeout handler, or is "
        "gone:\n" + "\n".join(f"  {k}" for k in stale_no_path)
    )


#: The ONE place a timeout path may signal a process group by hand. Everything else routes
#: through ``cancellation._signal_child``, which is the only code that checks group
#: LEADERSHIP first — a bare ``killpg`` on a child that does not lead its own group
#: signals the gateway itself.
_HAND_ROLLED_GROUP_KILL_ON_A_TIMEOUT: dict[str, str] = {
    "dashboard/handlers/terminal.py::_kill_session": (
        "outstanding: re-implements terminate_and_reap (SIGTERM → 5s → SIGKILL) for the "
        "interactive PTY, and its final `sess.proc.wait()` is UNBOUNDED. `_signal_session` "
        "is also used off the timeout path (explicit close), so collapsing it into the "
        "owner is a session-lifecycle change, not this one."
    ),
}


def test_the_owner_is_the_only_hand_rolled_group_kill_on_a_timeout_path():
    """No new ``os.killpg`` inside a ``TimeoutError`` handler.

    Three copies of the group-kill-then-drain pair existed: ``artifacts/build.py``'s
    ``_kill_tree``, ``action_providers/bash_provider.py``'s inline ``os.killpg``, and
    ``dashboard/handlers/terminal.py``'s ``_signal_session``. The first two are collapsed
    into the owner; the third is named above. This test exists so the answer to "the
    owner does not quite fit my site" is never a fourth copy.
    """
    found: dict[str, str] = {}
    for path in _src_files():
        rel = path.relative_to(_SRC).as_posix()
        if rel == "cancellation.py":  # the owner itself
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover
            continue
        for qual, fn in _iter_functions(tree):
            for handler in _timeout_handlers(fn):
                # Direct `os.killpg`, or a call to a local helper that wraps one.
                for inner in ast.walk(handler):
                    if isinstance(inner, ast.Call) and _callee(inner) in {
                        "killpg",
                        "_signal_session",
                        "_kill_tree",
                    }:
                        found[f"{rel}::{qual}"] = _callee(inner)

    unexplained = sorted(set(found) - set(_HAND_ROLLED_GROUP_KILL_ON_A_TIMEOUT))
    assert not unexplained, (
        "a timeout path signals a process group by hand instead of through "
        "cancellation.kill_timed_out:\n"
        + "\n".join(f"  {k}  (calls {found[k]})" for k in unexplained)
        + "\nkill_timed_out already checks group leadership and bounds the reap. Route "
        "through it rather than adding another copy."
    )
    stale = sorted(set(_HAND_ROLLED_GROUP_KILL_ON_A_TIMEOUT) - set(found))
    assert not stale, (
        "stale _HAND_ROLLED_GROUP_KILL_ON_A_TIMEOUT entr(y/ies) — collapsed already, so "
        "remove it:\n" + "\n".join(f"  {k}" for k in stale)
    )


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
async def test_gateway_auto_update_reaps_the_install_it_timed_out(monkeypatch, tmp_path):
    """A timed-out ``pip install -e .`` in the auto-update leaves NO live child behind.

    The defect this pins: ``gateway._auto_apply_update`` is the twin of
    ``dashboard/handlers/updates.py::api_update_apply``, and the reaping fix landed only
    on the twin. Its `pip install` had a ``wait_for`` and no ``TimeoutError`` handler, so
    a timeout unwound to the function's outer ``except Exception`` and the child was never
    signalled — **2 live processes per timed-out install** (the ``pip`` child and its
    forked build backend), still running after the call returned and the loop closed,
    accumulating once per auto-update poll.

    The site set here is smaller than when this class was first measured: the git stages
    moved into ``self_update``'s synchronous helpers, which run under
    ``subprocess.run(timeout=…)`` and so reap their own direct child inside CPython. The
    install is the one async spawn left in this function, and the one that can fork.

    The deadline is INJECTED (``_AUTOUPDATE_PIP_TIMEOUT``), never slept on.
    """
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
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(proj))
    monkeypatch.setattr(gw.sys, "executable", str(stub))
    monkeypatch.setattr(gw, "_AUTOUPDATE_PIP_TIMEOUT", 1.0)

    # Drive the nightly branch straight to the install: every git stage before it is a
    # `self_update` thread helper, stubbed to succeed so the deadline under test is the
    # only thing this exercises.
    ok = subprocess.CompletedProcess(args=["git"], returncode=0, stdout="", stderr="")
    monkeypatch.setattr(self_update, "git_tracked_changes", lambda _proj: [])
    monkeypatch.setattr(self_update, "resolve_default_branch", lambda _proj: "main")
    monkeypatch.setattr(self_update, "git_fetch", lambda _proj, _branch: ok)
    monkeypatch.setattr(self_update, "git_is_up_to_date", lambda _proj, _branch: False)
    monkeypatch.setattr(self_update, "git_fast_forward", lambda _proj, _branch: ok)
    monkeypatch.setattr(self_update, "package_root", lambda _proj: proj)
    monkeypatch.setattr(
        AppConfig,
        "load",
        classmethod(
            lambda cls: SimpleNamespace(updates=SimpleNamespace(channel="nightly", pin=""))
        ),
    )

    orch = gw.GatewayOrchestrator.__new__(gw.GatewayOrchestrator)
    orch.dashboard_state = None
    orch.sessions = None

    started = time.monotonic()
    await orch._auto_apply_update()
    elapsed = time.monotonic() - started

    recorded = [ln.split() for ln in pids.read_text().split("\n") if ln.strip()]
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
    pids = tmp_path / "pids"
    script = tmp_path / "forks.sh"
    script.write_text(
        f'#!/bin/sh\necho "child $$" >> "{pids}"\n'
        f'/bin/sleep {GRANDCHILD_SECS} &\necho "grandchild $!" >> "{pids}"\nwait\n'
    )
    script.chmod(0o755)

    proc = await asyncio.create_subprocess_exec(
        str(script),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    pgid = os.getpgid(proc.pid)
    assert pgid == proc.pid, "the control must lead its own group for its cleanup to be safe"
    try:
        with contextlib.suppress(asyncio.TimeoutError, TimeoutError):
            await asyncio.wait_for(proc.communicate(), timeout=1)
        # ...and then NO teardown whatsoever: the pre-fix arm did not exist.
        await asyncio.sleep(0.3)

        recorded = [ln.split() for ln in pids.read_text().split("\n") if ln.strip()]
        assert {r for r, _ in recorded} == {"child", "grandchild"}
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
