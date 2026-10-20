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

The threshold, for each case below: while the upload lands, no single step of the event loop
(one callback it runs, during which it runs nothing else) takes :data:`_BOUND` (0.1 s), and every
health check asked meanwhile answers within it. A step is timed as asyncio's debug mode times a
slow callback: a blocking call on the loop is inside one, a wait on the disk included, while time
the loop spends idle in its selector, given CPU or not, is between steps (:class:`_Probe`).
"""

from __future__ import annotations

import asyncio
import gc
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from table_rows import row

from personalclaw.dashboard.handlers import knowledge as kn_handlers
from personalclaw.dashboard.handlers_system import api_healthz
from personalclaw.dashboard.routes import _register_upload_routes
from personalclaw.knowledge.pipeline import ensure_nodes_registered
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.uploads.store import UploadStore

_MB = 1024 * 1024
#: The longest one step of the event loop may take, and a health check, while it lands.
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
    """Beside some work: a health check asked again 10 ms after the last one answered, and the
    time each step of the event loop takes.

    A step is one callback the loop runs (``Handle._run``: a task resumed up to its next
    ``await``, a timer, an I/O callback), timed as asyncio's debug mode times it for its
    ``slow_callback_duration`` warning. While a step runs the loop runs nothing else, so the
    longest step (:attr:`worst_step`) is the longest the loop was blocked: a blocking call on it,
    a wait on the disk included, is inside a step however little CPU it uses. Time the loop spends
    waiting in its selector for something to do is between steps, so a process given no CPU while
    it waits there is not counted as blocked.

    A 10 ms tick is kept beside it, and its longest gap (:attr:`worst_gap`) is reported: that gap
    is wall time between two turns, the selector's wait included, which is why it is not the
    measure."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.worst_step = 0.0
        self.worst_step_was = ""
        self.worst_gap = 0.0
        self.slowest_answer = 0.0
        self.answered_during = 0

    def _timed(self, loop_thread: int, real):
        probe = self

        def run(handle):
            if threading.get_ident() != loop_thread:
                return real(handle)
            began = time.monotonic()
            try:
                return real(handle)
            finally:
                took = time.monotonic() - began
                if took > probe.worst_step:
                    probe.worst_step = took
                    probe.worst_step_was = repr(handle)[:200]

        return run

    async def during(self, work):
        with patch.object(
            asyncio.events.Handle,
            "_run",
            self._timed(threading.get_ident(), asyncio.events.Handle._run),
        ):
            return await self._during(work)

    async def _during(self, work):
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
            f"the work took {self.took:.2f}s; the event loop's longest step was "
            f"{self.worst_step:.3f}s ({self.worst_step_was}); its longest gap between turns "
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
    assert probe.worst_step < _BOUND, probe.says()
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
    assert probe.worst_step < _BOUND, probe.says()
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
    assert probe.worst_step < _BOUND, probe.says()
    assert probe.slowest_answer < _BOUND, probe.says()
    assert probe.answered_during >= 1, f"premise: the gateway was asked meanwhile; {probe.says()}"


# ── what the upload becomes is decided off the loop ─────────────────────────────────────────

#: How long :class:`_HeldInsert` holds the insert when nothing lets it go.
_HOLD = 2.0


#: The store's calls that decide what an upload becomes, in the order they run.
_DECIDING_CALLS = ("find_active_by_file_hash", "create_typed_item", "get_item")


class _HeldInsert:
    """Records the thread each of :data:`_DECIDING_CALLS` runs on, and holds the insert of a new
    item, wherever it runs, until :meth:`release` or for :data:`_HOLD` seconds. Held on the event
    loop it stops every request for the whole hold; held in a worker thread, the loop answers
    meanwhile."""

    def __init__(self, store: KnowledgeStore) -> None:
        self.entered = threading.Event()
        self.released = threading.Event()
        self.threads: dict[str, list[int]] = {name: [] for name in _DECIDING_CALLS}
        self.timed_out = False
        self._patches = [
            patch.object(store, name, self._recorded(name, getattr(store, name)))
            for name in _DECIDING_CALLS
        ]

    def _recorded(self, name, real):
        def call(*args, **kwargs):
            self.threads[name].append(threading.get_ident())
            if name == "create_typed_item":
                self.entered.set()
                self.timed_out = not self.released.wait(_HOLD)
            return real(*args, **kwargs)

        return call

    def __enter__(self) -> "_HeldInsert":
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc) -> None:
        for p in reversed(self._patches):
            p.stop()

    def release(self) -> None:
        self.released.set()


