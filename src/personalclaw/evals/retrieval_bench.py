"""Retrieval eval harness with per-arm P@k/R@k ablation (EVALUATION-SUBSTRATE §5 / ES-3).

One arm-masked runner, TWO stores, run separately and read-only:

* **knowledge** — :class:`~personalclaw.knowledge.retrieval.HybridRetriever` over
  ``knowledge.db``: the FTS5 keyword arm, the entity-graph arm and the vector arm,
  RRF-fused;
* **memory** — :meth:`~personalclaw.vector_memory.VectorMemoryStore.rank_semantic` over
  ``memory.db``: the same three arm names over the ``0.6·vec + 0.4·kw`` hybrid plus
  MEMORY-GRAPH's graph boost.

The two stores never share a corpus, never cross-query, and neither is written: §5.1's
KNOWLEDGE/MEMORY boundary. Fixtures, qrels and reports are harness mechanics under
``~/.personalclaw/evals/`` — no eval artifact is a memory entry or a knowledge item.

**Arms are named ONCE.** :data:`ARMS` is asserted at import against
``knowledge.retrieval.ARMS`` and ``vector_memory.RECALL_ARMS``, because "the graph arm's
contribution" must mean the same thing in both halves of one report.

**A metric over an empty candidate set is not a score.** ``P@k`` of an empty result list
is ``0/0`` — it is :data:`None` here, with :data:`REASON_NO_CANDIDATES` recorded, and the
cell is ``VERIFIER_ABSENT`` so :func:`~personalclaw.evals.matrix.aggregate` cannot average
it in as a zero. ``R@k`` of an empty result list is a real ``0.0`` (none of the known
relevant ids was found) and is scored as such; ``R@k`` is ``None`` only when the qrels
declare no relevant id at all. That asymmetry is the whole point: "no candidates" and "0
relevant" are different facts and a report that spells them the same way is a report you
cannot act on.

**A mask that gates nothing is detected by the run, not by a test.** Every run includes a
CONTROL cell with every arm masked off. It must retrieve nothing. If it retrieves
anything, the mask never reached the retriever and every per-arm delta in the report is
noise — so the run raises :class:`MaskNotAppliedError` instead of publishing.

**Weak labels, and their bias, stated.** Qrels are mined from events, never synthesized
from the arms' own inputs: knowledge from ``intent_outcomes`` (an ingest-time LLM match
against an item's consolidated content — it does not consult retrieval, so it is not
circular), memory from ``mem_volunteer_events`` via
:meth:`~personalclaw.memory_graph.MemoryGraph.volunteer_qrels` (retrieved-then-used).
Deliberately NOT implemented: §5.2's source (c), synthetic entity queries from the alias
table. Both stores' graph arms take entity mentions as their INPUT, so a qrels set built
from mentions would score the graph arm against its own index and manufacture the very
"+P@5 from the graph arm" finding the report exists to test. See the plan's ES-3 log.
"""

from __future__ import annotations

import hashlib
import json
import logging
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from personalclaw.atomic_write import atomic_write
from personalclaw.evals import pinning, store
from personalclaw.evals.matrix import (
    FAILED,
    PASSED,
    TRIAL_KEY,
    VERIFIER_ABSENT,
    CellResult,
    MatrixSpec,
    aggregate,
    expand_cells,
)
from personalclaw.sel import sel

logger = logging.getLogger(__name__)

# ── the two stores (§5.1) ────────────────────────────────────────────────────
STORE_KNOWLEDGE = "knowledge"
STORE_MEMORY = "memory"
#: Every store the harness can measure. "Both stores" in ES-3's criterion is THIS tuple,
#: and :func:`run_retrieval_bench` refuses a name that is not in it — a typo'd store name
#: that fell through to one default would report half a benchmark as the whole.
STORES = (STORE_KNOWLEDGE, STORE_MEMORY)

# ── the arm vocabulary, asserted against both retrievers ─────────────────────
ARM_KEYWORD = "keyword"
ARM_GRAPH = "graph"
ARM_VECTOR = "vector"
#: The arms both stores expose. Order is the ``match_type`` order.
ARMS = (ARM_KEYWORD, ARM_GRAPH, ARM_VECTOR)

#: ``k`` for P@k/R@k. 5 per §5.3.
DEFAULT_K = 5

#: The ``MatrixSpec.scorer`` value ES-3's criterion names ("reports land in matrices/ via
#: scorer:qrels"). Already enumerated in ``MatrixSpec.scorer``'s own comment — this harness
#: fills a declared slot rather than minting a new scorer word.
SCORER_QRELS = "qrels"

#: The ``kind`` column this harness writes to ``results.tsv``, and — reused deliberately —
#: the ``kind`` its ``table.json`` DECLARES itself to be.
#:
#: 🔴 ``matrices/`` is a shared sink (§5.4), and ES-4's judge bench also writes a
#: ``table.json`` there. It used to claim every run that had one, so this harness's newest run
#: was served as the newest judge bench and the Learning page crashed reading ``wall_secs``
#: off a P@k row. The stamp is what makes ownership explicit; see
#: :data:`personalclaw.evals.judge_bench.TABLE_KIND` for the other half.
LEDGER_KIND = "retrieval_bench"

#: The axis name the ablation delta is grouped by. Shared with ES-7's ablation runner
#: (``overlay.ARM_AXIS``) so ``matrix.aggregate_by`` works on either report unchanged.
ARM_AXIS = "arm_mask"
K_AXIS = "k"

# ── mask spelling ────────────────────────────────────────────────────────────
MASK_SEP = "+"
#: The control mask's name. An empty axis value would render as ``""`` and group with
#: "cell had no arm_mask coordinate" in ``aggregate_by`` — two very different facts.
MASK_NONE = "none"

# ── why a metric is absent ───────────────────────────────────────────────────
REASON_OK = ""
#: Nothing was retrieved: P@k is ``0/0`` and undefined. NOT a zero, NOT a one.
REASON_NO_CANDIDATES = "no_candidates"
#: The qrels declare no relevant id for this query: R@k is ``0/0`` and undefined.
REASON_NO_RELEVANT = "no_relevant"

# ── the dark-ship verdict (§5.3) ─────────────────────────────────────────────
ARM_ENABLE = "enable"
ARM_HOLD = "hold"
ARM_UNMEASURED = "unmeasured"
ARM_VERDICTS = (ARM_ENABLE, ARM_HOLD, ARM_UNMEASURED)

#: Minimum leave-one-out P@k contribution for :data:`ARM_ENABLE`. A module CONSTANT, not
#: config, for ES-4's reason: a floor an operator can lower is not a floor. 0.02 = two
#: P@5 points; below that a personal-scale bench of a few dozen queries cannot tell the
#: arm from one query's tie-break (one slot of five on one query of N moves P@5 by
#: ``1/(5N)``).
MIN_ARM_CONTRIBUTION = 0.02

#: Below this many SCORED queries the verdict is :data:`ARM_UNMEASURED`, whatever the
#: delta says. Mirrors the harvest's ``low_power`` discipline: a number computed from four
#: queries is a number, not evidence.
MIN_SCORED_QUERIES = 5


class RetrievalBenchError(ValueError):
    """A benchmark that cannot be run as specified."""


class EmptyBenchmarkError(RetrievalBenchError):
    """No qrels could be mined, so there is nothing to score.

    Raised rather than returning a zero-query report: a benchmark over no queries has a
    mean of ``None``, and every downstream reader that treats ``None`` as 0.0 would file
    "retrieval scores 0" as a finding about the retriever.
    """


