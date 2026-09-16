"""Install-kind detection + the shared self-update primitives (contracts C1/C2).

Four fixtures — one per InstallKind — pin the resolution order:
env (container/desktop) wins first, then a .git working tree => git, else pip.
Each test isolates the two env vars the classifier reads (monkeypatch.delenv)
so it never inherits the runner's real environment.

The module under test moved out of ``dashboard/handlers/updates_kind.py`` into the
core package in DIST-13, so the CLI can reach the same decision the dashboard makes
without importing an HTTP handler.
"""

from __future__ import annotations

import asyncio
import subprocess

import aiohttp
import pytest

from personalclaw import self_update as uk
from personalclaw.dashboard.state import DashboardState
from personalclaw.self_update import detect_install_kind


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    monkeypatch.delenv("PERSONALCLAW_PROJECT_DIR", raising=False)


def test_container_env_wins(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # Even with a git tree present, the container env marker takes precedence.
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "container")
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
    assert detect_install_kind() == "container"


def test_desktop_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "desktop")
    assert detect_install_kind() == "desktop"


def test_env_kind_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "  Container ")
    assert detect_install_kind() == "container"


def test_unknown_env_kind_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    # A junk value is ignored — resolution falls through to git/pip probing.
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "banana")
    assert detect_install_kind() == "pip"


def test_git_when_project_dir_has_dot_git(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
    assert detect_install_kind() == "git"


def test_git_worktree_dot_git_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # In a git worktree/submodule, .git is a FILE pointing at the real gitdir.
    (tmp_path / ".git").write_text("gitdir: /somewhere/.git/worktrees/x\n")
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
    assert detect_install_kind() == "git"


def test_git_when_dot_git_in_monorepo_parent(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # Monorepo layout: the project dir is nested one level under the repo root
    # (which carries .git). The parent probe catches it.
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "PersonalClaw"
    nested.mkdir()
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(nested))
    assert detect_install_kind() == "git"


def test_pip_when_no_env_no_git(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # A project dir with NO .git (e.g. an unpacked source dir) is not "git".
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))
    assert detect_install_kind() == "pip"


def test_pip_when_nothing_set() -> None:
    # No env markers, no project dir -> a plain wheel/uv/pipx install.
    assert detect_install_kind() == "pip"


def test_install_kind_literal_values() -> None:
    # Guard the contract's value set (C1 / C2 wire shape).
    assert uk._ENV_KINDS == {"container", "desktop"}


# ── T4.2: tag-driven check + C2 payload ─────────────────────────────────────


def test_normalize_version_strips_leading_v() -> None:
    assert uk.normalize_version("v0.1.3") == "0.1.3"
    assert uk.normalize_version("0.1.3") == "0.1.3"
    assert uk.normalize_version("  v1.2.0 ") == "1.2.0"


def test_version_tuple_orders_numerically() -> None:
    assert uk.version_tuple("v0.2.0") > uk.version_tuple("0.1.9")
    assert uk.version_tuple("0.1.10") > uk.version_tuple("0.1.9")
    assert uk.version_tuple("garbage") == (0,)


def test_cache_round_trip(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    uk.write_release_cache({"tag": "v0.1.3", "etag": 'W/"abc"'})
    got = uk.read_release_cache()
    assert got["tag"] == "v0.1.3"
    assert got["etag"] == 'W/"abc"'


def test_read_cache_missing_is_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    assert uk.read_release_cache() == {}


@pytest.mark.asyncio
async def test_build_status_update_available(monkeypatch) -> None:
    async def _fake_release() -> dict:
        return {"tag": "v0.2.0", "name": "0.2.0", "body": "notes"}

    monkeypatch.setattr(uk, "fetch_latest_release", _fake_release)
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "container")
    status = await uk.build_update_status("0.1.0")
    assert status["kind"] == "container"
    assert status["current"] == "0.1.0"
    assert status["latest"] == "0.2.0"
    assert status["update_available"] is True
    assert status["apply_method"] == "instructions"
    assert status["instructions"]  # container carries pull+up commands
    assert status["commits_behind"] is None


@pytest.mark.asyncio
async def test_build_status_up_to_date_pip(monkeypatch) -> None:
    async def _fake_release() -> dict:
        return {"tag": "v0.1.0", "name": "0.1.0", "body": ""}

    monkeypatch.setattr(uk, "fetch_latest_release", _fake_release)
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    monkeypatch.delenv("PERSONALCLAW_PROJECT_DIR", raising=False)
    status = await uk.build_update_status("0.1.0")
    assert status["kind"] == "pip"
    assert status["update_available"] is False
    assert status["apply_method"] == "pip_upgrade"
    assert status["instructions"] == []


@pytest.mark.asyncio
async def test_build_status_offline_no_tag(monkeypatch) -> None:
    async def _empty_release() -> dict:
        return {}

    monkeypatch.setattr(uk, "fetch_latest_release", _empty_release)
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    monkeypatch.delenv("PERSONALCLAW_PROJECT_DIR", raising=False)
    status = await uk.build_update_status("0.1.0")
    # No latest known -> never claims an update is available (offline-tolerant).
    assert status["latest"] == ""
    assert status["update_available"] is False


