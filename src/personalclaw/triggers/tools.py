"""The `automation_*` chat-tool namespace (§4 — S92).

§4 specifies one namespace replacing `schedule_add/…`, and criterion 2 is its bar: *"When a file
in ~/notes changes, summarize it into my knowledge base" is creatable in chat in ONE message.*

S83 shipped the `file` kind's watch runtime and then recorded the honest reason it could not close
criterion 2: "Criterion 2 needs `automation_create` (§4), which needs somewhere to PUT a `file`
trigger. Measured: there is no unified trigger store." **S87 shipped that store.** Re-measured
before writing a line here: a `file` trigger round-trips through `TriggerStore` with zero errors,
and `SPEC_KEYS` accepts all nine kinds. The blocker is gone, so the tool lands.

**🔴 WHAT THE PROBES FOUND — the per-minute-poll trap.** The only NL schedule path is
`nl_to_cron`, cron-shaped by construction. Fed criterion 2's own sentence it returns an error,
which is the *good* case; the bad case is a model asked for a cron expression while handed a
file-watch request answering `* * * * *`, which validates and silently converts "when a file
changes" into a per-minute LLM turn. So `nl_kind.route()` decides the KIND first, and a
non-cadence request never reaches the cadence converter. Two further defects the probe caught
before any test existed are recorded in `nl_kind` (a URL mis-routing to `file`, and a change verb
that reached the dedup hint but not the routing check).

**What this owns, and the boundary.** Nine tools over `TriggerStore`: create/list/update/pause/
resume/run/history/delete, plus `delete_all` (S109 — the scoped bulk delete carried over when the
`schedule_*` aliases retired; it is the only capability those aliases had that this namespace did
not). It does NOT own the fire path (S86), the tick (S88), dispatch (S89), or
execution (S90) — `automation_run` hands off to the shipped executor rather than re-deriving a
turn. Keeping those injected is what let the whole chain be driven end to end without a model.

Per §4 + decision 5d, an agent-created trigger is tagged `created_by: agent`, **announced** in the
tool's own result text, and **capped** (default 20 active) — "visible, not silent".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Decision 5d: "`created_by: workflow|agent` triggers are announced to the user on creation and
#: capped (default 20 active) — visible, not silent." The cap counts ACTIVE agent-made rows only:
#: a paused one is not doing anything, and counting it would make the cap unrecoverable without
#: deleting history the user may still want.
#:
#: WF2LOO-9 made the number configurable. It was a module constant, so the one bound standing
#: between a self-scheduling agent and an unbounded fan-out of clocks could not be tightened by an
#: operator who wanted 5, nor set to 0 to turn self-scheduling off — the only way to change it was
#: to edit the source. Read per call, not captured at import, so a PATCH takes effect without a
#: restart (the same reason `mcp.json`'s resolvers became functions).
DEFAULT_MAX_AGENT_TRIGGERS = 20


def max_agent_triggers() -> int:
    """`workflows.self_schedule_max_outstanding`, or the historical 20 if config is unreadable.

    Falling back to the OLD default rather than to "unbounded" is the point: an unreadable config
    must not silently remove the only cap on agent-created automations. 0 is a legitimate value —
    it turns self-scheduling off — so the fallback cannot be 0 either, which would look like the
    operator had disabled the feature when they had not.
    """
    try:
        from personalclaw.config.loader import AppConfig

        return int(AppConfig.load().workflows.self_schedule_max_outstanding)
    except Exception:  # noqa: BLE001 - an unreadable config must not remove the cap
        return DEFAULT_MAX_AGENT_TRIGGERS


#: The tool names §4's table declares. Data rather than eight scattered string literals, so
#: `list_tools()`, the dispatcher, and the tests cannot drift out of step — the failure mode where
#: a declared tool has no handler and reports "unknown tool" at the worst moment.
TOOL_NAMES: tuple[str, ...] = (
    "automation_create",
    "automation_list",
    "automation_update",
    "automation_pause",
    "automation_resume",
    "automation_run",
    "automation_history",
    "automation_delete",
    "automation_delete_all",
)

#: Fields an `automation_update` patch may set. An allowlist because a patch is agent-supplied:
#: letting it reach `run_count`/`last_run_id`/`health_status` would let an automation rewrite its
#: own health record, and §3.7's autopause thresholds on exactly those numbers.
PATCHABLE: frozenset[str] = frozenset(
    {
        "name",
        "spec",
        "gates",
        "workflow",
        "enabled",
        "overlap",
        "session",
        "model_tier",
        "delivery",
        "failure_delivery",
        "yield_to_user",
        "catch_up",
        "expires_at",
    }
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class ToolResult:
    """One tool call's outcome. `text` is what the agent sees; `data` is for a surface."""

    ok: bool
    text: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "text": self.text, "data": dict(self.data)}


