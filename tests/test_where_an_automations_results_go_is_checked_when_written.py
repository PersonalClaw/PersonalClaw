"""Where an automation's results go is checked when it is written, and the tool says what applies.

Before, `automation_update` stored any `delivery` it was sent. Asked to send a watch's results to
Telegram, the chat patched `delivery: "telegram"`, which is not a route, and was told "Updated …:
delivery." The store read the row back as a notification with a warning on it, so the results went
to the bell while the chat told the owner they would arrive on Telegram. The same reply said the
watch still waited for the owner's Allow, which they had already given: the update said nothing
about the automation's state, so the model repeated what the create had said an hour earlier.
And the watch itself had been made with `delivery: none` for a request that said "tell me", so its
result would have reached nobody at all.

Now a route is checked by the rule the store reads it with, a chat channel named by its name is
that channel's route, anything else is refused with the routes there are, and the result of a
create or an update says where the results go and whether it runs, read from the saved row.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from personalclaw import channel_transports
from personalclaw.channel_transports.base import ChannelTransportProvider
from personalclaw.tool_providers.base import ToolFailure
from personalclaw.triggers import grants
from personalclaw.triggers import tools as T
from personalclaw.triggers.store import TriggerStore

EVERY_MONDAY = {"kind": "cron", "expr": "0 18 * * 1"}
RELEASES = "https://releases.example.com/httpx"


class _Transport(ChannelTransportProvider):
    """Just enough of a chat channel to give it a name and the name it is shown under."""

    def __init__(self, name: str, display: str) -> None:
        self._name, self._display = name, display

    name = property(lambda self: self._name)
    display_name = property(lambda self: self._display)

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, message: Any) -> bool:
        return True


TELEGRAM = {"telegram": _Transport("telegram", "Telegram")}


@pytest.fixture
def store(tmp_path):
    return TriggerStore(base_dir=tmp_path)


def _watch(store, *, allowed: bool = False) -> str:
    """The watch the chat makes for "watch this page and tell me when 0.29 ships"."""
    made = T.create(
        store,
        name="httpx 0.29 release watch",
        when=f"when {RELEASES} changes",
        message="If httpx 0.29 is listed, tell the owner it has shipped.",
        created_by="agent",
    )
    assert made.ok, made.text
    trigger_id = made.data["trigger"]["id"]
    if allowed:
        # The owner's Allow on the Triggers page: the grant for what its action runs.
        row = store.get(trigger_id)
        grants.give(row.trigger)
        store.upsert(row.trigger)
    return trigger_id


def _stored(store, trigger_id: str) -> dict[str, Any]:
    row = store.get(trigger_id)
    assert row is not None
    return row.trigger.to_dict()


# ── a route that is not one is refused, and nothing changes ─────────────────────────────────────


@pytest.mark.parametrize("field", ["delivery", "failure_delivery"])
def test_a_route_that_is_not_one_is_refused_and_nothing_changes(store, field):
    """🔴 Before: stored as sent, and answered "Updated …"."""
    trigger_id = _watch(store)
    before = _stored(store, trigger_id)

    out = T.update(store, trigger_id=trigger_id, patch={field: "pager"}, chat_channels=TELEGRAM)

    assert not out.ok, out.text
    assert out.text.startswith("Error: nothing was changed:"), out.text
    # The routes there are, so the owner can be asked which.
    for route in ("'inbox'", "'none'", "'channel:<name>'"):
        assert route in out.text, out.text
    assert "The chat channels set up here: Telegram." in out.text
    assert _stored(store, trigger_id) == before


def test_a_route_that_is_not_text_is_refused(store):
    trigger_id = _watch(store)
    before = _stored(store, trigger_id)

    out = T.update(store, trigger_id=trigger_id, patch={"delivery": None}, chat_channels=TELEGRAM)

    assert not out.ok, out.text
    assert _stored(store, trigger_id) == before


@pytest.mark.parametrize("route", ["signal", "channel:signal"])
def test_a_channel_that_is_not_set_up_here_is_refused_with_the_ones_that_are(store, route):
    trigger_id = _watch(store)
    before = _stored(store, trigger_id)

    out = T.update(store, trigger_id=trigger_id, patch={"delivery": route}, chat_channels=TELEGRAM)

    assert not out.ok, out.text
    assert "signal isn't one of the chat channels set up here." in out.text
    assert "The chat channels set up here: Telegram." in out.text
    assert _stored(store, trigger_id) == before


class _Ids(_Transport):
    """A channel whose chats have ids it checks, as a real one does."""

    def validate_target(self, target: str) -> str:
        return "" if target.startswith("C") else "A chat id here starts with C, like C0123456789."


def test_a_chat_on_a_channel_is_checked_by_that_channel(store):
    trigger_id = _watch(store)
    before = _stored(store, trigger_id)
    chatty = {"chatty": _Ids("chatty", "Chatty")}

    refused = T.update(
        store,
        trigger_id=trigger_id,
        patch={"delivery": "channel:chatty:#agent"},
        chat_channels=chatty,
    )
    taken = T.update(
        store,
        trigger_id=trigger_id,
        patch={"delivery": "channel:Chatty:C0123"},
        chat_channels=chatty,
    )

    assert not refused.ok, refused.text
    assert "A chat id here starts with C" in refused.text
    assert taken.ok, taken.text
    assert _stored(store, trigger_id)["delivery"] == "channel:chatty:C0123" != before["delivery"]
    assert "Where its results go: on Chatty to C0123, and on no other channel." in taken.text


# ── a chat channel named by its name is that channel's route ────────────────────────────────────


@pytest.mark.parametrize("named", ["telegram", "Telegram", "channel:telegram", "channel:Telegram"])
def test_a_chat_channel_named_by_its_name_is_its_route(store, named):
    """🔴 Before: `"telegram"` was stored as written, and read back as a notification."""
    trigger_id = _watch(store, allowed=True)

    out = T.update(store, trigger_id=trigger_id, patch={"delivery": named}, chat_channels=TELEGRAM)

    assert out.ok, out.text
    assert _stored(store, trigger_id)["delivery"] == "channel:telegram"
    assert "Where its results go: on Telegram, and on no other channel." in out.text
    row = store.get(trigger_id)
    assert row is not None and not row.warnings, [i.message for i in row.warnings]


@pytest.mark.parametrize(("named", "route"), [("Inbox", "inbox"), (" none ", "none")])
def test_a_route_in_another_case_is_that_route(store, named, route):
    trigger_id = _watch(store)

    out = T.update(store, trigger_id=trigger_id, patch={"delivery": named}, chat_channels=TELEGRAM)

    assert out.ok, out.text
    assert _stored(store, trigger_id)["delivery"] == route
    # Stored as the route, not as written and read back with a warning.
    row = store.get(trigger_id)
    assert row is not None and not row.warnings, [i.message for i in row.warnings]


# ── the result says what applies, read from the saved row ───────────────────────────────────────


def test_an_update_says_it_runs_now_once_the_owner_allowed_it(store):
    """🔴 Before: "Updated …: delivery." and nothing else, so the chat repeated the create's
    "it does not run until you allow it" about a watch the owner had allowed since."""
    trigger_id = _watch(store, allowed=True)

    out = T.update(
        store,
        trigger_id=trigger_id,
        patch={"delivery": "channel:telegram"},
        chat_channels=TELEGRAM,
    )

    assert out.ok, out.text
    assert "It is active now and visible on the Triggers page." in out.text
    assert "allow it" not in out.text
    assert out.data["needs_grant"] == []


def test_an_update_says_it_still_waits_for_the_owner(store):
    trigger_id = _watch(store)

    out = T.update(store, trigger_id=trigger_id, patch={"name": "httpx watch"})

    assert out.ok, out.text
    assert "it does not run until you allow it there" in out.text
    assert out.data["needs_grant"] == ["Run Prompt"]


def test_an_update_says_where_failures_go(store):
    trigger_id = _watch(store, allowed=True)

    out = T.update(
        store,
        trigger_id=trigger_id,
        patch={"failure_delivery": "telegram"},
        chat_channels=TELEGRAM,
    )

    assert out.ok, out.text
    assert _stored(store, trigger_id)["failure_delivery"] == "channel:telegram"
    assert "If it fails: on Telegram, and on no other channel." in out.text


def test_a_switched_off_update_says_so_once(store):
    """An update that switched it off says why, and does not then also call it active."""
    trigger_id = _watch(store, allowed=True)

    out = T.update(
        store,
        trigger_id=trigger_id,
        patch={"workflow": {"provider": "run-prompt", "config": {"message": "Something else."}}},
    )

    assert out.ok, out.text
    assert "saved switched off" in out.text
    assert "active now" not in out.text


# ── a task the owner asked to be told about tells them ──────────────────────────────────────────


def test_a_task_made_without_a_channel_sends_its_result_to_the_owner(store):
    """🔴 Before: `delivery: none`, so a "tell me when …" watch's result reached nobody."""
    trigger_id = _watch(store)

    assert _stored(store, trigger_id)["delivery"] == "inbox"