@pytest.mark.asyncio
async def test_fetch_latest_release_offline_returns_cache(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    uk.write_release_cache({"tag": "v0.1.2", "etag": 'W/"x"'})

    class _BoomSession:
        def __init__(self, *a, **k):
            raise OSError("network down")

    monkeypatch.setattr(aiohttp, "ClientSession", _BoomSession)
    got = await uk.fetch_latest_release()
    assert got["tag"] == "v0.1.2"  # degraded to the cached view, no raise


# ── RUM-3: the egress kill switch + the config-driven cadence ────────────────
#
# `updates.check_enabled=false` is the privacy/egress opt-out README documents. The
# proof is not "returns the cached view" (an offline run does that too, and the
# function swallows exceptions, so a raise inside a monkeypatched HTTP layer would be
# silently eaten) — it is that the network layer is NEVER TOUCHED. Each negative test
# uses a RECORDING stub and asserts it was called zero times, which is immune to the
# swallow; a matching positive control proves the switch is a GATE, not a constant.


def _write_updates_config(home, **updates) -> None:
    """Write a minimal config.json under `home` with an `updates` block."""
    import json

    (home / "config.json").write_text(json.dumps({"updates": updates}), encoding="utf-8")


@pytest.mark.asyncio
async def test_fetch_latest_release_kill_switch_makes_zero_calls(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _write_updates_config(tmp_path, check_enabled=False)
    uk.write_release_cache({"tag": "v0.1.2", "etag": 'W/"x"'})

    opened: list[str] = []

    class _RecordingSession:
        def __init__(self, *a, **k):
            opened.append("ClientSession")
            raise OSError("network")  # also degrade, in case the guard ever regressed

    monkeypatch.setattr(aiohttp, "ClientSession", _RecordingSession)

    got = await uk.fetch_latest_release()
    # The immune proof: no session was ever opened. `got == cache` alone would pass even
    # if a call had been made and swallowed — the empty `opened` is what pins the switch.
    assert opened == [], "fetch_latest_release opened a network session while check_enabled=false"
    assert got == {"tag": "v0.1.2", "etag": 'W/"x"'}  # the cached view, untouched


@pytest.mark.asyncio
async def test_fetch_latest_release_hits_network_when_enabled(monkeypatch, tmp_path) -> None:
    # Positive control: with the check ON, the function DOES open a session (so the test
    # above is a gate, not a constant). We degrade to the cache to keep it hermetic.
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _write_updates_config(tmp_path, check_enabled=True)
    uk.write_release_cache({"tag": "v0.1.2", "etag": 'W/"x"'})

    opened: list[str] = []

    class _RecordingSession:
        def __init__(self, *a, **k):
            opened.append("ClientSession")
            raise OSError("network")

    monkeypatch.setattr(aiohttp, "ClientSession", _RecordingSession)

    got = await uk.fetch_latest_release()
    assert opened == ["ClientSession"], "check_enabled=true must attempt the network"
    assert got == {"tag": "v0.1.2", "etag": 'W/"x"'}  # degraded to cache, no raise


class _FakeProc:
    """A subprocess stand-in whose fetch 'fails' so `_do_update_check` returns after one call."""

    returncode = 1

    async def communicate(self):
        return (b"", b"git fetch failed")


@pytest.mark.asyncio
async def test_do_update_check_kill_switch_runs_no_subprocess(monkeypatch, tmp_path) -> None:
    from personalclaw.dashboard.handlers import updates as dash_updates

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _write_updates_config(tmp_path, check_enabled=False)
    # A VALID project dir so the kill switch — not the "no project dir" guard — is what
    # stops the check; otherwise the assertion would pass vacuously.
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))

    calls: list[tuple] = []

    async def _rec(*a, **k):
        calls.append(a)
        return _FakeProc()

    monkeypatch.setattr(dash_updates.asyncio, "create_subprocess_exec", _rec)
    dash_updates._update_info["checked"] = False

    await dash_updates._do_update_check()
    assert calls == [], "_do_update_check ran a subprocess while check_enabled=false"


@pytest.mark.asyncio
async def test_do_update_check_runs_git_fetch_when_enabled(monkeypatch, tmp_path) -> None:
    # Positive control: with the check ON and a valid project dir, `_do_update_check`
    # runs `git fetch`. Pairs with the kill-switch test to make it a gate.
    from personalclaw.dashboard.handlers import updates as dash_updates

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _write_updates_config(tmp_path, check_enabled=True)
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))

    calls: list[tuple] = []

    async def _rec(*a, **k):
        calls.append(a)
        return _FakeProc()  # returncode=1 -> the check returns after this one fetch

    monkeypatch.setattr(dash_updates.asyncio, "create_subprocess_exec", _rec)

    await dash_updates._do_update_check()
    assert calls, "check_enabled=true must run the git fetch subprocess"
    assert calls[0][0] == "git" and calls[0][1] == "fetch"


def test_scheduled_check_due_reads_interval_hours() -> None:
    # The cadence is CONFIG-DRIVEN: the boundary moves with `check_interval_hours`, so a
    # hard-coded 43200s / 12h constant would fail the 6h and 1h cases below.
    from personalclaw.config.loader import AppConfig
    from personalclaw.dashboard.handlers.updates import _scheduled_check_due

    now = 1_000_000.0
    cfg = AppConfig()
    cfg.updates.check_enabled = True

    cfg.updates.check_interval_hours = 6
    assert _scheduled_check_due(cfg, now - (6 * 3600 - 1), now) is False  # just under -> not due
    assert _scheduled_check_due(cfg, now - (6 * 3600 + 1), now) is True  # just over -> due

    cfg.updates.check_interval_hours = 1  # a DIFFERENT interval moves the boundary
    assert _scheduled_check_due(cfg, now - (3600 - 1), now) is False
    assert _scheduled_check_due(cfg, now - (3600 + 1), now) is True


