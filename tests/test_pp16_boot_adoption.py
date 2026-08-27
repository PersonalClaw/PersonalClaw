"""`PP-16`'s "one adoption/reaping path", asserted at the CALL SITES.

The atom's `done_when` names six unifications; this file owns one of them. Before this,
restart-adoption was implemented twice and — the part that actually cost a user something —
*invoked* two different ways:

* the run side swept from inside `WorkflowWatchdog._poll_once`, on the first poll, with
  `_swept` flipped only after the sweep returned;
* the loop side swept from a `gateway.py` startup hook wrapped in
  `except Exception: logger.warning(...)`, awaited inline in the startup sequence, and then the
  watchdog was constructed and started regardless.

The hook shape guaranteed two defects that the poll shape cannot have, and neither is visible
from a test of the sweep's own body — which is why the rails here are about the caller:

1. **A failed sweep was lost for the life of the process.** The `except` swallowed it, the
   watchdog started anyway, and every loop the sweep should have re-armed sat persisted RUNNING
   with no worker — which a user reads as "still working" while nothing is.
2. **Startup blocked on revival.** Reviving a PLANNING loop re-runs a planner pass (a model
   call). Awaited inline, N stranded loops delayed everything after it in gateway startup.

`tests/test_loop_manager.py::TestBootSweep` covers what the loop sweep DOES; this file covers
that it is reached, reached once, reached before the poll it precedes, retried when it fails,
and reached through the one shared primitive rather than a second private one.

Every guard here carries a vacuity floor: a source scan asserts its target was found and its
anchor present before concluding anything from an absence, and each once-only / retry assertion
is paired with the opposite arrangement so a recorder that never fires cannot pass it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw import concurrency
from personalclaw.loop import manager as loop_manager
from personalclaw.loop import watchdog as loop_watchdog
from personalclaw.workflows import watchdog as run_watchdog

_SRC = Path(__file__).resolve().parent.parent / "src" / "personalclaw"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_homes(tmp_path, monkeypatch):
    """Both stores redirected. `personalclaw.loop.store` and `personalclaw.workflows.store`
    each bind `config_dir` at import, so patching the loader alone would miss them and the
    sweeps would read the real home."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    from personalclaw.loop import store as loop_store
    from personalclaw.workflows import store as run_store

    assert loop_store.config_dir() == home, "loop store still points at another home"
    assert run_store.config_dir() == home, "run store still points at another home"
    return home


def _text(rel: str) -> str:
    path = _SRC / rel
    assert path.is_file(), f"{path} does not exist — the scan below would measure nothing"
    body = path.read_text(encoding="utf-8")
    assert body.strip(), f"{path} is empty — an absence assertion would pass by being blind"
    return body


# ── one primitive ─────────────────────────────────────────────────────────────────────────


def test_there_is_exactly_one_boot_adoption_primitive():
    """`concurrency.boot_sweep` is it. The `reap_orphans` it replaced is gone rather than kept
    beside it — a second generic reaper is how the two nouns drifted in the first place."""
    assert callable(concurrency.boot_sweep), "the shared boot-adoption primitive is missing"
    assert not hasattr(concurrency, "reap_orphans"), (
        "personalclaw.concurrency exposes BOTH boot_sweep and reap_orphans. PP-16's clean break "
        "retires the second one; a caller that finds two primitives will pick one at random."
    )


def test_both_watchdogs_sweep_through_the_shared_primitive():
    """The convergence itself: neither noun may keep a private boot-adoption loop."""
    for rel in ("loop/watchdog.py", "workflows/watchdog.py"):
        body = _text(rel)
        assert "concurrency.boot_sweep(" in body, (
            f"{rel} no longer calls concurrency.boot_sweep. PP-16's 'one adoption/reaping path' "
            f"clause means both work-unit nouns partition, isolate and count their crash "
            f"survivors through ONE primitive; a private loop here re-forks the path."
        )


