"""WF2AUT-15 — the failure-routing controls round-trip through the schedule facade.

🔴 THE DEFECT. `Trigger.failure_delivery` and `failure_policy.dedupe_hash` are declared, persisted,
round-tripped by `to_dict`/`from_dict`, defaulted by the migration and READ BY THE FIRE PATH:
`delivery.route_for` picks the route per outcome (called from `gateway._deliver_fire_outcome`) and
`gateway._dedupe_repeat_failure` gates on the policy key. And neither field existed anywhere a user
could see or set one — `git grep -c 'failure_delivery' -- web/src` was **0**, `failure_policy` was
not even in `tools.PATCHABLE`, and `to_schedule_row` published neither. The capability was paid for
and unreachable: the config round-trip contract's last clause (a user-facing field needs a frontend
control and a write path) was never closed.

🪤 THE FAKE VERSION of this file PATCHes a field and asserts a 200. Every one of the four issues the
wire-field census was written for returned 200 while discarding the setting. So every case here
reads the value BACK — through `to_schedule_row`, the same projection the UI renders — and the
negative cases assert on what did NOT change.
"""

from __future__ import annotations

import json

import pytest
from aiohttp import web

from personalclaw.triggers import delivery, tools
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.schedule_view import to_schedule_row
from personalclaw.triggers.store import TriggerStore

# ── the vocabulary, in one place ──


def test_the_route_vocabulary_is_what_route_for_can_honour():
    """`is_valid_route` exists because `failure_delivery` reached the entity with NO validation:
    an agent could store 'emial' through `automation_update` and get a 200, and `route_for` would
    then hand it to `deliver`, which mutes only 'none' — so the automation notified normally while
    its stored setting said something else."""
    for good in ("", "inbox", "none", "channel:C0123456789"):
        assert delivery.is_valid_route(good), good
    for bad in ("slack", "emial", "None", "INBOX", "channel:", 3):
        assert not delivery.is_valid_route(bad), bad
    # Whitespace and `None` normalise to the INHERIT route rather than being refused, matching how
    # `channel`/`timezone` are read on the same handlers (`.strip()`), and both handlers store the
    # stripped value — so what is accepted is exactly what round-trips.
    for blank in ("  ", "\t", None):
        assert delivery.is_valid_route(blank), repr(blank)


def test_failure_policy_is_patchable_and_the_health_fields_are_not():
    """The allowlist gained `failure_policy` and nothing else. `dedupe_hash` was previously settable
    only by the one-time migration, which is not a control."""
    assert "failure_policy" in tools.PATCHABLE
    assert "failure_delivery" in tools.PATCHABLE
    for protected in ("health_status", "run_count", "last_run_id", "last_alert_hash"):
        assert protected not in tools.PATCHABLE


# ── the projection the UI reads ──


def test_the_wire_row_publishes_both_fields():
    trigger = Trigger(id="t", name="t", kind="clock")
    trigger.failure_delivery = "none"
    trigger.failure_policy = {"dedupe_hash": True, "autopause_after": 9}
    row = to_schedule_row(trigger)
    assert row["failure_delivery"] == "none"
    assert row["failure_dedupe"] is True


def test_an_inherited_route_publishes_the_empty_string_not_null():
    """🪤 `"" or None` would collapse the third choice ("follow the result route") into "unset", and
    the form would then render 'Inbox' for a trigger that inherits — then WRITE that on save."""
    trigger = Trigger(id="t", name="t", kind="clock")
    trigger.failure_delivery = ""
    row = to_schedule_row(trigger)
    assert row["failure_delivery"] == ""
    assert row["failure_delivery"] is not None


def test_a_malformed_policy_does_not_break_the_projection():
    """The store's load is lenient (S87), so a hand-edited row can hold a string here and still
    list. A projection that assumed a dict would 500 the whole schedule list over one bad row."""
    trigger = Trigger(id="t", name="t", kind="clock")
    trigger.failure_policy = "dedupe_hash"  # type: ignore[assignment]
    assert to_schedule_row(trigger)["failure_dedupe"] is False


# ── the PATCH path, driven through the real handler ──


