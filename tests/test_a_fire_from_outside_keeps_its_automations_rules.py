"""A fire from outside is its automation firing, held to every rule a fire is held to.

A webhook automation fires when a program posts to its address with a sender token made for it,
and a view automation fires when a surface it is bound to renders past its window. Neither is its
owner pressing Run now: nobody answers what it runs. Measured before this was written, both went
through the dispatch a run by hand takes, which records a run as ``manual``:

* the hourly cap passed over every one (`ScheduleRunStore.count_since` skips ``manual`` rows), and
  nothing asked the cap anyway, since no admission was walked: a webhook automation capped at two
  fires an hour ran on every request;
* the failure streak passed over every one too (`autopause` reads no ``manual`` row), so a webhook
  automation that failed on every request was never paused, and nobody was told;
* the run history labelled each one ``manual``, a Run now its owner never pressed;
* a view's refresh ran during an incident, which suspends every fire.

Now each is admitted as the clock's and an event's fires are (`service.admit_fire`: the incident,
spacing, the hourly cap, quiet hours, the budget, the overlap claim, the capability fence), and
runs through the dispatch every fire runs through (`GatewayOrchestrator._fire_store_trigger`), so
its row is a fire's and the streak pauses it. Run now is the owner's, and stays a run by hand.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from signed_in_gateway import Gateway, signed_in_gateway

from personalclaw.action_providers.base import ActionResult
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

#: The action every automation here runs: one that does nothing but say how it went.
PROBE = "outcome-probe"
SLUG = "build-finished"
AUTOMATION = f"store:webhook:{SLUG}"


class _Probe:
    """An action that records each run and succeeds, or fails when told to."""

    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []
        self.fails = False

    async def execute(self, config: dict, ctx: Any, timeout: int = 30) -> ActionResult:
        self.runs.append({"event": ctx.event, "payload": dict(ctx.payload or {})})
        if self.fails:
            return ActionResult(success=False, error="the build server said no")
        return ActionResult(success=True, stdout="done")


@pytest.fixture
def probe(monkeypatch) -> _Probe:
    from personalclaw.action_providers import registry

    action = _Probe()
    registry._ensure_default_providers_registered()
    monkeypatch.setitem(registry._providers, PROBE, action)
    return action


def _webhook(home: Path, **fields: Any) -> None:
    """A webhook automation its owner made and allowed to run its action."""
    TriggerStore(base_dir=home).upsert(
        Trigger(
            id=f"webhook:{SLUG}",
            name="Build finished",
            kind="webhook",
            created_by="user",
            workflow={"inline": {"provider": PROBE, "config": {}}},
            capabilities={"providers": [PROBE]},
            **fields,
        )
    )


def _stored(home: Path, trigger_id: str = f"webhook:{SLUG}") -> Trigger:
    loaded = TriggerStore(base_dir=home).get(trigger_id)
    assert loaded is not None
    return loaded.trigger


async def _sender(gw: Gateway) -> str:
    """A sender token for the automation, made where its owner makes one."""
    status, body = await gw.as_owner(
        "POST",
        "/api/external-access/clients",
        json={"label": "Build server", "surfaces": ["webhook"], "scope": {"trigger": AUTOMATION}},
    )
    assert status == 200, body
    return str(body["token"])


async def _settle(gw: Gateway) -> None:
    """Wait for every fire the gateway started to end, its claim given back."""
    while gw.state._background_tasks:
        await asyncio.gather(*list(gw.state._background_tasks), return_exceptions=True)


async def _fire(gw: Gateway, token: str) -> tuple[int, Any]:
    """The program posts, as it was told to, and waits for what it started to end."""
    async with aiohttp.ClientSession() as http:
        resp = await http.post(
            gw.url(f"/api/triggers/{AUTOMATION}/fire"),
            data=b"build 214 passed",
            headers={"Authorization": f"Bearer {token}"},
        )
        answer = resp.status, await resp.json(content_type=None)
    await _settle(gw)
    return answer


async def _history(gw: Gateway, automation: str = AUTOMATION) -> list[dict]:
    status, body = await gw.as_owner("GET", f"/api/triggers/{automation}/history?limit=50")
    assert status == 200, body
    return list(body["runs"])


@pytest.mark.asyncio
async def test_a_webhook_fire_past_its_hourly_cap_does_not_run_and_its_history_says_why(
    tmp_path, monkeypatch, probe
):
    """🔴 Red before: all three ran, each recorded ``manual``, and the cap of two held nothing."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _webhook(gw.home, gates={"max_runs_per_hour": 2})
        token = await _sender(gw)

        answers = [await _fire(gw, token) for _ in range(3)]

        assert [status for status, _ in answers[:2]] == [202, 202], answers
        status, held = answers[2]
        assert status == 429, held
        assert held["error"]["code"] == "fire_held"
        assert "as often as its owner allows" in held["error"]["message"]
        assert len(probe.runs) == 2
        assert {run["event"] for run in probe.runs} == {"webhook.fire"}
        rows = await _history(gw)
        ran = [r for r in rows if r["status"] == "success"]
        assert len(ran) == 2 and all(r["trigger"] != "manual" for r in ran), rows
        (suppressed,) = [r for r in rows if r["status"] == "skipped_gate"]
        assert "reaches the cap of 2" in suppressed["error"]
        # Only the fires that ran spent the automation's count, as a clock fire's admission does.
        assert _stored(gw.home).run_count == 2


