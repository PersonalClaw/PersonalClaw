"""Per-observer coalesced delivery for run events (WF2-R11, batch-5).

Sits between the controller's publish seam and the SSE write. The problem it solves is a
throughput one, not a correctness one: a 20-node parallel fan-out completing in a single
tick publishes twenty `workflow_node_done` events, and a naive path writes twenty frames
per connection — twenty JSON encodes, twenty socket writes and twenty React renders for one
logical moment. That is the §9 large-spec widget risk.

**The shape.** Events are classed by whether a consumer needs each one individually:

* **Coalescing-eligible** — the high-frequency per-node lifecycle chatter. These are
  accumulated per node instance (last-write-wins on the SAME instance, since a node's later
  state supersedes its earlier one) and flushed as ONE `workflow_batch` frame carrying the
  ordered list.
* **Pass-through** — everything a human acts on or that reorders the run: attention,
  needs_input, gate_resolved, run status, mutation, fork. Delaying an ask by 25ms to save a
  frame is a bad trade, and a status flip that arrives after the batch it should precede
  would make the fold's own ordering guards fire on legitimate events.

**Why per-observer and not per-run.** The dirty set is keyed by *registry key*, so two
browser tabs on the same run each get their own debounce window. A shared window would let
one tab's just-flushed timer swallow the other's first update, and the second tab would sit
a full window behind for no reason.

**Ordering is preserved within the window.** A batch carries its members in publish order
and every member keeps its own envelope (`event_id`, `seq`, `epoch`), so the FE fold applies
them exactly as if they had arrived as separate frames — the fold law is unaffected by
whether the transport batched. That is the invariant this module must not break: coalescing
is a TRANSPORT optimization, and a consumer that unwraps a batch sees the same event
sequence it would have seen unbatched.

A flush is also forced when the accumulated batch reaches :data:`MAX_BATCH`, so a
thousand-node fan-out cannot grow an unbounded frame while the timer waits.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: Debounce window. Long enough that one tick's fan-out lands in a single frame, short
#: enough to read as instant — a widget that lags a visible beat behind the log reads as
#: broken even when it is merely batched.
WINDOW_SECS = 0.025

#: A batch flushes at this many members regardless of the timer. Without it a 500-node
#: fan-out inside one window would build one enormous frame, trading twenty small writes for
#: a single write the client stalls parsing.
MAX_BATCH = 50

#: The coalesced frame's event name. A distinct name (rather than re-using a lifecycle name)
#: so a consumer that does NOT understand batching cannot silently mis-parse one member as
#: the whole batch — it drops the frame instead, which is visible.
BATCH_EVENT = "workflow_batch"

#: Events safe to accumulate: per-node lifecycle chatter, high-frequency by nature. Every
#: one is a node-keyed patch in the FE fold, so collapsing repeats of the SAME instance
#: loses nothing a later member does not already carry.
COALESCING_EVENTS = frozenset(
    {
        "workflow_node_started",
        "workflow_node_done",
        "workflow_progress",
    }
)


def is_coalescing(event: str) -> bool:
    """True when ``event`` may be batched.

    Deliberately an allowlist: a NEW event defaults to pass-through. The failure mode of
    wrongly passing through is one extra frame; the failure mode of wrongly coalescing is a
    gate ask arriving late or out of order, so the safe default is the boring one.
    """
    return event in COALESCING_EVENTS


class _Window:
    """One observer's in-flight batch and its timer."""

    __slots__ = ("events", "handle", "seen")

    def __init__(self) -> None:
        #: Ordered members, publish order preserved.
        self.events: list[dict[str, Any]] = []
        self.handle: asyncio.TimerHandle | None = None
        #: `(event, instance_path)` -> index in `events`, for last-write-wins on a repeat of
        #: the SAME event for the SAME instance.
        self.seen: dict[tuple[str, str], int] = {}


class EventCoalescer:
    """Debounced per-observer batching in front of a raw publish function.

    ``sink(key, event, payload)`` is the underlying write (in production
    ``SseRegistry.publish``). Construct one per registry; keys scope the windows.
    """

    def __init__(
        self,
        sink: Callable[[str, str, Any], None],
        *,
        window: float = WINDOW_SECS,
        max_batch: int = MAX_BATCH,
    ) -> None:
        self._sink = sink
        self._window = window
        self._max = max_batch
        self._windows: dict[str, _Window] = {}

    # ── publish ──

    def publish(self, key: str, event: str, payload: dict[str, Any]) -> None:
        """Route one event: pass it straight through, or accumulate it for a flush.

        A pass-through event FIRST flushes any pending batch. Otherwise a status flip could
        overtake the node events that logically precede it, and a consumer would see a run
        go `complete` while its last node still reads `running` until the batch landed.
        """
        if not is_coalescing(event):
            self.flush(key)
            self._sink(key, event, payload)
            return

        win = self._windows.get(key)
        if win is None:
            win = _Window()
            self._windows[key] = win

        member = {"event": event, "payload": payload}
        path = payload.get("instance_path") if isinstance(payload, dict) else None
        # Keyed by (event, path), NOT path alone: a `started` must not be eaten by the `done`
        # that follows it — the FE derives a node's running state from seeing both, and a
        # node that only ever reported `done` would never render as active.
        key_ = (event, path) if isinstance(path, str) else None
        prior = win.seen.get(key_) if key_ is not None else None
        if prior is not None:
            # A repeat of the same event for the same instance inside one window: the later
            # one supersedes, since a node's newer state is strictly more current.
            win.events[prior] = member
        else:
            if key_ is not None:
                win.seen[key_] = len(win.events)
            win.events.append(member)

        if len(win.events) >= self._max:
            self.flush(key)
            return
        self._arm(key, win)

    def _arm(self, key: str, win: _Window) -> None:
        """Start the window timer if it is not already running.

        NOT re-armed on each event: a steady stream would then never flush (the classic
        debounce-starvation bug). The first event of a window sets the deadline, so a burst
        is bounded by ONE window regardless of length.
        """
        if win.handle is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No loop (a synchronous test, or a publish from a worker thread): deliver
            # immediately rather than accumulating events nothing will ever flush.
            self.flush(key)
            return
        win.handle = loop.call_later(self._window, self._on_timer, key)

    def _on_timer(self, key: str) -> None:
        win = self._windows.get(key)
        if win is not None:
            win.handle = None
        self.flush(key)

    # ── flush ──

    def flush(self, key: str) -> None:
        """Write one observer's pending batch immediately (no-op when empty).

        A single accumulated event is written as ITSELF, not wrapped: wrapping would force
        every consumer to unwrap the common case, and the whole point of the batch frame is
        the case where there are many.
        """
        win = self._windows.pop(key, None)
        if win is None:
            return
        if win.handle is not None:
            win.handle.cancel()
            win.handle = None
        if not win.events:
            return
        try:
            if len(win.events) == 1:
                one = win.events[0]
                self._sink(key, one["event"], one["payload"])
            else:
                self._sink(key, BATCH_EVENT, {"events": win.events})
        except Exception:  # pragma: no cover — a broken sink must not kill a run
            logger.debug("coalesced flush failed for %s", key, exc_info=True)

    def flush_all(self) -> None:
        """Flush every observer. Called at run termination and gateway shutdown, so a run's
        last events are never stranded in a window whose timer will never fire."""
        for key in list(self._windows):
            self.flush(key)

    @property
    def pending(self) -> int:
        """Total accumulated events across all observers — for tests and a debug view."""
        return sum(len(w.events) for w in self._windows.values())