class MaskNotAppliedError(RuntimeError):
    """The all-arms-off control cell retrieved candidates.

    Every per-arm delta in the report would be noise, because the mask never reached the
    retriever. Raised in place of publishing.
    """


class StoreMutatedError(RuntimeError):
    """A store file changed across the run — §5.1's read-only clause, violated."""


# ── mask arithmetic ──────────────────────────────────────────────────────────


def mask_name(arms: "tuple[str, ...] | list[str] | set[str]") -> str:
    """Canonical, JSON-safe axis value for an arm mask (``"keyword+vector"``).

    Always in :data:`ARMS` order, so ``{vector, keyword}`` and ``{keyword, vector}`` are
    ONE axis value and not two rows that look like two experiments.
    """
    picked = tuple(a for a in ARMS if a in set(arms))
    return MASK_SEP.join(picked) if picked else MASK_NONE


def parse_mask(name: str) -> tuple[str, ...]:
    """Inverse of :func:`mask_name`. An unknown arm name is dropped, not guessed."""
    if not name or name == MASK_NONE:
        return ()
    parts = {p.strip() for p in str(name).split(MASK_SEP)}
    return tuple(a for a in ARMS if a in parts)


def ablation_masks() -> tuple[tuple[str, ...], ...]:
    """The masks one ablation run measures, in report order.

    ``()`` control → the full mask → leave-one-out per arm → each arm solo. The
    leave-one-out row is what :func:`contributions` differences against the full mask
    (BrainBench's shape: "the graph arm is worth +31.4 P@5" is a leave-one-out delta); the
    solo row answers the different question "could this arm carry the query alone".
    """
    full = tuple(ARMS)
    masks: list[tuple[str, ...]] = [(), full]
    masks.extend(tuple(a for a in ARMS if a != drop) for drop in ARMS)
    masks.extend((a,) for a in ARMS)
    return tuple(masks)


# ── metrics ──────────────────────────────────────────────────────────────────


def precision_at_k(
    retrieved: "list[str] | tuple[str, ...]",
    relevant: "list[str] | tuple[str, ...] | set[str]",
    k: int,
) -> float | None:
    """``|retrieved@k ∩ relevant| / |retrieved@k|``, or ``None`` when nothing was retrieved.

    ``None`` is the load-bearing return. The denominator is the number of candidates
    ACTUALLY returned, not ``k`` — a store with three items cannot return five, and
    dividing by ``k`` would report a ceiling of 0.6 as if the retriever had missed. With
    zero candidates the ratio is ``0/0``: undefined, and reported as such.
    """
    top = list(retrieved)[: max(0, int(k))]
    if not top:
        return None
    rel = set(relevant)
    return sum(1 for item_id in top if item_id in rel) / len(top)


def recall_at_k(
    retrieved: "list[str] | tuple[str, ...]",
    relevant: "list[str] | tuple[str, ...] | set[str]",
    k: int,
) -> float | None:
    """``|retrieved@k ∩ relevant| / |relevant|``, or ``None`` when nothing is relevant.

    Deliberately NOT symmetric with :func:`precision_at_k`: an empty result list yields a
    real ``0.0`` here (the retriever found none of the known answers — a defined,
    meaningful failure), and ``None`` only when the qrels themselves name no relevant id,
    which is the genuine ``0/0``.
    """
    rel = set(relevant)
    if not rel:
        return None
    top = list(retrieved)[: max(0, int(k))]
    return sum(1 for item_id in top if item_id in rel) / len(rel)


@dataclass(frozen=True)
class QrelsQuery:
    """One judged query: the text, its relevant ids, and where the label came from."""

    query: str
    relevant_ids: tuple[str, ...] = ()
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "relevant_ids": list(self.relevant_ids),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "QrelsQuery":
        return cls(
            query=str(data.get("query", "")),
            relevant_ids=tuple(str(i) for i in (data.get("relevant_ids") or [])),
            source=str(data.get("source", "")),
        )


# Qrels label provenance, recorded per query so a reader can weigh a mined weak label
# against a hand label without re-deriving where it came from.
SOURCE_MINED_INTENT = "mined:intent_outcomes"
SOURCE_MINED_VOLUNTEER = "mined:mem_volunteer_events"
SOURCE_HAND_LABEL = "hand_label"


@dataclass(frozen=True)
class RetrievalBenchmark:
    """``{name, store, corpus_snapshot_ref, queries, created_at}`` (§5.2).

    ``corpus_snapshot_ref`` versions the corpus by REFERENCE (row-id set + content hash),
    never by copying the store: re-running an old benchmark against a grown store reports
    "corpus drifted" instead of silently changing R@k's denominator.
    """

    name: str
    store: str
    queries: tuple[QrelsQuery, ...] = ()
    corpus_snapshot_ref: str = ""
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "store": self.store,
            "queries": [q.to_dict() for q in self.queries],
            "corpus_snapshot_ref": self.corpus_snapshot_ref,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RetrievalBenchmark":
        return cls(
            name=str(data.get("name", "")),
            store=str(data.get("store", "")),
            queries=tuple(QrelsQuery.from_dict(q) for q in (data.get("queries") or [])),
            corpus_snapshot_ref=str(data.get("corpus_snapshot_ref", "")),
            created_at=str(data.get("created_at", "")),
        )

    @property
    def sha256(self) -> str:
        """Canonical hash of the QUERIES + store, for the RunPin's subject hash.

        ``created_at`` and ``corpus_snapshot_ref`` are excluded on purpose: re-mining the
        same labels an hour later, or against a store that grew, must produce the SAME
        subject hash so ``pin_diff`` can attribute a score change to the environment
        rather than to a "new" benchmark. Corpus drift is reported by its own field.
        """
        payload = json.dumps(
            {"store": self.store, "queries": [q.to_dict() for q in self.queries]},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def sources(self) -> dict[str, int]:
        """``{qrels source: query count}`` — the ground truth's PROVENANCE, published.

        Every :class:`QrelsQuery` already carries its ``source``, but only inside
        ``benchmark.json``: the report and the panel showed P@5 with no visible statement
        of which labels produced it. §5.2 names three sources and this harness mines a
        SUBSTITUTE for one of them (``intent_outcomes`` stands in for LEARN-R4's
        ``surfacing_events``), so a reader who cannot see the mix cannot judge the number.
        The substitution was originally forced — that table had no schema and no writer —
        and is now a CHOICE: LEARN-R4 landed it
        (:mod:`personalclaw.learning.surfacing_events`), with ``query``/``entity``/``used``
        columns carried precisely so :func:`mine_knowledge_qrels` can read them, and
        switching this source over is ES-3's own remaining work rather than the table's.
        Counted here rather than in the frontend for
        the reason :func:`latest_retrieval_view` states: a re-derived answer eventually
        disagrees with the runner's.

        A query with no recorded source counts under ``""`` rather than being dropped — a
        census that hides its own unlabelled rows overstates what it knows.
        """
        out: dict[str, int] = {}
        for query in self.queries:
            out[query.source] = out.get(query.source, 0) + 1
        return dict(sorted(out.items()))


@dataclass(frozen=True)
class QueryScore:
    """One (query × mask) measurement — the RAW record the table is derived from."""

    query: str
    mask: str
    retrieved: tuple[str, ...] = ()
    relevant: tuple[str, ...] = ()
    precision: float | None = None
    recall: float | None = None
    reason: str = REASON_OK

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "mask": self.mask,
            "retrieved": list(self.retrieved),
            "relevant": list(self.relevant),
            "precision": self.precision,
            "recall": self.recall,
            "reason": self.reason,
        }