@pytest.mark.asyncio
async def test_failed_webhook_fires_count_toward_the_streak_that_pauses_the_automation(
    tmp_path, monkeypatch, probe
):
    """🔴 Red before: every failure was a run by hand, which the streak passes over, so the
    automation failed on every request, never paused, and its owner was never told."""
    probe.fails = True
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        told: list[dict] = []
        notify = gw.state.notify

        def _told(kind: str, title: str, body: str, **extra: Any) -> None:
            told.append({"title": title, "body": body, **extra})
            notify(kind, title, body, **extra)

        monkeypatch.setattr(gw.state, "notify", _told)
        _webhook(gw.home, failure_policy={"autopause_after": 2})
        token = await _sender(gw)

        first = await _fire(gw, token)
        second = await _fire(gw, token)
        third = await _fire(gw, token)

        assert (first[0], second[0]) == (202, 202), (first, second)
        assert third[0] == 404, third
        assert len(probe.runs) == 2
        paused = _stored(gw.home)
        assert paused.enabled is False and paused.state == "autopaused", paused.to_dict()
        rows = await _history(gw)
        assert [r["trigger"] for r in rows] == ["failed", "failed"], rows
        assert any(
            (t.get("meta") or {}).get("event") == "automation.needs_attention" for t in told
        ), told


@pytest.mark.asyncio
async def test_run_now_is_still_its_owners_run_by_hand(tmp_path, monkeypatch, probe):
    """Control, the same before and after: Run now is the owner's, so the hourly cap and the
    failure streak pass over it, its row says ``manual``, and testing a broken automation by hand
    neither pauses it nor spends its count."""
    probe.fails = True
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _webhook(gw.home, gates={"max_runs_per_hour": 1}, failure_policy={"autopause_after": 2})

        for _ in range(3):
            status, body = await gw.as_owner("POST", f"/api/triggers/{AUTOMATION}/run")
            assert status == 200, body
            assert body["ok"] is False and "the build server said no" in body["result"], body

        assert len(probe.runs) == 3
        assert {run["event"] for run in probe.runs} == {"manual.run"}
        rows = await _history(gw)
        assert [r["trigger"] for r in rows] == ["manual"] * 3, rows
        kept = _stored(gw.home)
        assert kept.enabled is True and kept.state == "active" and kept.run_count == 0


# ── a view's refresh ──

SURFACE = "dashboard.builds"


def _view(home: Path, **fields: Any) -> None:
    TriggerStore(base_dir=home).upsert(
        Trigger(
            id="view:builds",
            name="Builds tile",
            kind="view",
            created_by="user",
            spec={"surface_binding": SURFACE},
            workflow={"inline": {"provider": PROBE, "config": {}}},
            capabilities={"providers": [PROBE]},
            **fields,
        )
    )


async def _render(gw: Gateway) -> dict:
    status, body = await gw.as_owner("POST", "/api/triggers/view/render", json={"surface": SURFACE})
    assert status == 200, body
    await _settle(gw)
    return dict(body)


@pytest.mark.asyncio
async def test_a_views_refresh_is_its_automations_fire(tmp_path, monkeypatch, probe):
    """🔴 Red before: the refresh was recorded as a run by hand, ``manual``, and so went uncounted
    by the hourly cap and the failure streak."""
    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _view(gw.home)

        answer = await _render(gw)

        assert answer["refreshed"] == ["view:builds"], answer
        assert [run["event"] for run in probe.runs] == ["view.rendered"]
        (row,) = await _history(gw, "store:view:builds")
        assert row["status"] == "success" and row["trigger"] != "manual", row
        assert _stored(gw.home, "view:builds").run_count == 1


@pytest.mark.asyncio
async def test_a_view_does_not_refresh_during_an_incident(tmp_path, monkeypatch, probe):
    """🔴 Red before: the refresh ran its action while incident mode suspended every fire."""
    from personalclaw.guardrails import incident

    async with signed_in_gateway(tmp_path, monkeypatch) as gw:
        _view(gw.home)
        incident.activate("checking a leak")

        answer = await _render(gw)

        assert probe.runs == []
        assert answer["refreshed"] == [], answer
        (served,) = answer["served_cache"]
        assert served["trigger_id"] == "view:builds" and "incident" in served["reason"]
