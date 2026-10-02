"""Tool-description vectors: one index for the whole gateway, kept across restarts.

Tool retrieval ranks a turn's catalog by meaning, so it needs a vector for each tool's
``name: description``. Each runtime used to embed its whole catalog itself, on its first turn, one
request per tool, inside the turn and so on the event loop that serves every request. A new chat, a
workflow stage's agent and every restart paid it again: on a stock install 120 sequential requests,
and the gateway answered nothing else until they were done (21 s with the model server busy).

Now there is one index for the process, keyed by what is embedded and by the model that embedded
it, and saved in the home (:data:`TOOL_VECTORS_FILE`) so a restart embeds nothing that has not
changed. What is missing is embedded in the background, in batches (`embed_batch.embed_texts`:
one request per group, with its retry and split), on a thread of its own. A turn never waits for
it: it ranks with the vectors the index already holds, and a tool without one yet ranks by its
words, the way retrieval ranks when no embedding model is bound at all. Tool retrieval fails open,
so a tool it cannot rank is listed in the turn's catalog, never hidden.

Everything the background thread uses is resolved by its caller — the embedding functions, the
model they embed with, the file — so the thread never looks up a home or a binding of its own.

The file is a cache: `durability.inventory` ignores it, and a missing or unreadable one reads as
empty. Vectors are stored as base64 float32, which is ample for a cosine.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import threading
import time
from array import array
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.knowledge.embed_batch import DEFAULT_BATCH_SIZE, DEFAULT_RETRY_BUDGET, embed_texts

logger = logging.getLogger(__name__)

#: The index's file in the home.
TOOL_VECTORS_FILE = "tool_embeddings.json"

#: The most vectors the file keeps, the oldest dropped first. A catalog is a few hundred tools;
#: the bound keeps a home that has seen many tool servers come and go from growing it for ever.
MAX_VECTORS = 2048

#: How long a text that could not be embedded waits before it is tried again, so an embedding
#: server that is down is not asked again by every turn.
RETRY_FAILED_SECS = 300.0


def tool_text(name: str, description: str) -> str:
    """What is embedded for a tool: its name and its description."""
    return f"{name}: {description or ''}".strip()


@dataclass(frozen=True)
class Embedder:
    """The embedding model bound now, as the index uses it: its ref, and its functions."""

    #: ``provider:model`` — what a vector is filed under. Vectors of two models never compare.
    model: str
    #: One text, one vector (the query of a turn), or None when it could not be embedded.
    one: Callable[[str], list[float] | None]
    #: A group of texts in one call, or None when the provider has no batch path.
    many: Callable[[list[str]], list[list[float] | None]] | None


def bound_embedder() -> Embedder | None:
    """The embedding model bound now, or None when none is bound or it cannot be built.

    Both functions are built for the ONE binding read here, so a query and the vectors it is
    compared with come from the same model even while the binding changes.
    """
    try:
        from personalclaw.embedding_providers.registry import (
            _active_embedding_spec,
            embed_fn_for,
            embed_many_fn_for,
        )

        spec = _active_embedding_spec()
        if not spec or not spec[1]:
            return None
        one = embed_fn_for(*spec)
        if one is None:
            return None
        return Embedder(model=f"{spec[0]}:{spec[1]}", one=one, many=embed_many_fn_for(*spec))
    except Exception:  # noqa: BLE001 — no embedder means keyword ranking, never a failed turn
        logger.debug("tool vectors: the bound embedding model could not be resolved", exc_info=True)
        return None


def default_path() -> Path:
    """The index's file in the home in use."""
    from personalclaw.config.loader import config_dir

    return config_dir() / TOOL_VECTORS_FILE


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _pack(vector: list[float]) -> str:
    return base64.b64encode(array("f", vector).tobytes()).decode("ascii")