def score_query(
    query: QrelsQuery, retrieved: "list[str] | tuple[str, ...]", *, mask: str, k: int
) -> QueryScore:
    """Score one query under one mask, recording WHY a metric is absent.

    ``reason`` is the whole contract: :data:`REASON_NO_CANDIDATES` when the mask returned
    nothing (precision undefined, recall a real 0.0), :data:`REASON_NO_RELEVANT` when the
    label set is empty (recall undefined). Both reasons can hold at once, in which case
    "no candidates" is reported — it is the fact about the RUN, and the fact about the
    label set is already visible in an empty ``relevant``.
    """
    top = tuple(str(i) for i in list(retrieved)[: max(0, int(k))])
    precision = precision_at_k(top, query.relevant_ids, k)
    recall = recall_at_k(top, query.relevant_ids, k)
    if not top:
        reason = REASON_NO_CANDIDATES
    elif not query.relevant_ids:
        reason = REASON_NO_RELEVANT
    else:
        reason = REASON_OK
    return QueryScore(
        query=query.query,
        mask=mask,
        retrieved=top,
        relevant=tuple(query.relevant_ids),
        precision=precision,
        recall=recall,
        reason=reason,
    )


@dataclass(frozen=True)
class ArmMaskRow:
    """One published row: an arm mask's P@k and R@k over the queries it could score."""

    mask: str
    k: int
    p_at_k: float | None
    r_at_k: float | None
    queries: int
    scored_queries: int
    no_candidate_queries: int
    undefined_recall_queries: int

    def to_dict(self) -> dict:
        return {
            "mask": self.mask,
            "k": self.k,
            "p_at_k": self.p_at_k,
            "r_at_k": self.r_at_k,
            "queries": self.queries,
            "scored_queries": self.scored_queries,
            "no_candidate_queries": self.no_candidate_queries,
            "undefined_recall_queries": self.undefined_recall_queries,
        }


TABLE_COLUMNS = (
    "mask",
    "k",
    "p_at_k",
    "r_at_k",
    "queries",
    "scored_queries",
    "no_candidate_queries",
    "undefined_recall_queries",
)


def _mean(values: "list[float]") -> float | None:
    return (sum(values) / len(values)) if values else None


def build_table(scores: "list[QueryScore]", *, k: int) -> list[ArmMaskRow]:
    """Per-mask P@k / R@k, averaged over the queries where each metric is DEFINED.

    A mask whose every query returned nothing reports ``p_at_k=None`` with
    ``no_candidate_queries == queries`` — a visible "not measured", never a 0.0 that reads
    like a measured failure. ``scored_queries`` is the P@k denominator, so a reader can
    see how many queries a headline number rests on.
    """
    order = [mask_name(m) for m in ablation_masks()]
    buckets: dict[str, list[QueryScore]] = {}
    for score in scores:
        buckets.setdefault(score.mask, []).append(score)
    rows: list[ArmMaskRow] = []
    for name in order + [m for m in sorted(buckets) if m not in order]:
        group = buckets.get(name)
        if not group:
            continue
        precisions = [s.precision for s in group if s.precision is not None]
        recalls = [s.recall for s in group if s.recall is not None]
        rows.append(
            ArmMaskRow(
                mask=name,
                k=k,
                p_at_k=_mean(precisions),
                r_at_k=_mean(recalls),
                queries=len(group),
                scored_queries=len(precisions),
                no_candidate_queries=sum(1 for s in group if s.reason == REASON_NO_CANDIDATES),
                undefined_recall_queries=sum(1 for s in group if s.recall is None),
            )
        )
    return rows


@dataclass(frozen=True)
class ArmContribution:
    """One arm's marginal contribution — the number §5.3 asks for, plus its verdict."""

    arm: str
    full_p_at_k: float | None
    without_p_at_k: float | None
    contribution_p: float | None
    full_r_at_k: float | None
    without_r_at_k: float | None
    contribution_r: float | None
    solo_p_at_k: float | None
    scored_queries: int
    verdict: str
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "arm": self.arm,
            "full_p_at_k": self.full_p_at_k,
            "without_p_at_k": self.without_p_at_k,
            "contribution_p": self.contribution_p,
            "full_r_at_k": self.full_r_at_k,
            "without_r_at_k": self.without_r_at_k,
            "contribution_r": self.contribution_r,
            "solo_p_at_k": self.solo_p_at_k,
            "scored_queries": self.scored_queries,
            "verdict": self.verdict,
            "reasons": list(self.reasons),
        }


def arm_verdict(
    contribution_p: float | None, scored_queries: int, *, has_executor: bool = True
) -> tuple[str, tuple[str, ...]]:
    """The offline verdict a dark-shipped arm gets BEFORE enablement (§5.3).

    :data:`ARM_UNMEASURED` when the arm had no executor at all, when the delta does not
    exist, or when it rests on fewer than :data:`MIN_SCORED_QUERIES` queries — an
    unmeasured arm is never reported as a hold, because "we could not tell" and "we told,
    and it does not earn its keep" lead to different decisions. The no-executor case is
    checked FIRST and independently of the delta: an arm that never ran scores identically
    to its own absence, so its delta is exactly 0.0 and would otherwise read as a
    confident "this arm is worthless".
    """
    reasons: list[str] = []
    if not has_executor:
        reasons.append("no executor: the arm could not run in this process (no embedder?)")
        return ARM_UNMEASURED, tuple(reasons)
    if contribution_p is None:
        reasons.append("no delta: the full or leave-one-out mask scored nothing")
        return ARM_UNMEASURED, tuple(reasons)
    if scored_queries < MIN_SCORED_QUERIES:
        reasons.append(
            f"low power: {scored_queries} scored quer"
            f"{'y' if scored_queries == 1 else 'ies'} < {MIN_SCORED_QUERIES}"
        )
        return ARM_UNMEASURED, tuple(reasons)
    if contribution_p >= MIN_ARM_CONTRIBUTION:
        reasons.append(f"P@k contribution {contribution_p:+.4f} >= {MIN_ARM_CONTRIBUTION}")
        return ARM_ENABLE, tuple(reasons)
    reasons.append(f"P@k contribution {contribution_p:+.4f} < {MIN_ARM_CONTRIBUTION}")
    return ARM_HOLD, tuple(reasons)


def contributions(
    rows: "list[ArmMaskRow]", executors: "dict[str, bool] | None" = None
) -> list[ArmContribution]:
    """Per-arm leave-one-out contribution + verdict, from the published table.

    ``executors`` is :func:`arm_executors`' answer. An arm absent from it defaults to
    "had an executor" so a caller who does not know cannot accidentally mark every arm
    unmeasured — but :func:`run_retrieval_bench` always supplies it.
    """
    executors = executors or {}
    by_mask = {r.mask: r for r in rows}
    full = by_mask.get(mask_name(ARMS))
    out: list[ArmContribution] = []
    for arm in ARMS:
        without = by_mask.get(mask_name(tuple(a for a in ARMS if a != arm)))
        solo = by_mask.get(mask_name((arm,)))
        full_p = full.p_at_k if full else None
        without_p = without.p_at_k if without else None
        full_r = full.r_at_k if full else None
        without_r = without.r_at_k if without else None
        contribution_p = (
            (full_p - without_p) if (full_p is not None and without_p is not None) else None
        )
        contribution_r = (
            (full_r - without_r) if (full_r is not None and without_r is not None) else None
        )
        # The delta's power is bounded by the WEAKER of the two masks it differences.
        scored = min(
            full.scored_queries if full else 0,
            without.scored_queries if without else 0,
        )
        verdict, reasons = arm_verdict(
            contribution_p, scored, has_executor=bool(executors.get(arm, True))
        )
        out.append(
            ArmContribution(
                arm=arm,
                full_p_at_k=full_p,
                without_p_at_k=without_p,
                contribution_p=contribution_p,
                full_r_at_k=full_r,
                without_r_at_k=without_r,
                contribution_r=contribution_r,
                solo_p_at_k=solo.p_at_k if solo else None,
                scored_queries=scored,
                verdict=verdict,
                reasons=reasons,
            )
        )
    return out