def test_scheduled_check_due_kill_switch() -> None:
    from personalclaw.config.loader import AppConfig
    from personalclaw.dashboard.handlers.updates import _scheduled_check_due

    now = 1_000_000.0
    cfg = AppConfig()
    cfg.updates.check_enabled = False
    cfg.updates.check_interval_hours = 1
    # Even with an eternity elapsed, a disabled check is never due.
    assert _scheduled_check_due(cfg, 0.0, now) is False


# ── The opt-in staged auto-update gate: updates.auto decides the APPLY, not the CHECK ──
#
# The boot-path check runs first and UNCONDITIONALLY — its own egress kill switch is
# `updates.check_enabled` (RUM-3), proven by test_do_update_check_kill_switch_runs_no_subprocess
# / test_fetch_latest_release_kill_switch_makes_zero_calls. `updates.auto` (RUM-5, which RETIRED
# the legacy `auto_update` bool) then decides what happens to an AVAILABLE update:
#   • "off" (the default) — NOTIFY ONLY: raise the `update_available` refresh, never apply.
#   • "staged" — apply at the next safe point: HOLD while a session/subagent is in flight
#     (`DashboardState.active_work_snapshot`) and fire only once idle, on the resolved tag.
# These tests pin that gate at the boot-path call site so a doc/behaviour drift reddens here.


class _FakeAgent:
    def __init__(self, done: bool) -> None:
        self.done = done


class _StubSubagents:
    def __init__(self, agents: "list[_FakeAgent]") -> None:
        self.all_agents = agents


class _StubSessions:
    def __init__(self, n: int) -> None:
        self._sessions = {f"s{i}": object() for i in range(n)}


class _StubDashboardState:
    """The surfaces `active_work_snapshot` + `_check_for_updates` + `_auto_apply_update`
    touch, made controllable: a mutable running-agent / session count, a `push_refresh`
    recorder, and no-op progress hooks. `active_work_snapshot` is the REAL
    `DashboardState` method bound onto the stub, so the gate runs the production logic."""

    def __init__(self, *, running_agents: int = 0, sessions: int = 0) -> None:
        self.subagents = _StubSubagents([_FakeAgent(False) for _ in range(running_agents)])
        self.sessions = _StubSessions(sessions)
        self.refreshes: list[str] = []

    def drain(self) -> None:
        self.subagents.all_agents = []
        self.sessions._sessions = {}

    def push_refresh(self, kind: str) -> None:
        self.refreshes.append(kind)

    def push_update_progress(self, *_a, **_k) -> None:
        pass

    def clear_update_progress(self, *_a, **_k) -> None:
        pass

    active_work_snapshot = DashboardState.active_work_snapshot


def _orchestrator_stub(applied: list[str], *, dashboard_state=None):
    """A stand-in for the boot-path caller carrying the REAL staged-apply methods so the gate
    under test runs unmodified; only `_auto_apply_update` is a recorder."""
    from personalclaw.gateway import GatewayOrchestrator

    class _Stub:
        _staged_apply_task = None

        def __init__(self) -> None:
            self.dashboard_state = dashboard_state

        async def _auto_apply_update(self) -> None:
            applied.append("apply")

        _work_in_flight = GatewayOrchestrator._work_in_flight
        _staged_auto_apply = GatewayOrchestrator._staged_auto_apply
        _await_idle_then_apply = GatewayOrchestrator._await_idle_then_apply

    return _Stub()


async def _drive_check_for_updates(monkeypatch, *, auto: str, dashboard_state=None):
    """Run `GatewayOrchestrator._check_for_updates` against stubs, recording call order."""
    from personalclaw.dashboard import handlers as dash_handlers
    from personalclaw.gateway import GatewayOrchestrator

    order: list[str] = []
    applied: list[str] = []

    async def _fake_check() -> None:
        order.append("check")

    class _Updates:
        pass

    _Updates.auto = auto

    class _Cfg:
        updates = _Updates()

    def _load(*_a, **_k):
        order.append("config")
        return _Cfg()

    monkeypatch.setattr(dash_handlers, "_do_update_check", _fake_check)
    # `available` truthy so the updates.auto branch is actually reached; a falsy value would
    # short-circuit before the config read and make the ordering assertion vacuous.
    monkeypatch.setattr(dash_handlers, "_update_info", {"available": True})
    monkeypatch.setattr("personalclaw.config.AppConfig.load", _load)

    stub = _orchestrator_stub(applied, dashboard_state=dashboard_state)
    await GatewayOrchestrator._check_for_updates(stub)
    return order, applied, stub


@pytest.mark.asyncio
async def test_off_is_notify_only_and_never_applies(monkeypatch) -> None:
    # RUM-5 done_when: with updates.auto="off" (the default) the boot-path check still runs,
    # then the available update is NOTIFIED and NEVER applied. The check's own switch is
    # updates.check_enabled; here the check is stubbed so its config read is absent from
    # `order`. This isolates the apply gate.
    state = _StubDashboardState()
    order, applied, _stub = await _drive_check_for_updates(
        monkeypatch, auto="off", dashboard_state=state
    )
    assert order == ["check", "config"], (
        "the boot-path update check must run BEFORE updates.auto is consulted — got "
        f"{order}. updates.auto decides only whether the apply follows, never whether the "
        "check happens (the check's own switch is updates.check_enabled)."
    )
    assert applied == []  # "off" NEVER applies
    assert state.refreshes == ["update_available"]  # ...it ONLY notifies


