"""The append/stamp/spill machinery — everything a ledger does that has nothing to do with what
kind of work is being ledgered.

`LedgerWriter` is deliberately not usable on its own: it has no idea where its files live. A
producer subclasses it, binds :attr:`LedgerWriter._store` to its own run-scoped file store, and adds
whatever typed emitters its domain needs. Everything below the emitters — sequencing, stamping,
redaction, the ledger mirror, the oversize/binary spill and the resume-cache fold — is identical for
every producer, and is the part that must never be re-implemented: a second copy of the spill
boundary is a second answer to "how big is too big", and the two would drift.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Protocol

from personalclaw import record_files
from personalclaw.ledger.hashing import stable_json
from personalclaw.ledger.kinds import LEDGER_KINDS, STEP_CACHED, STEP_COMPLETED
from personalclaw.ledger.outcomes import OutcomeLedger
from personalclaw.ledger.redaction import is_binary_payload, redact

JOURNAL_FILE = "journal.jsonl"
EVENTS_FILE = "events.jsonl"

#: Outputs above this go to an artifact file and leave a stub behind. The same boundary
#: the live chat sanitizer uses — a 5MB tool result must not become a 5MB journal line
#: that every subsequent read has to parse.
MAX_INLINE_OUTPUT_BYTES = 64 * 1024

#: How much of each end of a spilled oversize body the stub keeps as a preview. Small
#: enough that the stub still fits an SSE frame and a journal line, large enough to orient a
#: reader before they fetch the artifact.
_PREVIEW_EDGE_CHARS = 140


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _seq_of(record: dict[str, Any]) -> int:
    """A journal line's number, 0 for a line without a usable one: an unreadable number must not
    leave the run's journal unwritable."""
    try:
        return int(record.get("seq", 0) or 0)
    except (TypeError, ValueError):
        return 0


class LedgerStore(Protocol):
    """The run-scoped file store a ledger appends through.

    Narrow on purpose — five calls, all keyed by an opaque id. A producer that can satisfy these
    can carry a ledger; nothing here knows about workflows, loops or tasks.
    """

    def ledger_file(self, run_id: str, filename: str) -> Path | None:
        """Where the ledger *run_id* keeps *filename*, or None for an id that names no ledger (the
        store writes nothing for one). The writer numbers each record from the journal there,
        under the lock beside it."""
        ...

    def append_jsonl(self, run_id: str, filename: str, record: dict[str, Any]) -> None: ...

    def read_jsonl(self, run_id: str, filename: str) -> list[dict[str, Any]]: ...

    def write_output(self, run_id: str, node_path: str, output: Any) -> str: ...

    def write_artifact(self, run_id: str, node_path: str, output: Any) -> str: ...


