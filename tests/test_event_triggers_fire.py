"""A data-event trigger fires once, through the one store dispatch, and leaves its run behind.

Measured on `main` (530d8fe3b) before any of this was written, driving a real gateway:

* The Triggers page's "Data event" form posts `trigger_type: "event"`, which wrote a row into a
  SECOND store (`event_triggers.json`) with its own engine and its own dispatch seam. That engine
  ran the action, but recorded nothing: the trigger's history answered `supported: false` ("event
  triggers record a fire count, not per-run records"), so a fire left no run anywhere.
* The chat's `automation_create` wrote `kind: "event"` rows into the ONE store, `triggers.json`,
  where the Triggers page listed and counted them ("On an event") — and nothing ever matched them.
  A memory write matching one of them left `run_count` at 0 through a full tick.

So an event trigger lives in `triggers.json` now, is matched by the bus against that store, is
admitted through the same gate walk a clock fire takes, and runs through
`gateway._fire_store_trigger` — the dispatch every other store trigger already uses.
"""

from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import personalclaw.config.loader as loader
from personalclaw.dashboard.handlers import triggers as T
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.triggers import tools as Tools
from personalclaw.triggers.store import TriggerStore

# ── harness ──


class _State:
    """A dashboard state that records what reaches the user.

    `notify` keeps `DashboardState.notify`'s real signature — `(kind, title, body, *, meta=None)`:
    the notify ACTION passes the first three positionally and the substrate's own reports pass
    keywords, and a fake with the wrong shape records nothing and reproduces the bug it is meant to
    catch.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.refreshed: list[tuple[str, ...]] = []

    def notify(self, kind, title, body="", *, meta=None, **_extra):
        self.sent.append({"kind": kind, "title": title, "body": body, "meta": meta or {}})
        return True

    def push_refresh(self, *kinds):
        self.refreshed.append(kinds)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """One temp home for every store this path touches: triggers, runs, spool, the legacy file."""
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.triggers.boot_migrate.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.gateway.config_dir", lambda: tmp_path, raising=False)
    return tmp_path


@pytest.fixture
def state(monkeypatch):
    from personalclaw.action_providers import services

    st = _State()
    monkeypatch.setattr(
        services,
        "_services",
        services.ActionServices(
            state=st, spawn_background=lambda coro: None  # type: ignore[arg-type]
        ),
    )
    return st


def _orch(state) -> GatewayOrchestrator:
    orch = object.__new__(GatewayOrchestrator)
    orch.dashboard_state = state  # type: ignore[assignment]
    return orch


def _req(method, path, *, body=None, match_info=None):
    app = web.Application()
    app["state"] = types.SimpleNamespace(push_refresh=lambda *k: None)
    req = make_mocked_request(method, path, match_info=match_info or {}, app=app)
    req["user"] = "tester"
    if body is not None:

        async def _json():
            return body

        req.json = _json  # type: ignore[assignment]
    return req


def _body(resp) -> dict:
    return json.loads(resp.body.decode())


_NOTIFY = {"provider": "notify", "config": {"title_template": "Acme changed: $key"}}


async def _create_through_the_api(**fields) -> dict:
    """POST /api/triggers exactly as the Triggers page's Data-event form sends it."""
    body = {
        "trigger_type": "event",
        "name": "Acme watch",
        "pattern": "MemoryKeyPattern",
        "key_glob": "project.acme.*",
        "action": _NOTIFY,
        **fields,
    }
    resp = await T.api_trigger_create(_req("POST", "/api/triggers", body=body))
    assert resp.status in (200, 201), _body(resp)
    return _body(resp)


def _memory(home: Path):
    """The real memory store — a write through it reaches the bus exactly as a user's does."""
    from personalclaw.vector_memory import VectorMemoryStore

    mem = VectorMemoryStore(db_path=home / "memory.db", embedding_dim=3)
    mem.init()
    return mem


def _write_memory(mem, key: str, value: str) -> None:
    assert mem.set_semantic(key, value, 1.0, "user_explicit") is None


def _store_row(home: Path, trigger_id: str):
    row = TriggerStore(base_dir=home).get(trigger_id)
    assert row is not None, f"{trigger_id} is not in triggers.json"
    return row.trigger


async def _history(trigger_id: str) -> dict:
    resp = await T.api_trigger_history(
        _req(
            "GET",
            f"/api/triggers/store:{trigger_id}/history",
            match_info={"id": f"store:{trigger_id}"},
        )
    )
    return _body(resp)


# ── 🔴 the defect: a data-event trigger created on the Triggers page ──