@pytest.mark.asyncio
async def test_staged_when_idle_reaches_the_apply(monkeypatch) -> None:
    # The other branch, so the test above is a gate not a constant: with an IDLE tree
    # (no agents/sessions) "staged" applies inline and does NOT fall back to notify-and-stop.
    state = _StubDashboardState()  # zero agents, zero sessions => idle
    order, applied, stub = await _drive_check_for_updates(
        monkeypatch, auto="staged", dashboard_state=state
    )
    assert order == ["check", "config"]
    assert applied == ["apply"]  # staged + idle => applied
    assert state.refreshes == []  # staged applies; it does not notify-and-stop
    assert stub._staged_apply_task is None  # idle => applied inline, no background hold


class _Ret:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


class _FakeOkProc:
    returncode = 0

    async def communicate(self):
        return (b"", b"")


@pytest.mark.asyncio
async def test_staged_holds_until_active_work_drains_then_applies_on_resolved_tag(
    monkeypatch, tmp_path
) -> None:
    """RUM-5 done_when centerpiece — with a session ACTIVE the staged apply HOLDS (nothing
    applied, a background waiter parks); once the work DRAINS it fires EXACTLY once, and the
    apply it runs lands on the RESOLVED release tag (git checkout <tag>), never main / a branch
    / a reset."""
    from personalclaw import gateway as gw
    from personalclaw.gateway import GatewayOrchestrator

    monkeypatch.setattr(gw, "_STAGED_APPLY_POLL_SECS", 0.001)

    # A stable-channel config drives the release-tag path (not nightly branch-tracking).
    class _Updates:
        channel = "stable"
        pin = ""

    class _Cfg:
        updates = _Updates()

    monkeypatch.setattr("personalclaw.config.AppConfig.load", lambda *_a, **_k: _Cfg())

    # Record the real apply's release-path seams instead of touching git / pip / the process.
    calls: list[tuple] = []

    async def _resolve(channel, pin=""):
        calls.append(("resolve", channel, pin))
        return "v9.9.9"

    def _fetch_tags(_proj):
        calls.append(("fetch_tags",))
        return _Ret(0)

    def _checkout(_proj, tag):
        calls.append(("checkout", tag))
        return _Ret(0)

    def _reset_or_branch_boom(*_a, **_k):  # the retired branch/reset paths must NEVER run
        raise AssertionError("staged release apply reached a branch / fast-forward / reset path")

    monkeypatch.setattr(uk, "resolve_target", _resolve)
    monkeypatch.setattr(uk, "git_tracked_changes", lambda _p: [])  # clean tree => proceeds
    monkeypatch.setattr(uk, "git_fetch_tags", _fetch_tags)
    monkeypatch.setattr(uk, "git_checkout", _checkout)
    monkeypatch.setattr(uk, "git_fast_forward", _reset_or_branch_boom, raising=False)
    monkeypatch.setattr(uk, "resolve_default_branch", _reset_or_branch_boom, raising=False)
    monkeypatch.setattr(uk, "package_root", lambda _p: str(tmp_path))
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(tmp_path))

    async def _fake_pip(*_a, **_k):
        return _FakeOkProc()

    monkeypatch.setattr(gw.asyncio, "create_subprocess_exec", _fake_pip)

    async def _fake_build(*_a, **_k):
        calls.append(("build",))

    monkeypatch.setattr(gw, "build_frontend_async", _fake_build)

    async def _fake_reexec(_state):
        calls.append(("reexec",))

    monkeypatch.setattr("personalclaw.dashboard.handlers.updates._graceful_reexec", _fake_reexec)

    # A stub carrying the REAL staged + apply methods, with ONE session live.
    state = _StubDashboardState(sessions=1)

    class _Stub:
        _staged_apply_task = None

        def __init__(self) -> None:
            self.dashboard_state = state

        _work_in_flight = GatewayOrchestrator._work_in_flight
        _staged_auto_apply = GatewayOrchestrator._staged_auto_apply
        _await_idle_then_apply = GatewayOrchestrator._await_idle_then_apply
        _auto_apply_update = GatewayOrchestrator._auto_apply_update

    stub = _Stub()

    # Work is in flight: the staged entry must HOLD — no apply, a waiter parked.
    await stub._staged_auto_apply()
    assert stub._staged_apply_task is not None
    await asyncio.sleep(0.02)  # give the waiter several poll cycles while still busy
    assert calls == [], f"staged apply fired while work was in flight: {calls}"
    assert not stub._staged_apply_task.done()

    # Release the work — the waiter must now fire the apply, exactly once, on the tag.
    state.drain()
    await asyncio.wait_for(stub._staged_apply_task, timeout=2.0)

    assert ("resolve", "stable", "") in calls  # resolved the channel/pin target
    assert ("checkout", "v9.9.9") in calls  # ...and checked out THAT tag
    assert ("build",) in calls and ("reexec",) in calls  # ran the apply tail to completion
    # Exactly one checkout (fired once, not per poll), and it followed the resolve.
    assert [c for c in calls if c[0] == "checkout"] == [("checkout", "v9.9.9")]
    assert calls.index(("resolve", "stable", "")) < calls.index(("checkout", "v9.9.9"))


