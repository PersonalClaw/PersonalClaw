"""Rung ROUTING — what each earned-autonomy rung actually does at a dispatch seam
(AUTONOMY-GUARDRAILS §5.2).

:mod:`~personalclaw.guardrails.autonomy` is the decision layer: it holds the ladder, the
declarations, the grants and the derived track record. It shipped with **no call sites**,
which makes it a decision object nothing consults. This module is the other half — the
one the dispatch seams call — and it answers one question:

    given a provider-dispatched action, may it run right now, and what does the user see?

**The four rungs, as behaviour:**

======================  ==========================================================
``draft_only``          Do not execute. File a PROPOSAL inbox item describing what
                        the action would have done.
``one_tap``             Do not execute. File an agent-request inbox item so the
                        user can decide. (The one-tap CARD is AG-8's frontend; the
                        durable row it renders is raised here.)
``auto_with_undo``      Execute, then persist the provider's reversal handle (SEL +
                        the notification's ``meta``) and passively notify — but only
                        when there IS a handle. A notification offering an undo that
                        cannot happen is worse than silence.
``autonomous``          Execute. The SEL row the action already writes is the record.
======================  ==========================================================

**How a declaration reaches a seam with no per-action branching.** A seam holds a
provider NAME (``hook.provider`` / ``trigger.action_provider``) and nothing else. The
name→type mapping lives on the DECLARATION (``ActionTypeSpec.providers``), so
:func:`route_provider_action` is the same three lines for ``bash`` and for an
app-contributed provider nobody in core has heard of. There is no ``if key == …`` at
either seam, and adding a governed action is data.

**An UNDECLARED provider keeps its pre-ladder behaviour.** ``route`` is ``execute`` and
``governed`` is False. This is deliberate and is not a hole: the denylist, the incident
kill switch, the injection screen and the creation-time capability grant all still run —
they are the floor this ladder sits on top of. Treating every undeclared provider as
``draft_only`` would withhold every hook and trigger in the tree, which is an outage
wearing a safety control's clothes. What DOES fail closed is a *declared* key with no
registration and a *granted* rung that cannot be proven: ``resolve_rung`` answers
``draft_only`` for both.

**Two levels, tightest wins** (PLATFORM-HARDENING-FLOORS §5). Level one is the type's own
ceiling, enforced by ``resolve_rung``. Level two is
:func:`~personalclaw.guardrails.policy.rung_ceiling_for_profile`, which may only NARROW —
so an unattended run cannot reach ``autonomous`` and go silent, whatever the type's
declaration says. Neither level can widen the other.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from personalclaw.guardrails.autonomy import (
    RUNG_AUTO_WITH_UNDO,
    RUNG_AUTONOMOUS,
    RUNG_DRAFT_ONLY,
    RUNG_ONE_TAP,
    RUNGS,
    ActionTypeSpec,
    action_type_for_provider,
    register_action_type,
    resolve_rung,
    rung_rank,
)

logger = logging.getLogger(__name__)

# ── routes (what a seam DOES) ─────────────────────────────────────────────────

#: Execute now; the action's own SEL row is the record.
ROUTE_EXECUTE = "execute"
#: Execute now, then persist the reversal handle + passively notify.
ROUTE_EXECUTE_WITH_UNDO = "execute_with_undo"
#: Withhold; raise an agent-request row for the user to decide.
ROUTE_ASK = "ask"
#: Withhold; raise a proposal row describing what would have happened.
ROUTE_DRAFT = "draft"

_ROUTE_BY_RUNG: dict[str, str] = {
    RUNG_DRAFT_ONLY: ROUTE_DRAFT,
    RUNG_ONE_TAP: ROUTE_ASK,
    RUNG_AUTO_WITH_UNDO: ROUTE_EXECUTE_WITH_UNDO,
    RUNG_AUTONOMOUS: ROUTE_EXECUTE,
}

#: The human name of each rung, said in terms of BEHAVIOUR rather than of the ladder —
#: "runs on its own" is what a user needs to know; "autonomous" is what the code calls it.
#: Defined here, next to the routes it describes, and served to the frontend by
#: ``GET /api/autonomy`` so a rung cannot be called one thing in a chip and another in the
#: proposal that offered it (the catalog-over-mirror rule the trigger UI already follows).
RUNG_LABELS: dict[str, str] = {
    RUNG_DRAFT_ONLY: "drafts only",
    RUNG_ONE_TAP: "asks first",
    RUNG_AUTO_WITH_UNDO: "runs with undo",
    RUNG_AUTONOMOUS: "runs on its own",
}


def rung_label(rung: str) -> str:
    """The rung in the words a USER reads, falling back to the key if it is unknown.

    The one accessor for ``RUNG_LABELS``, because "autonomous" is what the code calls a rung and
    "runs on its own" is what a person needs to know — and every sentence a user reads has to pick
    the second. Three copies of ``RUNG_LABELS.get(x, x)` had grown up in as many modules; prose
    composers call this instead. Machine-facing strings deliberately keep the key: an audit row, a
    dedup key, a ``ValueError`` for a developer, and the echo of a rung a caller supplied.
    """
    return RUNG_LABELS.get(rung, rung)


#: What each rung DOES at a dispatch seam, in one sentence — the table at the top of this
#: module, in the words the ladder panel shows.
RUNG_HINTS: dict[str, str] = {
    RUNG_DRAFT_ONLY: "Never executes on its own. Files a proposal describing what it would do.",
    RUNG_ONE_TAP: "Never executes on its own. Raises a request for you to decide.",
    RUNG_AUTO_WITH_UNDO: "Executes, then tells you and keeps a handle so you can undo it.",
    RUNG_AUTONOMOUS: "Executes silently. The audit log is the record.",
}


@dataclass(frozen=True)
class RungRoute:
    """One routing decision: the rung that applies and what the seam should do with it."""

    route: str = ROUTE_EXECUTE
    key: str = ""
    rung: str = ""
    reason: str = ""

    @property
    def governed(self) -> bool:
        """Whether a declaration claimed this action at all."""
        return bool(self.key)

    @property
    def executes(self) -> bool:
        return self.route in (ROUTE_EXECUTE, ROUTE_EXECUTE_WITH_UNDO)

    @property
    def records_reversal(self) -> bool:
        return self.route == ROUTE_EXECUTE_WITH_UNDO


# ── core declarations ─────────────────────────────────────────────────────────

# Every built-in provider-dispatched action, one spec each, keyed ``action.<provider>``.
#
# 🔴 READ THIS BEFORE CHANGING A FLOOR. These actions ALREADY run unattended today: their
# authority is the creation-time grant on the hook/trigger plus the decision-7 capability
# fence (`triggers/screen.py`), and the ladder was added ON TOP of that floor, never under
# it. So the honest floor for an action that runs today is the rung that matches today's
# behaviour, and declaring one lower would not "harden" anything — it would stop a user's
# existing automations. The recorded lesson is `enforcing a dead control is an outage`.
#
# What the declarations therefore buy is the other three columns: the CEILING (a bound a
# grant can never pass), `leaves_machine` (which keeps `promotion_eligibility` from ever
# proposing `autonomous` for an effect that escapes this machine), and the inventory the
# ladder panel enumerates. A machine-leaving core action that declares
# `ceiling=autonomous` below is making the deliberate, in-tree ceiling raise the ladder
# asks for — the same claim from an app manifest is clamped (`clamp_untrusted_ceiling`).
_PROVIDER_SPECS: tuple[ActionTypeSpec, ...] = (
    # Read-only / observe: nothing to reverse and nothing to tell.
    ActionTypeSpec(
        key="action.notify",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        providers=("notify",),
    ),
    ActionTypeSpec(
        key="action.knowledge_read",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        providers=("knowledge-retrieve", "knowledge-health", "knowledge-gaps"),
    ),
    ActionTypeSpec(
        key="action.artifact_inspect",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        providers=("artifact_inspect",),
    ),
    # SELF-VERIFICATION §3.2 step 1. Its own key rather than sharing `action.notify`'s:
    # reading a git working tree is a different governed behavior from writing a dashboard
    # row, and the shared-key convention below is for a second NAME for one behavior, not for
    # two behaviors that happen to be equally harmless. Autonomous at both ends because the
    # effect is a run-ledger row and nothing else — there is nothing to reverse and nothing
    # to tell the user about.
    ActionTypeSpec(
        key="action.selfqa_triage",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        providers=("selfqa-triage",),
    ),
    # Local writes: the effect stays on this machine, where the user can see and undo it.
    ActionTypeSpec(
        key="action.create_task",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        # `selfqa-file-finding` (SELF-VERIFICATION §3.2) shares this class rather than minting
        # its own key, on the same reasoning as `knowledge-report` below: what it ultimately
        # does is file a task through the same native provider listed beside it, plus one local
        # inbox row. Both effects stay on this machine and both are visible where the user
        # already looks, so a second key would be a second name for one governed behavior.
        providers=("create-task", "selfqa-file-finding"),
    ),
    ActionTypeSpec(
        key="action.knowledge_write",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        # WF2KNO-12's `knowledge-report` shares this class rather than minting its own key: what it
        # ultimately does is write a knowledge item, through the same persist provider listed beside
        # it. Its extra powers (it spends a model call, and it fires on a schedule) are governed
        # where they can be evaluated — the write-capable fence in `triggers/screen.py` and the
        # report's own iteration cap — not by a second name for one governed behavior.
        # WS-7's `source-digest` shares it on the same reasoning as `knowledge-report`, and NOT
        # `action.digest`: that class is documented as *deterministic, no-model, local-only*
        # notification writers, and the source digest is a model call over scraped text. What it
        # ultimately does is write ONE knowledge item, through the same store — plus one inbox
        # notification, which `knowledge-propose` beside it already does. Its extra powers (the
        # model call, the cron) are governed where they can be evaluated: the write-capable fence
        # in `triggers/screen.py` and `MAX_DIGEST_ITEMS`.
        providers=(
            "knowledge-persist",
            "knowledge-consolidate",
            "knowledge-propose",
            "knowledge-report",
            "source-digest",
        ),
    ),
    # LV-4's `identity-report` shares this class rather than minting its own, on the reasoning the
    # sibling comments use: what it ultimately does is write ONE versioned artifact, through the
    # same store as `render-report` beside it — which is also a scheduled report, also persists a
    # document, and also surfaces it. The inbox row the report raises is a POINTER at that
    # artifact, not a second governed effect, and `action.digest`'s members already treat one
    # local notification write as inside their class. Its extra power (one background model call
    # over fenced prose) is governed where it can be evaluated: the write-capable fence in
    # `triggers/screen.py` and `_MAX_NARRATIVE_CHARS`.
    ActionTypeSpec(
        key="action.artifact_write",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        providers=("artifact-update", "render-report", "identity-report"),
    ),
    # `usage-recap` (MRT-3) shares this class rather than minting its own: both are
    # local-only notification writers, so a separate key would be a second name for one governed
    # behavior. Nothing here leaves the machine.
    #
    # PA-2's `triage-digest` joins on the same reasoning `source-digest` uses in the
    # knowledge-write class above, and the reasoning is worth stating because it CORRECTS this
    # comment's earlier "deterministic, no-model" wording: what this class governs is the EFFECT
    # (one local notification write), not the powers used to compose it. `triage-digest` spends
    # model calls — a classifier gate and one proposal pass — so the class is no longer
    # uniformly no-model. Those extra powers are governed where they can be evaluated: the
    # write-capable fence in `triggers/screen.py`, the gate's own drop path, and the spend
    # callers registered in `guardrails/audit.py`. Minting `action.triage` instead would be a
    # second name for "writes one notification", which is exactly what the sibling comments
    # above refuse to do.
    ActionTypeSpec(
        key="action.digest",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        providers=("notification-digest", "usage-recap", "triage-digest"),
    ),
    # PLATFORM-RESILIENCE §4.3 (PR2-8): the health-scored remediation engine, driven by one
    # adaptive-clock trigger. Its OWN key rather than sharing `action.digest`'s, on the same
    # reasoning the sibling comments use in reverse: this is a different governed BEHAVIOR, not a
    # second name for one. A digest writes one local row; this deletes aged history files, prunes
    # the security event log and rebuilds search indexes.
    #
    # `autonomous` at the floor because the table's own rule forces it: the engine has run
    # unattended on every tick since PR2-5 (as `HeartbeatService._maybe_remediate`), so a lower
    # floor would not harden anything — it would stop the maintenance a live install depends on.
    # The lesson cited at the top of this table, `enforcing a dead control is an outage`, is exactly
    # this case. The ceiling is the same rung and `leaves_machine` is False: every job it runs
    # writes local state and nothing escapes the machine.
    ActionTypeSpec(
        key="action.self_remediation",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        providers=("self-remediation",),
    ),
    # PA-3's `inbox-op`. The ONE core provider that does not floor at `autonomous`, and the
    # reason is the paragraph at the top of this table read forwards instead of backwards: that
    # reasoning says an action which ALREADY runs unattended must declare the rung matching
    # today's behaviour, because a lower floor would stop a user's existing automations.
    # `inbox-op` runs nothing today — it is new in this commit — so there are no automations to
    # stop, and the floor can be the one the behaviour deserves rather than the one history
    # forces. `auto_with_undo` is that rung: PROACTIVE-ASSISTANT §1.6 bound 4 requires every
    # auto-executed inbox operation to keep a handle the user can click, and this floor is what
    # routes it through `ROUTE_EXECUTE_WITH_UNDO` so the handle is persisted and the user told.
    #
    # The CEILING is the same rung, which is the load-bearing half: `autonomous` would let an
    # accumulated track record eventually take the undo offer AWAY, and "archived 40 things
    # silently" is the exact outcome the trivial tier's reversibility argument depends on not
    # happening. `leaves_machine` is False — every op writes a local row and nothing else; a
    # `reply_draft` writes a DRAFT (the provider has no send path at all), so the type that
    # marks `leaves_machine` for a reply is `inbox.reply_draft` below, not this one.
    ActionTypeSpec(
        key="action.inbox_op",
        floor=RUNG_AUTO_WITH_UNDO,
        ceiling=RUNG_AUTO_WITH_UNDO,
        providers=("inbox-op",),
    ),
    # Spawns an LLM turn. `leaves_machine` because the turn's own toolset can reach the
    # network — the profile it runs under bounds that, not this declaration.
    # EI-7's `second-opinion` shares this class rather than minting its own key, on the same
    # reasoning the siblings above use: what it ultimately does is spawn ONE headless LLM turn —
    # a cataloged runner one-shot, or a subagent when the exclusion leaves no eligible runner —
    # and a second key would be a second name for one governed behavior. Its extra powers are
    # governed where they can be evaluated: the write-capable fence in `triggers/screen.py`, the
    # hard per-handoff timeout, and the disk re-diff that must confirm the edits before the
    # result is accepted at all.
    ActionTypeSpec(
        key="action.spawn_turn",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        leaves_machine=True,
        providers=("run-prompt", "invoke-agent", "run-workflow", "second-opinion"),
    ),
    # BROWSE-AUTOMATION §9 (BA-3): drives a real browser, so its effect is a click and a form
    # POST on somebody else's site. `one_tap` at BOTH ends, which is the only spec here that
    # is not `autonomous`, and deliberately so:
    #   * not `autonomous` — a SUBMIT is an irreversible external write with no undo handle
    #     (`reversal_kinds` is empty), so the silent rung would run it and leave nothing a user
    #     would notice.
    #   * not `draft_only` — that is BA-6's registration decision for a persisted browse PLAN;
    #     applying it to the PROVIDER would withhold every browse dispatch at every trigger
    #     seam before the atom that could promote it exists, which is a shipped-and-inert
    #     capability wearing a control's clothes.
    #   * ceiling equals the floor because widening it needs the reversal half BA-6 owns; a
    #     ceiling of `auto_with_undo` above a provider that cannot undo would route a form
    #     submission to "executes, then keeps a handle" and keep no handle.
    # Workflow ACTION NODES are unaffected — `workflows.engine.dispatch_action` does not
    # consult the ladder, which is why the engine path and the trigger path read differently.
    ActionTypeSpec(
        key="action.browse",
        floor=RUNG_ONE_TAP,
        ceiling=RUNG_ONE_TAP,
        leaves_machine=True,
        providers=("browse",),
    ),
    # Executes author-supplied code: arbitrary shell / sandboxed Python. The denylist and
    # the capability fence are what license these; the ladder does not add a gate.
    ActionTypeSpec(
        key="action.execute_code",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        leaves_machine=True,
        providers=("bash", "run-script"),
    ),
    # Delivers to somewhere the user does not control: a channel, or an app route whose
    # own permissions decide where it lands.
    ActionTypeSpec(
        key="action.send_message",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        leaves_machine=True,
        providers=("send-message",),
    ),
    ActionTypeSpec(
        key="action.call_app_route",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
        leaves_machine=True,
        providers=("call-app-route",),
    ),
)

# The AI affordances (`inbox_service.draft_reply` / `.classify`,
# `dashboard.chat_title._apply_auto_tags`). No `providers`: nothing dispatches these
# through the action-provider registry, so they are named directly.
#
# `inbox.reply_draft` is the one core type whose floor is genuinely the BOTTOM rung, and
# its declaration is the whole control: an AI-drafted reply is written and shown, never
# sent, and its `one_tap` ceiling plus `leaves_machine` mean no accumulated track record
# can ever propose sending one by itself. The send affordance that turns an earned
# `one_tap` into a card — and the approval verdict that would be its evidence — is AG-8.
_AFFORDANCE_SPECS: tuple[ActionTypeSpec, ...] = (
    ActionTypeSpec(
        key="inbox.reply_draft",
        floor=RUNG_DRAFT_ONLY,
        ceiling=RUNG_ONE_TAP,
        leaves_machine=True,
    ),
    ActionTypeSpec(
        key="inbox.classify",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
    ),
    ActionTypeSpec(
        key="sessions.auto_tag",
        floor=RUNG_AUTONOMOUS,
        ceiling=RUNG_AUTONOMOUS,
    ),
)

#: Every core declaration, in one tuple, so the ladder panel can enumerate the governed
#: inventory without importing the dashboard or the inbox service.
CORE_ACTION_TYPES: tuple[ActionTypeSpec, ...] = (*_PROVIDER_SPECS, *_AFFORDANCE_SPECS)


def ensure_core_action_types() -> None:
    """Register every core declaration. Idempotent — registration replaces by key.

    Called from the provider-registration seam
    (``action_providers.registry._ensure_default_providers_registered``) and from the
    inbox AI affordances, so the governed inventory is complete in any process that can
    dispatch an action, not only in one that already has.
    """
    for spec in CORE_ACTION_TYPES:
        register_action_type(spec)


# ── routing ───────────────────────────────────────────────────────────────────


def route_action_type(key: str, *, session_key: str = "") -> RungRoute:
    """The route for a declared action type, composed with the run's SafetyProfile.

    ``resolve_rung`` gives the type's own answer (floor + accepted grant, clamped to its
    ceiling, clamped again to ``one_tap`` during an incident). The profile then NARROWS it
    and can never widen it — the lower of the two wins.
    """
    from personalclaw.guardrails.policy import profile_for_session, rung_ceiling_for_profile

    rung = resolve_rung(key)
    profile = profile_for_session(session_key)
    ceiling = rung_ceiling_for_profile(profile)
    effective = RUNGS[min(max(rung_rank(rung), 0), max(rung_rank(ceiling), 0))]
    # 🪤 THIS SENTENCE IS USER COPY, AND IT IS ALWAYS EMBEDDED. `announce_withheld` puts it in the
    # body of the inbox row a held action raises, the seams put it in a hook/trigger error, and
    # `triggers/executor` puts it in a run summary — six call sites, and every one of them has
    # ALREADY named the action:
    #
    #     The 'acme-file-task' action on trigger t-acme did not run: {reason}.
    #     held for your approval: {reason}
    #
    # So naming the action type here said it twice, the second time as a code identifier the user
    # has never seen — `app:acme.acme-file-task`, or worse a DIFFERENT name for the thing the
    # sentence just called `'bash'` (`action.execute_code`). The key stays where it belongs: on the
    # row's `refs["action_type"]`, in the dedup key, and on `RungRoute.key` for any caller that
    # wants it. "This action" is the subject `_authority_sentence` already uses for the same job.
    reason = f"this action {rung_label(rung)}"
    if effective != rung:
        reason = (
            f"this action {rung_label(rung)}, narrowed so it {rung_label(effective)} "
            f"by the {profile.name} profile"
        )
    return RungRoute(
        route=_ROUTE_BY_RUNG.get(effective, ROUTE_DRAFT),
        key=key,
        rung=effective,
        reason=reason,
    )


def route_provider_action(provider_name: str, *, session_key: str = "") -> RungRoute:
    """The route for a provider-dispatched action — the seam-facing entry point.

    An UNDECLARED provider routes to ``execute`` with ``governed`` False: the ladder
    governs what a declaration claimed, and the pre-ladder floor (denylist, incident,
    capability fence, creation-time grant) governs the rest.

    Core declarations are (re)registered here rather than assumed. The seams reach this
    function from paths that do NOT all go through provider registration — an event
    trigger resolves an already-registered provider directly — and a missing declaration
    reads as "ungoverned", which is the one fail-OPEN this function could have. Making it
    self-sufficient costs a dozen dict writes per dispatch and removes that whole class.
    """
    ensure_core_action_types()
    spec = action_type_for_provider(provider_name)
    if spec is None:
        return RungRoute(
            route=ROUTE_EXECUTE,
            reason=f"no action type declares provider {provider_name!r}",
        )
    return route_action_type(spec.key, session_key=session_key)


# ── the withhold surface (draft_only / one_tap) ────────────────────────────────

# The registered notification pairs a withheld action borrows. Neither is new on purpose:
# a `proposal` and an `agent_request` are concepts the user already has one delivery rule
# for, and registering a third would hand them two switches for one idea (the reasoning
# `session_organize` recorded when it reused the proposal pair).
_WITHHOLD_SURFACE: dict[str, tuple[str, str, str]] = {
    # route → (notification source, notification kind, inbox item_kind)
    ROUTE_DRAFT: ("skills", "proposal", "proposal"),
    ROUTE_ASK: ("system", "agent_request", "agent_request"),
}


def announce_withheld(
    route: RungRoute,
    *,
    title: str,
    body: str = "",
    refs: dict | None = None,
    dedup_key: str = "",
) -> str:
    """Raise the durable row for an action the ladder withheld. Returns the item id.

    ``draft_only`` files a proposal ("here is what it would have done"); ``one_tap`` files
    an agent request ("decide"). Both carry the action-type key in ``refs`` so AG-8's card
    and the ladder panel can find every held action of one type.

    Deduped per action type by default: a trigger that matches every thirty seconds must
    not stack a hundred identical rows, and a held action is one standing request however
    many times it was attempted.

    Best-effort. A withheld action is already withheld — failing to *announce* it must not
    turn into an exception at the seam, which would be a strictly worse outcome than a
    missing row.
    """
    surface = _WITHHOLD_SURFACE.get(route.route)
    if surface is None:
        return ""
    source, kind, item_kind = surface
    try:
        from personalclaw.inbox import emit_attention_item
        from personalclaw.inbox_providers.native_source import get_dashboard_state

        try:
            state = get_dashboard_state()
        except Exception:  # noqa: BLE001 - headless: the row still persists
            state = None
            logger.debug("withheld action: no dashboard state", exc_info=True)
        return emit_attention_item(
            state,
            source=source,
            kind=kind,
            item_kind=item_kind,
            title=title,
            body=body,
            refs={"action_type": route.key, "rung": route.rung, **dict(refs or {})},
            dedup_key=dedup_key or f"autonomy_hold:{route.key}",
        )
    except Exception:  # noqa: BLE001
        logger.warning("withheld action: could not raise an inbox row", exc_info=True)
        return ""


# ── the undo surface (auto_with_undo) ─────────────────────────────────────────


def record_reversal(route: RungRoute, result: Any, *, label: str, refs: dict | None = None) -> str:
    """Persist the reversal handle an ``auto_with_undo`` action came back with.

    The handle itself is the PROVIDER's: only it knows what "undo" means for its own
    effect (``ActionResult.reversal``, e.g. the task row a ``create-task`` filed). This
    persists it in the three places the undo click needs — the SEL row (audit), the
    reversal record store (``guardrails.ladder``, which is what
    :func:`~personalclaw.guardrails.ladder.reverse_action` resolves an id against) and the
    notification's ``meta`` (which carries the record id, so the affordance is rendered
    from persisted state rather than from a handle sitting in a page) — and returns the
    handle it recorded.

    **No handle, no notification.** A provider that cannot reverse itself leaves
    ``reversal`` empty, and then the passive notify is skipped entirely: an "undo
    available" notice for an action that cannot be undone is a promise the product cannot
    keep, and it would also mean every unattended fire in the tree grew a notification
    overnight. The SEL row is still written, so the execution is auditable either way.
    """
    handle = str(getattr(result, "reversal", "") or "")
    meta = {"action_type": route.key, "rung": route.rung, **dict(refs or {})}
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller=f"autonomy:{route.key}",
            operation="guardrails.autonomy_executed",
            outcome="ok",
            source="guardrails",
            resources=f"rung={route.rung} reversal={handle or 'none'}"[:200],
        )
    except Exception:  # noqa: BLE001
        logger.debug("reversal SEL audit failed", exc_info=True)
    if not handle:
        return ""
    # The durable record the undo click acts on. A refused handle yields no record id, and
    # then the notification says the action ran WITHOUT offering an undo — the same "never
    # promise a reversal that cannot happen" rule as the no-handle case above, applied one
    # level down to a handle the store could not accept.
    record_id = ""
    try:
        from personalclaw.guardrails.ladder import record_reversal_handle

        record_id = record_reversal_handle(
            action_type=route.key, rung=route.rung, handle=handle, label=label
        )
    except Exception:  # noqa: BLE001
        logger.warning("could not record a reversal handle for %s", route.key, exc_info=True)
    try:
        from personalclaw import notification_kinds
        from personalclaw.action_providers.services import get_action_services

        services = get_action_services()
        state = getattr(services, "state", None) if services is not None else None
        if state is not None:
            state.notify(
                notification_kinds.INFO,
                "An automatic action ran",
                (
                    f"{label} ran on its own. You can still undo it."
                    if record_id
                    else f"{label} ran on its own."
                ),
                meta={**meta, "reversal": handle, "reversal_id": record_id},
            )
    except Exception:  # noqa: BLE001
        logger.debug("reversal notify failed", exc_info=True)
    return handle
