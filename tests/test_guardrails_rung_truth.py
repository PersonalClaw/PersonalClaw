"""One rung per action: what Settings shows, what the log records, and what the undo list offers.

`guardrails.autonomy_executed` records the rung each automated run took, and Settings › Guardrails
› Earned autonomy shows the rung from the same route (``rungs.route_action_type``). Five ways that
could still say "runs with undo" for a run that kept no undo, each closed and pinned here:

1. A class mixed a provider that can be undone (``create-task``) with one that cannot
   (``selfqa-file-finding``), and "can be undone" meant ANY provider: the second ran "with undo"
   and kept nothing. The rung is the class's, so every provider must agree, and the Self-QA filing
   step has its own class.
2. A run routed "with undo" that came back with no handle the store kept was logged at that rung.
   It is logged at the rung it had, and a row at the undo rung names its undo record.
3. An action that cannot be undone ran at "runs with undo" whenever its OWN rung put it there: a
   declaration, a grant, or the untrusted-ceiling clamp that turns an app's ``autonomous`` claim
   into that rung. It ran silently. It asks first now, and the panel and the Inbox row say that
   what it does cannot be taken back.
4. The notice for a run with an undo said "ran on its own", the name of the other rung.
5. A promotion could offer "runs with undo" to an action that cannot be undone.

Driven through the REAL store-trigger seam (``GatewayOrchestrator._fire_store_trigger``, the path
every clock, file, webhook and event trigger takes) and the REAL ``GET /api/autonomy`` handler.
"""

from __future__ import annotations

import asyncio
import json
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult
from personalclaw.apps.manifest import AppManifest, AutonomyConfig, ProviderConfig
from personalclaw.dashboard.handlers import autonomy as api_h
from personalclaw.guardrails import autonomy as au
from personalclaw.guardrails import ladder as ld
from personalclaw.guardrails import rungs as rg

