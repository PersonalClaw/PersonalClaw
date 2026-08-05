"""Crash-safe scheduling discipline for triggers (AUTOMATION-SUBSTRATE §3.1 — S63).

The shipped `ScheduleService` mechanism is kept, not rewritten: one re-armed asyncio task sleeping
`min(next-due-delay, 30s)`, deterministic per-id BLAKE2b jitter, IANA-tz cron matching, the mtime
`_sync` for external edits, the fcntl lock, the reaper. §2's disposition is explicit that this is a
rename, not a rewrite — so this module is the DISCIPLINE layered on top,
and every function here is a
pure decision the service applies.

The four rules that make it crash-safe, and the failure each one prevents:

* **Persist-before-execute.** `next_fire_at` is computed and written to the
row BEFORE the run starts,
  so a crash mid-fire cannot double-fire. This replaces minute-match
  dueness as the primary source —
  measured: the shipped `_is_due` guards a same-minute refire with `last_run_ts // 60 == now // 60`,
  which is correct for a live process and useless across a restart that loses the in-memory clock.
* **Recompute from NOW, anchored to the created-at grid.** From completion time, never the missed
  slot, or a run that overruns its interval re-fires immediately and
  storms. Anchored so the recompute
  does not re-phase the schedule to whenever the overrun happened.
* **Boot stagger.** Overdue fires are pushed and spread by the SAME id-derived jitter the scheduler
  already uses, so a restart does not fire every automation at once.
* **Lock self-expiry.** The fire claim carries a max-duration, so a crashed
holder cannot permanently
  wedge a trigger — the reaper is defense in depth, not the only guard.

Pure functions over records. No timers, no I/O, no firing: the service that owns the loop is the
caller, and keeping the decisions separable is what makes them testable without a running gateway.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any

#: Poll ceiling. The shipped service sleeps `min(next-due, 30s)` so an external edit (an MCP tool in
#: another process mutating the store) is picked up within one poll via the
#: mtime sync. Kept verbatim:
#: that 30s IS the propagation contract other surfaces rely on.
POLL_CEILING_SECS = 30.0

#: How far overdue fires are pushed at boot, before staggering. A restart that fired everything at
#: once is the thundering herd this exists to prevent — and 60s is long enough for the gateway to
#: finish coming up, so the first automation of the day does not race provider registration.
BOOT_STAGGER_BASE_SECS = 60.0

#: The window overdue fires are spread across at boot.
BOOT_STAGGER_WINDOW_SECS = 120.0

#: How late a fire must be before it is recorded as `ran_late` rather than `ran` (§1.3 — S170).
#:
#: DERIVED, not chosen. Ordinary on-time scheduling already carries two known delays: a wake
#: can be up to `POLL_CEILING_SECS` behind (the tick sleeps up to 30s), and a boot pushes
#: overdue fires by
#: `BOOT_STAGGER_BASE_SECS` and spreads them across `BOOT_STAGGER_WINDOW_SECS` to avoid a thundering
#: herd. A threshold below their sum would label the substrate's OWN designed behaviour as lateness,
#: which is how a signal becomes noise and then gets ignored.
#:
#: So: base + window + one poll, doubled for headroom against a slow provider registration on the
#: same boot. Anything under this is scheduling; anything over it is a story — the lid was shut, the
#: machine was asleep, a run overran its interval.
LATE_THRESHOLD_SECS = 2 * (BOOT_STAGGER_BASE_SECS + BOOT_STAGGER_WINDOW_SECS + POLL_CEILING_SECS)


#: A fire claim's self-expiry. Beyond this the claim is considered abandoned and another fire may
#: take it — so a process killed mid-run cannot wedge a trigger forever.
#: One hour matches the lease
#: ceiling in `workflows.pool`: the same question (how long may one holder
#: hold?) should not have two
#: answers on one machine.
CLAIM_MAX_DURATION_SECS = 3600.0

#: Timer ceiling. `asyncio.sleep` on a far-future delay is fine, but a 32-bit millisecond timer is
#: not — the shipped emdash-style guard clamps and re-ticks rather than trusting one long sleep.
#: 24 days is comfortably under 2^31 ms.
TIMER_CEILING_SECS = 24 * 24 * 3600.0


def jitter_offset(trigger_id: str, window: float) -> float:
    """A stable offset in `[0, window)` derived from the trigger id.

    The SAME algorithm as the shipped `ScheduleService._jitter_offset`, deliberately duplicated in
    docstring intent rather than imported: this module must not import the legacy service (the
    dependency would point the wrong way for §2's absorb order), and the property that matters is
    that a migrated trigger lands in the SAME slot as the job it came from. A different algorithm
    here would re-phase every schedule on migration day.

    Deterministic, not random: a random offset re-rolls every fire, so two
    triggers can still collide
    on any given fire and a restart reshuffles everything. An id-derived offset spreads them into
    stable, distinct slots once and keeps them there.
    """
    if window <= 0:
        return 0.0
    digest = hashlib.blake2b((trigger_id or "").encode("utf-8"), digest_size=8).digest()
    fraction = int.from_bytes(digest, "big") / float(1 << 64)
    return fraction * window


class Dueness(str, Enum):
    """Why a trigger is or is not due. Typed because the service logs the reason on every pass.

    `NOT_ARMED` and `NOT_YET` are different: the first means nothing
    computed a next fire (a bug, or a
    trigger that was just enabled), the second means the schedule is working. Collapsing them would
    hide the bug behind the normal case.
    """

    DUE = "due"
    NOT_YET = "not_yet"
    NOT_ARMED = "not_armed"
    DISABLED = "disabled"
    EXPIRED = "expired"


def is_due(
    *,
    next_fire_at: float,
    now: float,
    fires_automatically: bool,
    expires_at: float = 0.0,
) -> tuple[bool, str]:
    """Whether a trigger should fire now, and why not when it should not.

    Reads the PERSISTED `next_fire_at` rather than re-deriving from the recurrence. That is the
    persist-before-execute rule's whole point: the row is the source of truth for what is due, so a
    crash between "decided to fire" and "fired" leaves a schedule that is still correct.

    An expired trigger is refused even when armed: auto-expiry exists so a user-created recurring
    automation needs a deliberate renewal, and honouring a stale `next_fire_at` past that date would
    defeat it.
    """
    if not fires_automatically:
        return False, Dueness.DISABLED.value
    if expires_at and now >= expires_at:
        return False, Dueness.EXPIRED.value
    if next_fire_at <= 0:
        return False, Dueness.NOT_ARMED.value
    if now < next_fire_at:
        return False, Dueness.NOT_YET.value
    return True, Dueness.DUE.value


def next_wake_delay(next_fires: list[float], now: float) -> float:
    """How long the single re-armed task should sleep.

    `min(earliest-due, POLL_CEILING)`, clamped to the timer ceiling and
    never negative. The ceiling is
    what picks up an external edit; the clamp is what keeps a far-future
    one-shot from sitting on one
    enormous sleep that a 32-bit timer would truncate.

    An empty schedule still sleeps the ceiling rather than returning 0: a
    busy-loop on an idle machine
    is the worst possible answer to "nothing to do".
    """
    upcoming = [t for t in next_fires if t > 0]
    if not upcoming:
        return POLL_CEILING_SECS
    delay = min(upcoming) - now
    if delay < 0:
        # Something is already overdue. Wake immediately, but not on a negative sleep.
        return 0.0
    return max(0.0, min(delay, POLL_CEILING_SECS, TIMER_CEILING_SECS))


def recompute_from_completion(
    *,
    interval_secs: float,
    created_at: float,
    completed_at: float,
) -> float:
    """The next fire for an interval trigger, from COMPLETION, anchored to the created-at grid.

    Two rules in one function because getting either alone wrong produces a distinct bug:

    * From completion, not the missed slot — a run that takes 90 seconds on
    a 60-second interval would
      otherwise be due the instant it finishes, forever, and the machine never idles.
    * Anchored to the `created_at` grid — recomputing as `completed_at + interval` re-phases the
      schedule to whenever the overrun happened, so a job created to run on the hour drifts to :07
      after one slow day and stays there.

    Returns the first grid slot strictly after completion, so the phase survives an overrun.
    """
    if interval_secs <= 0:
        return 0.0
    anchor = created_at if created_at > 0 else completed_at
    elapsed = completed_at - anchor
    slots = int(elapsed // interval_secs) + 1
    return anchor + slots * interval_secs


def boot_recovery(
    *, next_fire_at: float, now: float, trigger_id: str, catch_up: bool
) -> tuple[float, str]:
    """What an overdue trigger's `next_fire_at` becomes at boot, and why.

    Three cases, and the reason each is not the obvious one:

    * Not overdue — untouched. Re-arming a schedule that is still valid
    would re-phase it for no gain.
    * Overdue WITHOUT `catch_up` — pushed to the stagger window and the
    missed slot is DROPPED. Firing
      it now is what makes a restart run every automation at once, and the user did not ask for the
      missed one.
    * Overdue WITH `catch_up` — also pushed, not fired inline. The plan's
    catch_up is "fire ONCE at
      boot/wake", and doing that inside recovery would run it before the gateway finished starting.
      The stagger is what makes it survivable; session 65 owns the exactly-once bookkeeping.

    The push is deterministic per id, so two triggers overdue by the same
    amount do not land together.
    """
    if next_fire_at <= 0:
        return 0.0, "not_armed"
    if now < next_fire_at:
        return next_fire_at, "still_upcoming"
    staggered = now + BOOT_STAGGER_BASE_SECS + jitter_offset(trigger_id, BOOT_STAGGER_WINDOW_SECS)
    return staggered, "caught_up_staggered" if catch_up else "missed_dropped"


@dataclass
class Claim:
    """A fire claim. The single-flight enforcement point (§3.1).

    `max_duration` is the self-expiry: a process killed mid-run cannot
    permanently wedge the trigger,
    because the next pass sees an expired claim and may take it. That complements the reaper rather
    than replacing it — the reaper kills the orphan, this releases the schedule.
    """

    trigger_id: str
    holder: str
    claimed_at: float
    max_duration_secs: float = CLAIM_MAX_DURATION_SECS

    def expired(self, now: float) -> bool:
        return now >= self.claimed_at + max(1.0, self.max_duration_secs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "holder": self.holder,
            "claimed_at": self.claimed_at,
            "max_duration_secs": self.max_duration_secs,
            "expires_at": self.claimed_at + self.max_duration_secs,
        }


def claim_fire(
    existing: Claim | None,
    *,
    trigger_id: str,
    holder: str,
    now: float,
    overlap: str = "skip",
) -> tuple[Claim | None, str]:
    """Decide a fire claim. Returns `(claim, refusal)` — the decision, never the write.

    `overlap` is the trigger's own policy and it changes the answer, which is why this is not a
    generic lock:

    * `skip` (the default) — a held claim refuses the fire, and the caller records
      `skipped_overlap`. That is the semantics autonudge already has for a mid-turn nudge.
    * `parallel` — a held claim does NOT refuse; the trigger opted into concurrent runs.
    * `queue` — refuses like skip, but the caller is expected to enqueue rather than drop. The
      distinction lives in the OUTCOME the caller records, not here: this function's job is only to
      say whether this fire may proceed now.

    An EXPIRED claim never refuses anything. That is the self-expiry doing its work.
    """
    if overlap == "parallel":
        return Claim(trigger_id=trigger_id, holder=holder, claimed_at=now), ""
    if existing is not None and not existing.expired(now):
        return None, f"held by {existing.holder} since {int(now - existing.claimed_at)}s ago"
    return Claim(trigger_id=trigger_id, holder=holder, claimed_at=now), ""


def coalesce_wakes(next_fires: dict[str, float], now: float, window_secs: float = 1.0) -> list[str]:
    """Which triggers fire on this wake — everything due within one window.

    Coalescing is the reason N triggers replacing one 60s heartbeat do not wake the laptop N times.
    Returns ids in a STABLE order (by fire time, then id) so a fire batch is reproducible: an
    unstable order would make two runs of the same batch interleave
    differently and any bug in one of
    them intermittent.
    """
    due = [(t, tid) for tid, t in next_fires.items() if 0 < t <= now + window_secs]
    due.sort()
    return [tid for _t, tid in due]


def revalidate(
    *,
    still_enabled: bool,
    next_fire_at_at_arm: float,
    next_fire_at_now: float,
) -> tuple[bool, str]:
    """Whether a fire that was armed earlier may still proceed.

    The shipped-scheduler detail §3.1 calls out: re-fetch and revalidate on
    fire, bail if the trigger
    was disabled or rescheduled mid-wait. Without it, a trigger the user
    disabled while the timer slept
    still fires once — which reads as the off switch not working, the
    single most damaging kind of bug
    for an automation surface.
    """
    if not still_enabled:
        return False, "disabled while the timer slept"
    if next_fire_at_now != next_fire_at_at_arm:
        return False, "rescheduled while the timer slept"
    return True, ""
