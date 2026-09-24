"""Rung routing at the dispatch seams (AUTONOMY-GUARDRAILS §5.2, atom AG-7).

AG-6 shipped the ladder with NO call sites — a decision object nothing consulted. The two
properties this atom is defined by are:

1. an app-contributed action inherits its DECLARED floor/ceiling with no dispatch-layer
   special-casing;
2. a ``leaves_machine`` type cannot resolve ``autonomous`` without an explicit ceiling
   raise.

**Everything here is driven through a REAL dispatch.** ``execute_event_action`` and
``run_script_hook`` are the production fire paths; the app declaration arrives through
``ActionTypeHandler.register``, the same handler the app loader calls on enable. A test that
constructed a spec and called ``resolve_rung`` by hand would prove the decision layer works
and say nothing about whether anything consults it — which is the exact defect AG-6 left and
this atom exists to close. The source-level check at the bottom is an ADDITION to that, not
a substitute: it fails when a FOURTH dispatch seam appears without routing, which no
behavioural test can notice.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
from typing import Any

import pytest

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult
from personalclaw.apps.manifest import AppManifest, AutonomyConfig, ProviderConfig
from personalclaw.guardrails import autonomy as au
from personalclaw.guardrails import rungs as rg


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """A throwaway home for the rung store, the SEL and the inbox.

    ``PERSONALCLAW_HOME`` as well as a patched ``config_dir``: the SEL singleton resolves
    its directory from the environment at instantiation, so patching ``config_dir`` alone
    would leave audit rows landing in the real home.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    cfg = home / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg)
    from personalclaw import sel as sel_mod

    sel_mod.SecurityEventLog._instance = None
    sel_mod.SecurityEventLog._initialized = False
    yield home
    sel_mod.SecurityEventLog._instance = None
    sel_mod.SecurityEventLog._initialized = False


@pytest.fixture(autouse=True)
def _clean_provider_registry():
    """Restore the action-provider registry — these tests install fakes into it."""
    from personalclaw.action_providers.registry import _providers

    before = dict(_providers)
    yield
    _providers.clear()
    _providers.update(before)


# ── a fake app-contributed action provider ────────────────────────────────────


class _AppAction(ActionProvider):
    """An app's action provider. Records every execution so a HELD fire is provable."""

    def __init__(self, name: str = "acme-do-thing", reversal: str = "") -> None:
        self._name = name
        self._reversal = reversal
        self.calls: list[ActionContext] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return "Acme Do Thing"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        self.calls.append(ctx)
        return ActionResult(success=True, stdout="did the thing", reversal=self._reversal)


def _install_app_action(
    *,
    floor: str = "",
    ceiling: str = "",
    network: bool = False,
    provider_name: str = "acme-do-thing",
    reversal: str = "",
) -> _AppAction:
    """Register an app's action provider THROUGH the production handler.

    ``ActionTypeHandler.register`` is what the app loader calls when an app is enabled, so
    the declaration this test relies on is produced by the same code an installed app goes
    through — manifest in, ``ActionTypeSpec`` out.
    """
    from personalclaw.providers.registry import ActionTypeHandler, RegisteredProvider

    manifest = AppManifest(name="acme", version="1.0.0", displayName="Acme", description="d")
    manifest.permissions.network = network
    provider_config = ProviderConfig(
        type="action",
        implementation="acme.provider:create",
        autonomy=AutonomyConfig(floor=floor, ceiling=ceiling),
    )
    ext = RegisteredProvider(name="acme", manifest=manifest, provider_config=provider_config)
    instance = _AppAction(provider_name, reversal=reversal)
    ActionTypeHandler().register(ext, instance)
    return instance


APP_KEY = "app:acme.acme-do-thing"


def _fire_event_trigger(provider_name: str = "acme-do-thing") -> Any:
    """Drive the REAL data-event fire path for a trigger pointing at ``provider_name``."""
    from personalclaw.event_triggers import (
        MEMORY_UPDATE,
        SOURCE_MEMORY,
        EventTrigger,
        execute_event_action,
    )

    trigger = EventTrigger(
        id="t-acme",
        pattern=MEMORY_UPDATE,
        source=SOURCE_MEMORY,
        action_provider=provider_name,
        action_config={"note": "hello"},
    )
    return asyncio.run(
        execute_event_action(
            trigger,
            source=SOURCE_MEMORY,
            event_type="update",
            key="project.acme.status",
            value="green",
        )
    )


