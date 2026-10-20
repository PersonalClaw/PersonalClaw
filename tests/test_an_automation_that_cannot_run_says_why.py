"""An automation that cannot run its action says so, once, where its owner looks.

A fire reaches its dispatch and finds nothing it can run: the app that provides its action is not
running here, a `{{secret:…}}` its action uses does not resolve, or it names no action at all. The
dispatch used to log a warning and return, so the run left no row, no notice and no Inbox item,
and the automation looked healthy on the Triggers page while it never ran. The refusals beside it
on the same path (a missing grant, the denylist, a held rung) write a Runs-history row.

Now each of the three is a refused run: a `refused` row in its history whose sentence names what is
missing (the app and its action, or the secret by name, never a value), the automation's last run
on the Triggers page reads refused with that sentence, and its owner hears of it once, on the
automation's failure route, until a run gets through or it is refused for another reason.

Driven through the real dispatches (`gateway._fire_store_trigger` and the hand-run one,
`trigger_runs._dispatch_store_action`) against a home of the test's own.
"""

from __future__ import annotations

import asyncio
import types
from unittest.mock import patch

import pytest

import personalclaw.action_providers as AP
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.schedule_history import ScheduleRunStore
from personalclaw.triggers import secrets as S
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

TID = "clock:nightly-post"
NAME = "Nightly post"