@dataclass
class LedgerWriter(OutcomeLedger):
    """Append-only per-run log. Not a class for state — a thin writer over the run
    directory, so two writers in one process cannot hold divergent views.

    Carries the outcome pair (`open_outcome`/`resolve_outcome`) from
    :class:`personalclaw.ledger.outcomes.OutcomeLedger`, so every producer that can carry a ledger
    can open a question about what LANDED — not only the one feature that first needed it.
    """

    run_id: str
    #: The number this writer gave its last record (`write`): one past the highest the journal
    #: held then. Deterministic event ids (`<run>-evt-<seq>`) make a re-emit an idempotent no-op
    #: instead of a duplicate.
    seq: int = 0
    _cache: dict[str, dict[str, Any]] | None = field(default=None, repr=False)
    #: The journal as this writer's own last append left it (`record_files.stamp`). While the
    #: file is still exactly that, nobody has appended since, and `seq` is still its highest.
    _left: tuple[int, int, int] | None = field(default=None, repr=False)

    #: Bound by each subclass to its own store module. Declared without a default so a producer
    #: that forgets fails loudly on its first write rather than quietly journaling somewhere else.
    _store: ClassVar[LedgerStore]

    # ── append ──

    def write(self, kind: str, **fields: Any) -> dict[str, Any]:
        """Write one journal record. The resume cache reads `journal.jsonl`; ledger
        consumers read `events.jsonl`. Ledger kinds land in BOTH — one write, two
        readers, no reconciliation step to get wrong.

        Numbered from the journal itself, under the lock both appends hold: a record takes the
        number after the highest the journal holds, so its id is its own in the run whoever writes
        it and whenever, and two writers at once take two numbers. A writer is built wherever there
        is a line to add to a run (the engine's, a provider's beside it, an answer to a digest after
        its run ended), and a count each one kept for itself started again at 1."""
        record = {"kind": kind, **fields}
        journal = self._store.ledger_file(self.run_id, JOURNAL_FILE)
        held = record_files.locked(journal) if journal is not None else contextlib.nullcontext()
        with held:
            self.seq = self._highest_seq(journal) + 1
            record.setdefault("ts", now())
            record["seq"] = self.seq
            record["event_id"] = f"{self.run_id}-evt-{self.seq}"
            written = redact(record)
            self._store.append_jsonl(self.run_id, JOURNAL_FILE, written)
            if kind in LEDGER_KINDS:
                self._store.append_jsonl(self.run_id, EVENTS_FILE, written)
            self._left = record_files.stamp(journal) if journal is not None else None
        if self._cache is not None and kind in (STEP_COMPLETED, STEP_CACHED):
            key = written.get("cache_key")
            if key:
                self._cache[str(key)] = written
        return written

    def _highest_seq(self, journal: Path | None) -> int:
        """The highest number the journal holds, read under its lock (`write`).

        Read again unless the file is exactly as this writer's own last append left it, which
        means that append is still its newest line: a writer's first record, another writer's
        append in between, and a rewritten file all read it. An id the store keeps no file for
        has only this writer's own count."""
        if journal is None:
            return self.seq
        if self._left is not None and record_files.stamp(journal) == self._left:
            return self.seq
        return max(
            (_seq_of(rec) for rec in self._store.read_jsonl(self.run_id, JOURNAL_FILE)),
            default=0,
        )

    # ── resume cache ──

    def _load_cache(self) -> dict[str, dict[str, Any]]:
        """Fold the journal into a cache-key → record map. Last write wins, which is
        correct: a later record for the same key came from a later attempt. Kept current by
        :meth:`write` from then on, which is why it lives with the writer rather than with the
        workflow-flavoured lookup."""
        if self._cache is None:
            cache: dict[str, dict[str, Any]] = {}
            for rec in self._store.read_jsonl(self.run_id, JOURNAL_FILE):
                if rec.get("kind") in (STEP_COMPLETED, STEP_CACHED):
                    key = rec.get("cache_key")
                    if key:
                        cache[str(key)] = rec
            self._cache = cache
        return self._cache

    # ── output spilling ──

    def store_output(self, path: str, output: Any) -> tuple[str, Any]:
        """Persist a node output, offloading oversized or binary payloads to an artifact file.

        Returns `(output_ref, inline_preview)`. The preview is what bindings and the widget
        read inline; anything past the boundary leaves a typed `result_omitted` stub so a
        reader knows the data exists rather than seeing a truncated string it might parse.

        Two spill reasons, both about what an inline value costs downstream:

        * `oversize` — over :data:`MAX_INLINE_OUTPUT_BYTES`. The same boundary the live chat
          sanitizer uses; a 5MB tool result must not become a 5MB journal line that every
          later read re-parses, nor a 5MB SSE frame.
        * `binary` — a magic-prefix match (PNG, JPEG, PDF, gzip, zip, ELF…). Detected by
          CONTENT, not size: a 400-byte PNG is under every threshold and still meaningless
          inline — it would render as mojibake in the widget, poison a `{{nodes.x.output}}`
          binding, and (if it reached a model) burn context on noise. Path-agnostic because
          a node's output is not a filename.

        An inline output is written to `outputs/`, byte-identical to before. An OFFLOADED
        one is written to `runs/<id>/artifacts/` instead, so its `output_ref` does NOT start
        with `outputs/` — the signal every reader uses to treat it as a fetch-on-demand pointer
        (`{{nodes.x.artifact}}`, the `artifact_inspect` provider). The oversize stub keeps a
        head+tail `preview` (both ends of the serialized body, not just the head) so a truncated
        view still shows where the value starts AND ends; a binary stub omits it, because a slice
        of a PNG is noise. The stub always carries `bytes` and `output_ref`, so the full value
        stays one read away and the stub itself explains why it is a stub.
        """
        safe = redact(output)
        encoded = stable_json(safe)
        size = len(encoded.encode("utf-8"))

        reason = None
        if is_binary_payload(safe):
            reason = "binary"
        elif size > MAX_INLINE_OUTPUT_BYTES:
            reason = "oversize"
        if reason is None:
            # Inline path: unchanged. The body stays under `outputs/` and rides in bindings.
            ref = self._store.write_output(self.run_id, path, safe)
            return ref, safe

        # Offload path: the full body goes to `artifacts/`, leaving a stub the reader can hand
        # to a binding or a widget without paying for the blob.
        ref = self._store.write_artifact(self.run_id, path, safe)
        stub: dict[str, Any] = {
            "result_omitted": True,
            "reason": reason,
            "bytes": size,
            "output_ref": ref,
        }
        if reason == "oversize" and len(encoded) > 2 * _PREVIEW_EDGE_CHARS:
            stub["preview"] = {
                "head": encoded[:_PREVIEW_EDGE_CHARS],
                "tail": encoded[-_PREVIEW_EDGE_CHARS:],
            }
        return ref, stub