# ── the read-only rail ───────────────────────────────────────────────────────


def store_files(db_path: "str | Path") -> list[Path]:
    """The files a write to ``db_path`` would land in.

    The ``-wal`` sidecar is included because in WAL mode that is where a write goes first;
    ``-shm`` is deliberately EXCLUDED — it is a shared-memory coordination file whose bytes
    change on a plain read, so hashing it would fire the rail on every run.
    """
    base = Path(db_path)
    return [base, Path(str(base) + "-wal")]


def store_digest(db_path: "str | Path") -> dict[str, str]:
    """``{path: sha256}`` for the store's files. A missing file digests as ``"absent"``."""
    out: dict[str, str] = {}
    for path in store_files(db_path):
        try:
            out[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            out[str(path)] = "absent"
    return out


@contextmanager
def store_unchanged(db_path: "str | Path") -> "Iterator[dict[str, str]]":
    """Context manager: assert the store is byte-identical across the block.

    The check runs in a ``finally``, so a body that RAISED is still checked — the case an
    edit-and-edit-back implementation strands. The digest is taken AFTER the caller has
    opened the store, because opening it runs ``CREATE TABLE IF NOT EXISTS`` migrations
    that legitimately write; §5.1's clause is about the RETRIEVAL being read-only, and
    measuring the open would make the rail fire on a first-ever run.
    """
    before = store_digest(db_path)
    failure: BaseException | None = None
    try:
        yield before
    except BaseException as exc:  # noqa: BLE001 - re-raised; captured for chaining
        failure = exc
        raise
    finally:
        after = store_digest(db_path)
        drift = sorted(p for p, digest in before.items() if after.get(p) != digest)
        if drift:
            raise StoreMutatedError(
                "retrieval bench wrote to a store (forbidden by §5.1, read-only): "
                + ", ".join(drift)
            ) from failure


def sibling_store_paths(measured: "str | Path") -> list[Path]:
    """The OTHER live store's files, when ``measured`` is a live store under this home.

    §5.1's clause is "never writes to **either**" store, but a run measures only one, so
    :func:`store_unchanged` over the measured path alone left the other store unguarded:
    a knowledge run that wrote to ``memory.db`` — the one write the KNOWLEDGE/MEMORY
    boundary exists to forbid — passed the rail.

    Scoped to stores under :func:`~personalclaw.config.loader.config_dir` on purpose. A
    caller measuring a ``tmp_path`` database is not measuring this home, and digesting the
    real ``~/.personalclaw/memory.db`` to guard it would make a test read a live file that
    a running gateway may be writing — a read-only rail must not itself reach outside the
    home under test.
    """
    from personalclaw.config.loader import config_dir

    try:
        home = Path(config_dir()).resolve()
        target = Path(measured).resolve()
    except OSError:  # pragma: no cover - an unresolvable path guards nothing extra
        return []
    if home not in target.parents:
        return []
    out: list[Path] = []
    for path in (knowledge_db_path(create=False), memory_db_path()):
        resolved = path.resolve() if path.exists() else path
        if resolved != target:
            out.append(resolved)
    return out


@contextmanager
def stores_unchanged(measured: "str | Path") -> "Iterator[dict[str, str]]":
    """Both stores byte-identical across the block — §5.1's read-only clause, in full.

    Guards the measured store AND (when it is this home's live store) the store the run
    never opened, so a cross-store write is a raise rather than a silent pass. Composed
    out of :func:`store_unchanged` rather than re-deriving the digest/drift comparison, so
    "unchanged" has one definition and one error.
    """
    digests: dict[str, str] = {}
    with ExitStack() as stack:
        for path in (Path(measured), *sibling_store_paths(measured)):
            if str(path) in ("", "."):  # a handle with no db_path guards nothing
                continue
            digests.update(stack.enter_context(store_unchanged(path)))
        yield digests


# ── store adapters: one retriever signature, two stores ──────────────────────

#: ``(query, k, arms) -> ranked ids``. The ONE shape the runner knows.
Retriever = Callable[[str, int, "tuple[str, ...]"], "list[str]"]


def knowledge_db_path(*, create: bool = True) -> Path:
    """The live knowledge store's path, through its own resolver.

    NEVER composed locally: ``knowledge.store``'s own docstring records that composing it
    yields ``<home>/knowledge/knowledge.db`` while the real store is
    ``<home>/workspace/knowledge/knowledge.db``, so a locally-built path would benchmark a
    second, empty database and report a retrieval floor of zero.

    ``create=False`` forwards the resolver's own no-mkdir mode, for the read-only rail:
    asking where a store WOULD live must not create its directory.
    """
    from personalclaw.knowledge.store import knowledge_db_path as resolver

    return Path(resolver(create=create))


def memory_db_path() -> Path:
    """The live memory store's path (``<home>/memory.db``).

    Imported function-locally, like ``VectorMemoryStore``'s own default does: a
    module-scope ``from … import config_dir`` binds the function object into THIS module
    and the test suite's home redirect would then have to know about us by name.
    """
    from personalclaw.config.loader import config_dir
    from personalclaw.vector_memory import _DB_FILE

    return Path(config_dir()) / _DB_FILE


def arm_executors(store_kind: str, handle) -> dict[str, bool]:
    """Which arms can actually EXECUTE against this store, right now.

    The vector arm has no executor without an embedder, and a mask that names an arm the
    process cannot run would report "the vector arm contributes nothing" when the truth is
    "the vector arm never ran". Surfaced so the report can say which, and so a caller can
    refuse rather than publish a delta over a dead arm.
    """
    if store_kind == STORE_KNOWLEDGE:
        from personalclaw.knowledge import retrieval as knowledge_retrieval

        retriever = getattr(handle, "_bench_retriever", None)
        has_embedder = bool(retriever and getattr(retriever, "embedder", None))
        return {
            ARM_KEYWORD: True,
            ARM_GRAPH: bool(getattr(knowledge_retrieval, "HybridRetriever", None)),
            ARM_VECTOR: has_embedder,
        }
    if store_kind == STORE_MEMORY:
        return {
            ARM_KEYWORD: True,
            ARM_GRAPH: bool(getattr(handle, "graph_enabled", False)),
            ARM_VECTOR: bool(getattr(handle, "embed_fn", None)),
        }
    raise RetrievalBenchError(f"unknown store {store_kind!r}; expected one of {STORES}")


def knowledge_retriever(knowledge_store) -> Retriever:
    """Adapt :class:`~personalclaw.knowledge.retrieval.HybridRetriever` to :data:`Retriever`.

    Wired with the SAME embedder every production caller passes
    (:func:`~personalclaw.knowledge.get_knowledge_embedder` — the Settings > Models active
    embedding selection). Building a bare ``HybridRetriever(store)`` here would leave the
    vector arm with no executor, and the report would then attribute the arm's silence to
    the arm instead of to the harness.

    Asserts the arm vocabulary at bind time rather than at import of this module alone:
    the retriever is the object that has to honour the mask, so its own ``ARMS`` is what
    must agree.
    """
    from personalclaw.knowledge import get_knowledge_embedder
    from personalclaw.knowledge import retrieval as knowledge_retrieval

    if tuple(knowledge_retrieval.ARMS) != ARMS:  # pragma: no cover - import-time invariant
        raise RetrievalBenchError(
            f"knowledge arm vocabulary drifted: {knowledge_retrieval.ARMS!r} != {ARMS!r}"
        )
    try:
        # `.embed` and the availability check, NOT the UnifiedEmbedder object:
        # `_vector_search` calls ``self.embedder(query)``, so handing it the object raises
        # ``TypeError: 'UnifiedEmbedder' object is not callable`` the first time the vector
        # arm runs. This is the idiom `builtin_tools`/`handlers.knowledge` use.
        unified = get_knowledge_embedder()
        embedder = unified.embed if unified and unified.is_available() else None
    except Exception:  # noqa: BLE001 - no embedder is a reported dead arm, not a crash
        logger.debug("knowledge embedder unavailable", exc_info=True)
        embedder = None
    retriever = knowledge_retrieval.HybridRetriever(knowledge_store, embedder=embedder)
    # Stashed so `arm_executors` can read the bound embedder off the same object the
    # search will use, rather than re-resolving it and possibly getting a different answer.
    knowledge_store._bench_retriever = retriever  # noqa: SLF001

    def _search(query: str, k: int, arms: "tuple[str, ...]") -> list[str]:
        hits = retriever.search(query, limit=k, arms=arms)
        return [str(h.get("id", "")) for h in hits if h.get("id")]

    return _search


def memory_retriever(memory_store) -> Retriever:
    """Adapt :meth:`~personalclaw.vector_memory.VectorMemoryStore.rank_semantic`.

    Binds ``embed_fn`` from the active embedding selection when the caller has not — the
    gateway sets it on its own store handle, and a harness-opened store would otherwise
    run with the vector arm dead.
    """
    from personalclaw import vector_memory

    if tuple(vector_memory.RECALL_ARMS) != ARMS:  # pragma: no cover - import-time invariant
        raise RetrievalBenchError(
            f"memory arm vocabulary drifted: {vector_memory.RECALL_ARMS!r} != {ARMS!r}"
        )
    if getattr(memory_store, "embed_fn", None) is None:
        try:
            from personalclaw.embedding_providers.registry import get_active_embed_fn

            memory_store.embed_fn = get_active_embed_fn()
        except Exception:  # noqa: BLE001 - a dead vector arm is reported, not raised
            logger.debug("memory embed_fn unavailable", exc_info=True)

    def _search(query: str, k: int, arms: "tuple[str, ...]") -> list[str]:
        rows = memory_store.rank_semantic(query, limit=k, arms=arms)
        return [str(r.get("key", "")) for r in rows if r.get("key")]

    return _search


def open_store(store_kind: str):
    """Open the live store for ``store_kind``. Returns ``(handle, db_path)``."""
    if store_kind == STORE_KNOWLEDGE:
        from personalclaw.knowledge.store import KnowledgeStore

        path = knowledge_db_path()
        return KnowledgeStore(str(path)), path
    if store_kind == STORE_MEMORY:
        from personalclaw.vector_memory import VectorMemoryStore

        path = memory_db_path()
        handle = VectorMemoryStore(db_path=path)
        # `init()` is mandatory: `.db` RAISES before it, so every accessor the harness
        # needs (`rank_semantic`, `graph`, the corpus snapshot) would fail on an
        # uninitialized store. It creates the schema, which is a write — and exactly why
        # `store_unchanged`'s digest is taken AFTER the open rather than around it.
        handle.init()
        return handle, path
    raise RetrievalBenchError(f"unknown store {store_kind!r}; expected one of {STORES}")


def retriever_for(store_kind: str, handle) -> Retriever:
    """The :data:`Retriever` for an open store handle."""
    if store_kind == STORE_KNOWLEDGE:
        return knowledge_retriever(handle)
    if store_kind == STORE_MEMORY:
        return memory_retriever(handle)
    raise RetrievalBenchError(f"unknown store {store_kind!r}; expected one of {STORES}")


# ── corpus snapshot reference (§5.2) ─────────────────────────────────────────

_CORPUS_SQL = {
    STORE_KNOWLEDGE: "SELECT id, updated_at FROM items WHERE status = 'active' ORDER BY id",
    STORE_MEMORY: ("SELECT key, updated_at FROM semantic_memory WHERE is_deleted = 0 ORDER BY key"),
}


def corpus_snapshot_ref(store_kind: str, handle) -> str:
    """``"<store>:<rows>:<hash>"`` — the row-id set plus a content hash, not a copy.

    Cheap enough to take on every run, which is the point: the ref is what makes
    :func:`corpus_drifted` able to say "these two numbers were measured over different
    denominators" instead of letting R@k move for an invisible reason.
    """
    sql = _CORPUS_SQL.get(store_kind)
    if not sql:
        raise RetrievalBenchError(f"unknown store {store_kind!r}; expected one of {STORES}")
    db = handle.db
    rows = db.execute(sql).fetchall()
    digest = hashlib.sha256()
    for row in rows:
        digest.update(f"{row[0]}\x1f{row[1]}\x1e".encode("utf-8"))
    return f"{store_kind}:{len(rows)}:{digest.hexdigest()[:16]}"


def corpus_drifted(benchmark: RetrievalBenchmark, current_ref: str) -> bool:
    """Did the corpus change since ``benchmark`` was labelled?

    An unset ref on either side is NOT drift — it is "unknown", and reporting unknown as
    drift would make every hand-authored benchmark permanently suspect.
    """
    if not benchmark.corpus_snapshot_ref or not current_ref:
        return False
    return benchmark.corpus_snapshot_ref != current_ref


# ── qrels mining (§5.2) ──────────────────────────────────────────────────────


def mine_knowledge_qrels(handle) -> list[QrelsQuery]:
    """Weak labels from ``intent_outcomes``: the intent's goal is the query, the matched
    item is a positive.

    Not circular: the intent stage runs at INGEST over an item's consolidated content and
    never consults the retriever, so a positive here is independent of every arm under
    test.
    """
    try:
        rows = handle.db.execute(
            "SELECT intent_name AS q, item_id FROM intent_outcomes "
            "WHERE item_id IS NOT NULL AND TRIM(COALESCE(intent_name, '')) != '' "
            "ORDER BY intent_name, item_id"
        ).fetchall()
    except Exception:  # noqa: BLE001 - a missing table is "no labels", not a crash
        logger.debug("intent_outcomes unavailable", exc_info=True)
        return []
    grouped: dict[str, list[str]] = {}
    for row in rows:
        query = str(row["q"] or "").strip()
        item_id = str(row["item_id"] or "").strip()
        if not query or not item_id:
            continue
        bucket = grouped.setdefault(query, [])
        if item_id not in bucket:
            bucket.append(item_id)
    return [
        QrelsQuery(query=q, relevant_ids=tuple(sorted(ids)), source=SOURCE_MINED_INTENT)
        for q, ids in sorted(grouped.items())
    ]


def mine_memory_qrels(handle) -> list[QrelsQuery]:
    """Weak labels from ``mem_volunteer_events`` — retrieved-then-used (§5.2 source (a)).

    Delegates the used predicate to
    :meth:`~personalclaw.memory_graph.MemoryGraph.volunteer_qrels` so the benchmark's
    ground truth is the SAME arithmetic the live per-arm health panel reads.
    """
    graph = getattr(handle, "graph", None)
    if graph is None or not hasattr(graph, "volunteer_qrels"):
        return []
    return [
        QrelsQuery(query=q, relevant_ids=tuple(refs), source=SOURCE_MINED_VOLUNTEER)
        for q, refs in graph.volunteer_qrels().items()
    ]


def mine_qrels(store_kind: str, handle) -> list[QrelsQuery]:
    """The mined qrels for one store."""
    if store_kind == STORE_KNOWLEDGE:
        return mine_knowledge_qrels(handle)
    if store_kind == STORE_MEMORY:
        return mine_memory_qrels(handle)
    raise RetrievalBenchError(f"unknown store {store_kind!r}; expected one of {STORES}")


def benchmarks_dir() -> Path:
    """``evals/benchmarks/retrieval/`` — where a store's benchmark JSON lives."""
    d = store.benchmarks_dir() / "retrieval"
    d.mkdir(parents=True, exist_ok=True)
    return d


def benchmark_path(store_kind: str) -> Path:
    """The saved benchmark for one store. One file per store — §5.1's "never share a
    corpus" is enforced by the filename, not by a field a caller could set wrong."""
    if store_kind not in STORES:
        raise RetrievalBenchError(f"unknown store {store_kind!r}; expected one of {STORES}")
    return benchmarks_dir() / f"{store_kind}.json"


def save_benchmark(benchmark: RetrievalBenchmark) -> Path:
    path = benchmark_path(benchmark.store)
    atomic_write(path, json.dumps(benchmark.to_dict(), indent=2, sort_keys=True) + "\n")
    return path


def load_benchmark(store_kind: str) -> RetrievalBenchmark | None:
    path = benchmark_path(store_kind)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RetrievalBenchError(f"unreadable benchmark at {path}: {exc}") from exc
    benchmark = RetrievalBenchmark.from_dict(data)
    if benchmark.store != store_kind:
        raise RetrievalBenchError(
            f"benchmark at {path} declares store {benchmark.store!r}, not {store_kind!r}"
        )
    return benchmark


def build_benchmark(store_kind: str, handle, *, name: str = "") -> RetrievalBenchmark:
    """Mine a fresh benchmark for one store, merging any hand labels already on disk.

    Hand labels WIN over a mined label for the same query: a human said which results
    answer that query, and a weak label mined from an event is exactly the thing the hand
    pass exists to correct.
    """
    mined = {q.query: q for q in mine_qrels(store_kind, handle)}
    existing = load_benchmark(store_kind)
    if existing:
        for q in existing.queries:
            if q.source == SOURCE_HAND_LABEL:
                mined[q.query] = q
    return RetrievalBenchmark(
        name=name or f"retrieval-{store_kind}",
        store=store_kind,
        queries=tuple(mined[q] for q in sorted(mined)),
        corpus_snapshot_ref=corpus_snapshot_ref(store_kind, handle),
        created_at=datetime.now(tz=timezone.utc).isoformat(),
    )


# ── the hand-label card (§5.2 source (b)) ────────────────────────────────────

#: How many candidates a card offers per query. §5.2's card is literally "mark which of
#: these 8 results answer this real query of yours".
HAND_LABEL_CANDIDATES = 8
#: How many queries one card offers. §5.2 calls it a 10-minute pass; 5 queries × 8
#: candidates is about that.
HAND_LABEL_QUERIES = 5


def hand_label_card(
    benchmark: RetrievalBenchmark, retriever: Retriever, *, limit: int = HAND_LABEL_QUERIES
) -> dict:
    """The payload of §5.2's hand-labeling pass: head queries × their top candidates.

    Offers the queries whose labels are WEAKEST first — mined before hand-labelled, and
    fewer relevant ids before more — because a human minute spent confirming a query that
    already has five hand labels buys nothing. The candidates come from the FULL arm mask:
    the card asks "which of these answer it", so it must show what the shipped retriever
    actually returns.
    """
    todo = [q for q in benchmark.queries if q.source != SOURCE_HAND_LABEL]
    todo.sort(key=lambda q: (len(q.relevant_ids), q.query))
    picked = todo[: max(0, int(limit))]
    return {
        "store": benchmark.store,
        "benchmark": benchmark.name,
        "candidates_per_query": HAND_LABEL_CANDIDATES,
        "queries": [
            {
                "query": q.query,
                "source": q.source,
                "already_relevant": list(q.relevant_ids),
                "candidates": retriever(q.query, HAND_LABEL_CANDIDATES, tuple(ARMS)),
            }
            for q in picked
        ],
    }


def apply_hand_labels(
    benchmark: RetrievalBenchmark, labels: "dict[str, list[str]]"
) -> RetrievalBenchmark:
    """Fold a completed card back into the benchmark as :data:`SOURCE_HAND_LABEL` queries.

    A query the human marked with NO relevant result is kept, with an empty label set: it
    is a real negative judgement ("none of these answer it"), and dropping it would let
    the benchmark quietly consist only of queries retrieval already handles. Its recall is
    then undefined and reported as such, never as a zero.
    """
    by_query = {q.query: q for q in benchmark.queries}
    for query, relevant in (labels or {}).items():
        key = str(query)
        by_query[key] = QrelsQuery(
            query=key,
            relevant_ids=tuple(sorted({str(i) for i in (relevant or [])})),
            source=SOURCE_HAND_LABEL,
        )
    return RetrievalBenchmark(
        name=benchmark.name,
        store=benchmark.store,
        queries=tuple(by_query[q] for q in sorted(by_query)),
        corpus_snapshot_ref=benchmark.corpus_snapshot_ref,
        created_at=benchmark.created_at,
    )


# ── the run ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RetrievalBenchResult:
    """One store's finished ablation run."""

    bench_id: str
    spec: MatrixSpec
    benchmark: RetrievalBenchmark
    scores: tuple[QueryScore, ...] = ()
    table: tuple[ArmMaskRow, ...] = ()
    contributions: tuple[ArmContribution, ...] = ()
    aggregates: dict = field(default_factory=dict)
    corpus_drifted: bool = False
    executors: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "bench_id": self.bench_id,
            "store": self.benchmark.store,
            "spec": self.spec.to_dict(),
            "table": [r.to_dict() for r in self.table],
            "contributions": [c.to_dict() for c in self.contributions],
            "aggregates": dict(self.aggregates),
            "corpus_drifted": self.corpus_drifted,
            "arm_executors": dict(self.executors),
        }