class _State:
    """A dashboard state that records what `notify` was called with (`DashboardState.notify`'s
    own shape, so the delivery path and the Inbox's view of an item are both recorded)."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def notify(self, kind, title, body, *, meta=None, **_kw):
        self.sent.append({"kind": kind, "title": title, "body": body, "meta": meta or {}})
        return True


class _Action:
    """An action provider that records the config it was handed."""

    display_name = "Acme Post"

    def __init__(self, name: str = "acme-post") -> None:
        self.name = name
        self.calls: list[dict] = []

    async def execute(self, config, ctx, timeout=30):
        self.calls.append(dict(config))
        return types.SimpleNamespace(success=True, stdout="posted")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """One home for everything the fire reads and writes: the trigger store, the run history and
    the Inbox, through every module that resolves it."""
    for target in (
        "personalclaw.config.loader.config_dir",
        "personalclaw.gateway.config_dir",
        "personalclaw.dashboard.handlers.triggers.config_dir",
    ):
        monkeypatch.setattr(target, lambda: tmp_path, raising=False)
    from personalclaw.action_providers import registry as R

    # Which app served which action in this process starts empty for each test.
    monkeypatch.setattr(R, "_origins", {}, raising=False)
    return tmp_path


def _save(home, *, provider: str = "acme-post", config: dict | None = None, workflow=None):
    TriggerStore(base_dir=home).upsert(
        Trigger(
            id=TID,
            name=NAME,
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 3600},
            capabilities={"providers": [provider]},
            workflow=(
                workflow
                if workflow is not None
                else {"inline": {"provider": provider, "config": dict(config or {})}}
            ),
        )
    )


def _orch(state=None) -> GatewayOrchestrator:
    orch = object.__new__(GatewayOrchestrator)
    orch.dashboard_state = state
    return orch


def _fire(home, *, providers: dict | None = None, state=None) -> None:
    """One fire of the saved trigger, with `providers` as everything the registry holds."""
    held = dict(providers or {})
    real = AP.get_action_provider
    try:
        AP.get_action_provider = lambda name: held.get(name)
        trigger = TriggerStore(base_dir=home).get(TID).trigger
        asyncio.run(_orch(state)._fire_store_trigger(trigger, {"trigger_id": TID}))
    finally:
        AP.get_action_provider = real


def _rows(home) -> list[dict]:
    rows, _total = asyncio.run(ScheduleRunStore(home).list_for_job(TID, 0, 50))
    return rows


def _stored(home):
    return TriggerStore(base_dir=home).get(TID).trigger


def _refusal_notes(state: _State) -> list[dict]:
    return [n for n in state.sent if str(n["title"]).endswith("did not run")]


# ── the refused run ──


def test_a_fire_whose_action_no_app_provides_is_a_refused_run_naming_the_action(home):
    """🔴 THE DEFECT. The fire logged a warning and returned: no row at all."""
    _save(home)
    _fire(home)

    rows = _rows(home)
    assert len(rows) == 1, "the fire must leave its row in the history"
    row = rows[0]
    assert row["status"] == "refused"
    assert row["trigger"] == "refused"
    assert row["error"].startswith("No app running here provides its “acme-post” action.")


def test_the_triggers_page_shows_the_last_run_as_refused_and_why(home):
    """The row the Triggers page lists: its last run reads refused, with the sentence beside it,
    and it has run (it is no longer a row that looks like it waits for its first fire)."""
    from personalclaw.dashboard.handlers.triggers import _schedule_row_for

    _save(home)
    _fire(home)

    row = _rows(home)[0]
    listed = _schedule_row_for(None, TriggerStore(base_dir=home).get(TID))
    assert listed["last_run_status"] == "refused"
    assert listed["last_error"] == row["error"]
    assert listed["last_run_ts"] is not None
    assert _stored(home).last_run_id == row["run_id"]


def test_the_refusal_names_the_app_that_provided_the_action(home, monkeypatch):
    """An app that served the action in this gateway is named, with what became of it: switched off
    (deactivated), removed, failed to start, or running without that action any more."""
    from personalclaw.apps.manifest import AppManifest, ProviderConfig
    from personalclaw.providers import registry as PR

    manifest = AppManifest(
        name="acme-hooks",
        version="1.0.0",
        displayName="Acme Hooks",
        description="Posts to an example endpoint.",
        provider=ProviderConfig(type="action", implementation="provider:create_provider"),
    )
    registry = PR.ProviderRegistry()
    registry.register(manifest, enabled=False)
    monkeypatch.setattr(PR, "_registry", registry)
    record = registry.get("acme-hooks")
    action = _Action()
    # The app's action registers the way an enable registers it, and goes the way a disable does.
    PR.ActionTypeHandler().register(record, action)
    PR.ActionTypeHandler().deregister(record, action)
    _save(home)

    def said() -> str:
        from personalclaw.action_providers.registry import _ensure_default_providers_registered

        _ensure_default_providers_registered()
        trigger = TriggerStore(base_dir=home).get(TID).trigger
        asyncio.run(_orch()._fire_store_trigger(trigger, {"trigger_id": TID}))
        return _rows(home)[0]["error"]

    deactivated = said()
    assert "“Acme Hooks”" in deactivated and "“Acme Post”" in deactivated
    assert "is deactivated" in deactivated

    record.error = "the provider module could not be imported"
    assert "did not start" in said()

    record.error = ""
    record.enabled = True
    assert "no longer provides" in said()

    registry.deregister("acme-hooks")
    removed = said()
    assert "“Acme Hooks”" in removed and "is not installed" in removed


def test_an_unresolved_secret_is_a_refused_run_naming_the_key_and_never_a_value(home):
    """The secret is named by its key. A value the action's other reference resolved to appears
    nowhere: not in the row, not on the trigger, not in the notice."""
    _save(home, config={"title": "{{secret:PRESENT_KEY}} for {{secret:MISSING_KEY}}"})
    action = _Action()
    state = _State()
    values = {"PRESENT_KEY": "fake-value-abc123"}
    with patch.object(S, "default_resolver", lambda key: values.get(key, "")):
        _fire(home, providers={"acme-post": action}, state=state)

    assert action.calls == [], "the action must not run with a secret it cannot fill"
    rows = _rows(home)
    assert [r["status"] for r in rows] == ["refused"]
    assert "“MISSING_KEY”" in rows[0]["error"]
    stored = _stored(home)
    notes = _refusal_notes(state)
    assert len(notes) == 1
    for surface in (str(rows[0]), stored.last_error_summary, str(notes[0])):
        assert "fake-value-abc123" not in surface


def test_an_owned_key_is_refused_with_why_no_action_can_read_it(home):
    _save(home, config={"title": "{{secret:PCSECRET_PROVIDER_KEY}}"})
    action = _Action()
    with patch.object(S, "default_resolver", lambda key: "never-read"):
        _fire(home, providers={"acme-post": action})

    assert action.calls == []
    error = _rows(home)[0]["error"]
    assert "“PCSECRET_PROVIDER_KEY”" in error
    assert "only that setting can read it" in error
    assert "never-read" not in error


def test_a_trigger_with_no_action_is_a_refused_run(home):
    """A row that names no action at all was dropped with a DEBUG line."""
    _save(home, workflow={})
    _fire(home)

    rows = _rows(home)
    assert [r["status"] for r in rows] == ["refused"]
    assert "no action" in rows[0]["error"]


# ── told once ──


def test_the_owner_is_told_once_not_at_every_fire(home):
    """Every fire is recorded; the notice goes out for the first one only."""
    _save(home)
    state = _State()
    for _ in range(3):
        _fire(home, state=state)

    assert [r["status"] for r in _rows(home)] == ["refused"] * 3
    notes = _refusal_notes(state)
    assert len(notes) == 1
    note = notes[0]
    assert note["title"] == f"{NAME} did not run"
    assert "“acme-post”" in note["body"]
    assert note["meta"].get("statusUrl") == f"#/triggers?open={TID}"


def test_a_run_that_gets_through_tells_the_next_refusal_again(home):
    """The cause fixed (the action runs) ends the stretch, so a later refusal is news again."""
    _save(home)
    state = _State()
    _fire(home, state=state)
    _fire(home, providers={"acme-post": _Action()}, state=state)
    _fire(home, state=state)

    assert [r["status"] for r in _rows(home)] == ["refused", "success", "refused"]
    assert len(_refusal_notes(state)) == 2


def test_a_refusal_for_another_reason_is_told(home):
    """The automation changed: it now uses a secret that is not stored. A new reason, a notice."""
    _save(home)
    state = _State()
    _fire(home, state=state)
    _save(home, config={"title": "{{secret:MISSING_KEY}}"})
    with patch.object(S, "default_resolver", lambda key: ""):
        _fire(home, providers={"acme-post": _Action()}, state=state)

    notes = _refusal_notes(state)
    assert len(notes) == 2
    assert "“MISSING_KEY”" in notes[1]["body"]


# ── the positive control ──


def test_a_healthy_automation_still_runs_and_records_success(home):
    _save(home, config={"title": "{{secret:PRESENT_KEY}}"})
    action = _Action()
    state = _State()
    with patch.object(S, "default_resolver", lambda key: "fake-value-abc123"):
        _fire(home, providers={"acme-post": action}, state=state)

    assert action.calls == [{"title": "fake-value-abc123"}]
    assert [r["status"] for r in _rows(home)] == ["success"]
    assert _refusal_notes(state) == []
    assert _stored(home).last_success_at


# ── what a refused fire does not spend ──


def test_a_refused_fire_spends_no_hourly_cap(home):
    """The cap bounds work the machine did; a refused fire did none, and counted it would hold the
    automation's first runs once its app is back."""
    _save(home)
    for _ in range(3):
        _fire(home)
    runs = ScheduleRunStore(home)
    assert len(_rows(home)) == 3
    assert asyncio.run(runs.count_since(TID, 0.0)) == 0

    _fire(home, providers={"acme-post": _Action()})
    assert asyncio.run(runs.count_since(TID, 0.0)) == 1


