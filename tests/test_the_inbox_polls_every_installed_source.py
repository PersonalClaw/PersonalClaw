"""Every installed inbox app's source is polled, and one that cannot be read says why.

Ledger 279: the gateway polled ONE source, the built-in drop folder, and only while
``inbox.enabled`` was on (off by default) — ``_init_inbox`` asked
``get_default_provider("filesystem")``. An installed Mail Inbox registered its source
and nothing ever polled it, so the app did nothing at all; Slack's inbox source the same.

Ledger 280 (core half): the poll there was had one health for the loop, not per source, and a
source that swallowed its own failure read "ok" beside a quiet one.

Now what is polled is one list, ``inbox_providers.source_catalog``: every registered source while
its own ``polling_enabled`` says so — an installed inbox app's always, the drop folder (which the
locked-on native ``filesystem-inbox`` app registers) only while ``inbox.enabled`` is on. Each is
polled on its own; a source's raised sentence is its row in ``/api/inbox/status``; and a reply to
one of its rows goes back to it (it was a 503 "not yet wired").
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.apps import app_manager, manager
from personalclaw.dashboard import handlers_inbox as H
from personalclaw.inbox import InboxItem, InboxState, InboxStore, ItemStatus
from personalclaw.inbox_providers.base import IncomingMessage, MessageSourceProvider

APP_NAME = "fixture-mailbox-app"
SOURCE_NAME = "fixture-mailbox"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """A scratch home for everything that resolves one, and a clean source registry."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: tmp_path)
    from personalclaw.providers import registry as reg

    monkeypatch.setattr(reg, "_registry", None, raising=False)
    from personalclaw.inbox_providers import registry as app_sources

    app_sources._sources.clear()
    yield tmp_path
    app_sources._sources.clear()


def _drop_folder(tmp_path: Path, on: bool) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"inbox": {"enabled": on}}), encoding="utf-8")


class _Source(MessageSourceProvider):
    """A source whose poll returns one message, raises a sentence, or never finishes."""

    def __init__(self, name: str, *, fail: str = "", hang: bool = False, reply: object = True):
        self._name = name
        self.fail = fail
        self.hang = hang
        self.reply = reply
        self.replies: list[tuple[str, str, str | None]] = []
        self.display_name = f"{name.title()} App"

    @property
    def source_name(self) -> str:
        return self._name

    async def poll(self, watched_channels, checkpoints, user_id):
        if self.hang:
            await asyncio.sleep(3600)
        if self.fail:
            raise RuntimeError(self.fail)
        msg = IncomingMessage(
            id=f"{self._name}-1",
            channel_id=f"{self._name}@example.test",
            channel_name=self._name,
            thread_id=f"<{self._name}-1@example.test>",
            text=f"hello from {self._name}",
            sender_id="dana@example.test",
            sender_name="Dana",
            timestamp=1790000000.0,
            kind="email",
        )
        return [msg], {f"cursor:{self._name}": "7"}

    async def send_reply(self, channel_id, text, thread_ts=None):
        self.replies.append((channel_id, text, thread_ts))
        return self.reply

    async def add_reaction(self, channel_id, ts, emoji):
        return False

    async def get_channel_history(self, channel_id, oldest, limit=200):
        return []

    async def resolve_user_name(self, user_id):
        return user_id


def _register(*sources: _Source) -> None:
    from personalclaw.inbox_providers.registry import register_source

    for s in sources:
        register_source(s)


def _service(tmp_path: Path):
    from personalclaw.inbox_providers import polled_sources
    from personalclaw.inbox_service import InboxService

    return InboxService(
        state=InboxState(tmp_path / "inbox_state.json"),
        store=InboxStore(path=tmp_path / "inbox.json"),
        sources=polled_sources,
    )


# ── the gateway polls what is installed ────────────────────────────────────────────────────────

_APP_PY = textwrap.dedent("""
    from personalclaw.inbox_providers.base import IncomingMessage, MessageSourceProvider

    class Mailbox(MessageSourceProvider):
        display_name = "Fixture Mailbox"

        def __init__(self, config=None):
            self.config = config or {}

        @property
        def source_name(self):
            return "fixture-mailbox"

        async def poll(self, watched_channels, checkpoints, user_id):
            return [IncomingMessage(
                id="<a@x>", channel_id="noor@example.test", channel_name="noor@example.test",
                text="Your talk is accepted", sender_id="talks@pyto.test", sender_name="PyTO",
                timestamp=1790000000.0, kind="email",
            )], {"mailuid:noor:INBOX": "15"}

        async def send_reply(self, channel_id, text, thread_ts=None):
            return True

        async def add_reaction(self, channel_id, ts, emoji):
            return False

        async def get_channel_history(self, channel_id, oldest, limit=200):
            return []

        async def resolve_user_name(self, user_id):
            return user_id

    def create_provider(config=None):
        return Mailbox(config)
""")


