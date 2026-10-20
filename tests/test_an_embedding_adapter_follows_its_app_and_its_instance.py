"""An app's embedding adapter follows the app's code and its instance's settings, with no restart.

An app that embeds for a provider type core does not know (Amazon Bedrock) registers a scanner,
and the embedding registry builds one adapter per configured instance from it. The registry kept
the first adapter it built under a name for the life of the process. So an app update, which
answers that no restart is needed, went on embedding with the version it replaced; an instance
whose region was edited in Settings → Providers went on with the old region; and an instance
removed, or its app uninstalled, left its adapter answering. The image, video, speech and voice
registries rebuild theirs.

The registry now replaces an adapter its app's update builds from new code, rebuilds every
app-built adapter when an instance is added, edited or removed, and drops the one no scanner
builds any more. It keeps an adapter while nothing it was built from changed: what holds an embed
function (``BoundEmbedding``) compares its sources by identity, and the adapter keeps the reason
its last embedding failed.

The app here is loaded and taken out the way the gateway does it (``app_code.claim``, an import
from its folder, ``app_code.release``), so its scanner is registered and taken back by the app
platform's own bookkeeping.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from personalclaw import app_code
from personalclaw.embedding_providers import registry as reg
from personalclaw.providers import media_scanners

APP = "embed-probe"
INSTANCE = "Probe"
MODULE = "_embed_probe_provider"

#: One version of the app's provider module. Each version embeds into a vector that names it, and
#: the instance's region.
_SOURCE = """
from personalclaw.sdk.embedding import EmbeddingProvider
from personalclaw.sdk.model import register_scanner

VERSION = {version}


class ProbeEmbedding(EmbeddingProvider):
    def __init__(self, name, region):
        self._name = name
        self.region = region

    @property
    def name(self):
        return self._name

    @property
    def display_name(self):
        return "Embedding probe"

    async def is_available(self):
        return True

    async def embed(self, text, model=""):
        return [float(VERSION), 1.0 if self.region == "us-east-1" else 0.0]

    async def embed_batch(self, texts, model=""):
        return [await self.embed(text, model) for text in texts]


def _scan(entries):
    return [
        ProbeEmbedding(e["name"], e["options"]["region"])
        for e in entries
        if e.get("type") == "embed-probe"
    ]


register_scanner("embedding", _scan)
"""


class _App:
    """The probe app as the gateway holds it: its versions installed, its instance's config."""

    def __init__(self, tmp_path: Path) -> None:
        self._tmp = tmp_path
        self.entries = [{"name": INSTANCE, "type": APP, "options": {"region": "us-west-2"}}]

    def install(self, version: int) -> None:
        """Load ``version`` the way the gateway loads an app: claim its folder, import its
        provider module from there."""
        root = self._tmp / f"v{version}"
        root.mkdir()
        (root / "provider.py").write_text(_SOURCE.format(version=version), encoding="utf-8")
        app_code.claim(APP, root)
        spec = importlib.util.spec_from_file_location(MODULE, root / "provider.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[MODULE] = module
        spec.loader.exec_module(module)

    def update(self, version: int) -> None:
        """What an app update does: take the installed version's code out, load the new one."""
        app_code.release(APP)
        self.install(version)


@pytest.fixture
def app(tmp_path, monkeypatch):
    """The probe app installed at version 1, with one instance, bound as the embedding model."""
    probe = _App(tmp_path)
    monkeypatch.setattr(media_scanners, "_scanners", {})
    monkeypatch.setattr(
        media_scanners, "_config_provider_entries", lambda: [dict(e) for e in probe.entries]
    )
    monkeypatch.setattr(reg, "_providers", {})
    monkeypatch.setattr(reg, "_scanned", {}, raising=False)
    monkeypatch.setattr(reg, "_active_embedding_spec", lambda: (INSTANCE, "probe-model"))
    probe.install(1)
    yield probe
    app_code.release(APP)
    for prefix in app_code.roots(APP):
        app_code._roots.pop(prefix, None)  # noqa: SLF001 — the claims this test made
    sys.modules.pop(MODULE, None)


def _embeds_with(bound: reg.BoundEmbedding) -> list[float] | None:
    fn, _ref = bound.current()
    return fn("a heron by the lake") if fn is not None else None


def test_an_app_update_embeds_with_the_update(app):
    """🔴 Red before: the update's code never embedded until the gateway restarted."""
    bound = reg.BoundEmbedding()
    assert _embeds_with(bound) == [1.0, 0.0]

    app.update(2)

    assert _embeds_with(bound) == [2.0, 0.0], "the next embedding runs the updated app's code"


def test_an_instance_edit_reaches_its_embedding_adapter(app):
    """An edit in Settings → Providers refreshes the media registries; the embedding adapter is
    rebuilt from the settings saved. 🔴 Red before: the old region stayed until a restart."""
    from personalclaw.dashboard.handlers.providers import _refresh_media_registries

    bound = reg.BoundEmbedding()
    assert _embeds_with(bound) == [1.0, 0.0]

    app.entries[0]["options"]["region"] = "us-east-1"
    _refresh_media_registries()

    assert reg.get_provider(INSTANCE).region == "us-east-1"
    assert _embeds_with(bound) == [1.0, 1.0]


def test_a_removed_instance_leaves_no_embedding_adapter(app):
    """🔴 Red before: the removed instance's adapter went on answering."""
    from personalclaw.dashboard.handlers.providers import _refresh_media_registries

    assert reg.get_provider(INSTANCE) is not None

    app.entries.clear()
    _refresh_media_registries()

    assert reg.get_provider(INSTANCE) is None
    assert INSTANCE not in {p.name for p in reg.list_providers()}


def test_an_uninstalled_app_leaves_no_embedding_adapter(app):
    """Its scanner is taken back with its code, so nothing builds the adapter any more. 🔴 Red
    before: the adapter of an app no longer installed went on answering."""
    assert reg.get_provider(INSTANCE) is not None

    app_code.release(APP)

    assert reg.get_provider(INSTANCE) is None


def test_an_adapter_is_kept_while_nothing_it_was_built_from_changed(app):
    """Each scan builds fresh adapters; the one registered stays, so a holder's embed function is
    not rebuilt on every call and the adapter keeps what it knows (why it last failed)."""
    first = reg.get_provider(INSTANCE)
    bound = reg.BoundEmbedding()
    fn, _ = bound.current()

    assert reg.get_provider(INSTANCE) is first
    assert {p.name for p in reg.list_providers()} == {INSTANCE}
    assert bound.current()[0] is fn


def test_a_provider_registered_by_name_is_not_replaced_by_a_scanner(app):
    """A provider registered under a name directly (the in-process native one is) stays that
    name's provider whatever a scanner builds under it."""

    class _Registered:
        name = INSTANCE

    registered = _Registered()
    reg.unregister_provider(INSTANCE)
    reg.register_provider(registered)  # type: ignore[arg-type]

    app.update(2)

    assert reg.get_provider(INSTANCE) is registered
