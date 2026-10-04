"""The claim store: which trigger is running right now (§3.1 overlap).

**🔴 TWO MEASURED DEFECTS THIS CLEARS.** `scheduling.claim_fire` decides overlap from an `existing`
claim the caller supplies, and `firepath.evaluate` returns the claim it granted with the note "the
caller must release it". **`tick()` passes no `existing_claim` and nothing persists the
granted one.** So:

1. **`overlap: skip` is inert.** Every fire is evaluated against `existing=None`, so the claim gate
   always grants — a trigger whose previous run is still going fires again anyway, which is the
   precise failure `overlap` exists to prevent. The control is present, reviewed, and enforcing
   nothing.
2. **`is_running` is unanswerable.** `ScheduleService` answers it from `self._executing`, a
   PROCESS-LOCAL dict — so it is wrong after a restart (a run in flight reads as idle) and invisible
   to any other process. The API facade needs this to re-point off `ScheduleService`, and a
   process-local set cannot serve it.

A claim is therefore a **sidecar file per trigger** (`<store dir>/trigger-claims/<safe-id>.json`),
the same convention as `trigger-watch/` and `task_leases/`, for the same reasons: it
is high-churn runtime state that must not rewrite `triggers.json` on every fire, and it must be
visible ACROSS processes — the MCP tools, the gateway tick, and the API all answer "is it running"
from one place. The root comes from the STORE, so a claim never describes a different store's
trigger.

**Expiry is read-time, not swept.** A claim carries `max_duration_secs`; a reader treats an older
claim as absent rather than requiring a janitor to have run. A crashed run must not hold its trigger
hostage until some cleanup pass notices — the same fail-open direction `pool`'s leases take.

**A claim also names its OWNER** (`owner_pid`). Read-time expiry bounds a crashed run at
one hour and the reaper bounds it at thirty minutes, but both are DEADLINES: until one elapses the
run reads as in flight, so the first answer a user gets after a restart is "still running" about a
process that no longer exists. The owning pid turns that into an OBSERVATION — see `orphaned_ids`,
and the boot pass in `reaper.terminalize_orphans` that consumes it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_json_write
from personalclaw.triggers.scheduling import CLAIM_MAX_DURATION_SECS, Claim

logger = logging.getLogger(__name__)

_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _claims_dir(base_dir: Path | str | None) -> Path:
    from personalclaw.config.loader import config_dir

    root = Path(base_dir) if base_dir else config_dir()
    return root / "trigger-claims"


def _claim_path(trigger_id: str, base_dir: Path | str | None) -> Path:
    safe = _SAFE_RE.sub("-", trigger_id) or "claim"
    return _claims_dir(base_dir) / f"{safe}.json"


def read_claim(
    trigger_id: str, *, now: float = 0.0, base_dir: Path | str | None = None
) -> Claim | None:
    """The live claim for a trigger, or None when it is idle.

    An EXPIRED claim reads as None (read-time expiry): a crashed run must not hold its
    trigger hostage until a janitor notices. A malformed record also reads as None — an
    unparseable claim that blocked every future fire would be worse than one that is ignored,
    and the row is visible on disk for a human either way.
    """
    now = now or time.time()
    path = _claim_path(trigger_id, base_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        claimed_at = float(raw.get("claimed_at") or 0.0)
        max_secs = float(raw.get("max_duration_secs") or CLAIM_MAX_DURATION_SECS)
        owner_pid = int(raw.get("owner_pid") or 0)
        owner_image = str(raw.get("owner_image") or "")
    except (TypeError, ValueError):
        return None
    if claimed_at <= 0 or now - claimed_at >= max_secs:
        return None
    return Claim(
        trigger_id=str(raw.get("trigger_id") or trigger_id),
        holder=str(raw.get("holder") or ""),
        claimed_at=claimed_at,
        max_duration_secs=max_secs,
        # 🪤 PASSED EXPLICITLY, including the 0 for a record that carries no owner. `Claim.owner_pid`
        # has a `default_factory=os.getpid`, so omitting it here would stamp the READER's pid — and
        # every claim on the machine would then read as owned by a live process, which is precisely
        # the always-true liveness check `orphaned_ids` exists to avoid.
        owner_pid=owner_pid,
        # The same for the image, and the same "" for a record that carries none: defaulted, every
        # claim this pid's previous image left would read as this image's own.
        owner_image=owner_image,
    )


def write_claim(claim: Any, *, base_dir: Path | str | None = None) -> None:
    """Persist a granted claim atomically, through the one JSON writer.

    Atomic because a half-written claim read back as malformed would read as IDLE, and the whole
    point of the record is that a second fire can see the first one.
    """
    if claim is None or not getattr(claim, "trigger_id", ""):
        return
    path = _claim_path(claim.trigger_id, base_dir)
    payload = {
        "trigger_id": claim.trigger_id,
        "holder": getattr(claim, "holder", ""),
        "claimed_at": float(getattr(claim, "claimed_at", 0.0)),
        "max_duration_secs": float(
            getattr(claim, "max_duration_secs", CLAIM_MAX_DURATION_SECS) or CLAIM_MAX_DURATION_SECS
        ),
        # The OWNING process, read from the claim rather than from `os.getpid()` here:
        # this function is the persister, and the grantor is the owner. Taking the pid at write time
        # would be right today by coincidence and wrong the first time a claim is written by
        # anything other than the process that runs the fire.
        "owner_pid": int(getattr(claim, "owner_pid", 0) or 0),
        "owner_image": str(getattr(claim, "owner_image", "") or ""),
    }
    atomic_json_write(path, payload)


#: What the claim of a run of yours is held as (:func:`hand_run_holder`): Run now, the review's Run
#: now or an answer, all through the attended dispatch (`trigger_runs._dispatch_store_action`).
_BY_HAND = "hand:"
#: What the claim of a fire someone else asked for is held as (:func:`asked_holder`): an agent's,
#: an app's, another automation's or a program's Run now. Any other fire's claim is held by what
#: admitted it: the tick, an event, a webhook's request, a view's render.
_ASKED = "asked:"


def hand_run_holder(event: str, *, at: float) -> str:
    """The holder of the claim a run of yours takes: what started it, and when."""
    return f"{_BY_HAND}{event}:{int(at)}"


def asked_holder(source: str, *, at: float) -> str:
    """The holder of the claim a fire that *source* asked for takes (`triggers.run_source`)."""
    return f"{_ASKED}{source}:{int(at)}"


def source_of(holder: str) -> str:
    """Who asked for the run a claim's *holder* is for: `run_source.YOU` for a run of yours
    (:func:`hand_run_holder`), the asker of a fire someone else asked for (:func:`asked_holder`),
    and ``""`` for a fire of the automation's own, which its kind says.

    The passes that close a claim its run never gave back, the boot's and the deadline's
    (`triggers.reaper`), record the run as what it was: a run of yours leaves its trigger's health
    alone, as its own record does (`run_record.record_run`).
    """
    from personalclaw.triggers import run_source

    if holder.startswith(_BY_HAND):
        return run_source.YOU
    if holder.startswith(_ASKED):
        # Never yours: a run of yours holds a hand run's claim, and nothing else stands for one.
        asker = holder[len(_ASKED) :].split(":", 1)[0]
        return asker if asker in run_source.ASKERS and not run_source.yours(asker) else ""
    return ""


def release_claim(trigger_id: str, *, base_dir: Path | str | None = None) -> bool:
    """Drop a trigger's claim. Idempotent — releasing an absent claim is success, not an error.

    Idempotent on purpose: the release path runs in a `finally`, and a run that failed BEFORE its
    claim was written would otherwise turn its own cleanup into a second error.
    """
    path = _claim_path(trigger_id, base_dir)
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        logger.debug("could not release claim for %s", trigger_id, exc_info=True)
        return False


def is_running(trigger_id: str, *, now: float = 0.0, base_dir: Path | str | None = None) -> bool:
    """Whether a run is in flight for this trigger, ACROSS processes.

    Replaces `ScheduleService.is_running`, which answered from a process-local dict — wrong after a
    restart (an in-flight run read as idle) and invisible to the MCP process writing the same store.
    """
    return read_claim(trigger_id, now=now, base_dir=base_dir) is not None


def running_since(
    trigger_id: str, *, now: float = 0.0, base_dir: Path | str | None = None
) -> float | None:
    """When the in-flight run started, or None. Replaces `ScheduleService.running_since`."""
    claim = read_claim(trigger_id, now=now, base_dir=base_dir)
    return claim.claimed_at if claim else None


def running_ids(*, now: float = 0.0, base_dir: Path | str | None = None) -> list[str]:
    """Every trigger with a live claim, sorted. One directory scan for a whole list view.

    Sorted so a list surface renders in a stable order rather than in directory order, which changes
    between reads and makes rows appear to move on their own.
    """
    root = _claims_dir(base_dir)
    if not root.is_dir():
        return []
    out: list[str] = []
    for entry in sorted(root.iterdir()):
        if entry.suffix != ".json":
            continue
        try:
            raw = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        trigger_id = str(raw.get("trigger_id") or "") if isinstance(raw, dict) else ""
        if trigger_id and is_running(trigger_id, now=now, base_dir=base_dir):
            out.append(trigger_id)
    return out


def orphaned_ids(*, now: float = 0.0, base_dir: Path | str | None = None) -> list[tuple[str, int]]:
    """Live claims whose OWNING PROCESS is provably gone, as `(trigger_id, owner_pid)`.

    🔴 WHY THIS EXISTS (WF2AUT-16). A claim answered "since when" and never "by whom", so the only
    thing that could terminalize a run a crash orphaned was `reaper.overdue` — a 1800s DEADLINE, not
    an observation. For that whole window the run read as in-flight on every surface, which is the
    defect `guardrails/self_destruct.py` states in its own words: *"the ScheduleRunStore row never
    reaches a terminal state and the fire reads afterwards as a HUNG run rather than as a
    self-inflicted stop. The user is left debugging a phantom."* A gateway restart is the common
    case, and after one the owner is always gone — so the answer is available immediately.

    🪤 **PROVABLY gone, never merely unknown.** A claim with no `owner_pid` (`0`) is NOT reported
    here. That distinction is the whole of this function's correctness: "terminalize every claim
    whose owner I cannot confirm" is trivially easy to write, would free the claim of a run that is
    still executing, and would record that run as interrupted while it works — strictly worse than
    the deadline it replaces. This org has shipped that shape once already (a liveness check that
    measured the pid of the short-lived CLI writer, and so was always true).

    PID REUSE fails safe in the same direction: if the OS recycled a dead owner's pid, the claim
    reads as live and falls through to the reaper deadline — today's behaviour, not a new hole.

    🔴 A RESTART KEEPS THE PID. The gateway restarts by re-execing itself in place, so the owner of
    every claim the old image left is THIS process, and `pid_is_alive` answered True for it: the
    run read as in flight after every Restart until the deadline reaper recorded it as reaped. A
    claim naming this pid is therefore judged by its image (`scheduling.PROCESS_IMAGE`): this
    image's own is live, another image's was granted by a program that no longer runs. A claim
    carrying no image is unknown, and like an unknown owner it is left to the deadline.

    A pure read, like `overdue`, so the boot pass and a test can ask without causing an effect;
    sorted for a stable, reproducible sweep order.
    """
    from personalclaw.gateway_base import pid_is_alive
    from personalclaw.triggers.scheduling import PROCESS_IMAGE

    now = now or time.time()
    out: list[tuple[str, int]] = []
    for trigger_id in running_ids(now=now, base_dir=base_dir):
        claim = read_claim(trigger_id, now=now, base_dir=base_dir)
        if claim is None:
            continue
        pid = int(getattr(claim, "owner_pid", 0) or 0)
        if pid <= 0:
            continue
        if pid == os.getpid():
            image = str(getattr(claim, "owner_image", "") or "")
            if not image or image == PROCESS_IMAGE:
                continue
        elif pid_is_alive(pid):
            continue
        out.append((trigger_id, pid))
    return sorted(out)


# ── named resource slots (§3.5 / AUTO-R9) ──


def slot_holders(
    store: Any, *, now: float = 0.0, base_dir: Path | str | None = None
) -> dict[str, str]:
    """`{slot_name: holding_trigger_id}` for every slot a RUNNING trigger holds.

    🔴 WHY THIS EXISTS. `Trigger.resource_slots` was declared in the entity, persisted, round-tripped
    by `to_dict`/`from_dict` — and read by **nothing**. Found by generalising S134's container audit
    across all 41 dataclasses in `triggers/`: it was the only field with zero
    non-declaration readers.
    §3.5 is explicit: *"triggers/runs declare needs (`gpu`, `local-llm`); the substrate serializes
    conflicting runs per slot and refuses over-capacity starts with a typed RESOURCE_BUSY + holder
    identity (a `deferred` ledger row)."* So a user could declare `resource_slots: ["local-llm"]` on
    three triggers and have all three run a local model at once — the exact contention §3.5
    exists to
    prevent on a machine shared with the interactive user.

    Derived from the CLAIM STORE rather than a second sidecar, which is the design decision here: a
    slot is held exactly as long as its trigger's run is, so claims already answer the
    question. That
    inherits read-time expiry (a crashed run does not hold `gpu` hostage forever) and cross-process
    visibility for free — a separate slot file would need its own reaper and could disagree with the
    claims about who is running.
    """
    now = now or time.time()
    held: dict[str, str] = {}
    for row in store.load():
        trigger = row.trigger
        # A row that does not PARSE contributes no holder. Found by a red test: a broken trigger
        # declaring `gpu` otherwise blocks every real `gpu` fire forever, because it can never run
        # and therefore never releases — a phantom holder is worse than an unserialized slot.
        if not getattr(row, "ok", True):
            continue
        slots = getattr(trigger, "resource_slots", None)
        if not slots or not isinstance(slots, (list, tuple)):
            continue
        if read_claim(trigger.id, now=now, base_dir=base_dir) is None:
            continue
        for slot in slots:
            name = str(slot or "").strip()
            # FIRST holder wins and is not overwritten: the answer to "who has the gpu" must be
            # stable across two calls in one tick, and a later row silently replacing an earlier
            # holder would make the refusal reason name the wrong trigger.
            if name and name not in held:
                held[name] = trigger.id
    return held


def busy_slot(
    trigger: Any,
    *,
    holders: dict[str, str] | None = None,
    store: Any = None,
    now: float = 0.0,
    base_dir: Path | str | None = None,
) -> tuple[str, str]:
    """The first slot this trigger wants but cannot have, as `(slot, holder_id)`; else `("", "")`.

    Returns the HOLDER too, because §3.5 asks for "holder identity" in the refusal: "the gpu is
    busy"
    sends a user looking through every automation they own, while "held by clock:nightly-index" is
    actionable.

    A trigger never blocks on a slot IT already holds — re-entering its own slot is what a retry
    inside one run looks like, and refusing that would deadlock a trigger against itself.
    """
    slots = getattr(trigger, "resource_slots", None)
    if not slots or not isinstance(slots, (list, tuple)):
        return ("", "")
    if holders is None:
        if store is None:
            return ("", "")
        holders = slot_holders(store, now=now, base_dir=base_dir)
    tid = str(getattr(trigger, "id", "") or "")
    for slot in slots:
        name = str(slot or "").strip()
        holder = holders.get(name, "")
        if name and holder and holder != tid:
            return (name, holder)
    return ("", "")