def _install_mailbox_app(tmp_path: Path) -> None:
    d = tmp_path / "src" / APP_NAME
    d.mkdir(parents=True)
    (d / "app.json").write_text(
        json.dumps(
            {
                "name": APP_NAME,
                "version": "1.0.0",
                "displayName": "Fixture Mailbox",
                "description": "An inbox app.",
                "provider": {"type": "inbox", "implementation": "provider:create_provider"},
            }
        ),
        encoding="utf-8",
    )
    (d / "provider.py").write_text(_APP_PY, encoding="utf-8")
    res = app_manager.install(d, confirm=True)
    assert res.ok, res.error


@pytest.mark.asyncio
async def test_the_gateway_polls_an_installed_inbox_app_with_the_drop_folder_off(tmp_path):
    """The whole defect: install an inbox app, leave the drop folder off (the default), and the
    gateway's inbox polls the app. It polled nothing but the drop folder before."""
    from personalclaw.gateway import GatewayOrchestrator
    from tests.test_gateway import _make_orchestrator, _mock_sessions

    _install_mailbox_app(tmp_path)
    orch = _make_orchestrator()
    assert isinstance(orch, GatewayOrchestrator)
    orch.ctx_builder = MagicMock()
    orch.sessions = _mock_sessions()
    orch.dashboard_state = None
    orch._cfg.inbox.enabled = False
    await orch._init_inbox()
    try:
        await orch.inbox_svc._poll_once()
    finally:
        orch.inbox_svc.stop()
    items = list(orch.inbox_svc.inbox.items.values())
    assert [(i.source, i.message, i.item_kind, i.reply_target) for i in items] == [
        (SOURCE_NAME, "Your talk is accepted", "email", "<a@x>")
    ], "the row lost the source's id for the mail, which a reply is addressed with"
    assert orch.inbox_svc.state.last_read_ts["mailuid:noor:INBOX"] == "15"


def test_the_drop_folder_is_polled_only_while_its_setting_is_on(tmp_path):
    from personalclaw.inbox_providers import source_catalog

    _register(_Source("mailbox"))
    _drop_folder(tmp_path, False)
    catalog = {str(s.source_name): polled for s, polled in source_catalog()}
    assert catalog == {"mailbox": True, "filesystem": False}
    _drop_folder(tmp_path, True)
    catalog = {str(s.source_name): polled for s, polled in source_catalog()}
    assert catalog == {"mailbox": True, "filesystem": True}


def test_the_drop_folder_the_native_app_registers_still_waits_for_its_setting(tmp_path):
    """The native ``filesystem-inbox`` app is locked on, and registers the drop folder the way
    an installed app registers its source. Being registered is not the owner's say-so for it,
    so it must still read ``inbox.enabled``: anything on this machine can drop a file there."""
    from personalclaw.apps import app_runtime
    from personalclaw.inbox_providers import source_catalog
    from personalclaw.inbox_providers.registry import list_source_names

    app_manager.seed_builtin_apps()  # what gateway startup does, then loads the enabled ones
    manifest = next(m for m, on in app_runtime.installed() if m.name == "filesystem-inbox" and on)
    app_runtime.load(manifest)
    assert list_source_names() == ["filesystem"], "the native app did not register its source"

    _drop_folder(tmp_path, False)
    assert {str(s.source_name): p for s, p in source_catalog()} == {"filesystem": False}
    _drop_folder(tmp_path, True)
    assert {str(s.source_name): p for s, p in source_catalog()} == {"filesystem": True}


# ── one source's failure is its own ────────────────────────────────────────────────────────────


REFUSED = (
    "the IMAP server imap.test:993 refused the login for noor@example.test (AUTHENTICATIONFAILED)"
)


