"""The `automation_*` chat-tool namespace.

§4 specifies one namespace replacing `schedule_add/…`, and criterion 2 is its bar: *"When a file
in ~/notes changes, summarize it into my knowledge base" is creatable in chat in ONE message.*

S83 shipped the `file` kind's watch runtime and then recorded the honest reason it could not close
criterion 2: "Criterion 2 needs `automation_create`, which needs somewhere to PUT a `file`
trigger. There is no unified trigger store." **S87 shipped that store.** Re-measured
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
not). It does NOT own the fire path, the tick, dispatch, or
execution — `automation_run` hands off to the shipped executor rather than re-deriving a
turn. Keeping those injected is what let the whole chain be driven end to end without a model.

Per §4 + decision 5d, an agent-created trigger is tagged `created_by: agent`, **announced** in the
tool's own result text, and **capped** (default 20 active) — "visible, not silent".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from personalclaw.security import redact_for_display, redact_values_for_display

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

#: the mandatory-TTL bound: every AGENT-created trigger expires. A self-scheduling agent
#: that forgets a clock must not leave it running forever — the cap bounds how MANY exist at
#: once, and the TTL bounds how LONG a forgotten one keeps counting against that cap. One-shot
#: triggers default to a week (a follow-up that has not fired in 7 days is stale by any reading);
#: recurring ones to 30 days, matching the deliberate-renewal posture `scheduling.is_due` already
#: enforces for expiry. An explicit `ttl_secs` is honoured within [60s, 90d] — clamped, not
#: refused, because the clamp direction is always the safe one (a too-long TTL becomes the max).
AGENT_ONETIME_TTL_SECS = 7 * 86400
AGENT_RECURRING_TTL_SECS = 30 * 86400
MIN_AGENT_TTL_SECS = 60
MAX_AGENT_TTL_SECS = 90 * 86400

#: How long past its own time an unfired one-time task stays armed (the gateway was off when it was
#: due). Its default expiry covers its time plus this, so a one-time task set further out than the
#: one-shot TTL still fires: measured on `main`, "remind me on the 20th" made 23 days out expired on
#: day 7 and never ran.
ONE_TIME_LATE_SECS = 86400

#: What a `when` must read as when the tool carrying it says: `set_onetime_task` runs once and
#: `set_recurring_task` repeats. `automation_create` takes either, and passes "".
ONCE = "once"
RECURRING = "recurring"


def _agent_expiry(resolved_spec: dict, ttl_secs: float, *, now: float = 0.0) -> float:
    """The mandatory expiry for an agent-created trigger, as epoch seconds.

    `resolved_spec` decides the default: a spec carrying `at` is a one-shot and gets the short
    TTL — or, when its own time is further out than that, its time plus `ONE_TIME_LATE_SECS`, since
    a one-time task that expires before it runs is a reminder that silently never comes. Anything
    else (cron, watch kinds) gets the recurring TTL. Explicit `ttl_secs` wins, clamped to the sane
    window rather than refused — the caller asked for a bound and gets one (`create` refuses one
    that would end before the task's own time).
    """
    import time as _time

    base = float(now) if now else _time.time()
    if ttl_secs and ttl_secs > 0:
        return base + min(
            max(float(ttl_secs), float(MIN_AGENT_TTL_SECS)), float(MAX_AGENT_TTL_SECS)
        )
    if not resolved_spec.get("at"):
        return base + float(AGENT_RECURRING_TTL_SECS)
    try:
        at = float(resolved_spec.get("at") or 0)
    except (TypeError, ValueError):
        at = 0.0
    return max(base + float(AGENT_ONETIME_TTL_SECS), at + float(ONE_TIME_LATE_SECS))


def _iso(epoch: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _expiry_after_its_time(trigger: Any) -> None:
    """Move a one-shot's expiry past its time when an edit moved the time past the expiry.

    A one-time task that expires before its own time never runs, and says nothing — the rule
    `create` keeps for a new one (its time plus `ONE_TIME_LATE_SECS`). A task the chat made
    carries that expiry, so moving it to a later day on the Triggers page left it listed, armed and
    certain to expire first. The expiry moves only as far as that bound, never earlier.
    """
    from personalclaw.triggers.service import to_epoch

    spec = trigger.spec if isinstance(getattr(trigger, "spec", None), dict) else {}
    expires = str(getattr(trigger, "expires_at", "") or "")
    if str(spec.get("kind") or "") != "at" or not expires:
        return
    try:
        at = float(spec.get("at") or 0.0)
    except (TypeError, ValueError):
        return
    if at > 0 and to_epoch(expires) < at + ONE_TIME_LATE_SECS:
        trigger.expires_at = _iso(at + ONE_TIME_LATE_SECS)


@dataclass(frozen=True)
class _Timing:
    """What a clock `when` read as: the spec it becomes and the sentence saying so, or the error."""

    spec: dict[str, Any] = field(default_factory=dict)
    because: str = ""
    error: str = ""


def _read_when(text: str, *, recurrence: str, converter: Any, now: float = 0.0) -> _Timing:
    """A clock `when` → a one-time `at` spec or a cron spec — never a cron for one time.

    An explicit one time is read WITHOUT a model (`triggers.when`), in the owner's zone. Only a
    phrase that leaves the time to judgement reaches `converter` — `nl_to_cron`, which is handed
    the clock and the zone and answers either a cadence or `ONCE <time>`. A phrase the tool cannot
    carry is refused with the tool that can: a one-time task runs once, a recurring one repeats.
    """
    import time as _time

    from personalclaw.triggers import when as when_mod

    current = float(now) if now else _time.time()
    if not when_mod.is_recurring(text):
        one = when_mod.read_one_time(text, now=current)
        if one is not None:
            return _one_time(one, text=text, recurrence=recurrence, now=current)
    elif recurrence == ONCE:
        return _Timing(error=_repeats_but_once(text))

    answer = converter(text)
    if answer.error:
        return _Timing(error=answer.error)
    if answer.once:
        from personalclaw.timezones import resolve_zone

        one = when_mod.OneTime(at=answer.at, zone=answer.zone, tz=resolve_zone(answer.zone))
        return _one_time(one, text=text, recurrence=recurrence, now=current)
    if recurrence == ONCE:
        return _Timing(error=_repeats_but_once(text, expr=answer.expr))
    return _Timing(
        spec={"kind": "cron", "expr": answer.expr}, because="read as a repeating schedule"
    )


def _one_time(one: Any, *, text: str, recurrence: str, now: float) -> _Timing:
    """A one-time reading as an `at` spec — refused when the tool repeats or the time has passed."""
    from personalclaw.timezones import is_known_zone

    said = one.describe(now=now)
    if recurrence == RECURRING:
        return _Timing(
            error=(
                f"{text!r} is one time ({said}), and a recurring task repeats. Give how often it "
                "should run ('every day at 5pm'), or use set_onetime_task to run it once."
            )
        )
    if one.at <= now:
        return _Timing(error=f"{said} has already passed. Give a time that is still to come.")
    spec: dict[str, Any] = {"kind": "at", "at": one.at, "delete_after_run": False}
    try:
        if is_known_zone(one.zone):
            # Shown in the zone it was said in. An ISO offset is not a zone, and the instant
            # does not need one.
            spec["timezone"] = one.zone
    except Exception:  # noqa: BLE001 - no tz database: the instant stands without a display zone
        logger.debug("zone %r not checkable; leaving it off the spec", one.zone)
    return _Timing(spec=spec, because=f"read as one time: {said}")


def _repeats_but_once(text: str, *, expr: str = "") -> str:
    reading = f" ({expr})" if expr else ""
    return (
        f"{text!r} repeats{reading}, and a one-time task runs once. Give the one time it should "
        "run ('tomorrow at 9am'), or use set_recurring_task to repeat it."
    )


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


#: The tool names the table declares. Data rather than eight scattered string literals, so
#: `list_tools()`, the dispatcher, and the tests cannot drift out of step — the failure mode where
#: a declared tool has no handler and reports "unknown tool" at the worst moment.
TOOL_NAMES: tuple[str, ...] = (
    "automation_create",
    "automation_list",
    "automation_update",
    "automation_pause",
    "automation_resume",
    "automation_run",
    "automation_dry_run",
    "automation_history",
    "automation_delete",
    "automation_delete_all",
)

#: Fields an `automation_update` patch may set. An allowlist because a patch is agent-supplied:
#: letting it reach `run_count`/`last_run_id`/`health_status` would let an automation rewrite its
#: own health record, and the autopause thresholds on exactly those numbers.
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
        # 🔴 `failure_policy` joins the allowlist. `failure_delivery` has been patchable
        # since S158 while the policy beside it was not, so `dedupe_hash` — the opt-in
        # `delivery.repeats_last_failure` gates on — was settable by the MIGRATION and by nothing
        # else. A control only a one-time migration can turn on is not a control.
        #
        # `autopause_after` rides in the same dict and is a threshold §3.7 acts on, which is why the
        # dashboard handler MERGES one key rather than sending the dict: this allowlist protects the
        # health *fields*, not the keys inside a patchable dict, so a caller that sends
        # `{"dedupe_hash": true}` alone would drop a tuned threshold. See `_update_schedule`.
        "failure_policy",
        "yield_to_user",
        "catch_up",
        "expires_at",
    }
)

#: The patchable fields that hold a route (`delivery.written_route` checks each).
ROUTE_FIELDS: frozenset[str] = frozenset({"delivery", "failure_delivery"})


def asks_chat_channels(patch: Any) -> bool:
    """Whether checking *patch* asks the chat channels set up here: a `send-message` action's
    channel, or a route that names one. A caller outside the gateway builds them only then."""
    return isinstance(patch, dict) and bool(({"workflow"} | ROUTE_FIELDS) & set(patch))


_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class AutomationToolResult:
    """One `automation_*` tool call's outcome. `text` is what the agent sees; `data` is for a
    surface.

    Named `ToolResult` until #3511, when `personalclaw.sdk.channel` had to publish it: three of
    its functions (`delete_automation`, `delete_all_automations`, `set_automation_paused`) return
    this type, and an app that cannot NAME a return type cannot annotate what it got back. The
    facade already exported a DIFFERENT `ToolResult` — `tool_providers.base.ToolResult`
    (`success`/`output`/`error`/`agent_error`), which every one of the eleven first-party apps
    imports from `sdk.tool`. Two disjoint dataclasses reachable under one name from one facade is
    a name that answers "which one?" with "it depends which module you imported", so the less
    established of the two was renamed rather than aliased: this one had zero by-name importers
    anywhere, and all forty-nine of its references lived in this module.

    NOT merged with that type, which would be the coherence fix: the field names are disjoint and
    `to_dict()`'s `{ok, text, data}` shape is on the wire (the `automation_*` chat tools, the
    dashboard trigger handlers and `mcp_automation` all read it), so merging is a wire change, not
    a rename.
    """

    ok: bool
    text: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "text": self.text, "data": dict(self.data)}


#: The one provider whose action config carries an execution target, as a LITERAL rather than an
#: import of `browse_provider.PROVIDER_NAME`.
#:
#: 🔴 Importing that module costs ~1.0s and drags in `personalclaw.browse` →
#: `browse.compress` → `browse.extraction` → `knowledge.connectors.web_url`. Paying that on EVERY
#: `create`/`update` — a `notify` cron included — to learn a five-character string is the opposite
#: of what this function's docstring promises. `test_browse_target.py` asserts this equals
#: `browse_provider.PROVIDER_NAME`, so the literal cannot drift; that rail is the right price for
#: keeping a cold dependency off a hot registration path.
_BROWSE_PROVIDER = "browse"


def unattended_action_refusal(workflow: Any) -> AutomationToolResult | None:
    """Refuse AT REGISTRATION an action whose execution target can never run unattended.

    Every trigger in this store fires with no human present by construction — the dispatch seam
    resolves its identity as `unattended_dispatch_key("trigger:<id>")` and wraps the whole fire
    in the `background` writing surface (`gateway._background_write_surface`). So a
    `user_browser` browse action on a trigger is not a run-time hazard to be caught later, it is
    a CONFIGURATION ERROR: the row could never fire successfully, and letting it save would give
    the user a green automation that refuses on every tick until they read a log.

    Returns `None` for every action that is not this one, so the cost on the normal path is one
    provider-name comparison. The typed `AgentError` rides in `data["error"]` —
    `AutomationToolResult.text` is the sentence, and a surface that wants to branch reads the code.
    """
    inline = _inline_action_of(workflow)
    if str(inline.get("provider") or "").strip() != _BROWSE_PROVIDER:
        return None
    config = inline.get("config")
    from personalclaw.browse.target import (
        UnknownBrowseTarget,
        permits_unattended,
        resolve_target,
        unattended_refusal,
        unknown_target_error,
    )

    try:
        target = resolve_target(config if isinstance(config, dict) else {})
    except UnknownBrowseTarget as exc:
        typed = unknown_target_error(exc.raw)
        return AutomationToolResult(
            False, f"Error: {typed.what}. {typed.fix}.", {"error": typed.to_dict()}
        )
    if permits_unattended(target):
        return None
    typed = unattended_refusal(target, origin="a scheduled automation")
    return AutomationToolResult(
        False, f"Error: {typed.what}. {typed.fix}.", {"error": typed.to_dict()}
    )


def _inline_action_of(workflow: Any) -> dict[str, Any]:
    """A raw `workflow` block's action as `{provider, config}`, or `{}`.

    Both shapes reach the registration paths — the migrated `{"inline": {…}}` form the API and CLI
    build, and the bare `{"provider": …, "config": …}` form `create`'s `message` branch builds — so
    unwrapping in ONE place is what keeps the two registration refusals below from disagreeing about
    where to look. `schedule_view._inline_action` answers the same question for a persisted
    `Trigger`; this one takes the block a writer is still holding.
    """
    block = workflow if isinstance(workflow, dict) else {}
    inline = block.get("inline")
    return inline if isinstance(inline, dict) else block


def unregistered_action_provider_refusal(workflow: Any) -> AutomationToolResult | None:
    """Refuse an action whose provider the registry cannot dispatch (#779).

    An unregistered provider was created-enabled-armed and then rejected on EVERY dispatch by the
    gateway — the exact green-row-silent-loop BA-7 exists to prevent. Refused against the LIVE
    registry so the fix is one edit away, and `_ensure_default_providers_registered` runs first
    because the built-ins register lazily on first action execution (a caller that skipped it would
    refuse every automation).

    `create` has refused this since #779; a FUNCTION rather than an inline block because `update`
    is the same door — save `notify`, then PATCH the provider to a name nothing dispatches, and a
    create-only check has been walked around. An empty provider is left to `normalize_action`,
    which owns "an action needs a provider"; a second sentence for one field is a second owner.
    `resume`-carrying workflows are exempt: they re-enter a paused run rather than naming a
    provider to dispatch.
    """
    block = workflow if isinstance(workflow, dict) else {}
    name = str(_inline_action_of(workflow).get("provider") or "").strip()
    if not name or "resume" in block:
        return None
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
        list_action_providers,
    )

    _ensure_default_providers_registered()
    # The bare existence question and nothing more — the resolved provider is never bound to a
    # name, handed to a runner, or executed, which is the whole of this module's exemption from
    # `EXECUTION_SITES` (`test_the_create_time_provider_check_only_asks_existence`).
    if get_action_provider(name) is None:
        return AutomationToolResult(
            False,
            f"Error: unknown action provider {name!r}. "
            f"Registered providers: {sorted(list_action_providers())}.",
            {"provider": name},
        )
    return None


def unsendable_message_refusal(
    workflow: Any, *, chat_channels: Any = None
) -> AutomationToolResult | None:
    """Refuse a `send-message` action no fire could send: a ``via`` that names no chat channel set
    up here, an id that channel does not take, or an id without ``via`` that no chat channel set up
    here, or more than one, takes (`send_message_provider.config_problem`, over *chat_channels*,
    the registered ones when None). The Triggers page asks the same question where it saves
    (`dashboard/handlers/triggers._action_problem`); this is the chat's and the CLI's door, for
    `create` and `update` alike."""
    action = _inline_action_of(workflow)
    if str(action.get("provider") or "") != "send-message":
        return None
    from personalclaw.action_providers.send_message_provider import config_problem

    config = action.get("config")
    problem = config_problem(config if isinstance(config, dict) else {}, transports=chat_channels)
    return AutomationToolResult(False, f"Error: {problem}") if problem else None


def _refused_command(workflow: Any) -> str:
    """Why every run of *workflow* would be refused before its `bash` command ran: the shell
    denylist refuses its text (`bash_provider.config_problem`). "" for any other action."""
    action = _inline_action_of(workflow)
    if str(action.get("provider") or "") != "bash":
        return ""
    from personalclaw.action_providers.bash_provider import config_problem

    config = action.get("config")
    return config_problem(config if isinstance(config, dict) else {})


def denied_command_refusal(workflow: Any) -> AutomationToolResult | None:
    """Refuse a `bash` action whose command the shell denylist refuses (:func:`_refused_command`):
    every run would refuse it. The Triggers page asks the same question where it saves
    (`dashboard/handlers/triggers._action_problem`); this is the chat's and the CLI's door, for
    `create` and `update` alike."""
    problem = _refused_command(workflow)
    return AutomationToolResult(False, f"Error: {problem}") if problem else None


def write_scope_refusal(workflow: Any) -> AutomationToolResult | None:
    """Refuse an agent-starting action whose ``writes`` names a file no automation may change
    (`write_scope.problem`): PersonalClaw's own files, a credential location, a secret file, a
    whole disk, or any file at all for an agent that runs on an agent CLI
    (`automation_posture.step_problem`). The Triggers page asks the same question where it saves
    (`dashboard/handlers/triggers._action_problem`); this is the chat's and the CLI's door."""
    from personalclaw.automation_posture import step_problem

    action = _inline_action_of(workflow)
    config = action.get("config")
    problem = step_problem(config if isinstance(config, dict) else {})
    if not problem:
        return None
    return AutomationToolResult(False, f"Error: the files it may change can't be saved: {problem}.")


def posture_refusal(
    workflow: Any, *, stored: dict[str, Any], creating: bool
) -> AutomationToolResult | None:
    """Refuse a step posture a caller without the owner's yes would loosen (`automation_posture`).

    ``approval_mode: "auto"`` lets the action's agent approve its own tool calls, and ``capability:
    "mutating"`` gives it write access; the owner's surfaces ask before saving either (the Triggers
    page's dialog, the CLI's ``--yes``) and pass their yes as `owner_consented`. Every other caller
    — the chat's ``automation_update`` is the one that can send a whole action — is refused here,
    with nothing saved, because the grant the owner can give from the Triggers page covers what the
    action runs, not whether its agent stops asking. Tightening one asks nobody. Like the grant
    refusals read away from the dashboard (`grants.refusal`), it says where the yes is given, not
    who gives it: the chat relays it to the owner.
    """
    from personalclaw.automation_posture import unconsented_step_loosening

    action = _inline_action_of(workflow)
    config = action.get("config")
    loosened = unconsented_step_loosening(
        "action",
        current=stored,
        new=config if isinstance(config, dict) else {},
        body={},
        provider=str(action.get("provider") or ""),
    )
    if loosened is None:
        return None
    _field, sentence = loosened
    where = "create it" if creating else "make that change"
    return AutomationToolResult(
        False,
        f"Error: nothing was saved: “{sentence}” That can be allowed only on the Triggers page, "
        f"which asks first: {where} there.",
        {"needs_consent": sentence},
    )


#: What `catch_up` does, in the owner's words: the tool schema's description and a created
#: automation's announcement say it in these sentences, so the two cannot disagree. True of both
#: ways a time is missed (`scheduling.slot_missed`): PersonalClaw stopped, or the computer asleep.
CATCH_UP_OFF = (
    "If one of its times is missed because PersonalClaw was stopped or the computer was asleep, "
    "it does not run late on its own: it waits on the Triggers page for you to run it or "
    "dismiss it."
)
CATCH_UP_ON = (
    "If one of its times is missed because PersonalClaw was stopped or the computer was asleep, "
    "it runs once by itself within a few minutes of PersonalClaw being back, however many times "
    "were missed, and its history says how late it ran."
)


def catch_up_refusal(kind: str, catch_up: Any) -> AutomationToolResult | None:
    """Refuse a `catch_up` that is not true or false, or that is on for a trigger with no times.

    Not a bool is refused rather than coerced, for the reason the dashboard's `enabled` gives: the
    string "false" is truthy, and a row stores what it is sent while `Trigger.from_dict` reads only
    `True` back, so a coerced value would catch up until the next reload and then silently not.
    And only a schedule has times to miss: kept on any other kind, the setting would say something
    the automation never does.
    """
    if not isinstance(catch_up, bool):
        return AutomationToolResult(
            False, "Error: catch_up is true or false.", {"catch_up": catch_up}
        )
    if catch_up and kind != "clock":
        return AutomationToolResult(
            False,
            "Error: catch_up is for an automation that runs at a time or on a schedule; this one "
            "has no times to miss.",
            {"catch_up": catch_up, "kind": kind},
        )
    return None


def spec_error_refusal(kind: str, spec: Any) -> AutomationToolResult | None:
    """Refuse a spec that cannot do what it says — structure AND semantics (#483/#687/#612/#270).

    `validate_spec` owns STRUCTURE ("Structure here, semantics there" — its own docstring) and
    `arm.semantic_spec_issues` owns the rest, living beside the fire path so a check can never
    drift from what arming does: an expression croniter refuses, a seconds-cadence 6/7-field
    expression, an unmatchable skip date, a typo'd zone. Only ERRORS refuse; warnings ride the
    created-response.

    Extracted from `create` for `update`'s sake — `arm` refuses an unparseable spec rather than
    guessing a cadence, so PATCHing a good expression to `'99 99 * * *'` put the row right back to
    enabled-and-never-armed, the create-time refusal walked around. One function so the two doors
    cannot answer differently about the same field.
    """
    block = spec if isinstance(spec, dict) else {}
    from personalclaw.triggers.arm import semantic_spec_issues
    from personalclaw.triggers.models import validate_spec

    errors = [
        i
        for i in (*validate_spec(kind, block), *semantic_spec_issues(kind, block))
        if i.severity == "error"
    ]
    if not errors:
        return None
    detail = "; ".join(f"{i.path}: {i.message}" for i in errors)
    return AutomationToolResult(False, f"Error: {detail}", {"spec": block})


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

    Nor an automation that is gone but whose runs are still recorded: a one-shot that retired
    after its run, or one the chat deleted. Its history is keyed by its id, so a new automation
    taking that id would show a run it never made as its own.
    """
    from pathlib import Path

    from personalclaw.schedule_history import ScheduleRunStore

    existing = {row.trigger.id for row in store.load()}
    root = getattr(store, "base_dir", None)
    runs = ScheduleRunStore(Path(root)) if root is not None else None

    def taken(candidate: str) -> bool:
        return candidate in existing or (runs is not None and runs.has_runs(candidate))

    if not taken(base):
        return base
    for n in range(2, 100):
        candidate = f"{base}-{n}"
        if not taken(candidate):
            return candidate
    return f"{base}-{len(existing) + 1}"


def _active_agent_count(store: Any) -> int:
    return sum(
        1 for row in store.load() if row.trigger.created_by == "agent" and row.trigger.enabled
    )


def _origin_harness_for(store: Any) -> str:
    """This home's stable `machine_id` — the origin stamped on a locally-minted trigger
    (MULTI-TENANCY-ENTITY TSE2-2). Resolved from the STORE's own home (`base_dir`) so it matches the
    file the row is written to, falling back to the active `config_dir`; REUSES `durability`'s
    per-machine key and never raises — an unreadable home degrades to ``""`` = "this harness's".
    """
    try:
        from pathlib import Path

        from personalclaw.durability.shards import machine_id

        base = getattr(store, "base_dir", None)
        if base is None:
            from personalclaw.config.loader import config_dir

            base = config_dir()
        return machine_id(Path(base))
    except Exception:  # noqa: BLE001 - origin attribution must never break a create
        return ""


def _workflow_run(run_id: str) -> Any:
    """The workflow run *run_id*, or None. Reads no store that does not exist yet: asking about a
    run must not make the run store."""
    from personalclaw.workflows import store as runs

    return runs.get(run_id) if run_id and runs.def_names() else None


def _running_workflow_runs() -> list[Any]:
    """The workflow runs still going, oldest first; none before any workflow has run."""
    from personalclaw.workflows import store as runs

    return sorted(runs.active_runs(), key=lambda r: r.created_at) if runs.def_names() else []


def _running_now() -> str:
    """The workflow runs still going, by id and workflow, for a refusal to offer."""
    running = _running_workflow_runs()
    if not running:
        return "No workflow run is going now."
    return "Going now: " + ", ".join(f"{r.id} ({r.workflow_name})" for r in running) + "."


#: The spec keys naming what a `run_completed` trigger waits on (`models.SPEC_KEYS`).
_RUN_SOURCE_KEYS: tuple[str, ...] = ("source_run", "source_trigger", "source_def")


def _run_source(store: Any, spec: dict[str, Any], run_name: str) -> tuple[dict[str, Any], str, str]:
    """``(spec, because, error)`` for a `run_completed` automation: what it waits on, checked.

    A run's id must be a run that is still going; a trigger's id a trigger here. A name the request
    gave the run ("the research run", "my nightly run") is resolved in that order to the ONE run
    going whose workflow it names, the one trigger it names, or the one workflow that has run under
    it. Anything else is refused with the runs going now, never saved: a `run_completed` row with
    nothing to wait on matches nothing and would sit listed and silent forever.
    """
    named = [key for key in _RUN_SOURCE_KEYS if str(spec.get(key) or "").strip()]
    if len(named) > 1:
        return spec, "", f"Give one thing for it to run after, not {' and '.join(named)}."
    if named == ["source_run"]:
        run_id = str(spec["source_run"]).strip()
        run = _workflow_run(run_id)
        if run is None:
            return spec, "", f"There is no workflow run {run_id}. {_running_now()}"
        if run.is_terminal:
            from personalclaw.workflows.models import run_ending

            return (
                spec,
                "",
                (
                    f"The workflow run {run_id} ({run.workflow_name}) {run_ending(run.status)} "
                    "already, so there is nothing left to wait for."
                ),
            )
        return spec, f"runs when the workflow run {run_id} ({run.workflow_name}) ends", ""
    if named == ["source_trigger"]:
        trigger_id = str(spec["source_trigger"]).strip()
        if store.get(trigger_id) is None:
            return spec, "", f"There is no automation {trigger_id} for it to run after."
        return spec, f"runs each time the work of the automation {trigger_id} ends", ""
    if named == ["source_def"]:
        return spec, f"runs each time a run of the workflow {spec['source_def']} ends", ""

    wanted = run_name.strip().casefold()
    if wanted:
        runs = [r for r in _running_workflow_runs() if wanted in r.workflow_name.casefold()]
        if len(runs) == 1:
            return _run_source(store, {**spec, "source_run": runs[0].id}, "")
        triggers = [
            t
            for t in store.list_triggers()
            if t.kind != "run_completed"
            and wanted in (t.id.casefold(), t.id.casefold().partition(":")[2], t.name.casefold())
        ]
        if len(triggers) == 1:
            return _run_source(store, {**spec, "source_trigger": triggers[0].id}, "")
        from personalclaw.workflows import store as wf_runs

        defs = [d for d in wf_runs.def_names() if wanted == d.casefold()]
        if len(defs) == 1:
            return _run_source(store, {**spec, "source_def": defs[0]}, "")
        return (
            spec,
            "",
            (
                f"I could not tell which run “{run_name}” is. Give the run's id, the automation's "
                f"name or the workflow's name. {_running_now()}"
            ),
        )
    return (
        spec,
        "",
        (
            "Which run should it wait for? Give the run's id, the automation's name or the "
            f"workflow's name. {_running_now()}"
        ),
    )


def _channel_shown(key: str, chat_channels: Any) -> str:
    """The name chat channel *key* is shown under: in *chat_channels*, else among the registered."""
    from personalclaw.channel_delivery import channel_shown_as

    transport = (chat_channels or {}).get(key) if isinstance(chat_channels, dict) else None
    return str(getattr(transport, "display_name", "") or channel_shown_as(key))


def _route_said(route: str, *, chat_channels: Any) -> str:
    """Where *route* sends a run's note, in the owner's words: what the fire path does with it
    (`delivery.deliver`), so the sentence and the delivery cannot disagree."""
    from personalclaw.triggers.delivery import is_muted, parse_channel_route

    channel = parse_channel_route(route)
    if channel is not None:
        key, chat = channel
        shown = _channel_shown(key, chat_channels)
        return f"on {shown}" + (f" to {chat}" if chat else "") + ", and on no other channel"
    if is_muted(route):
        return ""
    return "as a notification in PersonalClaw"


def _routes_said(trigger: Any, *, chat_channels: Any) -> list[str]:
    """Where *trigger*'s results go and where its failures go, as stored: the routes the fire path
    picks for each outcome (`delivery.route_for`, `delivery.files_in_inbox`)."""
    from personalclaw.triggers.delivery import files_in_inbox, notifies_on_its_own, route_for

    if notifies_on_its_own(trigger):
        results = "its action is itself a notification in PersonalClaw"
    else:
        results = _route_said(route_for(trigger, ok=True), chat_channels=chat_channels) or (
            "nowhere; nothing is sent to you when it runs"
        )
    if files_in_inbox(trigger, ok=False):
        failures = "filed in your Inbox"
    else:
        failures = (
            _route_said(route_for(trigger, ok=False), chat_channels=chat_channels)
            or "you are not told"
        )
    return [f"Where its results go: {results}.", f"If it fails: {failures}."]


def _as_stored(store: Any, trigger: Any) -> tuple[Any, list[Any]]:
    """*trigger* as the store reads it back, with what is wrong with it: the row every surface
    shows, so a tool's answer is about that row and not the object it just wrote."""
    row = store.get(trigger.id)
    return (trigger, []) if row is None else (row.trigger, list(row.errors))


def _standing(trigger: Any, errors: list[Any]) -> str:
    """Whether *trigger* runs now, as stored (:func:`_as_stored`): in the order the Triggers page's
    status line decides it, so the chat and the page say the same thing."""
    from personalclaw.triggers import grants
    from personalclaw.triggers.legacy_import import needs_review
    from personalclaw.triggers.models import TriggerState

    if errors:
        return f"it has a problem and does not run until it is fixed: {errors[0].message}"
    if needs_review(trigger):
        return (
            "it was brought over from an older version and does not run until you switch it on "
            "on the Triggers page"
        )
    if trigger.enabled and grants.labels(trigger):
        return (
            "it is on the Triggers page, and it does not run until you allow it there: open "
            "it and choose Allow, and PersonalClaw asks you first"
        )
    state = str(trigger.state or "")
    if state == TriggerState.AUTOPAUSED.value:
        return "it was stopped after repeated failures, and runs again once you switch it back on"
    if state == TriggerState.QUARANTINED.value:
        return (
            "it is quarantined: something it was given matched an injection pattern, and it does "
            "not run until it is re-authored"
        )
    if state == TriggerState.PARKED.value:
        return "it is parked: something it needs is busy, and it resumes on its own"
    if not trigger.enabled:
        return "it is switched off until you enable it, and visible on the Triggers page"
    return "it is active now and visible on the Triggers page"


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
    resume: dict[str, Any] | None = None,
    ttl_secs: float = 0,
    gates: dict[str, Any] | None = None,
    owner_consented: bool = False,
    recurrence: str = "",
    say: str = "",
    via: str = "",
    to: str = "",
    chat_channels: Any = None,
    changes: list[str] | None = None,
    catch_up: bool = False,
) -> AutomationToolResult:
    """`automation_create` — §4's NL-friendly constructor. Criterion 2's one message.

    `when` is routed by `nl_kind.route()` BEFORE any cadence conversion, which is the whole point:
    a file-watch request must never reach a component whose only output shape is a cron expression.
    An explicit `kind`+`spec` bypasses routing for a caller that already knows.

    A clock `when` becomes ONE TIME or a CADENCE, never a cron for one time (`_read_when`): "at 5
    pm", "in 20 minutes" and "tomorrow at 9am" are read without a model into a one-time `at` spec
    in the owner's zone, and only a phrase that leaves the time to judgement is asked of the model.
    `recurrence` is what the calling tool promises — `ONCE` (`set_onetime_task`) or `RECURRING`
    (`set_recurring_task`) — and a `when` that reads the other way is refused with the tool that
    fits, rather than made into what the caller did not ask for.

    `cadence_to_cron` is injected (defaulting to the shipped `nl_to_cron`, which answers a
    `nl_to_cron.Schedule`) so every branch of this function is testable without a model — the same
    seam `ScheduleService` uses for `_on_job` and the executor uses for its runner.

    🔴 A NEW TRIGGER IS GRANTED ONLY BY THE OWNER'S YES (`triggers.grants`). `owner_consented` is
    that yes: the Triggers page's create dialog passes it after asking, and the CLI after `--yes`.
    Without it the row is created as asked but not allowed to run its action, and the Triggers page
    offers Allow. Measured on `main`: `automation_create` froze the grant for whatever it made, so
    an agent's automation came with its own permission to run.

    `say` is words for the owner, sent as written each time it fires by a `send-message` action (no
    agent runs), and `via` the chat channel the owner named: the words go out there, and a task's
    result in `message` is delivered there (the trigger's `delivery`, the route the Triggers page's
    Notify channel sets), on no other channel. `to` is a chat on that channel, by the channel's own
    id; without it, the owner's direct messages there. A name that is not a chat channel set up
    here is refused with the ones that are, so the owner can be asked which
    (`channel_delivery.named_chat_channel`, over `chat_channels`, the registered ones when None),
    and an id the channel does not take is refused in the channel's own words.

    `catch_up` is what a missed time does (`missed.catch_up_plan`): off, a time missed while
    PersonalClaw was stopped or the computer slept waits on the Triggers page for the owner to run
    or dismiss; on, it runs once by itself when PersonalClaw is back, recorded as late. Only a
    schedule has times to miss, so it is refused for any other kind rather than kept and ignored.
    """
    from personalclaw.triggers import grants
    from personalclaw.triggers import screen as _screen
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.nl_kind import route

    if not (name or "").strip():
        return AutomationToolResult(False, "Error: name is required.")
    words = (say or "").strip()
    if words and ((message or "").strip() or workflow or resume is not None):
        return AutomationToolResult(
            False,
            "Error: give the words to send in `say`, or what the automation should do in "
            "`message`, not both.",
        )
    via_key = shown_via = ""
    chat = (to or "").strip()
    if chat and not (via or "").strip():
        return AutomationToolResult(
            False,
            "Error: `to` is a chat on the channel `via` names; give `via` too.",
            {"to": chat},
        )
    if (via or "").strip():
        from personalclaw.channel_delivery import (
            channel_shown_as,
            named_chat_channel,
            target_problem,
        )

        via_key, problem = named_chat_channel(via, transports=chat_channels)
        if problem:
            return AutomationToolResult(
                False,
                f"Error: nothing was saved: {problem} Ask the owner which one to use.",
                {"via": via},
            )
        problem = target_problem(via_key, chat, transports=chat_channels) if chat else ""
        if problem:
            return AutomationToolResult(
                False,
                f"Error: nothing was saved: {problem} Ask the owner for it.",
                {"via": via, "to": chat},
            )
        shown_via = str(
            getattr((chat_channels or {}).get(via_key), "display_name", "")
            or channel_shown_as(via_key)
        )

    resolved_spec = dict(spec or {})
    resolved_gates = dict(gates or {})
    because = ""
    run_name = ""
    if kind:
        resolved_kind = kind
    else:
        routed = route(when)
        if not routed.ok:
            # The refusal is the RESULT, phrased for the user. Defaulting an unroutable request to
            # a schedule is how "when a file changes" becomes a per-minute poll.
            return AutomationToolResult(False, f"Error: {routed.error}", {"when": when})
        resolved_kind, because = routed.kind, routed.because
        resolved_spec = {**routed.spec, **resolved_spec}
        run_name = routed.run_name
        if routed.cadence and "expr" not in resolved_spec and "at" not in resolved_spec:
            timing = _read_when(
                routed.cadence,
                recurrence=recurrence,
                converter=cadence_to_cron or _default_cadence_to_cron,
            )
            if timing.error:
                return AutomationToolResult(
                    False, f"Error: {timing.error}", {"cadence": routed.cadence}
                )
            resolved_spec = {**timing.spec, **resolved_spec}
            because = timing.because

    if resolved_kind == "run_completed":
        resolved_spec, waits_on, problem = _run_source(store, resolved_spec, run_name)
        if problem:
            return AutomationToolResult(False, f"Error: {problem}", {"spec": resolved_spec})
        because = waits_on

    if resolved_kind == "event":
        # An event spec names its pattern; the source follows from it, and an author who names one
        # must not have to repeat what it implies (`event_triggers.with_derived_source`). The burst
        # guard is the KIND's default rather than a per-surface one, so an event trigger made in
        # chat and one made on the Triggers page start with the same debounce.
        from personalclaw.event_triggers import DEFAULT_DEBOUNCE_SECS, with_derived_source

        resolved_spec = with_derived_source(resolved_spec)
        resolved_gates.setdefault("debounce_secs", DEFAULT_DEBOUNCE_SECS)

    if created_by == "agent":
        active = _active_agent_count(store)
        cap = max_agent_triggers()
        if active >= cap:
            # Decision 5d's cap. Refusing with the count and the remedy, because "limit reached"
            # without a number leaves the user unable to tell what to pause.
            return AutomationToolResult(
                False,
                f"Error: {active} agent-created automations are already active "
                f"(cap {cap}). Pause or delete one first.",
                {"active": active, "cap": cap},
            )

    if resume is not None:
        # The write side of AUTO-R11's resume targets: this trigger WAKES a parked run
        # instead of starting a new one. The resume dict becomes `workflow.resume` — the key
        # `wakeup.resume_target_of` reads — and it deliberately REPLACES the inline-action shape:
        # `models._resume_target_issues` treats both-declared as an authoring error, so a given
        # `message` rides as the gate ANSWER (what the woken run reads) rather than as a
        # run-prompt action that would never fire.
        if workflow:
            return AutomationToolResult(
                False,
                "Error: give either a resume target or a workflow, not both — a trigger with a "
                "resume target wakes the named run instead of running an action.",
            )
        target = {k: v for k, v in dict(resume).items() if v not in (None, "")}
        if not str(target.get("run_id", "") or "").strip():
            return AutomationToolResult(
                False, "Error: a resume target needs a run_id.", {"resume": target}
            )
        if message and "answer" not in target:
            target["answer"] = message
        workflow = {"resume": target}
    if words:
        # As written: a `$` the action's template would read as a placeholder is escaped.
        config: dict[str, Any] = {"text_template": re.sub(r"\$(?=[A-Za-z_{$])", "$$", words)}
        if via_key:
            config["via"] = via_key
        if chat:
            config["channel"] = chat
        workflow = {"provider": "send-message", "config": config}
    if message and not workflow:
        # `changes` are the files the job changes (`write_scope`): its agent may change those and
        # nothing else, once the owner allows it, and the Allow says so.
        run: dict[str, Any] = {"message": message}
        if changes:
            run["writes"] = [str(path).strip() for path in changes if str(path).strip()]
        workflow = {"provider": "run-prompt", "config": run}
    if not workflow:
        return AutomationToolResult(
            False, "Error: give a message or a workflow for the automation to run."
        )

    # Refused HERE, before the row exists, not on its first tick. A saved automation that
    # refuses forever is worse than a rejected form — the user gets a green row and a silent
    # failure loop instead of a sentence naming the mistake while they are still editing it.
    for refusal in (
        unattended_action_refusal(workflow),
        # Same rule for the SPEC and the PROVIDER (#483/#687/#612/#270/#779). Measured before
        # fixing: `POST /api/triggers` persisted `{"kind":"cron","expr":"not a cron"}` as an enabled
        # row with `next_fire_at=""` that could never fire, and an unregistered action provider
        # dispatched into the gateway's "unknown action provider" warning forever — both while the
        # doctor said healthy.
        spec_error_refusal(resolved_kind, resolved_spec),
        unregistered_action_provider_refusal(workflow),
        unsendable_message_refusal(workflow, chat_channels=chat_channels),
        denied_command_refusal(workflow),
        write_scope_refusal(workflow),
        None if owner_consented else posture_refusal(workflow, stored={}, creating=True),
        catch_up_refusal(resolved_kind, catch_up),
    ):
        if refusal is not None:
            return refusal

    # Warnings ride the created-response so a sub-floor cadence or inert skip date is named while
    # the author is still looking — the ERROR half above is what refuses.
    from personalclaw.triggers.arm import semantic_spec_issues

    spec_warnings = [
        i
        for i in semantic_spec_issues(resolved_kind, resolved_spec, workflow)
        if i.severity != "error"
    ]

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
        # Origin (MULTI-TENANCY-ENTITY TSE2-2): a locally-minted trigger's origin IS this home, so
        # it is stamped from the store's `machine_id` at create — never caller-set. Empty → "".
        origin_harness=_origin_harness_for(store),
        spec=resolved_spec,
        gates=resolved_gates,
        workflow=dict(workflow),
        catch_up=catch_up is True,
    )
    from personalclaw.triggers.delivery import CHANNEL_ROUTE_PREFIX, INBOX_ROUTE

    if via_key and not words:
        # A task's result goes where the owner named, as a trigger made on the Triggers page with
        # a Notify channel does: the same route, read by the same delivery.
        trigger.delivery = f"{CHANNEL_ROUTE_PREFIX}{via_key}" + (f":{chat}" if chat else "")
    elif not words and resume is None:
        # A task's result reaches the owner unless something says otherwise, as one made on the
        # Triggers page does ("It still reaches the dashboard"). The entity's default is silence,
        # which made "watch this page and tell me when it ships" a watch that told nobody. Words
        # in `say` are their own delivery, and a resume target's run reports on its own trigger.
        trigger.delivery = INBOX_ROUTE
    # 🔴 FREEZE THE CAPABILITY SET AT SAVE (decision 7 / R3), when the owner said yes to
    # this action. A read-only action gets an empty block either way: the fence permits those
    # without one, and a written-out grant would imply an opt-in nobody had to make.
    if owner_consented:
        trigger.capabilities = _screen.capabilities_for_action(trigger)
    # 🔴 ARM A CLOCK TRIGGER ON CREATION. `create` persisted `next_fire_at=""`, and
    # `service.due_ids` only surfaces rows that HAVE one — so every cron created through this
    # function (the chat tools, and the API from this session) would never fire. Arming at
    # creation rather than waiting for the next boot sweep is the difference between "runs tonight"
    # and "runs after the user restarts the gateway". An unarmable spec (invalid cron, elapsed
    # one-shot) returns "" and is left alone — `arm` refuses rather than guessing a cadence.
    # A self-scheduled (agent-created) trigger ALWAYS expires. Set before arming so
    # the row never exists without its bound; user-created rows keep their opt-in expiry
    # semantics untouched.
    # A `manual` row is the exception: it never fires on its own, only when the owner runs it, so
    # the bound on self-scheduling has nothing to bound, and an expiry shown on it would be false.
    if created_by == "agent" and not trigger.expires_at and resolved_kind != "manual":
        expiry = _agent_expiry(resolved_spec, ttl_secs)
        try:
            one_time_at = float(resolved_spec.get("at") or 0)
        except (TypeError, ValueError):
            one_time_at = 0.0
        if one_time_at > 0 and expiry <= one_time_at:
            # A one-time task that expires before its own time never runs, and says nothing.
            return AutomationToolResult(
                False,
                f"Error: ttl_secs ends before the task's own time ({_iso(one_time_at)}), so it "
                "would expire without running. Give a longer ttl_secs, or leave it out.",
                {"ttl_secs": ttl_secs},
            )
        trigger.expires_at = _iso(expiry)

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
    for w in spec_warnings:
        lines.append(f"  Warning ({w.path}): {w.message}")
    if resolved_spec.get("expr"):
        lines.append(f"  cron: {resolved_spec['expr']}")
    if resolved_spec.get("paths"):
        lines.append(f"  watching: {', '.join(resolved_spec['paths'])}")
    if saved.kind == "manual":
        lines.append(
            "  it runs only when you run it: Run now on the Triggers page, or the Run now button "
            "shown with this reply in the chat"
        )
    if words:
        where = (
            f"on {shown_via}" + (f" to {chat}" if chat else "") + ", and on no other channel"
            if via_key
            else "on the first connected chat channel that knows you, else in PersonalClaw"
        )
        lines.append(f"  sends you “{redact_for_display(words)}” {where}")
    else:
        # Read off the saved route, the one its runs report on (`delivery.route_for`).
        produced = _route_said(saved.delivery, chat_channels=chat_channels)
        if produced:
            lines.append(f"  sends you what it produced {produced}")
    if saved.catch_up:
        lines.append(f"  {CATCH_UP_ON}")
    reach = grants.what_its_agent_may_do(saved)
    if reach:
        lines.append(f"  when it runs: {reach}")
    needs = grants.labels(saved)
    if created_by == "agent":
        # "active now" is a claim about state, so it tracks state — the switch, and whether the
        # action is allowed to run — as stored. This string is UI: it is what the user reads in
        # chat after the agent creates an automation for them.
        _state = _standing(*_as_stored(store, saved))
        lines.append(
            f"  I created this for you — {_state} "
            f"({_active_agent_count(store)}/{max_agent_triggers()} agent-created)."
        )
    return AutomationToolResult(
        True, "\n".join(lines), {"trigger": saved.to_dict(), "needs_grant": needs}
    )


