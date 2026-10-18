"""The source-tree checks split from
``tests/test_kill_timed_out_pipe_block.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import source_index
from kill_timed_out_pipe_block_shared import (
    _async_spawn_vars,
    _callee,
    _iter_functions,
    _reaped_by_owner,
    _spawn_functions,
    _spawns_by_target,
    _timeout_handlers,
    _timeout_kill_style,
)

from personalclaw.loop import gates

_SRC = Path(gates.__file__).resolve().parents[1]


# From the kill-site census. A spawn earns its own session ONLY when the child can fork a
# grandchild that inherits a live pipe. Everything else is git plumbing that never forks;
# giving it a session buys nothing and widens the blast radius of a group signal.
# The `git pull` spawn is retired (the git kind rides release tags now; the
# advance goes through `asyncio.to_thread(self_update.git_*)`, sync `subprocess.run`
# under one seam, not a create_subprocess_exec here), and the dashboard dirty-tree
# check moved to `asyncio.to_thread(self_update.git_tracked_changes)` — so the `pull`
# group-leader and the `dirty` leaf spawn are gone from this module's census. The checkout's
# install moved to `checkout_update.py` with the rest of the checkout's update (its census is
# below).
_UPDATES_GROUP_LED = {
    "proc",  # git fetch      -> forks git-remote-https / ssh
    "pip_up",  # pip -U         -> forks build backends / compilers
}


_UPDATES_LEAF = {
    "local",  # git rev-parse HEAD
    "remote",  # git rev-parse @{u}
    "show",  # git show <sha>:./pyproject.toml
    "tags",  # git tag --list v*
    "diff",  # git diff <range> -- CHANGELOG.md
}


def _package_tree(*parts: str) -> ast.Module:
    """The tree of ``src/personalclaw/<parts>``, from the suite's one source index."""
    return source_index.tree(_SRC.joinpath(*parts))


def test_only_the_censused_spawns_lead_their_own_group():
    """Both directions: the two forking spawns opt in, the five leaves stay out."""
    src = _package_tree("dashboard", "handlers", "updates.py")
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


def test_updates_timeout_handlers_match_the_census_exactly():
    """The two forking spawns kill their GROUP; the five leaves still kill by pid.

    Bidirectional on purpose. A leaf drifting to ``group`` means someone blanket-swept
    and gave a non-forking child a session it doesn't need; a forking spawn drifting to
    ``pid`` means the grandchild is holding the pipe again.
    """
    src = _package_tree("dashboard", "handlers", "updates.py")
    style = _timeout_kill_style(src)

    assert {n for n, s in style.items() if s == "group"} == _UPDATES_GROUP_LED, (
        "the set of group-killed spawns in updates.py drifted from the census; "
        f"found {sorted(n for n, s in style.items() if s == 'group')}"
    )
    assert {n for n, s in style.items() if s == "pid"} == _UPDATES_LEAF, (
        "the set of pid-killed spawns in updates.py drifted from the census; "
        f"found {sorted(n for n, s in style.items() if s == 'pid')}"
    )


def test_the_checkouts_install_leads_its_group_and_its_timeout_kills_the_group():
    """checkout_update.py has ONE spawn: the checkout's install (pip install -e / uv sync), which
    forks build backends and compilers that inherit its pipes."""
    src = _package_tree("checkout_update.py")
    assert _timeout_kill_style(src) == {"install": "group"}
    spawns = _spawns_by_target(src, "create_subprocess_exec")
    assert set(spawns) == {"install"}, spawns
    assert "start_new_session" in spawns["install"], (
        "the checkout's install no longer leads its own session, so kill_timed_out falls back "
        "to a single-pid signal and its build backend holds the pipe open"
    )


def test_the_loop_gate_kills_its_shells_group():
    """gates.py has ONE spawn — an arbitrary ``/bin/sh -c``, so it always forks."""
    src = _package_tree("loop", "gates.py")
    assert _timeout_kill_style(src) == {"proc": "group"}
    spawns = _spawns_by_target(src, "create_subprocess_limited")
    assert "start_new_session" in spawns["proc"], (
        "the loop gate's shell no longer leads its own session, so kill_timed_out "
        "falls back to a single-pid signal and a test runner survives the 180s bound"
    )


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


