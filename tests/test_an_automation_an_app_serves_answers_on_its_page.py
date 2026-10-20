"""An automation an app serves answers on its Triggers page, every route of it.

The page LISTS an app's rows beside her own (`provider.all_rows`), and every route that acts on one
row looked it up in this home's own automations file alone. So for a reminder an app served, or her
own row in a shared automations file, History and Dry run answered "not found", Run now and Save
answered "not found", and so did Delete and the Enabled switch. And a row someone else wrote, which
this machine never runs, was refused only by that same "not found", while its panel offered every
action.

Now each route finds the row where it lives. Hers can be run, rehearsed, switched and deleted; an
edit says plainly that the app keeps what the automation is. A row someone else wrote says it is
read-only, on every route that would act on it, and nothing is written. Driven through the real
gateway with its sign-in in front.
"""

from __future__ import annotations

from typing import Any

import pytest
from signed_in_gateway import Gateway, signed_in_gateway
from test_an_automation_an_app_adds_fires_without_a_restart import ItemsStore
from test_triggers_write_back import FileProviderStore

from personalclaw.action_providers.base import ActionResult
from personalclaw.triggers import ownership as OWN
from personalclaw.triggers import registry as TREG
from personalclaw.triggers import routing as ROUTE
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

OWNER = "noor"
PROBE = "app-row-probe"
REMINDER = "dayplanner:reminder:bins"
WATCH = "dayplanner:watch:kitchen"
#: A reminder's time well after the test: it is never due while the test runs.
FAR_OFF = 4_000_000_000.0


class _Probe:
    """An action that records each run."""

    def __init__(self) -> None:
        self.runs: list[str] = []

    async def execute(self, config: dict, ctx: Any, timeout: int = 30) -> ActionResult:
        self.runs.append(str((ctx.payload or {}).get("trigger_id") or ""))
        return ActionResult(success=True, stdout="done")


@pytest.fixture
def probe(monkeypatch) -> _Probe:
    from personalclaw.action_providers import registry

    action = _Probe()
    registry._ensure_default_providers_registered()
    monkeypatch.setitem(registry._providers, PROBE, action)
    return action


@pytest.fixture(autouse=True)
def _owner(monkeypatch):
    monkeypatch.setattr(OWN, "owner_username", lambda: OWNER)


@pytest.fixture(autouse=True)
def _clean_registry():
    for name in list(TREG.registered_stores()):
        TREG.unregister_trigger_store(name)
    ROUTE.clear_quarantine()
    yield
    for name in list(TREG.registered_stores()):
        TREG.unregister_trigger_store(name)
    ROUTE.clear_quarantine()


def _planner(gw: Gateway) -> ItemsStore:
    store = ItemsStore(gw.home.parent / "apps" / "dayplanner" / "items.json", action=PROBE)
    TREG.register_trigger_store(store.name, store)
    store.add_reminder("bins", "Put the bins out", at=FAR_OFF)
    return store


def _shared(gw: Gateway) -> FileProviderStore:
    store = FileProviderStore(
        gw.home.parent / "Sync" / "family" / "automations.json", name="shared"
    )
    TREG.register_trigger_store(store.name, store)
    store.seed(_shared_row("bins-out", author=OWNER), _shared_row("sams-plants", author="sam"))
    return store


def _shared_row(tid: str, *, author: str) -> Trigger:
    return Trigger(
        id=tid,
        name=f"Shared {tid}",
        kind="clock",
        enabled=True,
        author=author,
        spec={"kind": "interval", "interval_secs": 3600},
        workflow={"provider": PROBE, "config": {}},
        capabilities={"providers": [PROBE]},
    )


def _local_ids(gw: Gateway) -> list[str]:
    return [row.trigger.id for row in TriggerStore(base_dir=gw.home).load()]


async def _listed(gw: Gateway, wire_id: str) -> dict[str, Any]:
    status, body = await gw.as_owner("GET", "/api/triggers")
    assert status == 200, body
    rows = {row["id"]: row for row in body["triggers"]}
    assert wire_id in rows, sorted(rows)
    return rows[wire_id]


