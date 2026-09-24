"""The deferred graph-maintenance host: a dirty watermark, a due rule, and one pass.

KL-14. Three maintenance jobs already document a cadence and none has one: the memory
lint, knowledge consolidation, and the chunk backfill — which ran from a *boot hook*, so on
a gateway that stays up for a week it never ran again. Each therefore only happened when a
human opened a panel. This module is the host that gives them a cadence, and the reason it
is a watermark rather than a queue is the shape of the work: graph maintenance is
idempotent and set-based, so "something changed since I last ran" is the whole input.

**Why a watermark and not inline work.** An index-affecting write doing its graph pass
inline makes a bulk import of N items perform N passes, each superseded by the next. The
watermark collapses that to one: every write moves a stamp, and the pass runs once when the
writes stop.

**Two stamps, not one, and the second one is the anti-starvation rule.** `dirty_since` is
when the FIRST write after the last clean landed; `dirty_ts` is the LATEST. Due-ness reads
`dirty_since` — the age of the oldest unprocessed dirt — because a busy pipeline's newest
write is always recent, so a rule written against `dirty_ts` would defer forever exactly
when there is most to do. `dirty_ts` is instead the snapshot boundary (below). One stamp
cannot be both: this was the first thing I got wrong.

**The snapshot is what stops a mid-run write being swallowed.** `execute` reads `dirty_ts`
BEFORE running and clears only up to that value. A write landing while the pass runs moves
`dirty_ts` past the snapshot, so the state stays dirty and the next tick picks it up. The
alternative — clearing to "now" at the end — silently discards every write that arrived
during the pass, and nothing downstream could tell.

**It does NOT inherit `durability.auto_backup`.** The host rides the durability tick because
that loop already exists and the alternative is a second one — `gateway.py`'s own rule about
one dispatch path rather than two that drift. But the durability loop gates its jobs on
`durability.auto_backup`, and a user turning off scheduled backups must not silently also
turn off graph maintenance: they mitigate unrelated failures. So the tick calls
`run_maintenance` outside that gate, and `test_maintenance_runs_with_auto_backup_off` is the
proof rather than the intention.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: State file, beside the knowledge store rather than in durability's state: this is
#: knowledge's own bookkeeping, and a shared file would make one plan's corrupt write cost
#: the other its cadence.
_STATE_FILENAME = "graph_maintenance.json"

#: How many items one sub-batch claims. Bounded so the store lock is released between
#: sub-batches — a maintenance pass holding it for a whole library would stall a UI save,
#: which is the failure that makes background work feel like a hang.
DEFAULT_BATCH_SIZE = 50

#: Fallback when config is unreadable. 15 minutes: long enough that a normal import
#: coalesces into one pass, short enough that a pipeline which never idles still gets
#: maintenance four times an hour.
DEFAULT_MAX_STALENESS_SECS = 900.0


def _state_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / _STATE_FILENAME


def load_state() -> dict[str, Any]:
    """The watermark state, or a clean one.

    A corrupt file reads as CLEAN rather than dirty-forever: the alternative would run the
    full pass on every tick with no way for it to ever succeed, and the passes are
    idempotent so losing one trigger costs a cadence, not correctness.
    """
    try:
        raw = json.loads(_state_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except FileNotFoundError:
        return {}
    except Exception:  # noqa: BLE001 — a hand-edited file must not wedge the host
        logger.warning("graph-maintenance state unreadable; treating the index as clean")
    return {}


def _save_state(state: dict[str, Any]) -> None:
    from personalclaw.atomic_write import atomic_write

    try:
        atomic_write(_state_path(), json.dumps(state, indent=2) + "\n")
    except Exception:  # noqa: BLE001 — losing a stamp costs a cadence, never a write
        logger.warning("graph-maintenance state not written", exc_info=True)


def mark_dirty(*, now: float | None = None, reason: str = "") -> None:
    """Record that an index-affecting write landed. Cheap, and never raises into a write.

    Called from the write path, so it must cost approximately nothing and must not be able
    to fail the write it is describing — a knowledge item that did not save because its
    maintenance stamp could not be written would be a strictly worse product.
    """
    stamp = now if now is not None else time.time()
    state = load_state()
    if not float(state.get("dirty_since") or 0.0):
        # FIRST write since the last clean — this is the age the due rule reads.
        state["dirty_since"] = stamp
    state["dirty_ts"] = stamp
    if reason:
        state["last_reason"] = reason[:120]
    _save_state(state)


def is_dirty(state: dict[str, Any] | None = None) -> bool:
    st = state if state is not None else load_state()
    return float(st.get("dirty_ts") or 0.0) > float(st.get("clean_ts") or 0.0)


def max_staleness_secs() -> float:
    """The configured anti-starvation window, clamped away from zero.

    Zero would make every tick "stale" and defeat the coalescing the watermark exists for,
    so the floor is enforced HERE as well as in the config bounds — the file is
    hand-editable and `_EDITABLE_CONFIG`'s range only guards the PATCH path.
    """
    try:
        from personalclaw.config.loader import AppConfig

        raw = float(getattr(AppConfig.load().knowledge, "maintenance_max_staleness_secs", 0) or 0)
    except Exception:  # noqa: BLE001
        logger.debug("graph-maintenance staleness unreadable; using the default", exc_info=True)
        return DEFAULT_MAX_STALENESS_SECS
    return max(1.0, raw) if raw > 0 else DEFAULT_MAX_STALENESS_SECS


def is_due(
    *,
    now: float | None = None,
    in_flight: int = 0,
    state: dict[str, Any] | None = None,
    staleness: float | None = None,
) -> tuple[bool, str]:
    """Whether the maintenance pass should run, and the named reason either way.

    `dirty AND (in-flight ingest is zero OR the dirt is older than max-staleness)`. The two
    disjuncts are the two failure modes: without the first, a bulk import runs a pass per
    item; without the second, a pipeline that never drains starves maintenance entirely.
    """
    st = state if state is not None else load_state()
    stamp = now if now is not None else time.time()
    if not is_dirty(st):
        return False, "clean"
    if in_flight <= 0:
        return True, "queue drained"
    window = staleness if staleness is not None else max_staleness_secs()
    age = stamp - float(st.get("dirty_since") or stamp)
    if age >= window:
        return True, f"dirt is {int(age)}s old (>= {int(window)}s) while {in_flight} in flight"
    return False, f"{in_flight} in flight and dirt is only {int(age)}s old"


def clear_up_to(snapshot: float) -> None:
    """Mark the index clean up to `snapshot` — never past it.

    A write that landed mid-run left `dirty_ts > snapshot`, so `is_dirty` stays True and the
    next tick runs again. `dirty_since` is reset only when the state is now fully clean,
    because that stamp is the AGE of outstanding dirt and carrying a stale one would make
    the staleness rule fire immediately on the next write.
    """
    state = load_state()
    state["clean_ts"] = max(float(state.get("clean_ts") or 0.0), float(snapshot))
    state["last_run_ts"] = time.time()
    if not is_dirty(state):
        state["dirty_since"] = 0.0
    else:
        # Still dirty: the oldest outstanding write is the one that landed during the run.
        state["dirty_since"] = max(float(snapshot), float(state.get("dirty_since") or 0.0))
    _save_state(state)


@dataclass
class MaintenancePass:
    """One registered maintenance job.

    `run(batch_size) -> int` returns how many units it processed; returning 0 means "nothing
    left", which is how the host knows to stop claiming sub-batches rather than looping on a
    job that has finished.
    """

    name: str
    run: Callable[..., int]
    #: Whether a failure here should stop the remaining passes. Default False: the jobs are
    #: independent, and one provider outage must not cost the others their cadence.
    fatal: bool = False
    #: Whether `run` is RESUMABLE — i.e. it claims one bounded batch and returning >0 means
    #: "there is more". Only such a pass is re-invoked until it returns 0.
    #:
    #: 🔴 This flag exists because the alternative silently mis-runs the other kind. The
    #: memory lint is a whole-store SWEEP that returns a finding count, so a store with three
    #: standing findings would return 3 forever and the host would re-lint it `max_batches`
    #: times per tick — busy-looping on a number that means "issues", not "remaining work".
    #: A single-sweep pass therefore runs exactly ONCE per tick and its return value is
    #: reported, never interpreted as backlog.
    batched: bool = True


@dataclass
class MaintenanceResult:
    """What one host run did — returned rather than logged only, so a caller (and a test)
    can assert on the counts instead of scraping log lines."""

    ran: bool = False
    reason: str = ""
    snapshot: float = 0.0
    per_pass: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self._as_int(v) for v in self.per_pass.values())

    @staticmethod
    def _as_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0


#: The registry. A dict keyed by name so a re-register REPLACES rather than appending — a
#: module imported twice must not get two passes, which is how "one edge pass" quietly
#: becomes two.
_PASSES: dict[str, MaintenancePass] = {}


def register_pass(
    name: str,
    run: Callable[..., int],
    *,
    fatal: bool = False,
    replace: bool = True,
    batched: bool = True,
) -> None:
    """Register a maintenance job under `name`. See `MaintenancePass.batched`."""
    if name in _PASSES and not replace:
        return
    _PASSES[name] = MaintenancePass(name=name, run=run, fatal=fatal, batched=batched)


def registered_passes() -> list[str]:
    return sorted(_PASSES)


def clear_passes() -> None:
    """Test seam: drop every registered pass so a suite starts from a known registry."""
    _PASSES.clear()


def _claim_batches(p: MaintenancePass, *, batch_size: int, max_batches: int) -> int:
    """Run one pass in bounded sub-batches until it reports nothing left.

    The lock is released between sub-batches by construction: each `run` call opens and
    closes its own store work, so a UI save waits at most one sub-batch rather than a whole
    library. `max_batches` bounds a pass whose `run` keeps claiming forever — a buggy job
    must cost one tick, not the loop.
    """
    if not p.batched:
        # A whole-store sweep: exactly one call, and its return value is a REPORT (findings,
        # items examined) rather than a backlog to drain.
        return int(p.run(batch_size=batch_size) or 0)
    done = 0
    for _ in range(max(1, max_batches)):
        processed = int(p.run(batch_size=batch_size) or 0)
        done += processed
        if processed <= 0:
            break
    return done


def execute(
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = 20,
    now: float | None = None,
) -> MaintenanceResult:
    """Run every registered pass once, then clear the watermark up to the SNAPSHOT.

    The snapshot is read first and deliberately not re-read: see the module docstring.
    """
    state = load_state()
    snapshot = float(state.get("dirty_ts") or (now if now is not None else time.time()))
    result = MaintenanceResult(ran=True, reason="running", snapshot=snapshot)
    for name in sorted(_PASSES):
        p = _PASSES[name]
        try:
            result.per_pass[name] = _claim_batches(
                p, batch_size=batch_size, max_batches=max_batches
            )
        except Exception as exc:  # noqa: BLE001 — one job's outage is not the others' problem
            logger.warning("graph maintenance pass %r failed: %s", name, exc, exc_info=True)
            result.errors[name] = f"{type(exc).__name__}: {exc}"
            if p.fatal:
                break
    # Cleared even when a pass errored: the stamp records WHAT WAS SEEN, not what succeeded.
    # Holding the watermark open on a persistent failure would re-run the whole library every
    # tick forever, and the passes are idempotent so the next write re-triggers them anyway.
    clear_up_to(snapshot)
    return result


def run_maintenance(
    *,
    now: float | None = None,
    in_flight: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    force: bool = False,
) -> MaintenanceResult:
    """The tick entry point: check due-ness, then run. Never raises.

    `in_flight` defaults to the live ingest queue depth. Passed explicitly by tests and by a
    caller that already knows, so the host never has to reach for a global to answer a
    question it was given.
    """
    try:
        depth = in_flight if in_flight is not None else _in_flight_depth()
        due, reason = is_due(now=now, in_flight=depth, state=load_state())
        if not (due or force):
            return MaintenanceResult(ran=False, reason=reason)
        return execute(batch_size=batch_size, now=now)
    except Exception as exc:  # noqa: BLE001 — a maintenance tick must never break the loop
        logger.warning("graph maintenance tick failed", exc_info=True)
        return MaintenanceResult(ran=False, reason=f"error: {type(exc).__name__}: {exc}")


#: How the host learns the live ingest depth. INJECTED rather than imported: the queue lives
#: on `DashboardState` (a lazy accessor that STARTS a queue if none exists), so reaching for
#: it from `knowledge/` would both invert the import direction and spin up a worker merely to
#: ask how busy it is. The gateway sets this once at startup.
#:
#: 🔴 The first version of this called a `knowledge.get_ingest_queue` that DOES NOT EXIST, so
#: every lookup fell into the except and returned 0 — the in-flight gate would have read
#: "drained" forever and clause 2's coalescing would never have engaged. An absent probe is
#: therefore NOISY (one warning, once) instead of silently permissive.
_IN_FLIGHT_PROBE: Callable[[], int] | None = None
_PROBE_WARNED = False


def set_in_flight_probe(probe: Callable[[], int] | None) -> None:
    """Install (or clear) the live ingest-depth probe. Called by the gateway at startup."""
    global _IN_FLIGHT_PROBE, _PROBE_WARNED
    _IN_FLIGHT_PROBE = probe
    _PROBE_WARNED = False


def has_in_flight_probe() -> bool:
    """Whether a probe is installed — the assertion a wiring test makes."""
    return _IN_FLIGHT_PROBE is not None


def _in_flight_depth() -> int:
    """Live ingest-queue depth, or 0 when nothing can answer.

    0 rather than a large number: the queue check exists to avoid thrashing during an import,
    not to be a second gate that can wedge the host closed. But an UNINSTALLED probe is a
    wiring bug, not a drained queue, so it says so once — the difference between the two is
    invisible in the return value and that is exactly how this went wrong the first time.
    """
    global _PROBE_WARNED
    if _IN_FLIGHT_PROBE is None:
        if not _PROBE_WARNED:
            _PROBE_WARNED = True
            logger.warning(
                "graph maintenance has no in-flight probe installed — treating the ingest "
                "queue as drained, so a bulk import will not coalesce"
            )
        return 0
    try:
        return max(0, int(_IN_FLIGHT_PROBE() or 0))
    except Exception:  # noqa: BLE001
        logger.debug("ingest-queue depth probe failed; treating it as drained", exc_info=True)
        return 0
