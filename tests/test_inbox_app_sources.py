"""An app-declared ``inbox`` provider must become a real, resolvable message source.

INU-8. Before this, ``inbox`` was served by an ``EntitySeamHandler``: the manifest
factory RAN at enable-time and its instance was discarded, and resolution read only
the ``personalclaw.message_source_providers`` entry-point group — which an installed
app cannot contribute to. So a manifest declaring ``{"type": "inbox", ...}`` validated,
installed clean, and then did nothing (the #47 class).

Driven here with a real ``MessageSourceProvider`` fixture app written to disk and put
through the actual install → enable → resolve → poll → disable path, plus the
precedence chain and the phantom-source check that deregistration is real.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, manager

APP_NAME = "fixture-inbox-app"
# Deliberately NOT the app name: the registry keys by the provider's own source_name
# (what an inbox item records and what a caller asks get_default_provider for).
SOURCE_NAME = "fixture-inbox"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Never touch the real home: PERSONALCLAW_HOME for anything that resolves it at
    runtime, plus the import-bound ``config_dir`` references (stores bind it at import)."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: tmp_path)
    # Fresh provider registry per test so registrations don't leak.
    from personalclaw.providers import registry as reg

    monkeypatch.setattr(reg, "_registry", None, raising=False)
    from personalclaw.inbox_providers import registry as app_sources

    app_sources._sources.clear()
    yield tmp_path
    app_sources._sources.clear()


_INBOX_PROVIDER_PY = textwrap.dedent('''
    from personalclaw.inbox_providers.base import IncomingMessage, MessageSourceProvider

    class FixtureInboxSource(MessageSourceProvider):
        """A source whose factory closes over app config — so it CANNOT be rebuilt
        from a class the way the entry-point path rebuilds one."""

        def __init__(self, config=None):
            self.config = config or {}

        @property
        def source_name(self):
            return "fixture-inbox"

        async def poll(self, watched_channels, checkpoints, user_id):
            msg = IncomingMessage(
                id="m1",
                channel_id="CFIX",
                channel_name="#fixture",
                text="hello from the app source",
                sender_id="U9",
                sender_name="App User",
                timestamp=1700000000.0,
            )
            return [msg], {"CFIX": "1700000000.0"}

        async def send_reply(self, channel_id, text, thread_ts=None):
            return True

        async def add_reaction(self, channel_id, ts, emoji):
            return True

        async def get_channel_history(self, channel_id, oldest, limit=200):
            return []

        async def resolve_user_name(self, user_id):
            return "App User"

    def create_provider(config=None):
        return FixtureInboxSource(config)
''')


def _inbox_app(tmp_path: Path) -> Path:
    d = tmp_path / "src" / APP_NAME
    d.mkdir(parents=True)
    (d / "app.json").write_text(
        json.dumps(
            {
                "name": APP_NAME,
                "version": "1.0.0",
                "displayName": "Fixture Inbox",
                "description": "provider-only inbox fixture",
                "provider": {
                    "type": "inbox",
                    "implementation": "provider:create_provider",
                },
            }
        ),
        encoding="utf-8",
    )
    (d / "provider.py").write_text(_INBOX_PROVIDER_PY, encoding="utf-8")
    return d


# ── end-to-end: declare → enable → resolve → message flows → disable → gone ──


@pytest.mark.asyncio
async def test_app_inbox_source_resolves_and_a_message_flows(tmp_path):
    """The whole point: an app's declared source is resolvable BY ITS source_name and a
    message it polls lands in the inbox through the generic InboxService path, attributed
    to that source. Fails without ``InboxTypeHandler`` — the seam has nothing to find."""
    from personalclaw.inbox import InboxState, InboxStore
    from personalclaw.inbox_providers import get_default_provider
    from personalclaw.inbox_providers.registry import list_source_names
    from personalclaw.inbox_service import InboxService

    res = app_manager.install(_inbox_app(tmp_path))
    assert res.ok, res.error

    # install() registers + enables providers → the source is live under source_name.
    assert list_source_names() == [SOURCE_NAME]
    provider = get_default_provider(SOURCE_NAME)
    assert provider.source_name == SOURCE_NAME
    # The INSTANCE the factory built (it closes over app config) — not a rebuilt class.
    assert type(provider).__name__ == "FixtureInboxSource"
    assert provider is get_default_provider(SOURCE_NAME)

    # A message flows through the GENERIC path (no vendor knowledge in InboxService).
    store = InboxStore()
    svc = InboxService(state=InboxState(), store=store, provider=provider, user_name="Alex")
    await svc._poll_once()
    items = list(store.items.values())
    assert len(items) == 1, [i.message for i in items]
    assert items[0].message == "hello from the app source"
    assert items[0].source == SOURCE_NAME
    assert items[0].can_reply is True

    # disable → the source is GONE (no phantom answering the seam).
    assert app_manager.disable(APP_NAME)
    assert list_source_names() == []
    fallback = get_default_provider(SOURCE_NAME)
    assert fallback.source_name != SOURCE_NAME

    # re-enable restores it (symmetric round trip)
    assert app_manager.enable(APP_NAME)
    assert get_default_provider(SOURCE_NAME).source_name == SOURCE_NAME


@pytest.mark.asyncio
async def test_uninstall_leaves_no_phantom_source(tmp_path):
    """A disabled/uninstalled app must not keep answering get_default_provider —
    otherwise the inbox looks like it is polling a source that is gone."""
    from personalclaw.inbox_providers.registry import get_source

    res = app_manager.install(_inbox_app(tmp_path))
    assert res.ok, res.error
    assert get_source(SOURCE_NAME) is not None

    assert app_manager.force_uninstall(APP_NAME)
    assert get_source(SOURCE_NAME) is None


def test_bundled_filesystem_inbox_is_a_live_consumer_of_this_path():
    """The mechanism is not fixture-only: the SHIPPED ``filesystem-inbox`` native app
    declares ``type: inbox``, so its factory's instance is what the real handler now
    registers and what the gateway's ``get_default_provider("filesystem")`` resolves.
    (The provider is stateless, so sharing that instance is equivalent to building a
    fresh one from the entry-point class — the pre-INU-8 behaviour.)"""
    import json as _json

    from personalclaw.inbox_providers.filesystem_source import create_provider
    from personalclaw.providers.loader import BUNDLED_DIR

    manifest = _json.loads((BUNDLED_DIR / "filesystem-inbox" / "app.json").read_text())
    assert manifest["provider"]["type"] == "inbox"
    assert create_provider().source_name == "filesystem"


def test_inbox_uses_a_real_handler_not_a_seam():
    """``inbox`` graduated from EntitySeamHandler → InboxTypeHandler; a seam no-op would
    silently drop the instance the seam now needs."""
    from personalclaw.providers.registry import (
        EntitySeamHandler,
        InboxTypeHandler,
        get_provider_registry,
    )

    handler = get_provider_registry()._type_handlers["inbox"]
    assert isinstance(handler, InboxTypeHandler)
    assert not isinstance(handler, EntitySeamHandler)


# ── precedence: app instance → entry-point class → native → filesystem ──


class _StubSource:
    """Stands in for an entry-point-discovered CLASS (that path yields types)."""

    _name = "stub"

    @property
    def source_name(self) -> str:
        return self._name


class _NativeStub(_StubSource):
    _name = "native"


class _FilesystemStub(_StubSource):
    _name = "filesystem"


class _EntryPointFixture(_StubSource):
    _name = SOURCE_NAME


def test_app_instance_beats_entry_point_class(monkeypatch):
    """Both paths can carry the same name; the app-contributed instance wins so an
    installed app actually takes its source_name."""
    import personalclaw.inbox_providers as ip
    from personalclaw.inbox_providers.registry import register_source

    monkeypatch.setattr(ip, "_cache", {SOURCE_NAME: _EntryPointFixture})
    app_instance = _EntryPointFixture()
    register_source(app_instance)

    resolved = ip.get_default_provider(SOURCE_NAME)
    assert resolved is app_instance, "entry-point class shadowed the app-contributed source"


def test_falls_back_to_entry_point_then_native_then_filesystem(monkeypatch):
    import personalclaw.inbox_providers as ip
    from personalclaw.inbox_providers.filesystem_source import FilesystemSourceProvider

    # requested name present in the entry-point group → instantiated from the CLASS
    monkeypatch.setattr(ip, "_cache", {SOURCE_NAME: _EntryPointFixture, "native": _NativeStub})
    assert isinstance(ip.get_default_provider(SOURCE_NAME), _EntryPointFixture)

    # unknown name → native
    monkeypatch.setattr(ip, "_cache", {"native": _NativeStub, "filesystem": _FilesystemStub})
    assert isinstance(ip.get_default_provider("nope"), _NativeStub)

    # no native → filesystem
    monkeypatch.setattr(ip, "_cache", {"filesystem": _FilesystemStub})
    assert isinstance(ip.get_default_provider("nope"), _FilesystemStub)

    # nothing discovered at all → the terminal in-process filesystem source
    monkeypatch.setattr(ip, "_cache", {})
    assert isinstance(ip.get_default_provider("nope"), FilesystemSourceProvider)


def test_app_source_registry_rejects_a_nameless_source():
    """source_name is the key; an empty one would create an unaddressable phantom."""
    from personalclaw.inbox_providers.registry import register_source

    class _Nameless(_StubSource):
        _name = ""

    with pytest.raises(ValueError):
        register_source(_Nameless())


def test_handler_register_deregister_round_trips():
    """Direct handler round trip (the KnowledgeTypeHandler pattern): registered by
    source_name, and gone after deregister."""
    from personalclaw.inbox_providers.registry import get_source, list_source_names
    from personalclaw.providers.registry import InboxTypeHandler

    handler = InboxTypeHandler()
    inst = _EntryPointFixture()
    handler.register(None, inst)  # ext unused by register()
    assert list_source_names() == [SOURCE_NAME]
    assert get_source(SOURCE_NAME) is inst

    handler.deregister(None, inst)
    assert list_source_names() == []