def _default_cadence_to_cron(cadence: str) -> Any:
    """Bridge to the shipped `nl_to_cron` from this synchronous dispatch; a `nl_to_cron.Schedule`.

    Mirrors `mcp_schedule._nl_to_cron_blocking` rather than inventing a second async bridge: the
    two would drift, and this one is already proven against a running loop.
    """
    import asyncio

    from personalclaw import memory_writes
    from personalclaw.nl_to_cron import nl_to_cron

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(nl_to_cron(cadence))
    # The worker thread carries the call's context: the turn's session key travels with the
    # call, so the chat whose tool is waiting is the one that says why.
    with memory_writes.ScopeCarryingExecutor() as pool:
        return pool.submit(asyncio.run, nl_to_cron(cadence)).result(timeout=60)


def list_automations(store: Any, *, kind: str = "", state: str = "") -> AutomationToolResult:
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
        # The name and last error are masked the way the Automations page masks them: this list is
        # a read of the same triggers, and it lands in a chat's context.
        out.append(
            {
                "id": trigger.id,
                "name": redact_for_display(trigger.name or ""),
                "kind": trigger.kind,
                "enabled": trigger.enabled,
                "created_by": trigger.created_by,
                "health": trigger.health_status,
                "runs": trigger.run_count,
                "next_fire_at": trigger.next_fire_at,
                "last_error": redact_for_display(trigger.last_error_summary or ""),
                "broken": [i.message for i in row.errors],
            }
        )
    if not out:
        return AutomationToolResult(True, "No automations match.", {"automations": []})
    lines = []
    for a in out:
        flag = "" if a["enabled"] else " [paused]"
        broken = f" ⚠ {a['broken'][0]}" if a["broken"] else ""
        health = f" health={a['health']}" if a["health"] else ""
        lines.append(f"{a['id']} — {a['name']} ({a['kind']}){flag}{health}{broken}")
    return AutomationToolResult(True, "\n".join(lines), {"automations": out})


