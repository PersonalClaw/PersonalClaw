"""Trigger "Silent" is a switch, not a one-way latch (#450).

🔴 THE DEFECT. Creating or editing a schedule trigger with **Silent OFF and no notify channel**
minted ``delivery = ""`` — a value the ``delivery`` vocabulary does not define. Three separate
readers then turned that blank into the SILENT value:

1. ``models.parse_trigger`` — ``str(data.get("delivery", "none") or "none")``, so a row written as
   ``""`` loaded as ``"none"``.
2. ``delivery.route_for(trigger, ok=True)`` — ``str(trigger.delivery or "none")``, the same
   coercion one layer out, on the fire path.
3. ``schedule_view.is_silent`` then reported ``silent: true`` for the user who chose OFF.

So the switch could be turned on and never off: ``PUT {"silent": false}`` on a ``"none"`` row
re-derived ``channel_of() == ""`` and landed back in the ``else ""`` branch. The only input that
produced a non-silent state was also setting a channel.

🪤 THE FAKE VERSION of this file asserts ``models.py`` no longer coerces and stops. That fixes ONE
of three readers and leaves the latch standing, because ``route_for`` re-applies the identical
``or "none"`` at the point that actually decides delivery. Every case here therefore drives the
real handler, reads the value back out of the STORE, and then asks the fire path's own consumers
(``route_for`` → ``is_muted``) what they would do with it.

🔴 WHY IT IS NOT COSMETIC ANY MORE. When #450 was filed ``delivery`` had no fire-path consumer, so
the wrong state was display-only. Since then ``gateway._deliver_fire_outcome`` wired
``build_delivery(destination=route_for(trigger, ok=ok))`` and ``delivery.deliver`` enforces
``if is_muted(delivery.destination): return False``. A "Silent OFF, no channel" row now genuinely
drops the success notification the user asked to receive. Failures still escape (``route_for``
returns ``failure_delivery``, default ``"inbox"``), which is what kept this short of data loss.
"""

from __future__ import annotations

import json

import pytest
from aiohttp import web  # noqa: F401 - imported for parity with the sibling handler suites

from personalclaw.triggers import delivery
from personalclaw.triggers.models import Trigger, parse_trigger
from personalclaw.triggers.schedule_view import channel_of, is_silent
from personalclaw.triggers.store import TriggerStore

CHANNEL = "channel:C0123456789"


class _State:
    """The two `DashboardState` members the schedule handlers touch."""

    def __init__(self) -> None:
        self.pushed: list[tuple[str, ...]] = []

    def push_refresh(self, *keys: str) -> None:
        self.pushed.append(keys)

    def notify(self, **_kwargs: object) -> None:  # pragma: no cover - not exercised here
        pass


def _request(body: dict) -> object:
    class _Req:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        async def json(self) -> dict:
            return self._payload

        def get(self, key: str, default: object = None) -> object:
            return default

    return _Req(body)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """An isolated home the handlers' own `config_dir()` resolves to.

    `config_dir()` CREATES the directory it resolves, so this must be patched rather than relying
    on an env var — a test that let the real resolver run would touch the developer's own home.
    """
    monkeypatch.setattr("personalclaw.dashboard.handlers.triggers.config_dir", lambda: tmp_path)
    return tmp_path


async def _create(state: _State, body: dict) -> dict:
    from personalclaw.dashboard.handlers import triggers as handlers

    resp = await handlers._create_schedule(state, body, _request(body))
    payload = json.loads(resp.body.decode())
    assert resp.status == 200, payload
    return payload["trigger"]


async def _update(state: _State, raw: str, body: dict) -> tuple[int, dict]:
    from personalclaw.dashboard.handlers import triggers as handlers

    resp = await handlers._update_schedule(state, raw, body)
    return resp.status, json.loads(resp.body.decode())


def _stored(home, raw: str) -> Trigger:
    """The row as the fire path will see it — through the store's own parse, never a hand-built
    `Trigger`. `is_silent` fed a raw `""` no production caller ever holds is how the original
    diagnosis of this bug went wrong."""
    row = TriggerStore(base_dir=home).get(raw)
    assert row is not None
    return row.trigger


def _body(**extra) -> dict:
    return {
        "name": "nightly",
        "every": 3600,
        "action": {"provider": "notify", "config": {"title": "hi", "body": "there"}},
        **extra,
    }


# ── the vocabulary: `""` is not the silent value, at any layer ──


def test_the_loader_no_longer_turns_a_blank_into_the_silent_value():
    """Reader 1. `or "none"` is what closed the loop: a falsy `""` became `'none'`, which IS the
    silent value, so a bad write became an invisible one."""
    trigger, issues = parse_trigger({"id": "t", "name": "t", "kind": "clock", "delivery": ""})
    assert trigger.delivery == "", "an explicit blank must survive the loader verbatim"
    assert not is_silent(trigger), "the user chose OFF; a blank is not consent to go quiet"
    # `""` is in `ROUTE_VALUES`, so it must not be REPORTED either. Scoped to `delivery`: this
    # skeleton row legitimately carries a `spec.kind` error (no clock cadence), which is not ours.
    assert not [i for i in issues if i.path == "delivery"]


def test_an_absent_delivery_still_takes_the_declared_default():
    """The discriminator against over-correcting. Absent means "nothing was said" and keeps the
    dataclass default; `""` means "a writer said blank". Collapsing the two is the distinction
    `failure_delivery` already had to re-learn (WF2AUT-15)."""
    trigger, _ = parse_trigger({"id": "t", "name": "t", "kind": "clock"})
    assert trigger.delivery == Trigger.delivery == "none"


