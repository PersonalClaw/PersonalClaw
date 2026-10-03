"""Resumable upload store — the ``init / part / status / complete`` protocol.

A single 2 GB POST is fragile (browser memory, proxy timeout, no resume). This is
a minimal tus-like protocol: the client declares the file up front (``init``,
validated against the size policy before a byte is sent), streams fixed-size parts
to disk (``part``, idempotent → the resume primitive), can ask what landed
(``status``), then ``complete`` assembles the parts into one file and hands it to
the destination handler (chat attach / knowledge / workspace) exactly as the
single-POST path does.

Bytes never sit in memory: each part streams chunk-by-chunk to disk, and assembly
copies part→final in bounded chunks. Every file here is written through
``atomic_write.open_streamed``, 0600 in 0700 directories under the home, as the shared writer
writes a file there: what someone uploads is theirs alone. Disk strategy is adaptive (see
:meth:`UploadStore.init`): with ≥2× headroom parts are separate files concatenated
at complete (robust resume — each part independently re-PUTtable); when tighter,
parts append into one growing final file (~1× disk). A session the client gives up
(a cancelled upload) is dropped at once (:meth:`UploadStore.drop`); one nobody drops
(a tab closed mid-upload) is swept by TTL.

Work whose cost grows with the file runs in a worker thread, never on the event loop: on the
loop, assembling a 512 MB upload stopped every other request for up to 0.34 s, and a 2 GB one for
as long as it takes to copy 2 GB. File reads and writes leave the interpreter lock while they
wait on the disk, so in a thread the loop goes on answering (measured: no gap longer than its
own 10 ms tick). That lets other requests run while an upload is completed, so for as long as
its complete runs (:meth:`UploadStore.completing`) no other request may touch it: a second
complete, a late part and a drop are refused, and the sweep passes it over.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

from personalclaw.atomic_write import atomic_json_write, ensure_private_dir, open_streamed
from personalclaw.local_models.fit import free_disk_bytes
from personalclaw.uploads.policy import check_upload
from personalclaw.uploads.spool import Spool

# One part = this many bytes. 8 MB balances request count vs per-request overhead;
# a 2 GB upload is ~256 parts. The client is told this in the init response.
PART_SIZE = 8 * 1024 * 1024

# Abandoned upload sessions (no activity) are swept after this long — a partial
# 2 GB upload can't linger forever.
_SESSION_TTL_SECS = 24 * 3600

# Assembly / part streaming copy chunk.
_COPY_CHUNK = 1024 * 1024

# The folders of the uploads whose complete is running, in this process. Every store over the
# same folder shares it: the routes' store and the sweep's are two objects.
_COMPLETING: set[str] = set()
_COMPLETING_LOCK = threading.Lock()


@dataclass
class UploadSession:
    """Persisted metadata for one in-flight resumable upload."""

    id: str
    filename: str
    size: int
    mime: str
    target: str  # "attachment" | "knowledge" | "workspace" | "voice_profile"
    target_dir: str  # workspace: the validated destination dir; voice_profile: profile dir
    category: str
    part_size: int
    append_mode: bool  # True = concat-in-place (append), False = separate parts
    created_at: float
    updated_at: float
    received: list[int] = field(default_factory=list)  # part indices that landed
    completed: bool = False
    # Which slot inside the target the finished file fills (voice_profile:
    # "ref_audio" | "consent"). Defaulted so an in-flight session written by an
    # older build still deserializes on resume.
    target_key: str = ""

    @property
    def total_parts(self) -> int:
        return max(1, (self.size + self.part_size - 1) // self.part_size)


class UploadError(Exception):
    """A protocol/validation error with an HTTP status + message."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