@pytest.mark.asyncio
async def test_a_source_that_cannot_be_read_says_why_and_the_others_still_arrive(tmp_path):
    _drop_folder(tmp_path, False)
    _register(_Source("mailbox", fail=REFUSED), _Source("slack"))
    svc = _service(tmp_path)
    svc.state.last_read_ts["cursor:mailbox"] = "3"
    await svc._poll_once()

    assert [i.source for i in svc.inbox.items.values()] == ["slack"]
    assert svc.state.last_read_ts["cursor:mailbox"] == "3", "a failed poll moved its cursor"
    health = svc.health()
    assert health["last_poll_ok"] is False
    assert health["last_error"] == f"Mailbox App: {REFUSED}"
    rows = {row["name"]: row for row in health["sources"]}
    assert rows["mailbox"]["ok"] is False and rows["mailbox"]["error"] == REFUSED
    assert rows["slack"]["ok"] is True and rows["slack"]["error"] == ""


@pytest.mark.asyncio
async def test_it_reads_ok_again_once_a_poll_reads_it(tmp_path):
    _drop_folder(tmp_path, False)
    mailbox = _Source("mailbox", fail=REFUSED)
    _register(mailbox)
    svc = _service(tmp_path)
    await svc._poll_once()
    assert svc.health()["last_poll_ok"] is False
    mailbox.fail = ""
    await svc._poll_once()
    health = svc.health()
    assert health["last_poll_ok"] is True and health["last_error"] == ""
    assert [i.source for i in svc.inbox.items.values()] == ["mailbox"]


@pytest.mark.asyncio
async def test_a_source_that_never_answers_does_not_hold_the_others(tmp_path, monkeypatch):
    import personalclaw.inbox_service as inbox_service

    monkeypatch.setattr(inbox_service, "SOURCE_POLL_TIMEOUT_SECS", 0.05)
    _drop_folder(tmp_path, False)
    _register(_Source("mailbox", hang=True), _Source("slack"))
    svc = _service(tmp_path)
    await svc._poll_once()
    assert [i.source for i in svc.inbox.items.values()] == ["slack"]
    rows = {row["name"]: row for row in svc.health()["sources"]}
    assert rows["mailbox"]["error"] == "its last poll did not finish within 0.05 seconds"


@pytest.mark.asyncio
async def test_a_disabled_apps_source_leaves_the_health(tmp_path):
    from personalclaw.inbox_providers.registry import unregister_source

    _drop_folder(tmp_path, False)
    _register(_Source("mailbox", fail=REFUSED))
    svc = _service(tmp_path)
    await svc._poll_once()
    unregister_source("mailbox")
    await svc._poll_once()
    assert svc.health()["sources"] == [] and svc.health()["last_poll_ok"] is True


def _status_state(tmp_path: Path, svc) -> MagicMock:
    st = MagicMock()
    st._inbox_svc = svc
    st._inbox_store = svc.inbox
    st._inbox_state = svc.state
    st.broadcast_ws = lambda ev, payload: None
    return st


@pytest.mark.asyncio
async def test_the_status_route_lists_each_source_with_its_last_poll(tmp_path):
    _drop_folder(tmp_path, False)
    _register(_Source("mailbox", fail=REFUSED))
    svc = _service(tmp_path)
    await svc._poll_once()
    app = web.Application()
    app["state"] = _status_state(tmp_path, svc)
    resp = await H.api_inbox_status(make_mocked_request("GET", "/api/inbox/status", app=app))
    body = json.loads(resp.text)
    rows = {row["name"]: row for row in body["sources"]}
    assert rows["mailbox"]["active"] is True and rows["mailbox"]["label"] == "Mailbox App"
    assert rows["mailbox"]["error"] == REFUSED and rows["mailbox"]["ok"] is False
    assert rows["filesystem"]["active"] is False, "the drop folder is off by default"
    assert rows["native"]["kind"] == "push"


# ── a reply goes back to the source the row came from ──────────────────────────────────────────


def _mail_row(store: InboxStore, source: str = "mailbox") -> InboxItem:
    # A mail that is itself a reply: its thread id names the mail it answered, and its own
    # Message-ID (the row's reply_target) is what a reply to it must be addressed with.
    item = InboxItem(
        id="noor@example.test_1790000000.0",
        channel="noor@example.test",
        channel_name="noor@example.test",
        thread_ts="<earlier@example.test>",
        message="Can you send it?",
        sender_id="talks@pyto.test",
        sender_name="PyTO",
        source=source,
        can_reply=True,
        reply_target="<a@example.test>",
    )
    store.add(item)
    return item


