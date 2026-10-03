"""A large upload leaves the gateway answering while it lands.

Measured: completing a resumable upload copied every part into one file and hashed it on the event
loop, and a knowledge upload sent in one request was moved and hashed there too. On the loop,
assembling a 512 MB upload stopped every other request for up to 0.34 s and hashing it for 0.22 s,
and a 2 GB one for as long as copying 2 GB takes: no request, socket message or approval was
answered meanwhile. The content scan a text-like upload gets held the loop for its whole run
(0.45 s for a 512 KB window of code).

The work on the file now runs off the loop: assembling, moving, hashing and removing it in worker
threads (file reads and writes and the hash leave the interpreter lock, so the loop keeps its
turns), and the scan in a child process (its parse of the window holds the lock: in a worker
thread it still stopped the loop for 0.12 s).

The threshold, for each case below: while the upload lands, the event loop never goes
:data:`_BOUND` (0.1 s) without a turn, and every health check asked meanwhile answers within it.
"""

from __future__ import annotations

import asyncio
import gc
import os
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers import knowledge as kn_handlers
from personalclaw.dashboard.handlers_system import api_healthz
from personalclaw.dashboard.routes import _register_upload_routes
from personalclaw.knowledge.pipeline import ensure_nodes_registered
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.uploads.store import UploadStore

_MB = 1024 * 1024
#: The longest the event loop may go without a turn, and a health check may take, while it lands.
_BOUND = 0.1
#: The resumable upload: 256 MB, assembled from 32 parts and hashed (on the loop: about 0.19 s).
_LARGE = 256 * _MB
#: The single-request upload: 384 MB, moved and hashed (on the loop: about 0.18 s).
_LARGER = 384 * _MB