def test_the_scan_rejects_a_symbol_that_does_not_exist():
    """Vacuity floor for the source scans: a probe that reported everything present — or
    everything absent — would pass the assertions above and below without measuring."""
    body = _text("loop/watchdog.py")
    assert "concurrency.boot_sweep(" in body, "positive control failed — the probe sees nothing"
    assert (
        "concurrency.boot_sweep_that_never_existed(" not in body
    ), "negative control failed — the probe matches a symbol that does not exist"


# ── the deleted second call site ───────────────────────────────────────────────────────────


def test_the_gateway_has_no_loop_boot_adoption_hook():
    """The second *invocation* is gone, not just the second implementation.

    Anchored on the loop-watchdog construction so this cannot pass by the block having moved
    or been renamed out from under it.
    """
    body = _text("gateway.py")
    assert "LoopWatchdog(self.dashboard_state, self.autonudge_svc)" in body, (
        "the gateway no longer constructs the LoopWatchdog where this rail expects it — "
        "re-anchor this scan rather than deleting it"
    )
    assert "reap_orphaned_loops" not in body, (
        "gateway.py awaits a loop boot-adoption hook again. PP-16 moved that sweep into "
        "LoopWatchdog's first poll: a hook here cannot be retried when it raises (its except "
        "swallows the failure and the watchdog starts anyway, leaving loops RUNNING with no "
        "worker) and it blocks startup on N planner passes."
    )


def test_the_retired_entry_point_is_gone_from_the_manager():
    """A runtime check, not a text one: `mypy` cannot catch a stranded first-party import
    (`ignore_missing_imports`), so the deletion is asserted against the imported module."""
    assert not hasattr(loop_manager, "reap_orphaned_loops"), (
        "loop.manager.reap_orphaned_loops is back. Boot adoption belongs to the watchdog that "
        "owns the noun; a second entry point on the manager is the shape PP-16 retired."
    )
    assert hasattr(loop_watchdog.LoopWatchdog, "_boot_sweep"), (
        "LoopWatchdog._boot_sweep is missing — the sweep did not land in its new home, so the "
        "assertion above is passing because boot adoption is gone entirely"
    )


def test_the_loop_boot_sweep_loads_the_kind_registry_itself():
    """`_rearm_running` asks the kind for its `launch_blocker`, and an UNLOADED registry answers
    `None` — which re-arms a brownfield loop against a workspace that is gone instead of parking
    it with a question. So the sweep must load the registry itself and not inherit it from
    `_poll_once`.

    Pinned structurally because the behavioural test for this
    (`test_loop_manager.py::TestBootSweep::test_brownfield_orphan_with_missing_workspace_pauses_not_rearms`)
    is **order-dependent**: under `-n0`, some earlier test in the same process has already loaded
    the registry, so it goes green while the defect is present. It only reds under xdist — which
    is how the defect was in fact found while landing this slice. A guard whose verdict depends on
    which worker picked up the test is not a guard, so the property gets its own rail.
    """
    body = _text("loop/watchdog.py")
    marker = "    async def _boot_sweep(self)"
    assert marker in body, "LoopWatchdog._boot_sweep moved or was renamed — re-anchor this scan"
    sweep = body.split(marker, 1)[1].split("    async def _rearm_running(", 1)[0]
    assert sweep.strip(), "the sliced _boot_sweep body is empty — the scan would measure nothing"
    assert "concurrency.boot_sweep(" in sweep, (
        "positive control failed: the slice does not contain the sweep's own call, so the slice "
        "boundaries are wrong and an absence below would be meaningless"
    )
    assert "def _rekick_planning" not in sweep, (
        "negative control failed: the slice ran past _boot_sweep into a sibling method, so it is "
        "scanning more than the method under test"
    )
    assert "kinds.ensure_loaded()" in sweep, (
        "LoopWatchdog._boot_sweep no longer loads the kind registry. Without it "
        "`kinds.get_or_none(loop.kind)` returns None, no `launch_blocker` is consulted, and a "
        "brownfield loop whose workspace vanished during downtime is silently re-armed against "
        "the gone path instead of being parked for the user."
    )


# ── the call site: once, on the first poll ─────────────────────────────────────────────────