def _inbox_rows(home) -> list[dict]:
    path = home / "inbox.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items", data)
    return list(items.values()) if isinstance(items, dict) else list(items)


# ── done_when 1: a declared floor governs a real dispatch ─────────────────────


def test_an_app_declared_floor_HOLDS_a_real_event_trigger_fire(_isolated_home):
    """🔴 THE ATOM. An app declares ``floor: one_tap`` and its action stops executing.

    Driven through `execute_event_action` — the production fire path — so the assertion is
    not "resolve_rung returns one_tap" but "the provider was never called". `calls` being
    empty is the whole proof: a routing decision nothing acts on leaves it non-empty.
    """
    action = _install_app_action(floor="one_tap", ceiling="auto_with_undo")

    outcome = _fire_event_trigger()

    assert action.calls == [], "a held action must never reach the provider"
    assert outcome.ran is False
    assert "held for your approval" in outcome.reason
    # And the hold is DURABLE, not just a refusal: the user gets a row to decide on.
    rows = _inbox_rows(_isolated_home)
    assert rows, "a held action must leave a standing attention item"
    held = rows[-1]
    assert held["item_kind"] == "agent_request"
    assert held["refs"]["action_type"] == APP_KEY
    assert held["refs"]["rung"] == "one_tap"
    assert held["refs"]["trigger"] == "t-acme"


def test_the_SAME_fire_executes_once_the_rung_is_granted(_isolated_home):
    """The other half: the ladder is a ladder, so a grant changes the dispatch outcome.

    Nothing about the trigger, the provider or the seam changes between the two fires —
    only the persisted grant. That is what makes this a routing test rather than a
    configuration test.
    """
    action = _install_app_action(floor="one_tap", ceiling="auto_with_undo")
    assert _fire_event_trigger().ran is False
    assert action.calls == []

    assert au.grant_rung(APP_KEY, "auto_with_undo", evidence_window="owner decision") == (
        "auto_with_undo"
    )

    outcome = _fire_event_trigger()
    assert outcome.ran is True
    assert len(action.calls) == 1


def test_the_seams_carry_no_per_action_branch(_isolated_home):
    """ "No dispatch-layer special-casing", asserted as a property of the SEAM SOURCE.

    A seam that reached its answer with `if provider == "acme-do-thing"` would pass the
    behavioural tests above and fail the requirement. The name→type mapping lives on the
    declaration (`ActionTypeSpec.providers`), so no seam mentions an action type at all.
    """
    from personalclaw import event_triggers, gateway, hooks

    for module in (hooks, event_triggers, gateway):
        src = inspect.getsource(module)
        for key in (APP_KEY, "acme-do-thing", "inbox.reply_draft", "action.execute_code"):
            assert key not in src, f"{module.__name__} names {key!r} — that is special-casing"


def test_an_UNDECLARED_provider_keeps_its_pre_ladder_behaviour(_isolated_home):
    """The additive property. A manifest with no ``autonomy`` block changes nothing.

    Measured deliberately: treating an undeclared provider as `draft_only` would withhold
    every hook and trigger in the tree — an outage wearing a safety control's clothes.
    """
    action = _install_app_action()  # no floor, no ceiling → no declaration at all

    assert au.action_type_for_provider("acme-do-thing") is None
    route = rg.route_provider_action("acme-do-thing")
    assert route.governed is False and route.executes is True

    outcome = _fire_event_trigger()
    assert outcome.ran is True
    assert len(action.calls) == 1
    assert _inbox_rows(_isolated_home) == []


# ── done_when 2: a leaves_machine ceiling cannot be claimed by a manifest ──────


def test_a_manifest_CANNOT_claim_autonomous_for_a_network_reaching_action(_isolated_home, caplog):
    """🔴 THE SECURITY RAIL. An app asking for ``autonomous`` is CLAMPED, and loudly.

    ``leaves_machine`` is derived by CORE from the app's own ``permissions.network``
    declaration — an app that could self-certify "my effect stays here" would be
    self-certifying its way to the top of the ladder.

    The clamp is asserted to be OBSERVABLE (log + SEL row) because a silent downgrade is a
    recorded finding in this tree: the app keeps working, nobody learns its declaration was
    overruled, and the manifest goes on claiming a rung it never had.
    """
    with caplog.at_level("WARNING"):
        _install_app_action(floor="auto_with_undo", ceiling="autonomous", network=True)

    spec = au.action_type("app:acme.acme-do-thing")
    assert spec is not None
    assert spec.leaves_machine is True
    assert spec.ceiling == au.RUNG_AUTO_WITH_UNDO, "an app's ceiling claim was honoured"
    assert "clamped to auto_with_undo" in caplog.text

    from personalclaw.sel import sel

    clamps = [
        e for e in sel().recent(200) if e.get("operation") == "guardrails.autonomy_ceiling_clamped"
    ]
    assert clamps, "the clamp left no audit trail"
    assert "declared=autonomous" in clamps[-1]["resources"]
    assert "granted=auto_with_undo" in clamps[-1]["resources"]