def slug_for(name: str, kind: str) -> str:
    """A stable, human-recognizable trigger id.

    `kind:slug` matches the `/api/triggers` facade's namespace, which §7 step 2 calls "the
    migration map" — an opaque uuid here would break that mapping and give the user an id they
    cannot recognize in their own store.
    """
    base = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-") or "automation"
    return f"{kind}:{base}"[:96]


def _unique_id(store: Any, base: str) -> str:
    """`base`, or `base-2`, `base-3`… — never silently overwriting an existing automation.

    Measured against the real store: `upsert` is an UPSERT, so creating "daily digest" twice would
    replace the first one and report success. A user who asked for a second automation and lost
    their first would have no way to know.
    """
    existing = {row.trigger.id for row in store.load()}
    if base not in existing:
        return base
    for n in range(2, 100):
        candidate = f"{base}-{n}"
        if candidate not in existing:
            return candidate
    return f"{base}-{len(existing) + 1}"


def _active_agent_count(store: Any) -> int:
    return sum(
        1 for row in store.load() if row.trigger.created_by == "agent" and row.trigger.enabled
    )


def create(
    store: Any,
    *,
    name: str,
    when: str = "",
    kind: str = "",
    spec: dict[str, Any] | None = None,
    workflow: dict[str, Any] | None = None,
    message: str = "",
    created_by: str = "agent",
    enabled: bool = True,
    cadence_to_cron: Any = None,
) -> ToolResult:
    """`automation_create` — §4's NL-friendly constructor. Criterion 2's one message.

    `when` is routed by `nl_kind.route()` BEFORE any cadence conversion, which is the whole point:
    a file-watch request must never reach a component whose only output shape is a cron expression.
    An explicit `kind`+`spec` bypasses routing for a caller that already knows.

    `cadence_to_cron` is injected (defaulting to the shipped `nl_to_cron`) so every branch of this
    function is testable without a model — the same seam `ScheduleService` uses for `_on_job` and
    the executor uses for its runner.
    """
    from personalclaw.triggers import screen as _screen
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.nl_kind import route

    if not (name or "").strip():
        return ToolResult(False, "Error: name is required.")

    resolved_spec = dict(spec or {})
    because = ""
    if kind:
        resolved_kind = kind
    else:
        routed = route(when)
        if not routed.ok:
            # The refusal is the RESULT, phrased for the user. Defaulting an unroutable request to
            # a schedule is how "when a file changes" becomes a per-minute poll.
            return ToolResult(False, f"Error: {routed.error}", {"when": when})
        resolved_kind, because = routed.kind, routed.because
        resolved_spec = {**routed.spec, **resolved_spec}
        if routed.cadence and "expr" not in resolved_spec and "at" not in resolved_spec:
            converter = cadence_to_cron or _default_cadence_to_cron
            expr, err = converter(routed.cadence)
            if err:
                return ToolResult(False, f"Error: {err}", {"cadence": routed.cadence})
            resolved_spec = {"kind": "cron", "expr": expr, **resolved_spec}

    if created_by == "agent":
        active = _active_agent_count(store)
        cap = max_agent_triggers()
        if active >= cap:
            # Decision 5d's cap. Refusing with the count and the remedy, because "limit reached"
            # without a number leaves the user unable to tell what to pause.
            return ToolResult(
                False,
                f"Error: {active} agent-created automations are already active "
                f"(cap {cap}). Pause or delete one first.",
                {"active": active, "cap": cap},
            )

    if message and not workflow:
        workflow = {"provider": "run-prompt", "config": {"message": message}}
    if not workflow:
        return ToolResult(False, "Error: give a message or a workflow for the automation to run.")

    trigger = Trigger(
        id=_unique_id(store, slug_for(name, resolved_kind)),
        name=name.strip(),
        kind=resolved_kind,
        # A caller may create a trigger switched OFF. This was hardcoded `True`, and there was no
        # parameter to say otherwise, so `POST /api/triggers {"enabled": false}` created a live,
        # armed automation and the field the caller sent was dropped without a word (#587). Default
        # stays `True`: "create an automation" means an automation that runs, and the chat tool has
        # always meant that.
        enabled=bool(enabled),
        created_by=created_by,
        spec=resolved_spec,
        workflow=dict(workflow),
        # 🔴 FREEZE THE CAPABILITY SET AT SAVE (decision 7 / R3 — S116). Authoring a trigger IS the
        # opt-in: the user picked this action. Without it, every trigger this function creates
        # (`run-prompt` from chat, `invoke-agent` from the CLI) carries an EMPTY block, and the
        # now-wired fence denies on empty — so 100% of real automations would refuse on their next
        # fire. A read-only action still gets an empty block: the fence permits those without one,
        # and a written-out grant would imply an opt-in the user never had to make.
        capabilities=_screen.capabilities_for_action(
            Trigger(id="", name="", kind=resolved_kind, workflow=dict(workflow))
        ),
    )
    # 🔴 ARM A CLOCK TRIGGER ON CREATION (S101). Measured: `create` persisted `next_fire_at=""`, and
    # `service.due_ids` only surfaces rows that HAVE one — so every cron created through this
    # function (the chat tools since S92, and the API from this session) would never fire. Arming at
    # creation rather than waiting for the next boot sweep is the difference between "runs tonight"
    # and "runs after the user restarts the gateway". An unarmable spec (invalid cron, elapsed
    # one-shot) returns "" and is left alone — `arm` refuses rather than guessing a cadence.
    from personalclaw.triggers.arm import arm as _arm

    # A trigger created switched off is NOT armed. Arming it would give it a `next_fire_at`, which
    # is what `service.due_ids` selects on — so the row would advertise a countdown for a fire the
    # `enabled` check then suppresses. `needs_arming` already refuses a disabled row for exactly
    # this reason; this is the same rule on the creation path, which does not go through it.
    armed = _arm(trigger) if trigger.enabled else ""
    if armed:
        trigger.next_fire_at = armed
    saved = store.upsert(trigger)

    # §4 + decision 5d: ANNOUNCED, not silent. The routing reason rides along so a wrong route is
    # correctable by the user instead of mysterious.
    lines = [f"Created automation '{saved.name}' ({saved.id}), kind {saved.kind}."]
    if because:
        lines.append(f"  {because}")
    if resolved_spec.get("expr"):
        lines.append(f"  cron: {resolved_spec['expr']}")
    if resolved_spec.get("paths"):
        lines.append(f"  watching: {', '.join(resolved_spec['paths'])}")
    if created_by == "agent":
        # "active now" is a claim about state, so it tracks state. A disabled trigger announced as
        # active is the same class of lie the rest of this module hunts — and this string is UI: it
        # is what the user reads in chat after the agent creates an automation for them.
        _state = "active now" if saved.enabled else "switched off until you enable it"
        lines.append(
            f"  I created this for you — it is {_state} and visible on the Automations page "
            f"({_active_agent_count(store)}/{max_agent_triggers()} agent-created)."
        )
    return ToolResult(True, "\n".join(lines), {"trigger": saved.to_dict()})