def test_the_create_says_where_a_tasks_result_goes(store):
    made = T.create(
        store,
        name="Weekly note",
        kind="clock",
        spec=EVERY_MONDAY,
        message="Summarise the week's notes.",
        created_by="agent",
    )

    assert made.ok, made.text
    assert "sends you what it produced as a notification in PersonalClaw" in made.text


def test_words_to_send_are_their_own_delivery(store):
    """A `say` automation's words are what it sends; a report about sending them would be a
    second message for one fire, so its route stays silent."""
    made = T.create(
        store,
        name="Plants",
        kind="clock",
        spec=EVERY_MONDAY,
        say="Water the plants.",
        created_by="agent",
    )

    assert made.ok, made.text
    assert _stored(store, made.data["trigger"]["id"])["delivery"] == "none"


# ── the chat's tool, as the agent calls it ──────────────────────────────────────────────────────


@pytest.fixture
def home(tmp_path, monkeypatch):
    from personalclaw.config import loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(channel_transports, "_transports", {})
    monkeypatch.setattr(channel_transports, "_apps", {})
    channel_transports.register_transport(_Transport("telegram", "Telegram"))
    return tmp_path


def _update(trigger_id: str, patch: dict[str, Any]) -> str:
    from personalclaw import mcp_automation

    return mcp_automation._call_tool(
        "automation_update", {"id": trigger_id, "patch": json.dumps(patch)}
    )


def test_the_chat_tool_takes_a_channel_by_its_name(home):
    trigger_id = _watch(TriggerStore(base_dir=home), allowed=True)

    out = _update(trigger_id, {"delivery": "telegram"})

    assert not isinstance(out, ToolFailure), out
    assert TriggerStore(base_dir=home).get(trigger_id).trigger.delivery == "channel:telegram"
    assert "Where its results go: on Telegram, and on no other channel." in out


def test_the_chat_tool_refuses_a_route_that_is_not_one(home):
    trigger_id = _watch(TriggerStore(base_dir=home))

    out = _update(trigger_id, {"delivery": "pager"})

    assert isinstance(out, ToolFailure), out
    assert TriggerStore(base_dir=home).get(trigger_id).trigger.delivery == "inbox"