def new_bench_id(store_kind: str) -> str:
    """A sortable, filesystem-safe run id that names its store."""
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"retrieval-{store_kind}-{stamp}"


def build_spec(benchmark: RetrievalBenchmark, *, k: int) -> MatrixSpec:
    """The ``scorer:qrels`` matrix spec for one store's ablation.

    ``trial_count=1`` on purpose: retrieval under a fixed mask over a fixed corpus is
    DETERMINISTIC, so a second trial re-measures the same number and would only inflate
    ``scored_count`` into looking like more evidence than there is.
    """
    return MatrixSpec(
        subject=benchmark.name,
        axes={
            ARM_AXIS: [mask_name(m) for m in ablation_masks()],
            K_AXIS: [int(k)],
        },
        trial_count=1,
        scorer=SCORER_QRELS,
    )


def _cell_outcome(score: QueryScore) -> tuple[str, float | None]:
    """Map one query score onto the three-state outcome.

    ``precision is None`` (nothing retrieved) ⇒ ``VERIFIER_ABSENT``: there is no precision
    to average, and ``aggregate`` excludes it rather than counting a 0.0 the retriever
    never earned. A retrieved-but-wrong result IS a measured failure and scores 0.0.
    """
    if score.precision is None:
        return VERIFIER_ABSENT, None
    return (PASSED if score.precision > 0 else FAILED), score.precision