# ── C2 wire-shape conformance (Tier-S once clients read it) ──────────────────


@pytest.mark.asyncio
async def test_c2_wire_shape_conformance(monkeypatch) -> None:
    """build_update_status emits exactly the C2 contract keys (+ additive extras),
    with the per-kind apply_method / commits_behind / instructions semantics the
    plan pins. Locks the Tier-S wire shape against silent drift."""

    async def _rel() -> dict:
        return {"tag": "v0.2.0", "name": "0.2.0", "body": "notes"}

    monkeypatch.setattr(uk, "fetch_latest_release", _rel)
    monkeypatch.delenv("PERSONALCLAW_PROJECT_DIR", raising=False)

    required = {
        "kind",
        "current",
        "latest",
        "update_available",
        "commits_behind",
        "apply_method",
        "instructions",
    }

    # container: apply_method=instructions, commits_behind=null, instructions non-empty
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "container")
    c = await uk.build_update_status("0.1.0")
    assert required <= set(c)
    assert c["apply_method"] == "instructions"
    assert c["commits_behind"] is None
    assert isinstance(c["instructions"], list) and c["instructions"]

    # desktop: apply_method=desktop_delegate
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "desktop")
    d = await uk.build_update_status("0.1.0")
    assert d["apply_method"] == "desktop_delegate"

    # pip: apply_method=pip_upgrade, commits_behind=null, instructions=[]
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    p = await uk.build_update_status("0.1.0")
    assert p["apply_method"] == "pip_upgrade"
    assert p["commits_behind"] is None
    assert p["instructions"] == []
    # current/latest are normalized (no leading v)
    assert p["current"] == "0.1.0"
    assert p["latest"] == "0.2.0"
    assert p["update_available"] is True


# ── DIST-13: the default branch is resolved, not guessed ────────────────────


class _GitScript:
    """Fake ``_run_git`` driven by a per-subcommand script; records every call."""

    def __init__(self, **replies: tuple[int, str]) -> None:
        self.calls: list[list[str]] = []
        self._replies = replies

    def __call__(self, args, *, cwd, timeout):  # type: ignore[no-untyped-def]
        import subprocess

        self.calls.append(list(args))
        rc, out = self._replies.get(args[0], (1, ""))
        return subprocess.CompletedProcess(["git", *args], rc, out, "")


def test_default_branch_prefers_the_checked_out_branch(monkeypatch) -> None:
    # Updating means "advance the branch I am on" — a contributor on a feature
    # branch must not be reset onto another one.
    git = _GitScript(**{"rev-parse": (0, "feature-foo\n")})
    monkeypatch.setattr(uk, "_run_git", git)
    assert uk.resolve_default_branch("/x") == "feature-foo"
    assert [c[0] for c in git.calls] == ["rev-parse"]  # no further probes needed


def test_default_branch_detached_head_reads_the_remote_head(monkeypatch) -> None:
    # A checkout parked on a release tag reports "HEAD"; origin/HEAD is the answer,
    # and it is a LOCAL ref, so this stays offline-safe.
    git = _GitScript(**{"rev-parse": (0, "HEAD\n"), "symbolic-ref": (0, "origin/main\n")})
    monkeypatch.setattr(uk, "_run_git", git)
    assert uk.resolve_default_branch("/x") == "main"


def test_default_branch_falls_back_to_remote_show(monkeypatch) -> None:
    # No refs/remotes/origin/HEAD (older clone, or a hand-added remote): ask the
    # remote. Last among the probes because it needs the network.
    git = _GitScript(
        **{
            "rev-parse": (0, "\n"),
            "symbolic-ref": (1, ""),
            "remote": (0, "* remote origin\n  HEAD branch: trunk\n  Fetch URL: x\n"),
        }
    )
    monkeypatch.setattr(uk, "_run_git", git)
    assert uk.resolve_default_branch("/x") == "trunk"


def test_default_branch_ignores_an_unknown_remote_head(monkeypatch) -> None:
    # A remote with no branches reports "HEAD branch: (unknown)" — not a branch name.
    git = _GitScript(
        **{
            "rev-parse": (0, "HEAD\n"),
            "symbolic-ref": (1, ""),
            "remote": (0, "  HEAD branch: (unknown)\n"),
        }
    )
    monkeypatch.setattr(uk, "_run_git", git)
    assert uk.resolve_default_branch("/x") == uk.DEFAULT_BRANCH_FALLBACK


def test_default_branch_last_resort_is_this_repo_s_real_default(monkeypatch) -> None:
    """Every probe fails ⇒ the literal fallback, and it must NAME A REAL BRANCH.

    The CLI hardcoded ``mainline`` — a branch this repository has never had — so a
    detached-HEAD update fetched an unresolvable ref and failed confusingly.
    """
    git = _GitScript()  # every subcommand returns rc=1
    monkeypatch.setattr(uk, "_run_git", git)
    assert uk.resolve_default_branch("/x") == "main"
    assert uk.DEFAULT_BRANCH_FALLBACK == "main"