def _send(tmp_path: Path, store: InboxStore, text: str):
    st = MagicMock()
    st._inbox_svc = None
    st._inbox_store = store
    st._inbox_state = InboxState(tmp_path / "s.json")
    st.broadcast_ws = lambda ev, payload: None
    app = web.Application()
    app["state"] = st
    req = make_mocked_request("POST", "/api/inbox/send", app=app)

    async def body():
        return {"id": "noor@example.test_1790000000.0", "text": text}

    req.json = body
    return H.api_inbox_send(req)


@pytest.mark.asyncio
async def test_a_reply_goes_to_its_source_and_closes_the_row_once_sent(tmp_path):
    _drop_folder(tmp_path, False)
    mailbox = _Source("mailbox")
    _register(mailbox)
    store = InboxStore(path=tmp_path / "inbox.json")
    item = _mail_row(store)
    resp = await _send(tmp_path, store, "Final abstract attached.")
    assert resp.status == 200, resp.text
    assert json.loads(resp.text) == {"ok": True, "sent": True}
    assert mailbox.replies == [
        ("noor@example.test", "Final abstract attached.", "<a@example.test>")
    ], "the reply was not addressed with the mail's own id"
    assert store.items[item.id].status == ItemStatus.HANDLED.value
    assert store.items[item.id].draft == "Final abstract attached."
    # When it was sent: what lets the open panel say "Sent" of this text and offer no Send.
    assert store.items[item.id].replied_at > 0


class _KeptAsDraft:
    """A falsy result that says why, the way a channel transport's refusal does."""

    def __bool__(self) -> bool:
        return False

    def __str__(self) -> str:
        return "it keeps replies as drafts while Send Replies is off"


@pytest.mark.asyncio
async def test_a_reply_the_source_did_not_send_says_why_and_keeps_the_text(tmp_path):
    _drop_folder(tmp_path, False)
    _register(_Source("mailbox", reply=_KeptAsDraft()))
    store = InboxStore(path=tmp_path / "inbox.json")
    item = _mail_row(store)
    resp = await _send(tmp_path, store, "Final abstract attached.")
    assert resp.status == 409
    assert json.loads(resp.text)["error"] == (
        "Mailbox App didn't send the reply: it keeps replies as drafts while Send Replies is off."
    )
    assert store.items[item.id].status != ItemStatus.HANDLED.value
    assert store.items[item.id].draft == "Final abstract attached.", "the owner's text was lost"
    assert store.items[item.id].replied_at == 0, "a reply that was not sent is not a reply"


@pytest.mark.asyncio
async def test_a_plain_false_says_it_was_not_sent(tmp_path):
    _drop_folder(tmp_path, False)
    _register(_Source("mailbox", reply=False))
    store = InboxStore(path=tmp_path / "inbox.json")
    _mail_row(store)
    resp = await _send(tmp_path, store, "hi")
    assert resp.status == 409
    assert json.loads(resp.text)["error"] == "Mailbox App didn't send the reply."


@pytest.mark.asyncio
async def test_a_reply_to_a_source_that_is_gone_is_not_sent(tmp_path):
    _drop_folder(tmp_path, False)
    store = InboxStore(path=tmp_path / "inbox.json")
    item = _mail_row(store, source="mail")
    resp = await _send(tmp_path, store, "hi")
    assert resp.status == 503
    assert json.loads(resp.text)["error"] == (
        "Mail isn't connected, so the reply was not sent. Enable its app to send it."
    )
    assert store.items[item.id].status != ItemStatus.HANDLED.value


@pytest.mark.asyncio
async def test_a_source_that_raises_says_so_and_keeps_the_text(tmp_path):
    _drop_folder(tmp_path, False)
    mailbox = _Source("mailbox")

    async def boom(channel_id, text, thread_ts=None):
        raise RuntimeError("the SMTP server smtp.example.test:587 is unreachable")

    mailbox.send_reply = boom  # type: ignore[method-assign]
    _register(mailbox)
    store = InboxStore(path=tmp_path / "inbox.json")
    item = _mail_row(store)
    resp = await _send(tmp_path, store, "Final abstract attached.")
    assert resp.status == 502
    assert json.loads(resp.text)["error"] == (
        "Mailbox App could not send the reply: the SMTP server smtp.example.test:587 is "
        "unreachable."
    )
    assert store.items[item.id].draft == "Final abstract attached."
