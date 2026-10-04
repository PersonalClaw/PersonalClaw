"""The provider-registration invariant: no execution without a policy check (§7 item 6 / R3 am.5).

The plan asks for "a test asserting no execution without a policy check". Measured before writing
it — and the honest result is that the invariant HOLDS today, so this file exists to keep it holding
rather than to fix a defect:

    hooks._run_provider (lifecycle)                  incident_active   + enforce_action
    gateway._fire_store_trigger (clock/file/event)   incident_active   + enforce_action
    workflows.engine.dispatch_action (a run's step)  enforce_action
    handlers/trigger_runs._dispatch_store_action     manual_refusal    + enforce_action
        (by hand or from outside: yours is attended, every other run is not)
    handlers/hooks                                   -- reads metadata only, never executes

(`event_triggers.execute_event_action` was a third unattended seam until a data-event trigger
became a row in the one store: its fires now run through `gateway._fire_store_trigger` like every
other store trigger, so the module no longer resolves or runs a provider at all.)

The `enforce_action` column is the addition, and it is a SECOND invariant over the same sites:
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
enforcement chokepoint as an attribute. None of the 16 shipped providers declares one.
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
    ("personalclaw.gateway", "the clock/file/event/webhook/view trigger fire path"),
    # Run now, the restart review's Run now, your answer to a run's question, and a Run now an
    # agent's tool or an app starts. Yours is attended and carries `manual_refusal`; every other
    # run has nobody answering it, so the module is a denylist seam below as well
    # (`test_the_hand_dispatch_holds_every_run_but_yours_to_the_denylist`).
    ("personalclaw.dashboard.handlers.trigger_runs", "the run-by-hand path"),
    # Approving an inbox proposal whose apply case is `action` dispatches a provider
    # directly (not through `triggers.tools.run`), so it is a real execution site. User-clicked,
    # so it carries `manual_refusal` — the manual Run path's gate — rather than the unattended
    # denylist seam; `test_the_denylist_seam_list_covers_every_unattended_execution_site`
    # therefore lists it beside that documented exemption.
    ("personalclaw.proposals_contract", "the inbox proposal apply path"),
    # A TTL dashboard tile re-runs its bound data nodes with nobody watching, so it is a
    # real UNATTENDED execution site and joins the denylist seams below rather than claiming an
    # exemption. Its providers are additionally narrowed to a read-only allowlist
    # (`tile_refresh.DATA_PROVIDERS`) — a second fence, not a substitute for these gates.
    ("personalclaw.dashboard.tile_refresh", "the chatless tile-refresh path"),
    # "Run now" on a scheduled research report dispatches the report provider
    # directly, so it is a real execution site. User-clicked, so it carries `manual_refusal`
    # — the same gate and the same documented exemption as the trigger Run path above.
    ("personalclaw.dashboard.handlers.research_reports", "the manual report Run path"),
    # The trivial-tier auto-execution dispatches a provider per approved proposal with
    # nobody watching, so it is a real UNATTENDED execution site and joins the denylist seams
    # below rather than claiming an exemption. Its providers are additionally narrowed to a
    # frozen capability set (`autoexec.AUTO_CAPABLE_PROVIDERS`) and its actions bounded by a
    # per-run cap and the NEW-1 budget floor — more fences, not a substitute for these gates.
    ("personalclaw.proactive.autoexec", "the triage auto-execution path"),
    # Every action step of a workflow run, whatever started the run and whatever lookup the
    # engine was handed: a run is unattended work, so it is a denylist seam below. It resolves
    # through a name it is handed (`getter = get_action_provider`), which the census finds by the
    # name, not by a literal call (`test_the_census_sees_a_lookup_under_another_name`).
    ("personalclaw.workflows.engine", "a workflow run's action step"),
)


REVERSAL_SITE = "personalclaw.guardrails.ladder"

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


#: The THREE seams AUTONOMY-GUARDRAILS §1.2 names, each of which must call `enforce_action`
#: BEFORE it reaches a provider. This is narrower than `EXECUTION_SITES` by exactly one entry —
#: the manual Run path, exempted below — and the two lists are cross-checked by
#: `test_the_denylist_seam_list_covers_every_unattended_execution_site` so a FOURTH unattended
#: seam cannot appear without joining this one.
#:
#: 🔴 Why a rail and not trust: the third seam was written as `gateway.py:701`
#: (`_run_action_job`), which retired with `ScheduleService`. The successor
#: (`_fire_store_trigger`) kept the kill switch and gained the rung ladder but silently lost the
#: denylist — measured at 1 / 1 / 0 `enforce_action` calls across hooks / event_triggers / gateway
#: while gateway is the busiest of the three (every clock, file, webhook and chained trigger).
#: AG-12 restored it; this rail is what stops the next retirement dropping it again.
DENYLIST_SEAMS: tuple[tuple[str, str], ...] = (
    ("personalclaw.hooks", "script hooks"),
    ("personalclaw.gateway", "clock / file / webhook / chained / data-event triggers"),
    ("personalclaw.dashboard.tile_refresh", "TTL dashboard tiles"),
    ("personalclaw.proactive.autoexec", "trivial-tier triage auto-execution"),
    ("personalclaw.workflows.engine", "workflow action steps"),
    (
        "personalclaw.dashboard.handlers.trigger_runs",
        "a webhook's fire, a view's refresh, an agent's or an app's Run now",
    ),
)

#: The run-by-hand-or-from-outside dispatch. It used to be exempt from the denylist as the Run
#: button's path, attended because a human just pressed Run, while a webhook's outside caller, a
#: view's refresh and an agent's `automation_run` reached the same dispatch with nobody answering:
#: a webhook whose action said `personalclaw stop` stopped the gateway. Now only a run you start
#: yourself is attended (`manual_refusal` gates it), and every other run is held to the denylist,
#: so the module is a denylist seam above and not an exemption below. Asserted in
#: `test_the_hand_dispatch_holds_every_run_but_yours_to_the_denylist`.
MANUAL_SEAM = "personalclaw.dashboard.handlers.trigger_runs"

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
    "personalclaw.proposals_contract",  # Approve on an inbox proposal
    # "Run now" on a scheduled research report. Attended by definition — the
    # SCHEDULED fire of the same report goes through the trigger path, which carries the
    # denylist — and the per-member assertion below holds it to carrying `manual_refusal`.
    "personalclaw.dashboard.handlers.research_reports",
)


#: The workflow modules that look a step's provider up only to read it, each with the one thing it
#: reads: whether the provider is registered at all, and whether its action is a model turn.
WORKFLOW_LOOKUPS: dict[str, str] = {
    "personalclaw.workflows.preflight": "is None",
    "personalclaw.workflows.node_bindings": "hands_config_to_a_model",
}


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


def test_the_hand_dispatch_holds_every_run_but_yours_to_the_denylist():
    """🔴 The run-by-hand dispatch was exempt as the Run button's path, while an agent's
    `automation_run` (and, then, a webhook's outside caller and a view's refresh, which now run
    through the dispatch every fire runs through) reached the same dispatch with nobody answering.
    The properties that make it a denylist seam now:

    * `_dispatch_store_action` asks `enforce_action`, threading the identity it judges the run
      under, before its provider runs;
    * your own run is still gated as an attended one is (`manual_refusal`);
    * every caller of the dispatch says whose run it is (`runs_for=`), so a new caller decides
      rather than inherits. One that did not would be judged as the trigger's own fire.
    """
    import ast

    from personalclaw.dashboard.handlers import trigger_runs, triggers

    src = _source(MANUAL_SEAM)
    assert "manual_refusal" in src, "your own Run now is no longer gated"
    [dispatch] = [
        node
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_dispatch_store_action"
    ]
    calls = [node for node in ast.walk(dispatch) if isinstance(node, ast.Call)]
    asks = [c for c in calls if isinstance(c.func, ast.Name) and c.func.id == "enforce_action"]
    runs = [c for c in calls if isinstance(c.func, ast.Attribute) and c.func.attr == "execute"]
    assert asks, "the hand dispatch runs a provider without asking the denylist"
    assert all(any(k.arg == "session_key" for k in c.keywords) for c in asks)
    assert runs and min(c.lineno for c in asks) < min(
        c.lineno for c in runs
    ), "the hand dispatch asks the denylist after its provider runs"

    callers = 0
    for module in (trigger_runs, triggers):
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != "_dispatch_store_action":
                continue
            callers += 1
            assert "runs_for" in {k.arg for k in node.keywords}, ast.unparse(node)
    # Run now, an answer, and the restart review's Run now.
    assert callers >= 3, f"found only {callers} callers; the scan is not seeing them"


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


def _reaches_the_registry(source: str) -> bool:
    """Whether *source* reaches the provider registry's lookup at all: calls it, imports it under
    any name, or hands the name on (`getter = get_action_provider`). Read from the syntax tree, so
    an alias is a site and a mention in a comment or a docstring is not.

    A literal-call search misses a module that looks its provider up through a name it is handed,
    as the workflow engine does (`getter = get_action_provider`): `get_action_provider(` never
    appears in it, so such a site would run providers with nothing here asking it for a check.
    """
    import ast

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id == "get_action_provider":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "get_action_provider":
            return True
        if isinstance(node, ast.ImportFrom) and any(
            alias.name == "get_action_provider" for alias in node.names
        ):
            return True
    return False


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


def test_the_site_list_is_not_STALE():
    """🔴 The test that makes the list above trustworthy.

    A hardcoded list of call sites rots the moment someone adds one — and a rotted list reads as
    "all sites are checked" while silently covering fewer. So the list is verified against the tree:
    every module that reaches `get_action_provider`, by any name (`_reaches_the_registry`), must be
    either an execution site or a documented exemption.
    """
    import pathlib

    root = pathlib.Path(inspect.getfile(__import__("personalclaw"))).parent
    callers: set[str] = set()
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if _reaches_the_registry(text):
            rel = path.relative_to(root).with_suffix("")
            # A package's `__init__` is the package (`personalclaw.action_providers`).
            parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
            callers.add(".".join(("personalclaw", *parts)))

    known = {m for m, _ in EXECUTION_SITES} | {
        REVERSAL_SITE,
        "personalclaw.dashboard.handlers.hooks",
        # The would-execute preview — reads `supports_dry_run` only; the properties that
        # earn the exemption are asserted in `test_the_would_execute_preview_site_only_reads_the_
        # declaration` above, so this entry cannot become a silent bypass.
        "personalclaw.dashboard.handlers.doctor",
        # The create-time existence check (#779) — `triggers.tools.create` resolves a provider to
        # ask ONE question, "is this name registered?", and refuses the row before it exists when
        # the answer is no. The result is never bound and nothing executes; the properties that
        # earn the exemption are asserted in
        # `test_the_create_time_provider_check_only_asks_existence` below.
        "personalclaw.triggers.tools",
        # The delegating provider -- the one resolve in this set that lives INSIDE a
        # provider rather than at a fire path. Every other entry in `EXECUTION_SITES` is a seam
        # a trigger/hook/tile arrives at, and the gate belongs there: `selfqa-commit-watch` is
        # only reachable THROUGH the gateway file-trigger path, which already carries the
        # denylist and the kill switch, so a second gate here would fence an already-fenced
        # call. The properties that earn the exemption are asserted in
        # `test_the_delegating_provider_only_hands_off_to_a_frozen_name` below.
        "personalclaw.action_providers.selfqa_watch_provider",
        # The grant copy — `triggers.grants` resolves a provider to read ONE attribute,
        # `display_name`, for the words the owner reads when a grant is asked for or a run is
        # refused ("not allowed to use the “Bash Command” action"). It decides nothing and runs
        # nothing; the properties that earn the exemption are asserted in
        # `test_the_grant_copy_only_reads_the_display_name` below.
        "personalclaw.triggers.grants",
        # The rung router -- `guardrails.rungs.can_be_undone` resolves each provider of an
        # action type to read ONE declaration, `reversal_kinds`, so a rung that promises undo
        # is only ever given to an action every provider of which can be taken back. It runs
        # nothing and undoes nothing; the properties that earn the exemption are asserted in
        # `test_the_rung_router_only_reads_the_undo_declaration` below.
        "personalclaw.guardrails.rungs",
        # A workflow step's two read-only lookups: preflight asks whether a step's provider is
        # registered, and the binding pass whether its action is a model turn
        # (`hands_config_to_a_model`). Neither runs anything; the properties that earn the
        # exemption are asserted in `test_the_workflow_lookups_only_read_a_declaration` below.
        *WORKFLOW_LOOKUPS,
        "personalclaw.action_providers.registry",  # defines it
        "personalclaw.action_providers",  # re-exports it
    }
    unaccounted = callers - known
    assert not unaccounted, (
        "these modules reach an action provider but are not in EXECUTION_SITES: "
        f"{sorted(unaccounted)}. Add them (with a policy check) or document the exemption."
    )


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
    green-row-silent-failure-loop this repo's BA-7 rule exists to prevent. That takes one
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