def test_a_clamped_type_cannot_be_GRANTED_autonomous_either(_isolated_home):
    """The clamp binds the grant path too, or it would only be advisory."""
    _install_app_action(floor="one_tap", ceiling="autonomous", network=True)

    assert au.grant_rung(APP_KEY, "autonomous") is None
    assert au.resolve_rung(APP_KEY) != au.RUNG_AUTONOMOUS
    assert rg.route_provider_action("acme-do-thing").rung != au.RUNG_AUTONOMOUS


def test_a_core_leaves_machine_type_may_raise_its_own_ceiling(_isolated_home):
    """ "Without an explicit ceiling raise" — core's in-tree declarations ARE that raise.

    `send-message` leaves the machine and declares `autonomous` deliberately: it already
    runs unattended today under the creation-time grant, and the ladder was added on top of
    that floor, never under it. What the flag still buys is that `promotion_eligibility`
    never PROPOSES the rung from a track record (asserted by AG-6's suite) — reaching it is
    a decision, made in a reviewed file, not an accumulation.
    """
    rg.ensure_core_action_types()
    spec = au.action_type_for_provider("send-message")
    assert spec is not None and spec.leaves_machine is True
    assert spec.ceiling == au.RUNG_AUTONOMOUS

    # The same claim, from a manifest, does not survive.
    _install_app_action(ceiling="autonomous", network=True)
    app_spec = au.action_type(APP_KEY)
    assert app_spec is not None and app_spec.ceiling == au.RUNG_AUTO_WITH_UNDO


# ── fail-closed ───────────────────────────────────────────────────────────────


def test_an_unregistered_type_key_routes_to_the_bottom_rung(_isolated_home):
    """A DECLARED key with no registration proves nothing, so it gets nothing."""
    route = rg.route_action_type("app:ghost.vanished")
    assert route.rung == au.RUNG_DRAFT_ONLY
    assert route.route == rg.ROUTE_DRAFT
    assert route.executes is False


def test_disabling_an_app_drops_its_declaration(_isolated_home):
    """A declaration outliving its provider would let a LATER app inherit its earned rung."""
    from personalclaw.providers.registry import ActionTypeHandler, RegisteredProvider

    instance = _install_app_action(floor="one_tap")
    assert au.action_type_for_provider("acme-do-thing") is not None

    manifest = AppManifest(name="acme", version="1.0.0", displayName="Acme", description="d")
    ext = RegisteredProvider(
        name="acme", manifest=manifest, provider_config=ProviderConfig(type="action")
    )
    ActionTypeHandler().deregister(ext, instance)

    assert au.action_type(APP_KEY) is None
    assert au.action_type_for_provider("acme-do-thing") is None


def test_every_builtin_action_provider_carries_a_declaration(_isolated_home):
    """The drift guard `action_providers.registry` promises in its docstring.

    A provider in the dispatch registry with no declaration behind it is indistinguishable,
    at a seam, from an ungoverned action — so registration and declaration have to move
    together.
    """
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        list_action_providers,
    )

    _ensure_default_providers_registered()
    missing = [n for n in list_action_providers() if au.action_type_for_provider(n) is None]
    assert missing == [], f"built-in providers with no autonomy declaration: {missing}"


# ── the profile layer: narrows, never widens ──────────────────────────────────


def test_an_unattended_profile_NARROWS_autonomous_to_auto_with_undo(_isolated_home):
    """PLATFORM-HARDENING-FLOORS §5: two levels, tightest wins.

    An unattended run has nobody watching, so `autonomous` — silent, no undo handle — would
    mean an action ran and left nothing a user would notice. The type's declaration is not
    weakened; it is narrowed.
    """
    rg.ensure_core_action_types()
    attended = rg.route_provider_action("create-task", session_key="")
    unattended = rg.route_provider_action("create-task", session_key="subagent:worker-1")

    assert attended.rung == au.RUNG_AUTONOMOUS and attended.route == rg.ROUTE_EXECUTE
    assert unattended.rung == au.RUNG_AUTO_WITH_UNDO
    assert unattended.route == rg.ROUTE_EXECUTE_WITH_UNDO
    assert "narrowed" in unattended.reason