@pytest.mark.asyncio
async def test_the_item_is_made_while_the_gateway_answers(gateway):
    """🔴 Red before: the duplicate check, the insert and the read of the new item ran on the
    event loop, so a commit or a read waiting on a busy disk held every request up (0.2 s and
    0.32 s measured). All three run in one worker thread, the same one; held open there, the
    insert lets a health check be answered before it is let go, and the complete still makes its
    one item."""
    loop_thread = threading.get_ident()
    payload = _bytes(4 * _MB)
    with _HeldInsert(gateway.store) as held:
        async with TestClient(TestServer(gateway.app)) as client:
            info = await _init(
                client, len(payload), target="knowledge", filename="clip.mp4", mime="video/mp4"
            )
            await _send_parts(client, info, payload)
            first = asyncio.ensure_future(_complete(client, info["uploadId"]))
            assert await asyncio.to_thread(held.entered.wait, 30), "the insert never began"
            health = await client.get("/api/healthz")
            answered_while_held = health.status == 200 and not held.released.is_set()
            held.release()
            status, body = await first

    ran_on = {name: threads for name, threads in held.threads.items()}
    assert all(
        len(threads) == 1 for threads in ran_on.values()
    ), f"premise: each ran once; {ran_on}"
    on_loop = [name for name, (thread,) in ran_on.items() if thread == loop_thread]
    assert not on_loop, f"what decides the item ran on the event loop's thread: {on_loop}"
    assert (
        len({thread for (thread,) in ran_on.values()}) == 1
    ), f"what decides the item did not run in one worker call: {ran_on}"
    assert not held.timed_out, "nothing was answered until the held insert gave up waiting"
    assert answered_while_held, "the gateway did not answer while the insert was held"
    assert status == 200, body
    assert _items(gateway.store) == 1 and gateway.queued == [body["item_id"]]


@pytest.mark.asyncio
async def test_two_uploads_of_the_same_bytes_finishing_together_make_one_item(gateway, tmp_path):
    """Off the loop, the duplicate check and the insert are still one step under the store's
    lock: two uploads of the same bytes stored at once make one item, and one is told so."""
    from personalclaw.knowledge.file_items import store_file_item

    payload = _bytes(_MB)
    first, second = tmp_path / "a.mov", tmp_path / "b.mov"
    first.write_bytes(payload)
    second.write_bytes(payload)

    made = await asyncio.gather(
        store_file_item(gateway.store, str(first), "a.mov", mime="video/quicktime"),
        store_file_item(gateway.store, str(second), "b.mov", mime="video/quicktime"),
    )

    assert sorted(is_new for _item, is_new in made) == [False, True]
    assert made[0][0]["id"] == made[1][0]["id"]
    assert _items(gateway.store) == 1


def test_an_item_decided_while_another_change_is_open_waits_for_it(gateway):
    """🔴 Red before: the store's changes held its connection's lock one statement at a time, so
    an upload decided in a worker thread while this thread's edit was open ran inside it: its
    insert failed with "cannot start a transaction within a transaction", the upload answered 500
    and its stored file was left without an item. A change now holds the lock from its start to
    its end, and the upload is decided after it."""
    from personalclaw.knowledge import file_items

    store = gateway.store
    edited = store.create_typed_item(item_type="note", title="draft", content="first")
    item = {"item_type": "note", "title": "clip", "content": "", "extra": {}}
    decided: dict = {}

    def decide() -> None:
        try:
            decided["made"] = file_items._decide(store, "", item)
        except Exception as exc:  # what the upload's request would have answered 500 with
            decided["error"] = exc

    worker = threading.Thread(target=decide)
    real_write_tags = store._write_item_tags
    opened = threading.Event()

    def write_tags_then_decide(*args, **kwargs):
        # Inside the edit's transaction, on this thread: the upload is decided meanwhile.
        if not opened.is_set():
            opened.set()
            worker.start()
            worker.join(1.0)
        return real_write_tags(*args, **kwargs)

    with patch.object(store, "_write_item_tags", write_tags_then_decide):
        store.update_item(edited, content="second", tags=["kept"])
    worker.join(30)

    assert opened.is_set(), "premise: the edit's transaction was open while the upload was decided"
    assert not worker.is_alive(), "the upload was never decided"
    assert "error" not in decided, f"the upload was decided inside the edit: {decided['error']!r}"
    made, is_new = decided["made"]
    assert is_new and made and made["title"] == "clip"
    assert store.get_item(edited)["content"] == "second", "the edit was not stored"
    assert _items(store) == 2


