"""The provider-registration invariant: no execution without a policy check (§7 item 6 / R3 am.5).

The plan asks for "a test asserting no execution without a policy check". Measured before writing
it — and the honest result is that the invariant HOLDS today, so this file exists to keep it holding
rather than to fix a defect:

    hooks._run_provider (lifecycle)                  incident_active   + enforce_action
    gateway._fire_store_trigger (clock/file/event)   incident_active   + enforce_action (AG-12)
    event_triggers.execute_event_action              incident_active   + enforce_action
    handlers/triggers._dispatch_store_action (manual) manual_refusal
    handlers/hooks                                   -- reads metadata only, never executes

The `enforce_action` column is AG-12's addition, and it is a SECOND invariant over the same sites:
`POLICY_CHECKS` below is satisfied by ANY one check, which is right for its question ("does this
site consult policy at all?") but blind to a specific control going missing at a specific seam.
That is precisely what happened — the gateway seam kept the kill switch and gained the rung ladder
while the denylist §1.2 promises at all three seams was 0 there — so `DENYLIST_SEAMS` names that
one control and requires it everywhere it was declared.

That last line is the reason this is a source-level test rather than a behavioural one. The
property is *structural*: "every site that reaches a provider passes a policy check first". A
behavioural test can only prove the sites it knows about, so it cannot fail when someone adds a
FIFTH execution site — the exact regression this invariant is written against. The failure mode
prevented is not "the check is wrong", it is "a new call path skipped the check entirely".

🔴 What is deliberately NOT asserted: the plan also describes providers each *declaring* their
enforcement chokepoint as an attribute. Measured: none of the 16 shipped providers declares one.
That is left alone rather than half-built, because an attribute nothing reads is exactly the
inert-control defect this program keeps finding — enforcement lives at the call sites, and this
test guards the call sites. Recorded so the next author knows it was a decision, not an oversight.
"""

from __future__ import annotations

import inspect

import pytest

#: Every module that resolves an action provider and RUNS it. Discovered by grepping
#: `get_action_provider(` across `src/`, then reading each hit to see whether it executes.
#: `dashboard/handlers/hooks.py` is excluded on purpose: it resolves providers to build the
#: `/api/action-providers` catalog and never calls `execute`, verified by
#: `test_the_catalog_site_does_not_execute` below.
EXECUTION_SITES: tuple[tuple[str, str], ...] = (
    ("personalclaw.hooks", "the lifecycle-hook fire path"),
    ("personalclaw.gateway", "the clock/file trigger fire path"),
    ("personalclaw.event_triggers", "the data-event fire path"),
    ("personalclaw.dashboard.handlers.triggers", "the manual Run path"),
    # INU-7: approving an inbox proposal whose apply case is `action` dispatches a provider
    # directly (not through `triggers.tools.run`), so it is a real execution site. User-clicked,
    # so it carries `manual_refusal` — the manual Run path's gate — rather than the unattended
    # denylist seam; `test_the_denylist_seam_list_covers_every_unattended_execution_site`
    # therefore lists it beside that documented exemption.
    ("personalclaw.proposals_contract", "the inbox proposal apply path"),
    # AS-2: a TTL dashboard tile re-runs its bound data nodes with nobody watching, so it is a
    # real UNATTENDED execution site and joins the denylist seams below rather than claiming an
    # exemption. Its providers are additionally narrowed to a read-only allowlist
    # (`tile_refresh.DATA_PROVIDERS`) — a second fence, not a substitute for these gates.
    ("personalclaw.dashboard.tile_refresh", "the chatless tile-refresh path"),
    # WF2KNO-12: "Run now" on a scheduled research report dispatches the report provider
    # directly, so it is a real execution site. User-clicked, so it carries `manual_refusal`
    # — the same gate and the same documented exemption as the trigger Run path above.
    ("personalclaw.dashboard.handlers.research_reports", "the manual report Run path"),
    # PA-3: §1.6's trivial-tier auto-execution dispatches a provider per approved proposal with
    # nobody watching, so it is a real UNATTENDED execution site and joins the denylist seams
    # below rather than claiming an exemption. Its providers are additionally narrowed to a
    # frozen capability set (`autoexec.AUTO_CAPABLE_PROVIDERS`) and its actions bounded by a
    # per-run cap and the NEW-1 budget floor — more fences, not a substitute for these gates.
    ("personalclaw.proactive.autoexec", "the triage auto-execution path"),
)


REVERSAL_SITE = "personalclaw.guardrails.ladder"