APP_KEY = "app:acme.acme-do-thing"
EXECUTED = "guardrails.autonomy_executed"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """A throwaway home for the rung and reversal stores, the SEL, tasks and the inbox.

    ``PERSONALCLAW_HOME`` as well as the patched ``config_dir``: the SEL singleton resolves its
    directory from the environment, and several stores bind ``config_dir`` at import.
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
    """Restore the action-provider registry — a test here installs an app provider into it."""
    from personalclaw.action_providers.registry import _providers

    before = dict(_providers)
    yield
    _providers.clear()
    _providers.update(before)


# ── driving it ────────────────────────────────────────────────────────────────


class _StandIn(ActionProvider):
    """Stands in for one provider at dispatch, so a fire's EFFECT never happens here.

    It carries the name it replaces and claims exactly the reversal kinds that provider claims,
    so the handle it returns is the shape the real one would. For a core provider the registry
    keeps the REAL one: whether an action can be undone is read from what actually ships, and
    only the seam's ``get_action_provider`` lookup is pointed here.
    """

    def __init__(self, name: str, kinds: tuple[str, ...] = (), *, handle: str | None = None):
        self._name = name
        self._kinds = kinds
        self._handle = (f"{kinds[0]}:stand-in-1" if kinds else "") if handle is None else handle
        self.calls: list[ActionContext] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return f"Stand-in for {self._name}"

    @property
    def reversal_kinds(self) -> tuple[str, ...]:
        return self._kinds

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        self.calls.append(ctx)
        return ActionResult(success=True, stdout="done", reversal=self._handle)


def _real_kinds(provider_name: str) -> tuple[str, ...]:
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )

    _ensure_default_providers_registered()
    provider = get_action_provider(provider_name)
    return tuple(getattr(provider, "reversal_kinds", ()) or ()) if provider else ()


def _fire_store_trigger(
    dispatched: ActionProvider, trigger_id: str = "clock:tidy-downloads"
) -> Any:
    """One REAL clock-trigger fire of ``dispatched`` on a bare orchestrator.

    The `object.__new__` shape the rung-routing and audit tests use: this seam's gates touch no
    gateway state, and a whole orchestrator would test its constructor instead.
    """
    import personalclaw.action_providers as ap
    from personalclaw.gateway import GatewayOrchestrator

    trigger = types.SimpleNamespace(
        id=trigger_id,
        kind="clock",
        workflow={"inline": {"provider": dispatched.name, "config": {}}},
        # Granted, as a row a fire brings here is: it passed `admit_fire`'s fence first.
        capabilities={"providers": [dispatched.name]},
    )
    original = ap.get_action_provider
    try:
        ap.get_action_provider = lambda name: dispatched if name == dispatched.name else None
        asyncio.run(
            object.__new__(GatewayOrchestrator)._fire_store_trigger(trigger, {"kind": "clock"})
        )
    finally:
        ap.get_action_provider = original  # type: ignore[assignment]
    return trigger


def _ladder() -> dict:
    resp = asyncio.run(api_h.api_autonomy(make_mocked_request("GET", "/api/autonomy")))
    return json.loads(resp.body.decode())


def _row(view: dict, key: str) -> dict:
    return next(t for t in view["types"] if t["key"] == key)


def _executed_rows(key: str = "") -> list[dict]:
    from personalclaw.sel import sel

    return [
        e
        for e in sel().recent(400)
        if e.get("operation") == EXECUTED
        and (not key or e.get("caller_identity") == f"autonomy:{key}")
    ]


def _audited_rung(event: dict) -> str:
    """The ``rung=`` field of an execution row: the claim this file checks."""
    return str(event.get("resources", "")).split(" ", 1)[0].removeprefix("rung=")


def _inbox_rows(home: Path) -> list[dict]:
    path = home / "inbox.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items", data)
    return list(items.values()) if isinstance(items, dict) else list(items)


def _install_app_action(
    *, floor: str, ceiling: str, network: bool = False, kinds: tuple[str, ...] = ()
) -> _StandIn:
    """An app's action through the production registration handler. ``kinds`` empty = an action
    whose provider cannot take its effect back."""
    from personalclaw.providers.registry import ActionTypeHandler, RegisteredProvider

    manifest = AppManifest(name="acme", version="1.0.0", displayName="Acme", description="d")
    manifest.permissions.network = network
    provider_config = ProviderConfig(
        type="action",
        implementation="acme.provider:create",
        autonomy=AutonomyConfig(floor=floor, ceiling=ceiling),
    )
    ext = RegisteredProvider(name="acme", manifest=manifest, provider_config=provider_config)
    instance = _StandIn("acme-do-thing", kinds)
    ActionTypeHandler().register(ext, instance)
    return instance


def _seed_clean_record(key: str, *, approvals: int = 12, days: int = 9) -> None:
    """The SEL approval trail a promotion is derived from, through the real logger, backdated
    so the approvals span the window ("ten approvals over seven days" means what it says)."""
    from personalclaw.sel import sel

    now = datetime.now(timezone.utc)
    log = sel()
    for _ in range(approvals):
        log.log_tool_invocation(
            tool_name="acme",
            outcome="approved",
            session_key="s",
            metadata={au.SEL_ACTION_TYPE_KEY: key},
        )
    path = Path(log._path)
    out: list[str] = []
    stamped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if (row.get("metadata") or {}).get(au.SEL_ACTION_TYPE_KEY) == key:
            step = timedelta(days=days * stamped / max(approvals - 1, 1))
            row["timestamp"] = (now - timedelta(days=days) + step).isoformat()
            stamped += 1
        out.append(json.dumps(row))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


# ── the sweep: for every core provider, the log's rung is the panel's rung ─────

CORE_PROVIDERS = sorted({p for spec in rg.CORE_ACTION_TYPES for p in spec.providers})


def test_the_sweep_covers_every_kind_of_rung_outcome():
    """The floor under the sweep: it must reach an action that runs on its own, one that runs
    with undo, and one that is held — or a green below proves only one of them."""
    assert {"bash", "heartbeat-tasks", "create-task", "inbox-op", "browse"} <= set(CORE_PROVIDERS)
    assert len(CORE_PROVIDERS) >= 30


@pytest.mark.parametrize("provider_name", CORE_PROVIDERS)
def test_the_audited_rung_is_the_rung_the_panel_shows(_isolated_home, provider_name):
    """For every core action provider, through a real unattended fire: the rung its row records
    is the rung its chip shows, a row at the undo rung names an undo the panel lists, and a row at
    any other rung leaves none. A run the ladder holds writes no row, and the panel says so."""
    stand_in = _StandIn(provider_name, _real_kinds(provider_name))
    _fire_store_trigger(stand_in)

    spec = au.action_type_for_provider(provider_name)
    assert spec is not None, f"{provider_name} is undeclared"
    view = _ladder()
    shown = _row(view, spec.key)["resolved_rung"]
    rows = _executed_rows(spec.key)
    pending = [
        r for r in view["reversals"] if r["action_type"] == spec.key and not r["reversed_at"]
    ]

    if not stand_in.calls:
        assert shown in (au.RUNG_ONE_TAP, au.RUNG_DRAFT_ONLY), (provider_name, shown)
        assert rows == [], "a held action wrote an execution row"
        held = [r for r in _inbox_rows(_isolated_home) if r["refs"].get("action_type") == spec.key]
        assert held and held[-1]["refs"]["rung"] == shown, held
        return

    assert len(rows) == 1, f"{provider_name}: a governed run must leave exactly one audit row"
    audited = _audited_rung(rows[0])
    assert audited == shown, f"{provider_name}: the log says {audited}, Settings says {shown}"
    if audited == au.RUNG_AUTO_WITH_UNDO:
        assert len(pending) == 1, f"{provider_name} ran with undo and the panel lists no undo"
        assert f"undo={pending[0]['id']}" in rows[0]["resources"]
    else:
        assert pending == [], f"{provider_name}: an undo record for a run that claims none"


def test_every_run_claiming_the_undo_rung_is_one_the_undo_list_offers(_isolated_home):
    """Over a mix of fires: every row at ``auto_with_undo`` names a pending undo record, every
    pending record has its row, and no row without a record claims the rung."""
    _fire_store_trigger(_StandIn("bash"))
    _fire_store_trigger(_StandIn("notify"), trigger_id="clock:notify")
    _fire_store_trigger(_StandIn("selfqa-file-finding"), trigger_id="clock:finding")
    _fire_store_trigger(_StandIn("create-task", ("task",)), trigger_id="clock:task")
    _fire_store_trigger(_StandIn("inbox-op", ("inbox-op",)), trigger_id="clock:inbox")

    rows = _executed_rows()
    assert len(rows) == 5, "every governed run leaves one row"
    claiming = [e for e in rows if _audited_rung(e) == au.RUNG_AUTO_WITH_UNDO]
    named = {
        part.removeprefix("undo=")
        for e in claiming
        for part in str(e["resources"]).split(" ")
        if part.startswith("undo=")
    }
    offered = {r["id"] for r in _ladder()["reversals"] if not r["reversed_at"]}
    assert len(claiming) == len(named) == 2, rows
    assert named == offered, (named, offered)


# ── 1. a class's providers agree on whether their effect can be undone ────────


def test_every_core_class_agrees_on_whether_it_can_be_undone():
    """The rung is the CLASS's, so "runs with undo" must be true of every provider in it or of
    none. A class that mixed them showed one rung in Settings and kept an undo for some of its
    fires only."""
    mixed = {}
    for spec in rg.CORE_ACTION_TYPES:
        if not spec.providers:
            continue
        claims = {name: bool(_real_kinds(name)) for name in spec.providers}
        if len(set(claims.values())) > 1:
            mixed[spec.key] = claims
    assert mixed == {}, f"classes mixing undoable and non-undoable providers: {mixed}"
    rg.ensure_core_action_types()
    assert rg.can_be_undone("action.create_task") and rg.can_be_undone("action.inbox_op")
    assert not rg.can_be_undone("action.selfqa_finding")


@pytest.mark.parametrize("session_key", ["", "unattended:trigger:t", "subagent:worker-1"])
def test_no_provider_runs_with_undo_unless_it_can_undo(_isolated_home, session_key):
    """Per PROVIDER, attended and unattended alike: wherever a dispatch lands on "runs with
    undo", the provider being dispatched names a reversal kind."""
    rg.ensure_core_action_types()
    with_undo = []
    for spec in rg.CORE_ACTION_TYPES:
        for name in spec.providers:
            if rg.route_provider_action(name, session_key=session_key).rung != (
                au.RUNG_AUTO_WITH_UNDO
            ):
                continue
            with_undo.append(name)
            assert _real_kinds(name), f"{name} runs with undo and cannot undo anything"
    assert "inbox-op" in with_undo, "the sweep never reached the undo rung"