class UploadStore:
    """Filesystem-backed resumable-upload sessions rooted under ``<home>/uploads/.parts``.

    Each session is a directory ``<root>/<id>/`` holding ``meta.json`` + either the
    part files (separate mode) or the single growing ``assembled`` file (append
    mode). Its record is read and written on the event loop only; parts for one id arrive
    serialized by the client, different ids are independent dirs, and an upload being
    completed is held by :meth:`completing` while its files are worked on in a thread."""

    def __init__(self, root: Path):
        self.root = Path(root)
        ensure_private_dir(self.root)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def init(
        self,
        *,
        filename: str,
        size: int,
        mime: str,
        target: str,
        target_dir: str = "",
        target_key: str = "",
    ) -> UploadSession:
        """Validate the declared file against the size policy + free disk, then open
        a session. Rejects a too-big file before a single byte is uploaded."""
        if size <= 0:
            raise UploadError("size must be positive", 400)
        check = check_upload(filename, mime, size=size)
        if not check.ok:
            raise UploadError(check.reason, check.status)

        # Adaptive disk strategy by free space on the uploads device. Separate-parts
        # + concat needs ~2× transient (parts + final); append-in-place needs ~1×.
        # Reject at init if even 1× (plus a safety margin) won't fit.
        free = _free_bytes(self.root)
        if free is None:
            # Refused, not let through: a disk that cannot be measured here (an I/O error, a
            # link to a drive that is not there) is one the parts cannot be written to either,
            # and the refusal says so instead of quoting a free space nobody measured.
            raise UploadError(
                "The free space on the disk uploads are saved to could not be measured, so the "
                "upload was not started.",
                507,
            )
        margin = 256 * 1024 * 1024  # keep some headroom; never fill the device
        if free < size + margin:
            raise UploadError(
                f"not enough free disk to receive {_human(size)} " f"({_human(free)} free)",
                507,
            )
        append_mode = free < (2 * size + margin)

        sid = uuid.uuid4().hex
        sess = UploadSession(
            id=sid,
            filename=filename,
            size=size,
            mime=mime,
            target=target,
            target_dir=target_dir,
            target_key=target_key,
            category=check.category,
            part_size=PART_SIZE,
            append_mode=append_mode,
            created_at=time.time(),
            updated_at=time.time(),
        )
        ensure_private_dir(self._dir(sid))
        if append_mode:
            # Pre-create the growing final file so part PUTs seek+write into it.
            with open_streamed(self._dir(sid) / "assembled", "wb"):
                pass
        self._save_meta(sess)
        return sess

    def get(self, sid: str) -> UploadSession:
        meta = self._dir(sid) / "meta.json"
        if not meta.is_file():
            raise UploadError("upload session not found", 404)
        data = json.loads(meta.read_text())
        return UploadSession(**data)

    async def write_part(self, sid: str, index: int, part_reader) -> UploadSession:
        """Stream one part to disk (chunk-by-chunk, never in memory). Idempotent:
        re-PUTting an index overwrites — the resume primitive. ``part_reader`` is an
        object with an async ``read_chunk()`` (aiohttp BodyPartReader or the raw
        request content wrapped to match)."""
        sess = self.get(sid)
        if sess.completed:
            raise UploadError("upload already completed", 409)
        if self._is_completing(sid):
            # Its parts are being assembled: a part written now would change the file under it.
            raise UploadError("upload is being completed", 409)
        if index < 0 or index >= sess.total_parts:
            raise UploadError(f"part index {index} out of range (0..{sess.total_parts - 1})", 400)

        expected = self._expected_part_size(sess, index)
        part_path = self._dir(sid) / f"part_{index:06d}"
        tmp = self._dir(sid) / f".part_{index:06d}.tmp"
        written = 0
        if sess.append_mode:
            # Seek to this part's offset in the single growing file and overwrite.
            final = self._dir(sid) / "assembled"
            with open_streamed(final, "r+b") as fh:
                fh.seek(index * sess.part_size)
                written = await _stream_to(part_reader, fh, cap=expected)
        else:
            try:
                with open_streamed(tmp, "wb") as fh:
                    written = await _stream_to(part_reader, fh, cap=expected)
            except BaseException:
                # A part that did not arrive whole (its connection closed: the upload was
                # cancelled, or the network dropped) leaves no file: a resume sends it again.
                tmp.unlink(missing_ok=True)
                raise
        if not (self._dir(sid) / "meta.json").is_file():
            # Dropped while this part streamed in (its files went with it). Recording the part
            # would write the session's record again and bring the dropped upload back.
            raise UploadError("upload session not found", 404)
        if not sess.append_mode:
            os.replace(tmp, part_path)

        if written > expected:
            # A part bigger than declared → the client is lying about size; abort.
            raise UploadError("part exceeds declared part size", 400)

        if index not in sess.received:
            sess.received.append(index)
            sess.received.sort()
        sess.updated_at = time.time()
        self._save_meta(sess)
        return sess

    def is_complete(self, sess: UploadSession) -> bool:
        return sorted(sess.received) == list(range(sess.total_parts))

    async def assemble(self, sid: str) -> tuple[Path, UploadSession]:
        """Concatenate parts (or return the append-mode final) into one file, verify
        the size, and return its path. Does NOT delete the session dir — the caller
        finalizes (scan + hand-off) then calls :meth:`cleanup`. Called inside
        :meth:`completing`, which keeps every other request off the parts meanwhile."""
        sess = self.get(sid)
        if not self.is_complete(sess):
            missing = sorted(set(range(sess.total_parts)) - set(sess.received))
            raise UploadError(f"upload incomplete — missing parts {missing[:10]}", 409)

        final = self._dir(sid) / "assembled"
        if not sess.append_mode:
            # Every byte of the file is copied, so in a worker thread (see the module docstring).
            await asyncio.to_thread(self._concatenate, sess, final)
        actual = final.stat().st_size if final.exists() else 0
        if actual != sess.size:
            raise UploadError(
                f"assembled size {actual} != declared {sess.size}",
                400,
            )
        return final, sess

    def _concatenate(self, sess: UploadSession, final: Path) -> None:
        """Copy the separate part files, in order and streamed, into ``final``."""
        with open_streamed(final, "wb") as out:
            for i in range(sess.total_parts):
                with open(self._dir(sess.id) / f"part_{i:06d}", "rb") as part:
                    shutil.copyfileobj(part, out, _COPY_CHUNK)

    @contextlib.contextmanager
    def completing(self, sid: str) -> Iterator[None]:
        """Hold upload *sid* while its complete runs, from assembling its parts to removing them.

        The complete's work on the files runs in worker threads, so other requests are answered
        while it does. None of them may touch this upload meanwhile: a second complete (a retry)
        is refused here, a part by :meth:`write_part` and a drop by :meth:`drop`, each with 409,
        and the sweep passes it over. Without it a retried complete assembled the same parts
        twice, and a drop removed them while they were being read."""
        key = str(self._dir(sid))
        with _COMPLETING_LOCK:
            if key in _COMPLETING:
                raise UploadError("upload is already being completed", 409)
            _COMPLETING.add(key)
        try:
            yield
        finally:
            with _COMPLETING_LOCK:
                _COMPLETING.discard(key)

    def cleanup(self, sid: str) -> None:
        """Remove upload *sid*'s folder: its parts and its assembled file, as large as the file
        itself, so the complete calls it in a worker thread, inside :meth:`completing`."""
        shutil.rmtree(self._dir(sid), ignore_errors=True)

    async def drop(self, sid: str) -> bool:
        """Discard an upload that will not be completed: the client cancelled it.

        Its parts go now rather than at the sweep, a part still streaming in for it is not
        recorded (:meth:`write_part`), and a later complete finds nothing to assemble. False
        when there is no such upload: never opened, already completed, dropped or swept. An
        upload whose complete is running is not dropped: 409 (:meth:`completing`).

        The folder is first renamed out of the upload's place, which is one step however large
        it is, so from then on every request for the upload finds nothing; what it held is then
        removed in a worker thread."""
        if self._is_completing(sid):
            raise UploadError("upload is being completed", 409)
        here = self._dir(sid)
        if not (here / "meta.json").is_file():
            return False
        dropped = self.root / f".dropped-{uuid.uuid4().hex}"
        try:
            here.rename(dropped)
        except FileNotFoundError:
            return False
        await asyncio.to_thread(shutil.rmtree, dropped, ignore_errors=True)
        return True

    def sweep(self, ttl_secs: int = _SESSION_TTL_SECS) -> int:
        """Delete session dirs idle longer than ``ttl_secs``. Returns count swept."""
        cutoff = time.time() - ttl_secs
        swept = 0
        if not self.root.is_dir():
            return 0
        for d in self.root.iterdir():
            if not d.is_dir() or self._is_completing(d.name):
                continue
            meta = d / "meta.json"
            try:
                mtime = meta.stat().st_mtime if meta.exists() else d.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                swept += 1
        return swept

    # ── internals ────────────────────────────────────────────────────────────

    def _dir(self, sid: str) -> Path:
        # sid is a server-minted uuid4 hex — no path traversal possible, but pin it.
        clean = "".join(c for c in sid if c.isalnum())
        return self.root / clean

    def _is_completing(self, sid: str) -> bool:
        with _COMPLETING_LOCK:
            return str(self._dir(sid)) in _COMPLETING

    def _save_meta(self, sess: UploadSession) -> None:
        atomic_json_write(self._dir(sess.id) / "meta.json", asdict(sess))

    @staticmethod
    def _expected_part_size(sess: UploadSession, index: int) -> int:
        if index < sess.total_parts - 1:
            return sess.part_size
        return sess.size - sess.part_size * (sess.total_parts - 1)