def test_a_profile_can_never_WIDEN_a_declared_ceiling(_isolated_home):
    """The direction that would be a hole. An attended profile does not lift a low ceiling."""
    _install_app_action(floor="draft_only", ceiling="one_tap")
    for session_key in ("", "subagent:x", "cron:y", "_bg"):
        route = rg.route_provider_action("acme-do-thing", session_key=session_key)
        assert route.rung in (au.RUNG_DRAFT_ONLY, au.RUNG_ONE_TAP), session_key
        assert route.executes is False, session_key


def test_an_incident_holds_an_otherwise_autonomous_action(_isolated_home):
    """The kill switch outranks both levels — the composition still honours it."""
    from personalclaw.guardrails.incident import activate, resume

    action = _install_app_action(floor="autonomous", ceiling="autonomous")
    assert _fire_event_trigger().ran is True
    assert len(action.calls) == 1

    activate("drill")
    try:
        outcome = _fire_event_trigger()
        # The rung layer holds it too, independently of the fire path's own incident gate.
        assert rg.route_provider_action("acme-do-thing").rung == au.RUNG_ONE_TAP
    finally:
        resume()
    # The incident gate at the top of the fire path refuses first; either way nothing ran.
    assert outcome.ran is False
    assert len(action.calls) == 1


# ── auto_with_undo: the reversal handle ───────────────────────────────────────


def test_auto_with_undo_persists_the_providers_reversal_handle(_isolated_home):
    """``auto_with_undo`` executes AND records what would have to be undone.

    The handle is the provider's, because only it knows what "undo" means for its own
    effect. Driven through the real fire path so the assertion covers the seam that has to
    read `ActionResult.reversal`, not just the field's existence.
    """
    action = _install_app_action(
        floor="auto_with_undo", ceiling="auto_with_undo", reversal="task:native:t-42"
    )
    outcome = _fire_event_trigger()

    assert outcome.ran is True and len(action.calls) == 1
    from personalclaw.sel import sel

    rows = [e for e in sel().recent(200) if e.get("operation") == "guardrails.autonomy_executed"]
    assert rows, "an auto_with_undo execution left no audit row"
    assert "reversal=task:native:t-42" in rows[-1]["resources"]
    assert "rung=auto_with_undo" in rows[-1]["resources"]


def test_no_handle_means_no_undo_PROMISE(_isolated_home):
    """A provider that cannot reverse itself gets an audit row and NO notification.

    Offering an undo that cannot happen is a promise the product cannot keep — and it would
    also mean every unattended fire in the tree grew a notification overnight.
    """
    notified: list[tuple] = []

    class _State:
        def notify(self, *a, **kw):
            notified.append((a, kw))

    class _Services:
        state = _State()

    import personalclaw.action_providers.services as svc

    original = svc.get_action_services
    svc.get_action_services = lambda: _Services()  # type: ignore[assignment]
    try:
        _install_app_action(floor="auto_with_undo", ceiling="auto_with_undo", reversal="")
        assert _fire_event_trigger().ran is True
        assert notified == [], "an action with nothing to undo must not offer an undo"

        au.unregister_action_type(APP_KEY)
        _install_app_action(
            floor="auto_with_undo", ceiling="auto_with_undo", reversal="task:native:t-9"
        )
        assert _fire_event_trigger().ran is True
        assert notified, "an action WITH a handle must tell the user it ran"
        assert notified[-1][1]["meta"]["reversal"] == "task:native:t-9"
    finally:
        svc.get_action_services = original  # type: ignore[assignment]


def test_create_task_supplies_a_real_reversal_handle():
    """The field has a PRODUCTION writer, so the mechanism is not an unwritten key.

    `create-task` files a durable row and knows its id, which is exactly what an undo has
    to act on. Empty when the task provider returned no id — the seam then records the run
    and offers nothing, rather than offering something with nothing behind it.
    """
    from personalclaw.action_providers.create_task_provider import CreateTaskActionProvider

    class _Task:
        id = "task-77"

    async def _create_task(provider_name, **fields):
        return _Task()

    import personalclaw.tasks.registry as treg

    original = treg.create_task
    treg.create_task = _create_task  # type: ignore[assignment]
    try:
        result = asyncio.run(
            CreateTaskActionProvider().execute(
                {"title_template": "Follow up"}, ActionContext(event="Stop")
            )
        )
    finally:
        treg.create_task = original  # type: ignore[assignment]
    assert result.success is True
    assert result.reversal == "task:native:task-77"


