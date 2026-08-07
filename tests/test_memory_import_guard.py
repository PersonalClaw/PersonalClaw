"""Regression: a memory import body that isn't an object is a client error (#591).

Both import entry points guarded the JSON *parse* but not the parsed *shape*, so
every scalar/array shape (`[]`, `"a string"`, `42`, `null`, `true`) reached
``import_memory``, which calls ``data.get("semantic", ...)`` — an AttributeError.
On the HTTP surface that surfaced as a bare 500; on the CLI it surfaced as a
traceback. Nine sibling handlers in the same module already reject a non-object
body with 400, so the fix is the house guard, not new phrasing.
"""

from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.cli_commands import _memory_cmd
from personalclaw.dashboard.handlers.memory import (
    api_memory_import,
    api_memory_migrate,
    api_memory_promote,
    api_memory_vault_sync,
)

# The five shapes measured against a live gateway — all of them used to 500.
NON_OBJECT_BODIES = [[], "a string", 42, None, True]


class _RecordingStore:
    """Records what reached the store, crashing on a non-dict exactly as the real
    ``VectorMemoryStore.import_memory`` does.

    Reproducing the AttributeError matters: a stub that tolerantly accepted any
    shape would return 200 with the guard removed, so the test would pin only the
    status code and not the crash the issue is about.
    """

    def __init__(self):
        self.imported: list = []

    def import_memory(self, data: dict) -> dict[str, int]:
        self.imported.append(data)
        data.get("semantic", [])  # the line vector_memory.py:2789 dies on
        return {"semantic": 1, "episodic": 0, "skipped": 0}


@pytest.fixture
def _import_request(monkeypatch):
    """A POST /api/memory/import request with a stubbed provider.

    ``make_mocked_request`` gives real (empty) headers, so the restricted-session
    gate ahead of the guard sees no X-Session-Key and lets the request through.
    """
    store = _RecordingStore()
    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.memory._get_provider", lambda _state: store
    )

    def _make(body):
        app = web.Application()
        app["state"] = MagicMock()
        request = make_mocked_request("POST", "/api/memory/import", app=app)

        async def _json():
            return body

        request.json = _json  # type: ignore[method-assign]
        return request

    return _make, store


@pytest.mark.asyncio
@pytest.mark.parametrize("body", NON_OBJECT_BODIES)
async def test_non_object_body_is_a_client_error(_import_request, body):
    make, store = _import_request

    resp = await api_memory_import(make(body))

    assert resp.status == 400
    assert json.loads(resp.body)["error"] == "JSON body must be an object"
    assert store.imported == []  # never reached import_memory


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["[]", '"a string"', "42", "null", "true"])
async def test_non_object_body_over_real_http_is_400_not_500(monkeypatch, raw):
    """The surface the bug was reported on: unguarded, aiohttp turned the
    AttributeError into a 500 'Server got itself in trouble'.

    The body goes over the wire as raw bytes rather than via the client's
    ``json=`` kwarg, because ``json=None`` sends no body at all and would land on
    the parse guard instead of the shape guard under test.
    """
    from aiohttp.test_utils import TestClient, TestServer

    store = _RecordingStore()
    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.memory._get_provider", lambda _state: store
    )
    app = web.Application()
    app["state"] = MagicMock()
    app.router.add_post("/api/memory/import", api_memory_import)

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/api/memory/import", data=raw, headers={"Content-Type": "application/json"}
        )
        assert resp.status == 400
        assert (await resp.json())["error"] == "JSON body must be an object"
    assert store.imported == []


@pytest.mark.asyncio
async def test_object_body_still_imports(_import_request):
    make, store = _import_request
    payload = {"semantic": [{"key": "project.x", "value_json": '"v"'}], "episodic": []}

    resp = await api_memory_import(make(payload))

    assert resp.status == 200
    assert json.loads(resp.body) == {"semantic": 1, "episodic": 0, "skipped": 0}
    assert store.imported == [payload]


@pytest.fixture
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize("raw", ["[]", '"a string"', "42", "null", "true"])
def test_cli_import_rejects_a_non_object_file(_home, capsys, raw):
    """`personalclaw memory import` is the second caller and crashed identically."""
    path = _home / "export.json"
    path.write_text(raw, encoding="utf-8")

    _memory_cmd(argparse.Namespace(mem_action="import", file=str(path)))

    out = capsys.readouterr()
    assert "must contain a JSON object" in out.err
    assert "Import complete" not in out.out


def test_cli_import_still_accepts_an_object_file(_home, capsys):
    path = _home / "export.json"
    path.write_text(
        json.dumps({"semantic": [{"key": "project.x", "value_json": '"v"'}], "episodic": []}),
        encoding="utf-8",
    )

    _memory_cmd(argparse.Namespace(mem_action="import", file=str(path)))

    out = capsys.readouterr()
    assert "Import complete" in out.out
    assert "Semantic: 1" in out.out