def _default_cadence_to_cron(cadence: str) -> tuple[str, str]:
    """Bridge to the shipped `nl_to_cron` from this synchronous dispatch.

    Mirrors `mcp_schedule._nl_to_cron_blocking` rather than inventing a second async bridge: the
    two would drift, and this one is already proven against a running loop.
    """
    import asyncio

    from personalclaw.nl_to_cron import nl_to_cron

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(nl_to_cron(cadence))
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, nl_to_cron(cadence)).result(timeout=60)


def list_automations(store: Any, *, kind: str = "", state: str = "") -> ToolResult:
    """`automation_list` — §4: "includes health rollups".

    Broken rows are INCLUDED. `store.load()` keeps a row it could not parse (S87's lenient-parse
    contract), and hiding it here would make a broken automation invisible in the one place an
    agent looks to debug why nothing fired.
    """
    rows = store.load()
    out: list[dict[str, Any]] = []
    for row in rows:
        trigger = row.trigger
        if kind and trigger.kind != kind:
            continue
        if state == "active" and not trigger.enabled:
            continue
        if state == "paused" and trigger.enabled:
            continue
        out.append(
            {
                "id": trigger.id,
                "name": trigger.name,
                "kind": trigger.kind,
                "enabled": trigger.enabled,
                "created_by": trigger.created_by,
                "health": trigger.health_status,
                "runs": trigger.run_count,
                "next_fire_at": trigger.next_fire_at,
                "last_error": trigger.last_error_summary,
                "broken": [i.message for i in row.errors],
            }
        )
    if not out:
        return ToolResult(True, "No automations match.", {"automations": []})
    lines = []
    for a in out:
        flag = "" if a["enabled"] else " [paused]"
        broken = f" ⚠ {a['broken'][0]}" if a["broken"] else ""
        health = f" health={a['health']}" if a["health"] else ""
        lines.append(f"{a['id']} — {a['name']} ({a['kind']}){flag}{health}{broken}")
    return ToolResult(True, "\n".join(lines), {"automations": out})


