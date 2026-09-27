"""Every chat channel's trust is managed on the Sender trust page, through the core seam.

The page could only read and revoke. A DM policy could not be changed, a sender code came only
from ``personalclaw pair``, and a group could be tracked only by the one app that called
``track()`` itself. So a Telegram or Discord group could never be read, and a channel set up
in the dashboard had no way to let anyone in. The routes here are the page's: they change the
store every channel's inbound crosses (``channel_trust.guard_inbound``).

Each refusal carries its vacuity floor: the same request, made the allowed way, goes through.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import channel_transports
from personalclaw import channel_trust as ct
from personalclaw.channel_transports.base import ChannelTransportProvider
from personalclaw.dashboard.handlers import channel_trust as h

PROVIDER = "fakechat"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point the entity-settings store + SEL at tmp_path (the real home is never touched)."""
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    channel_transports.register_transport(_Channel())
    yield tmp_path
    channel_transports.unregister_transport(PROVIDER)


class _Channel(ChannelTransportProvider):
    name = property(lambda self: PROVIDER)
    display_name = property(lambda self: "FakeChat")

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, message: Any) -> bool:
        return True


def _app() -> web.Application:
    """The trust routes, registered as `dashboard/server.py` registers them."""
    app = web.Application()
    app.router.add_get("/api/channels/trust", h.api_channel_trust)
    app.router.add_put("/api/channels/trust/{provider}/policies", h.api_channel_trust_policies)
    app.router.add_post("/api/channels/trust/{provider}/channels", h.api_channel_trust_track)
    app.router.add_delete(
        "/api/channels/trust/{provider}/channels/{channel_id}", h.api_channel_trust_untrack
    )
    app.router.add_post("/api/channels/trust/{provider}/pairing", h.api_channel_trust_pairing_start)
    app.router.add_delete(
        "/api/channels/trust/{provider}/pairing", h.api_channel_trust_pairing_cancel
    )
    return app


def _run(handler, method: str, path: str, *, match: dict | None = None, body: Any = None):
    """One request through the real routes. ``handler`` and ``match`` name what the path hits."""

    async def _go():
        async with TestClient(TestServer(_app())) as client:
            resp = await client.request(method, path, json=body)
            return resp.status, await resp.json(), dict(resp.headers)

    return asyncio.run(_go())


def _listed(provider: str = PROVIDER) -> dict:
    _, body, _ = _run(h.api_channel_trust, "GET", "/api/channels/trust")
    return {p["provider"]: p for p in body["providers"]}[provider]


def _sel_ops():
    from personalclaw.sel import sel

    return [(e.get("operation"), e.get("outcome")) for e in sel().recent(200)]


def _policies(body: dict):
    return _run(
        h.api_channel_trust_policies,
        "PUT",
        f"/api/channels/trust/{PROVIDER}/policies",
        match={"provider": PROVIDER},
        body=body,
    )


# ── the page lists every chat channel ──────────────────────────────────────────────────────


def test_a_channel_with_no_trust_state_yet_is_listed_by_its_own_name():
    """The page listed only providers the store already held, so a channel set up a minute ago
    had no section to pair anyone from."""
    entry = _listed()
    assert entry["display_name"] == "FakeChat" and entry["registered"] is True
    assert entry["policies"] == {"dm": "pairing", "group": "tracked_only"}
    assert entry["seen_channels"] == []


# ── DM and group policies ──────────────────────────────────────────────────────────────────


def test_the_dm_policy_is_changed_from_the_page_and_the_gate_obeys_it():
    assert ct.guard_inbound(None, PROVIDER, "999", is_dm=True, text="hi").allowed is False

    status, body, _ = _policies({"dm": "owner_only"})
    assert status == 200 and body["policies"]["dm"] == "owner_only"
    verdict = ct.guard_inbound(None, PROVIDER, "999", is_dm=True, text="hi")
    assert verdict.allowed is False and verdict.canned_reply == "", "owner_only stays silent"
    assert ("trust_policy_changed", "dm=owner_only") in _sel_ops()


