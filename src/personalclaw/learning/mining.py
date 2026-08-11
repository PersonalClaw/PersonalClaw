"""The PRODUCERS behind §3.2's detectors — embeddings, intent inversion, positive-path traces.

``learning.detectors`` holds pure verdicts and is deliberately fed from outside. Three of its
inputs had no producer at all, which is a specific and nasty failure shape: the detector is written,
tested, and reachable, but the argument it needs is never computed, so it can only ever return the
"nothing to see" branch. `similarity_verdict` is the clearest case — it consumes
``matches: list[tuple[run_id, cosine, age_days]]`` and nothing in the tree built that list, so "you
have built this three times" could not fire however many times you built it.

This module is the producer side, and only that:

* :func:`similar_run_matches` — embed a run's plan/spec through the EXISTING embedding substrate
  (``vector_memory`` via ``MemoryService``) and return the triples ``similarity_verdict`` wants.
* :func:`invert_intent` — the §3.2 intent-inversion pass: what the user ASKED for versus what the
  run actually DID, derived from the run's own journal.
* :func:`positive_path_candidates` — §3.2's positive half: recurring SUCCESSFUL step sequences mined
  from the Run Ledger, routed to the same PENDING proposal path the negative signals use.

**A registry miss is a typed reason, never an empty list.** The reason `similarity_verdict` was safe
to leave unwired is also what makes wiring it dangerous: "no similar plans" and "I could not embed
anything, so I looked at nothing" produce the identical verdict. The first is a calibrated detector;
the second is a blind one wearing its clothes. Every path that cannot resolve an embedding returns a
:class:`Miss` reason and records it, so a box with no embedder reads as UNAVAILABLE rather than as a
library with no repetition in it.

Nothing here installs. The proposal paths file PENDING rows through ``learning.proposals.enqueue``,
the same human gate every other inferred artifact clears.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

#: Tag every run-spec embedding carries. The episodic table is shared with conversation memory, so
#: the similarity search MUST be tag-filtered: an unfiltered nearest-neighbour query would score a
#: chat message against a plan and call it a prior run.
RUN_SPEC_TAG = "run_spec"

#: Ledger ``detail`` prefix for every row this module writes, mirroring
#: ``template_gate.LEDGER_PREFIX``. Three gates now share ``flush_records``; without a per-producer
#: prefix "why did nothing get mined" is a question with three indistinguishable answers.
LEDGER_PREFIX = "mining"

#: Minimum successful steps before a trace is a candidate sequence. Below this it is a command, the
#: same floor ``detectors.MIN_PLAN_STEPS`` applies to plans — mined from the other direction.
MIN_TRACE_STEPS = 2

#: How many runs must share a normalized successful trace before it is template-worthy. §3.2's
#: "gated by min_frequency": one success is an event, a repeat is a pattern.
MIN_TRACE_FREQUENCY = 3

#: How many recent runs the positive-path scan reads. Bounded because this runs on a real box with a
#: real ledger, and an unbounded scan turns a background pass into a stall.
TRACE_SCAN_LIMIT = 60

#: Cap on runs embedded/compared in one similarity pass, for the same reason.
SIMILARITY_SCAN_LIMIT = 24


class Miss(str, Enum):
    """Why a producer returned nothing. The typed alternative to a silent empty list.

    Each value names a DIFFERENT fix, which is the entire reason they are separate: ``NO_EMBEDDER``
    is "install an embedding model", ``NOT_INDEXED`` is "this run's spec was never embedded",
    ``EMPTY_SPEC`` is "the run carries no plan text to embed", and ``STORE_UNAVAILABLE`` is a
    degraded memory service. Collapsing them into a bare ``[]`` is what would let a permanently
    blind detector look calibrated.
    """

    #: No memory service was injected, or its store is not wired.
    STORE_UNAVAILABLE = "registry_miss_store_unavailable"
    #: A store is present but no embedder is configured — vector search degrades to nothing here.
    NO_EMBEDDER = "registry_miss_no_embedder"
    #: The run has no plan/spec text worth embedding.
    EMPTY_SPEC = "registry_miss_empty_spec"
    #: The spec embedded, but the query returned no vector-bearing neighbours: this run's own spec
    #: (and any prior) is absent from the index. Distinct from "searched and found nothing similar".
    NOT_INDEXED = "registry_miss_not_indexed"
    #: The journal could not be read.
    NO_JOURNAL = "registry_miss_no_journal"


@dataclass
class MatchSet:
    """The producer's result: the triples plus WHY they are empty when they are.

    ``matches`` is exactly what :func:`personalclaw.learning.detectors.similarity_verdict` consumes.
    ``miss`` is populated only when the emptiness is a capability gap rather than an observation, so
    a caller can tell "no repetition" from "no eyes" — and :meth:`blind` says which.
    """

    matches: list[tuple[str, float, float]] = field(default_factory=list)
    miss: Miss | None = None
    #: How many spec-tagged neighbours were examined before filtering. 0 with no miss means the
    #: index is genuinely empty of other runs.
    examined: int = 0

    @property
    def blind(self) -> bool:
        """True when emptiness is a capability gap, not a measurement."""
        return self.miss is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "matches": [list(m) for m in self.matches],
            "miss": self.miss.value if self.miss else "",
            "examined": self.examined,
            "blind": self.blind,
        }


def record_miss(miss: Miss, *, detail: str = "") -> bool:
    """Log a registry miss to the shared flush ledger. Returns True iff a row was written.

    Best-effort, like ``template_gate.record_skip``: observability must never fail the pass that
    produced the signal. But it is NOT optional-by-design — the whole point of the typed reason is
    that a blind detector leaves a trace, so the failure to record is logged too.
    """
    try:
        from personalclaw.learning.gate import Cadence
        from personalclaw.learning.staging import FlushOutcome, get_store

        get_store().record_flush(
            cadence=Cadence.RUN_END.value,
            outcome=FlushOutcome.FLUSH_SKIPPED,
            detail=f"{LEDGER_PREFIX}: {miss.value}{': ' + detail if detail else ''}",
        )
        return True
    except Exception:
        logger.debug("mining: recording miss %s failed", miss.value, exc_info=True)
        return False


def miss_counts(*, days: int = 30) -> dict[str, int]:
    """Counts per typed miss reason over a window — "how blind was the detector this month".

    Reads back only rows :func:`record_miss` wrote (via the ``mining:`` prefix), so the capture
    gate's denials and the template gate's skips cannot inflate a miss reason.
    """
    try:
        from personalclaw.learning.staging import FlushOutcome, get_store

        store = get_store()
        since = time.time() - max(1, days) * 86400
        with store._cursor() as cur:  # noqa: SLF001 — same-package read of the store's connection
            rows = cur.execute(
                "SELECT detail FROM flush_records WHERE outcome = ? AND created_ts >= ?;",
                (FlushOutcome.FLUSH_SKIPPED.value, since),
            ).fetchall()
    except Exception:
        logger.debug("mining: miss_counts unavailable", exc_info=True)
        return {}

    known = {m.value for m in Miss}
    counts: dict[str, int] = {}
    for row in rows:
        detail = str(row[0] or "")
        if not detail.startswith(LEDGER_PREFIX + ":"):
            continue
        reason = detail[len(LEDGER_PREFIX) + 1 :].strip().split(":", 1)[0].strip()
        if reason in known:
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


# ── clause A: the per-spec embedding producer ──


def spec_text(run: Any, *, journal: Any = None) -> str:
    """The embeddable text of a run's plan: declared intent + SYNTHESIZED intent + step names.

    Intent alone is too thin (two different plans phrased alike collide) and a full transcript is
    too noisy (outputs dominate the vector). The plan SHAPE — what it set out to do plus the steps
    it took to do it — is the thing §3.2 wants matched, and it is stable across reruns.

    The synthesized (inverted) intent is included deliberately, and it is what makes the corpus work
    on real data: a run launched from a template carries a terse or empty ``intent``, so an index
    keyed on the declared field alone would hold nothing for exactly the runs that repeat most.
    Inverting execution back into user register (:func:`invert_intent`) gives every run a comparable
    sentence, which is the §3.2 design — synthesize, then embed and cluster.
    """
    parts: list[str] = []
    intent = str(getattr(run, "intent", "") or "").strip()
    if intent:
        parts.append(intent)
    name = str(getattr(run, "workflow_name", "") or "").strip()
    if name:
        parts.append(f"workflow: {name}")
    steps = _step_names(run, journal=journal)
    synthesized = _synthesize_intent(name, steps)
    if synthesized:
        parts.append(synthesized)
    if steps:
        parts.append("steps: " + ", ".join(steps))
    return "\n".join(parts).strip()


def _step_names(run: Any, *, journal: Any = None) -> list[str]:
    """Node ids of the run's completed steps, in ledger order, de-duplicated."""
    journal = journal or _journal()
    if journal is None:
        return []
    try:
        events = journal.ledger(str(getattr(run, "id", "") or ""), kinds={journal.STEP_COMPLETED})
    except Exception:
        logger.debug("mining: ledger read failed for %s", getattr(run, "id", "?"), exc_info=True)
        return []
    seen: list[str] = []
    for rec in events:
        node = str(rec.get("node_id") or "").strip()
        if node and node not in seen:
            seen.append(node)
    return seen