def update(
    store: Any,
    *,
    trigger_id: str,
    patch: dict[str, Any],
    owner_consented: bool = False,
    chat_channels: Any = None,
) -> AutomationToolResult:
    """`automation_update` — patch an existing automation through the allowlist.

    `chat_channels` is the chat channels a `send-message` action's channel is looked up in
    (`unsendable_message_refusal`): the registered ones when None, as in the gateway.

    A rejected key is REPORTED, not dropped silently: an agent that thinks it changed
    `health_status` and got no error would keep believing a stale model of the automation.

    The next fire follows the edit here, for every caller (`arm.next_fire_after_edit`): a changed
    schedule re-arms, and a trigger left on with no next fire is armed. `next_fire_at` is engine
    state, not in `PATCHABLE`, so no caller can set it, and none re-arms for itself: the chat's
    `automation_update` saved a new time and kept the old next fire, so it fired on the schedule it
    had just left, while the Triggers page's save re-armed.

    🔴 AN EDIT GRANTS AT EDIT TIME, OR SAVES THE TRIGGER SWITCHED OFF (`triggers.grants`). A patch
    that re-points the action at something the trigger is not allowed to run needs the owner's yes,
    and so does one that changes what a granted action runs — the grant was for the action as the
    owner allowed it (`grants.narrow`), so another command, URL, prompt or agent is a new question.
    `owner_consented` is that yes, passed by the editor after its consent dialog
    (`dashboard.handlers.triggers._grant_for_save`) and by the CLI after `--yes`: the save then
    grants what the action runs, so Run now works straight away. Every other caller — the chat's
    `automation_update` — cannot give it, so the edit is kept and the trigger switched off until
    the owner switches it on from the Triggers page, which asks first; and a posture only the owner
    can loosen is refused outright (`posture_refusal`). Measured on `main`: an agent re-pointing an
    owner's `notify` schedule at `bash` left it switched on, and an agent rewriting a granted `bash`
    command left it switched on and running the new command on the old grant.
    """
    row = store.get(trigger_id)
    if row is None:
        return AutomationToolResult(False, f"Error: no automation with id {trigger_id!r}.")
    rejected = sorted(set(patch) - PATCHABLE)
    applied = {k: v for k, v in patch.items() if k in PATCHABLE}
    if not applied:
        return AutomationToolResult(
            False,
            f"Error: nothing to update. Not settable here: {', '.join(rejected) or 'none given'}.",
            {"rejected": rejected},
        )
    # The agent was shown this automation masked (`automation_list`, and the model boundary), so a
    # value it patches back can carry a `[REDACTED: …]` marker where the row holds the value: each
    # keeps what it stands for, the way the Triggers editor's save does (`_keep_masked_trigger`).
    from personalclaw.security import MASK_CONFLICT, MaskConflict, keep_masked_values

    def _stored_as_sent(key: str, value: Any) -> Any:
        # An action is compared in the shape the patch sends it in (either form reaches here).
        stored = getattr(row.trigger, key, None)
        if key != "workflow":
            return stored
        action = _inline_action_of(stored)
        return {"inline": action} if isinstance(value, dict) and "inline" in value else action

    try:
        applied = {
            key: keep_masked_values(value, _stored_as_sent(key, value))
            for key, value in applied.items()
        }
    except MaskConflict:
        return AutomationToolResult(False, f"Error: {MASK_CONFLICT}")
    # A route is checked where it is written, by the rule the store reads it with, and stored as
    # the route it names (`delivery.written_route`). Unchecked, `"telegram"` was saved as sent and
    # answered "Updated", and the store then read it back as `inbox`: results went to a
    # notification while the chat told the owner they would arrive on Telegram.
    from personalclaw.triggers.delivery import written_route

    for key in ROUTE_FIELDS & set(applied):
        route, problem = written_route(applied[key], transports=chat_channels)
        if problem:
            return AutomationToolResult(
                False,
                f"Error: nothing was changed: {problem} Ask the owner where it should go.",
                {key: applied[key]},
            )
        applied[key] = route
    # The same registration refusals as `create`. Without them the update path is the hole —
    # save a `gateway` browse automation, then patch its `workflow` to `user_browser`, and the
    # create-time check has been walked around. #779/#687 close the same hole for the other two:
    # create with `bash` + `0 9 * * *`, then PATCH the provider to a name nothing can dispatch or
    # the expr to one nothing can parse, and the row is right back to armed-and-inert.
    if "workflow" in applied:
        stored = _inline_action_of(row.trigger.workflow).get("config")
        for refusal in (
            unattended_action_refusal(applied["workflow"]),
            unregistered_action_provider_refusal(applied["workflow"]),
            unsendable_message_refusal(applied["workflow"], chat_channels=chat_channels),
            denied_command_refusal(applied["workflow"]),
            write_scope_refusal(applied["workflow"]),
            (
                None
                if owner_consented
                else posture_refusal(
                    applied["workflow"],
                    stored=stored if isinstance(stored, dict) else {},
                    creating=False,
                )
            ),
        ):
            if refusal is not None:
                return refusal
    # `enabled` is patchable, so this is a second way to switch a trigger on, and it answers the
    # way `set_paused` does for a row a legacy import brought over and nobody has reviewed.
    from personalclaw.triggers.legacy_import import needs_review

    # Truthiness, not `is True`: the value is stored as sent, and a `1` or a `"true"` switches the
    # row on just the same.
    if applied.get("enabled") and needs_review(row.trigger):
        return _awaiting_review_refusal(row.trigger)
    if "catch_up" in applied:
        refusal = catch_up_refusal(row.trigger.kind, applied["catch_up"])
        if refusal is not None:
            return refusal
    if "spec" in applied:
        if row.trigger.kind == "event" and isinstance(applied["spec"], dict):
            # The same derivation `create` applies: an edit that names a pattern has named its
            # source, and requiring it again would refuse the edit for a key the author never chose.
            from personalclaw.event_triggers import with_derived_source

            applied["spec"] = with_derived_source(applied["spec"])
        # The row's OWN kind, not one the patch could carry: `_SETTABLE` does not admit `kind`, so
        # the spec being edited is always this trigger's, and `semantic_spec_issues` returns nothing
        # for a non-clock kind — a `file` glob is untouched by the cron rule.
        refusal = spec_error_refusal(row.trigger.kind, applied["spec"])
        if refusal is not None:
            return refusal
    import copy

    from personalclaw.triggers import grants

    trigger = row.trigger
    before = copy.deepcopy(trigger)
    for key, value in applied.items():
        setattr(trigger, key, value)
    # A report's automation mirrors the report's schedule (`knowledge.report_schedules`): an edit
    # the report cannot hold is refused before anything is saved, and the rest is written into the
    # report once the row is (`adopt`, below), so the two never say different things.
    from personalclaw.knowledge import report_schedules

    refused = report_schedules.edit_refusal(before, trigger)
    if refused:
        return AutomationToolResult(False, f"Error: {refused}")
    if "enabled" in applied:
        # A switch the patch sets is a decision, as `set_paused`'s is: it ends a restore's hold.
        trigger.restore_hold = ""
    if "spec" in applied and "expires_at" not in applied:
        _expiry_after_its_time(trigger)
    # What the edit changed keeps no grant (`grants.narrow`), so `missing` below asks about it the
    # way it asks about a provider the trigger was never allowed.
    changed = grants.narrow(trigger, before) if "workflow" in applied else []
    missing = grants.missing(trigger)
    granted: list[str] = []
    note = ""
    # The owner's yes is about the action this save carries — the question the editor asked names
    # it — so it grants only when the patch carries one.
    if missing and owner_consented and "workflow" in applied:
        granted = grants.give(trigger)
    elif missing and "workflow" in applied:
        trigger.enabled = False
        trigger.next_fire_at = ""
        note = grants.switched_off(trigger, missing, changed=changed)
    elif missing and applied.get("enabled"):
        return AutomationToolResult(
            False,
            f"Error: {grants.refusal(trigger, missing, elsewhere=True, switching_on=True)}",
            {"needs_grant": grants.labels(trigger)},
        )
    # The next fire follows the edit, for every caller: a moved schedule re-arms, and a trigger
    # left on with no next fire is armed. Read after the grant check, so a trigger it switched off
    # is left with none.
    from personalclaw.triggers.arm import next_fire_after_edit

    rearmed = next_fire_after_edit(before, trigger)
    if rearmed is not None:
        trigger.next_fire_at = rearmed
    saved = store.upsert(trigger)
    # What applies now is read from the row as stored, so the agent repeats the store and not an
    # earlier answer: the update said only "Updated …: delivery.", and the chat went on telling the
    # owner a watch still waited for an Allow they had given since it was made.
    current, errors = _as_stored(store, saved)
    lines = [f"Updated {current.id}: {', '.join(sorted(applied))}."]
    if ROUTE_FIELDS & set(applied):
        lines.extend(f"  {line}" for line in _routes_said(current, chat_channels=chat_channels))
    if note:
        lines.append(f"  {note}")
    else:
        standing = _standing(current, errors)
        lines.append(f"  {standing[:1].upper()}{standing[1:]}.")
    unwritten = report_schedules.adopt(saved)
    if unwritten:
        lines.append(f"  {unwritten}")
    if rejected:
        lines.append(f"  Ignored (not settable via this tool): {', '.join(rejected)}.")
    return AutomationToolResult(
        True,
        "\n".join(lines),
        {
            "trigger": current.to_dict(),
            "rejected": rejected,
            "granted": granted,
            "needs_grant": grants.labels(current),
        },
    )


