"""In-process registry of action providers."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from personalclaw.action_providers.base import ActionProvider
    from personalclaw.tool_providers.base import RiskLevel


_providers: "dict[str, ActionProvider]" = {}


@dataclass(frozen=True)
class ActionOrigin:
    """The app that registered an action in this process, as its owner reads them both."""

    app: str
    app_label: str
    label: str


#: Action name → the app that registered it in this process, KEPT after the app's provider goes
#: (a disable, a removal, an update that no longer provides it): the one place the app is known
#: is its registration, and a fire that finds the action gone names the app it came from
#: (`triggers.cannot_run`). A core provider registering the name clears it.
_origins: dict[str, ActionOrigin] = {}


def register_action_provider(
    provider: "ActionProvider", *, app: str = "", app_label: str = ""
) -> None:
    """Register *provider* under its name; *app* (shown as *app_label*) is the installed app it
    comes from, "" for core's own."""
    name = provider.name
    _providers[name] = provider
    if app:
        label = str(getattr(provider, "display_name", "") or "") or name
        _origins[name] = ActionOrigin(app=app, app_label=app_label or app, label=label)
    else:
        _origins.pop(name, None)


def action_origin(name: str) -> ActionOrigin | None:
    """The app that registered the action *name* in this process, or None: a core action, or one
    no app registered since this process started."""
    return _origins.get(name)


def get_action_provider(name: str) -> "ActionProvider | None":
    return _providers.get(name)


def list_action_providers() -> list[str]:
    return list(_providers.keys())


def action_effect(name: str, action_config: "dict[str, Any] | None" = None) -> "RiskLevel":
    """What running action *name* with *action_config* does, as its provider declares it
    (:meth:`ActionProvider.effect`). A change (``CAUTION``) for a provider nothing dispatches, and
    for one whose declaration raises: an action nobody declared is never taken for a read."""
    from personalclaw.tool_providers.base import RiskLevel

    _ensure_default_providers_registered()
    provider = _providers.get(name)
    if provider is None:
        return RiskLevel.CAUTION
    try:
        return provider.effect(dict(action_config or {}))
    except Exception:  # noqa: BLE001 - a broken declaration reads as the change it may be
        return RiskLevel.CAUTION


def dispatchable_action_providers() -> frozenset[str]:
    """Every provider name an action could reach, for a READ-ONLY surface (#779).

    The set form of the predicate the trigger write path refuses against
    (``triggers.tools.unregistered_action_provider_refusal``), so a doctor and a create form can
    never disagree about whether a name is real. Ensures the built-ins are registered first, for the
    reason ``dashboard.handlers.doctor._observe_mode_fact`` records: they register lazily on first
    action execution, so a caller that skipped it would read an EMPTY registry and report every
    automation as unknown.

    Not ``validation.ALLOWED_HOOK_PROVIDERS``: that static allowlist is the LIFECYCLE-hook door's
    answer and deliberately carries names core never registers (``webhook``, ``a2a-call`` arrive
    with first-party app bundles). A trigger naming one of those still cannot dispatch until the app
    that provides it loads, which is exactly what this surface exists to say.
    """
    _ensure_default_providers_registered()
    return frozenset(_providers)


def _ensure_default_providers_registered() -> None:
    """Idempotent registration of the built-in providers.

    Called from `personalclaw.hooks` on first action execution so the providers
    are available even if no startup hook has registered them yet (tests,
    CLI invocations). These are intrinsic actions (not optional add-ons) — the
    script-hooks / triggers runtime resolves them by name (``bash`` is the default
    hook backend, ``run-script`` the deterministic script action) — so they register
    unconditionally and stay core-native.

    ``webhook`` (a self-contained HTTP-POST adapter that NO core runtime depends on)
    moved to a standalone app (apps/webhook-action) and registers via the app loader
    when installed. The four native actions (notify / send-message / create-task /
    invoke-agent) reach in-process services via the action service accessor.

    Registering a provider also registers its AUTONOMY DECLARATION (AUTONOMY-GUARDRAILS
    §5.2): the two paths must not be able to drift, because a provider present in the
    dispatch registry with no declaration behind it is exactly the case the dispatch seams
    cannot tell apart from an ungoverned action. ``guardrails.rungs.CORE_ACTION_TYPES`` is
    the table; ``test_guardrails_rung_routing`` asserts every built-in name here appears in
    it, so adding a provider without a declaration reds the build.
    """
    from personalclaw.guardrails.rungs import ensure_core_action_types

    ensure_core_action_types()
    if "bash" not in _providers:
        from personalclaw.action_providers.bash_provider import BashActionProvider

        register_action_provider(BashActionProvider())
    if "run-script" not in _providers:
        from personalclaw.action_providers.run_script_provider import RunScriptActionProvider

        register_action_provider(RunScriptActionProvider())
    if "notify" not in _providers:
        from personalclaw.action_providers.notify_provider import NotifyActionProvider

        register_action_provider(NotifyActionProvider())
    if "send-message" not in _providers:
        from personalclaw.action_providers.send_message_provider import SendMessageActionProvider

        register_action_provider(SendMessageActionProvider())
    if "notification-digest" not in _providers:
        from personalclaw.action_providers.digest_provider import (
            NotificationDigestActionProvider,
        )

        register_action_provider(NotificationDigestActionProvider())
    if "usage-recap" not in _providers:
        from personalclaw.action_providers.usage_recap_provider import UsageRecapActionProvider

        register_action_provider(UsageRecapActionProvider())
    if "self-remediation" not in _providers:
        # The health-scored remediation engine, re-homed off the
        # heartbeat onto its own adaptive-clock trigger. Registered unconditionally rather than
        # behind `resilience.remediation.enabled`, because the trigger row exists either way (the
        # reconciler disables it instead of deleting it, so a user can see the switch) and a live
        # row naming an unregistered provider validates, saves, and then fails at fire time. Added
        # to `ALLOWED_HOOK_PROVIDERS` and to `triggers/screen.py`'s write-capable set in the SAME
        # commit — a provider in one set but not the others is that same save-then-refuse mismatch.
        from personalclaw.action_providers.remediation_provider import (
            SelfRemediationActionProvider,
        )

        register_action_provider(SelfRemediationActionProvider())
    if "heartbeat-tasks" not in _providers:
        # The HEARTBEAT.md task queue, re-homed off the heartbeat loop onto its own system trigger
        # so the Triggers page lists it. Registered unconditionally for the reason
        # `self-remediation` records directly above: the row exists whether it is switched on or
        # off, and a live row naming an unregistered provider saves and then fails at fire time.
        # Added to `ALLOWED_HOOK_PROVIDERS`, to `triggers/screen.py`'s write-capable set and to
        # `guardrails/rungs.py`'s action table in the SAME commit.
        from personalclaw.action_providers.heartbeat_tasks_provider import (
            HeartbeatTasksActionProvider,
        )

        register_action_provider(HeartbeatTasksActionProvider())
    if "identity-report" not in _providers:
        # The periodic "how I've adapted to you" report.
        # Registered unconditionally rather than behind the cadence, for the reason
        # `self-remediation` records directly above: the trigger row exists either way (the
        # reconciler disables it for `off` instead of deleting it, so the switch stays visible)
        # and a live row naming an unregistered provider validates, saves, and then fails at fire
        # time. Added to `ALLOWED_HOOK_PROVIDERS`, to `triggers/screen.py`'s write-capable set and
        # to `guardrails/rungs.py`'s action table in the SAME commit.
        from personalclaw.action_providers.identity_report_provider import (
            IdentityReportActionProvider,
        )

        register_action_provider(IdentityReportActionProvider())
    if "source-digest" not in _providers:
        # WATCHED-SOURCES §6.2 (the caller). Registered unconditionally, not behind
        # `sources.enabled`: the bundled clock trigger that names it exists whether or not a
        # user has enrolled a source, and a provider a live trigger names must be dispatchable
        # (the digest itself no-ops on an empty window). A trigger pointing at an unregistered
        # provider validates, saves, and then fails at fire time.
        from personalclaw.action_providers.source_digest_provider import (
            SourceDigestActionProvider,
        )

        register_action_provider(SourceDigestActionProvider())
    if "create-task" not in _providers:
        from personalclaw.action_providers.create_task_provider import CreateTaskActionProvider

        register_action_provider(CreateTaskActionProvider())
    if "invoke-agent" not in _providers:
        from personalclaw.action_providers.invoke_agent_provider import InvokeAgentActionProvider

        register_action_provider(InvokeAgentActionProvider())
    if "selfqa-triage" not in _providers:
        from personalclaw.action_providers.selfqa_triage_provider import SelfQaTriageActionProvider

        register_action_provider(SelfQaTriageActionProvider())
    if "selfqa-file-finding" not in _providers:
        # The Self-QA loop's filing step. Registered here
        # rather than behind the `agent.self_qa.enabled` flag: a provider the `self-qa`
        # template names must be dispatchable whenever that template can run, and a
        # registration that depends on config is one the run-start preflight cannot see.
        from personalclaw.action_providers.selfqa_finding_provider import (
            SelfQaFindingActionProvider,
        )

        register_action_provider(SelfQaFindingActionProvider())
    if "selfqa-evidence" not in _providers:
        # The Self-QA loop's evidence-sealing step. Registered
        # unconditionally for the same reason as its siblings above: a provider the `self-qa`
        # template names must be dispatchable whenever that template can run, and a registration
        # gated on `agent.self_qa.enabled` is one the run-start preflight cannot see.
        from personalclaw.action_providers.selfqa_evidence_provider import (
            SelfQaEvidenceActionProvider,
        )

        register_action_provider(SelfQaEvidenceActionProvider())
    if "selfqa-commit-watch" not in _providers:
        # The vcs trigger's action — commit delta + delegated `run-workflow` start.
        # Registered unconditionally for the reason `selfqa-file-finding` records above: the
        # trigger row exists even when the companion is off (the reconciler disables rather
        # than deletes), and a live row naming an unregistered provider validates, saves,
        # and then fails at fire time. Added to `ALLOWED_HOOK_PROVIDERS`, to
        # `triggers/screen.py`'s write-capable set and to `guardrails/rungs.py` in the SAME
        # commit — a provider in one set but not the others is that save-then-refuse mismatch.
        from personalclaw.action_providers.selfqa_watch_provider import (
            SelfQaCommitWatchActionProvider,
        )

        register_action_provider(SelfQaCommitWatchActionProvider())
    if "triage-digest" not in _providers:
        # PROACTIVE-ASSISTANT §1.1-§1.5 — the triage digest's one call site. Registered
        # unconditionally, NOT behind `proactive.triage_enabled`: a provider the bundled
        # "Morning triage" template names must be dispatchable whenever that template can be
        # instantiated, and a registration that depends on config is one the run-start preflight
        # cannot see. The switch is enforced inside `execute`, where a refusal is reportable.
        # Added to ALLOWED_HOOK_PROVIDERS and to `triggers/screen.py`'s write-capable set in the
        # SAME commit — a provider in one set but not the others saves and then fails to run.
        from personalclaw.action_providers.triage_digest_provider import (
            TriageDigestActionProvider,
        )

        register_action_provider(TriageDigestActionProvider())
    if "inbox-op" not in _providers:
        # The triage tier's hands, and the ONE provider in the
        # default auto-capable set. Registered unconditionally, NOT behind
        # `proactive.auto_execute_enabled`: the switch governs whether the digest DISPATCHES an
        # action, not whether an inbox operation is a dispatchable action, and a registration
        # that depended on config would make a user's own hand-written trigger fail at fire time
        # because a triage setting was off. Added to ALLOWED_HOOK_PROVIDERS, to
        # `triggers/screen.py`'s write-capable set, and to `guardrails.rungs` in the SAME commit
        # — a provider in one set but not the others saves and then fails to run.
        from personalclaw.action_providers.inbox_op_provider import InboxOpActionProvider

        register_action_provider(InboxOpActionProvider())
    if "run-prompt" not in _providers:
        from personalclaw.action_providers.run_prompt_provider import RunPromptActionProvider

        register_action_provider(RunPromptActionProvider())
    if "run-workflow" not in _providers:
        # Re-registered against the v2 engine, in the SAME commit
        # that re-adds it to ALLOWED_HOOK_PROVIDERS — a provider in one set but not the
        # other is the mismatch that makes a trigger save and then fail to run.
        from personalclaw.action_providers.run_workflow_provider import (
            RunWorkflowActionProvider,
        )

        register_action_provider(RunWorkflowActionProvider())
    if "call-app-route" not in _providers:
        from personalclaw.action_providers.call_app_route_provider import (
            CallAppRouteActionProvider,
        )

        register_action_provider(CallAppRouteActionProvider())
    if "artifact-update" not in _providers:
        # WORKFLOWS-V2 Slice 9b: the zero-token write a dashboard-style template uses
        # to refresh its artifact. Added to ALLOWED_HOOK_PROVIDERS in the SAME commit — a
        # provider in one set but not the other is the mismatch that makes a trigger save and
        # then fail to run.
        from personalclaw.action_providers.artifact_update_provider import (
            ArtifactUpdateActionProvider,
        )

        register_action_provider(ArtifactUpdateActionProvider())
    if "knowledge-persist" not in _providers:
        # The zero-token write/read pair a synthesis template
        # uses, so a retrieve → synthesize → persist pattern spends ONE model call rather than
        # three. Added to ALLOWED_HOOK_PROVIDERS in the SAME commit — a provider in one set but
        # not the other is the mismatch that makes a trigger save and then fail to run.
        from personalclaw.action_providers.knowledge_persist_provider import (
            KnowledgePersistActionProvider,
        )

        register_action_provider(KnowledgePersistActionProvider())
    if "knowledge-retrieve" not in _providers:
        from personalclaw.action_providers.knowledge_retrieve_provider import (
            KnowledgeRetrieveActionProvider,
        )

        register_action_provider(KnowledgeRetrieveActionProvider())
    if "knowledge-relate" not in _providers:
        # The write-back for the MODEL tier's typed
        # edges. `knowledge-persist` beside it can only write the two verbs the deterministic
        # ladder can derive; the structural three are what a judging node proposes, and its
        # answer previously reached a display string and nothing else. Added to
        # ALLOWED_HOOK_PROVIDERS, to `triggers/screen.py`'s write-capable set, to
        # `guardrails.rungs`' `action.knowledge_write` class and to `ownership.py`'s
        # LEARNING_PROVIDERS in the SAME commit — a provider in one set but not the others is
        # the mismatch that makes a template validate, save, and then fail to run.
        from personalclaw.action_providers.knowledge_relate_provider import (
            KnowledgeRelateActionProvider,
        )

        register_action_provider(KnowledgeRelateActionProvider())
    if "artifact_inspect" not in _providers:
        # The read half of output-offloading — pulls a `{{nodes.x.artifact}}`
        # body on demand, confined to the run's own `artifacts/`. Added to
        # ALLOWED_HOOK_PROVIDERS in the SAME commit — a provider in one set but not the other is
        # the mismatch that makes a spec validate, save, and then fail to run.
        from personalclaw.action_providers.artifact_inspect_provider import (
            ArtifactInspectActionProvider,
        )

        register_action_provider(ArtifactInspectActionProvider())
    if "knowledge-health" not in _providers:
        # The maintenance tier, split by COST. `knowledge-health` is
        # zero-token and safe to run on every write; `knowledge-consolidate` is expensive, gated,
        # and dry-run by default. Both added to ALLOWED_HOOK_PROVIDERS in the same commit.
        from personalclaw.action_providers.knowledge_maintain_provider import (
            KnowledgeConsolidateActionProvider,
            KnowledgeGapsActionProvider,
            KnowledgeHealthActionProvider,
        )

        register_action_provider(KnowledgeHealthActionProvider())
        register_action_provider(KnowledgeConsolidateActionProvider())
        register_action_provider(KnowledgeGapsActionProvider())
    if "render-report" not in _providers:
        # KNOWLEDGE-SYNTHESIS §6.2 (KNOW-R15): a declarative spec into a sanitized, self-contained
        # export, so a periodic synthesizer regenerates visuals with no model call. Added to
        # ALLOWED_HOOK_PROVIDERS in the SAME commit — a provider in one set but not the other is
        # the mismatch that makes a trigger save and then fail to run.
        from personalclaw.action_providers.knowledge_render_provider import (
            KnowledgeRenderReportActionProvider,
        )

        register_action_provider(KnowledgeRenderReportActionProvider())
    if "knowledge-propose" not in _providers:
        # The PROPOSE half of the maintenance tier —
        # a gap-healing or schema-edit draft into the review queue instead of
        # into the store. Before it, `proposals.enqueue` had no workflow-reachable caller at all,
        # so a template that wanted to propose could only write. Added to ALLOWED_HOOK_PROVIDERS
        # in the SAME commit — a provider in one set but not the other is the mismatch that makes
        # a trigger save and then fail to run.
        from personalclaw.action_providers.knowledge_propose_provider import (
            KnowledgeProposeActionProvider,
        )

        register_action_provider(KnowledgeProposeActionProvider())
    if "knowledge-report" not in _providers:
        # The scheduled-research-report runner — one fire is one
        # report run (resolve scope → write one finding → stamp the watermark). It writes through
        # `knowledge-persist` rather than the store, so the report's finding is an ordinary
        # knowledge item on the one write path.
        from personalclaw.action_providers.knowledge_report_provider import (
            KnowledgeReportActionProvider,
        )

        register_action_provider(KnowledgeReportActionProvider())
    if "browse" not in _providers:
        # The autonomous web-interaction provider. Registered
        # unconditionally, like `source-digest` above and for the same reason — a workflow
        # template or trigger naming `browse` must be dispatchable whenever it can run, and a
        # registration that depended on config is one the run-start preflight cannot see. The
        # provider itself refuses cheaply (a typed `ERR_BROWSE_NO_TARGET`) when no browser
        # target is configured, so registering it costs nothing on a machine without one.
        from personalclaw.action_providers.browse_provider import BrowseActionProvider

        register_action_provider(BrowseActionProvider())
    if "net-fetch" not in _providers:
        # The dispatchable HTTP-egress action. Registered
        # unconditionally, like `browse` above and for the same reason — a workflow template or
        # trigger naming `net-fetch` must be dispatchable whenever it can run, and a registration
        # that depended on config is one the run-start preflight cannot see. Registering it costs
        # nothing on an unconfigured machine: the FETCH_ACTION policy's allow-list is empty, so the
        # provider refuses every host with a typed `ERR_NET_FETCH_EGRESS_BLOCKED` naming the
        # setting to change. Added to ALLOWED_HOOK_PROVIDERS, to `triggers/screen.py`'s
        # write-capable set and to `guardrails.rungs` in the SAME commit — a provider in one set
        # but not the others is the mismatch that makes a trigger save and then fail to run.
        from personalclaw.action_providers.net_fetch_provider import NetFetchActionProvider

        register_action_provider(NetFetchActionProvider())
    if "best-of-n" not in _providers:
        # The engine-native half of best-of-N — a thin wrapper
        # over the `sampling.best_of_n` core the bundled skill and MCP tool already call.
        # Registered unconditionally for the reason `triage-digest` records above: a
        # provider the bundled `best-of-n` template names must be dispatchable whenever
        # that template can be instantiated, and a registration that depends on config is
        # one the run-start preflight cannot see. The N× spend is governed at the core's
        # own chokepoint (every call rides one_shot_completion → ModelCallGuard, N clamped
        # to 5). Added to ALLOWED_HOOK_PROVIDERS, to `triggers/screen.py`'s write-capable
        # set and to `guardrails/rungs.py`'s action table in the SAME commit — a provider
        # in one set but not the others is the mismatch that makes a trigger save and then
        # fail to run.
        from personalclaw.action_providers.best_of_n_provider import BestOfNActionProvider

        register_action_provider(BestOfNActionProvider())
    if "check-work" not in _providers:
        # The engine-native verification node — a thin wrapper
        # over the `check_work.derive_and_run` core the bundled skill and the SDLC
        # post-gate hook already share. Registered unconditionally for the same reason as
        # `best-of-n` directly above. Zero tokens, bounded filesystem reads only (no
        # command runner is injected, so command checks report `unverifiable`). Added to
        # ALLOWED_HOOK_PROVIDERS, to `triggers/screen.py`'s read-only set and to
        # `guardrails/rungs.py`'s action table in the SAME commit.
        from personalclaw.action_providers.check_work_provider import CheckWorkActionProvider

        register_action_provider(CheckWorkActionProvider())
    if "second-opinion" not in _providers:
        # Hand a stalled loop/gate/session's state to a
        # DIFFERENT cataloged runner for one shot, and accept the answer only when a disk
        # re-diff confirms the edits it claims. Added to ALLOWED_HOOK_PROVIDERS and to
        # `triggers/screen.py`'s write-capable set in the SAME commit — a provider in one set
        # but not the other is the mismatch that makes a trigger save and then fail to run.
        from personalclaw.action_providers.second_opinion_provider import (
            SecondOpinionActionProvider,
        )

        register_action_provider(SecondOpinionActionProvider())