@pytest.mark.asyncio
async def test_two_stores_over_one_library_make_one_item_of_the_same_bytes(gateway, tmp_path):
    """🔴 Red before: the duplicate check and the insert were one step under ONE store's lock, and
    the process can hold two stores over one library (the vault's sweep opens its own). Two
    uploads of the same bytes decided on the two at once made two items. Deciding is one upload
    at a time in the process, so the second finds the first's item."""
    from personalclaw.knowledge.file_items import store_file_item

    other = KnowledgeStore(gateway.store.db_path)
    payload = _bytes(_MB)
    first, second = tmp_path / "a.mov", tmp_path / "b.mov"
    first.write_bytes(payload)
    second.write_bytes(payload)
    try:
        made = await asyncio.gather(
            store_file_item(gateway.store, str(first), "a.mov", mime="video/quicktime"),
            store_file_item(other, str(second), "b.mov", mime="video/quicktime"),
        )
    finally:
        other.close()

    assert sorted(is_new for _item, is_new in made) == [False, True]
    assert made[0][0]["id"] == made[1][0]["id"]
    assert _items(gateway.store) == 1


def test_the_module_that_makes_the_item_loads_with_the_upload_handlers():
    """🔴 Red before: a complete imported it on the first Knowledge upload, and a module's first
    import runs on the event loop (0.13-0.44 s measured on a busy machine). It is imported with
    the handlers, which the gateway loads when it registers the routes."""
    from personalclaw.dashboard.handlers import uploads
    from personalclaw.knowledge import file_items

    assert getattr(uploads, "store_file_item", None) is file_items.store_file_item


# ── what the probe counts as the loop being blocked ─────────────────────────────────────────

#: How long each planted block below holds the event loop, and how long the process is stopped.
_PLANTED = 0.2
_STOPPED = 0.25


def _block_by_sleeping(_tmp_path) -> None:
    time.sleep(_PLANTED)


def _block_by_writing(tmp_path) -> None:
    """A write that waits, as a write to a busy disk does, using no CPU meanwhile: 1 MB into a
    pipe whose reader starts draining it only after :data:`_PLANTED`."""
    fifo = tmp_path / "slow-disk"
    os.mkfifo(fifo)

    def drain() -> None:
        with open(fifo, "rb") as reader:
            time.sleep(_PLANTED)
            while reader.read(1 << 16):
                pass

    drainer = threading.Thread(target=drain)
    drainer.start()
    with open(fifo, "wb") as writer:
        writer.write(b"\0" * _MB)
    drainer.join()


_PLANTED_BLOCKS = {
    "a blocking sleep": _block_by_sleeping,
    "a blocking file write": _block_by_writing,
}


def _planted_app(block, tmp_path) -> web.Application:
    async def planted(_request: web.Request) -> web.Response:
        block(tmp_path)  # on the event loop, in the request
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/api/healthz", api_healthz)
    app.router.add_get("/planted", planted)
    return app


@pytest.mark.asyncio
async def test_a_blocking_call_on_the_loop_fails_the_probe(subtests, tmp_path):
    """The vacuity arm: a request that blocks the loop for 0.2 s, with a sleep or with a write
    that waits as a busy disk makes it wait, is a step over the bound. The tick's gap, which the
    probe measured before, catches it too, so the step catches everything that measure did."""
    assert len(_PLANTED_BLOCKS) == 2
    for name, block in _PLANTED_BLOCKS.items():
        with row(subtests, block=name):
            where = tmp_path / name.replace(" ", "-")
            where.mkdir()
            async with TestClient(TestServer(_planted_app(block, where))) as client:
                probe = _Probe(client)

                async def ask_planted():
                    r = await client.get("/planted")
                    return r.status

                status = await probe.during(ask_planted())

            assert status == 200, f"{name}: premise: the planted request answered"
            assert probe.worst_step >= _BOUND, f"{name}: the step missed it; {probe.says()}"
            assert probe.worst_gap >= _BOUND, f"{name}: premise: the tick saw it; {probe.says()}"