class _State:
    """The two `DashboardState` members the schedule handlers touch."""

    def __init__(self) -> None:
        self.pushed: list[tuple[str, ...]] = []

    def push_refresh(self, *keys: str) -> None:
        self.pushed.append(keys)

    def notify(self, **_kwargs: object) -> None:  # pragma: no cover - not exercised here
        pass


def _request(body: dict) -> web.Request:
    class _Req:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        async def json(self) -> dict:
            return self._payload

        def get(self, key: str, default: object = None) -> object:
            return default

    return _Req(body)  # type: ignore[return-value]


async def _create(state: _State, home, body: dict) -> dict:
    from personalclaw.dashboard.handlers import triggers as handlers

    resp = await handlers._create_schedule(state, body, _request(body))
    payload = json.loads(resp.body.decode())
    assert resp.status == 200, payload
    return payload["trigger"]


async def _update(state: _State, raw: str, body: dict) -> tuple[int, dict]:
    from personalclaw.dashboard.handlers import triggers as handlers

    resp = await handlers._update_schedule(state, raw, body)
    return resp.status, json.loads(resp.body.decode())


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """An isolated home the handlers' own `config_dir()` resolves to."""
    monkeypatch.setattr("personalclaw.dashboard.handlers.triggers.config_dir", lambda: tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_create_then_read_back_the_route_the_user_chose(home):
    state = _State()
    row = await _create(
        state,
        home,
        {
            "name": "nightly",
            "every": 3600,
            "silent": True,
            "failure_delivery": "inbox",
            "failure_dedupe": True,
            "action": {"provider": "notify", "config": {"title": "hi", "body": "there"}},
        },
    )
    # The create RESPONSE already answers, in the same projection the list uses.
    assert row["failure_delivery"] == "inbox"
    assert row["failure_dedupe"] is True
    # …and so does the store, which is the claim that matters.
    trigger = TriggerStore(base_dir=home).get(row["raw_id"]).trigger
    assert trigger.failure_delivery == "inbox"
    assert trigger.failure_policy["dedupe_hash"] is True
    # The atom's headline case: results muted, failures still routed. `route_for` is the consumer.
    assert trigger.delivery == "none"
    assert delivery.route_for(trigger, ok=True) == "none"
    assert delivery.route_for(trigger, ok=False) == "inbox"


@pytest.mark.asyncio
async def test_the_defaults_a_form_that_sends_nothing_gets(home):
    state = _State()
    row = await _create(
        state,
        home,
        {
            "name": "plain",
            "every": 3600,
            "action": {"provider": "notify", "config": {"title": "hi", "body": "there"}},
        },
    )
    assert row["failure_delivery"] == Trigger.failure_delivery == "inbox"
    assert row["failure_dedupe"] is False


@pytest.mark.asyncio
async def test_patch_round_trips_every_route_and_both_dedupe_states(home):
    state = _State()
    row = await _create(
        state,
        home,
        {
            "name": "nightly",
            "every": 3600,
            "action": {"provider": "notify", "config": {"title": "hi", "body": "there"}},
        },
    )
    raw = row["raw_id"]
    for route in ("none", "", "inbox", "channel:C0123456789"):
        for dedupe in (True, False):
            status, payload = await _update(
                state, raw, {"failure_delivery": route, "failure_dedupe": dedupe}
            )
            assert status == 200, payload
            served = payload["trigger"]
            assert served["failure_delivery"] == route, f"route={route!r}"
            assert served["failure_dedupe"] is dedupe, f"dedupe={dedupe}"
            # GET returns what was PATCHed — read from the store, not from the write response.
            trigger = TriggerStore(base_dir=home).get(raw).trigger
            assert to_schedule_row(trigger)["failure_delivery"] == route
            assert to_schedule_row(trigger)["failure_dedupe"] is dedupe


@pytest.mark.asyncio
async def test_toggling_dedupe_off_is_an_edit_not_a_no_op(home):
    """Presence, not truthiness. If the handler branched on the VALUE rather than the KEY, turning
    the switch off would leave `dedupe_hash: True` in place and the user's automation would stay
    coalesced after they asked it to stop."""
    state = _State()
    row = await _create(
        state,
        home,
        {
            "name": "nightly",
            "every": 3600,
            "failure_dedupe": True,
            "action": {"provider": "notify", "config": {"title": "hi", "body": "there"}},
        },
    )
    raw = row["raw_id"]
    status, payload = await _update(state, raw, {"failure_dedupe": False})
    assert status == 200, payload
    assert TriggerStore(base_dir=home).get(raw).trigger.failure_policy["dedupe_hash"] is False


@pytest.mark.asyncio
async def test_patching_dedupe_preserves_the_autopause_threshold_beside_it(home):
    """🪤 THE CLOBBER. `failure_policy` also holds `autopause_after`, the §3.7 threshold
    `autopause.evaluate` reads. A handler that assigned `{"dedupe_hash": …}` would silently reset a
    user's tuned failure budget to the default — the quietly-losable class `_carried` exists for."""
    state = _State()
    row = await _create(
        state,
        home,
        {
            "name": "nightly",
            "every": 3600,
            "action": {"provider": "notify", "config": {"title": "hi", "body": "there"}},
        },
    )
    raw = row["raw_id"]
    store = TriggerStore(base_dir=home)
    trigger = store.get(raw).trigger
    trigger.failure_policy = {"dedupe_hash": False, "autopause_after": 9}
    store.upsert(trigger)

    status, payload = await _update(state, raw, {"failure_dedupe": True})
    assert status == 200, payload
    policy = TriggerStore(base_dir=home).get(raw).trigger.failure_policy
    assert policy["dedupe_hash"] is True
    assert policy["autopause_after"] == 9, "the failure budget was reset by a dedup edit"


@pytest.mark.asyncio
async def test_an_unhonourable_route_is_refused_on_both_paths(home):
    """A 400, not a 200-and-discard: a stored route the substrate cannot honour is the inert shape
    this whole atom is about, one layer out."""
    state = _State()
    from personalclaw.dashboard.handlers import triggers as handlers

    body = {
        "name": "bad",
        "every": 3600,
        "failure_delivery": "emial",
        "action": {"provider": "notify", "config": {"title": "hi", "body": "there"}},
    }
    resp = await handlers._create_schedule(state, body, _request(body))
    assert resp.status == 400
    assert "failure_delivery" in resp.body.decode()

    row = await _create(
        state,
        home,
        {
            "name": "good",
            "every": 3600,
            "action": {"provider": "notify", "config": {"title": "hi", "body": "there"}},
        },
    )
    status, payload = await _update(state, row["raw_id"], {"failure_delivery": "slack"})
    assert status == 400, payload
    # The stored value is untouched — a refusal must not half-apply.
    assert TriggerStore(base_dir=home).get(row["raw_id"]).trigger.failure_delivery == "inbox"


@pytest.mark.asyncio
async def test_a_non_bool_dedupe_is_refused_rather_than_coerced(home):
    """`bool("false")` is True, so coercing would turn dedup ON for a caller asking to turn it
    off — the same inversion the `enabled` field's 400 exists to prevent."""
    state = _State()
    row = await _create(
        state,
        home,
        {
            "name": "nightly",
            "every": 3600,
            "action": {"provider": "notify", "config": {"title": "hi", "body": "there"}},
        },
    )
    status, payload = await _update(state, row["raw_id"], {"failure_dedupe": "false"})
    assert status == 400, payload
    assert (
        TriggerStore(base_dir=home).get(row["raw_id"]).trigger.failure_policy.get("dedupe_hash")
        is False
    )


@pytest.mark.asyncio
async def test_editing_the_failure_route_leaves_the_rest_of_the_advanced_block_alone(home):
    """🪤 The vacuity floor, matching the web test's. "Read two more fields" is one keystroke from
    "rewrite the update handler", and timezone / skip dates / silent are what worked all along."""
    state = _State()
    row = await _create(
        state,
        home,
        {
            "name": "nightly",
            "cron": "0 9 * * *",
            "timezone": "America/Los_Angeles",
            "skip_dates": ["2027-12-25"],
            "silent": True,
            "action": {"provider": "notify", "config": {"title": "hi", "body": "there"}},
        },
    )
    raw = row["raw_id"]
    status, payload = await _update(state, raw, {"failure_delivery": "none"})
    assert status == 200, payload
    served = payload["trigger"]
    assert served["timezone"] == "America/Los_Angeles"
    assert served["skip_dates"] == ["2027-12-25"]
    assert served["silent"] is True
    assert served["cron_expr"] == "0 9 * * *"
    assert served["failure_delivery"] == "none"