def _journal() -> Any:
    try:
        from personalclaw.workflows import journal as journal_mod

        return journal_mod
    except Exception:  # pragma: no cover - import failure is not a runtime path
        logger.debug("mining: journal unavailable", exc_info=True)
        return None


def index_run_spec(run: Any, service: Any, *, journal: Any = None) -> bool:
    """Embed and store this run's spec so FUTURE runs can match against it.

    The write half of the similarity detector, and the reason ``NOT_INDEXED`` is a real reason: a
    search-only implementation would query an index nothing ever populated and report "no priors"
    forever. Tagged :data:`RUN_SPEC_TAG` so the search can exclude conversation memory.

    Returns True iff a row was written. Never raises — a terminal run must not fail over a vector.
    """
    text = spec_text(run, journal=journal)
    if not text:
        return False
    if service is None or not getattr(service, "has_vector", False):
        return False
    run_id = str(getattr(run, "id", "") or "")
    # The run id leads the text, and that ordering is load-bearing. `write_episodic` dedupes on the
    # lowercased first 80 chars (vector_memory.py:1957) and REJECTS a match — so two runs of
    # the same template, whose spec text is by definition identical, would index exactly ONCE.
    # Measured: three identical runs produced one row, and the detector then read "1 similar
    # plan; 2 needed" on textbook repetition. Prefixing the unique run id makes each run its own row
    # without weakening a dedup other callers depend on. The id is a stable token, so it costs the
    # embedding nothing that the plan body does not dominate.
    body = f"run {run_id}\n{text}"
    try:
        return bool(
            service.write_episodic(
                body,
                conversation_id=run_id,
                tags=[RUN_SPEC_TAG, f"run:{run_id}"],
                importance=0.4,
                source="consolidation",
            )
        )
    except Exception:
        logger.debug("mining: indexing spec for %s failed", run_id, exc_info=True)
        return False


