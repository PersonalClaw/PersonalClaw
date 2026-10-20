"""The provider-registration invariant: no execution without a policy check.

Measured before writing this test, the honest result is that the invariant HOLDS today, so this
file exists to keep it holding rather than to fix a defect:

    hooks._run_provider (lifecycle)                  incident_active   + enforce_action
    gateway._fire_store_trigger (clock/file/event)   incident_active   + enforce_action
    workflows.engine.dispatch_action (a run's step)  enforce_action
    handlers/trigger_runs._dispatch_store_action     manual_refusal
        (yours alone: every other run, a webhook's, a view's, an agent's or an app's Run now,
        is a fire and runs through `gateway._fire_store_trigger`)
    handlers/hooks                                   -- reads metadata only, never executes

(`event_triggers.execute_event_action` was a third unattended seam until a data-event trigger
became a row in the one store: its fires now run through `gateway._fire_store_trigger` like every
other store trigger, so the module no longer resolves or runs a provider at all.)

The `enforce_action` column is the addition, and it is a SECOND invariant over the same sites:
`POLICY_CHECKS` below is satisfied by ANY one check, which is right for its question ("does this
site consult policy at all?") but blind to a specific control going missing at a specific seam.
That is precisely what happened — the gateway seam kept the kill switch and gained the rung ladder
while the denylist every unattended seam promises was 0 there — so `DENYLIST_SEAMS` names that
one control and requires it everywhere it was declared.

That last line is the reason this is a source-level test rather than a behavioural one. The
property is *structural*: "every site that reaches a provider passes a policy check first". A
behavioural test can only prove the sites it knows about, so it cannot fail when someone adds a
FIFTH execution site — the exact regression this invariant is written against. The failure mode
prevented is not "the check is wrong", it is "a new call path skipped the check entirely".

🔴 What is deliberately NOT asserted: providers each *declaring* their
enforcement chokepoint as an attribute. None of the 16 shipped providers declares one.
That is left alone rather than half-built, because an attribute nothing reads is exactly the
inert-control defect this codebase keeps finding — enforcement lives at the call sites, and this
test guards the call sites. Recorded so the next author knows it was a decision, not an oversight.
"""

from __future__ import annotations

import pytest
from action_provider_chokepoints_shared import (
    DENYLIST_SEAMS,
    EXECUTION_SITES,
    MANUAL_SEAM,
    REVERSAL_SITE,
    WORKFLOW_LOOKUPS,
    _enforce_action_calls,
    _reaches_the_registry,
    _source,
)

#: Any one of these, present in the module, satisfies the invariant. A LIST rather than one name
#: because the sites legitimately differ: an unattended fire is gated by the kill switch, a manual
#: fire by `manual_refusal`, and a store-backed fire additionally walks the whole `firepath`.
POLICY_CHECKS: tuple[str, ...] = (
    "incident_active",
    "enforce_action",
    "manual_refusal",
    "capability_allows",
    "unfenced_actions",
    "requested_capabilities",
    "path_allowed",
    "firepath",
)


#: The ONE site that resolves an action provider to UNDO an action rather than to run one.
#: Exempt from the execution invariant, and asserted separately by
#: `test_the_reversal_site_undoes_and_never_executes` rather than merely trusted. Why it is
#: exempt: it calls `reverse`, never `execute`; the provider it may reach is bounded by the
#: recorded action type's own declaration plus the handle kind that provider claims; and the
#: request is user-initiated and autonomy-REDUCING. An `incident_active` check here would refuse
#: to take back exactly the automatic action a user turned the kill switch on because of.
#: The execution sites a USER CLICKS. Each is exempt from the unattended denylist seam, and each
#: is exempt ONLY while it still carries `manual_refusal` — asserted per-member below, so an
#: exemption cannot outlive its own gate. Adding a member here is an argument, not a shortcut.
USER_CLICKED_SEAMS: tuple[str, ...] = (
    "personalclaw.proposals_contract",  # Approve on an inbox proposal
    # Your Run now, your answer to a run's question and the restart review's Run now. Every run
    # anyone else starts from this module is a fire, through the gateway seam
    # (`test_only_your_run_reaches_the_hand_dispatch`).
    MANUAL_SEAM,
    # "Run now" on a scheduled research report. Attended by definition — the
    # SCHEDULED fire of the same report goes through the trigger path, which carries the
    # denylist — and the per-member assertion below holds it to carrying `manual_refusal`.
    "personalclaw.dashboard.handlers.research_reports",
)