# ── before the apps start ──


def test_a_fire_before_the_apps_start_waits_for_them(home):
    """The clock and the event router start before the dashboard loads the apps, so a fire in that
    window found an app's action missing. It waits for the apps, then runs."""
    _save(home)
    action = _Action()
    held: dict = {}

    async def scenario() -> bool:
        orch = _orch()
        orch._apps_started = asyncio.Event()
        trigger = TriggerStore(base_dir=home).get(TID).trigger
        fire = asyncio.ensure_future(orch._fire_store_trigger(trigger, {"trigger_id": TID}))
        await asyncio.sleep(0.2)
        waited = not fire.done()
        held["acme-post"] = action
        orch._apps_started.set()
        await asyncio.wait_for(fire, 10)
        return waited

    real = AP.get_action_provider
    try:
        AP.get_action_provider = lambda name: held.get(name)
        waited = asyncio.run(scenario())
    finally:
        AP.get_action_provider = real

    assert waited, "the fire must wait for the apps rather than refuse at once"
    assert len(action.calls) == 1
    assert [r["status"] for r in _rows(home)] == ["success"]


def test_a_fire_whose_apps_never_start_is_refused_after_the_wait(home, monkeypatch):
    import personalclaw.gateway as G

    monkeypatch.setattr(G, "APPS_START_WAIT_SECS", 0.05, raising=False)
    _save(home)

    async def scenario() -> None:
        orch = _orch()
        orch._apps_started = asyncio.Event()
        trigger = TriggerStore(base_dir=home).get(TID).trigger
        await asyncio.wait_for(orch._fire_store_trigger(trigger, {"trigger_id": TID}), 10)

    real = AP.get_action_provider
    try:
        AP.get_action_provider = lambda name: None
        asyncio.run(scenario())
    finally:
        AP.get_action_provider = real

    rows = _rows(home)
    assert [r["status"] for r in rows] == ["refused"]
    assert "No app running here provides" in rows[0]["error"]