def set_paused(store: Any, *, trigger_id: str, paused: bool) -> AutomationToolResult:
    """`automation_pause` / `automation_resume`.

    Resume goes through `store.set_enabled`, which REFUSES to enable a row that failed to parse
    (S87). That refusal is surfaced rather than swallowed: silently leaving a "resumed" automation
    disabled is the class of lie this program keeps hunting.

    A resume of a trigger whose `max_fires` budget is SPENT restores the budget. An event trigger
    switches itself off when its last allowance fires ("tell me the NEXT time X"), and resuming it
    without clearing the count would flip `enabled` and change nothing — every later fire would meet
    the budget gate. The person pressing Resume has asked for it to run again.

    A trigger brought over from a legacy store and still waiting for review is REFUSED here, and so
    is any trigger whose action it is not allowed to run (`triggers.grants`): turning one on is the
    owner allowing what it runs, and this function is also the chat's `automation_resume`, where
    the one asking is an agent. The Triggers page's toggle asks the owner, grants, and only then
    calls this.

    A resumed clock trigger with no next fire is ARMED here (`arm.next_fire_after_edit`), for the
    page, the chat and the CLI alike: the clock only fires a trigger that carries one, and the
    chat's resume used to leave a trigger created switched off on and inert until a restart.
    """
    from personalclaw.triggers import grants
    from personalclaw.triggers.legacy_import import needs_review

    row = store.get(trigger_id)
    if row is None:
        return AutomationToolResult(False, f"Error: no automation with id {trigger_id!r}.")
    if not paused and not row.errors:
        if needs_review(row.trigger):
            return _awaiting_review_refusal(row.trigger)
        missing = grants.missing(row.trigger)
        if missing:
            return AutomationToolResult(
                False,
                "Error: " + grants.refusal(row.trigger, missing, elsewhere=True, switching_on=True),
                {"needs_grant": grants.labels(row.trigger)},
            )
    saved = store.set_enabled(trigger_id, not paused)
    if saved is not None and not paused:
        from personalclaw.triggers.arm import next_fire_after_edit
        from personalclaw.triggers.service import budget_spent

        reset = budget_spent(saved)
        if reset:
            saved.run_count = 0
        # Switched on is armed, from whichever surface: a trigger with no next fire is never due.
        rearmed = next_fire_after_edit(row.trigger, saved)
        armed = rearmed is not None and rearmed != saved.next_fire_at
        if armed:
            saved.next_fire_at = str(rearmed)
        if reset or armed:
            store.upsert(saved)
    if saved is not None:
        # A report's automation switched on or off is its report switched on or off.
        from personalclaw.knowledge import report_schedules

        report_schedules.adopt(saved)
    if saved is None:
        # 🔴 `set_enabled` returns None — not a trigger with `enabled` unchanged — when it
        # refuses a broken row. My first draft compared `saved.enabled`, a branch that could
        # never run, so a refused resume would have reported the generic "could not change" with no
        # hint that the row has a parse error the user must fix first.
        if row.errors:
            return AutomationToolResult(
                False,
                f"Error: {trigger_id} could not be resumed — it has a parse error "
                f"({row.errors[0].message}). Fix it first.",
                {"errors": [i.message for i in row.errors]},
            )
        return AutomationToolResult(False, f"Error: could not change {trigger_id!r}.")
    return AutomationToolResult(
        True,
        f"{'Paused' if paused else 'Resumed'} {saved.id} ({saved.name}).",
        {"trigger": saved.to_dict()},
    )