def test_the_self_qa_filing_step_runs_on_its_own_and_keeps_no_undo(_isolated_home):
    """It files a task and an inbox row and hands back nothing to undo: it is its own class,
    runs on its own, and its row and its chip say so."""
    _fire_store_trigger(_StandIn("selfqa-file-finding"))
    rows = _executed_rows("action.selfqa_finding")
    assert rows and _audited_rung(rows[-1]) == au.RUNG_AUTONOMOUS, rows
    view = _ladder()
    assert _row(view, "action.selfqa_finding")["resolved_rung"] == au.RUNG_AUTONOMOUS
    assert _row(view, "action.create_task")["resolved_rung"] == au.RUNG_AUTO_WITH_UNDO
    assert view["reversals"] == []


# ── 2. a run is logged at the rung it had ─────────────────────────────────────


def test_a_run_at_the_undo_rung_is_listed_and_can_be_undone(_isolated_home):
    """The REAL `create-task`, unattended: it runs with undo, so it files its task, its row names
    the undo record the panel lists, and the undo endpoint deletes the task it filed."""
    from personalclaw.action_providers.create_task_provider import CreateTaskActionProvider

    tasks = _isolated_home / "tasks"

    def task_files() -> list:
        if not tasks.exists():
            return []
        return [p for p in tasks.glob("*.json") if not p.name.startswith("_")]

    real = CreateTaskActionProvider()
    import personalclaw.action_providers as ap
    from personalclaw.gateway import GatewayOrchestrator

    trigger = types.SimpleNamespace(
        id="clock:file-a-task",
        kind="clock",
        workflow={"inline": {"provider": "create-task", "config": {"title_template": "Water"}}},
        capabilities={"providers": ["create-task"]},
    )
    original = ap.get_action_provider
    try:
        ap.get_action_provider = lambda name: real if name == "create-task" else None
        asyncio.run(
            object.__new__(GatewayOrchestrator)._fire_store_trigger(trigger, {"kind": "clock"})
        )
    finally:
        ap.get_action_provider = original  # type: ignore[assignment]

    assert len(task_files()) == 1, "the task was not filed"
    view = _ladder()
    pending = [r for r in view["reversals"] if not r["reversed_at"]]
    assert len(pending) == 1 and pending[0]["action_type"] == "action.create_task"
    rows = _executed_rows("action.create_task")
    assert rows and _audited_rung(rows[-1]) == au.RUNG_AUTO_WITH_UNDO
    assert f"undo={pending[0]['id']}" in rows[-1]["resources"], rows[-1]["resources"]

    req = make_mocked_request("POST", "/api/autonomy/undo")

    async def _body():
        return {"id": pending[0]["id"]}

    req.json = _body  # type: ignore[method-assign]
    resp = asyncio.run(api_h.api_autonomy_undo(req))
    assert resp.status == 200, resp.body
    assert task_files() == [], "undo must delete the task the action filed"


