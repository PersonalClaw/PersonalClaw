"""Resumable upload store — the ``init / part / status / complete`` protocol.

A single 2 GB POST is fragile (browser memory, proxy timeout, no resume). This is
a minimal tus-like protocol: the client declares the file up front (``init``,
validated against the size policy before a byte is sent), streams fixed-size parts
to disk (``part``, idempotent → the resume primitive), can ask what landed
(``status``), then ``complete`` assembles the parts into one file and hands it to
the destination handler (chat attach / knowledge / workspace) exactly as the
single-POST path does.

Bytes never sit in memory: each part streams chunk-by-chunk to disk, and assembly
copies part→final in bounded chunks. Disk strategy is adaptive (see
:meth:`UploadStore.init`): with ≥2× headroom parts are separate files concatenated
at complete (robust resume — each part independently re-PUTtable); when tighter,
parts append into one growing final file (~1× disk). Abandoned sessions are swept
by TTL.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from personalclaw.uploads.policy import check_upload

# One part = this many bytes. 8 MB balances request count vs per-request overhead;
# a 2 GB upload is ~256 parts. The client is told this in the init response.
PART_SIZE = 8 * 1024 * 1024

# Abandoned upload sessions (no activity) are swept after this long — a partial
# 2 GB upload can't linger forever.
_SESSION_TTL_SECS = 24 * 3600

# Assembly / part streaming copy chunk.
_COPY_CHUNK = 1024 * 1024


@dataclass
class UploadSession:
    """Persisted metadata for one in-flight resumable upload."""

    id: str
    filename: str
    size: int
    mime: str
    target: str  # "attachment" | "knowledge" | "workspace"
    target_dir: str  # for workspace target: the validated destination directory
    category: str
    part_size: int
    append_mode: bool  # True = concat-in-place (append), False = separate parts
    created_at: float
    updated_at: float
    received: list[int] = field(default_factory=list)  # part indices that landed
    completed: bool = False

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
    mode). Thread-safety: aiohttp handlers are single-loop; parts for one id arrive
    serialized by the client, and different ids are independent dirs."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def init(
        self, *, filename: str, size: int, mime: str, target: str, target_dir: str = ""
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
            category=check.category,
            part_size=PART_SIZE,
            append_mode=append_mode,
            created_at=time.time(),
            updated_at=time.time(),
        )
        self._dir(sid).mkdir(parents=True, exist_ok=True)
        if append_mode:
            # Pre-create the growing final file so part PUTs seek+write into it.
            (self._dir(sid) / "assembled").touch()
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
        if index < 0 or index >= sess.total_parts:
            raise UploadError(f"part index {index} out of range (0..{sess.total_parts - 1})", 400)

        expected = self._expected_part_size(sess, index)
        written = 0
        if sess.append_mode:
            # Seek to this part's offset in the single growing file and overwrite.
            final = self._dir(sid) / "assembled"
            with open(final, "r+b") as fh:
                fh.seek(index * sess.part_size)
                written = await _stream_to(part_reader, fh, cap=expected)
        else:
            part_path = self._dir(sid) / f"part_{index:06d}"
            tmp = self._dir(sid) / f".part_{index:06d}.tmp"
            with open(tmp, "wb") as fh:
                written = await _stream_to(part_reader, fh, cap=expected)
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
        finalizes (scan + hand-off) then calls :meth:`cleanup`."""
        sess = self.get(sid)
        if not self.is_complete(sess):
            missing = sorted(set(range(sess.total_parts)) - set(sess.received))
            raise UploadError(f"upload incomplete — missing parts {missing[:10]}", 409)

        final = self._dir(sid) / "assembled"
        if not sess.append_mode:
            # Concatenate the separate part files, streamed, into `assembled`.
            with open(final, "wb") as out:
                for i in range(sess.total_parts):
                    part_path = self._dir(sid) / f"part_{i:06d}"
                    with open(part_path, "rb") as pf:
                        while True:
                            chunk = pf.read(_COPY_CHUNK)
                            if not chunk:
                                break
                            out.write(chunk)
        actual = final.stat().st_size if final.exists() else 0
        if actual != sess.size:
            raise UploadError(
                f"assembled size {actual} != declared {sess.size}",
                400,
            )
        return final, sess

    def cleanup(self, sid: str) -> None:
        shutil.rmtree(self._dir(sid), ignore_errors=True)

    def sweep(self, ttl_secs: int = _SESSION_TTL_SECS) -> int:
        """Delete session dirs idle longer than ``ttl_secs``. Returns count swept."""
        cutoff = time.time() - ttl_secs
        swept = 0
        if not self.root.is_dir():
            return 0
        for d in self.root.iterdir():
            if not d.is_dir():
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

    def _save_meta(self, sess: UploadSession) -> None:
        meta = self._dir(sess.id) / "meta.json"
        tmp = meta.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(sess)))
        os.replace(tmp, meta)

    @staticmethod
    def _expected_part_size(sess: UploadSession, index: int) -> int:
        if index < sess.total_parts - 1:
            return sess.part_size
        return sess.size - sess.part_size * (sess.total_parts - 1)


async def _stream_to(part_reader, fh, *, cap: int) -> int:
    """Copy an async part reader to an open file, chunked; stop past ``cap``+slack.
    Returns bytes written."""
    written = 0
    slack = cap + _COPY_CHUNK  # allow one chunk of overrun to detect a lying client
    while True:
        chunk = (
            await part_reader.read_chunk(_COPY_CHUNK)
            if hasattr(part_reader, "read_chunk")
            else await part_reader.read(_COPY_CHUNK)
        )
        if not chunk:
            break
        fh.write(chunk)
        written += len(chunk)
        if written > slack:
            break
    return written


def _free_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(str(path)).free
    except OSError:
        return 0


def _human(n: int) -> str:
    gb = 1024**3
    mb = 1024**2
    if n >= gb:
        v = n / gb
        return f"{v:.0f} GB" if v == int(v) else f"{v:.1f} GB"
    v = n / mb
    return f"{v:.0f} MB" if v == int(v) else f"{v:.1f} MB"