def _awaiting_review_refusal(trigger: Any) -> AutomationToolResult:
    """Why an imported row waiting for review is not switched on or run from here, and where."""
    return AutomationToolResult(
        False,
        f"Error: {trigger.id} ({trigger.name}) was brought over from an older version of "
        "PersonalClaw and has not been allowed to run here. It can be switched on only from the "
        "Triggers page, which shows what it runs and asks first.",
        {"needs_review": True},
    )


def delete(store: Any, *, trigger_id: str, confirm: bool = False) -> AutomationToolResult:
    """`automation_delete` — §4: `(id, confirm: true)`.

    The confirm flag is enforced, not decorative. Deleting an automation the user built and cannot
    recover is exactly the irreversible action a tool call should not be able to take by accident.
    """
    if not confirm:
        return AutomationToolResult(
            False,
            f"Error: deleting {trigger_id!r} needs confirm: true. "
            "Pause it instead if you might want it back.",
        )
    row = store.get(trigger_id)
    if row is None:
        return AutomationToolResult(False, f"Error: no automation with id {trigger_id!r}.")
    name = row.trigger.name
    store.delete(trigger_id)
    from personalclaw.knowledge import report_schedules

    # A report's automation deleted is the report's schedule removed: it stays, unscheduled.
    report_schedules.adopt_removal(row.trigger)
    return AutomationToolResult(True, f"Deleted {trigger_id} ({name}).", {"deleted": trigger_id})