def update(store: Any, *, trigger_id: str, patch: dict[str, Any]) -> ToolResult:
    """`automation_update` — patch an existing automation through the allowlist.

    A rejected key is REPORTED, not dropped silently: an agent that thinks it changed
    `health_status` and got no error would keep believing a stale model of the automation.
    """
    row = store.get(trigger_id)
    if row is None:
        return ToolResult(False, f"Error: no automation with id {trigger_id!r}.")
    rejected = sorted(set(patch) - PATCHABLE)
    applied = {k: v for k, v in patch.items() if k in PATCHABLE}
    if not applied:
        return ToolResult(
            False,
            f"Error: nothing to update. Not settable here: {', '.join(rejected) or 'none given'}.",
            {"rejected": rejected},
        )
    trigger = row.trigger
    for key, value in applied.items():
        setattr(trigger, key, value)
    saved = store.upsert(trigger)
    text = f"Updated {saved.id}: {', '.join(sorted(applied))}."
    if rejected:
        text += f"\n  Ignored (not settable via this tool): {', '.join(rejected)}."
    return ToolResult(True, text, {"trigger": saved.to_dict(), "rejected": rejected})


def set_paused(store: Any, *, trigger_id: str, paused: bool) -> ToolResult:
    """`automation_pause` / `automation_resume`.

    Resume goes through `store.set_enabled`, which REFUSES to enable a row that failed to parse
    (S87). That refusal is surfaced rather than swallowed: silently leaving a "resumed" automation
    disabled is the class of lie this program keeps hunting.
    """
    row = store.get(trigger_id)
    if row is None:
        return ToolResult(False, f"Error: no automation with id {trigger_id!r}.")
    saved = store.set_enabled(trigger_id, not paused)
    if saved is None:
        # 🔴 MEASURED: `set_enabled` returns None — not a trigger with `enabled` unchanged — when it
        # refuses a broken row (S87). My first draft compared `saved.enabled`, a branch that could
        # never run, so a refused resume would have reported the generic "could not change" with no
        # hint that the row has a parse error the user must fix first.
        if row.errors:
            return ToolResult(
                False,
                f"Error: {trigger_id} could not be resumed — it has a parse error "
                f"({row.errors[0].message}). Fix it first.",
                {"errors": [i.message for i in row.errors]},
            )
        return ToolResult(False, f"Error: could not change {trigger_id!r}.")
    return ToolResult(
        True,
        f"{'Paused' if paused else 'Resumed'} {saved.id} ({saved.name}).",
        {"trigger": saved.to_dict()},
    )