def test_the_data_event_form_creates_a_row_in_the_ONE_trigger_store(home):
    """🔴 Red on main: the form's POST wrote `event_triggers.json`, a second store with its own
    engine, so the row was invisible to everything that reads `triggers.json` (the tick, the run
    ledger, the doctor, the chat's `automation_*` tools)."""
    data = asyncio.run(_create_through_the_api())
    trigger = data["trigger"]
    assert trigger["kind"] == "store" and trigger["store_kind"] == "event"
    stored = _store_row(home, trigger["raw_id"])
    assert stored.kind == "event"
    assert stored.spec == {
        "source": "memory",
        "pattern": "MemoryKeyPattern",
        "key_glob": "project.acme.*",
    }
    assert stored.workflow == {"inline": _NOTIFY}
    assert not (home / "event_triggers.json").exists()


def test_a_data_event_trigger_fires_ONCE_and_its_run_and_notification_appear(home, state):
    """🔴 THE DEFECT, end to end: create on the form, cause the event, see exactly one fire."""

    async def scenario():
        orch = _orch(state)
        orch._start_event_triggers()
        try:
            trigger_id = (await _create_through_the_api())["trigger"]["raw_id"]
            mem = _memory(home)
            _write_memory(mem, "project.acme.deadline", "The launch moved to Friday")
            await orch._event_router.settle()
            # A write the pattern does not match fires nothing.
            _write_memory(mem, "project.other.deadline", "unrelated")
            await orch._event_router.settle()
            return trigger_id, await _history(trigger_id)
        finally:
            orch._stop_event_triggers()

    trigger_id, history = asyncio.run(scenario())

    # The notification the action raised: one, naming the key, linking back to the trigger.
    notes = [n for n in state.sent if n["title"].startswith("Acme changed")]
    assert [n["title"] for n in notes] == ["Acme changed: project.acme.deadline"]
    assert notes[0]["meta"]["statusUrl"] == f"#/triggers?open={trigger_id}"

    # The run the fire left: one row in the trigger's own history, a success.
    assert history.get("supported") is not False, history
    assert history["total"] == 1
    assert history["runs"][0]["status"] == "success"

    # And the counter the list and the fire budget read.
    stored = _store_row(home, trigger_id)
    assert stored.run_count == 1
    assert stored.last_fired_at


def test_a_chat_created_event_automation_fires_through_the_same_path(home, state):
    """🔴 Red on main: `automation_create kind=event` made a row nothing matched."""
    store = TriggerStore(base_dir=home)
    made = Tools.create(
        store,
        name="Acme from chat",
        kind="event",
        spec={"pattern": "MemoryKeyPattern", "key_glob": "project.acme.*"},
        workflow={"inline": _NOTIFY},
        created_by="agent",
    )
    assert made.ok, made.text
    trigger_id = made.data["trigger"]["id"]
    assert made.data["trigger"]["spec"]["source"] == "memory"

    async def scenario():
        orch = _orch(state)
        orch._start_event_triggers()
        try:
            _write_memory(_memory(home), "project.acme.owner", "Dana")
            await orch._event_router.settle()
        finally:
            orch._stop_event_triggers()

    asyncio.run(scenario())
    assert [n["title"] for n in state.sent] == ["Acme changed: project.acme.owner"]
    assert _store_row(home, trigger_id).run_count == 1


def test_an_inbox_event_never_fires_a_memory_trigger(home, state):
    """The source gate survives the move: a trigger listens to exactly one source."""
    from personalclaw.event_triggers import SOURCE_INBOX, emit_event

    async def scenario():
        orch = _orch(state)
        orch._start_event_triggers()
        try:
            await _create_through_the_api(pattern="MemoryUpdate", key_glob="")
            emit_event(
                source=SOURCE_INBOX,
                event_type="message_received",
                key="C1_1",
                value="project.acme.deadline",
                now=1.0,
                meta={"sender": "alice", "address": "C1"},
            )
            await orch._event_router.settle()
        finally:
            orch._stop_event_triggers()

    asyncio.run(scenario())
    assert state.sent == []


def test_a_paused_event_trigger_does_not_fire(home, state):
    async def scenario():
        orch = _orch(state)
        orch._start_event_triggers()
        try:
            trigger_id = (await _create_through_the_api())["trigger"]["raw_id"]
            assert Tools.set_paused(TriggerStore(base_dir=home), trigger_id=trigger_id, paused=True)
            _write_memory(_memory(home), "project.acme.deadline", "Friday")
            await orch._event_router.settle()
        finally:
            orch._stop_event_triggers()

    asyncio.run(scenario())
    assert state.sent == []