@pytest.mark.parametrize("module_name,label", DENYLIST_SEAMS)
def test_every_unattended_seam_enforces_the_denylist(module_name, label):
    """🔴 THE DENYLIST INVARIANT. The denylist's whole promise is that "an app-contributed provider
    inherits the denylist without knowing it exists" — which holds only if EVERY unattended
    dispatch seam calls it. Two of three is the same shape as none, because an author only needs
    to reach the unguarded one."""
    calls = _enforce_action_calls(module_name)
    assert calls, (
        f"the {label} seam ({module_name}) dispatches an action provider without calling "
        "guardrails.denylist.enforce_action, which every unattended dispatch seam must call."
    )


@pytest.mark.parametrize("module_name,label", DENYLIST_SEAMS)
def test_every_seam_threads_the_session_key(module_name, label):
    """The call SHAPE, not just its presence. `session_key=""` classifies as ATTENDED, so the
    run's `SafetyProfile` (its `denylist_extra` globs and its `path_allowlist` confinement) is
    skipped entirely — the defect. A seam that calls `enforce_action` without threading a
    session key enforces only the built-ins, which is a quieter version of not enforcing."""
    for call in _enforce_action_calls(module_name):
        assert any(kw.arg == "session_key" for kw in call.keywords), (
            f"the {label} seam ({module_name}) calls enforce_action without session_key=; "
            "the SafetyProfile layer is silently skipped."
        )


def test_the_denylist_seam_list_covers_every_unattended_execution_site():
    """🔴 The rail that makes `DENYLIST_SEAMS` trustworthy, and the one that catches a FOURTH seam.

    Derived from `EXECUTION_SITES` (itself verified against the tree by
    `test_the_site_list_is_not_STALE`) minus the documented manual exemption, so a new
    provider-execution path cannot be added without either carrying the denylist or being
    argued into an exemption here.
    """
    unattended = {m for m, _ in EXECUTION_SITES} - set(USER_CLICKED_SEAMS)
    declared = {m for m, _ in DENYLIST_SEAMS}
    assert unattended == declared, (
        "the denylist seam list drifted from the execution-site list: "
        f"missing {sorted(unattended - declared)}, stale {sorted(declared - unattended)}"
    )


@pytest.mark.parametrize("module_name", USER_CLICKED_SEAMS)
def test_each_user_clicked_seam_is_a_documented_denylist_exemption(module_name):
    """The exemption asserted rather than assumed: it must still be gated by `manual_refusal`.

    If that check ever disappears, this path becomes an unattended-equivalent execution site with
    no policy gate at all — so the exemption is only valid while its own gate is present.
    """
    src = _source(module_name)
    assert "manual_refusal" in src, (
        f"{module_name} is exempt from the denylist because a human initiates it and "
        "`manual_refusal` gates it. That gate is gone, so the exemption no longer holds."
    )


@pytest.mark.parametrize("module_name,label", EXECUTION_SITES)
def test_every_execution_site_has_a_policy_check(module_name, label):
    """🔴 THE INVARIANT. A new provider-execution path that forgot its policy check is how an
    automation surface quietly stops being fenced — the defect once found for the kill switch, where
    three unattended entry points existed and only one checked the flag."""
    src = _source(module_name)
    found = [c for c in POLICY_CHECKS if c in src]
    assert found, (
        f"{label} ({module_name}) executes an action provider with no policy check. "
        f"Expected one of: {', '.join(POLICY_CHECKS)}"
    )


def test_the_catalog_site_does_not_execute():
    """The one `get_action_provider` caller exempt from the invariant, and why.

    `dashboard/handlers/hooks.py` resolves every provider to read `display_name`/`supports_blocking`
    for the catalog. If it ever gained an `execute` call it would become an unfenced execution path,
    so the exemption is asserted rather than assumed.
    """
    src = _source("personalclaw.dashboard.handlers.hooks")
    assert "get_action_provider(" in src, "the exemption is stale if this site no longer resolves"
    assert ".execute(" not in src, "the catalog site must never execute a provider"