def similar_run_matches(
    run: Any,
    service: Any,
    *,
    journal: Any = None,
    limit: int = SIMILARITY_SCAN_LIMIT,
    now: float | None = None,
) -> MatchSet:
    """Produce ``(run_id, cosine, age_days)`` triples for ``similarity_verdict``.

    This is the missing producer. It embeds the run's own spec through the configured embedding
    substrate, searches the spec-tagged episodic index for prior runs, and converts each neighbour
    into the triple the verdict consumes. The run's own row is excluded — a plan is always maximally
    similar to itself, and counting it would let a single run clear ``min_priors`` on its own.

    Every empty return carries a :class:`Miss`, recorded to the ledger. That asymmetry is the point:
    an unembeddable spec must not look like an unrepeated one.
    """
    now = time.time() if now is None else now
    run_id = str(getattr(run, "id", "") or "")

    text = spec_text(run, journal=journal)
    if not text:
        record_miss(Miss.EMPTY_SPEC, detail=run_id)
        return MatchSet(miss=Miss.EMPTY_SPEC)
    if service is None or not getattr(service, "has_vector", False):
        record_miss(Miss.STORE_UNAVAILABLE, detail=run_id)
        return MatchSet(miss=Miss.STORE_UNAVAILABLE)
    # `can_vector_search` is store-presence AND embedder-presence; `has_vector` is only the former.
    # Without the embedder the search silently degrades to FTS, whose scores are NOT cosines — a
    # keyword hit scored as 0.9 similarity would fabricate a prior.
    if not getattr(service, "can_vector_search", False):
        record_miss(Miss.NO_EMBEDDER, detail=run_id)
        return MatchSet(miss=Miss.NO_EMBEDDER)

    try:
        # mmr=False is load-bearing, not a tuning preference. MMR reranking exists to DIVERSIFY
        # recall results, so it actively suppresses near-duplicates — and near-duplicates are the
        # entire signal here. Measured: three runs of the identical plan came back as ONE match, so
        # `similarity_verdict` reported "1 similar plan; 2 needed before calling it a pattern" and
        # the detector stayed silent on textbook repetition. A diversity-reranked repetition counter
        # is guaranteed to undercount exactly the cases it exists to catch.
        rows = service.search_episodic(
            query_text=text, limit=max(1, limit), tag_filter=[RUN_SPEC_TAG], mmr=False
        )
    except Exception:
        logger.debug("mining: similarity search failed for %s", run_id, exc_info=True)
        record_miss(Miss.STORE_UNAVAILABLE, detail=run_id)
        return MatchSet(miss=Miss.STORE_UNAVAILABLE)

    matches: list[tuple[str, float, float]] = []
    scored = 0
    for row in rows or []:
        other = str(row.get("conversation_id") or "").strip()
        if not other or other == run_id:
            continue
        cosine = row.get("cosine_sim")
        if cosine is None:
            # An FTS-shaped row (no cosine) reached us despite the embedder check. Skipped rather
            # than coerced: a fabricated similarity is worse than a missing one.
            continue
        scored += 1
        matches.append((other, float(cosine), _age_days(row.get("created_at"), now)))

    if not rows:
        # Nothing spec-tagged came back at all — the index has no run specs in it, which is a
        # capability gap (nothing indexed yet), not the observation "your plans do not repeat".
        record_miss(Miss.NOT_INDEXED, detail=run_id)
        return MatchSet(miss=Miss.NOT_INDEXED)
    return MatchSet(matches=matches, examined=scored)