def test_no_module_hardcodes_a_branch_this_repo_does_not_have() -> None:
    """Regression rail for the `mainline` default (DIST-13).

    Cheap and exact: the string must not reappear in either updater surface, in a
    fallback or a comment that a later edit could copy back into code.
    """
    from pathlib import Path

    import personalclaw

    root = Path(personalclaw.__file__).parent
    for name in ("self_update.py", "cli_server.py", "gateway.py"):
        assert "mainline" not in (root / name).read_text(encoding="utf-8"), name


def test_run_git_reports_a_timeout_as_a_failure_not_an_exception(monkeypatch) -> None:
    """A timeout is an ordinary updater failure: non-zero + a reason, never a raise.

    Callers report `stderr` and stop; making them wrap every probe in try/except is
    how a timeout ends up swallowed instead.
    """
    import subprocess

    def _boom(*a, **k):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(subprocess, "run", _boom)
    res = uk._run_git(["status"], cwd="/x", timeout=1)
    assert res.returncode == 124
    assert "timed out" in res.stderr


def test_run_git_reports_a_missing_git_binary(monkeypatch) -> None:
    import subprocess

    def _missing(*a, **k):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", _missing)
    res = uk._run_git(["status"], cwd="/x", timeout=1)
    assert res.returncode == 127
    assert "cannot run git" in res.stderr


def test_tracked_changes_excludes_untracked_entries(monkeypatch) -> None:
    # Untracked files survive a checkout / fast-forward, so warning about them would
    # train the reader to click through the warning that matters.
    git = _GitScript(**{"status": (0, " M a.py\n?? scratch.txt\nA  b.py\n")})
    monkeypatch.setattr(uk, "_run_git", git)
    assert uk.git_tracked_changes("/x") == [" M a.py", "A  b.py"]


def test_upgrade_spec_pins_a_known_tag_and_falls_back_unpinned() -> None:
    assert uk.upgrade_spec("v0.1.4") == "personalclaw==0.1.4"
    assert uk.upgrade_spec("") == "personalclaw"


def test_git_root_finds_the_worktree_that_carries_dot_git(tmp_path) -> None:
    nested = tmp_path / "PersonalClaw"
    nested.mkdir()
    (tmp_path / ".git").mkdir()
    # Git runs at the repo root even when the project dir is the nested package.
    assert uk.git_root(str(nested)) == str(tmp_path)
    assert uk.git_root("") == ""


# ── RUM-2: channel + pin resolver ───────────────────────────────────────────
#
# A faked releases list (the normalized view fetch_releases emits) that is
# ADVERSARIAL to every shortcut a resolver might take:
#   - the newest entry is a PRERELEASE and comes FIRST → `stable` must SKIP
#     index 0, killing a "return releases[0]" cheat;
#   - stable's answer (v0.2.1) and beta's answer (v0.3.0-rc.1) are DIFFERENT
#     tags → a resolver that ignores the channel fails one of them;
#   - the pin target (v0.2.0) is OLDER than both channel answers → a "return
#     newest" cheat cannot satisfy pin-hit;
#   - no branch ever returns the last entry (v0.1.3) → "return releases[-1]"
#     is dead too.
_FAKE_RELEASES: list[dict[str, object]] = [
    {"tag": "v0.3.0-rc.1", "prerelease": True, "name": "0.3.0-rc.1", "body": "beta notes"},
    {"tag": "v0.2.1", "prerelease": False, "name": "0.2.1", "body": "stable notes"},
    {"tag": "v0.2.0", "prerelease": False, "name": "0.2.0", "body": ""},
    {"tag": "v0.1.3", "prerelease": False, "name": "0.1.3", "body": ""},
]


def test_select_target_stable_excludes_prereleases() -> None:
    got = uk.select_target(_FAKE_RELEASES, "stable")
    assert got == "v0.2.1"  # newest NON-prerelease
    assert got != "v0.3.0-rc.1"  # ...and never the newer prerelease


def test_select_target_beta_includes_prereleases() -> None:
    got = uk.select_target(_FAKE_RELEASES, "beta")
    assert got == "v0.3.0-rc.1"  # newest release INCLUDING prereleases
    assert got != "v0.2.1"  # ...not merely the newest stable


def test_select_target_pin_overrides_channel_with_an_exact_hit() -> None:
    # A non-empty pin wins over the channel and names EXACTLY the pinned release —
    # even an OLDER one than either channel would pick.
    for channel in ("stable", "beta", "nightly"):
        assert uk.select_target(_FAKE_RELEASES, channel, "0.2.0") == "v0.2.0"
    # A leading v on the pin is tolerated (normalized both sides).
    assert uk.select_target(_FAKE_RELEASES, "stable", "v0.2.0") == "v0.2.0"
    # Guard: the pin answer is genuinely the pinned tag, not a channel default.
    assert uk.select_target(_FAKE_RELEASES, "stable", "0.2.0") != uk.select_target(
        _FAKE_RELEASES, "stable"
    )
    assert uk.select_target(_FAKE_RELEASES, "beta", "0.2.0") != uk.select_target(
        _FAKE_RELEASES, "beta"
    )


def test_select_target_pin_miss_returns_empty() -> None:
    # A pin to a version with no matching release resolves to "" (not the newest).
    assert uk.select_target(_FAKE_RELEASES, "stable", "9.9.9") == ""
    assert uk.select_target(_FAKE_RELEASES, "beta", "9.9.9") == ""