def test_opening_dms_to_anyone_asks_first():
    status, body, _ = _policies({"dm": "open"})
    assert status == 400 and body["error"]["code"] == "confirmation_required"
    assert "your own instructions" in body["error"]["detail"]["consent"]
    assert ct.trust_policies(PROVIDER)["dm"] == "pairing", "nothing changed without consent"

    status, body, _ = _policies({"dm": "open", "confirm": True})
    assert status == 200 and body["policies"]["dm"] == "open"
    assert ct.guard_inbound(None, PROVIDER, "999", is_dm=True, text="hi").allowed is True


def test_group_messages_can_be_turned_off_and_back_on():
    ct.track(PROVIDER, "grp-1", "Standup")
    assert ct.guard_inbound(None, PROVIDER, "999", channel_id="grp-1", is_dm=False).allowed

    assert _policies({"group": "off"})[0] == 200
    verdict = ct.guard_inbound(None, PROVIDER, "999", channel_id="grp-1", is_dm=False)
    assert verdict.allowed is False and verdict.reason == "group_policy_off"

    assert _policies({"group": "tracked_only"})[0] == 200
    assert ct.guard_inbound(None, PROVIDER, "999", channel_id="grp-1", is_dm=False).allowed


@pytest.mark.parametrize(
    "body", [{"dm": "everyone"}, {"group": "all"}, {}, {"dm": None, "group": None}]
)
def test_a_policy_outside_the_vocabulary_is_refused_and_changes_nothing(body):
    status, reply, _ = _policies(body)
    assert status == 400 and reply["error"]["code"] == "invalid_request"
    assert ct.trust_policies(PROVIDER) == {"dm": "pairing", "group": "tracked_only"}


def test_a_channel_that_is_not_set_up_is_a_404():
    status, body, _ = _run(
        h.api_channel_trust_policies,
        "PUT",
        "/api/channels/trust/nope/policies",
        match={"provider": "nope"},
        body={"dm": "owner_only"},
    )
    assert status == 404 and body["error"]["code"] == "channel_trust_provider_unknown"


# ── groups ─────────────────────────────────────────────────────────────────────────────────


def test_a_group_that_messaged_the_agent_is_listed_with_its_name_to_track():
    """Refused silently, as before, and now remembered: its id is the vendor's, and nothing
    showed it to the owner."""
    verdict = ct.guard_inbound(
        None, PROVIDER, "999", channel_id="-100123", is_dm=False, channel_name="Family"
    )
    assert verdict.allowed is False and verdict.reason == "untracked_channel"
    seen = _listed()["seen_channels"]
    assert [(g["channel_id"], g["name"]) for g in seen] == [("-100123", "Family")]


def test_tracking_a_seen_group_lets_its_messages_in_and_keeps_its_name():
    ct.guard_inbound(
        None, PROVIDER, "999", channel_id="-100123", is_dm=False, channel_name="Family"
    )
    status, _, _ = _run(
        h.api_channel_trust_track,
        "POST",
        f"/api/channels/trust/{PROVIDER}/channels",
        match={"provider": PROVIDER},
        body={"channel_id": "-100123"},
    )
    assert status == 200
    entry = _listed()
    assert [(g["channel_id"], g["name"]) for g in entry["tracked_channels"]] == [
        ("-100123", "Family")
    ]
    assert entry["seen_channels"] == []
    verdict = ct.guard_inbound(None, PROVIDER, "999", channel_id="-100123", is_dm=False, text="hi")
    assert verdict.allowed is True and verdict.fenced_text, "another person's words, fenced"
    assert ("channel_tracked", "owner") in _sel_ops()


def test_stopping_tracking_refuses_the_group_again_and_a_second_stop_is_a_404():
    ct.track(PROVIDER, "grp-1", "Standup")
    path = f"/api/channels/trust/{PROVIDER}/channels/grp-1"
    match = {"provider": PROVIDER, "channel_id": "grp-1"}
    assert _run(h.api_channel_trust_untrack, "DELETE", path, match=match)[0] == 200
    assert not ct.guard_inbound(None, PROVIDER, "999", channel_id="grp-1", is_dm=False).allowed
    status, body, _ = _run(h.api_channel_trust_untrack, "DELETE", path, match=match)
    assert status == 404 and body["error"]["code"] == "channel_trust_channel_unknown"
    assert ("channel_untracked", "owner") in _sel_ops()