async def _stream_to(part_reader, fh, *, cap: int) -> int:
    """Copy an async part reader to an open file, chunked; stop past ``cap``+slack.
    Returns bytes written. The writes run in a worker thread (:class:`Spool`)."""
    written = 0
    slack = cap + _COPY_CHUNK  # allow one chunk of overrun to detect a lying client
    spool = Spool(fh)
    while True:
        chunk = (
            await part_reader.read_chunk(_COPY_CHUNK)
            if hasattr(part_reader, "read_chunk")
            else await part_reader.read(_COPY_CHUNK)
        )
        if not chunk:
            break
        await spool.write(chunk)
        written += len(chunk)
        if written > slack:
            break
    await spool.flush()
    return written


def _free_bytes(path: Path) -> int | None:
    """Free bytes on the disk ``path`` is on, or will be remade on: the gateway builds the store
    once and caches it, so a root removed under it is only remade by ``init``. ``None`` when the
    disk cannot be measured — never 0, which is a measurement."""
    try:
        return free_disk_bytes(path)
    except OSError:
        return None


def _human(n: int) -> str:
    gb = 1024**3
    mb = 1024**2
    if n >= gb:
        v = n / gb
        return f"{v:.0f} GB" if v == int(v) else f"{v:.1f} GB"
    v = n / mb
    return f"{v:.0f} MB" if v == int(v) else f"{v:.1f} MB"
