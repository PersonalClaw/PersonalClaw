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
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw import memory_writes
from personalclaw.cli_commands import _memory_cmd
from personalclaw.dashboard.handlers.memory import (
    api_memory_consolidate,
    api_memory_import,
    api_memory_migrate,
    api_memory_promote,
    api_memory_vault_sync,
)
from personalclaw.workflows import ownership

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

    with pytest.raises(SystemExit) as exited:
        _memory_cmd(argparse.Namespace(mem_action="import", file=str(path)))

    out = capsys.readouterr()
    assert exited.value.code == 1
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

RESTRICTED_CHAT = "e1"
RESTRICTED_KEY = f"dashboard:{RESTRICTED_CHAT}"


def _state(*chats: tuple[str, str]):
    """The dashboard state the gateway holds, with these chats live in it, by name and in their
    mode, as it holds a chat from its creation on. The guard reads a chat's mode there first
    (``memory_writes.session_mode``), before its transcript exists."""
    from personalclaw.dashboard.state import DashboardState

    state = DashboardState(sessions=MagicMock(count=0), start_time=0.0)
    for name, mode in chats:
        state.get_or_create_session(name, memory_mode=mode)
    return state


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


def _stub_the_writes(monkeypatch):
    """The provider, the memory service and ``_sel`` stubbed, so a fired guard touches NOTHING
    (never a real home) and what it audits is kept."""
    provider = MagicMock()
    service = MagicMock()
    # The picked memory's and the global memory's (vault sync and migrate read only that one).
    for name in ("_get_provider", "_global_provider"):
        monkeypatch.setattr(f"personalclaw.dashboard.handlers.memory.{name}", provider)
    for name in ("_get_service", "_global_service"):
        monkeypatch.setattr(f"personalclaw.dashboard.handlers.memory.{name}", service)
    audit = MagicMock()
    monkeypatch.setattr("personalclaw.dashboard.handlers.memory._sel", lambda: audit)
    return provider, service, audit


def _restricted_request(path, monkeypatch, mode):
    """A POST from a Temporary or Incognito chat's session, with the writes stubbed.

    The chat is live in the gateway's state, as the gateway holds it from its creation on: the
    record the guard reads first. A mock state holds no chats to read, so the guard would find no
    record of the chat at all and read its request as work that is no chat's.
    """
    provider, service, audit = _stub_the_writes(monkeypatch)
    app = web.Application()
    app["state"] = _state((RESTRICTED_CHAT, mode))
    request = make_mocked_request("POST", path, headers={"X-Session-Key": RESTRICTED_KEY}, app=app)
    return request, provider, service, audit


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["temporary", "incognito"])
@pytest.mark.parametrize(
    "handler,path,operation",
    [
        (api_memory_vault_sync, "/api/memory/vault/sync", "memory.vault_sync"),
        (api_memory_migrate, "/api/memory/migrate", "memory.migrate"),
        (api_memory_promote, "/api/memory/promote", "memory.promote"),
    ],
)
async def test_restricted_session_is_denied_with_no_side_effect(
    monkeypatch, handler, path, operation, mode
):
    request, provider, service, audit = _restricted_request(path, monkeypatch, mode)

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


# ── a session whose mode cannot be read is refused, over real HTTP ──
#
# The guard reads a session's mode from every record of it (``memory_writes.session_mode``): the
# live chat, the registry a channel marks, the transcript, the run a step names. A record that is
# there and cannot be read says nothing of what the chat allows, so the write is refused as a
# Temporary chat's is. Read as a session with nothing recorded, the write would go through.

#: A channel thread's chat: no live dashboard chat names it, so its own records are all there is.
THREAD_KEY = "channel:thread-4242"
#: A stage of a workflow run, whose mode is its run's.
STEP_RUN = "5e1f0a2b"
STEP_KEY = ownership.owned_key(STEP_RUN, "summarize")

#: Each write route: its path, its handler, what it audits as, and the body it is sent.
WRITE_ROUTES: list[tuple[str, Any, str, Any]] = [
    ("/api/memory/vault/sync", api_memory_vault_sync, "memory.vault_sync", None),
    ("/api/memory/migrate", api_memory_migrate, "memory.migrate", None),
    ("/api/memory/promote", api_memory_promote, "memory.promote", {}),
    ("/api/memory/import", api_memory_import, "memory.import", {"semantic": [], "episodic": []}),
    ("/api/memory/consolidate", api_memory_consolidate, "memory.consolidate", {"key": THREAD_KEY}),
]


def _guarded_app(state) -> web.Application:
    """The write routes as the gateway serves them: behind the memory write middleware, which
    makes each request the work of the session it names, beside the gateway's state."""
    from personalclaw.dashboard.memory_write_gate import memory_write_middleware

    app = web.Application(middlewares=[memory_write_middleware()])
    app["state"] = state
    for path, handler, _operation, _body in WRITE_ROUTES:
        app.router.add_post(path, handler)
    return app


def _transcript_path(key: str):
    from personalclaw.history import session_path

    path = session_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _a_run_record(run_id: str) -> None:
    """A run's record, as its start creates it before its first stage runs."""
    from personalclaw.workflows import store
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    store.create(WorkflowRun(id=run_id, workflow_name="weekly-digest", status=RunStatus.RUNNING))


def _metadata_cut_short(monkeypatch) -> str:
    _transcript_path(THREAD_KEY).write_text('{"_type": "metadata", "memory_mo\n', encoding="utf-8")
    return THREAD_KEY


