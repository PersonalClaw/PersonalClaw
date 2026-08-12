"""Dispatch: inbox + wakeup, and the event-bus delivery contract
(AUTOMATION-SUBSTRATE §3.2/§3.3 — S64).

The scheduler never executes directly. A fired trigger enqueues a typed
payload plus a wakeup signal;
a dispatcher claims and drives it. Crash-safety falls out of that shape — the payload survives an
executor crash because it is in the queue, not in a coroutine.

**The bug this session fixes, reproduced before it was written.** `event_triggers._schedule_fire`
records the fire and then does `asyncio.get_running_loop()`; with no loop
(a sync CLI memory write) it
`return`s. Measured on a real store: `fire_count` becomes 1 and **the action is dropped with nothing
anywhere recording that it did not run**. That is the silent drop §1.3
bans, in shipped code. The spool
here is the fix: a sync-context fire is written to disk and drained on the next tick.

The delivery rules, each with the failure it prevents:

* **Peek-then-deliver-then-ack**, never atomic read-and-mark. An event acked before its handler
  finished is an event lost to a crash mid-handling.
* **The cursor advances only on CONSUMED events.** A transient failure (provider down, key absent)
  HOLDS the drain with a bounded retry; a permanent one (payload
  malformed) advances and logs. Without
  the distinction you get either event loss or a poison pill that stalls the queue forever.
* **Payload-hash dedup window.** A webhook retried by its sender and an fs event fired twice for one
  save are the same work; a hash window collapses them.
* **Monotonic cursor per (trigger, stream).** A repeatedly-firing trigger must never reprocess
  history — the failure mode where enabling one trigger replays a month of events.

Pure records and decisions plus one small on-disk spool. The loop that drains it is the service.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

#: How long two identical payloads are treated as one fire. Five minutes covers a webhook sender's
#: retry ladder and an editor's save-twice, which are the two real sources of duplicate fires.
DEDUP_WINDOW_SECS = 300.0

#: Bounded retries for a transient failure before the drain gives up on that event and moves on.
#: Five, then a loud ledger row: holding the drain forever on one unreachable provider would stop
#: every other automation on the machine, which is a worse failure than dropping one event loudly.
MAX_TRANSIENT_RETRIES = 5

#: Per-event-family coalescing window. Two events of one family inside this window are one wake.
COALESCE_WINDOW_SECS = 0.25


class Handling(str, Enum):
    """What a handler reports back. The vocabulary that makes "never drop" checkable.

    An unexpected THROW is `TRANSIENT`, not permanent — a handler that
    raised on a network blip must be
    retried, and treating an unclassified exception as permanent is how a
    recoverable failure becomes
    data loss.
    """

    DELIVERED = "delivered"
    TRANSIENT = "transient"
    PERMANENT = "permanent"


class DeliveryStatus(str, Enum):
    """Per-target delivery state on a dispatch record."""

    PENDING = "pending"
    DELIVERED = "delivered"
    GIVEN_UP = "given_up"


class WakeKind(str, Enum):
    """Two wakeup kinds with DIFFERENT drop semantics — the distinction §3.2 turns on.

    `WAKE` is droppable: if the session is already running, the inbox will be drained by the run in
    flight, so skipping is the natural implementation of `overlap: skip` (and exactly what autonudge
    already does for a mid-turn nudge).

    `RESUME` is NOT droppable. It carries a gate answer for a parked run,
    and an overlap guard that ate
    it would strand the run forever waiting for an answer the user
    already gave. It re-queues until the
    parked lock releases.
    """

    WAKE = "wake"
    RESUME = "resume"


def droppable(kind: str) -> bool:
    """Whether a wakeup of this kind may be skipped when the session is busy.

    Written as a function rather than inlined at the call site because
    getting it wrong in one place is
    enough: a `resume` treated as droppable silently eats approvals, and the symptom (a run parked
    forever) points nowhere near the guard that dropped it.
    """
    return kind != WakeKind.RESUME.value


@dataclass
class Envelope:
    """One event on the bus.

    `event_id` is DETERMINISTIC — derived from source, kind and payload
    hash — so a re-delivery is an
    idempotent no-op rather than a second row. A random id would make at-least-once delivery
    indistinguishable from duplicate work.
    """

    seq: int
    source: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    emitted_at: float = 0.0
    #: Set when this event was produced BY a trigger's own run — the cycle guard reads it so a run's
    #: lifecycle events cannot re-match the trigger that started it (decision 5's spawned_by skip).
    spawned_by: str = ""

    @property
    def payload_hash(self) -> str:
        return payload_hash(self.source, self.kind, self.payload)

    @property
    def event_id(self) -> str:
        return f"evt-{self.payload_hash[:16]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "source": self.source,
            "kind": self.kind,
            "payload": dict(self.payload),
            "emitted_at": self.emitted_at,
            "spawned_by": self.spawned_by,
            "event_id": self.event_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Envelope:
        d = d or {}
        return cls(
            seq=int(d.get("seq", 0) or 0),
            source=str(d.get("source", "") or ""),
            kind=str(d.get("kind", "") or ""),
            payload=dict(d["payload"]) if isinstance(d.get("payload"), dict) else {},
            emitted_at=float(d.get("emitted_at", 0.0) or 0.0),
            spawned_by=str(d.get("spawned_by", "") or ""),
        )


def payload_hash(source: str, kind: str, payload: dict[str, Any]) -> str:
    """A stable SHA-256 over the event's identity.

    Sorted keys, because `json.dumps` preserves insertion order by
    default and two dicts with the same
    content in a different order would hash differently — which would
    defeat the dedup window exactly
    when it matters (a sender retrying with a re-serialized body).
    """
    basis = json.dumps(
        {"source": source, "kind": kind, "payload": payload or {}},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def is_duplicate(
    envelope: Envelope, seen: dict[str, float], now: float, window: float = DEDUP_WINDOW_SECS
) -> bool:
    """Whether this event is a duplicate within the window.

    Reads and does not mutate: the caller records the hash only after it decides to process, so a
    crash between the check and the work leaves the event still deliverable. Marking here would make
    the dedup window itself a source of dropped events.
    """
    last = seen.get(envelope.payload_hash)
    return last is not None and (now - last) < window


@dataclass
class Cursor:
    """A consumer's position in one stream, per (trigger, stream).

    Monotonic by construction: `advance` refuses to move backwards. That is what stops a
    repeatedly-firing trigger from reprocessing history — the failure where enabling one trigger
    replays a month of events.
    """

    trigger_id: str
    stream: str
    seq: int = 0
    #: Retries spent on the event currently HELD at `seq + 1`. Reset on
    #: every advance, because a count
    #: that carried over would give the next event a shorter budget than the first.
    held_retries: int = 0

    def advance(self, to_seq: int) -> bool:
        if to_seq <= self.seq:
            return False
        self.seq = to_seq
        self.held_retries = 0
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "stream": self.stream,
            "seq": self.seq,
            "held_retries": self.held_retries,
        }


class DrainAction(str, Enum):
    """What the drain does with the event at the cursor.

    Every member has a PRODUCER and a branch in `triggers.loop._drain_spool` (WF2AUT-13), and
    that function's dispatch carries a raising tail so a member added here without a branch
    fails loudly instead of inheriting another's behaviour.

    `SKIP_CYCLE` was DELETED rather than left declared. A cycle skip needs a `trigger_id` to
    compare `Envelope.spawned_by` against, and the spool drain has none: it re-enters through
    `emit_event`, which does its own matching against every stored trigger, so there is no one
    trigger the drain could guard. Nothing in production writes `spawned_by` either (measured:
    zero writers outside this module). A member with no honest producer reads as a shipped
    control and is worse than its absence — so `cycle_guard`, which returns a plain
    `(bool, reason)`, stays the only expression of that rule.
    """

    CONSUME = "consume"
    HOLD = "hold"
    GIVE_UP = "give_up"
    SKIP_DUPLICATE = "skip_duplicate"


def drain_decision(
    *,
    handling: str,
    held_retries: int,
    max_retries: int = MAX_TRANSIENT_RETRIES,
) -> tuple[str, str]:
    """What to do after a handler reports back. Returns `(action, reason)`.

    The cursor rule, in one place:

    * `delivered` → CONSUME and advance.
    * `permanent` → CONSUME and advance, with a loud reason. The payload
    is bad; holding forever would
      be a poison pill that stalls every later event in the stream.
    * `transient` → HOLD, until the retry budget is spent. A held drain is the correct answer to
      "the provider is down": the event is not lost, and the next tick tries again.
    * `transient` past the budget → GIVE_UP, loudly. Holding indefinitely
    on one unreachable provider
      would stop every other automation, which is worse than one loudly-dropped event.

    Exhaustive over the closed enum with a RAISING tail (WF2AUT-13). The transient rules used to
    be the *fallthrough*, which meant an unknown handling string — a typo, or a `Handling` member
    added without a rule here — silently inherited "retry five times then drop". A new member must
    declare its own cursor behaviour, because inheriting the wrong one of these three is either
    event loss or a poison pill.
    """
    if handling == Handling.DELIVERED.value:
        return DrainAction.CONSUME.value, ""
    if handling == Handling.PERMANENT.value:
        return (
            DrainAction.CONSUME.value,
            "permanent failure: the payload cannot be handled, so holding would stall the stream",
        )
    if handling == Handling.TRANSIENT.value:
        if held_retries + 1 >= max_retries:
            return (
                DrainAction.GIVE_UP.value,
                f"transient failure persisted for {max_retries} attempts; advancing loudly rather "
                "than stalling every other automation",
            )
        return (
            DrainAction.HOLD.value,
            f"transient failure, attempt {held_retries + 1} of {max_retries}; the event is not "
            "lost",
        )
    raise AssertionError(
        f"no branch for handling {handling!r} — a new Handling member must declare its own cursor "
        "rule here rather than inherit another member's"
    )


def classify_handler_outcome(exception: BaseException | None, reported: str = "") -> str:
    """Map a handler's result onto the typed vocabulary.

    An unexpected exception is TRANSIENT. That is the "never drop" rule: a handler that raised on a
    network blip must be retried, and treating an unclassified throw as
    permanent turns a recoverable
    failure into data loss. A handler that explicitly reports `permanent` is believed — it knows its
    payload is unusable in a way the dispatcher cannot see.
    """
    if reported in {h.value for h in Handling}:
        return reported
    if exception is not None:
        return Handling.TRANSIENT.value
    return Handling.DELIVERED.value


def cycle_guard(envelope: Envelope, trigger_id: str) -> tuple[bool, str]:
    """Whether this event may fire this trigger, or would be a self-loop.

    The hook-recursion storm: a run's own lifecycle events re-matching the trigger that started it.
    Guarded on `spawned_by` rather than on a depth counter alone, because
    depth catches the storm one
    level late — by then the trigger has already fired itself once, which
    for a mutating automation is
    one unwanted write.
    """
    if envelope.spawned_by and envelope.spawned_by == trigger_id:
        return False, f"{trigger_id} would fire on an event its own run emitted"
    return True, ""


def coalesce_family(
    envelopes: list[Envelope], now: float, window: float = COALESCE_WINDOW_SECS
) -> list[Envelope]:
    """Collapse same-family events inside the window to the LATEST of each family.

    The latest, not the first: for a `FileChanged` burst the newest state
    is the one worth acting on,
    and acting on the first means reading a file the user has since
    changed again. Order is preserved
    by seq so the batch is reproducible.
    """
    latest: dict[tuple[str, str], Envelope] = {}
    for env in envelopes:
        key = (env.source, env.kind)
        current = latest.get(key)
        if current is None or env.seq > current.seq:
            # Only collapse events that are actually close together; a family seen an hour apart is
            # two separate facts, not a burst.
            if current is not None and abs(env.emitted_at - current.emitted_at) > window:
                latest[key] = env
                continue
            latest[key] = env
    return sorted(latest.values(), key=lambda e: e.seq)


# ── the spool: the fix for the measured sync-context drop ──


def spool_path() -> Path:
    """Where sync-context fires are parked until a loop can drain them.

    Under `config_dir()`, resolved per call rather than at import: a
    module-level path binds to whatever
    home was set when the module first loaded, which is how a test writes into the real
    `~/.personalclaw` (this program has paid for that once already).
    """
    from personalclaw.config.loader import config_dir

    return Path(config_dir()) / "trigger-spool.jsonl"


def spool_fire(envelope: Envelope, *, path: Path | None = None) -> bool:
    """Park one fire on disk. Returns whether it was written.

    THE fix for the measured bug: `event_triggers._schedule_fire` records
    the fire, asks for a running
    loop, and `return`s when there is none — so a sync CLI memory write increments `fire_count` and
    drops the action with nothing recording that it did not run.
    Appending here means the fire survives
    to the next tick.

    Append-only JSONL, one event per line: a partial write damages one
    line, and `drain_spool` skips an
    unparseable line rather than refusing the whole file. A single JSON
    array would lose every spooled
    fire to one truncated write.
    """
    target = path or spool_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(envelope.to_dict(), separators=(",", ":")) + "\n")
        return True
    except Exception:
        # Best-effort by design: a spool failure must not break the
        # caller's write path. The event is
        # lost, but the memory write that triggered it still succeeds —
        # the opposite trade would make
        # an unwritable disk take down ordinary use.
        return False


def drain_spool(*, path: Path | None = None, limit: int = 500) -> tuple[list[Envelope], int]:
    """Read spooled fires. Returns `(envelopes, skipped_bad_lines)`.

    Reads and does NOT truncate: truncation happens after the caller has handled them, which is the
    peek-then-deliver-then-ack rule applied to the spool. Truncating here
    would lose every spooled fire
    to a crash during handling — the same bug the spool exists to fix, one layer up.

    `limit` bounds a spool that grew while the gateway was down, so a restart drains a bounded batch
    rather than blocking boot on ten thousand events.
    """
    target = path or spool_path()
    envelopes: list[Envelope] = []
    skipped = 0
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except Exception:
        return [], 0
    for line in lines[:limit]:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            envelopes.append(Envelope.from_dict(json.loads(stripped)))
        except Exception:
            # One damaged line (a partial write at power-loss) must not hide the rest.
            skipped += 1
    return envelopes, skipped


def clear_spool(*, handled: int, path: Path | None = None) -> None:
    """Drop the first `handled` lines, keeping whatever arrived during the drain.

    Rewrite-with-remainder rather than delete: a fire spooled while the
    drain was running would be lost
    by an unconditional truncate, and that window is exactly when a busy machine spools most.

    ⚠️ **PREFIX ACK ONLY, and that constrains the drain** (WF2AUT-13). This can express "ack the
    first N" and nothing else — there is no "keep line 2, ack lines 1 and 3". So `DrainAction.HOLD`
    is necessarily HEAD-OF-LINE: the drain acks the prefix it consumed, stops at the first envelope
    it must hold, and leaves that envelope plus everything after it for the next tick. Extending
    this to an arbitrary keep-set was considered and rejected — it would have to re-serialize lines
    a concurrent `spool_fire` may have appended since the read, trading a bounded retry for a lost
    fire. Head-of-line blocking is bounded by `MAX_TRANSIENT_RETRIES` ticks, and the transient this
    exists for (the event engine unreachable) fails the whole batch identically anyway.
    """
    target = path or spool_path()
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except Exception:
        return
    remaining = lines[handled:]
    try:
        from personalclaw.workflows.store import atomic_write

        atomic_write(target, "\n".join(remaining) + ("\n" if remaining else ""))
    except Exception:
        return


# ── the HELD envelope's retry budget: what makes HOLD bounded across restarts (WF2AUT-13) ──


def spool_hold_path() -> Path:
    """Where the currently HELD envelope's retry count lives, beside the spool.

    A SIDECAR rather than a field on the spooled line, because the append path forbids the
    alternative: `trigger-spool.jsonl` is append-only and `clear_spool` only ever drops a prefix, so
    stamping a count onto one line would mean re-serializing lines a concurrent `spool_fire` may
    have appended since the read — trading a bounded retry for a lost fire. This file has exactly
    one writer (the drain) and never touches the append path.

    Resolved per call like `spool_path`, for the same reason: a module-level path binds to whatever
    home was set when the module first loaded, which is how a test writes into the real
    `~/.personalclaw`.

    Deliberately NOT a `durability.inventory` StateEntry — like the spool itself and `file_poll`'s
    hash maps, this is high-churn runtime bookkeeping that is meaningless once restored, and a
    backup that carried a stale retry count would resurrect a budget for an envelope that is gone.
    """
    from personalclaw.config.loader import config_dir

    return Path(config_dir()) / "trigger-spool-hold.json"


def read_spool_hold(*, path: Path | None = None) -> tuple[str, int]:
    """The `(event_id, held_retries)` of the currently held envelope, or `("", 0)` if none.

    ONE record, not a map, because HOLD is head-of-line by construction (see `clear_spool`): the
    drain stops at the first envelope it must hold, so a second can never be held in the same pass.
    That also makes the file self-pruning — a per-event map would accumulate a dead key for every
    envelope ever held and need its own garbage collection, over a file nothing else reads.

    Keyed on `event_id` — deterministic, derived from the payload hash — and NOT on `Cursor.seq`:
    every spooled envelope is written with `seq=0` (`event_triggers._spool`), so seq cannot identify
    one. A recorded id that does not match the current head means the head changed and the budget
    starts over, which is `Cursor.advance`'s rule applied to the spool.

    Unreadable or malformed reads as "no hold". That direction is chosen: it costs extra retries on
    a genuinely transient failure, where assuming the budget is spent would drop a recoverable event
    on its first blip.
    """
    target = path or spool_hold_path()
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            return "", 0
        return str(record.get("event_id", "") or ""), int(record.get("held_retries", 0) or 0)
    except Exception:
        return "", 0


def write_spool_hold(*, event_id: str, held_retries: int, path: Path | None = None) -> bool:
    """Persist the held envelope's retry count. Returns whether it was written.

    This is what makes HOLD *bounded*. Without a durable count the budget resets on every process
    start, and a retry that survives restarts without a surviving count is an unbounded retry — a
    crash-looping gateway would re-enter the same failing envelope forever. That is strictly worse
    than the unconditional ack this replaced, which at least terminated.

    So the return value is load-bearing and the caller MUST honour it: a drain that cannot persist
    the count cannot bound the retry, and must ack the envelope loudly rather than hold it.
    """
    target = path or spool_hold_path()
    try:
        from personalclaw.workflows.store import atomic_write

        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(
            target,
            json.dumps(
                {"event_id": event_id, "held_retries": int(held_retries)}, separators=(",", ":")
            ),
        )
        return True
    except Exception:
        return False


def clear_spool_hold(*, path: Path | None = None) -> None:
    """Forget the hold. Called whenever the drain acks past the envelope it was holding."""
    try:
        (path or spool_hold_path()).unlink(missing_ok=True)
    except Exception:
        return


# ── the dispatch record ──


@dataclass
class Dispatch:
    """One trigger fire on its way to a target, with per-target delivery state.

    `attempts` and `status` are per-target because one fire can have
    several (notify AND inbox), and a
    single status would make "delivered" mean "delivered somewhere" — which is what a user reads as
    "it worked" when half of it did not.
    """

    id: str
    trigger_id: str
    event_id: str
    kind: str = WakeKind.WAKE.value
    targets: dict[str, str] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    created_at: float = 0.0

    def mark(self, target: str, status: str) -> None:
        self.targets[target] = status
        if status != DeliveryStatus.DELIVERED.value:
            self.attempts[target] = self.attempts.get(target, 0) + 1

    @property
    def fully_delivered(self) -> bool:
        """Every target delivered. A dispatch with no targets is NOT delivered.

        No targets means nobody was told, which for `delivery: none` is
        correct and for anything else is
        a bug — so the honest answer is False and the caller decides.
        """
        return bool(self.targets) and all(
            s == DeliveryStatus.DELIVERED.value for s in self.targets.values()
        )

    @property
    def given_up(self) -> list[str]:
        return sorted(t for t, s in self.targets.items() if s == DeliveryStatus.GIVEN_UP.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "trigger_id": self.trigger_id,
            "event_id": self.event_id,
            "kind": self.kind,
            "targets": dict(self.targets),
            "attempts": dict(self.attempts),
            "created_at": self.created_at,
            "fully_delivered": self.fully_delivered,
        }