# ── her own reminder, kept by an app ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_her_reminder_an_app_keeps_can_be_rehearsed_run_switched_and_deleted(
    tmp_path, monkeypatch, probe
):
    """🔴 Before: History, Dry run, Run now, the switch and Delete all answered 404 "not found"."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        planner = _planner(gw)
        wire = f"schedule:{REMINDER}"

        listed = await _listed(gw, wire)
        assert listed["served_by"] == "Day Planner"
        assert listed["read_only"] is False

        status, body = await gw.as_owner("GET", f"/api/triggers/{wire}/history")
        assert status == 200, body
        assert body["runs"] == []

        status, body = await gw.as_owner("POST", f"/api/triggers/{wire}/run?dry_run=1", json={})
        assert status == 200, body
        assert body["would_run"]["provider"] == PROBE
        assert probe.runs == []

        status, body = await gw.as_owner("POST", f"/api/triggers/{wire}/run", json={})
        assert status == 200, body
        assert body["ok"] is True, body
        assert probe.runs == [REMINDER]
        status, body = await gw.as_owner("GET", f"/api/triggers/{wire}/history")
        assert [run["status"] for run in body["runs"]] == ["success"], body

        status, body = await gw.as_owner(
            "POST", f"/api/triggers/{wire}/toggle", json={"enabled": False}
        )
        assert status == 200, body
        assert planner.get(REMINDER).trigger.enabled is False
        status, body = await gw.as_owner(
            "POST", f"/api/triggers/{wire}/toggle", json={"enabled": True}
        )
        assert status == 200, body
        assert planner.get(REMINDER).trigger.enabled is True

        status, body = await gw.as_owner("DELETE", f"/api/triggers/{wire}")
        assert status == 200, body
        assert planner.get(REMINDER) is None
        assert _local_ids(gw) == []


@pytest.mark.asyncio
async def test_saving_an_edit_to_a_row_an_app_keeps_says_where_it_is_changed(
    tmp_path, monkeypatch, probe
):
    """The app keeps what the reminder is (its title, its time), and takes back only what running
    it changes, so an edit saved here would not be kept. 🔴 Before: Save failed with "not found"."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        planner = _planner(gw)
        status, body = await gw.as_owner(
            "PUT", f"/api/triggers/schedule:{REMINDER}", json={"name": "Bins and recycling"}
        )
        assert status == 409, body
        assert body["error"]["code"] == "automation_kept_elsewhere"
        assert "Day Planner" in body["error"]["message"]
        assert planner.get(REMINDER).trigger.name == "Put the bins out"
        assert _local_ids(gw) == []


@pytest.mark.asyncio
async def test_her_watch_an_app_keeps_answers_on_its_page(tmp_path, monkeypatch, probe):
    """The same for an app's file watch, listed among the store kinds."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        planner = _planner(gw)
        planner.add_watch("kitchen", f"{tmp_path}/Kitchen/*.pdf", "Kitchen PDFs")
        wire = f"store:{WATCH}"
        assert (await _listed(gw, wire))["served_by"] == "Day Planner"
        status, body = await gw.as_owner("GET", f"/api/triggers/{wire}/history")
        assert status == 200, body
        status, body = await gw.as_owner("POST", f"/api/triggers/{wire}/run", json={})
        assert status == 200 and body["ok"] is True, body
        assert probe.runs == [WATCH]
        status, body = await gw.as_owner(
            "POST", f"/api/triggers/{wire}/toggle", json={"enabled": False}
        )
        assert status == 200, body
        assert planner.get(WATCH).trigger.enabled is False
        status, body = await gw.as_owner("DELETE", f"/api/triggers/{wire}")
        assert status == 200, body
        assert planner.get(WATCH) is None


@pytest.mark.asyncio
async def test_a_missed_run_of_her_app_reminder_stays_on_the_review(tmp_path, monkeypatch, probe):
    """The review kept a card for the app's reminder and then dropped it the first time the page
    read it: its lookup found no such automation in this home's file."""
    from personalclaw.triggers import review

    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _planner(gw)
        slot = 1_800_000_000.0
        review.record(
            [
                review.ReviewCard(
                    trigger_id=REMINDER, kind=review.MISSED, count=1, latest=slot, oldest=slot
                )
            ],
            base_dir=gw.home,
        )
        status, body = await gw.as_owner("GET", "/api/triggers/review")
        assert status == 200, body
        assert [card["trigger_id"] for card in body["cards"]] == [REMINDER]
        assert [c.trigger_id for c in review.pending(base_dir=gw.home)] == [REMINDER]


