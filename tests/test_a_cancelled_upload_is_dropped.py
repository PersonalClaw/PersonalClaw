"""A cancelled upload is dropped at once, and nothing is made from it.

Measured: Knowledge › Add knowledge › Video, a 60 MB file over a throttled connection, Cancel
pressed at 14%. The form closed, but the browser went on sending the parts and then asked for the
upload to be completed, so the item was created with the form's title, processed, and its frames
were sent to a model. The gateway had no way to be told an upload was abandoned: its parts sat in
``uploads/.parts`` until the day-long sweep, and nothing refused a later complete.

``DELETE /api/uploads/{id}`` drops an upload. Its parts go at once, a part still streaming in for it
is not recorded, and a complete asked for afterwards is refused, so no item is made. A request whose
browser goes away before its body has all arrived (which is what a cancel does to the request in
flight) records nothing, leaves no partial file, and is logged as the ordinary event it is.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers import knowledge as kn_handlers
from personalclaw.dashboard.routes import _register_upload_routes
from personalclaw.knowledge.pipeline import ensure_nodes_registered
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.uploads.store import UploadError, UploadStore

_MB = 1024 * 1024


class _Reader:
    """An aiohttp body reader stand-in: hands out *data* in chunks, running *on_read* first."""

    def __init__(self, data: bytes, on_read=None, fail_after: int | None = None):
        self.data = data
        self.pos = 0
        self.on_read = on_read
        self.fail_after = fail_after

    async def read_chunk(self, n: int) -> bytes:
        if self.on_read is not None:
            self.on_read()
            self.on_read = None
        if self.fail_after is not None and self.pos >= self.fail_after:
            raise ConnectionResetError("Connection lost")
        chunk = self.data[self.pos : self.pos + n]
        self.pos += len(chunk)
        return chunk


def _append_mode_store(store: UploadStore, size: int):
    """Open an upload in append mode: free disk between one and two copies of the file."""
    with patch("personalclaw.uploads.store._free_bytes", return_value=size + 260 * _MB):
        sess = store.init(filename="clip.mp4", size=size, mime="video/mp4", target="knowledge")
    assert sess.append_mode is True
    return sess


# ── the store ────────────────────────────────────────────────────────────────────────────────


class TestDrop:
    @pytest.mark.asyncio
    async def test_dropping_an_upload_removes_its_parts_at_once(self, tmp_path):
        store = UploadStore(tmp_path)
        sess = store.init(filename="clip.mp4", size=20 * _MB, mime="video/mp4", target="knowledge")
        for i in range(2):
            await store.write_part(sess.id, i, _Reader(os.urandom(sess.part_size)))
        # The part that was arriving when the browser stopped sending.
        (tmp_path / sess.id / ".part_000002.tmp").write_bytes(b"\0" * _MB)

        assert store.drop(sess.id) is True

        assert not (tmp_path / sess.id).exists()
        with pytest.raises(UploadError) as ei:
            store.get(sess.id)
        assert ei.value.status == 404

    @pytest.mark.asyncio
    async def test_dropping_an_upload_written_in_place_removes_its_file(self, tmp_path):
        store = UploadStore(tmp_path)
        sess = _append_mode_store(store, 20 * _MB)
        await store.write_part(sess.id, 0, _Reader(os.urandom(sess.part_size)))

        assert store.drop(sess.id) is True

        assert not (tmp_path / sess.id).exists()

    def test_dropping_an_upload_that_is_not_there_says_so(self, tmp_path):
        store = UploadStore(tmp_path)
        sess = store.init(filename="clip.mp4", size=20 * _MB, mime="video/mp4", target="knowledge")

        assert store.drop("0" * 32) is False
        assert store.drop(sess.id) is True
        assert store.drop(sess.id) is False, "a second drop finds nothing to drop"

    def test_an_id_that_names_no_upload_removes_nothing(self, tmp_path):
        """A drop deletes a folder, so only an upload's own folder may go: an id that is not an
        upload's (nothing left once reduced to an id's characters, or the folder above) is
        refused, and every upload in progress stays."""
        root = tmp_path / ".parts"
        store = UploadStore(root)
        sess = store.init(filename="clip.mp4", size=20 * _MB, mime="video/mp4", target="knowledge")

        for not_an_upload in ("", "..", "./"):
            assert store.drop(not_an_upload) is False

        assert (root / sess.id / "meta.json").is_file()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("append_mode", [False, True], ids=["separate-parts", "in-place"])
    async def test_a_part_still_arriving_when_the_upload_is_dropped_does_not_bring_it_back(
        self, tmp_path, append_mode
    ):
        store = UploadStore(tmp_path)
        if append_mode:
            sess = _append_mode_store(store, 20 * _MB)
        else:
            sess = store.init(
                filename="clip.mp4", size=20 * _MB, mime="video/mp4", target="knowledge"
            )
        reader = _Reader(os.urandom(sess.part_size), on_read=lambda: store.drop(sess.id))

        with pytest.raises(UploadError) as ei:
            await store.write_part(sess.id, 0, reader)

        assert ei.value.status == 404
        assert not (tmp_path / sess.id).exists(), "the dropped upload was made again"

    @pytest.mark.asyncio
    async def test_a_part_whose_connection_closes_leaves_nothing_behind(self, tmp_path):
        store = UploadStore(tmp_path)
        sess = store.init(filename="clip.mp4", size=20 * _MB, mime="video/mp4", target="knowledge")
        await store.write_part(sess.id, 0, _Reader(os.urandom(sess.part_size)))
        cut = _Reader(os.urandom(sess.part_size), fail_after=2 * _MB)

        with pytest.raises(ConnectionResetError):
            await store.write_part(sess.id, 1, cut)

        assert sorted(p.name for p in (tmp_path / sess.id).iterdir()) == [
            "meta.json",
            "part_000000",
        ], "the part that did not arrive whole left a file behind"
        assert store.get(sess.id).received == [0], "it was not recorded"


class TestSweep:
    """The day-long sweep is what drops an upload nobody drops: a tab closed mid-upload."""

    @pytest.mark.asyncio
    async def test_the_sweep_drops_an_abandoned_upload_and_its_parts(self, tmp_path):
        store = UploadStore(tmp_path)
        sess = store.init(filename="clip.mp4", size=20 * _MB, mime="video/mp4", target="knowledge")
        await store.write_part(sess.id, 0, _Reader(os.urandom(sess.part_size)))
        (tmp_path / sess.id / ".part_000001.tmp").write_bytes(b"\0" * _MB)
        in_place = _append_mode_store(store, 20 * _MB)
        await store.write_part(in_place.id, 0, _Reader(os.urandom(in_place.part_size)))
        for sid in (sess.id, in_place.id):
            os.utime(tmp_path / sid / "meta.json", (1.0, 1.0))

        assert store.sweep(ttl_secs=60) == 2

        assert not (tmp_path / sess.id).exists()
        assert not (tmp_path / in_place.id).exists()

    @pytest.mark.asyncio
    async def test_the_sweep_keeps_an_upload_still_receiving_parts(self, tmp_path):
        store = UploadStore(tmp_path)
        sess = store.init(filename="clip.mp4", size=20 * _MB, mime="video/mp4", target="knowledge")
        await store.write_part(sess.id, 0, _Reader(os.urandom(sess.part_size)))

        assert store.sweep(ttl_secs=3600) == 0

        assert (tmp_path / sess.id / "part_000000").is_file()


# ── the routes ───────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def gateway(tmp_path, monkeypatch):
    """The real upload routes plus the knowledge ingest route, over a knowledge store in tmp."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(tempfile, "tempdir", str(_mkdir(tmp_path / "tmp")))
    ensure_nodes_registered()
    queued: list[str] = []
    finished = asyncio.Event()

    @web.middleware
    async def _note_finished(request, handler):
        try:
            return await handler(request)
        finally:
            finished.set()

    app = web.Application(middlewares=[_note_finished])
    app["upload_store"] = UploadStore(tmp_path / ".parts")
    app["state"] = SimpleNamespace(
        knowledge_store=KnowledgeStore(str(tmp_path / "knowledge.db")),
        knowledge_ingest_queue=lambda: SimpleNamespace(enqueue=queued.append),
    )
    _register_upload_routes(app)
    app.router.add_post("/api/knowledge/ingest", kn_handlers.ingest_file)
    return SimpleNamespace(
        app=app,
        parts=tmp_path / ".parts",
        tmp=tmp_path / "tmp",
        store=app["state"].knowledge_store,
        queued=queued,
        finished=finished,
    )


