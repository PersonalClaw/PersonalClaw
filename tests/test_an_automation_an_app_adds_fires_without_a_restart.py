"""An automation an app adds while the gateway runs fires at its time, with no restart.

A trigger app serves its rows to the clock (`routing.routed`), and it mints them as an app does:
from its own items, with no ``next_fire_at``. Only the gateway's start armed such a row, so one an
app added afterwards (a reminder asked for in chat, the day's nudge, a row a shared file gained) sat
on the Triggers page reading active, with a countdown, and never went off until the next restart.
Nor did an app's file watch ever fire: the watch loop read this home's own automations file, so a
watched folder an app contributed was never looked at while the chat said it was watching it.

Every test drives the gateway's own clock loop (tick, dispatch, runner, recorder) or its file-watch
pass over real stores, with a fixed clock and the Dashboard Notification action ringing a bell.
Two provider shapes, because the fix must hold for every app that serves rows: one that keeps its
own items and takes back only what core writes about running them (a reminder app), and one that
keeps whole rows in a file (a shared automations file).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from test_triggers_write_back import FileProviderStore

from personalclaw.gateway import GatewayOrchestrator
from personalclaw.triggers import ownership as OWN
from personalclaw.triggers import registry as TREG
from personalclaw.triggers import routing as ROUTE
from personalclaw.triggers import service as SVC
from personalclaw.triggers.models import Trigger, parse_trigger
from personalclaw.triggers.store import LoadedTrigger, TriggerStore

OWNER = "noor"
ZONE = "America/Toronto"

#: What core writes back about running a row: the only fields an app that keeps its own items takes
#: from a write.
RUNTIME = (
    "enabled",
    "next_fire_at",
    "last_run_id",
    "run_count",
    "last_success_at",
    "last_failure_at",
    "last_fired_at",
    "park_retry_after",
    "last_alert_hash",
    "last_alert_at",
    "health_status",
    "last_error_summary",
    "state",
)


class ItemsStore:
    """A trigger provider that keeps ITEMS and mints a row from each one on every read.

    The shape a reminder app has: what is on disk is a reminder's title and time, a watched path,
    and core's write-back about running each row; the row itself (its kind, its action) is built
    in code and carries no ``next_fire_at`` until core writes one back. A write takes the runtime
    fields and nothing else, and a delete removes the item behind the row.
    """

    name = "dayplanner"
    display_name = "Day Planner"

    def __init__(self, path: Path, *, action: str = "notify") -> None:
        self._path = Path(path)
        self._action = action

    @property
    def base_dir(self) -> Path:
        return self._path.parent

    def _state(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"reminders": [], "watches": [], "runtime": {}}
        return json.loads(self._path.read_text(encoding="utf-8"))

    def _save(self, state: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(state), encoding="utf-8")

    # ── what the app's own tools do ──

    def add_reminder(self, rid: str, title: str, *, at: float = 0.0, cron: str = "") -> str:
        state = self._state()
        state["reminders"].append({"id": rid, "title": title, "at": at, "cron": cron})
        self._save(state)
        return f"dayplanner:reminder:{rid}"

    def add_watch(self, wid: str, path: str, label: str) -> str:
        state = self._state()
        state["watches"].append({"id": wid, "path": path, "label": label})
        self._save(state)
        return f"dayplanner:watch:{wid}"

    def runtime_of(self, trigger_id: str) -> dict[str, Any]:
        return dict(self._state()["runtime"].get(trigger_id) or {})

    # ── the rows ──

    def _rows(self) -> list[dict[str, Any]]:
        state = self._state()
        rows: list[dict[str, Any]] = []
        for item in state["reminders"]:
            if item["cron"]:
                spec: dict[str, Any] = {"kind": "cron", "expr": item["cron"], "timezone": ZONE}
            else:
                spec = {"kind": "at", "at": item["at"], "delete_after_run": True, "timezone": ZONE}
            rows.append(
                self._row(f"dayplanner:reminder:{item['id']}", "clock", spec, item["title"], state)
            )
        for item in state["watches"]:
            spec = {"paths": [item["path"]], "dedup": "content"}
            rows.append(
                self._row(f"dayplanner:watch:{item['id']}", "file", spec, item["label"], state)
            )
        return rows

    def _row(self, tid: str, kind: str, spec: dict, title: str, state: dict) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": tid,
            "name": title,
            "kind": kind,
            "enabled": True,
            "author": "",
            "spec": spec,
            "workflow": {
                "provider": self._action,
                "config": {"kind": "info", "title_template": title, "body_template": title},
            },
            "capabilities": {"providers": [self._action]},
            "delivery": "none",
        }
        row.update(state["runtime"].get(tid) or {})
        return row

    # ── the contract ──

    def load(self) -> list[LoadedTrigger]:
        out = []
        for raw in self._rows():
            trigger, issues = parse_trigger(raw)
            out.append(LoadedTrigger(trigger=trigger, issues=list(issues)))
        return out

    def list_triggers(self, *, kind: str = "", include_broken: bool = True) -> list[Trigger]:
        return [r.trigger for r in self.load() if not kind or r.trigger.kind == kind]

    def get(self, trigger_id: str) -> LoadedTrigger | None:
        return next((r for r in self.load() if r.trigger.id == trigger_id), None)

    def upsert(self, trigger: Trigger) -> Trigger:
        state = self._state()
        kept = dict(state["runtime"].get(trigger.id) or {})
        kept.update({key: getattr(trigger, key) for key in RUNTIME})
        state["runtime"][trigger.id] = kept
        self._save(state)
        stored = self.get(trigger.id)
        return stored.trigger if stored is not None else trigger

    def delete(self, trigger_id: str) -> bool:
        state = self._state()
        before = len(state["reminders"]) + len(state["watches"])
        for kind, one in (("reminders", "reminder"), ("watches", "watch")):
            state[kind] = [
                item for item in state[kind] if f"dayplanner:{one}:{item['id']}" != trigger_id
            ]
        state["runtime"].pop(trigger_id, None)
        self._save(state)
        return len(state["reminders"]) + len(state["watches"]) < before

    def changed_on_disk(self) -> bool:
        return False


class _Bell:
    """The dashboard, as far as a fire reaches it: the bell."""

    def __init__(self) -> None:
        self.notes: list[dict[str, Any]] = []

    def notify(self, kind, title, body="", *, meta=None, raised_by_app=""):
        self.notes.append({"kind": kind, "title": title, "body": body})

    def push_refresh(self, *keys: str) -> None:
        return None

    def broadcast_ws(self, *_a: Any, **_k: Any) -> None:
        return None

    @property
    def titles(self) -> list[str]:
        return [note["title"] for note in self.notes]


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """One home for the gateway's store, the page's store and the run history alike."""
    for seam in (
        "personalclaw.config.loader.config_dir",
        "personalclaw.dashboard.handlers.triggers.config_dir",
        "personalclaw.gateway.config_dir",
    ):
        monkeypatch.setattr(seam, lambda: tmp_path / "home", raising=False)
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


@pytest.fixture()
def bell(monkeypatch):
    from personalclaw.action_providers import notify_provider

    state = _Bell()
    monkeypatch.setattr(
        notify_provider, "get_action_services", lambda: SimpleNamespace(state=state)
    )
    return state


@pytest.fixture(autouse=True)
def _owner(monkeypatch):
    """A configured owner: without one the ownership filter lets every row through, and the test
    of a row someone else wrote would measure nothing."""
    monkeypatch.setattr(OWN, "owner_username", lambda: OWNER)


@pytest.fixture(autouse=True)
def _clean_registry():
    """The provider registry and the storm quarantine are process-global."""
    for name in list(TREG.registered_stores()):
        TREG.unregister_trigger_store(name)
    ROUTE.clear_quarantine()
    yield
    for name in list(TREG.registered_stores()):
        TREG.unregister_trigger_store(name)
    ROUTE.clear_quarantine()


@pytest.fixture()
def planner(tmp_path) -> ItemsStore:
    store = ItemsStore(tmp_path / "apps" / "dayplanner" / "items.json")
    TREG.register_trigger_store(store.name, store)
    return store


@pytest.fixture()
def shared(tmp_path) -> FileProviderStore:
    store = FileProviderStore(tmp_path / "Sync" / "family" / "automations.json", name="shared")
    TREG.register_trigger_store(store.name, store)
    return store


async def _tick_at(monkeypatch, bell: _Bell, now: float) -> None:
    """One pass of the gateway's clock loop at ``now``: its tick, dispatch, runner and recorder."""
    import personalclaw.triggers.loop as clock_loop

    tick_once = clock_loop.tick_once

    async def one_tick(store, *, runner, sessions=None, base_dir=None, on_missed=None, **_kw):
        await tick_once(
            store, runner=runner, sessions=sessions, base_dir=base_dir, now=now, on_missed=on_missed
        )

    monkeypatch.setattr(clock_loop, "run_forever", one_tick)
    orch = object.__new__(GatewayOrchestrator)
    orch.sessions = SimpleNamespace(_sessions={}, enqueue=lambda *_a, **_k: False)
    orch.dashboard_state = bell
    await orch._clock_loop()