async def test_the_loop_watchdog_sweeps_on_its_first_poll_and_only_then():
    """The sweep is wired into `_poll_once`, not merely defined. Three polls ⇒ one sweep."""
    calls: list[int] = []

    async def _record(self) -> set[str]:
        calls.append(1)
        return set()

    wd = loop_watchdog.LoopWatchdog(_State(), _Svc())
    wd._boot_sweep = _record.__get__(wd)  # type: ignore[method-assign]
    for _ in range(3):
        await wd._poll_once()
    assert len(calls) == 1, f"expected exactly one boot sweep across three polls, got {len(calls)}"


async def test_a_loop_boot_sweep_that_raises_is_retried_on_the_next_poll():
    """The defect the gateway hook guaranteed, asserted as a property of the new caller.

    The hook's `except Exception: logger.warning(...)` made a failed sweep permanent. Here
    `_swept` is flipped only AFTER the sweep returns, so the poll loop comes back to it — and
    `_loop` already swallows a raising `_poll_once` per poll, so a transient failure costs one
    poll interval instead of the whole process.
    """
    attempts: list[int] = []

    async def _boom(self) -> set[str]:
        attempts.append(1)
        raise RuntimeError("store unreadable at boot")

    wd = loop_watchdog.LoopWatchdog(_State(), _Svc())
    wd._boot_sweep = _boom.__get__(wd)  # type: ignore[method-assign]
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await wd._poll_once()
    assert len(attempts) == 3, (
        f"a failed boot sweep was not retried: {len(attempts)} attempt(s) across three polls. "
        f"`_swept` must be set only after `_boot_sweep` returns."
    )
    assert wd._swept is False, "_swept was flipped despite the sweep never succeeding"


async def test_a_run_boot_sweep_that_raises_is_retried_on_the_next_poll():
    """The same property on the run side — the shape the loop side was converged ONTO. Asserted
    here too because "one path" is only true if both ends keep it."""
    attempts: list[int] = []

    async def _boom(self) -> set[str]:
        attempts.append(1)
        raise RuntimeError("substrate probe failed at boot")

    wd = run_watchdog.WorkflowWatchdog(None, run_watchdog.EngineServices())
    wd._boot_sweep = _boom.__get__(wd)  # type: ignore[method-assign]
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await wd._poll_once()
    assert (
        len(attempts) == 3
    ), f"a failed run boot sweep was not retried: {len(attempts)} attempt(s) across three polls"
    assert wd._swept is False, "_swept was flipped despite the sweep never succeeding"


async def test_the_retry_rails_can_fail():
    """Vacuity floor for the two retry tests: with the sweep SUCCEEDING, the same driver must
    see exactly one attempt. If the recorder were never installed — or `_poll_once` never
    consulted `_swept` — both retry tests would read 0 attempts and this would read 0 too,
    which the assertion below rejects."""
    attempts: list[int] = []

    async def _ok(self) -> set[str]:
        attempts.append(1)
        return set()

    wd = run_watchdog.WorkflowWatchdog(None, run_watchdog.EngineServices())
    wd._boot_sweep = _ok.__get__(wd)  # type: ignore[method-assign]
    for _ in range(3):
        await wd._poll_once()
    assert len(attempts) == 1, (
        f"expected exactly one successful sweep across three polls, got {len(attempts)} — "
        f"the recorder is not reaching the caller, so the retry rails prove nothing"
    )
    assert wd._swept is True, "_swept was never flipped after a successful sweep"


# ── minimal fakes: `_poll_once` needs a state + svc but not a live one ─────────────────────


class _State:
    """Enough dashboard state for a poll over an EMPTY loop store."""

    def __init__(self) -> None:
        self._sessions: dict[str, object] = {}

    def push_refresh(self, *kinds: str) -> None:
        pass

    def notify(self, *a: object, **kw: object) -> None:
        pass

    def loop_sse(self):
        from personalclaw.dashboard.sse import SseRegistry

        return SseRegistry()


class _Svc:
    def get_by_session(self, session_name: str):
        return None

    async def update(self, loop_id: str, **kw: object) -> None:
        pass

    async def remove(self, loop_id: str) -> None:
        pass