def _bytes(size: int) -> bytes:
    """*size* bytes of a file: what is in it does not change how long it takes to copy or hash."""
    return bytes(range(256)) * (size // 256)


@pytest.fixture()
def gateway(tmp_path, monkeypatch):
    """The real upload routes, the knowledge ingest route and the health check, over a home in tmp.

    Free disk reads as plenty, so a large upload is kept as separate parts and assembled at its
    complete, whatever this machine has free."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(tempfile, "tempdir", str(_mkdir(tmp_path / "tmp")))
    monkeypatch.setattr("personalclaw.uploads.store._free_bytes", lambda _path: 1 << 40)
    # A chat attachment's extraction is the chat's business, not this upload's.
    monkeypatch.setattr(
        "personalclaw.dashboard.attachment_extract.get_extractor",
        lambda: SimpleNamespace(start=lambda *a, **k: None),
    )
    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.files._upload_dir", lambda: tmp_path / "home" / "uploads"
    )
    ensure_nodes_registered()
    queued: list[str] = []
    app = web.Application(client_max_size=4 * 1024 * _MB)
    app["upload_store"] = UploadStore(tmp_path / "home" / "uploads" / ".parts")
    app["state"] = SimpleNamespace(
        knowledge_store=KnowledgeStore(str(tmp_path / "knowledge.db")),
        knowledge_ingest_queue=lambda: SimpleNamespace(enqueue=queued.append),
    )
    _register_upload_routes(app)
    app.router.add_post("/api/knowledge/ingest", kn_handlers.ingest_file)
    app.router.add_get("/api/healthz", api_healthz)
    return SimpleNamespace(
        app=app,
        parts=tmp_path / "home" / "uploads" / ".parts",
        uploads=tmp_path / "home" / "uploads",
        store=app["state"].knowledge_store,
        queued=queued,
    )


def _mkdir(path):
    path.mkdir(parents=True, exist_ok=True)
    return path


class _Probe:
    """Beside some work: a 10 ms tick on the event loop, and a health check asked again 10 ms
    after the last one answered."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.worst_gap = 0.0
        self.slowest_answer = 0.0
        self.answered_during = 0

    async def during(self, work):
        stop = asyncio.Event()
        started = finished = 0.0

        async def tick() -> None:
            last = time.monotonic()
            while not stop.is_set():
                await asyncio.sleep(0.01)
                now = time.monotonic()
                self.worst_gap = max(self.worst_gap, now - last)
                last = now

        async def ask() -> None:
            while not stop.is_set():
                asked = time.monotonic()
                r = await self.client.get("/api/healthz")
                await r.read()
                assert r.status == 200
                now = time.monotonic()
                self.slowest_answer = max(self.slowest_answer, now - asked)
                if started and not finished:
                    self.answered_during += 1
                await asyncio.sleep(0.01)

        # The tick measures the upload's work, not the collector's: a full collection of this
        # test process's heap is not something the upload does.
        gc.collect()
        gc.disable()
        tasks = [asyncio.create_task(tick()), asyncio.create_task(ask())]
        try:
            await asyncio.sleep(0.05)  # both run before the work starts
            started = time.monotonic()
            result = await work
            finished = time.monotonic()
        finally:
            stop.set()
            await asyncio.gather(*tasks)
            gc.enable()
        self.took = finished - started
        return result

    def says(self) -> str:
        return (
            f"the work took {self.took:.2f}s; the event loop's longest gap was "
            f"{self.worst_gap:.3f}s and the slowest health check {self.slowest_answer:.3f}s "
            f"({self.answered_during} answered during it)"
        )


async def _init(client, size: int, *, target: str, filename: str, mime: str) -> dict:
    r = await client.post(
        "/api/uploads/init",
        json={"filename": filename, "size": size, "mime": mime, "target": target},
    )
    assert r.status == 200, await r.text()
    return await r.json()


async def _send_parts(client, info: dict, payload: bytes) -> None:
    part = info["partSize"]
    for i in range(info["totalParts"]):
        r = await client.put(
            f"/api/uploads/{info['uploadId']}/part?index={i}",
            data=payload[i * part : (i + 1) * part],
            headers={"Content-Type": "application/octet-stream"},
        )
        assert r.status == 200, await r.text()


async def _complete(client, uid: str) -> tuple[int, dict]:
    r = await client.post(f"/api/uploads/{uid}/complete", json={})
    return r.status, await r.json()


def _items(store: KnowledgeStore) -> int:
    return store.db.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"]


@pytest.mark.asyncio
async def test_completing_a_large_upload_leaves_the_gateway_answering(gateway):
    """🔴 Red before: the complete of a 256 MB video copied its 32 parts into one file and hashed
    it on the event loop, which went 0.19 s without a turn."""
    payload = _bytes(_LARGE)
    async with TestClient(TestServer(gateway.app)) as client:
        info = await _init(
            client, _LARGE, target="knowledge", filename="walkthrough.mov", mime="video/quicktime"
        )
        await _send_parts(client, info, payload)
        probe = _Probe(client)

        status, body = await probe.during(_complete(client, info["uploadId"]))

    assert status == 200, body
    assert gateway.queued == [body["item_id"]], "premise: the upload became an item"
    item = gateway.store.get_item(body["item_id"])
    assert item["file_size"] == _LARGE
    assert not (gateway.parts / info["uploadId"]).exists(), "its parts were removed"
    assert probe.worst_gap < _BOUND, probe.says()
    assert probe.slowest_answer < _BOUND, probe.says()
    assert probe.answered_during >= 1, f"premise: the gateway was asked meanwhile; {probe.says()}"


async def _multipart(filename: str, mime: str, payload: bytes, boundary: str):
    """A one-file form body, sent a megabyte at a time as a browser sends it."""
    yield (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode()
    view = memoryview(payload)
    for start in range(0, len(payload), _MB):
        yield bytes(view[start : start + _MB])
    yield f"\r\n--{boundary}--\r\n".encode()


@pytest.mark.asyncio
async def test_filing_a_large_single_request_upload_leaves_the_gateway_answering(gateway):
    """🔴 Red before: a knowledge upload sent in one request was moved and hashed on the event
    loop once its bytes had arrived: 0.18 s without a turn for this one."""
    payload = _bytes(_LARGER)
    boundary = "pcuploadboundary"
    async with TestClient(TestServer(gateway.app)) as client:
        probe = _Probe(client)

        async def send() -> tuple[int, dict]:
            r = await client.post(
                "/api/knowledge/ingest",
                data=_multipart("walkthrough.mov", "video/quicktime", payload, boundary),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
            return r.status, await r.json()

        status, body = await probe.during(send())

    assert status == 200, body
    assert gateway.queued == [body["item_id"]], "premise: the upload became an item"
    assert gateway.store.get_item(body["item_id"])["file_size"] == _LARGER
    assert probe.worst_gap < _BOUND, probe.says()
    assert probe.slowest_answer < _BOUND, probe.says()
    assert probe.answered_during >= 1, f"premise: the gateway was asked meanwhile; {probe.says()}"


def _json_lines(size: int) -> bytes:
    """Ordinary JSON lines, the kind of export that is uploaded as a large text file. Each line
    also reads as Python, and is 64 bytes, so the scan's head and tail windows end on a line's
    end and its parse has the whole window to parse."""
    line = b'{"id": 12, "title": "release walkthrough", "tags": ["notes"]}'
    line = line.ljust(63) + b"\n"
    assert len(line) == 64
    return line * (size // len(line))


@pytest.mark.asyncio
async def test_scanning_a_large_text_upload_leaves_the_gateway_answering(gateway):
    """🔴 Red before: the content scan ran on the event loop, which went its whole run without
    a turn. In a worker thread its parse still held the loop for about 0.1 s; in a child
    process, not at all."""
    payload = _json_lines(2 * _MB)
    async with TestClient(TestServer(gateway.app)) as client:
        info = await _init(
            client,
            len(payload),
            target="attachment",
            filename="feed-export.jsonl",
            mime="application/x-ndjson",
        )
        await _send_parts(client, info, payload)
        probe = _Probe(client)

        status, body = await probe.during(_complete(client, info["uploadId"]))

    assert status == 200, body
    (path,) = body["paths"]
    assert os.path.getsize(path) == len(payload), "premise: the scanned upload was kept"
    assert probe.worst_gap < _BOUND, probe.says()
    assert probe.slowest_answer < _BOUND, probe.says()
    assert probe.answered_during >= 1, f"premise: the gateway was asked meanwhile; {probe.says()}"


# ── while its complete runs, nothing else touches the upload ────────────────────────────────


class _HeldAssembly:
    """Holds *store*'s assembly of an upload in its worker thread until :meth:`release`."""

    def __init__(self, store: UploadStore) -> None:
        self.entered = threading.Event()
        self._go = threading.Event()
        self._real = store._concatenate

    def __call__(self, sess, final):
        self.entered.set()
        assert self._go.wait(30), "the test never let the assembly go on"
        return self._real(sess, final)

    def release(self) -> None:
        self._go.set()


@pytest.mark.asyncio
async def test_a_running_complete_is_left_alone_and_still_makes_its_item(gateway):
    """The complete's work runs in threads, so other requests are answered meanwhile. A retried
    complete, a part sent again and a drop (the page sends one when the complete's request
    fails) are each refused with 409 while it runs, and the complete makes its one item."""
    payload = _bytes(20 * _MB)
    store = gateway.app["upload_store"]
    held = _HeldAssembly(store)
    with patch.object(store, "_concatenate", held):
        async with TestClient(TestServer(gateway.app)) as client:
            info = await _init(
                client, len(payload), target="knowledge", filename="clip.mp4", mime="video/mp4"
            )
            uid = info["uploadId"]
            await _send_parts(client, info, payload)
            first = asyncio.ensure_future(_complete(client, uid))
            assert await asyncio.to_thread(held.entered.wait, 30), "the complete never began"

            again = await client.post(f"/api/uploads/{uid}/complete", json={})
            part = await client.put(
                f"/api/uploads/{uid}/part?index=0",
                data=payload[: info["partSize"]],
                headers={"Content-Type": "application/octet-stream"},
            )
            drop = await client.delete(f"/api/uploads/{uid}")
            refusals = [
                (again.status, await again.json()),
                (part.status, await part.json()),
                (drop.status, await drop.json()),
            ]
            swept = store.sweep(ttl_secs=-1)

            held.release()
            status, body = await first
            after = await client.delete(f"/api/uploads/{uid}")

    assert refusals == [
        (409, {"error": "upload is already being completed"}),
        (409, {"error": "upload is being completed"}),
        (
            409,
            {"error": {"code": "upload_completing", "message": refusals[2][1]["error"]["message"]}},
        ),
    ]
    assert "can no longer be cancelled" in refusals[2][1]["error"]["message"]
    assert swept == 0, "the sweep passed over the upload being completed"
    assert status == 200, body
    assert _items(gateway.store) == 1 and gateway.queued == [body["item_id"]]
    assert gateway.store.get_item(body["item_id"])["file_size"] == len(payload)
    assert after.status == 404, "once completed, there is nothing left to drop"


@pytest.mark.asyncio
async def test_an_upload_whose_content_cannot_be_checked_is_not_used(gateway):
    """A content scan that never ran is not a pass: when its child cannot start, the upload is
    refused (503), nothing is made from it, and its parts go. The same upload is kept when the
    child runs: the vacuity arm."""
    payload = _json_lines(_MB)
    async with TestClient(TestServer(gateway.app)) as client:

        async def upload() -> tuple[int, dict]:
            info = await _init(
                client,
                len(payload),
                target="attachment",
                filename="feed-export.jsonl",
                mime="application/x-ndjson",
            )
            await _send_parts(client, info, payload)
            return await _complete(client, info["uploadId"])

        with patch(
            "personalclaw.uploads.content_scan.scan_argv",
            return_value=["/nonexistent/personalclaw-content-scan"],
        ):
            refused, said = await upload()
        kept, body = await upload()

    assert refused == 503, said
    assert said["error"]["code"] == "upload_content_unchecked"
    assert "could not be checked" in said["error"]["message"]
    assert list(gateway.parts.iterdir()) == [], "its parts went"
    assert kept == 200, body
    assert [p.name for p in gateway.uploads.iterdir() if p.is_file()] == [
        os.path.basename(body["paths"][0])
    ], "only the checked upload was kept"
