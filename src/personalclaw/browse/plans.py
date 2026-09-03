"""Persisted, idempotent scheduled-actuator browse plans (BROWSE-AUTOMATION §(d)/A3, BA-6).

A long-running browse is NOT a long-lived session — it is a **persisted plan re-executed as
stateless one-tick runs**. Each plan lives in ``browse/plans/<id>.json`` and carries a
``cursor`` that is the whole of its progress, so killing the gateway mid-flow loses at most
one step and re-firing the same tick is a no-op at the same cursor.

Two kinds (§(d)):

* ``watch_page`` — one tick re-extracts the page and diffs the content against the cursor.
  A tick that sees the same content reports no change and leaves the cursor untouched; the
  cursor is the last content hash, so the operation is idempotent by construction.
* ``walk_flow`` — one step per tick. The cursor (a step index) advances ONLY on a verified
  success, so a crash between the browser action and the cursor write re-runs the same step
  rather than skipping it, and a plan that never verifies never advances.

**Autonomy floor (§(d) rung cap).** The ``action.browse`` PROVIDER sits at ``one_tap`` on the
earned-autonomy ladder; the floor for a persisted PLAN is decided here, per plan:

* A read-only ``watch_page`` plan may graduate up the ladder (its floor is ``one_tap``).
* A ``walk_flow`` plan that can SUBMIT registers ``floor=draft_only`` — it cannot run
  unattended until its type earns promotion, because a SUBMIT is an irreversible external
  write with no undo handle.
* A plan naming the ``user_browser`` target is refused at REGISTRATION with a typed error:
  that target's floor never permits unattended execution (:func:`target.permits_unattended`),
  so a scheduled tick, cron, or loop could never legally drive it — catching it at
  registration turns a run-time refusal into a configuration error the author sees at once.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.browse.target import (
    TARGET_GATEWAY,
    TARGET_KEY,
    permits_unattended,
    resolve_target,
)
from personalclaw.guardrails.autonomy import RUNG_DRAFT_ONLY, RUNG_ONE_TAP
from personalclaw.record_ids import record_path

# ── plan kinds ──────────────────────────────────────────────────────────────────────────
KIND_WATCH_PAGE = "watch_page"
KIND_WALK_FLOW = "walk_flow"
PLAN_KINDS: tuple[str, ...] = (KIND_WATCH_PAGE, KIND_WALK_FLOW)

#: One tick advances at most this many browser steps by default. A scheduled actuator takes
#: ONE step per tick so a crash loses ≤1 step; a caller may raise it but never below 1.
DEFAULT_MAX_STEPS_PER_TICK = 1


class PlanError(ValueError):
    """A persisted plan is malformed or names an unrunnable target/kind."""


def _clamp_steps(value: Any) -> int:
    return max(1, int(value or DEFAULT_MAX_STEPS_PER_TICK))


@dataclass(frozen=True)
class BrowsePlan:
    """A persisted scheduled-actuator plan. The ``cursor`` is the whole of its progress.

    ``submits`` declares whether the plan's flow can perform a SUBMIT; it decides the autonomy
    floor and is a property of the plan the AUTHOR states, not something inferred from a run —
    a plan that only reads must not silently earn a submit's caution, and a plan that submits
    must not escape ``draft_only`` by never having reached the submit step yet.
    """

    id: str
    goal: str
    kind: str
    start_url: str
    target: str = TARGET_GATEWAY
    submits: bool = False
    max_steps_per_tick: int = DEFAULT_MAX_STEPS_PER_TICK
    cursor: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def floor(self) -> str:
        """The earned-autonomy rung this plan sits at with no grant.

        ``draft_only`` for a SUBMIT-bearing plan (it may not run unattended until promoted);
        ``one_tap`` for a read-only plan, which may then graduate up the ladder on evidence.
        """
        return RUNG_DRAFT_ONLY if self.submits else RUNG_ONE_TAP

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "kind": self.kind,
            "start_url": self.start_url,
            "target": self.target,
            "submits": self.submits,
            "max_steps_per_tick": self.max_steps_per_tick,
            "cursor": dict(self.cursor),
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BrowsePlan":
        return cls(
            id=str(data.get("id", "")),
            goal=str(data.get("goal", "")),
            kind=str(data.get("kind", "")),
            start_url=str(data.get("start_url", "")),
            target=str(data.get("target") or TARGET_GATEWAY),
            submits=bool(data.get("submits", False)),
            max_steps_per_tick=_clamp_steps(data.get("max_steps_per_tick")),
            cursor=dict(data.get("cursor") or {}),
            notes=tuple(data.get("notes") or ()),
        )


def plans_dir() -> Path:
    """``$PERSONALCLAW_HOME/browse/plans`` — the persisted-plan store.

    ``config_dir()`` is imported and called INSIDE the function, never bound at import time,
    so a test that patches ``config_dir`` (or sets ``PERSONALCLAW_HOME``) writes into its
    isolated home rather than the operator's real one — the same rule the profile store follows.
    """
    from personalclaw.config import config_dir

    d = Path(config_dir()) / "browse" / "plans"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _plan_path(plan_id: str) -> Path:
    return record_path(plans_dir(), plan_id, kind="plan_id")


def validate_plan(plan: BrowsePlan) -> None:
    """Raise :class:`PlanError` if the plan cannot legally run as a scheduled actuator.

    The ``user_browser`` refusal is the load-bearing one (§(d) rung cap): a persisted plan is
    executed by an unattended tick, and ``user_browser`` sits at a floor no evidence promotes,
    so such a plan could never run — surfacing that at registration, not at every silent tick.
    """
    if not plan.id:
        raise PlanError("plan needs a non-empty id")
    if plan.kind not in PLAN_KINDS:
        raise PlanError(f"unknown plan kind {plan.kind!r} (expected one of {PLAN_KINDS})")
    if not plan.goal or not plan.start_url:
        raise PlanError("plan needs both a goal and a start_url")
    if not permits_unattended(plan.target):
        raise PlanError(
            f"a scheduled browse plan cannot name target {plan.target!r}: it never permits "
            "unattended execution, so a persisted plan could not run — drive it interactively "
            "through the browse action instead"
        )


def save_plan(plan: BrowsePlan) -> Path:
    """Validate and persist a plan atomically. Registration is where an illegal plan is caught."""
    validate_plan(plan)
    path = _plan_path(plan.id)
    atomic_write(path, json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n")
    return path


def load_plan(plan_id: str) -> BrowsePlan | None:
    path = _plan_path(plan_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return BrowsePlan.from_dict(data) if isinstance(data, dict) else None


def list_plans() -> list[BrowsePlan]:
    out: list[BrowsePlan] = []
    for p in sorted(plans_dir().glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            out.append(BrowsePlan.from_dict(data))
    return out


def plan_from_config(action_config: dict[str, Any]) -> BrowsePlan:
    """Build (unsaved) a plan from a browse action config, resolving the target the same way
    the browse action does so a plan and a one-off browse agree on which browser they mean."""
    target = resolve_target({TARGET_KEY: action_config.get(TARGET_KEY)})
    return BrowsePlan(
        id=str(action_config.get("id", "")),
        goal=str(action_config.get("goal", "")),
        kind=str(action_config.get("kind", "")),
        start_url=str(action_config.get("start_url", "")),
        target=target,
        submits=bool(action_config.get("submits", False)),
        max_steps_per_tick=_clamp_steps(action_config.get("max_steps_per_tick")),
        cursor=dict(action_config.get("cursor") or {}),
        notes=tuple(action_config.get("notes") or ()),
    )


# ── one tick ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TickOutcome:
    """What ONE browse execution produced — the injected runner's return.

    ``content`` is the extracted page text a ``watch_page`` tick hashes and diffs. ``html`` is
    the raw rendered markup behind that text, carried for a caller that must run its OWN
    extraction on the DOM (the WATCHED-SOURCES browse tier runs detectors on it); it never
    feeds the change hash, which stays the stable text. ``verified`` is whether a ``walk_flow``
    step's success was confirmed (a step that acted but did not verify does NOT advance the
    cursor). ``submitted`` records a SUBMIT actually happened, so a tick can refuse to persist a
    submit that ran below the plan's floor.
    """

    content: str = ""
    html: str = ""
    ok: bool = False
    verified: bool = False
    submitted: bool = False
    final_url: str = ""
    note: str = ""


#: The one thing a tick needs from the outside world: run this plan from its cursor and report
#: the outcome. Injected so the idempotency/cursor logic is unit-testable without a live browser;
#: production wires it to the ``browse`` action provider (a gateway CDP session + the loop).
TickRunner = Callable[[BrowsePlan], Awaitable[TickOutcome]]


@dataclass(frozen=True)
class TickResult:
    """The outcome of one scheduled tick against a plan.

    ``content``/``html`` carry the rendered bytes up from the runner so a caller that escalated
    to a browse tick (the WATCHED-SOURCES tier) can run its own extraction on the DOM; both are
    empty for a ``walk_flow`` tick, which produces no page to hand back.
    """

    plan_id: str
    ok: bool
    changed: bool = False  # watch_page: content differed from the cursor
    advanced: bool = False  # walk_flow: a verified step moved the cursor forward
    cursor: dict[str, Any] = field(default_factory=dict)
    content: str = ""  # watch_page: the extracted text this tick rendered
    html: str = ""  # watch_page: the raw markup behind that text, for a caller's own detectors
    note: str = ""
    refused: bool = False  # the plan's floor forbids this unattended run


async def execute_tick(
    plan: BrowsePlan,
    *,
    run: TickRunner,
    unattended: bool = True,
    granted_rung: str = "",
    now: float | None = None,
) -> TickResult:
    """Run ONE idempotent tick against ``plan`` and persist the advanced cursor.

    Idempotency is the whole point: for ``watch_page`` the cursor is the content hash, so a
    tick that sees unchanged content reports ``changed=False`` and rewrites the same cursor;
    for ``walk_flow`` the cursor advances ONLY when the step verified, so a re-fire before a
    verified success repeats the same step rather than skipping it. Either way the cursor is
    written AFTER the browser work, so a crash mid-tick loses at most the step just taken.

    The autonomy floor is enforced BEFORE the runner is called: an unattended tick on a plan
    whose floor is ``draft_only`` (a SUBMIT-bearing plan) with no promoting grant is refused
    without touching the browser, so a scheduled run can never perform an ungoverned submit.
    """
    from personalclaw.guardrails.autonomy import rung_rank

    ts = time.time() if now is None else now
    if unattended and rung_rank(granted_rung) < rung_rank(plan.floor()):
        return TickResult(
            plan_id=plan.id,
            ok=False,
            refused=True,
            cursor=dict(plan.cursor),
            note=(
                f"plan {plan.id} sits at floor {plan.floor()!r} and cannot run unattended "
                f"at granted rung {granted_rung or '(none)'!r}"
            ),
        )

    outcome = await run(plan)

    if plan.kind == KIND_WATCH_PAGE:
        new_hash = sha256(outcome.content.encode("utf-8")).hexdigest()
        changed = new_hash != plan.cursor.get("content_hash")
        new_cursor = {**plan.cursor, "content_hash": new_hash, "last_tick": ts}
        _persist_cursor(plan, new_cursor)
        return TickResult(
            plan_id=plan.id,
            ok=outcome.ok,
            changed=changed,
            cursor=new_cursor,
            content=outcome.content,
            html=outcome.html,
            note=outcome.note or ("content changed" if changed else "no change"),
        )

    # walk_flow: advance the step index only on a verified success.
    advanced = bool(outcome.ok and outcome.verified)
    step = int(plan.cursor.get("step", 0))
    new_cursor = {**plan.cursor, "last_tick": ts}
    if advanced:
        new_cursor["step"] = step + 1
    _persist_cursor(plan, new_cursor)
    return TickResult(
        plan_id=plan.id,
        ok=outcome.ok,
        advanced=advanced,
        cursor=new_cursor,
        note=outcome.note or (f"step {step}→{step + 1}" if advanced else f"step {step} unverified"),
    )


def _persist_cursor(plan: BrowsePlan, cursor: dict[str, Any]) -> None:
    """Write the plan back with an advanced cursor. A plan that vanished mid-run (deleted by
    the operator) is left deleted rather than resurrected — the cursor write is not a create."""
    path = _plan_path(plan.id)
    if not path.exists():
        return
    updated = BrowsePlan(
        id=plan.id,
        goal=plan.goal,
        kind=plan.kind,
        start_url=plan.start_url,
        target=plan.target,
        submits=plan.submits,
        max_steps_per_tick=plan.max_steps_per_tick,
        cursor=cursor,
        notes=plan.notes,
    )
    atomic_write(path, json.dumps(updated.to_dict(), indent=2, sort_keys=True) + "\n")
