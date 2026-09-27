"""A trigger runs only what it was granted, and a restart never grants.

Measured on `main` (2f3b269aa) before any of this was written:

* `POST /api/triggers/{id}/run` — the Run now button, and the route the chat's `automation_run`
  and `schedule_trigger` post to — dispatched a trigger's action without reading its capability
  block. An enabled `bash` schedule with an empty block ran its command.
* The autonomous dispatch (`gateway._fire_store_trigger`) did not read it either. Clock and event
  fires meet the fence in `service.admit_fire` first; a file, web_watch or chained fire goes
  straight to the dispatch, so an ungranted `bash` file trigger ran on the next change.
* An edit that re-pointed an action (`tools.update`: the schedule editor, the chat's
  `automation_update`, the CLI) granted nothing and asked nothing, and every boot's capability
  backfill then granted whatever an ungranted row ran. A `notify` schedule an agent re-pointed at
  `bash` came back from a restart allowed to run `bash`.

The contract: nothing runs an action without the grant it needs; a run that lacks one is refused
with a sentence that names the grant and how the owner gives it; an edit that needs a new grant
asks the owner, or is saved switched off; and nothing that runs unattended grants — a restart
least of all.
"""

from __future__ import annotations

import asyncio
import copy
import json
import types
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import personalclaw.action_providers as AP
import personalclaw.config.loader as loader
from personalclaw.action_providers.base import ActionResult
from personalclaw.dashboard.handlers import triggers as T
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.schedule_history import ScheduleRunStore
from personalclaw.triggers import tools as Tools
from personalclaw.triggers.boot_migrate import migrate_and_arm
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0
_BASH = {"inline": {"provider": "bash", "config": {"command": "touch /tmp/ran"}}}
_NOTIFY = {"inline": {"provider": "notify", "config": {"title_template": "hi"}}}