def delete_all(
    store: Any, *, created_by: str = "agent", confirm: bool = False
) -> AutomationToolResult:
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
        return AutomationToolResult(
            False,
            f"Error: deleting every {created_by}-created automation needs confirm: true. "
            "Pause them instead if you might want them back.",
        )
    owned = [row.trigger for row in store.load() if row.trigger.created_by == created_by]
    if not owned:
        return AutomationToolResult(
            True,
            f"No {created_by}-created automations to delete.",
            {"deleted": [], "created_by": created_by},
        )
    from personalclaw.knowledge import report_schedules

    deleted: list[str] = []
    for trigger in owned:
        try:
            store.delete(trigger.id)
            deleted.append(trigger.id)
        except Exception:  # noqa: BLE001 - one undeletable row must not strand the rest
            logger.debug("could not delete %s", trigger.id, exc_info=True)
            continue
        report_schedules.adopt_removal(trigger)
    text = f"Deleted {len(deleted)} {created_by}-created automation(s): {', '.join(deleted)}."
    if len(deleted) != len(owned):
        # Reported, not swallowed: a partial bulk delete that claimed full success would leave the
        # caller believing the list is empty when rows it cannot see are still firing.
        text += f"\n  ⚠️ {len(owned) - len(deleted)} could not be deleted."
    return AutomationToolResult(True, text, {"deleted": deleted, "created_by": created_by})


