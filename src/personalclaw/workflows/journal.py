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
EFFECT = "effect"
ITERATION = "iteration"
USER_EDITED_MID_FLIGHT = "user_edited_mid_flight"
CONSULTED = "consulted"
CHILD_RUN_ATTACH = "child_run_attach"
RUN_ABANDONED = "run_abandoned"
CRYSTALLIZED = "crystallized"
RUN_STARTED = "run_started"
RUN_FINISHED = "run_finished"

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
        EFFECT,
        ITERATION,
        USER_EDITED_MID_FLIGHT,
        CONSULTED,
        CHILD_RUN_ATTACH,
        RUN_ABANDONED,
        CRYSTALLIZED,
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

    def child_run_attach(self, parent_run_id: str, child_run_id: str, node_id: str) -> None:
        self.write(
            CHILD_RUN_ATTACH,
            parent_run_id=parent_run_id,
            child_run_id=child_run_id,
            node_id=node_id,
        )

    def effect(
        self, path: str, *, idempotency_key: str, effect_status: str, compensation_ref: str = ""
    ) -> None:
        self.write(
            EFFECT,
            instance_path=path,
            idempotency_key=idempotency_key,
            effect_status=effect_status,
            compensation_ref=compensation_ref,
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
        """Persist a node output, spilling oversized payloads to a file.

        Returns `(output_ref, inline_preview)`. The preview is what bindings and the
        widget read inline; anything over the boundary leaves a typed stub so a reader
        knows the data exists rather than seeing a truncated string it might parse.
        """
        safe = redact(output)
        encoded = _stable_json(safe)
        if len(encoded.encode("utf-8")) <= MAX_INLINE_OUTPUT_BYTES:
            ref = store.write_output(self.run_id, path, safe)
            return ref, safe
        ref = store.write_output(self.run_id, path, safe)
        stub = {
            "result_omitted": True,
            "reason": "oversize",
            "bytes": len(encoded.encode("utf-8")),
            "output_ref": ref,
        }
        return ref, stub


# ── ledger queries ───────────────────────────────────────────────────────────


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
