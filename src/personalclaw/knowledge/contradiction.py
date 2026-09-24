"""Conflict detection at persist time — flag at ingest, not at query.

The reason this runs on the WRITE path rather than the read path: by the time a contradiction
surfaces during retrieval, something has already built on one side of it. A claim that entered
the store unflagged has been retrieved, cited, and folded into a synthesis, and unwinding that
means finding everything downstream. Flagging at ingest costs one deterministic pass per write.

Two tiers, cheapest first:

**Deterministic (zero cost).** Two claims sharing a SUBJECT and PREDICATE with different
OBJECTS, or a subject and object with opposite predicates, conflict — no model needed. This is
what §2.1's structured claims exist to make possible: the same test over free text needs an LLM,
over `{subject, predicate, object}` it is a comparison.

**Fast-model (metered).** For claims the deterministic tier cannot separate, a shortlist of
semantically-near items goes to one background-tier call. This module contributes only the
ranked, capped `shortlist` — which is what keeps marginal cost independent of store size. The
call itself lives in the `contradiction-review` workflow's judge node, fed the candidates
`knowledge_persist_provider._unsettled_candidates` builds from that shortlist.

**Both claims are kept, always.** A conflict record carries a source-precedence ladder
(`user > compiled > timeline > external`) so a reader knows which to prefer — but the losing
claim stays, with its citation. Silently picking one is how a store becomes confidently wrong:
the discarded claim was evidence, and its absence is unrecoverable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Source precedence, strongest first. A user's own statement outranks anything the system
#: compiled, which outranks a dated timeline entry, which outranks something scraped.
SOURCE_PRECEDENCE = ("user", "compiled", "timeline", "external")

#: Below this similarity two claims about the same subject are treated as unrelated, so
#: different numbers in them are two facts rather than a conflict. Measured in
#: `learning/proposals.py`: without it, one number-bearing rule collapsed an entire queue.
NUMBER_CONFLICT_MIN_SIM = 0.75

#: Candidate cap for the model tier. Marginal cost has to be independent of store size.
MAX_CONFLICT_CANDIDATES = 30

#: Most conflicts one pass will report. A write that produced 200 conflict records has found a
#: systematic problem, and 200 rows is a worse way to say that than one capped list.
MAX_CONFLICTS_PER_PASS = 10

#: Predicate pairs that assert opposite things. Ordered pairs, checked both ways.
_OPPOSITE_PREDICATES = (
    ("is", "is not"),
    ("has", "lacks"),
    ("supports", "blocks"),
    ("increases", "decreases"),
    ("enables", "prevents"),
    ("requires", "forbids"),
    ("includes", "excludes"),
    ("causes", "prevents"),
)

#: Auxiliaries a negation drags in. Dropped for the polarity comparison so "does not need" and
#: "needs" compare as the same claim rather than as two different ones.
_AUXILIARIES = frozenset(
    {
        "do",
        "doe",
        "does",
        "did",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "will",
        "would",
        "can",
        "could",
    }
)

#: Words that flip a statement's polarity without changing its subject.
_NEGATIONS = frozenset(
    {"not", "no", "never", "cannot", "without", "lacks", "fails", "isn't", "doesn't", "won't"}
)


@dataclass
class Claim:
    """One structured assertion. The shape §2.1 persists inside `file_metadata`."""

    id: str = ""
    statement: str = ""
    subject: str = ""
    predicate: str = ""
    object: str = ""
    confidence: float = 0.0
    source_ref: str = ""
    origin: str = "external"

    @classmethod
    def from_dict(cls, data: Any) -> Claim:
        if not isinstance(data, dict):
            return cls()
        claim = cls(
            id=str(data.get("id", "") or ""),
            statement=str(data.get("statement", "") or ""),
            subject=str(data.get("subject", "") or ""),
            predicate=str(data.get("predicate", "") or ""),
            object=str(data.get("object", "") or ""),
            confidence=_float(data.get("confidence"), 0.0),
            source_ref=str(data.get("source_ref", "") or ""),
            origin=str(data.get("origin", "") or "external"),
        )
        if not (claim.subject and claim.predicate):
            # Most claims arrive as prose. Parsing here rather than requiring the producer to
            # decompose means the deterministic tier works on real input instead of only on
            # input that was already in the right shape — which is the shape nothing produces.
            claim.subject, claim.predicate, claim.object = decompose(claim.statement)
        return claim

    @property
    def spo(self) -> tuple[str, str, str]:
        return (_norm(self.subject), _norm(self.predicate), _norm(self.object))


@dataclass
class Conflict:
    """A recorded disagreement. First-class, not a log line.

    `prefer` names which side the precedence ladder favours — advice for a reader, never an
    instruction to delete the other.
    """

    left_claim: str = ""
    right_claim: str = ""
    left_item: str = ""
    right_item: str = ""
    kind: str = "value"  # value | polarity | number
    prefer: str = ""  # "left" | "right" | "" when the ladder cannot decide
    detail: str = ""
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "left_claim": self.left_claim,
            "right_claim": self.right_claim,
            "left_item": self.left_item,
            "right_item": self.right_item,
            "kind": self.kind,
            "prefer": self.prefer,
            "detail": self.detail,
            "confidence": round(self.confidence, 4),
        }


# ── decomposition ──

#: A predicate we recognize, with its object trailing. Deliberately a small closed set rather
#: than a parser: a wrong decomposition produces a wrong CONFLICT, and a missed one produces
#: nothing — so the failure directions are not symmetric and the conservative set wins.
_PREDICATE_RE = re.compile(
    r"^(?P<subject>.{1,80}?)\s+"
    r"(?P<predicate>is not|are not|is|are|was|were|has|have|had|lacks|requires|forbids|"
    r"supports|blocks|enables|prevents|causes|increases|decreases|includes|excludes|"
    r"takes|uses|needs|returns|measured|costs)\s+"
    r"(?P<object>.+)$",
    re.IGNORECASE,
)


def decompose(statement: str) -> tuple[str, str, str]:
    """Best-effort `(subject, predicate, object)` from a prose claim.

    Returns empty strings when nothing matches, and the deterministic tier then declines to
    judge rather than guessing — an unparsed claim is not a claim about nothing.
    """
    text = " ".join((statement or "").split())
    if not text:
        return ("", "", "")
    match = _PREDICATE_RE.match(text)
    if not match:
        return ("", "", "")
    return (
        match.group("subject").strip(),
        match.group("predicate").strip().lower(),
        match.group("object").strip().rstrip(".!?"),
    )


# ── the deterministic tier ──


def deterministic_conflict(left: Claim, right: Claim) -> Conflict | None:
    """Do these two claims conflict, provably, with no model call?

    Three shapes, all requiring the same SUBJECT — without that, "X is fast" and "Y is slow"
    would read as a contradiction, and a store full of false conflicts is worse than one with
    none because nobody reads the report.
    """
    ls, lp, lo = left.spo
    rs, rp, ro = right.spo

    # Polarity first, and NOT behind the SPO gate. This rule exists to catch negations the
    # predicate set does not enumerate ("does not need" — the set has `needs`, not `need`), and
    # those are exactly the statements decomposition fails on. Behind the gate it was
    # unreachable: measured, "needs a restart" vs "does not need a restart" returned None.
    #
    # The subject test here is prose similarity rather than a parsed subject, for the same
    # reason. The high floor is what keeps it from firing on two different negated claims.
    if polarity(left.statement) != polarity(right.statement):
        if core_similarity(left.statement, right.statement) >= NUMBER_CONFLICT_MIN_SIM:
            return _make(left, right, kind="polarity", detail="opposite polarity, same claim")

    if not ls or not rs or ls != rs:
        return None
    if not lp or not rp:
        return None

    # 1. Same subject and predicate, different object: "cold start is 4s" vs "cold start is 9s".
    #
    # The similarity gate applies to NUMERIC objects too. Measured: without it, "The M2 has 8
    # cores" and "The M2 has 16 gigabytes of unified memory" were reported as a numeric conflict
    # — two unrelated properties of one subject, and a store full of that kind of finding is one
    # nobody reads. The numbers only disagree if the claims are otherwise about the same thing.
    if lp == rp and lo and ro and lo != ro:
        if similarity(left.statement, right.statement) < NUMBER_CONFLICT_MIN_SIM:
            return None
        kind = (
            "number" if _numbers(lo) and _numbers(ro) and _numbers(lo) != _numbers(ro) else "value"
        )
        # The ORIGINAL object text, not the normalized form: normalization strips the decimal
        # point, so a "4.2 vs 9.1" conflict rendered as "4 2 seconds vs 9 1 seconds".
        return _make(
            left,
            right,
            kind=kind,
            detail=f"{left.predicate or lp}: {left.object or lo} vs {right.object or ro}",
        )

    # 2. Same subject and object, opposite predicates: "X supports Y" vs "X blocks Y".
    if lo and ro and lo == ro and _opposed(lp, rp):
        return _make(left, right, kind="polarity", detail=f"{lp} vs {rp} on {lo}")

    return None


def find_conflicts(incoming: list[Claim], existing: list[Claim]) -> list[Conflict]:
    """Every deterministic conflict between what is arriving and what is stored.

    Incoming-vs-existing only, NOT incoming-vs-incoming: two claims in one write came from one
    source that already reconciled them, and flagging them would report the source's own
    internal structure as a disagreement.
    """
    out: list[Conflict] = []
    for new in incoming:
        for old in existing:
            if new.id and new.id == old.id:
                continue  # the same claim being reinforced is not a conflict with itself
            conflict = deterministic_conflict(new, old)
            if conflict is not None:
                out.append(conflict)
                if len(out) >= MAX_CONFLICTS_PER_PASS:
                    return out
    return out


def _make(left: Claim, right: Claim, *, kind: str, detail: str) -> Conflict:
    return Conflict(
        left_claim=left.statement,
        right_claim=right.statement,
        left_item=left.source_ref,
        right_item=right.source_ref,
        kind=kind,
        prefer=prefer_side(left, right),
        detail=detail,
        confidence=1.0,
    )


def prefer_side(left: Claim, right: Claim) -> str:
    """Which side the source-precedence ladder favours, or "" when it cannot say.

    "" is a real answer and the honest one for two same-tier sources: a ladder that always
    picked a winner would manufacture authority out of arrival order.
    """
    ranks = {name: index for index, name in enumerate(SOURCE_PRECEDENCE)}
    left_rank = ranks.get(_norm(left.origin), len(SOURCE_PRECEDENCE))
    right_rank = ranks.get(_norm(right.origin), len(SOURCE_PRECEDENCE))
    if left_rank < right_rank:
        return "left"
    if right_rank < left_rank:
        return "right"
    return ""


# ── the model tier ──


def shortlist(
    incoming: Claim, existing: list[Claim], *, cap: int = MAX_CONFLICT_CANDIDATES
) -> list[Claim]:
    """Candidates worth one model call, ranked by overlap.

    Deterministic shortlisting BEFORE the call is what keeps the marginal cost
    graph-size-independent: without it, every write would send the whole store.
    """
    scored = [
        (similarity(incoming.statement, other.statement), index, other)
        for index, other in enumerate(existing)
        if other.statement and other.source_ref != incoming.source_ref
    ]
    # Index as the tiebreak so the order is stable — a shortlist that reshuffles between runs
    # would hand the same write a different candidate set, and the judgement would follow it.
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [claim for score, _index, claim in scored[:cap] if score > 0]


# ── typed-edge inference (§3.2) ──

#: The 5-verb vocabulary. Closed, so a typo cannot invent a sixth relation nothing reads.
RELATION_VERBS = ("supersedes", "contradicts", "derived_from", "depends_on", "part_of")

#: Most edges one background pass will propose. The plan's cap.
MAX_EDGES_PER_PASS = 10


@dataclass
class Edge:
    """One typed relation between two items."""

    source: str
    target: str
    relation: str
    confidence: float = 1.0
    provenance: str = "extracted"
    justification: str = ""

    @property
    def valid(self) -> bool:
        """A self-edge is never meaningful, and an unknown verb is unreadable downstream."""
        return bool(
            self.source
            and self.target
            and self.source != self.target
            and self.relation in RELATION_VERBS
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "relation": self.relation,
            "confidence": round(self.confidence, 4),
            "provenance": self.provenance,
            "justification": self.justification[:120],
        }


def edges_from_conflicts(conflicts: list[Conflict]) -> list[Edge]:
    """`contradicts` edges for what the DETERMINISTIC tier proved.

    Every edge here is `provenance: extracted`, because every conflict reaching this function was
    proven without a model call — `find_conflicts` is the only producer. `inferred` is still a
    reachable provenance in the graph, but it is written by `parse_edge_proposals`, where a model
    actually proposed the relation. Reading the two apart is the point of the field.
    """
    out: list[Edge] = []
    for conflict in conflicts:
        if not (conflict.left_item and conflict.right_item):
            continue
        edge = Edge(
            source=conflict.left_item,
            target=conflict.right_item,
            relation="contradicts",
            confidence=conflict.confidence,
            provenance="extracted",
            justification=conflict.detail,
        )
        if edge.valid:
            out.append(edge)
    return out[:MAX_EDGES_PER_PASS]


def parse_edge_proposals(raw: Any, *, source_item: str) -> list[Edge]:
    """Typed edges a background model proposed, validated against the closed vocabulary."""
    if not isinstance(raw, dict):
        return []
    rows = raw.get("edges")
    if not isinstance(rows, list):
        return []
    out: list[Edge] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        edge = Edge(
            source=source_item,
            target=str(row.get("target", "") or ""),
            relation=str(row.get("relation", "") or "").strip().lower(),
            confidence=min(0.95, max(0.1, _float(row.get("confidence"), 0.5))),
            provenance="inferred",
            justification=str(row.get("justification", "") or ""),
        )
        if edge.valid:
            out.append(edge)
    return out[:MAX_EDGES_PER_PASS]


# ── shared text helpers ──


def similarity(left: str, right: str) -> float:
    """Token-overlap similarity in [0, 1].

    Jaccard rather than embeddings because this runs on EVERY persist, and the no-embedder path
    is a supported configuration — a conflict pass that silently degraded to "nothing conflicts"
    there would fail exactly where nobody is watching.
    """
    a = set(re.findall(r"[a-z0-9]{2,}", (left or "").lower()))
    b = set(re.findall(r"[a-z0-9]{2,}", (right or "").lower()))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def core_similarity(left: str, right: str) -> float:
    """Similarity with the NEGATION stripped and inflection flattened.

    Plain Jaccard is the wrong instrument for a polarity comparison: negating a claim adds
    tokens ("does", "not") and usually changes inflection ("needs" → "need"), so the score is
    systematically depressed for exactly the pair the rule wants to catch. Measured, "the gateway
    needs a restart after a config change" vs "the gateway does not need a restart after a config
    change" scored 0.60 against a 0.75 floor and was missed.

    Removing the negation words is what makes the floor mean "are these about the same thing"
    rather than "does one of them contain a negation".
    """
    return similarity(_strip_negation(left), _strip_negation(right))


def _strip_negation(statement: str) -> str:
    """Drop negation and auxiliary words, and flatten a trailing `s`.

    The auxiliaries go too ("does not need" leaves `do` and `need` behind otherwise), and the
    crude de-pluralization handles the verb agreement a negation forces. Crude on purpose: a real
    stemmer would collapse words this comparison needs kept distinct.
    """
    tokens = re.findall(r"[a-z0-9]{2,}", (statement or "").lower())
    kept = [
        token.rstrip("s") if len(token) > 3 else token
        for token in tokens
        if token not in _NEGATIONS and token not in _AUXILIARIES
    ]
    return " ".join(kept)


def polarity(statement: str) -> bool:
    """True when the statement asserts, False when it negates.

    Counts negation WORDS rather than testing for any, so a double negation ("it is not never
    used") reads as an assertion instead of a denial. Negative PREFIXES are deliberately not
    detected — "not unrelated" reads as a denial here. Morphological negation needs a lexicon to
    do correctly, and a half-built one that treats "invaluable" as negated would be worse than
    the miss: it would manufacture conflicts between statements that agree.
    """
    tokens = re.findall(r"[a-z']+", (statement or "").lower())
    return sum(1 for token in tokens if token in _NEGATIONS) % 2 == 0


def _opposed(left: str, right: str) -> bool:
    for first, second in _OPPOSITE_PREDICATES:
        if {left, right} == {first, second}:
            return True
    return False


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", text or "")


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", (text or "").lower()).split())


def _float(raw: Any, fallback: float) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        try:
            return float(str(raw).strip())
        except (TypeError, ValueError):
            return fallback
    return float(raw)