def test_select_target_nightly_is_branch_tracking_not_a_tag() -> None:
    # nightly follows the checked-out branch, so there is no release tag to name.
    assert uk.select_target(_FAKE_RELEASES, "nightly") == ""


def test_select_target_prerelease_detected_by_tag_suffix() -> None:
    # The `prerelease` flag is False, but a `-rc`/`-beta` tag suffix ALSO marks a
    # prerelease (tag convention §3.6): stable skips it, beta takes it.
    rels: list[dict[str, object]] = [
        {"tag": "v0.4.0-rc.1", "prerelease": False, "name": "", "body": ""},
        {"tag": "v0.3.9", "prerelease": False, "name": "", "body": ""},
    ]
    assert uk.select_target(rels, "stable") == "v0.3.9"
    assert uk.select_target(rels, "beta") == "v0.4.0-rc.1"


def test_select_target_beta_tie_prefers_the_published_release() -> None:
    # When a stable release and its own release candidate share a version, beta
    # offers the PUBLISHED one — the tie-break, not an accident of list order.
    rels: list[dict[str, object]] = [
        {"tag": "v0.3.0-rc.1", "prerelease": True, "name": "", "body": ""},
        {"tag": "v0.3.0", "prerelease": False, "name": "", "body": ""},
    ]
    assert uk.select_target(rels, "beta") == "v0.3.0"


def test_select_target_empty_list_returns_empty() -> None:
    # No releases known (offline, empty cache) -> "" on every channel, never raises.
    for channel in ("stable", "beta", "nightly"):
        assert uk.select_target([], channel) == ""
    assert uk.select_target([], "stable", "0.2.0") == ""


@pytest.mark.asyncio
async def test_resolve_target_proves_all_four_branches(monkeypatch) -> None:
    """The done_when headline: a faked releases list proves stable / beta /
    pin-hit / pin-miss through the real resolve_target seam."""

    async def _fake_releases() -> list[dict[str, object]]:
        return _FAKE_RELEASES

    monkeypatch.setattr(uk, "fetch_releases", _fake_releases)
    assert await uk.resolve_target("stable") == "v0.2.1"  # stable
    assert await uk.resolve_target("beta") == "v0.3.0-rc.1"  # beta
    assert await uk.resolve_target("stable", "0.2.0") == "v0.2.0"  # pin-hit (overrides)
    assert await uk.resolve_target("beta", "9.9.9") == ""  # pin-miss


@pytest.mark.asyncio
async def test_resolve_target_offline_no_cache_returns_empty_never_raises(
    monkeypatch, tmp_path
) -> None:
    # Offline with no prior cache: "" on every branch, and NEVER a raise.
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))

    class _BoomSession:
        def __init__(self, *a, **k):
            raise OSError("network down")

    monkeypatch.setattr(aiohttp, "ClientSession", _BoomSession)
    assert await uk.resolve_target("stable") == ""
    assert await uk.resolve_target("beta") == ""
    assert await uk.resolve_target("stable", "0.2.0") == ""


@pytest.mark.asyncio
async def test_fetch_releases_offline_returns_cached_list(monkeypatch, tmp_path) -> None:
    # A network failure degrades to the cached list; resolve_target then still
    # answers from that cache (offline-tolerant).
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    uk.write_releases_cache(
        {
            "releases": [
                {"tag": "v0.3.0-rc.1", "prerelease": True, "name": "", "body": ""},
                {"tag": "v0.2.1", "prerelease": False, "name": "", "body": ""},
            ],
            "etag": 'W/"cached"',
        }
    )

    class _BoomSession:
        def __init__(self, *a, **k):
            raise OSError("network down")

    monkeypatch.setattr(aiohttp, "ClientSession", _BoomSession)
    rels = await uk.fetch_releases()
    assert [r["tag"] for r in rels] == ["v0.3.0-rc.1", "v0.2.1"]  # cached view, no raise
    assert await uk.resolve_target("stable") == "v0.2.1"  # resolves from the cache


# ── RUM-2: the ETag-cached fetch path (200 refresh + 304 conditional) ────────


class _FakeResp:
    def __init__(self, status, payload=None, headers=None) -> None:
        self.status = status
        self._payload = payload
        self.headers = headers or {}

    async def json(self):  # type: ignore[no-untyped-def]
        return self._payload

    async def __aenter__(self):  # type: ignore[no-untyped-def]
        return self

    async def __aexit__(self, *a):  # type: ignore[no-untyped-def]
        return False


class _FakeSession:
    """Records the outbound request so the ETag conditional can be asserted."""

    last_url = ""
    last_headers: dict = {}

    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp

    def get(self, url, headers=None):  # type: ignore[no-untyped-def]
        _FakeSession.last_url = url
        _FakeSession.last_headers = dict(headers or {})
        return self._resp

    async def __aenter__(self):  # type: ignore[no-untyped-def]
        return self

    async def __aexit__(self, *a):  # type: ignore[no-untyped-def]
        return False