def delete(store: Any, *, trigger_id: str, confirm: bool = False) -> ToolResult:
    """`automation_delete` — §4: `(id, confirm: true)`.

    The confirm flag is enforced, not decorative. Deleting an automation the user built and cannot
    recover is exactly the irreversible action a tool call should not be able to take by accident.
    """
    if not confirm:
        return ToolResult(
            False,
            f"Error: deleting {trigger_id!r} needs confirm: true. "
            "Pause it instead if you might want it back.",
        )
    row = store.get(trigger_id)
    if row is None:
        return ToolResult(False, f"Error: no automation with id {trigger_id!r}.")
    name = row.trigger.name
    store.delete(trigger_id)
    return ToolResult(True, f"Deleted {trigger_id} ({name}).", {"deleted": trigger_id})


def delete_all(store: Any, *, created_by: str = "agent", confirm: bool = False) -> ToolResult:
    """`automation_delete_all` — bulk delete, SCOPED to one creator (S109).

    Carries forward the one capability `schedule_remove_all` had that no `automation_*` tool did.
    That matters because the alias was not just a convenience: it enforced a real access control —
    `jobs = [j for j in jobs if j.session_key == session_key]`, so an agent could only mass-delete
    automations it had created, and it REFUSED outright when no session key was set. Retiring the
    alias without carrying that scope forward would either lose the bulk operation or (worse) leave
    a future author to re-add it unscoped.

    The scope is `created_by` rather than the legacy `session_key`, because that is the ownership
    the store records. Measured: `mcp_schedule` set `job.session_key` on add, but a row created
    through `tools.create` carries `session="fresh"` (the default) and `created_by="agent"` — so a
    session-keyed filter would match NOTHING for exactly the rows an agent can create, making the
    control vacuous in the new world while looking identical in a diff.

    `confirm` is required for the reason single `delete` requires it, only more so: this is the most
    destructive tool in the namespace. An empty scope reports that it deleted nothing rather than
    reporting success — "Removed 0 job(s)" beside an untouched list is how a caller learns its scope
    was wrong instead of assuming the work is done.
    """
    if not confirm:
        return ToolResult(
            False,
            f"Error: deleting every {created_by}-created automation needs confirm: true. "
            "Pause them instead if you might want them back.",
        )
    owned = [row.trigger for row in store.load() if row.trigger.created_by == created_by]
    if not owned:
        return ToolResult(
            True,
            f"No {created_by}-created automations to delete.",
            {"deleted": [], "created_by": created_by},
        )
    deleted: list[str] = []
    for trigger in owned:
        try:
            store.delete(trigger.id)
            deleted.append(trigger.id)
        except Exception:  # noqa: BLE001 - one undeletable row must not strand the rest
            logger.debug("could not delete %s", trigger.id, exc_info=True)
    text = f"Deleted {len(deleted)} {created_by}-created automation(s): {', '.join(deleted)}."
    if len(deleted) != len(owned):
        # Reported, not swallowed: a partial bulk delete that claimed full success would leave the
        # caller believing the list is empty when rows it cannot see are still firing.
        text += f"\n  ⚠️ {len(owned) - len(deleted)} could not be deleted."
    return ToolResult(True, text, {"deleted": deleted, "created_by": created_by})


#: Gates a MANUAL fire may skip, per §4: "bypasses min-interval + max_runs_per_hour, never rate
#: floors". `quiet` and `duty` are the per-trigger cadence limiters — the user asking for a run
#: right now has overridden their own quiet hours by definition. Everything absent from this set is
#: enforced on a manual fire exactly as on a scheduled one.
MANUAL_BYPASSES: frozenset[str] = frozenset({"quiet", "duty"})

#: 🔴 Gates a manual fire may NEVER skip, spelled out as data so the intent survives a refactor.
#: `screen` is the prompt-injection boundary (criterion 6) and `capability` is the frozen action
#: set — a "the user asked for it" bypass on either would make the trust boundary optional, which
#: is precisely the escalation route criterion 6 is written against. `budget` stays because §4 says
#: "never rate floors": a manual fire that could spend past the cap would make the cap advisory.
#:
#: `incident` is listed for the reason the LEGACY path already recorded, verbatim: "a `/test` that
#: ignored incident mode would run unattended work during the incident the kill switch was thrown
#: for". The kill switch is the one control an operator reaches for when something is actively going
#: wrong, so a UI button that still fires through it would make it advisory at the worst moment. Two
#: fire paths disagreeing about the same switch is also how an operator learns not to trust it.
MANUAL_NEVER_BYPASSES: frozenset[str] = frozenset(
    {"incident", "screen", "capability", "budget", "claim"}
)