def test_a_burst_inside_the_debounce_fires_once_and_records_the_rest(home, state):
    """`debounce_secs` is the `spacing` gate's: the second write lands a typed suppression row."""

    async def scenario():
        orch = _orch(state)
        orch._start_event_triggers()
        try:
            trigger_id = (await _create_through_the_api(debounce_secs=60))["trigger"]["raw_id"]
            mem = _memory(home)
            _write_memory(mem, "project.acme.a", "one")
            await orch._event_router.settle()
            _write_memory(mem, "project.acme.b", "two")
            await orch._event_router.settle()
            return trigger_id, await _history(trigger_id)
        finally:
            orch._stop_event_triggers()

    trigger_id, history = asyncio.run(scenario())
    assert [n["title"] for n in state.sent] == ["Acme changed: project.acme.a"]
    statuses = sorted(r["status"] for r in history["runs"])
    assert statuses == ["skipped_gate", "success"], history
    assert _store_row(home, trigger_id).run_count == 1


def test_max_fires_is_the_next_time_budget_and_retires_the_trigger(home, state):
    """ "Tell me the NEXT time X": one fire, then the trigger switches itself off, visibly."""

    async def scenario():
        orch = _orch(state)
        orch._start_event_triggers()
        try:
            trigger_id = (await _create_through_the_api(max_fires=1, debounce_secs=0))["trigger"][
                "raw_id"
            ]
            mem = _memory(home)
            _write_memory(mem, "project.acme.a", "one")
            await orch._event_router.settle()
            _write_memory(mem, "project.acme.b", "two")
            await orch._event_router.settle()
            return trigger_id
        finally:
            orch._stop_event_triggers()

    trigger_id = asyncio.run(scenario())
    assert len(state.sent) == 1
    stored = _store_row(home, trigger_id)
    assert stored.run_count == 1
    assert stored.enabled is False


def test_resuming_a_spent_trigger_gives_it_its_budget_back(home, state):
    """Resume on a trigger that retired itself must make it fire again. Flipping `enabled` alone
    would leave the spent count in place, and every later event would meet the budget gate."""

    async def scenario():
        orch = _orch(state)
        orch._start_event_triggers()
        try:
            trigger_id = (await _create_through_the_api(max_fires=1, debounce_secs=0))["trigger"][
                "raw_id"
            ]
            mem = _memory(home)
            _write_memory(mem, "project.acme.a", "one")
            await orch._event_router.settle()
            resumed = Tools.set_paused(
                TriggerStore(base_dir=home), trigger_id=trigger_id, paused=False
            )
            assert resumed.ok, resumed.text
            _write_memory(mem, "project.acme.b", "two")
            await orch._event_router.settle()
            return trigger_id
        finally:
            orch._stop_event_triggers()

    trigger_id = asyncio.run(scenario())
    assert [n["title"] for n in state.sent] == [
        "Acme changed: project.acme.a",
        "Acme changed: project.acme.b",
    ]
    stored = _store_row(home, trigger_id)
    assert stored.enabled is False, "the second allowance was spent too, so it retired again"


# ── another process: the CLI, the `mcp-core` server an agent's memory tools run in ──


def test_an_event_in_a_process_with_no_gateway_is_SPOOLED_and_fires_on_the_next_tick(home, state):
    """An agent's memory tools run in the `mcp-core` subprocess, where no gateway dispatch exists.
    The event is parked in the spool and the gateway's tick delivers it — once."""
    from personalclaw.triggers import loop as clock_loop

    trigger_id = asyncio.run(_create_through_the_api())["trigger"]["raw_id"]
    # No router attached: this is the other process.
    _write_memory(_memory(home), "project.acme.deadline", "Friday")
    spooled = (home / "trigger-spool.jsonl").read_text().splitlines()
    assert len(spooled) == 1

    async def gateway_tick():
        orch = _orch(state)
        orch._start_event_triggers()
        try:
            assert clock_loop._drain_spool() == 1
            await orch._event_router.settle()
        finally:
            orch._stop_event_triggers()

    asyncio.run(gateway_tick())
    assert [n["title"] for n in state.sent] == ["Acme changed: project.acme.deadline"]
    assert _store_row(home, trigger_id).run_count == 1


def test_a_process_with_no_gateway_spools_NOTHING_when_no_trigger_matches(home):
    """Every memory write passes through the bus; only a write some trigger wants is parked."""
    asyncio.run(_create_through_the_api())
    _write_memory(_memory(home), "project.other.deadline", "Friday")
    assert not (home / "trigger-spool.jsonl").exists()


# ── the legacy store, absorbed ──


def test_a_legacy_event_triggers_file_is_absorbed_into_the_store_at_boot(home):
    """🔴 Red on main: nothing imported `event_triggers.json`. A home that made data-event
    triggers before this change keeps them — same id, pattern, matcher, action, budget and count —
    and the old file is renamed so the import happens once."""
    from personalclaw.triggers.boot_migrate import migrate_and_arm

    legacy = [
        {
            "id": "acme-note",
            "pattern": "MemoryKeyPattern",
            "source": "memory",
            "action_provider": "notify",
            "action_config": {"title_template": "Acme memory changed"},
            "key_glob": "project.acme.*",
            "content_re": "",
            "sender_glob": "",
            "address_glob": "",
            "event_glob": "",
            "enabled": True,
            "state": "active",
            "park_reason": "",
            "park_retry_after": 0.0,
            "max_fires": 5,
            "fire_count": 2,
            "debounce_secs": 5.0,
            "last_fired_at": 1790418113.3,
        }
    ]
    (home / "event_triggers.json").write_text(json.dumps(legacy))

    report = migrate_and_arm(home)

    assert report["events_absorbed"] == 1
    stored = _store_row(home, "event:acme-note")
    assert stored.name == "acme-note"
    assert stored.kind == "event"
    assert stored.enabled is True
    assert stored.spec == {
        "source": "memory",
        "pattern": "MemoryKeyPattern",
        "key_glob": "project.acme.*",
    }
    assert stored.workflow == {
        "inline": {"provider": "notify", "config": {"title_template": "Acme memory changed"}}
    }
    assert stored.gates == {"max_fires": 5, "debounce_secs": 5.0}
    assert stored.run_count == 2
    assert stored.last_fired_at
    assert not (home / "event_triggers.json").exists()
    assert (home / "event_triggers.json.migrated").exists()

    # Idempotent: a second boot has nothing left to absorb and changes nothing.
    again = migrate_and_arm(home)
    assert again["events_absorbed"] == 0
    assert _store_row(home, "event:acme-note").run_count == 2


# ── the spec is validated at the door ──


@pytest.mark.parametrize(
    "spec,needle",
    [
        ({"pattern": "NoSuchPattern"}, "unknown event pattern"),
        ({"pattern": "InboxSender"}, "sender_glob"),
        ({"pattern": "MemoryKeyPattern"}, "key_glob"),
        ({"pattern": "ContentMatch"}, "content_re"),
        ({"source": "inbox", "pattern": "MemoryUpdate"}, "listens to memory"),
    ],
)
def test_an_event_spec_that_could_never_fire_is_refused(home, spec, needle):
    made = Tools.create(
        TriggerStore(base_dir=home),
        name="never",
        kind="event",
        spec=spec,
        workflow={"inline": _NOTIFY},
        created_by="user",
    )
    assert not made.ok
    assert needle in made.text


# ── the `manual` kind fires from its Run button (checked; this already held on main) ──


def test_a_manual_trigger_fires_once_from_run_now_and_records_the_run(home, state):
    store = TriggerStore(base_dir=home)
    made = Tools.create(
        store,
        name="Tidy downloads",
        kind="manual",
        workflow={"inline": {"provider": "notify", "config": {"title_template": "Tidied"}}},
        created_by="user",
    )
    assert made.ok, made.text
    trigger_id = made.data["trigger"]["id"]

    async def run_now():
        resp = await T.api_trigger_run(
            _req(
                "POST",
                f"/api/triggers/store:{trigger_id}/run",
                body={},
                match_info={"id": f"store:{trigger_id}"},
            )
        )
        return _body(resp), await _history(trigger_id)

    result, history = asyncio.run(run_now())
    assert result["ok"] is True, result
    assert [n["title"] for n in state.sent] == ["Tidied"]
    assert history["total"] == 1
    assert history["runs"][0]["trigger"] == "manual"


def test_a_run_trigger_s_list_row_says_when_it_last_ran(home, state):
    """🔴 Red on main: after Run now the Triggers list still read "never" for this row. A store row
    carried no last-run field, so the page inferred "has it run" from `run_count` — the fire meter
    a Run button deliberately does not spend — and contradicted the run its own history listed."""
    store = TriggerStore(base_dir=home)
    made = Tools.create(
        store,
        name="Tidy downloads",
        kind="manual",
        workflow={"inline": {"provider": "notify", "config": {"title_template": "Tidied"}}},
        created_by="user",
    )
    trigger_id = made.data["trigger"]["id"]

    async def listed() -> dict:
        rows = _body(await T.api_triggers(_req("GET", "/api/triggers?type=store")))["triggers"]
        return next(r for r in rows if r["raw_id"] == trigger_id)

    async def run_now_then_list():
        before = await listed()
        await T.api_trigger_run(
            _req(
                "POST",
                f"/api/triggers/store:{trigger_id}/run",
                body={},
                match_info={"id": f"store:{trigger_id}"},
            )
        )
        return before, await listed()

    before, after = asyncio.run(run_now_then_list())
    assert before["last_run_ts"] is None and before["last_run_status"] is None
    assert after["run_count"] == 0, "a Run button never spends the fire budget"
    assert after["last_run_ts"], after
    assert after["last_run_status"] == "success"