def test_the_rung_router_only_reads_the_undo_declaration():
    """The properties that earn `guardrails.rungs`'s exemption.

    It resolves a provider to ask whether that provider can take its action back, which decides
    whether the action may run at the rung that promises undo. It must only read that
    declaration: if it ever executes or reverses a provider, it must argue its way into
    `EXECUTION_SITES` (or be the reversal site) with a real gate instead.
    """
    src = _source("personalclaw.guardrails.rungs")
    assert "get_action_provider(" in src, "the exemption is stale if rungs no longer resolves"
    assert ".execute(" not in src, "the rung router must never execute a provider"
    assert ".reverse(" not in src, "the rung router must never undo through a provider"
    assert "reversal_kinds" in src, "the one thing rungs reads from a provider is its undo claim"


def test_the_reversal_site_undoes_and_never_executes():
    """The second exemption from the execution invariant, and the properties that earn it.

    `guardrails.ladder` resolves a provider so a user can take an `auto_with_undo` action BACK.
    That is the opposite direction from every site in `EXECUTION_SITES`, so the kill-switch check
    they share would be wrong here — but "it's different" is not an exemption, so the difference
    is asserted: it must never execute, and it must resolve its provider through the declaration
    (`reversal_kinds`) rather than accept whatever name a caller supplies.
    """
    src = _source(REVERSAL_SITE)
    assert "get_action_provider(" in src, "the exemption is stale if this site no longer resolves"
    assert ".execute(" not in src, "the reversal site must never execute a provider"
    assert ".reverse(" in src, "the reversal site must reach the provider's own undo"
    assert "reversal_kinds" in src, "resolution must be bounded by what the provider claims"


def test_the_would_execute_preview_site_only_reads_the_declaration():
    """The third exemption, and the properties that earn it.

    `dashboard/handlers/doctor.py`'s would-execute simulator resolves a provider to read ONE
    declaration — `supports_dry_run` — because that is the honesty rule: only the spawn-based
    LLM providers have a real observe mode, and a panel that labelled a deterministic provider's
    description "observe-mode result" would promise a safety property the provider does not have.

    The kill-switch check every `EXECUTION_SITES` entry shares would be wrong here, because this
    site is not an entry point at all: the dry fire it renders returns before the automation engine
    consults a runner. "It's different" is not an exemption, so the difference is asserted —
    it must never execute, and it must never dispatch a fire with a runner attached.
    """
    src = _source("personalclaw.dashboard.handlers.doctor")
    assert "get_action_provider(" in src, "the exemption is stale if this site no longer resolves"
    assert ".execute(" not in src, "the preview site must never execute a provider"
    assert "supports_dry_run" in src, "the only reason to resolve here is the dry-run declaration"
    # 🪤 The load-bearing one. `triggers.tools.run` executes when handed a runner, so a `runner=`
    # that ever became anything but None would turn this read-only panel into a fire path.
    assert "runner=None" in src, "the dry fire must be dispatched with no runner"


def test_the_census_sees_a_lookup_under_another_name():
    """The census's own falsification: each way of reaching the lookup is a site, and words about
    it are not."""
    reaches = [
        "provider = get_action_provider(name)\n",
        "provider = registry.get_action_provider(name)\n",
        "getter = get_provider or get_action_provider\nprovider = getter(name)\n",
        "from personalclaw.action_providers import get_action_provider as find\n",
    ]
    for source in reaches:
        assert _reaches_the_registry(source), source
    words = '"""Looks a provider up as get_action_provider(name) does."""\n# get_action_provider\n'
    assert not _reaches_the_registry(words)


@pytest.mark.parametrize("module_name", sorted(WORKFLOW_LOOKUPS))
def test_the_workflow_lookups_only_read_a_declaration(module_name):
    """The properties that earn the two workflow lookups their exemption: each resolves a step's
    provider to read one thing about it, and never runs or undoes it. If one ever does, it must
    argue its way into `EXECUTION_SITES` with the denylist instead; the engine's dispatch is the
    one place a workflow step's provider runs."""
    src = _source(module_name)
    assert _reaches_the_registry(src), "the exemption is stale if this site no longer resolves"
    assert ".execute(" not in src, "a workflow lookup must never execute a provider"
    assert ".reverse(" not in src, "a workflow lookup must never undo through a provider"
    assert WORKFLOW_LOOKUPS[module_name] in src, "the only reason to resolve here is that read"


