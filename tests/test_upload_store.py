"""Tests for the resumable upload store (personalclaw.uploads.store)."""

import os
from unittest.mock import patch

import pytest

from personalclaw.uploads.store import UploadError, UploadStore

_MB = 1024 * 1024
_GB = 1024**3


class _FakeReader:
    """Mimics aiohttp BodyPartReader/StreamReader: async read_chunk(n)."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    async def read_chunk(self, n: int) -> bytes:
        chunk = self.data[self.pos : self.pos + n]
        self.pos += len(chunk)
        return chunk


async def _upload_all(store: UploadStore, sid: str, payload: bytes, part_size: int, order=None):
    total = max(1, (len(payload) + part_size - 1) // part_size)
    indices = order if order is not None else list(range(total))
    for idx in indices:
        start = idx * part_size
        chunk = payload[start : start + part_size]
        await store.write_part(sid, idx, _FakeReader(chunk))


class TestRoundTrip:
    @pytest.mark.asyncio
    async def test_separate_parts_roundtrip(self, tmp_path):
        store = UploadStore(tmp_path)
        size = 20 * _MB
        payload = os.urandom(size)
        sess = store.init(filename="clip.mp4", size=size, mime="video/mp4", target="attachment")
        assert sess.category == "video"
        assert sess.append_mode is False  # ample disk in tmp
        # out-of-order + idempotent re-PUT
        await _upload_all(store, sess.id, payload, sess.part_size, order=[2, 0, 1, 1])
        assert store.is_complete(store.get(sess.id))
        final, _ = await store.assemble(sess.id)
        assert final.read_bytes() == payload
        store.cleanup(sess.id)
        assert not (tmp_path / sess.id).exists()

    @pytest.mark.asyncio
    async def test_append_mode_roundtrip(self, tmp_path):
        store = UploadStore(tmp_path)
        size = 20 * _MB
        payload = os.urandom(size)
        # Force append mode: free between 1x+margin and 2x+margin.
        with patch("personalclaw.uploads.store._free_bytes", return_value=size + 260 * _MB):
            sess = store.init(filename="a.mp4", size=size, mime="video/mp4", target="attachment")
        assert sess.append_mode is True
        await _upload_all(store, sess.id, payload, sess.part_size, order=[0, 2, 1])
        final, _ = await store.assemble(sess.id)
        assert final.read_bytes() == payload

    @pytest.mark.asyncio
    async def test_single_part_file(self, tmp_path):
        store = UploadStore(tmp_path)
        size = 1 * _MB  # < one part
        payload = os.urandom(size)
        sess = store.init(filename="s.png", size=size, mime="image/png", target="knowledge")
        assert sess.total_parts == 1
        await store.write_part(sess.id, 0, _FakeReader(payload))
        final, _ = await store.assemble(sess.id)
        assert final.read_bytes() == payload


class TestInitValidation:
    def test_reject_too_big(self, tmp_path):
        store = UploadStore(tmp_path)
        with pytest.raises(UploadError) as ei:
            store.init(filename="huge.mp4", size=3 * _GB, mime="video/mp4", target="attachment")
        assert ei.value.status == 413 and "2 GB" in ei.value.message

    def test_reject_no_disk(self, tmp_path):
        store = UploadStore(tmp_path)
        with patch("personalclaw.uploads.store._free_bytes", return_value=1024):
            with pytest.raises(UploadError) as ei:
                store.init(filename="a.mp4", size=20 * _MB, mime="video/mp4", target="attachment")
        assert ei.value.status == 507

    def test_reject_nonpositive_size(self, tmp_path):
        store = UploadStore(tmp_path)
        with pytest.raises(UploadError):
            store.init(filename="a.mp4", size=0, mime="video/mp4", target="attachment")


class TestPartErrors:
    @pytest.mark.asyncio
    async def test_part_out_of_range(self, tmp_path):
        store = UploadStore(tmp_path)
        sess = store.init(filename="a.mp4", size=20 * _MB, mime="video/mp4", target="attachment")
        with pytest.raises(UploadError):
            await store.write_part(sess.id, 999, _FakeReader(b"x"))

    @pytest.mark.asyncio
    async def test_assemble_incomplete(self, tmp_path):
        store = UploadStore(tmp_path)
        size = 20 * _MB
        payload = os.urandom(size)
        sess = store.init(filename="a.mp4", size=size, mime="video/mp4", target="attachment")
        # only part 0
        await store.write_part(sess.id, 0, _FakeReader(payload[: sess.part_size]))
        with pytest.raises(UploadError) as ei:
            await store.assemble(sess.id)
        assert ei.value.status == 409

    @pytest.mark.asyncio
    async def test_status_unknown_session(self, tmp_path):
        store = UploadStore(tmp_path)
        with pytest.raises(UploadError) as ei:
            store.get("deadbeef")
        assert ei.value.status == 404


class TestSweep:
    def test_sweep_removes_stale(self, tmp_path):
        store = UploadStore(tmp_path)
        sess = store.init(filename="a.mp4", size=20 * _MB, mime="video/mp4", target="attachment")
        d = tmp_path / sess.id
        assert d.exists()
        # Backdate the meta mtime well past the TTL.
        old = 1.0
        os.utime(d / "meta.json", (old, old))
        swept = store.sweep(ttl_secs=60)
        assert swept == 1 and not d.exists()

    def test_sweep_keeps_fresh(self, tmp_path):
        store = UploadStore(tmp_path)
        sess = store.init(filename="a.mp4", size=20 * _MB, mime="video/mp4", target="attachment")
        swept = store.sweep(ttl_secs=3600)
        assert swept == 0 and (tmp_path / sess.id).exists()