#: Any one of these, present in the module, satisfies the invariant. A LIST rather than one name
#: because the sites legitimately differ: an unattended fire is gated by the kill switch, a manual
#: fire by `manual_refusal`, and a store-backed fire additionally walks the whole `firepath`.
POLICY_CHECKS: tuple[str, ...] = (
    "incident_active",
    "manual_refusal",
    "capability_allows",
    "unfenced_actions",
    "requested_capabilities",
    "path_allowed",
    "firepath",
)


#: The THREE seams AUTONOMY-GUARDRAILS §1.2 names, each of which must call `enforce_action`
#: BEFORE it reaches a provider. This is narrower than `EXECUTION_SITES` by exactly one entry —
#: the manual Run path, exempted below — and the two lists are cross-checked by
#: `test_the_denylist_seam_list_covers_every_unattended_execution_site` so a FOURTH unattended
#: seam cannot appear without joining this one.
#:
#: 🔴 Why a rail and not trust: §1.2's third seam was written as `gateway.py:701`
#: (`_run_action_job`), which retired with `ScheduleService` (S112). The successor
#: (`_fire_store_trigger`) kept the kill switch and gained the rung ladder but silently lost the
#: denylist — measured at 1 / 1 / 0 `enforce_action` calls across hooks / event_triggers / gateway
#: while gateway is the busiest of the three (every clock, file, webhook and chained trigger).
#: AG-12 restored it; this rail is what stops the next retirement dropping it again.
DENYLIST_SEAMS: tuple[tuple[str, str], ...] = (
    ("personalclaw.hooks", "script hooks"),
    ("personalclaw.gateway", "clock / file / webhook / chained triggers"),
    ("personalclaw.event_triggers", "memory-event triggers"),
    ("personalclaw.dashboard.tile_refresh", "TTL dashboard tiles"),
    ("personalclaw.proactive.autoexec", "trivial-tier triage auto-execution"),
)

#: The one execution site NOT required to carry the denylist, and why: it runs a trigger because a
#: human just pressed Run, so it is attended by definition and is gated by `manual_refusal`
#: instead. Asserted in `test_the_manual_run_path_is_the_documented_denylist_exemption` rather
#: than merely stated.
MANUAL_SEAM = "personalclaw.dashboard.handlers.triggers"

#: The ONE site that resolves an action provider to UNDO an action rather than to run one
#: (AUTONOMY-GUARDRAILS §6.1). Exempt from the execution invariant, and asserted separately by
#: `test_the_reversal_site_undoes_and_never_executes` rather than merely trusted. Why it is
#: exempt: it calls `reverse`, never `execute`; the provider it may reach is bounded by the
#: recorded action type's own declaration plus the handle kind that provider claims; and the
#: request is user-initiated and autonomy-REDUCING. An `incident_active` check here would refuse
#: to take back exactly the automatic action a user turned the kill switch on because of.
#: The execution sites a USER CLICKS. Each is exempt from the unattended denylist seam, and each
#: is exempt ONLY while it still carries `manual_refusal` — asserted per-member below, so an
#: exemption cannot outlive its own gate. Adding a member here is an argument, not a shortcut.
USER_CLICKED_SEAMS: tuple[str, ...] = (
    MANUAL_SEAM,
    "personalclaw.proposals_contract",  # INU-7: Approve on an inbox proposal
    # WF2KNO-12: "Run now" on a scheduled research report. Attended by definition — the
    # SCHEDULED fire of the same report goes through the trigger path, which carries the
    # denylist — so the exemption is the same argument as the trigger Run path, and the
    # per-member assertion below holds it to carrying `manual_refusal`.
    "personalclaw.dashboard.handlers.research_reports",
)


def _source(module_name: str) -> str:
    import importlib

    return inspect.getsource(importlib.import_module(module_name))


def _enforce_action_calls(module_name: str) -> list:
    """Every `enforce_action(...)` CALL node in a module, found via AST.

    AST rather than a substring search because the property under test is a property of the
    CALL — that it passes `session_key=` — and the three seams spell the call across one, four
    and five lines. A regex that happened to match today's formatting would stop seeing the call
    the moment someone reflowed it, and a rail that matches nothing reads exactly like a pass.
    """
    import ast

    tree = ast.parse(_source(module_name))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "enforce_action"
    ]