def _age_days(created_at: Any, now: float) -> float:
    """Age in days of an ISO timestamp, clamped at 0. Unparseable → 0.0 (treated as fresh).

    Fresh-on-unparseable is the safe direction here: the window filter DROPS stale matches, so
    guessing "old" would silently discard a real prior, while guessing "new" leaves it to the
    cosine threshold, which is the filter that actually judges sameness.
    """
    from datetime import datetime, timezone

    raw = str(created_at or "").strip()
    if not raw:
        return 0.0
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed.timestamp()) / 86400.0)


# ── clause B: intent inversion ──


@dataclass
class Inversion:
    """§3.2's intent inversion: the run's own execution read back as a user-register intent.

    The normal direction is intent → plan. This is the INVERSE — plan → intent — which is what makes
    it a usable corpus: a run's declared ``intent`` may be terse, absent, or written in machine
    register, while its node names always describe what it actually did. ``synthesized`` is the
    canonical user-register sentence built from (workflow name + node names), and it is what feeds
    the embedding/clustering path (:func:`spec_text` uses it), because clustering runs by what they
    DID finds repetition that clustering by what they were ASKED never sees.

    ``asked`` is the declared intent, kept alongside so the inversion is checkable: ``drift`` is the
    fraction of asked-for content no executed step mentions (0.0 = the run covered the ask, 1.0 =
    it shares no vocabulary with it), and ``unaddressed`` names the specific missing terms, because
    a scalar alone is untunable — "which part did it skip" is the actionable half.
    """

    run_id: str
    asked: str = ""
    did: str = ""
    synthesized: str = ""
    drift: float = 0.0
    unaddressed: list[str] = field(default_factory=list)
    miss: Miss | None = None

    @property
    def inverted(self) -> bool:
        """True when the run drifted far enough from the ask to be worth a signal."""
        return self.miss is None and self.drift >= INVERSION_DRIFT_THRESHOLD

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "asked": self.asked,
            "did": self.did,
            "synthesized": self.synthesized,
            "drift": round(self.drift, 4),
            "unaddressed": list(self.unaddressed),
            "inverted": self.inverted,
            "miss": self.miss.value if self.miss else "",
        }


#: Drift at or above which a run counts as having done something other than what was asked. Set high
#: on purpose: node ids are terse and legitimately share little vocabulary with prose, so a low bar
#: would flag every healthy run.
INVERSION_DRIFT_THRESHOLD = 0.75

#: Words carrying no topical signal. A drift score computed over these measures phrasing, not
#: intent.
_STOPWORDS = frozenset("""
    a an and are as at be been but by can do does for from had has have how i if in into is it its
    me my no not of on or our so than that the their them then there these they this to us was we
    were what when which who will with would you your please just also make made need needs want
    """.split())