# ── harness ──


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    monkeypatch.setattr("personalclaw.triggers.boot_migrate.config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def audit(monkeypatch):
    """The security audit rows written."""
    rows: list[dict] = []

    class _Sel:
        def log_api_access(self, **kwargs):
            rows.append(kwargs)

    monkeypatch.setattr("personalclaw.sel.sel", lambda: _Sel())
    return rows


class _Ran:
    """An action provider that records each run instead of doing anything."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, config, ctx, timeout=30):
        self.calls.append(dict(config))
        return ActionResult(success=True, stdout="ran")


@pytest.fixture
def ran(monkeypatch):
    """`bash` and `grant-probe` dispatch to a recorder; every other name resolves as it does.

    `grant-probe` is registered too, so it is a provider the registry can dispatch: an unregistered
    name fails on the unknown name before any grant is asked about (`grants.missing`).
    """
    from personalclaw.action_providers import registry

    recorder = _Ran()
    registry._ensure_default_providers_registered()
    monkeypatch.setitem(registry._providers, "grant-probe", recorder)
    real = AP.get_action_provider
    monkeypatch.setattr(
        AP,
        "get_action_provider",
        lambda name: recorder if name in ("bash", "grant-probe") else real(name),
    )
    return recorder


def _store(home: Path) -> TriggerStore:
    return TriggerStore(base_dir=home)


def _schedule(home: Path, tid="nightly", *, workflow=_BASH, capabilities=None, enabled=True):
    _store(home).upsert(
        Trigger(
            id=tid,
            name=f"Job {tid}",
            kind="clock",
            enabled=enabled,
            created_by="user",
            spec={"kind": "cron", "expr": "0 9 * * *"},
            workflow=copy.deepcopy(workflow),
            capabilities=dict(capabilities or {}),
        )
    )


def _row(home: Path, tid: str) -> Trigger:
    loaded = _store(home).get(tid)
    assert loaded is not None, f"{tid} is not in triggers.json"
    return loaded.trigger


def _req(method: str, path: str, *, body: dict, match_info: dict, headers: dict | None = None):
    app = web.Application()
    app["state"] = types.SimpleNamespace(push_refresh=lambda *k: None, _background_tasks=set())
    req = make_mocked_request(method, path, match_info=match_info, app=app, headers=headers)
    req["user"] = "owner"

    async def _json():
        return body

    req.json = _json  # type: ignore[assignment]
    return req


def _body(resp: web.Response) -> dict:
    return json.loads(resp.body.decode())


def _run(trigger_id: str) -> dict:
    return _body(
        asyncio.run(
            T.api_trigger_run(
                _req(
                    "POST",
                    f"/api/triggers/{trigger_id}/run",
                    body={},
                    match_info={"id": trigger_id},
                )
            )
        )
    )


def _edit(trigger_id: str, **body) -> web.Response:
    """A save from the schedule editor: it names the revision it read, as the page does
    (`trigger_revisions` refuses a whole-form save that does not)."""
    _kind, raw = T._split_id(trigger_id)
    listed = T._schedule_row_for(types.SimpleNamespace(), T._trigger_store().get(raw))
    return asyncio.run(
        T.api_trigger_detail(
            _req(
                "PUT",
                f"/api/triggers/{trigger_id}",
                body=body,
                match_info={"id": trigger_id},
                headers={"If-Match": f'"{listed["revision"]}"'},
            )
        )
    )


def _toggle(trigger_id: str, **body) -> web.Response:
    return asyncio.run(
        T.api_trigger_toggle(
            _req(
                "POST",
                f"/api/triggers/{trigger_id}/toggle",
                body=body,
                match_info={"id": trigger_id},
            )
        )
    )


# ── 🔴 clause 1: a run checks the grant, for every trigger ──


def test_run_now_refuses_an_action_the_trigger_was_not_granted(home, ran):
    """🔴 Red on main: the command ran. Not an imported row — any row whose block lacks its grant.
    The refusal names the grant and where the owner gives it."""
    _schedule(home)

    answer = _run("schedule:nightly")

    assert answer["ok"] is False
    assert ran.calls == []
    refused = answer["refused"]
    assert "not allowed to use the “Bash Command” action" in refused
    assert "Triggers page" in refused and "Allow" in refused


def test_run_now_says_to_switch_on_a_trigger_that_is_off(home, ran):
    """The way to give the grant depends on the switch: a row that is off is allowed by switching
    it on, which asks first."""
    _schedule(home, enabled=False)

    refused = _run("schedule:nightly")["refused"]

    assert "Switch it on" in refused
    assert ran.calls == []


def test_run_now_checks_a_store_trigger_the_same_way(home, ran):
    _store(home).upsert(
        Trigger(
            id="event:deploy",
            name="deploy",
            kind="event",
            enabled=True,
            created_by="user",
            spec={"source": "memory", "pattern": "MemoryKeyPattern", "key_glob": "project.*"},
            workflow=copy.deepcopy(_BASH),
        )
    )

    answer = _run("store:event:deploy")

    assert answer["ok"] is False and "“Bash Command”" in answer["refused"]
    assert ran.calls == []


def test_a_granted_trigger_still_runs_now(home, ran):
    """The floor: the check refuses only what the block does not cover."""
    _schedule(home, capabilities={"providers": ["bash"]})

    assert _run("schedule:nightly")["ok"] is True
    assert ran.calls == [{"command": "touch /tmp/ran"}]


def test_the_chat_automation_run_refuses_before_its_runner(home):
    """🔴 Red on main: the runner was called. `automation_run` hands the id to `/run`; it answers
    first, so the agent is told who can allow it."""
    _schedule(home)
    calls: list[dict] = []

    result = Tools.run(_store(home), trigger_id="nightly", runner=calls.append)

    assert not result.ok
    assert calls == []
    assert "not allowed to use the “Bash Command” action" in result.text
    assert "owner" in result.text


def test_schedule_trigger_passes_the_refusal_on(monkeypatch):
    """🔴 Red on main: `schedule_trigger` (and `personalclaw cron trigger`) read a refusal as
    "trigger failed", dropping the sentence that says what to do."""
    from personalclaw import schedule_trigger

    sentence = "“Job abc123” is not allowed to use the “Bash Command” action, so it did not run."
    monkeypatch.setattr(
        "personalclaw.mcp_core._post",
        lambda path, body=None: {"ok": False, "name": "Job abc123", "refused": sentence},
    )

    assert schedule_trigger.trigger_schedule_job("abc123") == (False, sentence)


def test_the_restart_review_keeps_its_card_when_the_grant_is_missing(home, ran):
    """🔴 Red on main: Run now on a missed-runs card ran the ungranted command. Refused BEFORE the
    card is taken, like the kill switch, so the decision is still waiting."""
    from personalclaw.triggers import review

    _schedule(home)
    review.record(
        [review.ReviewCard(trigger_id="nightly", kind="missed", count=1, latest=NOW, oldest=NOW)],
        base_dir=home,
    )
    request = _req(
        "POST",
        "/api/triggers/review",
        body={"trigger_id": "nightly", "kind": "missed", "action": "run_now"},
        match_info={},
    )

    answer = _body(asyncio.run(T.api_trigger_review(request)))

    assert answer["ok"] is False and "“Bash Command”" in answer["refused"]
    assert ran.calls == []
    assert [card.trigger_id for card in review.pending(base_dir=home)] == ["nightly"]


def test_a_view_refresh_does_not_run_an_ungranted_action(home, monkeypatch):
    """🔴 Red on main: a `view` trigger bound to a surface refreshed into `bash` with no grant."""
    dispatched: list[str] = []

    async def _spy(trigger, payload, *, event="manual.run"):
        dispatched.append(trigger.id)
        return True, "ran"

    monkeypatch.setattr(T, "_dispatch_store_action", _spy)
    _store(home).upsert(
        Trigger(
            id="view:tile",
            name="tile",
            kind="view",
            enabled=True,
            created_by="user",
            spec={"surface_binding": "artifact.notes"},
            workflow=copy.deepcopy(_BASH),
        )
    )
    state = types.SimpleNamespace(_background_tasks=set())
    app = web.Application()
    app["state"] = state
    request = make_mocked_request("POST", "/api/triggers/view/render", app=app)

    async def _json():
        return {"surface": "artifact.notes"}

    request.json = _json  # type: ignore[assignment]

    async def _render():
        answer = _body(await T.api_trigger_view_render(request))
        await asyncio.gather(*state._background_tasks)
        return answer

    answer = asyncio.run(_render())

    assert dispatched == []
    assert answer["refreshed"] == []
    (served,) = answer["served_cache"]
    assert served["trigger_id"] == "view:tile"
    assert "not allowed to use the “Bash Command” action" in served["reason"]


def test_the_dispatch_itself_refuses_so_no_caller_can_forget(home, ran):
    """🔴 Red on main. Every attended caller — Run now, the review's Run now, a view refresh, a
    webhook fire — reaches the action through `_dispatch_store_action`, so the refusal lives there
    too, not only in the callers that remembered to ask first."""
    _schedule(home)

    done, note = asyncio.run(
        T._dispatch_store_action(_row(home, "nightly"), {"trigger_id": "nightly"})
    )

    assert done is False and "not allowed to use the “Bash Command” action" in note
    assert ran.calls == []


# ── 🔴 the unattended dispatch checks it too ──


def _fire(trigger) -> None:
    asyncio.run(
        object.__new__(GatewayOrchestrator)._fire_store_trigger(
            trigger, {"trigger_id": trigger.id, "kind": trigger.kind}, event="file.changed"
        )
    )


def test_a_file_fire_refuses_an_action_it_was_not_granted_and_says_so(home, ran):
    """🔴 Red on main: the file fire ran it. Clock and event fires meet the fence in `admit_fire`;
    file, web_watch and chained fires reach the dispatch without it, so the dispatch checks."""
    trigger = types.SimpleNamespace(
        id="file:notes",
        kind="file",
        workflow={"inline": {"provider": "grant-probe", "config": {}}},
        capabilities={},
    )

    _fire(trigger)

    assert ran.calls == []
    runs, total = asyncio.run(ScheduleRunStore(home).list_for_job("file:notes", 0, 20))
    assert total == 1
    assert "not allowed to use the “grant-probe” action" in runs[0]["error"]


def test_a_granted_file_fire_still_runs(home, ran):
    trigger = types.SimpleNamespace(
        id="file:notes",
        kind="file",
        workflow={"inline": {"provider": "grant-probe", "config": {}}},
        capabilities={"providers": ["grant-probe"]},
    )

    _fire(trigger)

    assert ran.calls == [{}]


# ── 🔴 clause 2: an edit grants at edit time, or saves the trigger switched off ──


def test_editing_an_action_to_bash_asks_first_and_changes_nothing(home, audit):
    """🔴 Red on main: the edit saved `bash` with no grant and no question."""
    _schedule(home, workflow=_NOTIFY)

    asked = _edit("schedule:nightly", action={"provider": "bash", "config": {"command": "id"}})

    assert asked.status == 400
    error = _body(asked)["error"]
    assert error["code"] == "confirmation_required"
    assert "“Bash Command”" in error["detail"]["consent"]
    assert _row(home, "nightly").workflow == _NOTIFY
    assert [(r["operation"], r["outcome"]) for r in audit] == [("trigger.grant", "denied")]


def test_the_owner_consents_once_and_run_now_works_straight_away(home, audit, ran):
    """🔴 Clause 3, end to end with no restart: edit a schedule to `bash`, give consent once, and
    Run now runs it. On main the edit saved no grant, so there was nothing to consent to and the
    run only worked because the run path checked nothing."""
    _schedule(home, workflow=_NOTIFY)

    saved = _edit(
        "schedule:nightly", action={"provider": "bash", "config": {"command": "id"}}, confirm=True
    )

    assert saved.status == 200, _body(saved)
    trigger = _row(home, "nightly")
    assert trigger.capabilities == {"providers": ["bash"]}
    assert trigger.enabled is True
    assert ("trigger.grant", "success") in [(r["operation"], r["outcome"]) for r in audit]
    assert _run("schedule:nightly")["ok"] is True
    assert ran.calls == [{"command": "id"}]


def test_a_yes_to_a_save_without_its_action_grants_nothing(home):
    """The owner's `confirm: true` answers the question the editor asked, and that question names
    the action being saved. A save that carries no action (a rename) was asked nothing, so it grants
    nothing, even on a trigger that lacks its grant."""
    _schedule(home)

    saved = _edit("schedule:nightly", name="Renamed", confirm=True)

    assert saved.status == 200, _body(saved)
    trigger = _row(home, "nightly")
    assert trigger.name == "Renamed"
    assert trigger.capabilities == {}


def test_an_edit_that_keeps_a_granted_action_asks_nothing(home):
    """The floor: re-saving the action a trigger is already allowed to run is not a new grant. A
    new command is (`test_a_grant_is_for_the_action_the_owner_allowed`)."""
    _schedule(home, capabilities={"providers": ["bash"]})

    saved = _edit(
        "schedule:nightly", action={"provider": "bash", "config": {"command": "touch /tmp/ran"}}
    )

    assert saved.status == 200, _body(saved)


def test_the_chat_editing_an_action_saves_it_switched_off_and_grants_nothing(home):
    """🔴 Red on main: `automation_update` re-pointed an owner's `notify` schedule at `bash` and
    left it switched on. An agent cannot give the owner's consent, so the edit is kept and the
    trigger is switched off until the owner allows it."""
    _schedule(home, workflow=_NOTIFY)

    result = Tools.update(
        _store(home),
        trigger_id="nightly",
        patch={"workflow": {"inline": {"provider": "bash", "config": {"command": "id"}}}},
    )

    assert result.ok
    assert "switched off" in result.text and "Triggers page" in result.text
    trigger = _row(home, "nightly")
    assert trigger.enabled is False
    assert trigger.next_fire_at == ""
    assert trigger.capabilities == {}


def test_the_chat_cannot_switch_an_ungranted_trigger_on(home):
    """🔴 Red on main: `automation_resume` and `automation_update {enabled: true}` switched on a
    row whose action it was not allowed to run."""
    _schedule(home, enabled=False)
    store = _store(home)

    resumed = Tools.set_paused(store, trigger_id="nightly", paused=False)
    patched = Tools.update(store, trigger_id="nightly", patch={"enabled": True})

    assert not resumed.ok and "“Bash Command”" in resumed.text
    assert not patched.ok and "“Bash Command”" in patched.text
    assert _row(home, "nightly").enabled is False


def test_allow_on_a_trigger_that_is_on_asks_then_grants(home, audit):
    """The owner's way to give a grant a trigger that is on lacks: the panel's Allow sends
    `enabled: true` to the toggle, which asks first."""
    _schedule(home)

    asked = _toggle("schedule:nightly", enabled=True)
    assert asked.status == 400
    assert _row(home, "nightly").capabilities == {}

    allowed = _toggle("schedule:nightly", enabled=True, confirm=True)
    assert allowed.status == 200, _body(allowed)
    assert _row(home, "nightly").capabilities == {"providers": ["bash"]}
    assert _row(home, "nightly").enabled is True


# ── 🔴 a restart never grants ──


def test_a_restart_never_grants(home):
    """🔴 Red on main: the boot's capability backfill granted `bash` to the row an edit had left
    ungranted. Nothing that runs unattended grants — a boot least of all."""
    _schedule(home)

    migrate_and_arm(home, now=NOW)
    migrate_and_arm(home, now=NOW)

    assert _row(home, "nightly").capabilities == {}


# ── the same shape, found next to it ──


def test_a_pack_cannot_ship_its_own_grant(home):
    """🔴 Red on main: a pack's staged trigger kept the capability block the pack wrote, so
    switching it on asked nothing. A pack is somebody else's file; its grants are not the owner's.
    """
    from personalclaw.packs.triggers import deploy_triggers, staged_triggers_dir

    staged = staged_triggers_dir(home, "st1")
    staged.mkdir(parents=True)
    (staged / "clock-pack.json").write_text(
        json.dumps(
            {
                "id": "clock:pack",
                "name": "from a pack",
                "kind": "clock",
                "enabled": True,
                "spec": {"kind": "cron", "expr": "0 9 * * *"},
                "workflow": _BASH,
                "capabilities": {"providers": ["bash"]},
            }
        )
    )

    assert deploy_triggers("st1", home=home)["deployed"] == ["clock:pack"]
    trigger = _row(home, "clock:pack")
    assert trigger.enabled is False
    assert trigger.capabilities == {}


def test_the_page_is_told_what_each_trigger_is_not_allowed_to_use(home):
    """The list badges it and the panel offers Allow, from the server's own answer."""
    _schedule(home)
    _schedule(home, "granted", capabilities={"providers": ["bash"]})
    rows = {row.trigger.id: row for row in _store(home).load()}
    state = types.SimpleNamespace()

    assert T._serialize_store(rows["nightly"])["needs_grant"] == ["Bash Command"]
    assert T._serialize_store(rows["granted"])["needs_grant"] == []
    assert T._schedule_row_for(state, rows["nightly"])["needs_grant"] == ["Bash Command"]
