"""The idle runtime — what actually FIRES a `kind:idle` trigger (§1.2 / §7 step 9 — WF2AUT-11).

**🔴 MEASURED BEFORE A LINE WAS WRITTEN.** `idle` has been a fully declared kind since S87: it is
in `models.KINDS`, `SPEC_KEYS['idle']` names its three fields (`scope`, `idle_secs`,
`first_idle_secs`), and `nl_kind` gives it NL phrasings, so a user can ask for "nudge me when the
session goes quiet", have it persisted, listed by `/api/triggers` and rendered on the Automations
page — and **nothing fired it**. `dispatch.py`, `executor.py` and `firepath.py` contain no mention
of it, and `idle_secs` was read only by `autonudge.py`. The clock tick could never surface one
either: `service.due_ids` skips any trigger with no `next_fire_at`, and an idle trigger has none
because its due-ness is not a schedule — it is a *predicate over session activity*.

That is the same declared-but-unpolled shape as `file` (S93), `web_watch` (S121) and
`run_completed` (S122), and it is closed the same way: a runtime that enumerates the kind, decides
due-ness from real state, and hands a fire to the shipped dispatch→executor chain.

**Why this rides the TICK rather than a fourth poll loop.** `file` and `web_watch` each got their
own gateway loop because each polls an external world (a filesystem, the network) on its own
cadence. Idle polls nothing: the input is the session manager already in memory and a per-trigger
`last_fire`/`armed_at` stamp. `loop.tick_once` already wakes at most every
`service.MAX_SLEEP_SECS` (30s), already holds the `sessions` manager, and is already the one place
that turns a decision into a dispatch. A fifth loop would only add a second thing that can be
forgotten — which is exactly the defect this module exists to close. So `tick_once` calls
`poll(...)` once per iteration, above its `if not result.fires` early return, for the same reason
the spool drain sits there: an idle session is *precisely* when an idle trigger is due, and
returning early on an empty due-set would skip it exactly then.

**🔴 HALF 2 LANDED (WF2AUT-11): `autonudge.py` is deleted and the loop tick engine rides this
runtime.** The two populations that used to coexist — user idle triggers here, loop-worker nudge
loops in autonudge's private store + timers — now live in ONE store and tick off ONE poll. The
split that remains is a ROUTING split, decided per row by one spec read (`_is_nudge`):

* **A plain idle row** (no `spec.message`) fires through the shipped wake path below —
  `wakeup.dispatch_fires` + `executor.drain` — unchanged.
* **A nudge row** (`spec.message` present) is handed to `triggers.nudge.AutoNudgeService.deliver`,
  which carries the old timer's fire body verbatim in meaning (stop-sentinel → remove;
  `max_cycles` → deactivate; delivered-only counting through this module's `record_delivery`) and
  whose `on_fire` is the gateway's loop-cycle driver, injected exactly as before. A due nudge row
  with no service to hand it to is a typed skip (`nudge_service_unavailable`), never a
  fall-through to the wake path.
* **The overlap that WOULD be possible is still refused.** A plain idle trigger scoped to a
  session some nudge loop drives is skipped with the same typed reason (`autonudge_owns_session`,
  kept verbatim for wire/test stability) — logged, never silently dropped (§7 crit 8). The fence
  is now a scan of the rows already in hand (`_nudge_owned_sessions`) rather than a probe into a
  second store, because there is no second store.

**Preserved autonudge semantics** (from the deleted `autonudge.py`'s timer body, kept verbatim
in meaning — the historical line references live in the WF2AUT-11 design note):

* **Reactive re-arm.** A fire re-arms from the fire, not from a fixed grid: `armed_at` is stamped
  to the fire instant so the next fire is `idle_secs` after *this* one settled. User activity
  re-arms too — `notify_activity` restamps `armed_at`, which is autonudge's
  `notify_user_input` cancel-and-re-arm expressed as state instead of a timer (a poll has no timer
  to cancel; moving the arm point forward is the same thing and survives a restart, which the
  timer did not).
* **Delivered-only counting.** `cycle_count` and `last_fire` advance ONLY when the dispatcher
  reports `delivered`. Autonudge's comment says why: "skipped nudges (e.g. session mid-turn)
  inflate cycle_count and prematurely trip max_cycles".
* **Mid-turn drop.** A wake for a session that is mid-turn is dropped by `wakeup.deliver`
  (`SKIPPED_RUNNING`) because that session drains its own inbox. That drop is what makes the
  delivered-only rule load-bearing rather than decorative — and because the counter does not move,
  the trigger stays armed and retries on the next tick.
* **`first_idle_secs`, one-shot, `0` = disabled.** A freshly-created idle trigger may fire after a
  shorter first wait so it starts promptly instead of sitting for the full `idle_secs`; the short
  wait applies only while `cycle_count == 0` and is spent on the first DELIVERED fire.

**State is a SIDECAR.** `config_dir()/trigger-idle/<safe-id>.json`, matching `file_poll`'s
`trigger-watch/` and the `task_leases/` convention. `armed_at`/`cycle_count` churn on every turn;
writing them onto the trigger entity would rewrite `triggers.json` constantly and race every
unrelated edit (S61d).
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from personalclaw.triggers.provider import armable

logger = logging.getLogger(__name__)

#: The `idle` spec's default quiet period, in seconds. The same 60s `NudgeLoop.idle_secs` defaults
#: to, so an absorbed autonudge loop keeps its cadence rather than acquiring a new one.
DEFAULT_IDLE_SECS = 60.0

#: A trigger id → filesystem-safe sidecar name. An idle trigger's id carries a `:`
#: (`idle:standup`), legal on macOS/Linux but not Windows; folded exactly as `file_poll` folds its
#: watch ids.
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")

#: Why an enabled idle trigger did not fire this poll. Typed so the caller can log a reason rather
#: than a silence — §7 criterion 8 counts an unexplained non-fire as a silent drop.
SKIP_NOT_IDLE = "not_idle_yet"
SKIP_AUTONUDGE = "autonudge_owns_session"
SKIP_SESSION_BUSY = "session_mid_turn"
#: A due nudge row with no nudge service to hand it to (flag off, or an API-only process). The
#: state stays un-advanced so the fire retries once the service exists — same rule as
#: `no_session_manager` below, and NEVER a fall-through to the wake path.
SKIP_NUDGE_UNAVAILABLE = "nudge_service_unavailable"


@dataclass
class IdleState:
    """One idle trigger's runtime state. The sidecar's whole contents.

    `armed_at` is the instant the quiet period restarted — a fire, a user turn, or first sight of
    the trigger. `cycle_count` counts DELIVERED fires only, which is what makes `first_idle_secs`
    a genuine one-shot: a mid-turn drop leaves the count at 0 and the short first wait still armed.

    `error_count` and `created_ts` carry the nudge adapter's per-loop state (WF2AUT-11 half 2):
    consecutive errored turns (turn-churning, so it belongs here per S61d, not on the row) and
    the loop's birth time (the trigger entity deliberately keeps no birth time — its legacy field
    map says so — so the `/api/autonudge` wire's `created_ts` lives here instead). Both read as 0
    on every sidecar written before the port, which is the right default for each.
    """

    armed_at: float = 0.0
    cycle_count: int = 0
    last_fire: float = 0.0
    error_count: int = 0
    created_ts: float = 0.0


def _state_dir(base_dir: Path | str | None) -> Path:
    from personalclaw.config.loader import config_dir

    root = Path(base_dir) if base_dir else config_dir()
    return root / "trigger-idle"


def _state_path(trigger_id: str, base_dir: Path | str | None) -> Path:
    safe = _SAFE_RE.sub("-", trigger_id) or "idle"
    return _state_dir(base_dir) / f"{safe}.json"


def load_state(trigger_id: str, *, base_dir: Path | str | None = None) -> IdleState:
    """Read one trigger's sidecar. A missing or damaged file reads as fresh state.

    Fail-open on a bad read rather than raising: a corrupt sidecar would otherwise stop the
    trigger firing forever, and the worst case of treating it as fresh is one extra quiet period.
    """
    path = _state_path(trigger_id, base_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return IdleState()
    except Exception:  # noqa: BLE001 - a damaged sidecar must not stop the trigger
        logger.warning("idle state unreadable for %s; treating as fresh", trigger_id)
        return IdleState()
    if not isinstance(raw, dict):
        return IdleState()
    return IdleState(
        armed_at=float(raw.get("armed_at", 0) or 0),
        cycle_count=int(raw.get("cycle_count", 0) or 0),
        last_fire=float(raw.get("last_fire", 0) or 0),
        error_count=int(raw.get("error_count", 0) or 0),
        created_ts=float(raw.get("created_ts", 0) or 0),
    )


def save_state(trigger_id: str, state: IdleState, *, base_dir: Path | str | None = None) -> None:
    """Persist one trigger's sidecar. Best-effort — an unwritable state dir must not stop a fire."""
    path = _state_path(trigger_id, base_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        from personalclaw.atomic_write import atomic_write

        atomic_write(path, json.dumps(asdict(state), indent=2))
    except Exception:  # noqa: BLE001 - state is a cache of when to fire, not the fire itself
        logger.warning("could not persist idle state for %s", trigger_id, exc_info=True)


def clear_state(trigger_id: str, *, base_dir: Path | str | None = None) -> None:
    """Delete one trigger's sidecar — a removed nudge loop must not leave a stale arm point
    behind for a future row that re-uses the id. Best-effort, like `save_state`."""
    try:
        _state_path(trigger_id, base_dir).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        logger.debug("could not clear idle state for %s", trigger_id, exc_info=True)


def idle_triggers(store: Any) -> list[Any]:
    """Every idle trigger the substrate should fire on its own.

    `fires_automatically` rather than `enabled`: it asks the whole question (enabled AND active AND
    not `manual`), and checking `enabled` alone is how an autopaused trigger keeps firing.
    """
    rows = []
    for trigger in armable(store):
        if trigger.kind != "idle":
            continue
        if not trigger.fires_automatically:
            continue
        rows.append(trigger)
    return rows


def scope_session(trigger: Any) -> str:
    """The session the trigger watches, from `spec.scope` (§1.2: `session:<key>` | `gateway`).

    `gateway` scope returns `""` — a gateway-wide idle period is not one session's quiet, and the
    delivery target is decided by `wakeup.session_key_for` from the trigger id like any other fire.
    """
    spec = trigger.spec if isinstance(getattr(trigger, "spec", None), dict) else {}
    scope = str(spec.get("scope") or "").strip()
    if scope.startswith("session:"):
        return scope.split(":", 1)[1].strip()
    return ""


def _secs(spec: dict[str, Any], key: str, default: float) -> float:
    try:
        value = float(spec.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def wait_secs(trigger: Any, state: IdleState) -> float:
    """How long this trigger must stay quiet before its NEXT fire.

    `first_idle_secs` applies only while no fire has been DELIVERED (`cycle_count == 0`), and `0`
    disables it — the trigger then waits the full `idle_secs` for its first fire too, which is
    autonudge's original behavior. Kept as one function so the poll and any surface asking
    "when will this fire?" cannot disagree.
    """
    spec = trigger.spec if isinstance(getattr(trigger, "spec", None), dict) else {}
    idle_secs = _secs(spec, "idle_secs", DEFAULT_IDLE_SECS)
    try:
        first = float(spec.get("first_idle_secs", 0) or 0)
    except (TypeError, ValueError):
        first = 0.0
    if first > 0 and state.cycle_count == 0:
        return first
    return idle_secs


def is_idle(trigger: Any, state: IdleState, *, now: float) -> tuple[bool, str]:
    """Whether the quiet period has elapsed. Returns `(due, reason_when_not)`.

    A pure decision over `(spec, state, now)` — no clock read, no session probe — so a test drives
    an exact instant and the doctor can explain a non-fire without side effects.
    """
    if state.armed_at <= 0:
        # First sight: arm from now rather than firing immediately. A trigger created seconds ago
        # has not observed a quiet period yet, and firing on sight would nudge a user who is
        # actively typing — the opposite of what an idle trigger means.
        return False, SKIP_NOT_IDLE
    quiet_for = now - state.armed_at
    if quiet_for < wait_secs(trigger, state):
        return False, SKIP_NOT_IDLE
    return True, ""


def notify_activity(
    trigger_ids: list[str] | None = None,
    *,
    session_key: str = "",
    store: Any = None,
    now: float = 0.0,
    base_dir: Path | str | None = None,
) -> list[str]:
    """Re-arm on user activity — autonudge's `notify_user_input`, as state instead of a timer.

    Returns the ids re-armed. Autonudge CANCELLED a pending `asyncio` timer; a poll has no timer to
    cancel, so the equivalent is moving the arm point to now, which also survives a restart (the
    cancelled timer did not). Either an explicit id list or a `session_key` + `store` to resolve
    the scope; a `session_key` matching no idle trigger re-arms nothing and is not an error.
    """
    now = now or time.time()
    ids = list(trigger_ids or [])
    if not ids and store is not None and session_key:
        ids = [t.id for t in idle_triggers(store) if scope_session(t) == session_key]
    rearmed = []
    for trigger_id in ids:
        state = load_state(trigger_id, base_dir=base_dir)
        state.armed_at = now
        save_state(trigger_id, state, base_dir=base_dir)
        rearmed.append(trigger_id)
    return rearmed


def _is_nudge(trigger: Any) -> bool:
    """Whether this idle row is a NUDGE row — `triggers.nudge.is_nudge`, without the import.

    Inlined rather than imported because `nudge.py` imports THIS module at module level; the
    discriminator is one spec read, and duplicating it is cheaper than a lazy import on every
    poll iteration. `tests` pin the two against each other.
    """
    spec = trigger.spec if isinstance(getattr(trigger, "spec", None), dict) else {}
    return bool(str(spec.get("message") or "").strip())


def _nudge_owned_sessions(triggers: list[Any]) -> set[str]:
    """The sessions a nudge row already owns (the anti-double-fire fence, post-port).

    Before WF2AUT-11 half 2 this was a probe into `autonudge.py`'s separate store; both
    populations now live in ONE store, so the fence is a scan of the rows already in hand. The
    question is unchanged — a plain idle trigger scoped to a session some nudge loop drives must
    defer, with the same typed reason (`SKIP_AUTONUDGE`), or the session gets nudged twice.
    Deactivated nudge rows are not in `triggers` (armable drops them), so a paused loop releases
    its session — exactly as a removed autonudge loop used to.
    """
    return {scope_session(t) for t in triggers if _is_nudge(t)} - {""}


def due_fires(
    store: Any, *, now: float = 0.0, base_dir: Path | str | None = None
) -> tuple[list[Any], list[dict[str, str]]]:
    """Decide which idle triggers are due. Returns `(fires, skipped)` and NEVER dispatches.

    Split from `poll` for the same reason `service.tick` is split from `loop.run_forever`: the
    decision is pure and drivable, and the dispatch is the caller's. Each skip carries a reason so
    the caller can log it — a non-fire with no reason is the silent drop §7 criterion 8 bans.

    Newly-seen triggers are ARMED here (their sidecar stamped with `now`), which is why this is not
    read-only: an unarmed trigger has no quiet period to measure, and leaving it unarmed would make
    it permanently not-due.
    """
    from personalclaw.triggers.service import DueFire

    now = now or time.time()
    fires: list[Any] = []
    skipped: list[dict[str, str]] = []
    triggers = idle_triggers(store)
    owned = _nudge_owned_sessions(triggers)
    for trigger in triggers:
        try:
            state = load_state(trigger.id, base_dir=base_dir)
            if state.armed_at <= 0:
                state.armed_at = now
                save_state(trigger.id, state, base_dir=base_dir)
                skipped.append({"trigger_id": trigger.id, "reason": SKIP_NOT_IDLE})
                continue
            session_key = scope_session(trigger)
            if not _is_nudge(trigger) and session_key in owned:
                skipped.append({"trigger_id": trigger.id, "reason": SKIP_AUTONUDGE})
                continue
            due, why = is_idle(trigger, state, now=now)
            if not due:
                skipped.append({"trigger_id": trigger.id, "reason": why})
                continue
            fires.append(DueFire(trigger=trigger, scheduled_for=state.armed_at, reason="idle"))
        except Exception:  # noqa: BLE001 - one bad trigger must not stop the others
            logger.warning("idle poll failed for %s", getattr(trigger, "id", "?"), exc_info=True)
    return fires, skipped


def record_delivery(
    trigger_id: str,
    *,
    delivered: bool,
    now: float = 0.0,
    base_dir: Path | str | None = None,
) -> IdleState:
    """Advance one idle trigger's state after a dispatch. Returns the state as it now stands.

    **🔴 DELIVERED-ONLY (autonudge lines 343-352, verbatim in meaning).** A fire the dispatcher
    dropped — the mid-turn case — advances NOTHING: not `cycle_count`, not `last_fire`, not
    `armed_at`. Autonudge's own comment is the reason: "skipped nudges (e.g. session mid-turn)
    inflate cycle_count and prematurely trip max_cycles". Leaving `armed_at` untouched also keeps
    the trigger due, so the next tick retries instead of waiting another whole quiet period for a
    fire that never reached anyone.

    A delivered fire re-arms REACTIVELY from the fire instant (autonudge's `notify_turn_complete`
    arm point), and spends `first_idle_secs` by taking `cycle_count` off zero.
    """
    now = now or time.time()
    state = load_state(trigger_id, base_dir=base_dir)
    if not delivered:
        return state
    state.cycle_count += 1
    state.last_fire = now
    state.armed_at = now
    save_state(trigger_id, state, base_dir=base_dir)
    return state


async def poll(
    store: Any,
    sessions: Any,
    runner: Any,
    *,
    now: float = 0.0,
    base_dir: Path | str | None = None,
) -> tuple[int, list[dict[str, str]]]:
    """Fire every due idle trigger through the shipped chain. Returns `(delivered, skipped)`.

    The one runtime `KIND_RUNTIMES` names for `idle`. Deliberately reuses `wakeup.dispatch_fires`
    and `executor.drain` rather than a second dispatch path: an idle fire must walk exactly the
    gates a clock fire walks, and a private path is precisely how the `web_watch` screen gap (S134)
    happened.

    `sessions is None` (an API-only process) is reported, not treated as a delivery: the state
    stays un-advanced so the fire retries once a session manager exists, rather than counting a
    cycle nobody received.
    """
    from personalclaw.triggers import executor as ex
    from personalclaw.triggers import wakeup as wk

    now = now or time.time()
    fires, skipped = due_fires(store, now=now, base_dir=base_dir)
    if not fires:
        return 0, skipped

    # ── Nudge rows ride the adapter, not the wake path (WF2AUT-11 half 2). ──
    # A message-bearing idle row means "inject this into the bound conversation" — the loop
    # tick engine's fire — and its deliverer (the gateway's loop-cycle driver) lives on the
    # nudge service, injected exactly as the old autonudge `on_fire` was. State advances via the
    # SAME delivered-only rule: `deliver` calls `record_delivery` itself, so a mid-turn drop
    # leaves the trigger due and it retries next tick. An absent service (flag off, or an
    # API-only process) is a typed skip, never a fall-through to the wake path — waking a loop
    # worker's inbox instead of nudging it would be a silent behavior change.
    nudge_fires = [f for f in fires if _is_nudge(f.trigger)]
    fires = [f for f in fires if not _is_nudge(f.trigger)]
    delivered_nudges = 0
    if nudge_fires:
        from personalclaw.triggers import nudge as nudge_mod

        service = nudge_mod.get_instance()
        for fire in nudge_fires:
            if service is None:
                skipped.append({"trigger_id": fire.trigger.id, "reason": SKIP_NUDGE_UNAVAILABLE})
                continue
            try:
                ok, why = await service.deliver(fire.trigger, now=now)
            except Exception:  # noqa: BLE001 - one nudge must not strand the others
                logger.warning("nudge deliver failed for %s", fire.trigger.id, exc_info=True)
                ok, why = False, "deliver_error"
            if ok:
                delivered_nudges += 1
            else:
                skipped.append({"trigger_id": fire.trigger.id, "reason": why or SKIP_SESSION_BUSY})

    if not fires:
        return delivered_nudges, skipped
    if sessions is None:
        for fire in fires:
            skipped.append({"trigger_id": fire.trigger.id, "reason": "no_session_manager"})
        return delivered_nudges, skipped

    deliveries = wk.dispatch_fires(sessions, fires, now=now)
    by_id = {d.wakeup.trigger_id: d for d in deliveries}
    delivered_count = delivered_nudges
    for fire in fires:
        delivery = by_id.get(fire.trigger.id)
        delivered = bool(delivery is not None and delivery.delivered)
        record_delivery(fire.trigger.id, delivered=delivered, now=now, base_dir=base_dir)
        if not delivered:
            # The mid-turn drop, made VISIBLE. `wakeup` already classified it; recording the
            # disposition as the reason is what turns "my nudge did not happen" into an answer.
            reason = (
                str(getattr(delivery, "disposition", "") or SKIP_SESSION_BUSY)
                if delivery is not None
                else SKIP_SESSION_BUSY
            )
            skipped.append({"trigger_id": fire.trigger.id, "reason": reason})
            continue
        delivered_count += 1
        key = wk.session_key_for(fire.trigger.id, session=str(getattr(fire.trigger, "session", "")))
        try:
            await ex.drain(sessions, key, runner, now=now, base_dir=base_dir)
        except Exception:  # noqa: BLE001 - one trigger's drain must not strand the others
            logger.warning("idle drain failed for %s", fire.trigger.id, exc_info=True)
    return delivered_count, skipped
