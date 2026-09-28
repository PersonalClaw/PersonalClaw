"""A stop or a Restart records the run it cut off, as the boot pass records one whose owner died.

A stop cancels the loops a fire runs in, and a cancellation is not an `Exception`: the dispatch's
`except Exception`, which records a failed fire, never saw it, and the executor's `finally` gave
the claim back — so the boot pass (`reaper.terminalize_orphans`) had nothing to close either. The
run ended with no row in its history and no card on the review. A Run now held no claim at all,
and a Restart re-execs the gateway in the same process, so even a claim left behind named a pid
that was alive and read as in flight until the deadline reaper called it hung.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import pytest

from personalclaw import restart_request
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.schedule_history import ScheduleRunStore
from personalclaw.triggers import claims, reaper, review
from personalclaw.triggers.models import Trigger, TriggerHealth
from personalclaw.triggers.scheduling import PROCESS_IMAGE, Claim
from personalclaw.triggers.store import TriggerStore

TID = "clock:nightly-backup"


class _Blocks:
    """A provider whose action is still running when the gateway stops."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def execute(self, config: Any, ctx: Any, timeout: float = 30) -> Any:
        self.started.set()
        await asyncio.sleep(3600)


class _Notes:
    """The dashboard's notification sink, keeping what it was sent."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def notify(self, kind: str, title: str, body: str, *, meta: dict | None = None) -> None:
        self.sent.append({"kind": kind, "title": title, "body": body, "meta": meta or {}})

    def push_refresh(self, *kinds: str) -> None:
        pass


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.gateway.config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.triggers.config_dir", lambda: tmp_path, raising=False
    )
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(
        Trigger(
            id=TID,
            name="Nightly backup",
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 3600},
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )
    return tmp_path


def _restarting(monkeypatch, yes: bool) -> None:
    request = restart_request.RestartRequest("python", ("python",), {}) if yes else None
    monkeypatch.setattr(restart_request, "pending", lambda: request)


def _rows(home) -> list[dict[str, Any]]:
    rows, _total = asyncio.run(ScheduleRunStore(home).list_for_job(TID, 0, 10))
    return rows


async def _cut_off(run: Any, provider: _Blocks) -> None:
    """Start *run*, wait until its action is running, then stop it the way a stop does."""
    task = asyncio.ensure_future(run)
    await asyncio.wait_for(provider.started.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def _gateway(monkeypatch, provider: _Blocks) -> tuple[GatewayOrchestrator, _Notes, list]:
    monkeypatch.setattr("personalclaw.action_providers.get_action_provider", lambda name: provider)
    orch = object.__new__(GatewayOrchestrator)
    notes = _Notes()
    orch.dashboard_state = notes  # type: ignore[assignment]
    chained: list = []

    async def _chain(trigger: Any, payload: Any) -> None:
        chained.append(trigger.id)

    orch._fire_chained_triggers = _chain  # type: ignore[method-assign]
    return orch, notes, chained


class TestAFireAStopCutsOff:
    def test_is_recorded_as_interrupted_by_the_restart(self, home, monkeypatch):
        """🔴 Red before: the cancellation passed the dispatch's `except Exception`, and the run
        left no row, no card and no notice."""
        _restarting(monkeypatch, True)
        provider = _Blocks()
        orch, notes, _chained = _gateway(monkeypatch, provider)
        trigger = TriggerStore(base_dir=home).get(TID).trigger

        asyncio.run(_cut_off(orch._fire_store_trigger(trigger, {"trigger_id": TID}), provider))

        (row,) = _rows(home)
        assert row["status"] == reaper.RESTART_INTERRUPTED_STATUS
        assert row["error"].startswith("Interrupted by a gateway restart: ")
        assert "run it again from the review on the Triggers page" in row["error"]
        cards = review.pending(base_dir=home)
        assert [(c.trigger_id, c.kind) for c in cards] == [(TID, review.INTERRUPTED)]
        assert cards[0].reason == row["error"]
        after = TriggerStore(base_dir=home).get(TID).trigger
        assert after.health_status == TriggerHealth.DEGRADED.value
        assert after.last_error_summary == row["error"]
        (note,) = notes.sent
        assert note["title"] == "Nightly backup was interrupted by a restart"
        assert note["body"] == row["error"]
        assert note["meta"]["statusUrl"] == f"#/triggers?open={TID}"

    def test_a_plain_stop_says_it_stopped(self, home, monkeypatch):
        """A stop that is not a restart must not be called one: the gateway may never come back."""
        _restarting(monkeypatch, False)
        provider = _Blocks()
        orch, notes, _chained = _gateway(monkeypatch, provider)
        trigger = TriggerStore(base_dir=home).get(TID).trigger

        asyncio.run(_cut_off(orch._fire_store_trigger(trigger, {"trigger_id": TID}), provider))

        (row,) = _rows(home)
        assert row["error"].startswith("Interrupted when the gateway stopped: ")
        assert "restart" not in row["error"]
        assert notes.sent[0]["title"] == "Nightly backup was interrupted when the gateway stopped"

    def test_starts_no_chain(self, home, monkeypatch):
        """🔴 Red before: the dispatch's `finally` fired the `run_completed` chain for a run that
        did not complete, while the gateway was stopping."""
        _restarting(monkeypatch, True)
        provider = _Blocks()
        orch, _notes, chained = _gateway(monkeypatch, provider)
        trigger = TriggerStore(base_dir=home).get(TID).trigger

        asyncio.run(_cut_off(orch._fire_store_trigger(trigger, {"trigger_id": TID}), provider))

        assert chained == []


class TestARunNow:
    def _dispatch(self, monkeypatch, provider: Any) -> Any:
        from personalclaw.dashboard.handlers import trigger_runs

        monkeypatch.setattr(
            "personalclaw.action_providers.get_action_provider", lambda name: provider
        )
        return trigger_runs

    def test_holds_the_claim_while_it_runs(self, home, monkeypatch):
        """🔴 Red before: a Run now held no claim, so it read as idle while it ran — a second
        click ran the action again beside it, and a tick fired whatever its `overlap` said."""
        provider = _Blocks()
        trigger_runs = self._dispatch(monkeypatch, provider)
        trigger = TriggerStore(base_dir=home).get(TID).trigger
        seen: dict[str, bool] = {}

        async def _drive() -> None:
            task = asyncio.ensure_future(
                trigger_runs._dispatch_store_action(trigger, {"trigger_id": TID, "manual": True})
            )
            await asyncio.wait_for(provider.started.wait(), timeout=10)
            seen["while"] = claims.is_running(TID, base_dir=home)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            seen["after"] = claims.is_running(TID, base_dir=home)

        asyncio.run(_drive())

        assert seen == {"while": True, "after": False}

    def test_a_second_run_now_is_refused_while_the_first_runs(self, home, monkeypatch):
        """The 409 was asked for a clock trigger only, and of a claim a Run now never took."""
        from unittest.mock import MagicMock

        from personalclaw.dashboard.handlers import trigger_runs

        claims.write_claim(
            Claim(trigger_id=TID, holder="manual.run:1", claimed_at=time.time()), base_dir=home
        )
        request = MagicMock()
        request.match_info = {"id": f"store:{TID}"}
        request.query = {}
        request.can_read_body = False
        request.body_exists = False

        async def _body(_request: Any) -> dict:
            return {}

        monkeypatch.setattr(trigger_runs, "json_object_body", _body)
        resp = asyncio.run(trigger_runs.api_trigger_run(request))

        assert resp.status == 409

    def test_a_stop_cuts_off_is_recorded_as_the_hand_run_it_was(self, home, monkeypatch):
        """🔴 Red before: nothing recorded it. Its row is `manual` — the failure streak and the
        hourly cap pass over it — and the trigger's health is left alone, as a hand run's is."""
        _restarting(monkeypatch, True)
        provider = _Blocks()
        trigger_runs = self._dispatch(monkeypatch, provider)
        trigger = TriggerStore(base_dir=home).get(TID).trigger

        asyncio.run(
            _cut_off(
                trigger_runs._dispatch_store_action(trigger, {"trigger_id": TID, "manual": True}),
                provider,
            )
        )

        (row,) = _rows(home)
        assert row["status"] == reaper.RESTART_INTERRUPTED_STATUS
        assert row["trigger"] == "manual"
        assert row["error"].startswith("Interrupted by a gateway restart: ")
        assert [c.kind for c in review.pending(base_dir=home)] == [review.INTERRUPTED]
        after = TriggerStore(base_dir=home).get(TID).trigger
        assert after.health_status != TriggerHealth.DEGRADED.value
        assert not claims.is_running(TID, base_dir=home)

    def test_leaves_a_claim_a_tick_holds_to_the_tick(self, home, monkeypatch):
        """A view refresh or a webhook fire beside a tick's run must not free the tick's claim."""

        class _Quick:
            async def execute(self, config: Any, ctx: Any, timeout: float = 30) -> Any:
                from personalclaw.action_providers.base import ActionResult

                return ActionResult(success=True, stdout="done")

        trigger_runs = self._dispatch(monkeypatch, _Quick())
        claims.write_claim(
            Claim(trigger_id=TID, holder="tick:1", claimed_at=time.time()), base_dir=home
        )
        trigger = TriggerStore(base_dir=home).get(TID).trigger

        ran, _note = asyncio.run(
            trigger_runs._dispatch_store_action(trigger, {"trigger_id": TID}, event="view.rendered")
        )

        assert ran
        held = claims.read_claim(TID, base_dir=home)
        assert held is not None and held.holder == "tick:1"

    def test_leaves_a_claim_a_tick_took_over_while_it_ran(self, home, monkeypatch):
        """A tick whose `overlap` lets it fire beside the hand run writes its own claim over the
        hand run's, and the hand run ending must not free a run still in flight."""
        from personalclaw.action_providers.base import ActionResult

        class _TickFiresBeside:
            async def execute(self, config: Any, ctx: Any, timeout: float = 30) -> Any:
                assert claims.is_running(TID, base_dir=home)
                claims.write_claim(
                    Claim(trigger_id=TID, holder="tick:2", claimed_at=time.time()), base_dir=home
                )
                return ActionResult(success=True, stdout="done")

        trigger_runs = self._dispatch(monkeypatch, _TickFiresBeside())
        trigger = TriggerStore(base_dir=home).get(TID).trigger

        ran, _note = asyncio.run(
            trigger_runs._dispatch_store_action(trigger, {"trigger_id": TID, "manual": True})
        )

        assert ran
        held = claims.read_claim(TID, base_dir=home)
        assert held is not None and held.holder == "tick:2"