@pytest.mark.parametrize("channel_id", ["", "  ", "a b", "x" * 300])
def test_a_group_id_that_cannot_be_one_is_refused(channel_id):
    status, body, _ = _run(
        h.api_channel_trust_track,
        "POST",
        f"/api/channels/trust/{PROVIDER}/channels",
        match={"provider": PROVIDER},
        body={"channel_id": channel_id},
    )
    assert status == 400 and body["error"]["code"] == "invalid_request"
    assert _listed()["tracked_channels"] == []


def test_the_seen_list_is_bounded_and_a_busy_group_is_not_rewritten_on_every_message(monkeypatch):
    for n in range(ct.SEEN_CHANNELS_MAX + 5):
        ct.note_untracked_channel(PROVIDER, f"grp-{n}", f"Group {n}")
    assert len(_listed()["seen_channels"]) == ct.SEEN_CHANNELS_MAX

    writes = []
    real = ct._write_store
    monkeypatch.setattr(ct, "_write_store", lambda data: (writes.append(1), real(data)))
    for _ in range(10):
        ct.note_untracked_channel(PROVIDER, "grp-busy", "Busy")
    assert len(writes) == 1, "one write per group per window, not one per message"


# ── a sender's pairing code from the page ──────────────────────────────────────────────────


def test_the_page_mints_a_sender_code_once_and_the_sender_redeems_it():
    status, body, headers = _run(
        h.api_channel_trust_pairing_start,
        "POST",
        f"/api/channels/trust/{PROVIDER}/pairing",
        match={"provider": PROVIDER},
    )
    code = body["code"]
    assert status == 200 and len(code) == ct.PAIRING_CODE_DIGITS
    assert headers.get("Cache-Control") == "no-store"
    listed = _listed()
    assert listed["pairing_active"] is True and code not in json.dumps(listed)

    verdict = ct.guard_inbound(None, PROVIDER, "4242", sender_name="Dana", is_dm=True, text=code)
    assert verdict.reason == "paired"
    # Kept with the name they go by: on most channels the id alone is a number.
    assert [(s["sender_id"], s["name"], s["via"]) for s in _listed()["allowed_senders"]] == [
        ("4242", "Dana", "pairing")
    ]


def test_a_cancelled_sender_code_pairs_nobody():
    _, body, _ = _run(
        h.api_channel_trust_pairing_start,
        "POST",
        f"/api/channels/trust/{PROVIDER}/pairing",
        match={"provider": PROVIDER},
    )
    status, reply, _ = _run(
        h.api_channel_trust_pairing_cancel,
        "DELETE",
        f"/api/channels/trust/{PROVIDER}/pairing",
        match={"provider": PROVIDER},
    )
    assert status == 200 and reply["cancelled"] is True
    assert (
        ct.guard_inbound(None, PROVIDER, "4242", is_dm=True, text=body["code"]).reason != "paired"
    )
    assert not ct.is_allowed_sender(PROVIDER, "4242")
    assert ("pairing_code_cancelled", "owner") in _sel_ops()


def test_the_stranger_is_told_to_ask_the_owner_not_to_run_a_command():
    """The reply went to a stranger and named `personalclaw pair <provider>`, placeholder too."""
    verdict = ct.guard_inbound(None, PROVIDER, "999", is_dm=True, text="hello")
    assert verdict.canned_reply == ct.CANNED_PAIRING_REPLY
    assert "personalclaw" not in verdict.canned_reply and "<provider>" not in verdict.canned_reply


def test_the_owner_is_told_which_channel_by_its_own_name():
    notes: list[tuple[str, str]] = []

    class _State:
        def notify(self, kind, title, body, *, meta=None):
            notes.append((title, body))

    ct.guard_inbound(_State(), PROVIDER, "999", sender_name="Dana", is_dm=True, text="hello")
    assert notes and "FakeChat" in notes[0][0] and "fakechat" not in notes[0][0]
    assert "Dana" in notes[0][1]