def test_every_file_browser_spawn_reaps_its_timeout():
    """No spawn in files.py may time out and walk away, and the census must be complete.

    Three directions, because each is a way the module drifted or could drift again:
    an unclassified spawn (a NEW endpoint shelling out) reds naming itself; a censused
    reaper that stops reaping reds; and a leaf that acquires a session it doesn't need
    reds too.
    """
    src = _package_tree("dashboard", "handlers", "files.py")
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
    # `test_updates_timeout_handlers_match_the_census_exactly` above: these five never
    # fork, so a group signal would only widen the blast radius. Routing them through the
    # owner would red that rail, so the two rails are kept consistent here on purpose.
    "dashboard/handlers/updates.py::_do_update_check::local": "censused leaf (git rev-parse)",
    "dashboard/handlers/updates.py::_do_update_check::remote": "censused leaf (git rev-parse @{u})",
    "dashboard/handlers/updates.py::_do_update_check::show": "censused leaf (git show)",
    "dashboard/handlers/updates.py::_do_update_check::tags": "censused leaf (git tag)",
    "dashboard/handlers/updates.py::_do_update_check::diff": "censused leaf (git diff)",
    # ── still outstanding: pid-killed and reaped, so no hang; the grandchild leaks ──
    "dashboard/handlers/_shared.py::_list_marketplace_skills::proc": (
        "outstanding: `personalclaw skills list` pid-killed, unbounded drain"
    ),
    "workflows/review_service.py::_git::proc": (
        "outstanding: run-workspace `git diff` pid-killed; forks under fsmonitor/LFS"
    ),
    # ── still outstanding: NO teardown at all, but every one is a leaf tmux client ──
    # `tmux -S <home>/tmux.sock <verb>` against the home's OWN server. A hung client is a
    # leaked client, not a leaked tree, and `new_session` has 30 dependent test files — a
    # separable change, deliberately not swept in with this one.
    "tmux_substrate.py::new_session::proc": "outstanding: leaf tmux client, no teardown",
    "tmux_substrate.py::has_session::proc": "outstanding: leaf tmux client, no teardown",
    "tmux_substrate.py::list_sessions::proc": "outstanding: leaf tmux client, no teardown",
    "tmux_substrate.py::kill_session::proc": "outstanding: leaf tmux client, no teardown",
}


#: Async spawn sites with NO ``TimeoutError`` handler in their function at all. Listed so
#: the distinction is a decision rather than an omission: a site here has no deadline to
#: leak on, or its teardown lives in a different lifecycle.
_NO_TIMEOUT_PATH: dict[str, str] = {
    "sandbox.py::create_subprocess_limited::<unassigned>": "the ceiling helper — caller waits",
    "sandbox_providers/none.py::_NoneHandle.exec::<unassigned>": "provider handle — caller waits",
    "sandbox_providers/docker.py::_DockerHandle.exec::<unassigned>": "provider — caller waits",
    "sandbox_providers/lima.py::_LimaHandle.exec::<unassigned>": "provider — caller waits",
    "knowledge/pipeline/nodes/media_nodes.py::_run_cmd::proc": (
        "ffmpeg: no deadline of its own (its step's bound); a cancel kills it"
    ),
    "transcribe.py::_segment::proc": (
        "ffmpeg: no deadline of its own (its step's bound); a cancel kills it"
    ),
    "local_models/sidecar.py::run_once::proc": (
        "one model call in a child: no deadline of its own (the caller's bound); a cancel "
        "kills its group"
    ),
    "dashboard/handlers/terminal.py::api_terminal_ws::proc": (
        "long-lived interactive PTY — torn down by `_kill_pty_session` on close, not by a "
        "per-command deadline"
    ),
}


def _src_files() -> list[Path]:
    """Every package module, from the suite's one source index."""
    return list(source_index.package_files())


def _async_spawn_census() -> tuple[dict[str, bool], dict[str, str]]:
    """``key -> routed?`` for spawns on a timeout path, plus ``key -> reason-less`` for
    spawns with no timeout path at all."""
    routed: dict[str, bool] = {}
    no_path: dict[str, str] = {}
    for path in _src_files():
        tree = source_index.tree(path)
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
        tree = source_index.tree(path)
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
