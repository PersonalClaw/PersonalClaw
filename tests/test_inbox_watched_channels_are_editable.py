"""``inbox.watched_channels`` has a write path, and the inbox reads it at every poll.

The list is handed to every source's ``poll``, and Slack's inbox source reads nothing else, but it
was not editable: not in the PATCH allowlist and in no screen, so the source polled nothing unless
config.json was edited by hand. Settings → Inbox now lists it ("Channels to read") while a polled
source says it reads it (``MessageSourceProvider.watches_channels``, on the status and providers
routes), and an edit is one channel id in or out, trimmed, with anything that is not one id refused.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.config.loader import AppConfig


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


async def _patch(body: dict) -> tuple[int, dict]:
    from personalclaw.dashboard.handlers import api_personalclaw_config_patch

    app = web.Application()
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    async with TestClient(TestServer(app)) as client:
        resp = await client.patch("/api/config/personalclaw", json=body)
        return resp.status, await resp.json()


def _stored(home) -> list:
    return json.loads((home / "config.json").read_text(encoding="utf-8"))["inbox"][
        "watched_channels"
    ]


@pytest.mark.asyncio
async def test_a_channel_is_added_and_removed_by_its_id(home):
    status, body = await _patch({"path": "inbox.watched_channels", "add": "C0AAAA1111"})
    assert status == 200, body
    status, body = await _patch({"path": "inbox.watched_channels", "add": " C0BBBB2222 "})
    assert status == 200, body
    assert _stored(home) == ["C0AAAA1111", "C0BBBB2222"], "an id is kept as its one word"
    assert AppConfig.load().inbox.watched_channels == ["C0AAAA1111", "C0BBBB2222"]

    status, body = await _patch({"path": "inbox.watched_channels", "remove": "C0AAAA1111"})
    assert status == 200, body
    assert _stored(home) == ["C0BBBB2222"]


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["general chat", "https://example.slack.test/archives/C0A", " "])
async def test_what_is_not_one_channel_id_is_refused_and_named(home, entry):
    status, body = await _patch({"path": "inbox.watched_channels", "add": entry})
    assert status == 400
    if entry.strip():
        assert body["error"] == (
            f"{entry!r} is not a channel id: add each channel by its id alone, one word "
            "(for example C0123456789)"
        )
    assert not (home / "config.json").exists() or "watched_channels" not in json.loads(
        (home / "config.json").read_text(encoding="utf-8")
    ).get("inbox", {}), "nothing was written"


@pytest.mark.asyncio
async def test_the_next_poll_reads_the_channels_just_added(home, monkeypatch):
    """Read at every poll, so an edit needs no restart."""
    from personalclaw import inbox_service as mod
    from personalclaw.inbox import InboxState, InboxStore
    from personalclaw.inbox_service import InboxService

    seen: list[list[str]] = []

    class Watcher:
        source_name = "chat"
        watches_channels = True

        async def poll(self, watched, checkpoints, user_id):
            seen.append(list(watched))
            return [], {}

    monkeypatch.setattr(mod, "_dashboard_state", lambda: None)
    svc = InboxService(
        state=InboxState(home / "s.json"),
        store=InboxStore(home / "i.json"),
        sources=lambda: [Watcher()],
    )
    await svc._poll_once()
    status, body = await _patch({"path": "inbox.watched_channels", "add": "C0AAAA1111"})
    assert status == 200, body
    await svc._poll_once()
    assert seen == [[], ["C0AAAA1111"]]


def test_the_sources_say_whether_they_read_the_channels():
    """What Settings → Inbox decides from: the providers route (and the status route's rows)."""
    from personalclaw.inbox_providers.base import MessageSourceProvider

    assert MessageSourceProvider.watches_channels is False, "a source reads them only by saying so"


@pytest.mark.asyncio
async def test_the_providers_route_names_a_source_that_reads_the_channels(monkeypatch):
    from personalclaw import inbox_providers
    from personalclaw.dashboard import handlers_inbox

    watcher = SimpleNamespace(source_name="chat", display_name="Chat", watches_channels=True)
    plain = SimpleNamespace(source_name="mail", display_name="Mail")
    monkeypatch.setattr(inbox_providers, "source_catalog", lambda: [(watcher, True), (plain, True)])

    app = web.Application()
    app.router.add_get("/api/inbox/providers", handlers_inbox.api_inbox_providers)
    async with TestClient(TestServer(app)) as client:
        rows = (await (await client.get("/api/inbox/providers")).json())["providers"]
    assert [(r["name"], r["watches_channels"]) for r in rows] == [("chat", True), ("mail", False)]