@pytest.mark.asyncio
async def test_fetch_releases_200_maps_and_caches(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    payload = [
        {"tag_name": "v0.3.0-rc.1", "name": "rc", "body": "b", "prerelease": True},
        {"tag_name": "v0.2.1", "name": "stable", "body": "s", "prerelease": False},
        "not-a-dict",  # a junk element is dropped defensively
    ]
    resp = _FakeResp(200, payload, {"ETag": 'W/"fresh"'})
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeSession(resp))

    rels = await uk.fetch_releases()
    # tag_name -> tag, prerelease preserved, junk element dropped.
    assert [r["tag"] for r in rels] == ["v0.3.0-rc.1", "v0.2.1"]
    assert rels[0]["prerelease"] is True and rels[1]["prerelease"] is False
    # Hit the releases LIST endpoint (not releases/latest), and cached the ETag.
    assert "/releases" in _FakeSession.last_url and "latest" not in _FakeSession.last_url
    assert uk.read_releases_cache()["etag"] == 'W/"fresh"'


@pytest.mark.asyncio
async def test_fetch_releases_304_returns_cache_and_sends_conditional(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    uk.write_releases_cache(
        {
            "releases": [{"tag": "v0.2.1", "prerelease": False, "name": "", "body": ""}],
            "etag": 'W/"prev"',
        }
    )
    resp = _FakeResp(304)
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeSession(resp))

    rels = await uk.fetch_releases()
    assert [r["tag"] for r in rels] == ["v0.2.1"]  # returned the cached list on 304
    assert _FakeSession.last_headers.get("If-None-Match") == 'W/"prev"'  # sent the ETag


# ── RUM-4: the release-tag apply primitives, driven on a REAL repo ───────────
#
# These exercise the actual `git` binary against throwaway repos in tmp_path — the
# git layer's one seam under real conditions — proving the RUM-4 apply advances a
# checkout to a tag / fast-forwards a branch without ever resetting --hard.


def _git(cwd, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _rev(cwd, ref: str = "HEAD") -> str:
    out = subprocess.run(
        ["git", "rev-parse", ref], cwd=str(cwd), check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def _init_repo(path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Tester")
    _git(path, "config", "commit.gpgsign", "false")
    _git(path, "config", "tag.gpgsign", "false")


def test_release_apply_leaves_head_exactly_at_the_target_tag(tmp_path) -> None:
    """RUM-4 done_when: driven on a checkout ONE RELEASE BEHIND, the release apply
    (git fetch --tags + git checkout <tag>) leaves HEAD EXACTLY at the target tag —
    not at the branch tip, and with no reset."""
    origin = tmp_path / "origin"
    _init_repo(origin)
    (origin / "f.txt").write_text("one\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "release 0.0.1")
    _git(origin, "tag", "v0.0.1")

    # Clone and park the checkout on v0.0.1 — one release behind what's coming.
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    _git(clone, "checkout", "-q", "v0.0.1")
    behind_commit = _rev(clone)

    # Origin publishes a newer release the clone has never seen.
    (origin / "f.txt").write_text("two\n")
    _git(origin, "commit", "-aqm", "release 0.0.2")
    _git(origin, "tag", "v0.0.2")
    target_commit = _rev(origin, "v0.0.2")
    assert target_commit != behind_commit
    assert _rev(clone) == behind_commit  # still behind before the apply

    # The RUM-4 release apply: fetch tags, then check the resolved tag out.
    assert uk.git_fetch_tags(str(clone)).returncode == 0
    assert uk.git_checkout(str(clone), "v0.0.2").returncode == 0

    # HEAD is EXACTLY the target tag's commit — the whole point of "ride tags".
    assert _rev(clone) == target_commit
    assert _rev(clone, "v0.0.2") == target_commit


def test_fast_forward_advances_a_branch_without_reset(tmp_path) -> None:
    """nightly: git merge --ff-only advances the tracked branch to origin,
    preserving history (no reset)."""
    origin = tmp_path / "origin"
    _init_repo(origin)
    (origin / "f.txt").write_text("a\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "A")

    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    start = _rev(clone)

    (origin / "f.txt").write_text("b\n")
    _git(origin, "commit", "-aqm", "B")
    ahead = _rev(origin, "main")

    assert uk.git_fetch(str(clone), "main").returncode == 0
    assert not uk.git_is_up_to_date(str(clone), "main")  # a real advance is pending
    assert uk.git_fast_forward(str(clone), "main").returncode == 0
    assert _rev(clone) == ahead  # advanced to origin's tip
    assert _rev(clone) != start
    # Still on the branch (fast-forward, not a detached checkout).
    head = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(clone),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == "main"


def test_fast_forward_refuses_a_diverged_branch_and_leaves_it_untouched(tmp_path) -> None:
    """A diverged local branch cannot fast-forward: ff-only fails non-zero and
    leaves HEAD untouched — the safe answer, and why RUM-4 has no reset fallback."""
    origin = tmp_path / "origin"
    _init_repo(origin)
    (origin / "f.txt").write_text("a\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "A")

    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(clone.parent / "origin"), str(clone))
    _git(clone, "config", "user.email", "t@example.com")
    _git(clone, "config", "user.name", "Tester")
    _git(clone, "config", "commit.gpgsign", "false")

    # Origin and the clone diverge: each adds a different commit on main.
    (origin / "f.txt").write_text("origin-b\n")
    _git(origin, "commit", "-aqm", "origin B")
    (clone / "g.txt").write_text("local\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "local C")
    local_head = _rev(clone)

    assert uk.git_fetch(str(clone), "main").returncode == 0
    ff = uk.git_fast_forward(str(clone), "main")
    assert ff.returncode != 0  # cannot fast-forward a diverged branch
    assert _rev(clone) == local_head  # tree untouched — the local commit survives