def _mkdir(path):
    path.mkdir(parents=True, exist_ok=True)
    return path


class _GatewayServer(TestServer):
    """A test server whose handler goes on running when its client goes away, as the gateway's
    own runner does (``dashboard/server.py`` leaves aiohttp's ``handler_cancellation`` off).
    aiohttp's test server turns it on, which cancels the handler instead: a different event
    from the one a cancelled upload is in production."""

    async def _make_runner(self, **kwargs):
        kwargs["handler_cancellation"] = False
        return await super()._make_runner(**kwargs)


async def _init(client, size: int) -> dict:
    r = await client.post(
        "/api/uploads/init",
        json={
            "filename": "walkthrough.mov",
            "size": size,
            "mime": "video/quicktime",
            "target": "knowledge",
        },
    )
    assert r.status == 200, await r.text()
    return await r.json()


async def _put(client, uid: str, index: int, data: bytes):
    return await client.put(
        f"/api/uploads/{uid}/part?index={index}",
        data=data,
        headers={"Content-Type": "application/octet-stream"},
    )


def _items(store: KnowledgeStore) -> int:
    return store.db.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"]


async def _send_and_hang_up(client, head: str, body: bytes) -> None:
    """Send a request whose body stops short, then close the connection, as a cancel does."""
    reader, writer = await asyncio.open_connection(client.server.host, client.server.port)
    writer.write(head.encode() + body)
    await writer.drain()
    await asyncio.sleep(0.1)
    writer.close()
    await writer.wait_closed()