_WORD = re.compile(r"[a-z0-9]+")


def _topical_words(text: str) -> set[str]:
    """Content words of ``text``, lowercased, stopword-stripped, ≥3 chars."""
    return {
        w for w in _WORD.findall(str(text or "").lower()) if len(w) >= 3 and w not in _STOPWORDS
    }


def invert_intent(run: Any, *, journal: Any = None) -> Inversion:
    """Mine one run's journal for the inverse signal: asked-for versus done.

    A journal-mining pass, not a call site — the run's ledger already records every step it took, so
    the "what it did" half needs no new instrumentation. The canonical ``did`` string is synthesized
    deterministically from node ids plus the workflow name (§3.2 describes a cheap synthesis pass;
    a model call per terminal run is the cost this whole section exists to avoid).

    Never raises. An unreadable journal returns a typed ``NO_JOURNAL`` miss rather than a drift of
    0.0 — scoring a run as perfectly on-target because we could not read it is exactly the blind
    calibration this module refuses.
    """
    run_id = str(getattr(run, "id", "") or "")
    asked = str(getattr(run, "intent", "") or "").strip()
    journal = journal or _journal()
    if journal is None:
        record_miss(Miss.NO_JOURNAL, detail=run_id)
        return Inversion(run_id=run_id, asked=asked, miss=Miss.NO_JOURNAL)

    steps = _step_names(run, journal=journal)
    name = str(getattr(run, "workflow_name", "") or "").strip()
    did = f"ran {name or 'a workflow'}: " + (", ".join(steps) if steps else "no steps completed")
    synthesized = _synthesize_intent(name, steps)

    # The synthesized intent stands alone: it is derived from execution, so it exists even for a run
    # launched with no declared intent — which is exactly the case the corpus most needs, since a
    # run with no intent contributes nothing to an intent-keyed index otherwise. Drift, however,
    # needs both halves, and stays 0.0 with nothing to have drifted FROM.
    if not asked:
        return Inversion(run_id=run_id, did=did, synthesized=synthesized)

    asked_words = _topical_words(asked)
    did_words = _topical_words(did)
    if not asked_words:
        return Inversion(run_id=run_id, asked=asked, did=did, synthesized=synthesized)
    unaddressed = sorted(asked_words - did_words)
    drift = len(unaddressed) / len(asked_words)
    return Inversion(
        run_id=run_id,
        asked=asked,
        did=did,
        synthesized=synthesized,
        drift=drift,
        unaddressed=unaddressed[:12],
    )


def _synthesize_intent(workflow_name: str, steps: list[str]) -> str:
    """Build the user-register sentence for what a run DID, from its shape alone.

    Deterministic and model-free (§3.2 calls for a *cheap* pass; a completion per terminal run is
    the cost this section exists to avoid). Node ids are de-slugged into words so the sentence
    shares a vocabulary with prose intents — ``fetch_data`` and "fetch the data" must land near
    each other in the index, and an un-deslugged id would embed as an opaque token.
    """
    if not steps and not workflow_name:
        return ""
    readable = [re.sub(r"[_\-.]+", " ", str(s or "")).strip().lower() for s in steps]
    readable = [s for s in readable if s]
    subject = re.sub(r"[_\-.]+", " ", workflow_name).strip().lower()
    if not readable:
        return f"Carry out {subject}." if subject else ""
    body = ", then ".join(readable)
    lead = f"Using {subject}, " if subject else ""
    return f"{lead}{body}.".capitalize()


# ── clause C: positive-path trace mining ──


@dataclass
class Trace:
    """One recurring SUCCESSFUL step sequence — the positive half of §3.2's signal set."""

    signature: str
    steps: list[str] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    workflow_name: str = ""

    @property
    def frequency(self) -> int:
        return len(self.run_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "signature": self.signature,
            "steps": list(self.steps),
            "run_ids": list(self.run_ids),
            "workflow_name": self.workflow_name,
            "frequency": self.frequency,
        }


def trace_signature(steps: list[str]) -> str:
    """Normalize a step sequence into a joinable key.

    ORDER-PRESERVING: "fetch → transform → publish" and "publish → transform → fetch" are different
    procedures, and a set-based key would merge them into one template that matches neither. Node
    ids are lowercased and epoch/index suffixes stripped so the same step across runs collapses.
    """
    parts = []
    for step in steps:
        norm = re.sub(r"[#:\[]\d+\]?$", "", str(step or "").strip().lower())
        norm = re.sub(r"\s+", "_", norm)
        if norm:
            parts.append(norm)
    return " → ".join(parts)