class _StoppedWhileIdle:
    """Stops this process from outside (``SIGSTOP``, then ``SIGCONT`` after :data:`_STOPPED`)
    once, while the event loop waits in its selector: a process the machine gives no CPU while
    it has nothing to do. The loop's next selector wait hands a helper process the word, and
    stays there until the helper has stopped and continued it, so the stop lands in that wait
    and in no step."""

    _HELPER = textwrap.dedent("""
        import os, signal, sys, time
        pid, stopped = int(sys.argv[1]), float(sys.argv[2])
        if sys.stdin.buffer.read(1) == b"s":
            os.kill(pid, signal.SIGSTOP)
            time.sleep(stopped)
            os.kill(pid, signal.SIGCONT)
        sys.stdout.buffer.write(b"c")
        sys.stdout.buffer.flush()
        """)

    def __init__(self) -> None:
        self.stopped = False
        self._helper = subprocess.Popen(
            [sys.executable, "-c", self._HELPER, str(os.getpid()), str(_STOPPED)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

    def selector(self, real):
        def select(timeout=None):
            if not self.stopped:
                self.stopped = True
                self._helper.stdin.write(b"s")
                self._helper.stdin.flush()
                assert self._helper.stdout.read(1) == b"c", "the helper never continued us"
            return real(timeout)

        return select

    def close(self) -> None:
        if self._helper.poll() is None:
            self._helper.stdin.close()
        self._helper.wait(10)


@pytest.mark.asyncio
async def test_a_process_stopped_while_the_loop_is_idle_does_not_fail_the_probe(tmp_path):
    """🔴 Red before (the tick's gap was the measure): stopped for 0.25 s while it had nothing to
    do, the process went 0.25 s without a turn, and the probe called that a blocked loop. No step
    ran meanwhile, so the step measure passes it, as it does a runner that gives the process no
    core while it waits."""
    async with TestClient(TestServer(_planted_app(_block_by_sleeping, tmp_path))) as client:
        probe = _Probe(client)
        stop = _StoppedWhileIdle()
        selector = asyncio.get_running_loop()._selector  # type: ignore[attr-defined]
        try:

            async def idle():
                await asyncio.sleep(0.05)
                with patch.object(selector, "select", stop.selector(selector.select)):
                    await asyncio.sleep(_STOPPED + 0.1)

            await probe.during(idle())
        finally:
            stop.close()

    assert stop.stopped, "premise: the process was stopped"
    assert probe.worst_gap >= _STOPPED, f"premise: the tick counted the stop; {probe.says()}"
    assert probe.worst_step < _BOUND, f"the stop was counted as a step; {probe.says()}"


# ── what the upload's requests read and write on disk, they do off the loop ─────────────────


def _recorder(ran: list[int], real):
    """*real*, recording the thread of each call into *ran*."""

    def call(*args, **kwargs):
        ran.append(threading.get_ident())
        return real(*args, **kwargs)

    return call


@pytest.mark.asyncio
async def test_the_health_check_reads_the_disk_in_a_worker_thread(gateway, monkeypatch):
    """🔴 Red before: the health check resolved the home and the folder the code is served from
    on the event loop. Each walks the file system and waits on the disk (0.13 s caught on a busy
    machine, in the very check this file asks while an upload lands). Both are read in a worker
    thread."""
    from personalclaw.config import loader as config_loader
    from personalclaw.dashboard import handlers_system

    loop_thread = threading.get_ident()
    ran: dict[str, list[int]] = {"resolve_config_dir": [], "_serving_root": []}
    async with TestClient(TestServer(gateway.app)) as client:
        monkeypatch.setattr(
            config_loader,
            "resolve_config_dir",
            _recorder(ran["resolve_config_dir"], config_loader.resolve_config_dir),
        )
        monkeypatch.setattr(
            handlers_system,
            "_serving_root",
            _recorder(ran["_serving_root"], handlers_system._serving_root),
        )
        r = await client.get("/api/healthz")
        body = await r.json()

    assert r.status == 200 and body["status"] == "ok", body
    assert body["root_ok"] is True and body["home_id"], f"premise: it still answers whose; {body}"
    assert all(ran.values()), f"premise: each ran; {ran}"
    on_loop = [name for name, threads in ran.items() if loop_thread in threads]
    assert not on_loop, f"the health check read the disk on the event loop's thread: {on_loop}"


@pytest.mark.asyncio
async def test_a_parts_record_is_saved_in_a_worker_thread(gateway, monkeypatch):
    """🔴 Red before: each part's PUT saved the upload's record (``meta.json``) on the event loop:
    a temporary file written, its mode set and renamed over the record, each waiting on the disk
    (0.31 s caught on a busy machine). It is saved in a worker thread."""
    from personalclaw.uploads import store as upload_store

    loop_thread = threading.get_ident()
    saved: list[int] = []
    payload = _bytes(10 * _MB)  # two parts
    async with TestClient(TestServer(gateway.app)) as client:
        info = await _init(
            client, len(payload), target="knowledge", filename="clip.mp4", mime="video/mp4"
        )
        monkeypatch.setattr(
            upload_store, "atomic_json_write", _recorder(saved, upload_store.atomic_json_write)
        )
        await _send_parts(client, info, payload)
        r = await client.get(f"/api/uploads/{info['uploadId']}")
        status = await r.json()

    assert status["received"] == [0, 1], f"premise: both parts were recorded; {status}"
    assert len(saved) == 2, f"premise: one save of the record per part; {saved}"
    assert loop_thread not in saved, "a part's record was saved on the event loop's thread"


class _Body:
    """A part's body, handed out a chunk at a time, as aiohttp's reader hands it."""

    def __init__(self, data: bytes) -> None:
        self._data = memoryview(data)

    async def read_chunk(self, n: int) -> bytes:
        chunk, self._data = bytes(self._data[:n]), self._data[n:]
        return chunk


@pytest.mark.asyncio
async def test_a_drop_while_a_parts_record_is_saved_does_not_bring_the_upload_back(
    tmp_path, monkeypatch
):
    """Saved in a worker thread, the record could be written after a drop had taken the upload's
    folder away, and writing it makes the folder again: the dropped upload came back. The save
    and the drop's rename take one lock, so a drop asked meanwhile waits for the save, then drops
    the upload whole."""
    from personalclaw.uploads import store as upload_store

    store = UploadStore(tmp_path)
    sess = store.init(filename="clip.mp4", size=20 * _MB, mime="video/mp4", target="knowledge")
    real = upload_store.atomic_json_write
    entered, go = threading.Event(), threading.Event()

    def held(path, data):
        entered.set()
        assert go.wait(5), "the test never let the save go on"
        return real(path, data)

    monkeypatch.setattr(upload_store, "atomic_json_write", held)
    writing = asyncio.ensure_future(store.write_part(sess.id, 0, _Body(_bytes(sess.part_size))))
    assert await asyncio.to_thread(entered.wait, 5), "the record's save never began"
    dropping = asyncio.ensure_future(store.drop(sess.id))
    await asyncio.sleep(0.2)  # the drop is asked while the record is being saved
    go.set()
    written, dropped = await asyncio.gather(writing, dropping, return_exceptions=True)

    assert not isinstance(written, BaseException), f"the part was not written: {written!r}"
    assert dropped is True, f"the drop did not drop the upload: {dropped!r}"
    assert not (tmp_path / sess.id).exists(), "the dropped upload was made again"


@pytest.mark.asyncio
async def test_the_librarys_files_folder_is_made_in_a_worker_thread(gateway, monkeypatch):
    """🔴 Red before: an upload asked for the library's files folder on the event loop, which
    resolves the home and makes the folder when it is missing, each waiting on the disk. It is
    asked in a worker thread."""
    import personalclaw.knowledge as knowledge

    loop_thread = threading.get_ident()
    ran: list[int] = []
    monkeypatch.setattr(
        knowledge, "knowledge_files_dir", _recorder(ran, knowledge.knowledge_files_dir)
    )
    payload = _bytes(_MB)
    boundary = "pcuploadboundary"
    async with TestClient(TestServer(gateway.app)) as client:
        r = await client.post(
            "/api/knowledge/ingest",
            data=_multipart("walkthrough.mov", "video/quicktime", payload, boundary),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        body = await r.json()

    assert r.status == 200, body
    assert gateway.queued == [body["item_id"]], "premise: the upload became an item"
    assert ran, "premise: the upload asked for the files folder"
    assert loop_thread not in ran, "the files folder was made on the event loop's thread"


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