@pytest.mark.parametrize(
    "handle",
    [
        "",  # it came back with nothing to undo this time
        "task:has/a/slash",  # one the reversal store refuses
    ],
)
def test_a_run_that_kept_no_undo_is_not_logged_at_the_undo_rung(_isolated_home, handle):
    """An action that can be undone, whose run kept no undo record: it ran, so the row says it
    ran on its own, and nothing is offered for undo."""
    stand_in = _StandIn("create-task", ("task",), handle=handle)
    _fire_store_trigger(stand_in)
    assert stand_in.calls, "the run did not happen"
    rows = _executed_rows("action.create_task")
    assert rows and _audited_rung(rows[-1]) == au.RUNG_AUTONOMOUS, rows
    assert "undo=" not in rows[-1]["resources"]
    assert _ladder()["reversals"] == []


# ── 3. an action that cannot be undone never runs at the undo rung ────────────


def test_an_undo_rung_DECLARED_for_an_action_that_cannot_be_undone_asks_first(_isolated_home):
    """Running it would keep no undo, which is running on its own — above the rung it was given.
    So it asks, and the panel and the Inbox row both say that what it does cannot be taken back."""
    action = _install_app_action(floor=au.RUNG_AUTO_WITH_UNDO, ceiling=au.RUNG_AUTO_WITH_UNDO)
    _fire_store_trigger(action)

    assert action.calls == [], "it ran with no undo, under a rung that promises one"
    assert _executed_rows(APP_KEY) == []
    row = _row(_ladder(), APP_KEY)
    assert row["resolved_rung"] == au.RUNG_ONE_TAP
    assert "cannot be taken back" in row["authority"], row["authority"]
    held = [r for r in _inbox_rows(_isolated_home) if r["refs"].get("action_type") == APP_KEY]
    assert held and held[-1]["refs"]["rung"] == au.RUNG_ONE_TAP
    assert "cannot be taken back" in str(held[-1].get("message", "")), held[-1]


def test_a_network_app_clamped_to_the_undo_rung_cannot_run_silently(_isolated_home):
    """The untrusted-ceiling clamp lands a manifest's ``autonomous`` claim on ``auto_with_undo``.
    For an action with no undo that rung ran it silently all the same; now it asks first."""
    action = _install_app_action(floor=au.RUNG_AUTONOMOUS, ceiling=au.RUNG_AUTONOMOUS, network=True)
    assert au.action_type(APP_KEY).ceiling == au.RUNG_AUTO_WITH_UNDO
    _fire_store_trigger(action)
    assert action.calls == []
    row = _row(_ladder(), APP_KEY)
    assert row["resolved_rung"] == au.RUNG_ONE_TAP
    assert "cannot be taken back" in row["authority"]


def test_a_GRANT_of_the_undo_rung_to_an_action_that_cannot_be_undone_still_asks(_isolated_home):
    """The owner's grant is recorded, but it cannot make an undo exist: the run asks first, and
    the panel says both whose grant it is and why it still asks."""
    action = _install_app_action(floor=au.RUNG_ONE_TAP, ceiling=au.RUNG_AUTO_WITH_UNDO)
    assert au.grant_rung(APP_KEY, au.RUNG_AUTO_WITH_UNDO, evidence_window="owner decision")
    _fire_store_trigger(action)
    assert action.calls == []
    row = _row(_ladder(), APP_KEY)
    assert row["resolved_rung"] == au.RUNG_ONE_TAP
    assert "You promoted it" in row["authority"] and "cannot be taken back" in row["authority"]


def test_the_same_declaration_for_an_action_that_CAN_be_undone_runs_with_undo(_isolated_home):
    """The control for the three above: the rule is about the missing undo, not the rung."""
    action = _install_app_action(
        floor=au.RUNG_AUTO_WITH_UNDO, ceiling=au.RUNG_AUTO_WITH_UNDO, kinds=("task",)
    )
    _fire_store_trigger(action)
    assert len(action.calls) == 1
    assert _row(_ladder(), APP_KEY)["resolved_rung"] == au.RUNG_AUTO_WITH_UNDO
    rows = _executed_rows(APP_KEY)
    assert rows and _audited_rung(rows[-1]) == au.RUNG_AUTO_WITH_UNDO