# ── draft_only: the proposal row ──────────────────────────────────────────────


def test_draft_only_files_a_PROPOSAL_row_through_a_real_hook_run(_isolated_home):
    """The bottom rung, driven through `hooks.run_script_hook`.

    A proposal rather than an agent request because the two ask different questions:
    `draft_only` reports what an action WOULD have done, `one_tap` asks for a decision.
    """
    from personalclaw.hooks import ScriptHook, run_script_hook

    action = _install_app_action(floor="draft_only", ceiling="one_tap")
    hook = ScriptHook(
        id="h-acme",
        name="Acme on stop",
        event="Stop",
        provider="acme-do-thing",
        provider_config={"note": "x"},
    )
    result = asyncio.run(run_script_hook(hook))

    assert action.calls == []
    assert hook.last_status == "held_for_rung"
    assert "held for your approval" in result.error
    rows = _inbox_rows(_isolated_home)
    assert rows and rows[-1]["item_kind"] == "proposal"
    assert rows[-1]["refs"]["hook"] == "h-acme"


def test_the_held_hook_status_projects_as_a_skipped_gate():
    """An unmapped `last_status` falls to the `RAN if last_run` default and would report a
    held action as one that succeeded — the landmine the table's own comments record."""
    from personalclaw.triggers.history import HOOK_STATUS_TO_OUTCOME
    from personalclaw.triggers.models import INERT_OUTCOMES, Outcome

    assert HOOK_STATUS_TO_OUTCOME["held_for_rung"] == Outcome.SKIPPED_GATE.value
    assert Outcome.SKIPPED_GATE.value in INERT_OUTCOMES


# ── the manifest block: additive, validated, round-tripped ────────────────────


def test_a_manifest_without_an_autonomy_block_round_trips_unchanged():
    """Additive means additive: no new key appears in a manifest that declared nothing."""
    pc = ProviderConfig(type="action", implementation="m.p:f")
    assert "autonomy" not in pc.to_dict()
    assert ProviderConfig.from_dict(pc.to_dict()).autonomy == AutonomyConfig()

    m = AppManifest(name="acme", version="1.0.0", displayName="A", description="d")
    m.provider = ProviderConfig(type="action", implementation="m.p:f")
    assert "autonomy" not in m.to_dict().get("provider", {})


def test_the_autonomy_block_round_trips_both_fields():
    pc = ProviderConfig(
        type="action",
        implementation="m.p:f",
        autonomy=AutonomyConfig(floor="one_tap", ceiling="auto_with_undo"),
    )
    assert pc.to_dict()["autonomy"] == {"floor": "one_tap", "ceiling": "auto_with_undo"}
    assert ProviderConfig.from_dict(pc.to_dict()).autonomy == pc.autonomy


@pytest.mark.parametrize(
    "floor,ceiling,fragment",
    [
        ("wishful", "", "autonomy.floor must be one of"),
        ("", "totally_free", "autonomy.ceiling must be one of"),
        ("auto_with_undo", "one_tap", "is below its floor"),
    ],
)
def test_a_malformed_autonomy_block_is_REFUSED_at_validation(floor, ceiling, fragment):
    """An unusable declaration is rejected, not coerced: a silently-corrected rung name
    would hide which actions are actually governed."""
    errors = ProviderConfig(
        type="action",
        implementation="m.p:f",
        autonomy=AutonomyConfig(floor=floor, ceiling=ceiling),
    ).validate()
    assert any(fragment in e for e in errors), errors


def test_an_unparseable_autonomy_value_does_not_break_the_manifest():
    """A non-object `autonomy` reads as UNDECLARED rather than raising — a malformed app
    manifest must fail its own validation, never take the loader down with it."""
    pc = ProviderConfig.from_dict(
        {"type": "action", "implementation": "m.p:f", "autonomy": "autonomous"}
    )
    assert pc.autonomy == AutonomyConfig()


# ── the third seam: the store-trigger fire path ───────────────────────────────