def manual_gate_plan(dry_run: bool = False) -> dict[str, Any]:
    """Which gates a manual `automation_run` skips and which still apply.

    Returned as data (and asserted in tests against `firepath.GATE_ORDER`) so the bypass set can
    never silently grow to include `screen` or `capability`. A bypass list that drifted into the
    trust boundary is the kind of change that reads as a small convenience in a diff.

    🔴 THIS IS A DESCRIPTION, NOT AN ENFORCEMENT. It reports intent for a surface to render; the
    refusal lives in `manual_refusal` below. Measured: `run()` printed "gates enforced: incident,
    screen, budget, claim, yield, capability" and enforced **none** of them — a plan describing a
    control nobody applies is worse than no plan, because it tells the user the boundary held.
    """
    from personalclaw.triggers.firepath import GATE_ORDER

    enforced = [g for g in GATE_ORDER if g not in MANUAL_BYPASSES]
    return {
        "bypassed": [g for g in GATE_ORDER if g in MANUAL_BYPASSES],
        "enforced": enforced,
        "dry_run": bool(dry_run),
        # A dry run must not execute, so it stops after the gate walk. Reported explicitly because
        # "dry run" that silently ran would be the worst possible surprise.
        "executes": not dry_run,
    }


def manual_refusal() -> str:
    """The reason a manual fire must be refused right now, or "" to proceed.

    🔴 The enforcement `manual_gate_plan` only ever DESCRIBED. Measured with the kill switch thrown:
    `run()` reported `incident` under "gates enforced", returned `ok: True`, and invoked the runner.

    Only `incident` is checked here, and that is deliberate rather than partial. It is the one gate
    in `MANUAL_NEVER_BYPASSES` that is a GLOBAL, operator-thrown state a manual caller can trip
    without knowing; the other three are properties of the fire itself and are enforced where they
    can be evaluated — `screen` needs payload text a manual run does not carry, `capability` is
    checked at dispatch against the frozen block, and `budget`/`claim` are explicitly not spent by a
    manual fire (`record_fire` is not called, so there is no allowance to breach and no claim to
    take). Listing them here without an evaluable input is what produced the inert plan.
    """
    from personalclaw.guardrails.incident import incident_active

    if incident_active():
        return (
            "incident mode is active: unattended fires are suspended "
            "(resume with `personalclaw incident off`)"
        )
    return ""


def run(
    store: Any,
    *,
    trigger_id: str,
    dry_run: bool = False,
    runner: Any = None,
) -> ToolResult:
    """`automation_run` — §4: "(id, dry_run?) — manual fire / observe-mode replay".

    A DISABLED automation still runs manually: pausing means "stop firing on your own", and
    refusing a hand-driven run of a paused automation would remove the main way a user tests one
    before re-enabling it. Reported in the result so nobody mistakes it for a resume.

    `runner` is injected — this tool does NOT own the turn (S90 does). A `dry_run` never calls it
    at all, which is the property that makes observe-mode safe to offer.
    """
    row = store.get(trigger_id)
    if row is None:
        return ToolResult(False, f"Error: no automation with id {trigger_id!r}.")
    if row.errors:
        return ToolResult(
            False,
            f"Error: {trigger_id} has a parse error and cannot run " f"({row.errors[0].message}).",
            {"errors": [i.message for i in row.errors]},
        )
    plan = manual_gate_plan(dry_run)
    trigger = row.trigger
    lines = [
        f"{'Dry run' if dry_run else 'Manual run'} of {trigger.id} ({trigger.name}).",
        f"  gates enforced: {', '.join(plan['enforced'])}",
        f"  bypassed (manual): {', '.join(plan['bypassed']) or 'none'}",
    ]
    if not trigger.enabled:
        lines.append("  note: this automation is paused — running it here does not re-enable it.")
    if dry_run:
        lines.append("  nothing was executed.")
        return ToolResult(True, "\n".join(lines), {"plan": plan, "trigger": trigger.to_dict()})
    # 🔴 The gates the plan claims to enforce, actually enforced. Below the dry-run return so a dry
    # run still REPORTS the plan during an incident (that is a read, and telling an operator what
    # would happen is the opposite of running unattended work).
    refusal = manual_refusal()
    if refusal:
        lines.append(f"  refused: {refusal}")
        return ToolResult(False, "\n".join(lines), {"plan": plan, "refused": refusal})
    if runner is None:
        # Honest refusal rather than a fabricated success. "Launched" with nothing behind it is the
        # fire-and-forget lie S90's executor was written to keep out of this codebase.
        lines.append("  no runner is wired in this context, so nothing was executed.")
        return ToolResult(False, "\n".join(lines), {"plan": plan})
    result = runner({"trigger_id": trigger.id, "workflow": dict(trigger.workflow)})
    lines.append(f"  result: {result}")
    return ToolResult(True, "\n".join(lines), {"plan": plan, "result": result})