# ── #801: restricted-session guard on the three write handlers that skipped it ──
#
# ``vault_sync``/``migrate``/``promote`` never ran the ``_is_restricted_session``
# gate that ``api_memory_import``/``api_memory_consolidate`` enforce — and the
# first two read no request body at all, so ANY POST (even a garbage body) from an
# incognito/temporary/guest session ran the full side effect: mirror the whole
# store to disk, migrate legacy memory, promote episodics. A restricted session is
# explicitly promised memory writes are OFF. The fix copies ``api_memory_import``'s
# guard verbatim (403 + ``sel.log_api_access(..., outcome="denied")``).

RESTRICTED_KEY = "dashboard:e1"


class _WriteStore:
    """Minimal provider for the NORMAL-session path — records the write it ran so
    the guard-fired case can assert the same write never started."""

    def __init__(self):
        self.embed_fn = object()  # truthy → migrate skips the embed-fn wiring branch
        self.calls: list[str] = []

    def migrate_from_markdown(self) -> dict[str, int]:
        self.calls.append("migrate")
        return {"semantic": 0, "episodic": 0}

    def promote_episodic_patterns(self, min_count: int, min_sim: float) -> int:
        self.calls.append("promote")
        return 3


def _restricted_request(path, monkeypatch):
    """A POST from a restricted session, with the provider/service and ``_sel``
    stubbed so a fired guard touches NOTHING (never a real home).

    ``_is_restricted_session`` returns True on the first check (``sk in
    state._restricted_keys``), so a real set on the mock state is enough to arm the
    gate while keeping the file's ``MagicMock`` state + ``make_mocked_request``
    harness.
    """
    provider = MagicMock()
    service = MagicMock()
    monkeypatch.setattr("personalclaw.dashboard.handlers.memory._get_provider", provider)
    monkeypatch.setattr("personalclaw.dashboard.handlers.memory._get_service", service)
    audit = MagicMock()
    monkeypatch.setattr("personalclaw.dashboard.handlers.memory._sel", lambda: audit)

    app = web.Application()
    state = MagicMock()
    state._restricted_keys = {RESTRICTED_KEY}
    app["state"] = state
    request = make_mocked_request("POST", path, headers={"X-Session-Key": RESTRICTED_KEY}, app=app)
    return request, provider, service, audit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler,path,operation",
    [
        (api_memory_vault_sync, "/api/memory/vault/sync", "memory.vault_sync"),
        (api_memory_migrate, "/api/memory/migrate", "memory.migrate"),
        (api_memory_promote, "/api/memory/promote", "memory.promote"),
    ],
)
async def test_restricted_session_is_denied_with_no_side_effect(
    monkeypatch, handler, path, operation
):
    request, provider, service, audit = _restricted_request(path, monkeypatch)

    resp = await handler(request)

    assert resp.status == 403
    assert json.loads(resp.body)["error"] == "Memory writes are not allowed in this session mode."
    # The side effect never started: neither the vector provider nor the memory
    # service was even resolved.
    assert provider.call_count == 0
    assert service.call_count == 0
    audit.log_api_access.assert_called_once_with(
        caller=RESTRICTED_KEY,
        operation=operation,
        outcome="denied",
        source="dashboard",
        resources="restricted_session_block",
    )


@pytest.mark.asyncio
async def test_migrate_normal_session_is_not_blocked(monkeypatch):
    store = _WriteStore()
    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.memory._get_provider", lambda _state: store
    )
    app = web.Application()
    app["state"] = MagicMock()  # empty headers → not a restricted session
    request = make_mocked_request("POST", "/api/memory/migrate", app=app)

    resp = await api_memory_migrate(request)

    assert resp.status != 403
    assert store.calls == ["migrate"]


@pytest.mark.asyncio
async def test_promote_normal_session_is_not_blocked(monkeypatch):
    store = _WriteStore()
    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.memory._get_provider", lambda _state: store
    )
    app = web.Application()
    app["state"] = MagicMock()
    request = make_mocked_request("POST", "/api/memory/promote", app=app)

    async def _json():
        return {}

    request.json = _json  # type: ignore[method-assign]

    resp = await api_memory_promote(request)

    assert resp.status != 403
    assert store.calls == ["promote"]
    assert json.loads(resp.body) == {"ok": True, "promoted": 3}


@pytest.mark.asyncio
async def test_vault_sync_normal_session_is_not_blocked(monkeypatch, tmp_path):
    """The vault write stays in tmp_path — a fake MemoryVault whose sync() is a
    no-op — so this proves the guard does not over-block without touching a home."""

    class _FakeVault:
        def __init__(self, service, vdir):
            self.vdir = vdir

        def sync(self) -> dict:
            return {"created": 0, "updated": 0, "deleted": 0}

    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.memory._get_service", lambda _state: MagicMock()
    )
    monkeypatch.setattr("personalclaw.memory_vault.MemoryVault", _FakeVault)
    monkeypatch.setattr("personalclaw.memory_vault.vault_dir_from_config", lambda: tmp_path)

    app = web.Application()
    app["state"] = MagicMock()
    request = make_mocked_request("POST", "/api/memory/vault/sync", app=app)

    resp = await api_memory_vault_sync(request)

    assert resp.status != 403
    assert json.loads(resp.body)["path"] == str(tmp_path)
