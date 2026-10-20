"""What someone uploads is written 0600 in 0700 directories, as the shared writer writes a file.

🔴 A resumable upload's parts streamed to disk through a bare ``open``, and the file they were
assembled into was made the same way, so both kept the umask's mode (0644 on the usual umask), and
so did the directories they sat in, where every file the shared writer writes under the home is
0600. Each is now opened through ``atomic_write.open_streamed``, which gives it that mode.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from personalclaw.uploads.store import UploadStore

_MB = 1024 * 1024


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data, self.pos = data, 0

    async def read_chunk(self, n: int) -> bytes:
        chunk = self.data[self.pos : self.pos + n]
        self.pos += len(chunk)
        return chunk


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _store() -> UploadStore:
    from personalclaw.config.loader import config_dir

    return UploadStore(config_dir() / "uploads" / ".parts")


async def _upload(store: UploadStore, sid: str, payload: bytes, part_size: int) -> None:
    for index in range((len(payload) + part_size - 1) // part_size):
        chunk = payload[index * part_size : (index + 1) * part_size]
        await store.write_part(sid, index, _Reader(chunk))


@pytest.mark.asyncio
async def test_parts_and_the_file_they_make_are_0600(tmp_path):
    store = _store()
    payload = os.urandom(9 * _MB)
    sess = store.init(filename="clip.mp4", size=len(payload), mime="video/mp4", target="attachment")
    assert sess.append_mode is False, "separate parts, which this checks"
    await _upload(store, sess.id, payload, sess.part_size)
    session_dir = store.root / sess.id
    parts = sorted(session_dir.glob("part_*"))
    assert len(parts) == 2, "the fixture must stream more than one part"
    assert {_mode(p) for p in parts} == {0o600}
    final, _ = await store.assemble(sess.id)
    assert final.read_bytes() == payload
    assert _mode(final) == 0o600
    assert _mode(session_dir) == 0o700
    assert _mode(store.root) == 0o700


@pytest.mark.asyncio
async def test_the_one_file_an_upload_appends_into_is_0600():
    store = _store()
    payload = os.urandom(3 * _MB)
    # Room for the upload once but not twice (with the store's margin): the parts append.
    with patch("personalclaw.uploads.store._free_bytes", return_value=len(payload) + 257 * _MB):
        sess = store.init(
            filename="a.mp4", size=len(payload), mime="video/mp4", target="attachment"
        )
    assert sess.append_mode is True, "the file parts append into, which this checks"
    await _upload(store, sess.id, payload, sess.part_size)
    final, _ = await store.assemble(sess.id)
    assert final.read_bytes() == payload
    assert _mode(final) == 0o600


def test_a_file_opened_to_stream_is_tightened_if_it_was_looser():
    from personalclaw.atomic_write import open_streamed
    from personalclaw.config.loader import config_dir

    loose = config_dir() / "uploads" / "loose.bin"
    loose.parent.mkdir(parents=True, exist_ok=True)
    loose.write_bytes(b"before")
    loose.chmod(0o644)
    with open_streamed(loose, "r+b") as fh:
        fh.write(b"after!")
    assert loose.read_bytes() == b"after!"
    assert _mode(loose) == 0o600


def test_outside_the_home_a_streamed_file_keeps_the_umask_mode(tmp_path):
    from personalclaw.atomic_write import open_streamed

    target = tmp_path / "elsewhere" / "export.bin"
    with open_streamed(target, "wb") as fh:
        fh.write(b"x")
    umask = os.umask(0)
    os.umask(umask)
    assert _mode(target) == 0o666 & ~umask