def _started(home: Path, now: float) -> None:
    """The gateway's start: its boot pass, over whatever is there then."""
    SVC.boot(TriggerStore(base_dir=home), now=now)


def _local_ids(home: Path) -> list[str]:
    return [row.trigger.id for row in TriggerStore(base_dir=home).load()]


def _history(home: Path, tid: str) -> list[dict[str, Any]]:
    path = home / "cron-history" / f"{tid}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _soon(secs: int) -> float:
    return float(int(time.time()) + secs)


# ── a reminder app's rows ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_reminder_an_app_adds_while_the_gateway_runs_goes_off_at_its_time(
    home, bell, planner, monkeypatch
):
    """🔴 Before: the row stayed unarmed until a restart, so at its time nothing rang."""
    start = _soon(0)
    _started(home, start)
    at = _soon(600)
    tid = planner.add_reminder("bins", "Put the bins out", at=at)

    await _tick_at(monkeypatch, bell, start + 5)
    # Armed into the app's own store, at the reminder's own time; this home's file gained nothing.
    assert SVC.to_epoch(planner.runtime_of(tid).get("next_fire_at")) == at
    assert _local_ids(home) == []
    assert bell.titles == []

    await _tick_at(monkeypatch, bell, at + 1)
    assert bell.titles == ["Put the bins out"]
    (run,) = _history(home, tid)
    assert run["status"] == "success", run
    # It did its work, so it leaves the app's list, and this home's file never held it.
    assert planner.get(tid) is None
    assert _local_ids(home) == []

    await _tick_at(monkeypatch, bell, at + 60)
    assert bell.titles == ["Put the bins out"], "it goes off once"