def test_the_fire_path_does_not_read_a_blank_as_muted():
    """Reader 2, the one a models-only fix leaves standing. `route_for` carried the identical
    `or "none"`, so it re-minted silence at the only point that decides delivery — and it
    contradicted `is_muted`'s own stated rule that "an empty destination is NOT muted"."""
    trigger = Trigger(id="t", name="t", kind="clock")
    trigger.delivery = ""
    assert not delivery.is_muted(delivery.route_for(trigger, ok=True))


def test_an_explicit_none_is_still_honoured_everywhere():
    """The floor. Without this, the three tests above pass on a build that has simply stopped
    muting anything, which would be a worse bug than the latch."""
    trigger = Trigger(id="t", name="t", kind="clock")
    trigger.delivery = "none"
    assert is_silent(trigger)
    assert delivery.route_for(trigger, ok=True) == "none"
    assert delivery.is_muted(delivery.route_for(trigger, ok=True))
    # …and a failure still escapes the mute, which is the R12 contract `route_for` exists for.
    assert delivery.route_for(trigger, ok=False) == "inbox"
    assert not delivery.is_muted(delivery.route_for(trigger, ok=False))


def test_an_out_of_vocabulary_route_surfaces_instead_of_becoming_silence():
    """`bogus-value` parsed through with ZERO issues, so a hand-edited or agent-written row could
    hold a route `deliver` cannot honour and nothing said so. It must be reported — and it must
    NOT fall back to the silent value, or a typo would turn into missing alerts."""
    trigger, issues = parse_trigger({"id": "t", "name": "t", "kind": "clock", "delivery": "emial"})
    assert [i for i in issues if i.path == "delivery"], "an unreadable route said nothing"
    assert not is_silent(trigger), "a typo must not silence an automation"
    assert delivery.is_valid_route(trigger.delivery)


# ── the writers, through the real handlers ──


@pytest.mark.asyncio
async def test_creating_with_silent_off_and_no_channel_is_not_silent(home):
    """The issue's headline row: `silent:false`, no channel → disk `''` → API `silent: true`."""
    state = _State()
    row = await _create(state, _body(silent=False))
    assert row["silent"] is False, "the create response reported a state the user did not choose"
    trigger = _stored(home, row["raw_id"])
    assert delivery.is_valid_route(trigger.delivery), f"{trigger.delivery!r} is out of vocabulary"
    assert not is_silent(trigger)
    assert not delivery.is_muted(
        delivery.route_for(trigger, ok=True)
    ), "the success notification the user asked to receive is being dropped"


@pytest.mark.asyncio
async def test_silent_off_survives_an_unrelated_later_write(home):
    """The follow-on effect the report measured: the `''` was transient (any later write rewrote
    disk to `'none'`) while the wrong STATE became permanent. So a rename must not mute."""
    state = _State()
    row = await _create(state, _body(silent=False))
    raw = row["raw_id"]
    status, payload = await _update(state, raw, {"name": "renamed"})
    assert status == 200, payload
    trigger = _stored(home, raw)
    assert not is_silent(trigger), "an unrelated rename silenced the automation"
    assert not delivery.is_muted(delivery.route_for(trigger, ok=True))


@pytest.mark.asyncio
async def test_silent_can_be_turned_back_off(home):
    """THE LATCH. On a `'none'` row, `PUT {"silent": false}` re-derived `channel_of() == ""` and
    landed in the `else ""` branch, which read back as `'none'` — so the only way out was to also
    set a channel. This is the round trip the user cannot currently make."""
    state = _State()
    row = await _create(state, _body(silent=True))
    raw = row["raw_id"]
    assert is_silent(_stored(home, raw)), "the precondition: it really is silent first"

    status, payload = await _update(state, raw, {"silent": False})
    assert status == 200, payload
    assert payload["trigger"]["silent"] is False, "the write response still claims silent"
    trigger = _stored(home, raw)
    assert not is_silent(trigger), "Silent is a one-way latch — OFF did not take"
    assert channel_of(trigger) == "", "and it must not have invented a channel to escape"
    assert not delivery.is_muted(delivery.route_for(trigger, ok=True))


@pytest.mark.asyncio
async def test_the_switch_round_trips_both_ways(home):
    """On → off → on → off, reading the store each time. A fix that only made the first OFF work
    would pass `test_silent_can_be_turned_back_off` alone."""
    state = _State()
    raw = (await _create(state, _body()))["raw_id"]
    for want_silent in (True, False, True, False):
        status, payload = await _update(state, raw, {"silent": want_silent})
        assert status == 200, payload
        trigger = _stored(home, raw)
        assert is_silent(trigger) is want_silent, f"silent={want_silent} did not take"
        assert delivery.is_muted(delivery.route_for(trigger, ok=True)) is want_silent


@pytest.mark.asyncio
async def test_a_channel_still_wins_over_silent_off(home):
    """The one input that always produced a usable value must keep working, and Silent ON must
    still override it — otherwise this fix would have traded the latch for a lost channel."""
    state = _State()
    row = await _create(state, _body(silent=False, channel="C0123456789"))
    raw = row["raw_id"]
    trigger = _stored(home, raw)
    assert trigger.delivery == CHANNEL
    assert channel_of(trigger) == "C0123456789"
    assert not is_silent(trigger)

    status, payload = await _update(state, raw, {"silent": True})
    assert status == 200, payload
    assert is_silent(_stored(home, raw)), "Silent ON must still mute a channel-routed trigger"