def positive_path_candidates(
    *,
    workflow_name: str = "",
    min_frequency: int = MIN_TRACE_FREQUENCY,
    min_steps: int = MIN_TRACE_STEPS,
    limit: int = TRACE_SCAN_LIMIT,
    journal: Any = None,
    store: Any = None,
) -> tuple[list[Trace], Miss | None]:
    """Scan recent SUCCESSFUL runs for recurring step sequences.

    §3.2's positive-path mining: zero model calls, gated by ``min_frequency`` AND outcome quality
    (only runs whose terminal status is COMPLETE contribute — a sequence mined from a failed run is
    a recipe for failing). Returns ``(traces, miss)``; the miss is typed for the same reason as
    everywhere else in this module.
    """
    journal = journal or _journal()
    if journal is None:
        record_miss(Miss.NO_JOURNAL)
        return [], Miss.NO_JOURNAL
    if store is None:
        try:
            from personalclaw.workflows import store as store_mod

            store = store_mod
        except Exception:
            logger.debug("mining: run store unavailable", exc_info=True)
            record_miss(Miss.STORE_UNAVAILABLE)
            return [], Miss.STORE_UNAVAILABLE
    try:
        runs, _total = store.list_runs(workflow_name=workflow_name, limit=max(1, limit))
    except Exception:
        logger.debug("mining: listing runs failed", exc_info=True)
        record_miss(Miss.STORE_UNAVAILABLE)
        return [], Miss.STORE_UNAVAILABLE

    grouped: dict[str, Trace] = {}
    for run in runs or []:
        if not _is_successful(run):
            continue
        steps = _step_names(run, journal=journal)
        if len(steps) < max(1, min_steps):
            continue
        sig = trace_signature(steps)
        if not sig:
            continue
        trace = grouped.get(sig)
        if trace is None:
            trace = Trace(
                signature=sig,
                steps=steps,
                workflow_name=str(getattr(run, "workflow_name", "") or ""),
            )
            grouped[sig] = trace
        run_id = str(getattr(run, "id", "") or "")
        if run_id and run_id not in trace.run_ids:
            trace.run_ids.append(run_id)

    hits = [t for t in grouped.values() if t.frequency >= max(1, min_frequency)]
    hits.sort(key=lambda t: (-t.frequency, t.signature))
    return hits, None


def _is_successful(run: Any) -> bool:
    """Outcome-quality gate: only a COMPLETE terminal run contributes a positive trace."""
    status = getattr(run, "status", "")
    return str(getattr(status, "value", status) or "").lower() == "complete"


def file_positive_trace(trace: Trace, *, session_key: str = "") -> str:
    """File one mined trace as a PENDING ``template`` proposal. Returns its id or ``""``.

    Routed through the SAME ``proposals.enqueue`` draft path the gate's accepted candidates use, so
    the positive and negative halves land in one queue a human reviews once. ``occurrences`` carries
    the real frequency, so the queue's evidence floor judges a mined pattern on its actual
    repetition rather than on a default.
    """
    from personalclaw.learning.proposals import Kind, enqueue

    body = (
        f"These {trace.frequency} successful runs of `{trace.workflow_name or 'ad-hoc work'}` all "
        f"took the same path:\n\n"
        + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(trace.steps))
        + "\n\nMining what already works: a repeated successful sequence is a procedure worth "
        "naming, not a coincidence.\n\nRuns: " + ", ".join(trace.run_ids[:10])
    )
    try:
        _verdict, prop = enqueue(
            kind=Kind.TEMPLATE.value,
            title=f"Recurring successful path: {trace.signature}"[:120],
            body=body,
            target=trace.signature,
            provenance="inferred",
            session_key=session_key,
            run_id=trace.run_ids[0] if trace.run_ids else "",
            source_cadence="run_end",
            evidence_strength="correlated",
            confidence=min(0.9, 0.4 + 0.1 * trace.frequency),
            tags=["positive_path", "trace_mining"],
            occurrences=trace.frequency,
        )
    except Exception:
        logger.debug("mining: filing trace %s failed", trace.signature, exc_info=True)
        return ""
    return prop.id if prop is not None else ""