#: Gates a MANUAL fire may skip,: "bypasses min-interval + max_runs_per_hour, never rate
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
) -> AutomationToolResult:
    """`automation_run` and `automation_dry_run` — §4's manual fire and observe-mode replay.

    A DISABLED automation still runs manually: pausing means "stop firing on your own", and
    refusing a hand-driven run of a paused automation would remove the main way a user tests one
    before re-enabling it. Reported in the result so nobody mistakes it for a resume. What a run
    never bypasses is the grant (`triggers.grants`): a trigger its action is not allowed to run is
    refused, a row a legacy import brought over and the owner has not switched on included.

    `runner` is injected — this tool does NOT own the turn (S90 does). A `dry_run` never calls it
    at all, which is the property that makes observe-mode safe to offer. It answers as the
    gateway's `/run` does, and `ok` is what that answer says (:func:`_run_outcome`): this reported
    `ok` for every answer, a refused or failed run included.
    """
    row = store.get(trigger_id)
    if row is None:
        return AutomationToolResult(False, f"Error: no automation with id {trigger_id!r}.")
    if row.errors:
        return AutomationToolResult(
            False,
            f"Error: {trigger_id} has a parse error and cannot run " f"({row.errors[0].message}).",
            {"errors": [i.message for i in row.errors]},
        )
    plan = manual_gate_plan(dry_run)
    trigger = row.trigger
    lines = [
        f"{'Dry run' if dry_run else 'Manual run'} of {trigger.id} "
        f"({redact_for_display(trigger.name or '')}).",
        f"  gates enforced: {', '.join(plan['enforced'])}",
        f"  bypassed (manual): {', '.join(plan['bypassed']) or 'none'}",
    ]
    from personalclaw.triggers import grants

    missing = grants.missing(trigger)
    if not trigger.enabled:
        lines.append("  note: this automation is paused — running it here does not re-enable it.")
    if dry_run:
        if missing:
            lines.append(f"  note: a real run is refused: {grants.refusal(trigger, missing)}")
        # An automation saved before its command matched a pattern (one added since, or an import).
        if refused := _refused_command(trigger.workflow):
            lines.append(f"  note: a real run is refused. {refused}")
        lines.append("  nothing was executed.")
        # The trigger as a read shows it, masked: a dry run is a read of the automation, and its
        # answer reaches the page and the chat that asked.
        return AutomationToolResult(
            True,
            "\n".join(lines),
            {"plan": plan, "trigger": redact_values_for_display(trigger.to_dict())},
        )
    # 🔴 The gates the plan claims to enforce, actually enforced. Below the dry-run return so a dry
    # run still REPORTS the plan during an incident (that is a read, and telling an operator what
    # would happen is the opposite of running unattended work).
    refusal = manual_refusal()
    if refusal:
        lines.append(f"  refused: {refusal}")
        return AutomationToolResult(False, "\n".join(lines), {"plan": plan, "refused": refusal})
    # Not the agent's to run before the owner has allowed it (`triggers.legacy_import`,
    # `triggers.grants`); the HTTP route the runner posts to refuses it too.
    from personalclaw.triggers.legacy_import import needs_review

    if needs_review(trigger):
        return _awaiting_review_refusal(trigger)
    if missing:
        lines.append(f"  refused: {grants.refusal(trigger, missing, elsewhere=True)}")
        return AutomationToolResult(
            False, "\n".join(lines), {"plan": plan, "needs_grant": grants.labels(trigger)}
        )
    if runner is None:
        # Honest refusal rather than a fabricated success. "Launched" with nothing behind it is the
        # fire-and-forget lie the executor was written to keep out of this codebase.
        lines.append("  no runner is wired in this context, so nothing was executed.")
        return AutomationToolResult(False, "\n".join(lines), {"plan": plan})
    result = runner({"trigger_id": trigger.id, "workflow": dict(trigger.workflow)})
    ran, line = _run_outcome(result)
    # Masked like the name above: the route's sentence can quote the automation.
    lines.append(redact_for_display(line))
    return AutomationToolResult(ran, "\n".join(lines), {"plan": plan, "result": result})