def test_the_grant_copy_only_reads_the_display_name():
    """The properties that earn `triggers.grants`' exemption.

    A grant question and a refusal have to say what the action is — "Bash Command", not the bare
    id `bash` — and that takes the provider's `display_name` and nothing more. "It's different" is
    not an exemption, so the difference is asserted: if this module ever USES the provider it
    resolves, this fails and it must argue its way into `EXECUTION_SITES` with a real policy gate.
    """
    import re

    src = _source("personalclaw.triggers.grants")
    calls = re.findall(r"get_action_provider\(", src)
    assert calls, "the exemption is stale if the grant copy no longer resolves a provider"
    assert ".execute(" not in src, "the grant copy must never execute a provider"
    assert ".reverse(" not in src, "the grant copy must never undo through a provider"
    assert '"display_name"' in src, "the only reason to resolve here is the display name"


def test_the_delegating_provider_only_hands_off_to_a_frozen_name():
    """The properties that earn `selfqa_watch_provider`'s exemption.

    It is the only resolve in the known set that happens inside a PROVIDER. A provider cannot
    be an entry point: something already gated -- here the gateway's `file`-trigger fire path,
    which carries the denylist and the kill switch -- has to dispatch it first, so the policy
    check every `EXECUTION_SITES` entry shares has already run upstream by the time this code
    executes. "It's downstream" is not an exemption on its own, so the two properties that make
    it safe are asserted: it resolves a FROZEN literal name (never one a caller supplies, the
    hole the reversal-site exemption also closes), and it delegates the start rather than
    re-implementing it, so the dedupe and origin stamping stay single-writer.

    If this provider ever resolves a name off its `action_config`, it becomes a
    caller-steerable dispatcher and must argue its way into `EXECUTION_SITES` with a real
    policy gate instead.
    """
    import re

    src = _source("personalclaw.action_providers.selfqa_watch_provider")
    calls = re.findall(r"get_action_provider\(([^)]*)\)", src)
    assert calls, "the exemption is stale if the provider no longer delegates"
    assert all(c.strip() in {'"run-workflow"', "'run-workflow'"} for c in calls), (
        "every resolve must be the frozen `run-workflow` literal; a name read from "
        f"action_config would make this a caller-steerable dispatcher. found: {calls}"
    )
    assert "action_config" not in "".join(calls), "the delegate name must not come from the caller"


def test_the_create_time_provider_check_only_asks_existence():
    """The properties that earn `triggers.tools`'s exemption (#779).

    `create` refuses an unregistered action provider BEFORE the row exists — the
    green-row-silent-failure-loop that refusal exists to prevent. That takes one
    registry question, "is this name registered?", and nothing more: the resolved provider
    is never bound to a name, never handed to a runner, and never executed. "It's different"
    is not an exemption, so the difference is asserted here — if `create` ever starts USING
    the provider it resolves, this test fails and the module must argue its way into
    `EXECUTION_SITES` with a real policy gate instead.
    """
    import re

    src = _source("personalclaw.triggers.tools")
    calls = re.findall(r"get_action_provider\([^)]*\)[^\n]*", src)
    assert calls, "the exemption is stale if create no longer resolves a provider"
    assert all("is None" in c for c in calls), (
        "every resolve in triggers.tools must be the bare `is None` existence check; "
        f"found: {calls}"
    )
    assert "_ensure_default_providers_registered()" in src, (
        "the existence check must register the built-ins first, or startup order would "
        "make it refuse real providers"
    )


def test_no_shipped_provider_declares_a_chokepoint_attribute():
    """Pins the measured state the docstring records, so the next author sees it as a decision.

    If someone later adds a `chokepoint` attribute to providers, this test fails and they must
    either wire something that READS it or drop it — which is the point. An attribute nothing reads
    is the inert-control defect, and a security-shaped one is worse than none.
    """
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
        list_action_providers,
    )

    _ensure_default_providers_registered()
    declaring = [
        name
        for name in list_action_providers()
        if any(
            hasattr(get_action_provider(name), attr)
            for attr in ("chokepoint", "requires_policy_check")
        )
    ]
    assert not declaring, (
        f"{declaring} declare a chokepoint attribute. Either wire a consumer that ENFORCES it, or "
        "remove it — a declared-but-unread security attribute is worse than none."
    )
