"""The journal — the resume cache and the Run Ledger.

Two jobs that share one append-only file, because they are the same data read two ways.

**Resume cache.** After a crash or a rewind, work already done must not be redone. A
cache entry is keyed by `(instance_path, epoch, inputs_hash, spec_region_hash)` — all
four, and each one earns its place:

* `epoch` — a rewind bumps it, so a replayed region from a superseded epoch can never be
  mistaken for a hit on the current one.
* `inputs_hash` — if an upstream output changed, this node's inputs changed, and the
  cached output is stale even though the node itself was not edited.
* `spec_region_hash` — if the node's own config was edited, the cached output came from
  a different prompt. Without this, editing a prompt mid-run and resuming would silently
  serve the pre-edit answer.

A hit emits `step_cached` (WF2-A1) rather than staying invisible: "did my edit actually
re-run anything?" is the first question a user asks after a mid-flight edit, and the
answer has to come from the ledger, not from reading logs.

**Run Ledger.** The event subset the Learning Flywheel's template-refiner reads. These
are emission REQUIREMENTS, not a nice-to-have: a downstream evaluator that wants to know
which model a step used, what it cost, and why it failed is starved if the engine only
journals free text. `resolved_prompt_ref` points at the fully-resolved post-binding
prompt so a trajectory can be replayed — the acceptance bar is that prompt → tool calls
→ output is reconstructable from ledger events alone.

Everything written here passes through `redact()` first. A journal is read back by the
flywheel, shipped in bug reports, and rendered in a UI; a credential that reaches it is
a credential leaked to all three.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from personalclaw.workflows import store
from personalclaw.workflows.models import Failure, InstanceState

logger = logging.getLogger(__name__)

JOURNAL_FILE = "journal.jsonl"
EVENTS_FILE = "events.jsonl"

#: Outputs above this go to an artifact file and leave a stub behind. The same boundary
#: the live chat sanitizer uses — a 5MB tool result must not become a 5MB journal line
#: that every subsequent read has to parse.
MAX_INLINE_OUTPUT_BYTES = 64 * 1024


# ── ledger event kinds (the Learning-Flywheel contract) ──────────────────────

STEP_STARTED = "step_started"
STEP_COMPLETED = "step_completed"
STEP_FAILED = "step_failed"
STEP_SKIPPED = "step_skipped"
STEP_CACHED = "step_cached"
#: One try at one node — typed, so a retry gets actionable feedback rather than prose,
#: and so the flywheel can later see WHICH corrections actually worked (WF2-R4).
STEP_ATTEMPT = "step_attempt"
#: Retries spent or the breaker tripped — a typed decision record, not a bare failure.
STEP_ESCALATED = "step_escalated"
GATE_REJECTED = "gate_rejected"
GATE_CRITERION = "gate_criterion"
#: A human answered a waiting gate (WF2-R7). Journaled with the answer so a later reader
#: knows WHO decided what, not merely that the run continued.
GATE_RESOLVED = "gate_resolved"
EFFECT = "effect"
#: A node wrote outside its declared `allowed_write_paths` (WF2-R19). Ledgered whether
#: the mode was warn or reject — an escape a `warn` run continued past still has to be
#: findable afterwards.
STEP_SCOPE = "step_scope_violation"
ITERATION = "iteration"
USER_EDITED_MID_FLIGHT = "user_edited_mid_flight"
#: A queued batch failed its TOCTOU re-verify (state moved under the preview). Journaled
#: because a silently dropped mutation is indistinguishable from an applied one.
MUTATION_REJECTED = "mutation_rejected"
#: A done node whose inputs changed but which is NOT being re-run (WF2-R2 #3) — better a
#: visible flag than an answer computed from inputs that no longer exist.
INPUTS_STALE = "inputs_stale"
CONSULTED = "consulted"
CHILD_RUN_ATTACH = "child_run_attach"
RUN_ABANDONED = "run_abandoned"
CRYSTALLIZED = "crystallized"
#: Context-lifecycle records (WF2-R6). Journaled rather than held in memory so a rewind or fork
#: REPLAYS them — a handoff reconstructed after the fact is a summary, which is the thing it exists
#: to replace.
HANDOFF = "handoff"
CARRYOVER = "carryover"
DECISION = "decision"
RUN_STARTED = "run_started"
RUN_FINISHED = "run_finished"
#: LOOPS-EVOLUTION R4/R14: the middleware's own observable events. `breaker_trip` and
#: `steering` are ledger kinds because a refiner needs to know a run was nudged or
#: steered — a verdict that followed a human's mid-run instruction is not evidence about
#: the template, and without the event there is no way to tell the two apart.
BREAKER_TRIP = "breaker_trip"
STEERING = "steering"
JUDGE_VERDICT = "judge_verdict"
JUDGE_DIVERGENCE = "judge_divergence"
#: KNOWLEDGE-SYNTHESIS §4: long-run watcher mechanics. `watcher_reaped` is a ledger kind
#: because a watcher stopped early produced fewer cycles than its cadence implies, and a
#: refiner reading cycle counts without it would conclude the template under-performed.
#: `seen_set` and `buffer_seal` are what make a months-long run's cost auditable — the whole
#: point of the seen-set is invisible without a record of what it suppressed.
WATCHER_REAPED = "watcher_reaped"
SEEN_SET = "seen_set"
BUFFER_SEAL = "buffer_seal"
DELAY_CLAMPED = "delay_clamped"

#: TASKS-SOPS §1/§4/§5 (S61e): the task-projection events. Ledger kinds rather than a parallel
#: channel, because every one of them answers a question a reader asks of the ledger and nowhere
#: else: WHY does this task exist (`task_materialized`), WHO answered this gate
#: (`confirmation_pending`/`confirmation_resolved`), WHAT evidence flipped it (`task_verified`),
#: and WHICH upstream failure blocked it (`cascade_blocked`). Without them a projected task's whole
#: provenance is invisible — the board shows a task and the ledger shows the run, with nothing
#: connecting the two.
TASK_MATERIALIZED = "task_materialized"
CONFIRMATION_PENDING = "confirmation_pending"
CONFIRMATION_RESOLVED = "confirmation_resolved"
TASK_VERIFIED = "task_verified"
CASCADE_BLOCKED = "cascade_blocked"

#: The subset a downstream refiner reads. Named so a drift test can assert the engine
#: still emits all of them.
LEDGER_KINDS = frozenset(
    {
        STEP_COMPLETED,
        STEP_FAILED,
        STEP_SKIPPED,
        STEP_CACHED,
        STEP_ATTEMPT,
        STEP_ESCALATED,
        GATE_REJECTED,
        GATE_CRITERION,
        GATE_RESOLVED,
        EFFECT,
        STEP_SCOPE,
        MUTATION_REJECTED,
        INPUTS_STALE,
        ITERATION,
        USER_EDITED_MID_FLIGHT,
        CONSULTED,
        CHILD_RUN_ATTACH,
        RUN_ABANDONED,
        CRYSTALLIZED,
        HANDOFF,
        CARRYOVER,
        TASK_MATERIALIZED,
        CONFIRMATION_PENDING,
        CONFIRMATION_RESOLVED,
        TASK_VERIFIED,
        CASCADE_BLOCKED,
        DECISION,
        BREAKER_TRIP,
        STEERING,
        JUDGE_VERDICT,
        JUDGE_DIVERGENCE,
        WATCHER_REAPED,
        SEEN_SET,
        BUFFER_SEAL,
        DELAY_CLAMPED,
    }
)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── redaction ────────────────────────────────────────────────────────────────


def redact(value: Any) -> Any:
    """Strip credentials from anything bound for the journal.

    Delegates to the platform's existing redactors rather than re-deriving patterns:
    they are already maintained, already cover the exfiltration-URL case, and a second
    private copy of the rules would drift out of date exactly when it mattered.
    """
    if isinstance(value, str):
        try:
            from personalclaw.security import redact_credentials, redact_exfiltration_urls

            text, _ = redact_exfiltration_urls(value)
            text, _ = redact_credentials(text)
            return text
        except Exception:  # pragma: no cover — redaction must never break a write
            logger.debug("redaction unavailable", exc_info=True)
            return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


# ── binary detection ─────────────────────────────────────────────────────────

#: Magic prefixes for the formats a node output plausibly picks up — an action provider
#: reading a file, a screenshot tool, a fetched asset. Not exhaustive by design: this is a
#: cheap "is this obviously not text" check, and the size boundary catches whatever slips
#: through. Bytes rather than str because that is what a magic number IS.
_MAGIC_PREFIXES: tuple[bytes, ...] = (
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"\xff\xd8\xff",  # JPEG
    b"GIF87a",
    b"GIF89a",
    b"%PDF-",
    b"\x1f\x8b",  # gzip
    b"PK\x03\x04",  # zip / docx / xlsx / jar
    b"BZh",  # bzip2
    b"\xfd7zXZ\x00",  # xz
    b"\x7fELF",
    b"OggS",
    b"RIFF",  # wav / avi / webp container
)

#: The SAME formats as they arrive base64-encoded. This is the realistic carrier: a node
#: output is JSON, and JSON cannot hold arbitrary bytes — so a screenshot tool or a fetched
#: asset reaches the journal base64'd, and a raw-byte check alone would miss every one of
#: them. Prefixes are long enough (7+ chars of a fixed header) that a false positive on prose
#: is not a practical concern.
_BASE64_PREFIXES: tuple[str, ...] = (
    "iVBORw0KGgo",  # PNG
    "/9j/",  # JPEG
    "R0lGODdh",  # GIF87a
    "R0lGODlh",  # GIF89a
    "JVBERi0",  # %PDF-
    "H4sI",  # gzip
    "UEsDBB",  # zip
    "f0VMRg",  # ELF
)


def is_binary_payload(value: Any) -> bool:
    """True when ``value`` is a string whose leading bytes match a known binary format.

    Content-based, so it catches a small binary an inline-size check never would: a 400-byte
    PNG is under every threshold and still meaningless inline — mojibake in the widget, a
    poisoned `{{nodes.x.output}}` binding, wasted context if it reaches a model.

    Both carriers are checked. Raw bytes decoded into a `str` are recovered with latin-1
    (which maps codepoints 0-255 back to the identical bytes) rather than UTF-8 — a PNG's
    leading `\\x89` UTF-8-encodes to TWO bytes, so a UTF-8 round-trip silently fails to match
    any magic number, which is exactly the bug this comment exists to prevent. Base64 is the
    other carrier, and in practice the more common one.

    Only strings are inspected. A dict or list is structure the engine created, and treating
    a container as binary because one leaf looked like a PNG would spill a whole useful
    output over one field.
    """
    if not isinstance(value, str) or not value:
        return False
    head = value[:16]
    try:
        raw = head.encode("latin-1")
    except UnicodeEncodeError:
        # Codepoints above 255: genuinely text (or surrogate-escaped bytes, which latin-1
        # cannot hold either). Fall back so a lone astral character cannot mask a match.
        raw = head.encode("utf-8", errors="surrogateescape")
    if any(raw.startswith(m) for m in _MAGIC_PREFIXES):
        return True
    return value[:16].startswith(_BASE64_PREFIXES)


# ── hashing ──────────────────────────────────────────────────────────────────


def _stable_json(value: Any) -> str:
    """Canonical form for hashing: sorted keys, no incidental whitespace. Two logically
    identical inputs must hash identically or the resume cache never hits."""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def hash_value(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()[:16]


def inputs_hash(resolved: dict[str, Any]) -> str:
    """Hash of a node's fully-resolved inputs — what actually reached the node, not what
    the spec said. An upstream change shows up here even when this node was untouched."""
    return hash_value(resolved)


def spec_region_hash(node_dict: dict[str, Any]) -> str:
    """Hash of the node's own spec region, children EXCLUDED.

    Children are excluded deliberately: editing a child must invalidate the child, not
    silently re-run its already-completed parent container.
    """
    trimmed = {
        k: v
        for k, v in (node_dict or {}).items()
        if k not in ("children", "body", "cases", "default")
    }
    return hash_value(trimmed)


@dataclass(frozen=True)
class CacheKey:
    """All four fields participate — see the module docstring for why each is load-
    bearing. Dropping any one of them produces a cache that serves stale answers."""

    path: str
    epoch: int
    inputs_hash: str
    spec_hash: str

    def to_str(self) -> str:
        return f"{self.path}|{self.epoch}|{self.inputs_hash}|{self.spec_hash}"


# ── journal writer ───────────────────────────────────────────────────────────


@dataclass
class Journal:
    """Append-only per-run log. Not a class for state — a thin writer over the run
    directory, so two writers in one process cannot hold divergent views."""

    run_id: str
    #: Monotonic sequence for deterministic event ids (`<run>-evt-<seq>`), which makes a
    #: re-emit an idempotent no-op instead of a duplicate (WF2-R11).
    seq: int = 0
    _cache: dict[str, dict[str, Any]] | None = field(default=None, repr=False)

    # ── low-level append ──

    def _append(self, filename: str, record: dict[str, Any]) -> dict[str, Any]:
        self.seq += 1
        record = dict(record)
        record.setdefault("ts", _now())
        record["seq"] = self.seq
        record["event_id"] = f"{self.run_id}-evt-{self.seq}"
        safe = redact(record)
        store.append_jsonl(self.run_id, filename, safe)
        return safe

    def write(self, kind: str, **fields: Any) -> dict[str, Any]:
        """Write one journal record. The resume cache reads `journal.jsonl`; ledger
        consumers read `events.jsonl`. Ledger kinds land in BOTH — one write, two
        readers, no reconciliation step to get wrong."""
        record = {"kind": kind, **fields}
        written = self._append(JOURNAL_FILE, record)
        if kind in LEDGER_KINDS:
            store.append_jsonl(self.run_id, EVENTS_FILE, written)
        if self._cache is not None and kind in (STEP_COMPLETED, STEP_CACHED):
            key = written.get("cache_key")
            if key:
                self._cache[str(key)] = written
        return written

    # ── step lifecycle ──

    def step_started(
        self, path: str, node_id: str, *, epoch: int, lane: str, resolved_prompt_ref: str = ""
    ) -> None:
        self.write(
            STEP_STARTED,
            instance_path=path,
            node_id=node_id,
            epoch=epoch,
            lane=lane,
            resolved_prompt_ref=resolved_prompt_ref,
        )

    def step_completed(
        self,
        path: str,
        node_id: str,
        *,
        epoch: int,
        cache_key: str,
        state: InstanceState,
        duration_secs: float = 0.0,
        tokens: int = 0,
        retries: int = 0,
        model: str = "",
        provider: str = "",
        cost_usd: float = 0.0,
        degraded_reason: str = "",
        resolved_prompt_ref: str = "",
        output_ref: str = "",
    ) -> None:
        """The ledger's primary record. Every field here is required by the flywheel's
        refiner (§5 Run Ledger) — `cost_usd` is backend-authoritative with a rate-table
        floor, never a frontend estimate."""
        self.write(
            STEP_COMPLETED,
            instance_path=path,
            node_id=node_id,
            epoch=epoch,
            cache_key=cache_key,
            state=state.value,
            duration_secs=round(float(duration_secs), 3),
            tokens=int(tokens),
            retries=int(retries),
            model=model,
            provider=provider,
            cost_usd=round(float(cost_usd), 6),
            degraded_reason=degraded_reason,
            resolved_prompt_ref=resolved_prompt_ref,
            output_ref=output_ref,
        )

    def step_failed(
        self,
        path: str,
        node_id: str,
        *,
        epoch: int,
        failure: Failure,
        attempt: int = 0,
        retries_exhausted: bool = False,
        signature: dict[str, Any] | None = None,
    ) -> None:
        self.write(
            STEP_FAILED,
            instance_path=path,
            node_id=node_id,
            epoch=epoch,
            error=failure.cause_plain,
            failure=failure.to_dict(),
            failure_signature=dict(signature or {}),
            attempt=int(attempt),
            retries_exhausted=bool(retries_exhausted),
        )

    def step_skipped(self, path: str, node_id: str, *, epoch: int, actor: str = "engine") -> None:
        """`actor` distinguishes a user's deliberate skip from the engine routing around
        an untaken branch — the refiner must not read the latter as a rejection."""
        self.write(STEP_SKIPPED, instance_path=path, node_id=node_id, epoch=epoch, actor=actor)

    def step_cached(
        self,
        path: str,
        node_id: str,
        *,
        epoch: int,
        cache_key: str,
        state: InstanceState,
        output_ref: str = "",
    ) -> None:
        """A resume/rewind cache hit (WF2-A1). Emitted so a user can confirm from the
        ledger that an edit re-ran exactly the binding closure and nothing else."""
        self.write(
            STEP_CACHED,
            instance_path=path,
            node_id=node_id,
            epoch=epoch,
            cache_key=cache_key,
            state=state.value,
            output_ref=output_ref,
            cached=True,
        )

    def iteration(
        self,
        path: str,
        node_id: str,
        *,
        iteration: int,
        outcome: str,
        error_signature: str = "",
        tokens: int = 0,
    ) -> None:
        """Feeds the deterministic circuit breaker: N identical `error_signature`s in a
        row is a thrash, detectable at zero LLM cost."""
        self.write(
            ITERATION,
            instance_path=path,
            node_id=node_id,
            iteration=int(iteration),
            outcome=outcome,
            error_signature=error_signature,
            tokens=int(tokens),
        )

    def run_started(
        self,
        workflow_name: str,
        *,
        inputs: dict[str, Any],
        spec_version: int,
        resumed: bool = False,
    ) -> None:
        self.write(
            RUN_STARTED,
            workflow_name=workflow_name,
            inputs=dict(inputs or {}),
            spec_version=spec_version,
            resumed=resumed,
        )

    def run_finished(
        self, status: str, *, elapsed_secs: float = 0.0, tokens: int = 0, error: str = ""
    ) -> None:
        self.write(
            RUN_FINISHED,
            status=status,
            elapsed_secs=round(float(elapsed_secs), 3),
            tokens=int(tokens),
            error=error,
        )

    def run_abandoned(self, at_node_id: str, *, elapsed_secs: float = 0.0) -> None:
        self.write(RUN_ABANDONED, at_node_id=at_node_id, elapsed_secs=round(elapsed_secs, 3))

    def user_edited_mid_flight(self, ops: list[dict[str, Any]]) -> None:
        """The structured mutation batch, not a diff blob — the refiner needs to know
        WHAT kind of correction a human made, which a textual diff destroys."""
        self.write(USER_EDITED_MID_FLIGHT, ops=list(ops or []))

    def consulted(self, path: str, node_id: str, *, ref: str) -> None:
        self.write(CONSULTED, instance_path=path, node_id=node_id, ref=ref)

    def handoff(
        self, path: str, node_id: str, *, epoch: int, iteration: int, handoff: dict
    ) -> None:
        """One iteration's handoff to the next (WF2-R6).

        Journaled, not held in memory: a rewind to iteration 3 must replay iteration 2's handoff,
        and an in-memory one would be lost — leaving the replayed iteration to reconstruct from a
        transcript, which is the summarization failure the handoff exists to avoid.
        """
        self.write(
            HANDOFF,
            instance_path=path,
            node_id=node_id,
            epoch=epoch,
            iteration=iteration,
            **handoff,
        )

    def carryover(
        self, path: str, node_id: str, *, epoch: int, iteration: int, buckets: dict
    ) -> None:
        """The typed facts that survive a session reset."""
        self.write(
            CARRYOVER,
            instance_path=path,
            node_id=node_id,
            epoch=epoch,
            iteration=iteration,
            **buckets,
        )

    def decision(self, path: str, node_id: str, *, epoch: int, decision: dict) -> None:
        """A settled choice and why (WF2-R6).

        The rejected alternatives are the point: compaction keeps "we used X" and drops "we
        rejected Y because", so a resumed run re-proposes Y with nothing in its context saying it
        was already dismissed.
        """
        self.write(DECISION, instance_path=path, node_id=node_id, epoch=epoch, **decision)

    def child_run_attach(self, parent_run_id: str, child_run_id: str, node_id: str) -> None:
        self.write(
            CHILD_RUN_ATTACH,
            parent_run_id=parent_run_id,
            child_run_id=child_run_id,
            node_id=node_id,
        )

    def effect(
        self,
        path: str,
        *,
        idempotency_key: str,
        effect_status: str,
        epoch: int = 0,
        node_id: str = "",
        provider: str = "",
        output_id: str = "",
        compensation_ref: str = "",
        detail: str = "",
    ) -> None:
        """One effect-lifecycle event (WF2-R1). ATTEMPTED is written BEFORE dispatch, so
        a crash between attempt and outcome leaves evidence the effect MAY have fired —
        "unknown, possibly fired" and "never fired" demand different recovery."""
        self.write(
            EFFECT,
            instance_path=path,
            idempotency_key=idempotency_key,
            effect_status=effect_status,
            epoch=int(epoch),
            node_id=node_id,
            provider=provider,
            output_id=output_id,
            compensation_ref=compensation_ref,
            detail=detail,
        )

    # ── resume cache ──

    def _load_cache(self) -> dict[str, dict[str, Any]]:
        """Fold the journal into a cache-key → record map. Last write wins, which is
        correct: a later record for the same key came from a later attempt."""
        if self._cache is None:
            cache: dict[str, dict[str, Any]] = {}
            for rec in store.read_jsonl(self.run_id, JOURNAL_FILE):
                if rec.get("kind") in (STEP_COMPLETED, STEP_CACHED):
                    key = rec.get("cache_key")
                    if key:
                        cache[str(key)] = rec
                self.seq = max(self.seq, int(rec.get("seq", 0) or 0))
            self._cache = cache
        return self._cache

    def lookup(self, key: CacheKey) -> dict[str, Any] | None:
        """A cache hit, or None. Only SUCCESS states are served from cache: replaying a
        cached FAILURE would make a transient error permanent across a resume."""
        rec = self._load_cache().get(key.to_str())
        if not rec:
            return None
        try:
            state = InstanceState(str(rec.get("state", "")))
        except ValueError:
            return None
        from personalclaw.workflows.models import SUCCESS_STATES

        return rec if state in SUCCESS_STATES else None

    def invalidate_prefix(self, path_prefix: str) -> int:
        """Drop cache entries at or under a path — the in-memory half of a rewind.

        The journal FILE is never rewritten: it is append-only by contract, and the
        archival of a rewound region is Slice 4's job. This only stops the current
        process serving hits from the invalidated region.
        """
        cache = self._load_cache()
        doomed = [k for k in cache if k.split("|", 1)[0].startswith(path_prefix)]
        for k in doomed:
            cache.pop(k, None)
        return len(doomed)

    # ── output spilling ──

    def store_output(self, path: str, output: Any) -> tuple[str, Any]:
        """Persist a node output, spilling oversized or binary payloads to a file.

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

        The stub always carries `bytes` and `output_ref`, so the full value stays one read
        away and the stub itself explains why it is a stub.
        """
        safe = redact(output)
        encoded = _stable_json(safe)
        size = len(encoded.encode("utf-8"))
        ref = store.write_output(self.run_id, path, safe)

        reason = None
        if is_binary_payload(safe):
            reason = "binary"
        elif size > MAX_INLINE_OUTPUT_BYTES:
            reason = "oversize"
        if reason is None:
            return ref, safe
        return ref, {
            "result_omitted": True,
            "reason": reason,
            "bytes": size,
            "output_ref": ref,
        }

    # ── ledger queries ───────────────────────────────────────────────────────────

    # ── TASKS-SOPS projection events (S61e) ──

    def task_materialized(
        self,
        path: str,
        node_id: str,
        *,
        task_id: str,
        fingerprint: str = "",
        refreshed: bool = False,
    ) -> None:
        """A leaf node became (or refreshed) a Task.

        `refreshed` distinguishes a rewind's dedup-merge from a first materialization. Without it a
        reader counting `task_materialized` events over-counts the run's output every time it was
        rewound — and §1 makes idempotent recompute the NORMAL path, so that is not a rare case.
        """
        self.write(
            TASK_MATERIALIZED,
            instance_path=path,
            node_id=node_id,
            task_id=task_id,
            fingerprint=fingerprint,
            refreshed=bool(refreshed),
        )

    def confirmation_pending(
        self, path: str, node_id: str, *, confirmation_id: str, kind: str = "approval"
    ) -> None:
        """A gate is waiting on a human. Paired with `confirmation_resolved` by `confirmation_id`.

        Recorded when the gate STARTS waiting, not only when it is answered: a run that sat
        unanswered for a week and one answered instantly are indistinguishable from the
        resolution alone, and the wait is the number a user cares about.
        """
        self.write(
            CONFIRMATION_PENDING,
            instance_path=path,
            node_id=node_id,
            confirmation_id=confirmation_id,
            confirmation_kind=kind,
        )

    def confirmation_resolved(
        self,
        path: str,
        node_id: str,
        *,
        confirmation_id: str,
        verb: str,
        approved: bool,
        resolved_by: str = "",
    ) -> None:
        """A human answered. Carries BOTH the verb and the boolean.

        The boolean is what the engine acted on; the verb is what the user chose. They cannot
        disagree today, but recording only the boolean would make an audit unable to distinguish a
        reject from an expiry auto-reject — which is exactly the distinction §4's per-type expiry
        policy exists to create.
        """
        self.write(
            CONFIRMATION_RESOLVED,
            instance_path=path,
            node_id=node_id,
            confirmation_id=confirmation_id,
            verb=verb,
            approved=bool(approved),
            resolved_by=resolved_by or "unknown",
        )

    def task_verified(
        self, path: str, node_id: str, *, task_id: str, passed: bool, criterion: str = ""
    ) -> None:
        """A done-criterion ran and the engine flipped (or withheld) the task's done state.

        `criterion` is recorded because "verification failed" without naming what was checked is a
        finding a user cannot act on, and the criterion is the def author's text.
        """
        self.write(
            TASK_VERIFIED,
            instance_path=path,
            node_id=node_id,
            task_id=task_id,
            passed=bool(passed),
            criterion=criterion,
        )

    def cascade_blocked(
        self, path: str, node_id: str, *, blocked_task_ids: list[str], cause: str
    ) -> None:
        """An upstream failure blocked dependents. ONE event for the whole cascade.

        The blocked ids ride as a list rather than one event each: §1 debounces the
        notification, and a ledger recording N events for one upstream failure would make
        the run look like it failed N times.
        """
        self.write(
            CASCADE_BLOCKED,
            instance_path=path,
            node_id=node_id,
            blocked_task_ids=list(blocked_task_ids),
            cause=cause,
        )


def ledger(run_id: str, *, kinds: set[str] | None = None) -> list[dict[str, Any]]:
    """Read the ledger, optionally filtered. Pass-rate, failure distribution and
    latency percentiles are queries over this — not a separate metrics store."""
    records = store.read_jsonl(run_id, EVENTS_FILE)
    if kinds is None:
        return records
    return [r for r in records if r.get("kind") in kinds]


def run_totals(run_id: str) -> dict[str, Any]:
    """Aggregate a run's ledger into the counters the run row carries.

    Budgets are PRE-CHARGED from this on resume (WF2-R4 invariant #1): a resumed run
    must inherit what it already spent, or a crash loop becomes an unbounded spend.
    """
    tokens = 0
    cost = 0.0
    steps = 0
    failures = 0
    cached = 0
    for rec in store.read_jsonl(run_id, EVENTS_FILE):
        kind = rec.get("kind")
        if kind == STEP_COMPLETED:
            steps += 1
            tokens += int(rec.get("tokens", 0) or 0)
            cost += float(rec.get("cost_usd", 0.0) or 0.0)
        elif kind == STEP_FAILED:
            failures += 1
        elif kind == STEP_CACHED:
            cached += 1
    return {
        "tokens": tokens,
        "cost_usd": round(cost, 6),
        "steps_completed": steps,
        "steps_failed": failures,
        "steps_cached": cached,
    }