def _a_transcript_that_cannot_be_opened(monkeypatch) -> str:
    _transcript_path(THREAD_KEY).mkdir()  # a folder where the file belongs: opening it fails
    return THREAD_KEY


def _a_registry_that_cannot_be_read(monkeypatch) -> str:
    def _cannot_read(_key: str) -> bool:
        raise RuntimeError("the registry cannot be read")

    monkeypatch.setattr("personalclaw.session_restrictions.is_temporary", _cannot_read)
    return THREAD_KEY


def _a_run_store_that_cannot_be_read(monkeypatch) -> str:
    from personalclaw.workflows import store

    database = store.workflows_dir() / "runs.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    database.write_bytes(b"this is not a database " * 64)
    return STEP_KEY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unreadable",
    [
        pytest.param(_metadata_cut_short, id="its-transcripts-metadata-is-cut-short"),
        pytest.param(_a_transcript_that_cannot_be_opened, id="its-transcript-cannot-be-opened"),
        pytest.param(_a_registry_that_cannot_be_read, id="the-registry-cannot-be-read"),
        pytest.param(_a_run_store_that_cannot_be_read, id="its-runs-record-cannot-be-read"),
    ],
)
async def test_a_session_whose_mode_cannot_be_read_is_refused_over_real_http(
    monkeypatch, unreadable
):
    """A guard that cannot read what the chat allows refuses: every write route answers the
    restricted refusal, audits the denial and starts nothing. The same sessions are let through
    when their records can be read (the test below), so it is the unreadable record that
    refuses."""
    from aiohttp.test_utils import TestClient, TestServer

    provider, service, audit = _stub_the_writes(monkeypatch)
    key = unreadable(monkeypatch)

    async with TestClient(TestServer(_guarded_app(_state()))) as client:
        for path, _handler, _operation, body in WRITE_ROUTES:
            resp = await client.post(path, json=body, headers={"X-Session-Key": key})
            assert resp.status == 403, f"{path}: {resp.status} {await resp.text()}"
            assert (await resp.json())["error"] == memory_writes.REFUSAL

    assert provider.call_count == 0
    assert service.call_count == 0
    assert [call.kwargs for call in audit.log_api_access.call_args_list] == [
        {
            "caller": key,
            "operation": operation,
            "outcome": "denied",
            "source": "dashboard",
            "resources": "restricted_session_block",
        }
        for _path, _handler, operation, _body in WRITE_ROUTES
    ]


#: A chat that keeps its memory, live in the gateway and not yet transcribed.
NORMAL_CHAT = "n1"


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [THREAD_KEY, STEP_KEY, f"dashboard:{NORMAL_CHAT}"])
async def test_a_session_whose_record_reads_as_keeping_memory_is_let_through(monkeypatch, key):
    """The control: the thread's transcript and the step's run readable and keeping memory, and a
    live chat that keeps it, read from the live chat on its first turn."""
    from aiohttp.test_utils import TestClient, TestServer

    store = _RecordingStore()
    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.memory._get_provider", lambda _state: store
    )
    if key == THREAD_KEY:
        metadata = {"_type": "metadata", "memory_mode": "persistent"}
        _transcript_path(THREAD_KEY).write_text(json.dumps(metadata) + "\n", encoding="utf-8")
    elif key == STEP_KEY:
        _a_run_record(STEP_RUN)
    payload = {"semantic": [{"key": "project.x", "value_json": '"v"'}], "episodic": []}

    state = _state((NORMAL_CHAT, "persistent"))
    async with TestClient(TestServer(_guarded_app(state))) as client:
        resp = await client.post("/api/memory/import", json=payload, headers={"X-Session-Key": key})
        assert resp.status == 200, await resp.text()

    assert store.imported == [payload]


@pytest.mark.asyncio
async def test_migrate_normal_session_is_not_blocked(monkeypatch):
    store = _WriteStore()
    # Legacy markdown memory is the global memory's alone, so the migration reads only that one.
    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.memory._global_provider", lambda _state: store
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

    seen: dict = {}

    class _FakeVault:
        def __init__(self, service, vdir, *, mode="mirror"):
            self.vdir = vdir
            seen["mode"] = mode

        def sync(self) -> dict:
            seen["synced"] = True
            return {"created": 0, "updated": 0, "deleted": 0}

        async def sweep_raw(self, *, knowledge=None, enqueue=None) -> dict:
            seen["swept"] = True
            return {"ingested": 0, "refused": 0, "waiting": 0, "left": 0, "failed": 0}

    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.memory._get_service", lambda _state: MagicMock()
    )
    monkeypatch.setattr("personalclaw.memory_vault.MemoryVault", _FakeVault)
    monkeypatch.setattr("personalclaw.memory_vault.vault_mode_from_config", lambda: "off")
    monkeypatch.setattr("personalclaw.memory_vault.vault_path_from_config", lambda: tmp_path)

    app = web.Application()
    app["state"] = MagicMock()
    request = make_mocked_request("POST", "/api/memory/vault/sync", app=app)

    resp = await api_memory_vault_sync(request)

    assert resp.status != 403
    assert json.loads(resp.body)["path"] == str(tmp_path)
    assert seen["synced"] is True and seen["swept"] is True
    # An `off` vault exports one-shot but must NOT be silently upgraded to two_way:
    # a "sync now" button is not how a user chooses to have their files read back.
    assert seen["mode"] == "mirror"