class TestTheBootPassAfterARestart:
    """A restart keeps the pid, so a claim the replaced image left names a live process."""

    def _claim(self, home, *, image: str) -> None:
        claims.write_claim(
            Claim(
                trigger_id=TID,
                holder="manual.run:1",
                claimed_at=time.time() - 5,
                owner_pid=os.getpid(),
                owner_image=image,
            ),
            base_dir=home,
        )

    def test_closes_a_claim_an_earlier_image_of_this_process_left(self, home):
        """🔴 Red before: `pid_is_alive` answered True for this very process, so the run read as in
        flight until the 1800s deadline recorded it as reaped."""
        self._claim(home, image="an-image-a-restart-replaced")

        assert claims.orphaned_ids(base_dir=home) == [(TID, os.getpid())]
        (record,) = reaper.terminalize_orphans_sync(
            store=TriggerStore(base_dir=home), base_dir=home
        )
        assert record["reason"].startswith(
            "Interrupted by a gateway restart: the gateway restarted while this was running."
        )
        assert not claims.is_running(TID, base_dir=home)

    def test_leaves_this_images_own_claim(self, home):
        self._claim(home, image=PROCESS_IMAGE)
        assert claims.orphaned_ids(base_dir=home) == []

    def test_leaves_a_claim_that_names_no_image_to_the_deadline(self, home):
        """Unknown is not gone: a claim written before images were stamped is left alone."""
        self._claim(home, image="")
        assert claims.orphaned_ids(base_dir=home) == []

    def test_a_claim_reads_back_the_image_that_granted_it(self, home):
        """Read back with the stored image, never the reader's: defaulted, every claim an earlier
        image left would read as this one's."""
        self._claim(home, image="an-image-a-restart-replaced")
        held = claims.read_claim(TID, base_dir=home)
        assert held is not None and held.owner_image == "an-image-a-restart-replaced"
        assert Claim(trigger_id="t", holder="h", claimed_at=1.0).owner_image == PROCESS_IMAGE