def run_retrieval_bench(
    store_kind: str,
    *,
    benchmark: RetrievalBenchmark | None = None,
    handle=None,
    db_path: "str | Path | None" = None,
    k: int = DEFAULT_K,
    bench_id: str = "",
) -> RetrievalBenchResult:
    """Run one store's per-arm P@k/R@k ablation and persist it under ``matrices/<id>/``.

    ``store_kind`` is REQUIRED and there is no default: §5.1 runs the two stores
    SEPARATELY, and a default would make "I ran the retrieval bench" ambiguous about which
    half was measured. Pass ``handle``/``db_path`` to measure a store you already opened
    (tests, and the CLI which opens once to mine and then to score); otherwise the live
    store for ``store_kind`` is opened here.

    Raises before any measurement when the pin is incomplete or the benchmark is empty, and
    AFTER measurement when the control mask retrieved anything
    (:class:`MaskNotAppliedError`) or EITHER store's files changed
    (:class:`StoreMutatedError` — :func:`stores_unchanged` guards the store this run never
    opened too, because §5.1 forbids a write to either one).
    """
    if store_kind not in STORES:
        raise RetrievalBenchError(f"unknown store {store_kind!r}; expected one of {STORES}")
    opened = handle is None
    if opened:
        handle, resolved = open_store(store_kind)
    else:
        resolved = Path(db_path) if db_path else Path(getattr(handle, "db_path", "") or "")
    try:
        benchmark = benchmark or load_benchmark(store_kind) or build_benchmark(store_kind, handle)
        if not benchmark.queries:
            raise EmptyBenchmarkError(
                f"no qrels for the {store_kind} store: nothing has been mined from "
                f"{'intent_outcomes' if store_kind == STORE_KNOWLEDGE else 'mem_volunteer_events'}"
                " and no hand labels exist. Run the hand-label card first."
            )
        retriever = retriever_for(store_kind, handle)
        current_ref = corpus_snapshot_ref(store_kind, handle)
        bench_id = bench_id or new_bench_id(store_kind)
        pin = pinning.compute_pin_for_subject(benchmark.name, benchmark.sha256)
        if not pin.is_complete():
            raise store.PinRequiredError(
                f"refusing to run retrieval bench {bench_id}: incomplete RunPin "
                f"(missing: {', '.join(pin.missing_parts())})"
            )
        spec = build_spec(benchmark, k=k)
        bench_dir = store.matrix_dir(bench_id)
        store.write_matrix_experiment(bench_id, spec.to_dict())
        pinning.write_pin(bench_dir, pin)
        _sel_log(bench_id, store_kind, benchmark, outcome="started")

        scores: list[QueryScore] = []
        cells: list[CellResult] = []
        with stores_unchanged(resolved):
            for combo in expand_cells(spec):
                coords = {key: value for key, value in combo.items() if key != TRIAL_KEY}
                mask = parse_mask(str(coords[ARM_AXIS]))
                cell_k = int(coords[K_AXIS])
                for query in benchmark.queries:
                    score = score_query(
                        query,
                        retriever(query.query, cell_k, mask),
                        mask=str(coords[ARM_AXIS]),
                        k=cell_k,
                    )
                    scores.append(score)
                    outcome, cell_score = _cell_outcome(score)
                    cells.append(
                        CellResult(
                            coords={**coords, "query": query.query},
                            outcome=outcome,
                            score=cell_score,
                            artifact_ref=str(bench_dir),
                        )
                    )

        _assert_mask_applied(scores)
        table = build_table(scores, k=k)
        # Read AFTER the run, off the same objects that ran it: `knowledge_retriever` binds
        # the embedder it will use, and reading before the bind would report the arm dead.
        executors = arm_executors(store_kind, handle)
        contribs = contributions(table, executors)
        aggregates = aggregate(cells)
        store.write_matrix_aggregates(bench_id, aggregates)
        store.write_matrix_trials(bench_id, cells)
        drifted = corpus_drifted(benchmark, current_ref)
        write_bench_artifacts(
            bench_id,
            benchmark=benchmark,
            scores=scores,
            table=table,
            contribs=contribs,
            current_ref=current_ref,
            drifted=drifted,
            executors=executors,
        )
        full_row = next((r for r in table if r.mask == mask_name(ARMS)), None)
        store.append_result(
            {
                "study_id": bench_id,
                "kind": LEDGER_KIND,
                "verdict": _bench_verdict(full_row),
                "score_new": full_row.p_at_k if full_row else None,
                "k": k,
                "ts": datetime.now(tz=timezone.utc).isoformat(),
            },
            pin=pin,
        )
        _sel_log(bench_id, store_kind, benchmark, outcome="completed")
        return RetrievalBenchResult(
            bench_id=bench_id,
            spec=spec,
            benchmark=benchmark,
            scores=tuple(scores),
            table=tuple(table),
            contributions=tuple(contribs),
            aggregates=aggregates,
            corpus_drifted=drifted,
            executors=executors,
        )
    finally:
        if opened:
            closer = getattr(handle, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # noqa: BLE001 - a close fault must not mask a result
                    logger.debug("store close failed", exc_info=True)


def _assert_mask_applied(scores: "list[QueryScore]") -> None:
    """The control cell's vacuity floor: all arms off MUST retrieve nothing.

    Without this, a mask that silently gated nothing would make every mask score
    identically and the whole report would read as "no arm contributes anything" — a
    conclusion about the retriever drawn from a bug in the harness.
    """
    control = [s for s in scores if s.mask == MASK_NONE]
    if not control:
        raise MaskNotAppliedError(
            "no control cell ran: the all-arms-off mask is the only check that the mask "
            "reaches the retriever, so a report without it is unfalsifiable"
        )
    leaked = [s for s in control if s.retrieved]
    if leaked:
        raise MaskNotAppliedError(
            "the all-arms-off control retrieved candidates, so the arm mask never reached "
            f"the retriever and every per-arm delta is noise: {leaked[0].query!r} → "
            f"{list(leaked[0].retrieved)}"
        )


def _bench_verdict(full_row: ArmMaskRow | None) -> str:
    """One ledger word about the RUN's measurability, not about the score.

    Deliberately NOT ``runner._matrix_verdict``'s rule (any failed cell ⇒ ``fail``): a
    personal corpus where some queries score P@5 = 0 is a normal measurement, and calling
    that run "fail" would make the ledger's verdict column mean "retrieval is broken"
    every single time. ``verifier_absent`` when the full mask scored no query at all.
    """
    if full_row is None or full_row.p_at_k is None:
        return VERIFIER_ABSENT
    return "pass"


def render_table_tsv(rows: "list[ArmMaskRow]") -> str:
    """The published table as TSV — same shape as the judge bench's."""
    lines = ["\t".join(TABLE_COLUMNS)]
    for row in rows:
        data = row.to_dict()
        lines.append(
            "\t".join("" if data[col] is None else str(data[col]) for col in TABLE_COLUMNS)
        )
    return "\n".join(lines) + "\n"


def write_bench_artifacts(
    bench_id: str,
    *,
    benchmark: RetrievalBenchmark,
    scores: "list[QueryScore]",
    table: "list[ArmMaskRow]",
    contribs: "list[ArmContribution]",
    current_ref: str,
    drifted: bool,
    executors: "dict[str, bool] | None" = None,
) -> None:
    """Persist the drill-down and the published table beside the matrix artifacts.

    ``observations.json`` is the RAW per-query record; ``table.json``/``table.tsv`` and
    ``contributions.json`` are pure functions of it, so a reader who distrusts a headline
    can recompute it and a reader who distrusts a cell can find the query that produced it.
    """
    d = store.matrix_dir(bench_id)
    atomic_write(d / "benchmark.json", json.dumps(benchmark.to_dict(), indent=2, sort_keys=True))
    atomic_write(
        d / "observations.json",
        json.dumps([s.to_dict() for s in scores], indent=2, sort_keys=True),
    )
    atomic_write(
        d / "table.json",
        json.dumps(
            {
                # The artifact declares its OWNER — `matrices/` is shared with the judge bench.
                "kind": LEDGER_KIND,
                "store": benchmark.store,
                "columns": list(TABLE_COLUMNS),
                "rows": [r.to_dict() for r in table],
                "corpus_snapshot_ref": current_ref,
                "benchmark_corpus_snapshot_ref": benchmark.corpus_snapshot_ref,
                "corpus_drifted": drifted,
                "arm_executors": dict(executors or {}),
                # The ground truth's provenance travels WITH the numbers it produced: a
                # P@5 read without knowing whether its labels were mined or hand-supplied
                # is a number without a claim attached.
                "qrels_sources": benchmark.sources(),
                "queries": len(benchmark.queries),
                "floors": {
                    "min_arm_contribution": MIN_ARM_CONTRIBUTION,
                    "min_scored_queries": MIN_SCORED_QUERIES,
                },
            },
            indent=2,
            sort_keys=True,
        ),
    )
    atomic_write(d / "table.tsv", render_table_tsv(table))
    atomic_write(
        d / "contributions.json",
        json.dumps([c.to_dict() for c in contribs], indent=2, sort_keys=True),
    )


def read_bench_artifact(bench_id: str, name: str) -> object | None:
    """Read one of a run's JSON artifacts back, or ``None`` when it is absent/unreadable."""
    path = store.matrix_dir(bench_id) / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.debug("retrieval bench artifact unreadable: %s", path, exc_info=True)
        return None


def latest_retrieval_view() -> dict:
    """The read-only payload ``GET /api/evals/retrieval`` publishes — BOTH stores.

    Everything arrives DECIDED: the per-mask table, the per-arm contribution, its verdict
    and the floors it was compared against. A frontend that re-derived "is this arm worth
    enabling" would eventually disagree with the runner, and the copy shipping the
    permissive answer would be the UI.

    A store with no run yet is present with ``"run": ""`` rather than omitted — an absent
    key and "not measured yet" read the same to a renderer, and only one of them is a state
    a user can act on.
    """
    stores: dict[str, dict] = {}
    for store_kind in STORES:
        bench_id = latest_bench_id(store_kind)
        stores[store_kind] = {
            "run": bench_id,
            "table": read_bench_artifact(bench_id, "table.json") if bench_id else None,
            "contributions": (
                read_bench_artifact(bench_id, "contributions.json") if bench_id else None
            ),
            "benchmark": read_bench_artifact(bench_id, "benchmark.json") if bench_id else None,
        }
    return {
        "stores": stores,
        "arms": list(ARMS),
        "masks": [mask_name(m) for m in ablation_masks()],
        "control_mask": MASK_NONE,
        "arm_verdicts": list(ARM_VERDICTS),
        "k": DEFAULT_K,
        "floors": {
            "min_arm_contribution": MIN_ARM_CONTRIBUTION,
            "min_scored_queries": MIN_SCORED_QUERIES,
        },
    }


def card_for_store(store_kind: str, *, limit: int = HAND_LABEL_QUERIES) -> dict:
    """Build §5.2's hand-label card against the LIVE store, read-only.

    Opens the store, (re)mines the qrels so the card offers today's weakest-labelled
    queries, and asks the shipped retriever for each one's top candidates under the FULL
    mask — the card says "which of these answer it", so it must show what the retriever
    actually returns. Wrapped in :func:`stores_unchanged`: building a labelling card must
    not write to knowledge.db or memory.db — BOTH, which is why the guard is the
    two-store one and not :func:`store_unchanged` over the opened half.
    """
    handle, resolved = open_store(store_kind)
    try:
        with stores_unchanged(resolved):
            benchmark = build_benchmark(store_kind, handle)
            save_benchmark(benchmark)
            retriever = retriever_for(store_kind, handle)
            card = hand_label_card(benchmark, retriever, limit=limit)
        card["labelled"] = sum(1 for q in benchmark.queries if q.source == SOURCE_HAND_LABEL)
        card["mined"] = len(benchmark.queries) - card["labelled"]
        return card
    finally:
        closer = getattr(handle, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:  # noqa: BLE001 - a close fault must not mask the card
                logger.debug("store close failed", exc_info=True)


def apply_labels_for_store(store_kind: str, labels: "dict[str, list[str]]") -> RetrievalBenchmark:
    """Fold a completed card back into the saved benchmark. Writes ONLY under ``evals/``.

    Refuses an empty mapping: an accepted card that changed nothing would report success
    while the qrels stayed weak, and a user who marked nothing needs to be told so rather
    than shown a green tick.
    """
    if store_kind not in STORES:
        raise RetrievalBenchError(f"unknown store {store_kind!r}; expected one of {STORES}")
    if not labels:
        raise RetrievalBenchError(
            "no labels submitted: mark which results answer each query before saving"
        )
    existing = load_benchmark(store_kind) or RetrievalBenchmark(
        name=f"retrieval-{store_kind}", store=store_kind
    )
    updated = apply_hand_labels(existing, labels)
    save_benchmark(updated)
    return updated


def latest_bench_id(store_kind: str = "") -> str:
    """The newest retrieval-bench run id, optionally for one store. ``""`` when none.

    Ownership comes from the ARTIFACT's own :data:`LEDGER_KIND` stamp, not from the run id —
    the id is only how the runs SORT (it is timestamped) and which store they belong to. A
    filename rule would strand any run whose id a caller chose, and would still mistake a
    third writer of ``table.json`` for this one.
    """
    if store_kind and store_kind not in STORES:
        raise RetrievalBenchError(f"unknown store {store_kind!r}; expected one of {STORES}")
    try:
        candidates = sorted(store.matrices_dir().iterdir(), key=lambda p: p.name)
    except OSError:
        return ""
    for run_dir in reversed(candidates):
        table = _read_table_if_ours(run_dir)
        if table is None:
            continue
        if store_kind and str(table.get("store", "")) != store_kind:
            continue
        return run_dir.name
    return ""


def _read_table_if_ours(run_dir: Path) -> dict | None:
    """This run's ``table.json`` if THIS harness wrote it, else ``None``."""
    if not run_dir.is_dir():
        return None
    path = run_dir / "table.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.debug("unreadable retrieval table at %s", path, exc_info=True)
        return None
    if not isinstance(data, dict) or data.get("kind") != LEDGER_KIND:
        return None
    return data


def _sel_log(
    bench_id: str, store_kind: str, benchmark: RetrievalBenchmark, *, outcome: str
) -> None:
    """SEL-log a bench lifecycle event (§10). Best-effort — never breaks a run."""
    try:
        sel().log_api_access(
            caller=f"retrieval-bench:{bench_id}",
            operation="evals_retrieval_bench",
            outcome=outcome,
            source="evals",
            resources=(
                f"store={store_kind} subject={benchmark.name} "
                f"queries={len(benchmark.queries)} scorer={SCORER_QRELS}"
            ),
        )
    except Exception:  # noqa: BLE001 - telemetry must never cost a run
        logger.debug("SEL retrieval-bench log failed", exc_info=True)