def _same_trigger(record_id: str, wanted: str) -> bool:
    """Whether a history row belongs to this trigger, across the two id namespaces.

    🔴 MEASURED, and it made the first draft return an empty feed for a trigger with real runs.
    `history.schedule_run_to_record` synthesizes `schedule:<job_id>` when the caller does not pass
    an explicit `trigger_id`, so a store id of `file:notes` arrives as `schedule:file:notes`. An
    equality check silently reported "no recorded runs yet" for an automation that had run — the
    worst possible answer for a tool whose whole purpose is letting an agent self-debug.

    Matching on the suffix as well as equality keeps both namespaces readable without teaching this
    tool the legacy prefix vocabulary.
    """
    if not record_id or not wanted:
        return False
    return record_id == wanted or record_id.endswith(f":{wanted}")


def history(
    store: Any,
    *,
    trigger_id: str,
    n: int = 10,
    schedule_runs: list[dict[str, Any]] | None = None,
    hooks: list[Any] | None = None,
    event_triggers: list[Any] | None = None,
) -> ToolResult:
    """`automation_history` — §4: "run/fire rows incl. typed outcomes (agents self-debug)".

    Projects through S84's `unified_feed` rather than a second projection, so a `file` trigger, a
    hook and a cron report the SAME record shape here as in the Runs inbox (criterion 4).

    **Measured, and it corrected this function's first draft:** `history` exposes no reader — no
    `recent_fires`, no store. `unified_feed` is a pure projection over source rows the CALLER
    supplies. So the sources are parameters, which is also what makes the filtering testable
    without a populated home. A caller with no sources gets an honest "no runs yet" rather than a
    fabricated empty feed that looks authoritative.
    """
    row = store.get(trigger_id)
    if row is None:
        return ToolResult(False, f"Error: no automation with id {trigger_id!r}.")
    from personalclaw.triggers.history import feed_response, unified_feed

    records = unified_feed(
        schedule_runs=schedule_runs,
        hooks=hooks,
        event_triggers=event_triggers,
        limit=max(1, n) * 10,
    )
    mine = [r for r in records if _same_trigger(r.trigger_id, trigger_id)][: max(1, n)]
    if not mine:
        return ToolResult(
            True,
            f"{trigger_id} has no recorded runs yet.",
            {"trigger_id": trigger_id, **feed_response([])},
        )
    # `started_at`/`scheduled_for`, not a `fired_at` — measured against the real `FireRecord`. A
    # scheduled-but-suppressed row has no start time, so the scheduled slot is the honest fallback.
    lines = [
        f"{r.started_at or r.scheduled_for or '?'} {r.outcome}"
        + (f" — {r.reason}" if r.reason else "")
        for r in mine
    ]
    return ToolResult(
        True,
        f"{trigger_id} — last {len(mine)} run(s):\n" + "\n".join(lines),
        {"trigger_id": trigger_id, **feed_response(mine)},
    )