# ── her own row in a shared file ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_her_row_in_a_shared_file_can_be_run_switched_and_deleted(
    tmp_path, monkeypatch, probe
):
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        shared = _shared(gw)
        wire = "schedule:bins-out"
        assert (await _listed(gw, wire))["served_by"] == "shared"
        status, body = await gw.as_owner("POST", f"/api/triggers/{wire}/run", json={})
        assert status == 200 and body["ok"] is True, body
        assert probe.runs == ["bins-out"]
        status, body = await gw.as_owner(
            "POST", f"/api/triggers/{wire}/toggle", json={"enabled": False}
        )
        assert status == 200, body
        assert shared.get("bins-out").trigger.enabled is False
        status, body = await gw.as_owner("DELETE", f"/api/triggers/{wire}")
        assert status == 200, body
        assert shared.get("bins-out") is None
        assert shared.get("sams-plants") is not None, "only hers went"
        assert _local_ids(gw) == []


@pytest.mark.asyncio
async def test_a_delete_the_app_does_not_make_says_the_row_is_still_there(
    tmp_path, monkeypatch, probe
):
    """An app can keep a row it was asked to delete. 🔴 The answer was "deleted" over a row the
    page still listed."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        shared = _shared(gw)
        shared.noop_delete = True
        status, body = await gw.as_owner("DELETE", "/api/triggers/schedule:bins-out")
        assert status == 409, body
        assert body["error"]["code"] == "automation_kept_elsewhere"
        assert "was not deleted" in body["error"]["message"]
        assert shared.get("bins-out") is not None


# ── a row someone else wrote ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_row_someone_else_wrote_says_it_is_read_only_on_every_route_that_acts(
    tmp_path, monkeypatch, probe
):
    """🔴 Before: each of these answered 404 "not found", and nothing said why."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        shared = _shared(gw)
        wire = "schedule:sams-plants"
        listed = await _listed(gw, wire)
        assert listed["read_only"] is True and listed["author"] == "sam"

        acts = [
            ("POST", f"/api/triggers/{wire}/run", {}),
            ("POST", f"/api/triggers/{wire}/run?dry_run=1", {}),
            ("POST", f"/api/triggers/{wire}/toggle", {"enabled": False}),
            ("PUT", f"/api/triggers/{wire}", {"name": "Mine now"}),
            ("DELETE", f"/api/triggers/{wire}", None),
        ]
        for method, path, payload in acts:
            kwargs = {} if payload is None else {"json": payload}
            status, body = await gw.as_owner(method, path, **kwargs)
            assert status == 409, (method, path, body)
            assert body["error"]["code"] == "automation_read_only", (method, path, body)
            assert "sam wrote this automation" in body["error"]["message"], body

        assert probe.runs == []
        assert shared.upserts == []
        kept = shared.get("sams-plants").trigger
        assert (kept.name, kept.enabled) == ("Shared sams-plants", True)
        # Reading it stays open: what it is and its (empty) history.
        status, body = await gw.as_owner("GET", f"/api/triggers/{wire}/history")
        assert status == 200, body


@pytest.mark.asyncio
async def test_a_schedule_that_can_never_fire_is_listed_as_needing_attention(
    tmp_path, monkeypatch, probe
):
    """A row hand-written in a shared file with a cron the clock refuses: no tick can arm it.
    🔴 Before: listed switched on as "At 87:99 PM", with nothing to say it would never run."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        shared = _shared(gw)
        typo = _shared_row("bins-typo", author=OWNER)
        typo.spec = {"kind": "cron", "expr": "99 99 * * *"}
        shared.seed(typo)
        listed = await _listed(gw, "schedule:bins-typo")
        assert any("not a valid cron expression" in said for said in listed["broken"]), listed