# ── 4. the notice names the rung the run had ──────────────────────────────────


def _capture_notices(monkeypatch) -> list[dict]:
    notices: list[dict] = []

    class _State:
        def notify(self, kind, title, body, *, meta=None):
            notices.append({"title": title, "body": body, "meta": dict(meta or {})})

    class _Services:
        state = _State()

    import personalclaw.action_providers.services as svc

    monkeypatch.setattr(svc, "get_action_services", lambda: _Services())
    return notices


def test_the_undo_notice_does_not_borrow_the_other_rungs_name(_isolated_home, monkeypatch):
    """A run at "runs with undo" was announced as having "ran on its own" — the label of the
    rung that keeps no undo. It says it ran without asking and can be undone."""
    notices = _capture_notices(monkeypatch)
    _fire_store_trigger(_StandIn("create-task", ("task",)))
    assert len(notices) == 1, notices
    body = notices[0]["body"]
    assert "You can still undo it." in body, body
    assert "on its own" not in body, body
    assert notices[0]["meta"]["rung"] == au.RUNG_AUTO_WITH_UNDO
    assert notices[0]["meta"]["reversal_id"]


def test_a_refused_handle_is_announced_as_keeping_nothing_to_undo(_isolated_home, monkeypatch):
    """A handle the store refused: the notice says the action ran and kept nothing to undo, and
    names the rung the run had, the same one its row records."""
    notices = _capture_notices(monkeypatch)
    _fire_store_trigger(_StandIn("create-task", ("task",), handle="task:has/a/slash"))
    assert len(notices) == 1, notices
    assert "kept nothing you can undo" in notices[0]["body"], notices[0]["body"]
    assert "on its own" not in notices[0]["body"]
    assert notices[0]["meta"]["rung"] == au.RUNG_AUTONOMOUS
    assert notices[0]["meta"]["reversal_id"] == ""


# ── 5. promotion never offers the undo rung to an action that cannot be undone ──


def test_the_undo_rung_is_never_proposed_to_an_action_that_cannot_be_undone(_isolated_home):
    """Earned, at "asks first", ceiling "runs with undo": the next rung would change nothing it
    said it would, so nothing is offered, and the record says why."""
    _install_app_action(floor=au.RUNG_ONE_TAP, ceiling=au.RUNG_AUTO_WITH_UNDO)
    _seed_clean_record(APP_KEY)

    el = au.promotion_eligibility(APP_KEY)
    assert el.eligible is False and el.next_rung == ""
    assert "cannot be taken back" in el.reason, el.reason
    row = _row(_ladder(), APP_KEY)
    assert row["eligible"] is False and row["next_rung"] == ""
    assert ld.propose_promotions() == []
    assert not [r for r in _inbox_rows(_isolated_home) if "has earned" in str(r.get("message"))]


def test_its_next_rung_is_the_one_above_the_undo_rung(_isolated_home):
    """With a ceiling above it, an action that cannot be undone climbs from "asks first" straight
    to "runs on its own" — still a click, still only on a clean record."""
    _install_app_action(floor=au.RUNG_ONE_TAP, ceiling=au.RUNG_AUTONOMOUS)
    _seed_clean_record(APP_KEY)
    el = au.promotion_eligibility(APP_KEY)
    assert el.eligible is True and el.next_rung == au.RUNG_AUTONOMOUS, el
    assert ld.propose_promotions() == [APP_KEY]


def test_an_action_that_can_be_undone_is_still_offered_the_undo_rung(_isolated_home):
    """The control: the same record and bounds, with an undo, propose "runs with undo"."""
    _install_app_action(floor=au.RUNG_ONE_TAP, ceiling=au.RUNG_AUTO_WITH_UNDO, kinds=("task",))
    _seed_clean_record(APP_KEY)
    el = au.promotion_eligibility(APP_KEY)
    assert el.eligible is True and el.next_rung == au.RUNG_AUTO_WITH_UNDO, el