# ── the hand-run dispatch: your Run now, your answer, the restart review's Run now ──


@pytest.mark.parametrize("event", ["manual.run", "manual.answer", "review.run_now"])
def test_the_hand_run_dispatch_refuses_in_the_same_words_and_records_it(home, event):
    """A run of yours is refused in the same sentence as a fire, with the same row (saying it was
    yours) and the same one notice."""
    from personalclaw.dashboard.handlers.trigger_runs import _dispatch_store_action

    _save(home)
    state = _State()
    trigger = TriggerStore(base_dir=home).get(TID).trigger
    real = AP.get_action_provider
    try:
        AP.get_action_provider = lambda name: None
        ran, note = asyncio.run(
            _dispatch_store_action(trigger, {"trigger_id": TID}, event=event, state=state)
        )
    finally:
        AP.get_action_provider = real

    assert ran is False
    rows = _rows(home)
    assert [r["status"] for r in rows] == ["refused"]
    assert (rows[0]["trigger"], rows[0]["source"]) == ("refused", "you")
    assert note == rows[0]["error"]
    assert "“acme-post”" in note
    assert len(_refusal_notes(state)) == 1


def _hand_run(home, *, providers: dict, state=None) -> tuple[bool, str]:
    from personalclaw.dashboard.handlers.trigger_runs import _dispatch_store_action

    held = dict(providers)
    trigger = TriggerStore(base_dir=home).get(TID).trigger
    real = AP.get_action_provider
    try:
        AP.get_action_provider = lambda name: held.get(name)
        return asyncio.run(_dispatch_store_action(trigger, {"trigger_id": TID}, state=state))
    finally:
        AP.get_action_provider = real


def test_run_now_fills_a_secret_as_a_fire_does(home):
    """The hand-run dispatch handed the provider `{{secret:KEY}}` itself: a Run now sent the
    placeholder where a fire sent the value."""
    _save(home, config={"title": "{{secret:PRESENT_KEY}}"})
    action = _Action()
    with patch.object(S, "default_resolver", lambda key: "fake-value-abc123"):
        ran, _note = _hand_run(home, providers={"acme-post": action}, state=_State())

    assert ran is True
    assert action.calls == [{"title": "fake-value-abc123"}]


def test_run_now_refuses_a_secret_that_is_not_stored(home):
    _save(home, config={"title": "{{secret:MISSING_KEY}}"})
    action = _Action()
    state = _State()
    with patch.object(S, "default_resolver", lambda key: ""):
        ran, note = _hand_run(home, providers={"acme-post": action}, state=state)

    assert ran is False
    assert action.calls == [], "the action must not run with the placeholder in it"
    rows = _rows(home)
    assert [(r["status"], r["trigger"], r["source"]) for r in rows] == [
        ("refused", "refused", "you")
    ]
    assert note == rows[0]["error"]
    assert "“MISSING_KEY”" in note
    assert len(_refusal_notes(state)) == 1