class TestDropRoute:
    @pytest.mark.asyncio
    async def test_a_dropped_upload_is_gone_and_a_later_complete_makes_no_item(self, gateway):
        payload = os.urandom(20 * _MB)
        async with TestClient(_GatewayServer(gateway.app)) as client:
            info = await _init(client, len(payload))
            uid, part = info["uploadId"], info["partSize"]
            assert (await _put(client, uid, 0, payload[:part])).status == 200
            assert (gateway.parts / uid).is_dir()

            r = await client.delete(f"/api/uploads/{uid}")

            assert r.status == 200
            assert await r.json() == {"uploadId": uid, "dropped": True}
            assert not (gateway.parts / uid).exists(), "its parts are still on disk"
            assert (await client.get(f"/api/uploads/{uid}")).status == 404
            assert (await _put(client, uid, 1, payload[part : 2 * part])).status == 404
            assert (await client.post(f"/api/uploads/{uid}/complete", json={})).status == 404
            assert _items(gateway.store) == 0
            assert gateway.queued == [], "nothing was sent for processing"

    @pytest.mark.asyncio
    async def test_the_same_upload_left_alone_is_made_into_an_item(self, gateway):
        """The vacuity arm: these routes over this store DO make an item from a finished
        upload, so the drop's 'no item' above is the drop's doing."""
        payload = os.urandom(20 * _MB)
        async with TestClient(_GatewayServer(gateway.app)) as client:
            info = await _init(client, len(payload))
            uid, part = info["uploadId"], info["partSize"]
            for i in range(info["totalParts"]):
                assert (
                    await _put(client, uid, i, payload[i * part : (i + 1) * part])
                ).status == 200

            r = await client.post(f"/api/uploads/{uid}/complete", json={})

            assert r.status == 200, await r.text()
            assert _items(gateway.store) == 1
            assert gateway.queued == [(await r.json())["item_id"]]

    @pytest.mark.asyncio
    async def test_dropping_an_upload_that_is_not_there_answers_with_a_code(self, gateway):
        async with TestClient(_GatewayServer(gateway.app)) as client:
            r = await client.delete(f"/api/uploads/{'0' * 32}")

            assert r.status == 404
            assert (await r.json())["error"]["code"] == "upload_not_found"


class TestABrowserThatGoesAway:
    @pytest.mark.asyncio
    async def test_a_part_cut_off_mid_body_records_nothing_and_logs_no_error(self, gateway, caplog):
        caplog.set_level(logging.INFO)
        async with TestClient(_GatewayServer(gateway.app)) as client:
            info = await _init(client, 20 * _MB)
            uid = info["uploadId"]
            gateway.finished.clear()
            head = (
                f"PUT /api/uploads/{uid}/part?index=0 HTTP/1.1\r\nHost: gateway.test\r\n"
                "Content-Type: application/octet-stream\r\n"
                f"Content-Length: {info['partSize']}\r\n\r\n"
            )

            await _send_and_hang_up(client, head, b"\0" * _MB)
            await asyncio.wait_for(gateway.finished.wait(), 10)

            status = await (await client.get(f"/api/uploads/{uid}")).json()
        assert status["received"] == []
        assert sorted(p.name for p in (gateway.parts / uid).iterdir()) == ["meta.json"]
        assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []

    @pytest.mark.asyncio
    async def test_a_knowledge_file_cut_off_mid_body_makes_no_item_and_logs_no_error(
        self, gateway, caplog
    ):
        caplog.set_level(logging.INFO)
        boundary = "pcformboundary"
        preamble = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="walkthrough.mov"\r\n'
            "Content-Type: video/quicktime\r\n\r\n"
        ).encode()
        length = len(preamble) + 8 * _MB + len(f"\r\n--{boundary}--\r\n")
        head = (
            "POST /api/knowledge/ingest HTTP/1.1\r\nHost: gateway.test\r\n"
            f"Content-Type: multipart/form-data; boundary={boundary}\r\n"
            f"Content-Length: {length}\r\n\r\n"
        )
        async with TestClient(_GatewayServer(gateway.app)) as client:
            gateway.finished.clear()

            await _send_and_hang_up(client, head, preamble + b"\0" * _MB)
            await asyncio.wait_for(gateway.finished.wait(), 10)

        assert _items(gateway.store) == 0
        assert gateway.queued == []
        assert list(gateway.tmp.iterdir()) == [], "the partial file was left behind"
        assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []
