"""In-process registry of action providers."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.action_providers.base import ActionProvider


_providers: "dict[str, ActionProvider]" = {}


def register_action_provider(provider: "ActionProvider") -> None:
    _providers[provider.name] = provider


def get_action_provider(name: str) -> "ActionProvider | None":
    return _providers.get(name)


def list_action_providers() -> list[str]:
    return list(_providers.keys())


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
        # PLATFORM-RESILIENCE §4.3 (PR2-8): the health-scored remediation engine, re-homed off the
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
    if "source-digest" not in _providers:
        # WATCHED-SOURCES §6.2 (WS-7's caller). Registered unconditionally, not behind
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
        # SELF-VERIFICATION §3.2 step 5 — the Self-QA loop's filing step. Registered here
        # rather than behind the `agent.self_qa.enabled` flag: a provider the `self-qa`
        # template names must be dispatchable whenever that template can run, and a
        # registration that depends on config is one the run-start preflight cannot see.
        from personalclaw.action_providers.selfqa_finding_provider import (
            SelfQaFindingActionProvider,
        )

        register_action_provider(SelfQaFindingActionProvider())
    if "triage-digest" not in _providers:
        # PROACTIVE-ASSISTANT §1.1-§1.5 (PA-2) — the triage digest's one call site. Registered
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
        # PROACTIVE-ASSISTANT §1.6 (PA-3) — the triage tier's hands, and the ONE provider in the
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
        # Re-registered against the v2 engine (WORKFLOWS-V2 Slice 3), in the SAME commit
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
        # WORKFLOWS-V2 Slice 9b (WF2-R15): the zero-token write a dashboard-style template uses
        # to refresh its artifact. Added to ALLOWED_HOOK_PROVIDERS in the SAME commit — a
        # provider in one set but not the other is the mismatch that makes a trigger save and
        # then fail to run.
        from personalclaw.action_providers.artifact_update_provider import (
            ArtifactUpdateActionProvider,
        )

        register_action_provider(ArtifactUpdateActionProvider())
    if "knowledge-persist" not in _providers:
        # KNOWLEDGE-SYNTHESIS §2.1/§2.2: the zero-token write/read pair a synthesis template
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
    if "artifact_inspect" not in _providers:
        # WORKFLOWS-V2 WV-11: the read half of output-offloading — pulls a `{{nodes.x.artifact}}`
        # body on demand, confined to the run's own `artifacts/`. Added to
        # ALLOWED_HOOK_PROVIDERS in the SAME commit — a provider in one set but not the other is
        # the mismatch that makes a spec validate, save, and then fail to run.
        from personalclaw.action_providers.artifact_inspect_provider import (
            ArtifactInspectActionProvider,
        )

        register_action_provider(ArtifactInspectActionProvider())
    if "knowledge-health" not in _providers:
        # KNOWLEDGE-SYNTHESIS §3.4: the maintenance tier, split by COST. `knowledge-health` is
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
        # KNOWLEDGE-SYNTHESIS §3.3/§3.4 (WF2KNO-8): the PROPOSE half of the maintenance tier —
        # a gap-healing or schema-edit draft into the LEARNING-FLYWHEEL review queue instead of
        # into the store. Before it, `proposals.enqueue` had no workflow-reachable caller at all,
        # so a template that wanted to propose could only write. Added to ALLOWED_HOOK_PROVIDERS
        # in the SAME commit — a provider in one set but not the other is the mismatch that makes
        # a trigger save and then fail to run.
        from personalclaw.action_providers.knowledge_propose_provider import (
            KnowledgeProposeActionProvider,
        )

        register_action_provider(KnowledgeProposeActionProvider())
    if "knowledge-report" not in _providers:
        # KNOWLEDGE-SYNTHESIS WF2KNO-12: the scheduled-research-report runner — one fire is one
        # report run (resolve scope → write one finding → stamp the watermark). It writes through
        # `knowledge-persist` rather than the store, so the report's finding is an ordinary
        # knowledge item on the one write path.
        from personalclaw.action_providers.knowledge_report_provider import (
            KnowledgeReportActionProvider,
        )

        register_action_provider(KnowledgeReportActionProvider())