def _run_outcome(result: Any) -> tuple[bool, str]:
    """Whether a runner's answer says the automation ran, and the line that says what happened.

    The runner answers as the gateway's `/run` does: `ok` for whether the action ran, with its
    `result`; `refused` for a gate that stopped it; `error` for a request it could not serve (and
    for a gateway it could not reach). Only an answer that says it ran is a run.
    """
    if not isinstance(result, dict):
        return False, f"  failed: {result}"
    if result.get("error"):
        return False, f"  failed: {result['error']}"
    if result.get("refused"):
        return False, f"  refused: {result['refused']}"
    if result.get("ok") is True:
        return True, f"  result: {result.get('result') or 'ran'}"
    return False, f"  did not run: {result.get('result') or 'the run did not start'}"


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
) -> AutomationToolResult:
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
        return AutomationToolResult(False, f"Error: no automation with id {trigger_id!r}.")
    from personalclaw.triggers.history import feed_response, unified_feed

    records = unified_feed(
        schedule_runs=schedule_runs,
        hooks=hooks,
        limit=max(1, n) * 10,
    )
    mine = [r for r in records if _same_trigger(r.trigger_id, trigger_id)][: max(1, n)]
    if not mine:
        return AutomationToolResult(
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
    return AutomationToolResult(
        True,
        f"{trigger_id} — last {len(mine)} run(s):\n" + "\n".join(lines),
        {"trigger_id": trigger_id, **feed_response(mine)},
    )