def _unpack(blob: object) -> list[float] | None:
    if not isinstance(blob, str):
        return None
    try:
        raw = base64.b64decode(blob, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not raw or len(raw) % 4:
        return None
    vector = array("f")
    vector.frombytes(raw)
    return vector.tolist()


def _read(path: Path, model: str) -> dict[str, list[float]]:
    """The vectors *path* holds for *model*; empty when it is missing, unreadable or another
    model's."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("model") != model:
        return {}
    stored = data.get("vectors")
    if not isinstance(stored, dict):
        return {}
    out: dict[str, list[float]] = {}
    for digest, blob in stored.items():
        vector = _unpack(blob)
        if vector:
            out[str(digest)] = vector
    return out


def _write(path: Path, model: str, vectors: dict[str, list[float]]) -> None:
    body = {"model": model, "vectors": {d: _pack(v) for d, v in vectors.items()}}
    atomic_write(path, json.dumps(body, separators=(",", ":")))


@dataclass
class _Job:
    """Texts waiting to be embedded with one model, into one file or into memory only."""

    path: Path | None
    embedder: Embedder
    texts: dict[str, str]  # digest -> text


class ToolVectors:
    """The process's tool-description vectors (see the module docstring), or, built with
    another ``what``, an index of the same kind for other descriptions: skill surfacing keeps the
    skills' in one (``skills.surfacing``), in a file of its own, and agent routing its candidates'
    (``agents.routing``) in memory only, which is what a ``path`` of ``None`` means.

    Thread-safe: a turn's selection reads it from a worker thread while the background fill
    writes it from its own.
    """

    def __init__(self, what: str = "tool description", thread: str = "tool-vectors") -> None:
        #: What its texts are, as its log lines name them, and its fill thread's name.
        self._what = what
        self._thread = thread
        self._lock = threading.Lock()
        # The one (file, model) held in memory, and its vectors, oldest first.
        self._held: tuple[Path | None, str] | None = None
        self._vectors: dict[str, list[float]] = {}
        self._jobs: list[_Job] = []
        # (file, model, digest) of every text queued or being embedded now, so a turn that asks
        # while the fill runs does not queue the same texts twice.
        self._pending: set[tuple[Path | None, str, str]] = set()
        self._failed: dict[tuple[Path | None, str, str], float] = {}
        self._worker: threading.Thread | None = None

    def _hold(self, path: Path | None, model: str) -> None:
        """Hold *path*'s vectors for *model* in memory (lock held)."""
        if self._held != (path, model):
            self._held = (path, model)
            self._vectors = _read(path, model) if path is not None else {}

    def vectors(
        self, path: Path | None, model: str, texts: Iterable[str]
    ) -> dict[str, list[float]]:
        """The vector of each of *texts* the index holds for *model*. Never embeds anything."""
        with self._lock:
            self._hold(path, model)
            out: dict[str, list[float]] = {}
            for text in texts:
                vector = self._vectors.get(_digest(text))
                if vector is not None:
                    out[text] = vector
            return out

    def want(self, path: Path | None, embedder: Embedder, texts: Iterable[str]) -> None:
        """Embed those of *texts* the index lacks, in the background; returns at once."""
        now = time.monotonic()
        model = embedder.model
        with self._lock:
            self._hold(path, model)
            todo = {}
            for text in texts:
                digest = _digest(text)
                key = (path, model, digest)
                if not text or digest in self._vectors or key in self._pending:
                    continue
                failed_at = self._failed.get(key)
                if failed_at is not None and now - failed_at < RETRY_FAILED_SECS:
                    continue
                todo[digest] = text
                self._pending.add(key)
            if not todo:
                return
            for job in self._jobs:
                if job.path == path and job.embedder.model == model:
                    job.texts.update(todo)
                    job.embedder = embedder
                    break
            else:
                self._jobs.append(_Job(path=path, embedder=embedder, texts=todo))
            if self._worker is None:
                self._worker = threading.Thread(target=self._fill, name=self._thread, daemon=True)
                self._worker.start()

    def drain(self, timeout: float | None = None) -> bool:
        """Wait for the background fill to finish, and say whether it did — for a test, which must
        not end while a thread it started still runs."""
        with self._lock:
            worker = self._worker
        if worker is None:
            return True
        worker.join(timeout)
        return not worker.is_alive()

    def _fill(self) -> None:
        while True:
            with self._lock:
                if not self._jobs:
                    self._worker = None
                    return
                job = self._jobs.pop(0)
            vectors: list[list[float] | None] = []
            try:
                vectors = self._embed(job)
            except Exception:  # noqa: BLE001 — a failed fill leaves those tools ranked by words
                logger.warning(
                    "%s vectors: embedding %d %s(s) failed",
                    self._what,
                    len(job.texts),
                    self._what,
                    exc_info=True,
                )
            self._store(job, vectors)

    def _embed(self, job: _Job) -> list[list[float] | None]:
        # The group size and the retries are passed, not read from the knowledge settings:
        # reading them would have this thread look up a home of its own.
        return embed_texts(
            list(job.texts.values()),
            embed_many=job.embedder.many,
            embed_one=job.embedder.one,
            batch_size=DEFAULT_BATCH_SIZE,
            retry_budget=DEFAULT_RETRY_BUDGET,
        )

    def _store(self, job: _Job, vectors: list[list[float] | None]) -> None:
        """File what *job* embedded, remember what it could not, and save the index."""
        model = job.embedder.model
        now = time.monotonic()
        with self._lock:
            if self._held == (job.path, model):
                stored = self._vectors
            else:
                stored = _read(job.path, model) if job.path is not None else {}
            embedded = 0
            for i, digest in enumerate(job.texts):
                key = (job.path, model, digest)
                self._pending.discard(key)
                vector = vectors[i] if i < len(vectors) else None
                if vector:
                    stored.pop(digest, None)
                    stored[digest] = list(vector)
                    self._failed.pop(key, None)
                    embedded += 1
                else:
                    self._failed[key] = now
            for digest in list(stored)[: max(0, len(stored) - MAX_VECTORS)]:
                del stored[digest]
            snapshot = dict(stored)
        if embedded and job.path is not None:
            try:
                _write(job.path, model, snapshot)
            except OSError:
                logger.warning("%s vectors: could not save %s", self._what, job.path, exc_info=True)
        logger.info(
            "%s vectors: embedded %d of %d %s(s) with %s",
            self._what,
            embedded,
            len(job.texts),
            self._what,
            model,
        )


_INDEX = ToolVectors()


def tool_vectors() -> ToolVectors:
    """The process's :class:`ToolVectors`."""
    return _INDEX