def _fire_store_trigger(action: _AppAction, kind: str = "clock") -> Any:
    """Drive the REAL clock/file/webhook dispatch on a bare orchestrator.

    The same `object.__new__` shape `test_blocked_fire_ledger` uses: this seam's refusal
    branches touch no gateway state, and building a whole orchestrator would test the
    constructor rather than the routing.
    """
    import types

    import personalclaw.action_providers as ap
    from personalclaw.gateway import GatewayOrchestrator

    trigger = types.SimpleNamespace(
        id=f"{kind}:acme",
        kind=kind,
        workflow={"inline": {"provider": action.name, "config": {"note": "x"}}},
    )
    original = ap.get_action_provider
    try:
        ap.get_action_provider = lambda name: action  # type: ignore[assignment]
        asyncio.run(
            object.__new__(GatewayOrchestrator)._fire_store_trigger(trigger, {"kind": kind})
        )
    finally:
        ap.get_action_provider = original  # type: ignore[assignment]
    return trigger


def test_the_store_trigger_seam_HOLDS_a_declared_floor(_isolated_home):
    """🔴 THE SEAM THE PLAN MISNAMED. `_run_action_job` retired with `ScheduleService`
    (S112) and the substrate GENERALISED it into `_fire_store_trigger` — which is the path
    every clock, file, webhook and chained trigger takes. Routing the other two seams and
    not this one would honour a declared floor at two of three dispatch points, which reads
    to a user exactly like not honouring it at all.

    The ledger row is asserted alongside the hold: criterion 8's "zero silent drops" applies
    to a rung refusal for the same reason it applies to a screened payload — the user sees an
    automation that stopped and needs somewhere to look.
    """
    from personalclaw.schedule_history import ScheduleRunStore
    from personalclaw.triggers.models import Outcome

    action = _install_app_action(floor="one_tap", ceiling="auto_with_undo")
    trigger = _fire_store_trigger(action)

    assert action.calls == [], "a held store-trigger action must never reach the provider"
    runs, total = asyncio.run(ScheduleRunStore(_isolated_home).list_for_job(trigger.id, 0, 20))
    assert total == 1, "a held fire left no ledger row"
    assert runs[0]["status"] == Outcome.SKIPPED_GATE.value
    assert "held for your approval" in runs[0]["error"]
    rows = _inbox_rows(_isolated_home)
    assert rows and rows[-1]["refs"]["trigger"] == trigger.id


def test_the_store_trigger_seam_records_the_reversal_handle(_isolated_home):
    """And the executing half: `auto_with_undo` runs and persists what it would undo."""
    action = _install_app_action(
        floor="auto_with_undo", ceiling="auto_with_undo", reversal="task:native:t-5"
    )
    _fire_store_trigger(action)

    assert len(action.calls) == 1
    from personalclaw.sel import sel

    rows = [e for e in sel().recent(200) if e.get("operation") == "guardrails.autonomy_executed"]
    assert rows and "reversal=task:native:t-5" in rows[-1]["resources"]


# ── the structural rail: a FOURTH seam cannot appear unrouted ─────────────────

#: Every module that resolves an action provider and RUNS it, mirroring
#: `test_action_provider_chokepoints.EXECUTION_SITES`. The manual Run path
#: (`dashboard/handlers/triggers`) is excluded: a user pressing Run IS the approval a rung
#: withholds for, and routing it would refuse the click that authorised the action.
ROUTED_SEAMS: tuple[tuple[str, str], ...] = (
    ("personalclaw.hooks", "the lifecycle-hook fire path"),
    ("personalclaw.event_triggers", "the data-event fire path"),
    ("personalclaw.gateway", "the clock/file/webhook store-trigger fire path"),
)


@pytest.mark.parametrize("module_name,label", ROUTED_SEAMS)
def test_every_unattended_dispatch_seam_ROUTES(module_name, label):
    """🔴 THE INVARIANT AG-6 could not have. A seam that reaches a provider without asking
    the ladder is how a declared floor quietly stops applying — and a new seam is exactly
    the case a behavioural test cannot fail on, because it does not know it exists.

    Asserted through the AST rather than a substring so an alias (`as _route_action`) counts
    and a mention inside a comment does not.
    """
    import importlib

    tree = ast.parse(inspect.getsource(importlib.import_module(module_name)))
    local_names = {
        (alias.asname or alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("guardrails.rungs")
        for alias in node.names
        if alias.name == "route_provider_action"
    }
    assert local_names, f"{label} never imports route_provider_action"
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert local_names & called, f"{label} imports the router but never calls it"