@pytest.mark.parametrize("module_name,label", DENYLIST_SEAMS)
def test_every_unattended_seam_enforces_the_denylist(module_name, label):
    """🔴 THE §1.2 INVARIANT. The denylist's whole promise is that "an app-contributed provider
    inherits the denylist without knowing it exists" — which holds only if EVERY unattended
    dispatch seam calls it. Two of three is the same shape as none, because an author only needs
    to reach the unguarded one."""
    calls = _enforce_action_calls(module_name)
    assert calls, (
        f"the {label} seam ({module_name}) dispatches an action provider without calling "
        "guardrails.denylist.enforce_action. §1.2 requires it at all three dispatch seams."
    )


@pytest.mark.parametrize("module_name,label", DENYLIST_SEAMS)
def test_every_seam_threads_the_session_key(module_name, label):
    """The call SHAPE, not just its presence. `session_key=""` classifies as ATTENDED, so the
    run's `SafetyProfile` (its `denylist_extra` globs and its `path_allowlist` confinement) is
    skipped entirely — the PHF-8 defect. A seam that calls `enforce_action` without threading a
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
def test_the_manual_run_path_is_the_documented_denylist_exemption(module_name):
    """The exemption asserted rather than assumed: it must still be gated by `manual_refusal`.

    If that check ever disappears, this path becomes an unattended-equivalent execution site with
    no policy gate at all — so the exemption is only valid while its own gate is present.
    """
    src = _source(module_name)
    assert "manual_refusal" in src, (
        "the manual Run path is exempt from the denylist because a human initiates it and "
        "`manual_refusal` gates it. That gate is gone, so the exemption no longer holds."
    )


@pytest.mark.parametrize("module_name,label", EXECUTION_SITES)
def test_every_execution_site_has_a_policy_check(module_name, label):
    """🔴 THE INVARIANT. A new provider-execution path that forgot its policy check is how an
    automation surface quietly stops being fenced — the defect S117 found for the kill switch, where
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
    """The third exemption, and the properties that earn it (PLATFORM-RESILIENCE §3.3 — PR2-7).

    `dashboard/handlers/doctor.py`'s would-execute simulator resolves a provider to read ONE
    declaration — `supports_dry_run` — because that is the T9 honesty rule: only the spawn-based
    LLM providers have a real observe mode, and a panel that labelled a deterministic provider's
    description "observe-mode result" would promise a safety property the provider does not have.

    The kill-switch check every `EXECUTION_SITES` entry shares would be wrong here, because this
    site is not an entry point at all: the dry fire it renders returns before AUTOMATION-SUBSTRATE
    consults a runner. "It's different" is not an exemption, so the difference is asserted —
    it must never execute, and it must never dispatch a fire with a runner attached.
    """
    src = _source("personalclaw.dashboard.handlers.doctor")
    assert "get_action_provider(" in src, "the exemption is stale if this site no longer resolves"
    assert ".execute(" not in src, "the preview site must never execute a provider"
    assert "supports_dry_run" in src, "the only reason to resolve here is the T9 declaration"
    # 🪤 The load-bearing one. `triggers.tools.run` executes when handed a runner, so a `runner=`
    # that ever became anything but None would turn this read-only panel into a fire path.
    assert "runner=None" in src, "the dry fire must be dispatched with no runner"


def test_the_site_list_is_not_STALE():
    """🔴 The test that makes the list above trustworthy.

    A hardcoded list of call sites rots the moment someone adds one — and a rotted list reads as
    "all sites are checked" while silently covering fewer. So the list is verified against the tree:
    every module that calls `get_action_provider(` must be either an execution site or the
    documented catalog exemption.
    """
    import pathlib
    import re

    root = pathlib.Path(inspect.getfile(__import__("personalclaw"))).parent
    callers: set[str] = set()
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"[^f]get_action_provider\(", text):
            rel = path.relative_to(root).with_suffix("")
            callers.add("personalclaw." + str(rel).replace("/", "."))

    known = {m for m, _ in EXECUTION_SITES} | {
        REVERSAL_SITE,
        "personalclaw.dashboard.handlers.hooks",
        # The would-execute preview (PR2-7) — reads `supports_dry_run` only; the properties that
        # earn the exemption are asserted in `test_the_would_execute_preview_site_only_reads_the_
        # declaration` above, so this entry cannot become a silent bypass.
        "personalclaw.dashboard.handlers.doctor",
        "personalclaw.action_providers.registry",  # defines it
        "personalclaw.action_providers",  # re-exports it
    }
    unaccounted = callers - known
    assert not unaccounted, (
        "these modules reach an action provider but are not in EXECUTION_SITES: "
        f"{sorted(unaccounted)}. Add them (with a policy check) or document the exemption."
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