@pytest.mark.asyncio
async def test_a_reminder_set_moments_before_its_time_still_goes_off(
    home, bell, planner, monkeypatch
):
    """The clock looks at most every thirty seconds, so a reminder set just before its time can be
    seen first just after it. It goes off then, a few seconds late, rather than never."""
    start = _soon(0)
    _started(home, start)
    at = start + 10
    tid = planner.add_reminder("kettle", "The kettle", at=at)
    await _tick_at(monkeypatch, bell, at + 20)
    assert bell.titles == ["The kettle"]
    assert [r["status"] for r in _history(home, tid)] in (["success"], ["ran_late"])


@pytest.mark.asyncio
async def test_a_reminder_first_seen_long_after_its_time_waits_for_her_on_the_review(
    home, bell, planner, monkeypatch
):
    """A reminder whose time was long past before the clock first saw it is not run late on its
    own: it goes to the missed-run review, as one the computer slept through does. 🔴 Before: it
    sat on the page reading active, and nothing ever said it was missed."""
    from personalclaw.triggers import review

    start = _soon(0)
    _started(home, start)
    tid = planner.add_reminder("call", "Call the clinic", at=start - 3600)
    await _tick_at(monkeypatch, bell, start + 5)
    assert bell.titles == ["Missed scheduled runs"]
    # Said as it happened: not "while PersonalClaw was paused or the computer was asleep".
    assert "that reached PersonalClaw only after its time" in bell.notes[0]["body"]
    (card,) = review.pending(base_dir=home)
    assert (card.trigger_id, card.cause) == (tid, review.UNSEEN)
    assert _history(home, tid) == []
    kept = planner.get(tid)
    assert kept is not None and kept.trigger.enabled is False
    await _tick_at(monkeypatch, bell, start + 120)
    assert bell.titles == ["Missed scheduled runs"], "nothing runs it later on its own"


