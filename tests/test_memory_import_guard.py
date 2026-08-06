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
from personalclaw.dashboard.handlers.memory import api_memory_import

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
