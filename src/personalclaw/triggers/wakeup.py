"""The WakeupDispatcher — inbox + wakeup, with `wake`/`resume` drop semantics (§3.2 — S89).

§3.2: "The scheduler never executes directly. A fired trigger enqueues a typed payload onto
the target
session's inbox queue + a wakeup signal; a **WakeupDispatcher** claims and drives runs. Two
wakeup kinds
with different drop semantics: **wake** — drain inbox; skipped entirely if the session is
already running
… **resume** — a gate-answer/HITL result for a parked run; **must re-queue until the parked lock
releases** — overlap guards must never eat gate answers intended for parked runs."

S88's `tick()` returns fires and deliberately does not run them. This is what receives them.

**🔴 TWO HAZARDS MEASURED IN THE SHIPPED `enqueue`, before a line of this was written.**

1. **`enqueue` DROPS the payload for an idle session.** It returns False and appends nothing unless
   `session.semaphore.locked()` or `force=True`. Driven: an idle session's queue stayed at
   length 0. For a
   3am cron that is the normal case — the session is idle precisely because nobody is using
   the machine —
   so a naive enqueue would silently lose exactly the fires this subsystem exists to deliver. Every
   enqueue here passes `force=True`, which is what the flag was added for ("covers the startup
   race where
   a task exists but hasn't acquired the lock").
2. **`enqueue` returns False when the session does not exist at all**, which is also the
normal case for a
   trigger whose session has never been opened. So a delivery that cannot be queued is
   reported, never
   assumed: `deliver()` returns a typed result and the caller decides whether to create the
   session or
   spool the payload.

Neither is a bug in `enqueue` — it was written for mid-turn chat nudges, where "the session is
idle so
just run it" is correct. It is the wrong default for a trigger fire, and the difference is invisible
unless you drive it.

**The two wakeup kinds, and why their drop semantics differ.**

* `wake` is **droppable**. It means "there is work in the inbox"; if the session is already
  running it will
  drain the queue on its own, so a second wake is noise. That is §3.2's "natural implementation of
  `overlap: skip`".
* `resume` is **never droppable**. It carries a gate ANSWER for a parked run. Dropping one
  because the
  session looks busy would strand the run forever waiting for a reply that was thrown away —
  §3.2 calls
  this out as what makes R11 resume-targets and R13 approvals safe. So a resume that cannot be
  delivered
  is REQUEUED, and `dispatch.droppable()` is the shipped predicate that decides.

**Crash-safety falls out** (§3.2): the payload lives in the inbox, so an executor crash loses
the run, not
the request. This module never executes; it queues, signals, and reports. One code path serves every
trigger kind.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class WakeKind(str, Enum):
    """§3.2's two wakeup kinds. An enum because their drop semantics are opposite, and a bare string
    would let a caller invent a third with no defined behaviour."""

    WAKE = "wake"
    RESUME = "resume"


#: Session-key prefixes, centralized here per §3.2's "all bus/queue key formats are
#: centralized in one
#: auditable module (the `MessageBusKeys` pattern) — extending the session-key conventions table
#: (`cron:{id}`, `cron-{id}` dashboard pair, `_bg`, `loop-<id>`, …) rather than inventing a
#: parallel one".
#:
#: `cron:` is preserved verbatim rather than renamed to `trigger:`: the shipped
#: `_STATELESS_PREFIXES`
#: reset behaviour, the `cron-{id}` dashboard pairing and `schedule_trigger`'s HTTP path all
#: key off it.
#: A new prefix would silently opt every migrated trigger out of conventions it already relies on.
KEY_PREFIX_TRIGGER = "cron:"
KEY_PREFIX_LOOP = "loop-"


def session_key_for(trigger_id: str, *, session: str = "") -> str:
    """The session key a trigger's fire targets.

    Three cases, all from §3's session-binding note:

    * `pinned:cron:{id}` → the stateful per-trigger session, so a cron that builds context
      across runs
      keeps it. Rendered as the shipped `cron:{id}` key, which is the same thing under its
      existing name.
    * `conversation:<key>` → an in-chat nudge renders into a live conversation.
    * anything else (including "") → a FRESH stateless key per fire, which is the shipped default.

    The raw id is used verbatim after the prefix. Namespaced ids (`schedule:j1`) keep their
    namespace,
    because the id namespace IS §6's migration map and rewriting it here would break the mapping.
    """
    raw = trigger_id.split(":", 1)[-1] if trigger_id.startswith("schedule:") else trigger_id
    binding = (session or "").strip()
    if binding.startswith("conversation:"):
        return binding.split(":", 1)[1] or f"{KEY_PREFIX_TRIGGER}{raw}"
    return f"{KEY_PREFIX_TRIGGER}{raw}"


@dataclass
class Wakeup:
    """One typed payload plus its wakeup signal — what §3.2 enqueues.

    `payload` is a dict rather than a formatted string: the executor decides how to render a
    fire, and
    baking prose in here would make the same fire read differently depending on which surface
    queued it.
    """

    kind: str
    trigger_id: str
    session_key: str
    payload: dict[str, Any] = field(default_factory=dict)
    #: Monotonic-ish sequence for ordering within a session. Supplied by the caller so a batch
    #: queued in
    #: one tick keeps its order; `0` means "unordered".
    seq: int = 0
    emitted_at: float = 0.0

    @property
    def droppable(self) -> bool:
        """Whether this wakeup may be discarded when the session is busy.

        Delegates to `dispatch.droppable` — the shipped predicate — rather than re-deriving
        the rule, so
        a change to the drop policy cannot disagree between the spool and the dispatcher.
        """
        from personalclaw.triggers.dispatch import droppable

        return droppable(self.kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "trigger_id": self.trigger_id,
            "session_key": self.session_key,
            "payload": dict(self.payload),
            "seq": self.seq,
            "emitted_at": self.emitted_at,
            "droppable": self.droppable,
        }


class Disposition(str, Enum):
    """What happened to one delivery attempt. Typed because a surface filters on it and prose
    would make
    "why did my automation not run" unanswerable."""

    #: Queued onto the session inbox. The executor will drain it.
    QUEUED = "queued"
    #: Dropped on purpose: a `wake` for a session already running, which will drain the inbox
    #: itself.
    SKIPPED_RUNNING = "skipped_running"
    #: Could not be queued and must be retried — a `resume` whose session is not ready.
    REQUEUED = "requeued"
    #: No session exists and none could be created. The caller spools or creates.
    NO_SESSION = "no_session"


@dataclass
class Delivery:
    """The result of one delivery attempt."""

    disposition: str
    wakeup: Wakeup
    reason: str = ""

    @property
    def delivered(self) -> bool:
        return self.disposition == Disposition.QUEUED.value

    @property
    def needs_retry(self) -> bool:
        return self.disposition == Disposition.REQUEUED.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition,
            "reason": self.reason,
            "delivered": self.delivered,
            "needs_retry": self.needs_retry,
            **self.wakeup.to_dict(),
        }


def wakeup_for(fire: Any, *, seq: int = 0, now: float = 0.0) -> Wakeup:
    """Build the `wake` a due fire becomes. Takes S88's `DueFire`.

    A fire is always a `wake`, never a `resume`: a resume answers a question a parked run
    asked, and a
    trigger firing on its schedule has asked nothing. Conflating them would make every
    scheduled fire
    un-droppable and defeat `overlap: skip`.
    """
    trigger = getattr(fire, "trigger", None)
    trigger_id = str(getattr(trigger, "id", "") or getattr(fire, "trigger_id", "") or "")
    return Wakeup(
        kind=WakeKind.WAKE.value,
        trigger_id=trigger_id,
        session_key=session_key_for(trigger_id, session=str(getattr(trigger, "session", "") or "")),
        payload={
            "trigger_id": trigger_id,
            "kind": str(getattr(trigger, "kind", "") or ""),
            "scheduled_for": float(getattr(fire, "scheduled_for", 0) or 0),
            "reason": str(getattr(fire, "reason", "") or ""),
        },
        seq=seq,
        emitted_at=now or time.time(),
    )


def resume_for(
    *, trigger_id: str, session_key: str, answer: dict[str, Any], seq: int = 0, now: float = 0.0
) -> Wakeup:
    """Build a `resume` — a gate answer for a parked run.

    `session_key` is passed in rather than derived: a resume targets the session that PARKED,
    which for a
    workflow gate is the run's own session and not necessarily the trigger's. Deriving it
    would deliver
    the answer to the wrong place, which reads as "the gate never got my reply".
    """
    return Wakeup(
        kind=WakeKind.RESUME.value,
        trigger_id=trigger_id,
        session_key=session_key,
        payload=dict(answer),
        seq=seq,
        emitted_at=now or time.time(),
    )


def is_running(sessions: Any, key: str) -> bool:
    """Whether the session is mid-turn.

    Reads the semaphore, which is what `enqueue` itself checks — asking a different question here
    (provider alive, session exists) would make the dispatcher and the queue disagree about
    "busy", and
    the payload would land on the wrong side of the drop rule.

    A missing session is NOT running: nothing is executing, so a `wake` for it is not redundant.
    """
    try:
        session = getattr(sessions, "_sessions", {}).get(key)
    except Exception:  # noqa: BLE001 - a probe must never break delivery
        return False
    if session is None:
        return False
    semaphore = getattr(session, "semaphore", None)
    try:
        return bool(semaphore.locked()) if semaphore is not None else False
    except Exception:  # noqa: BLE001
        return False


def deliver(sessions: Any, wakeup: Wakeup) -> Delivery:
    """Queue one wakeup onto its session inbox. Never raises.

    The whole point of the module, and the two measured hazards live here:

    * **`force=True` always.** The shipped `enqueue` appends nothing for an idle session
      unless forced —
      driven and confirmed: an idle session's queue stayed empty. A 3am cron fires precisely
      when the
      session is idle, so an unforced enqueue would silently drop the fires this subsystem delivers.
    * **A missing session is REPORTED, not assumed.** `enqueue` returns False when the key has no
      session, which is normal for a trigger whose session was never opened. `NO_SESSION`
      hands that back
      so the caller creates the session or spools the payload rather than believing a delivery
      happened.

    `wake` vs `resume` (§3.2): a `wake` for a running session is dropped, because that session
    will drain
    the inbox itself. A `resume` is never dropped — it carries a gate answer, and discarding
    it strands
    the parked run forever waiting for a reply that no longer exists.
    """
    if sessions is None:
        return Delivery(Disposition.NO_SESSION.value, wakeup, "no session manager")

    if wakeup.droppable and is_running(sessions, wakeup.session_key):
        return Delivery(
            Disposition.SKIPPED_RUNNING.value,
            wakeup,
            "session is already running and will drain its own inbox",
        )

    try:
        queued = sessions.enqueue(
            wakeup.session_key,
            _msg_ts(wakeup),
            _text(wakeup),
            force=True,
            wakeup=wakeup.to_dict(),
        )
    except Exception:  # noqa: BLE001 - a broken queue must not lose the reason it failed
        logger.debug("wakeup enqueue raised for %s", wakeup.session_key, exc_info=True)
        queued = False

    if queued:
        return Delivery(Disposition.QUEUED.value, wakeup, "")
    if not wakeup.droppable:
        # A resume that could not be queued MUST come back. §3.2: "must re-queue until the
        # parked lock
        # releases — overlap guards must never eat gate answers intended for parked runs."
        return Delivery(
            Disposition.REQUEUED.value, wakeup, "session not ready; a resume is never dropped"
        )
    return Delivery(
        Disposition.NO_SESSION.value,
        wakeup,
        "no session for this key; create it or spool the payload",
    )


def _msg_ts(wakeup: Wakeup) -> str:
    """The queue's message id.

    Derived from the trigger and its sequence rather than random, so a re-delivery of the same fire
    carries the same id — the queue's own `cancelled` set keys on it, and a fresh id per
    attempt would
    make a cancelled fire un-cancellable on retry.
    """
    return f"{wakeup.kind}:{wakeup.trigger_id}:{wakeup.seq or int(wakeup.emitted_at)}"


def _text(wakeup: Wakeup) -> str:
    """The queue's text slot.

    Deliberately a marker, not a rendered prompt: the executor owns rendering, and a prompt
    baked here
    would be the second place a fire's wording lives. The structured payload rides
    `kwargs['wakeup']`,
    which `enqueue` already forwards verbatim.
    """
    return f"[{wakeup.kind}:{wakeup.trigger_id}]"


def deliver_all(sessions: Any, wakeups: list[Wakeup]) -> list[Delivery]:
    """Deliver a batch, preserving order. Returns one `Delivery` per wakeup.

    One result per input, always — a caller diffing counts to find what happened would be doing the
    dispatcher's job, and §7 crit 8's "zero silent drops" applies here as much as to the fire path.
    """
    return [deliver(sessions, w) for w in wakeups]


def dispatch_fires(sessions: Any, fires: list[Any], *, now: float = 0.0) -> list[Delivery]:
    """Turn S88's `tick()` output into deliveries. The seam between the scheduler and the executor.

    Sequence numbers come from the batch position, so fires queued in one tick keep their
    order in the
    inbox. Without that a five-trigger coalesced wake could drain in any order, and a user
    watching two
    dependent automations would see them run backwards.
    """
    stamp = now or time.time()
    wakeups = [wakeup_for(f, seq=i + 1, now=stamp) for i, f in enumerate(fires or [])]
    return deliver_all(sessions, wakeups)


def retry_queue(deliveries: list[Delivery]) -> list[Wakeup]:
    """The wakeups that must be re-attempted — the resumes §3.2 refuses to let anyone drop.

    Returned as wakeups rather than deliveries so the caller can feed them straight back into
    `deliver_all` on the next tick without unwrapping.
    """
    return [d.wakeup for d in deliveries if d.needs_retry]


def summary(deliveries: list[Delivery]) -> dict[str, Any]:
    """Per-disposition tally, for the ledger and the runs inbox.

    Every `Disposition` member is a key even at zero, so a surface renders a fixed set of
    chips instead of
    discovering dispositions at runtime.
    """
    counts = {d.value: 0 for d in Disposition}
    for delivery in deliveries:
        if delivery.disposition in counts:
            counts[delivery.disposition] += 1
    return {
        "total": len(deliveries),
        "delivered": counts[Disposition.QUEUED.value],
        "by_disposition": counts,
        "retry": counts[Disposition.REQUEUED.value],
    }