@pytest.mark.asyncio
async def test_a_daily_nudge_an_app_adds_while_the_gateway_runs_fires_at_its_hour(
    home, bell, planner, monkeypatch
):
    """The day's nudge is a cron row the app adds when she sets its time in Settings."""
    tz = ZoneInfo(ZONE)
    start = _soon(0)
    local = datetime.fromtimestamp(start, tz=tz) + timedelta(hours=1)
    hour, minute = local.hour, local.minute
    slot = local.replace(second=0, microsecond=0).timestamp()
    if slot <= start:
        slot = (local.replace(second=0, microsecond=0) + timedelta(days=1)).timestamp()
    _started(home, start)
    tid = planner.add_reminder("brief", "Plan the day", cron=f"{minute} {hour} * * *")

    await _tick_at(monkeypatch, bell, start + 5)
    assert SVC.to_epoch(planner.runtime_of(tid).get("next_fire_at")) == slot
    await _tick_at(monkeypatch, bell, slot + 1)
    assert bell.titles == ["Plan the day"]
    # Its next fire is the same hour tomorrow, kept where the row lives.
    after = SVC.to_epoch(planner.runtime_of(tid).get("next_fire_at"))
    assert after > slot + 23 * 3600
    assert _local_ids(home) == []


# ── a shared file's rows ───────────────────────────────────────────────────────────────────


def _shared_row(tid: str, *, author: str, every: int = 3600) -> Trigger:
    return Trigger(
        id=tid,
        name=f"Shared {tid}",
        kind="clock",
        enabled=True,
        author=author,
        spec={"kind": "interval", "interval_secs": every},
        workflow={"provider": "notify", "config": {"kind": "info", "title_template": tid}},
        capabilities={"providers": ["notify"]},
    )


@pytest.mark.asyncio
async def test_her_row_a_shared_file_gains_while_the_gateway_runs_is_armed_and_fires(
    home, bell, shared, monkeypatch
):
    """A second app shape: whole rows in a file another machine may write. 🔴 Before: "runs on its
    own never", with next_fire_at empty and nothing written back."""
    start = _soon(0)
    _started(home, start)
    shared.seed(_shared_row("bins-out", author=OWNER, every=900))

    await _tick_at(monkeypatch, bell, start + 5)
    armed = SVC.to_epoch(shared.next_fire_of("bins-out"))
    assert start + 5 < armed <= start + 5 + 900
    await _tick_at(monkeypatch, bell, armed + 1)
    assert bell.titles == ["bins-out"]
    assert SVC.to_epoch(shared.next_fire_of("bins-out")) > armed
    assert _local_ids(home) == []


@pytest.mark.asyncio
async def test_a_row_someone_else_wrote_is_still_never_armed(home, bell, shared, monkeypatch):
    """Isolation holds: a row another person wrote is shown, and this machine never arms it."""
    start = _soon(0)
    _started(home, start)
    shared.seed(_shared_row("sams-plants", author="sam", every=900))
    await _tick_at(monkeypatch, bell, start + 5)
    await _tick_at(monkeypatch, bell, start + 2000)
    assert shared.next_fire_of("sams-plants") == ""
    assert shared.upserts == []
    assert bell.titles == []


# ── an app's file watch ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_app_served_file_watch_fires_when_its_file_changes(home, bell, planner, tmp_path):
    """🔴 Before: the watch loop read this home's automations file alone, so the app's watch was
    never polled and a new PDF in the folder said nothing."""
    from personalclaw.triggers import file_poll

    kitchen = tmp_path / "Documents" / "Home" / "Kitchen"
    kitchen.mkdir(parents=True)
    (kitchen / "menu.pdf").write_bytes(b"%PDF-1.4 menu")
    tid = planner.add_watch("kitchen", f"{kitchen}/*.pdf", "Kitchen PDFs")
    gateway = object.__new__(GatewayOrchestrator)
    gateway.dashboard_state = bell

    # The watch loop's pass, with the store it is handed: this home's own.
    assert file_poll.poll_all(TriggerStore(base_dir=home)) == [], "its first look only records"
    (kitchen / "recipe.pdf").write_bytes(b"%PDF-1.4 recipe")
    fired = file_poll.poll_all(TriggerStore(base_dir=home))
    assert [p["trigger_id"] for p in fired] == [tid]
    for payload in fired:
        await gateway._fire_file_trigger(payload)

    assert bell.titles == ["Kitchen PDFs"]
    assert planner.runtime_of(tid).get("run_count") == 1
    assert [r["status"] for r in _history(home, tid)] == ["success"]
    assert _local_ids(home) == []
    assert file_poll.poll_all(TriggerStore(base_dir=home)) == [], "no change, no fire"
