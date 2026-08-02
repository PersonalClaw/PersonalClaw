"""Knowledge-store semantics — typed kinds, logical identity, and idempotent writes.

The store already holds items. What it does not have is a way to say **"this is the same
thing I persisted last time"**. Without that, a retried persist node writes a second copy,
a rewound one writes a third, and a synthesis loop that runs nightly accumulates a hundred
near-identical articles that all look like independent corroboration.

So identity here is *derived*, not assigned:

**A logical key is `{kind}:{normalized_title}`.** Items keep their UUID primary keys —
changing that would rewrite every foreign key in the database — and the logical key is an
indexed derived column beside them. Lookup-before-write against it is what makes a persist
idempotent, and deriving it from content means a retry computes the same key without
having to remember what it did last time.

**A content hash decides no-op versus update.** Same logical key AND same content is a
no-op returning the existing id: the caller gets the same answer it would have got, and
nothing is written. Same key, different content is an update. That pair is the whole
idempotency story, and it works across retries, resumes and rewinds without any of them
knowing about each other.

**Duplicate content REINFORCES rather than inserting.** Re-persisting a known claim
appends a *mention* — source, confidence, quote — and re-aggregates confidence as
`1 - ∏(1 - cᵢ)`. That formula matters: three independent sources at 0.6 give 0.936, not
1.8 and not 0.6. Summing would exceed certainty; averaging would make corroboration
*weaken* a strong claim. `support_count` then becomes a retrieval signal, so a claim three
sources agree on outranks one nobody has confirmed.

**Supersession sets `invalid_at`; it never deletes.** "What was true when" stays queryable,
which is the difference between a knowledge base and a cache.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── The typed taxonomy ──

#: The `kind` vocabulary. Distinct from `item_type`, which routes the INGESTION graph and
#: stays one of the 12 native types — conflating the two would make "how did this arrive"
#: and "what sort of knowledge is it" the same field, and they answer different questions.
KINDS = (
    "fact",
    "decision",
    "insight",
    "report",
    "reference",
    "known-issue",
    "preference-note",
    "glossary",
    "overview",
    "probe",
)

#: Kinds that are SYNTHESIZED rather than observed. These require citations, because an
#: unsourced synthesis is indistinguishable from a confident guess once it is in the store
#: and being retrieved as fact.
SYNTHESIZED_KINDS = frozenset({"insight", "report", "overview"})

#: Per-kind size budgets in characters. A budget overrun returns a descriptive failure
#: rather than raising, so the synthesizing stage can condense and retry under the
#: engine's normal retry semantics instead of dying.
KIND_BUDGETS: dict[str, int] = {
    "fact": 4_000,
    "decision": 8_000,
    "insight": 12_000,
    "report": 40_000,
    "reference": 20_000,
    "known-issue": 8_000,
    "preference-note": 2_000,
    "glossary": 4_000,
    "overview": 16_000,
    "probe": 2_000,
}
DEFAULT_BUDGET = 12_000

#: The 5-verb relation vocabulary. Closed, and deliberately small: this is
#: "item-fields-plus-report", not a graph database, and every verb added is one a reader
#: has to learn and a query has to handle.
RELATION_TYPES = ("supersedes", "contradicts", "derived_from", "depends_on", "part_of")

#: Where an edge came from. `extracted` is deterministic and trusted at 1.0; anything a
#: model inferred carries a score, because an inferred edge presented as fact is how a
#: wrong link becomes permanent.
RELATION_PROVENANCE = ("extracted", "inferred", "ambiguous")

#: How a claim is stated. Kept because a hedged claim treated as asserted is how "might
#: be related to" becomes "is caused by" three retrievals later.
HEDGING_LEVELS = ("asserted", "hedged", "speculative")

#: The ceiling on aggregated confidence. Deliberately below 1.0: a claim at exactly 1.0 is
#: unfalsifiable by construction — no contradiction can lower it — and a knowledge base
#: that cannot be corrected is a knowledge base that will be wrong forever.
MAX_CONFIDENCE = 0.999


def effective_budgets() -> dict[str, int]:
    """The per-kind budgets, with the owner's `report_budget_chars` applied.

    Reads config rather than using the module constants directly: a knob the validator
    never consults is a knob that does nothing, and `report_budget_chars` is exposed as
    runtime-editable specifically so an owner mid-research can raise it without a restart.

    Falls back to the constants when config cannot be read — a knowledge write should not
    fail because the config file is briefly unreadable.
    """
    budgets = dict(KIND_BUDGETS)
    try:
        from personalclaw.config.loader import AppConfig

        configured = int(getattr(AppConfig.load().knowledge, "report_budget_chars", 0) or 0)
        if configured > 0:
            budgets["report"] = configured
    except Exception:
        logger.debug("knowledge budget config unreadable — using defaults", exc_info=True)
    return budgets


# ── Logical identity ──

_PUNCT_RE = re.compile(r"[^\w\s-]", re.UNICODE)
_WS_RE = re.compile(r"[\s_-]+")


def normalize_title(title: str) -> str:
    """Normalize a title for logical identity.

    Unicode-normalized, case-folded, punctuation-stripped, whitespace-collapsed. The point
    is that "The Parser's Design", "the parser's design" and "The Parser’s Design" (curly
    apostrophe) are ONE thing — a title retyped by hand, or round-tripped through a model
    that smartened the quotes, must not become a second article.
    """
    if not title:
        return ""
    folded = unicodedata.normalize("NFKD", str(title)).casefold()
    stripped = _PUNCT_RE.sub(" ", folded)
    return _WS_RE.sub("-", stripped).strip("-")


def logical_key(kind: str, title: str) -> str:
    """The deterministic logical identity: `{kind}:{normalized_title}`.

    Includes the kind because the same title genuinely means different things across them:
    a `decision` called "caching" and a `known-issue` called "caching" are two records, and
    collapsing them would let a resolved decision overwrite an open bug.
    """
    normalized = normalize_title(title)
    if not normalized:
        return ""
    return f"{(kind or 'fact').strip().lower()}:{normalized}"


def content_hash(
    *,
    title: str = "",
    content: str = "",
    summary: str = "",
    claims: list[dict] | None = None,
) -> str:
    """A stable hash of everything a persist would write.

    Claims are included and sorted: a re-persist that adds a claim IS a content change and
    must not be treated as a no-op, while the same claims in a different order is the same
    knowledge and must be. Whitespace is normalized so a reflowed paragraph does not read
    as an edit — otherwise every model that rewraps its output looks like it changed the
    article.
    """
    payload = {
        "title": " ".join((title or "").split()),
        "content": " ".join((content or "").split()),
        "summary": " ".join((summary or "").split()),
        "claims": sorted(
            (json.dumps(c, sort_keys=True, default=str) for c in (claims or [])),
        ),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def chunk_hash(text: str) -> str:
    """A hash of one chunk of text, for differential refresh.

    Separate from `content_hash` so a large reference document can be refreshed section by
    section: re-embedding a 40k-character report because one paragraph changed is the cost
    this exists to avoid.
    """
    return hashlib.sha256(" ".join((text or "").split()).encode("utf-8")).hexdigest()[:32]


# ── Confidence aggregation ──


def aggregate_confidence(confidences: list[float]) -> float:
    """Combine independent confidences as `1 - ∏(1 - cᵢ)`.

    Three sources at 0.6 give 0.936 — more than any one of them, less than certainty.
    Summing would exceed 1.0 and averaging would make corroboration WEAKEN a strong claim,
    which is the opposite of what agreement means.

    Values are clamped to [0, 1) rather than [0, 1]: a source claiming absolute certainty
    would make the aggregate 1.0 forever, and no amount of later contradiction could move
    it. Nothing in a personal knowledge base earns that.
    """
    product = 1.0
    for raw in confidences or []:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        product *= 1.0 - max(0.0, min(0.999, value))
    # A hard ceiling, not just a per-value clamp. Measured: ten sources at 0.999 each
    # round to exactly 1.0, which would make the docstring above a lie and — worse — leave
    # a claim that no amount of later contradiction could move. Nothing in a personal
    # knowledge base earns absolute certainty.
    return min(MAX_CONFIDENCE, round(1.0 - product, 6))


# ── Claims and mentions ──


@dataclass
class Mention:
    """One observation of a claim: who said it, how sure, and their words.

    The quote is kept verbatim. A paraphrase loses the thing that made the source worth
    citing, and a claim whose evidence has been summarized cannot be re-checked.
    """

    source_ref: str
    confidence: float = 0.5
    quote: str = ""
    observed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_ref": self.source_ref,
            "confidence": round(float(self.confidence), 4),
            "quote": self.quote[:2000],
            "observed_at": self.observed_at or _now(),
        }


@dataclass
class Claim:
    """A phenomenon-level assertion, with its evidence and its validity window.

    The statement is phenomenon-level by rule — numbers and specifics live in the quote —
    so that claims from different sources about the same phenomenon are COMPARABLE. "Cold
    starts are slow" is comparable; "cold starts took 4.2s on Tuesday" is a measurement
    masquerading as a claim, and two of them can never agree or conflict.
    """

    id: str
    statement: str
    status: str = "open"
    confidence: float = 0.5
    hedging: str = "asserted"
    mentions: list[Mention] = field(default_factory=list)
    valid_at: str = ""
    #: Set on supersession. NEVER deleted — "what was true when" stays queryable, which is
    #: the difference between a knowledge base and a cache.
    invalid_at: str = ""

    @property
    def support_count(self) -> int:
        """How many independent sources back this. A retrieval ranking signal."""
        return len({m.source_ref for m in self.mentions if m.source_ref})

    @property
    def valid(self) -> bool:
        return not self.invalid_at

    def aggregate(self) -> float:
        """Re-derive confidence from the mentions."""
        if not self.mentions:
            return round(float(self.confidence), 6)
        return aggregate_confidence([m.confidence for m in self.mentions])

    def add_mention(self, mention: Mention) -> bool:
        """Append a mention and re-aggregate. False if this source already spoke.

        Deduplicated by `source_ref`: the same source re-read twice is not two independent
        confirmations, and counting it as such would let one loud source manufacture
        consensus with itself.
        """
        if any(m.source_ref == mention.source_ref for m in self.mentions):
            return False
        self.mentions.append(mention)
        self.confidence = self.aggregate()
        return True

    def supersede(self, at: str = "") -> None:
        self.invalid_at = at or _now()
        self.status = "superseded"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "status": self.status,
            "confidence": round(float(self.confidence), 4),
            "hedging": self.hedging,
            "mentions": [m.to_dict() for m in self.mentions],
            "support_count": self.support_count,
            "valid_at": self.valid_at,
            "invalid_at": self.invalid_at,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Claim | None:
        """Parse one claim, tolerating a malformed row.

        Returns None rather than raising: claims live in a JSON blob that an LLM wrote, and
        one bad claim must not make an otherwise-good article unreadable.
        """
        if not isinstance(raw, dict) or not raw.get("statement"):
            return None
        hedging = str(raw.get("hedging", "asserted"))
        return cls(
            id=str(raw.get("id") or _claim_id(str(raw["statement"]))),
            statement=str(raw["statement"]),
            status=str(raw.get("status", "open") or "open"),
            confidence=_as_float(raw.get("confidence"), 0.5),
            hedging=hedging if hedging in HEDGING_LEVELS else "asserted",
            mentions=[
                Mention(
                    source_ref=str(m.get("source_ref", "")),
                    confidence=_as_float(m.get("confidence"), 0.5),
                    quote=str(m.get("quote", "")),
                    observed_at=str(m.get("observed_at", "")),
                )
                for m in (raw.get("mentions") or [])
                if isinstance(m, dict)
            ],
            valid_at=str(raw.get("valid_at", "")),
            invalid_at=str(raw.get("invalid_at", "")),
        )


def _claim_id(statement: str) -> str:
    """A derived claim id, so the same statement gets the same id across runs."""
    return (
        "c-"
        + hashlib.sha256(" ".join(statement.split()).casefold().encode("utf-8")).hexdigest()[:12]
    )


# ── Freshness ──


@dataclass
class Freshness:
    """How stale an item is, for the retrieving model to reason about.

    Reported rather than enforced: whether a three-month-old fact still holds depends on
    the fact, and a store that silently hid stale items would make its own gaps invisible.
    """

    age_days: float
    last_verified: str = ""
    expires_at: str = ""
    expired: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "age_days": round(self.age_days, 2),
            "last_verified": self.last_verified,
            "expires_at": self.expires_at,
            "expired": self.expired,
        }


def freshness(
    *,
    updated_at: str,
    last_verified: str = "",
    expires_at: str = "",
    now: datetime | None = None,
) -> Freshness:
    """Compute freshness from the stored timestamps.

    Age counts from `last_verified` when present, else `updated_at`: an item re-checked
    yesterday is fresh even if it was written a year ago, and that distinction is the whole
    reason `last_verified` exists as a separate column.
    """
    moment = now or datetime.now(timezone.utc)
    basis = _parse(last_verified) or _parse(updated_at)
    age = ((moment - basis).total_seconds() / 86400.0) if basis else 0.0
    expiry = _parse(expires_at)
    return Freshness(
        age_days=max(0.0, age),
        last_verified=last_verified,
        expires_at=expires_at,
        expired=bool(expiry and moment >= expiry),
    )


def ttl_to_expiry(ttl: str, *, now: datetime | None = None) -> str:
    """Turn a `ttl` like `7d` / `12h` / `30m` into an absolute `expires_at`.

    Stored absolute, not relative: a TTL evaluated at read time would keep an item alive
    forever as long as nothing read it, which is the opposite of expiry.
    """
    raw = (ttl or "").strip().lower()
    match = re.fullmatch(r"(\d+)\s*([dhmw])", raw)
    if not match:
        return ""
    amount = int(match.group(1))
    unit = match.group(2)
    delta = {
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
        "w": timedelta(weeks=amount),
    }[unit]
    return ((now or datetime.now(timezone.utc)) + delta).isoformat()


# ── Write validation ──


@dataclass
class PersistCheck:
    """Whether a persist may proceed, and why not. An error-as-RETURN, never a raise.

    The engine's retry semantics can act on a returned failure — condense and try again —
    but an exception just kills the node. A budget overrun is a recoverable situation and
    should be presented as one.
    """

    ok: bool
    error: str = ""
    normalized_kind: str = ""
    logical_key: str = ""
    content_hash: str = ""
    expires_at: str = ""

    def to_result(self) -> dict[str, Any]:
        return {"success": self.ok, "error": self.error} if not self.ok else {"success": True}


def check_persist(
    *,
    kind: str = "fact",
    title: str = "",
    content: str = "",
    summary: str = "",
    claims: list[dict] | None = None,
    citations: list[str] | None = None,
    unsourced: bool = False,
    ttl: str = "",
    expires_at: str = "",
    budgets: dict[str, int] | None = None,
    now: datetime | None = None,
) -> PersistCheck:
    """Validate and derive everything a persist needs. The single entry point.

    Order is deliberate: identity is computed before the budget check, so a rejected
    oversize write still tells the caller WHICH item it would have been — otherwise the
    retry cannot tell whether it is creating or updating.
    """
    normalized_kind = (kind or "fact").strip().lower()
    if normalized_kind not in KINDS:
        return PersistCheck(
            False,
            f"unknown kind {kind!r} — one of: {', '.join(KINDS)}",
        )
    if not (title or "").strip():
        return PersistCheck(False, "a knowledge item needs a title (it is half its identity)")

    key = logical_key(normalized_kind, title)
    digest = content_hash(title=title, content=content, summary=summary, claims=claims)
    expiry = expires_at or (ttl_to_expiry(ttl, now=now) if ttl else "")

    # Citations on synthesized kinds. An unsourced synthesis is indistinguishable from a
    # confident guess once it is in the store being retrieved as fact — so the requirement
    # is explicit, and so is the opt-out.
    if normalized_kind in SYNTHESIZED_KINDS and not citations and not unsourced:
        return PersistCheck(
            False,
            f"kind {normalized_kind!r} is synthesized and needs `citations`, or an explicit "
            "`unsourced: true` — an unsourced synthesis reads as fact on retrieval",
            normalized_kind=normalized_kind,
            logical_key=key,
            content_hash=digest,
        )

    limits = budgets if budgets is not None else effective_budgets()
    budget = int(limits.get(normalized_kind, DEFAULT_BUDGET))
    size = len(content or "") + len(summary or "")
    if size > budget:
        return PersistCheck(
            False,
            f"over budget by {size - budget} chars — condense and retry "
            f"({size} of {budget} for kind {normalized_kind!r})",
            normalized_kind=normalized_kind,
            logical_key=key,
            content_hash=digest,
        )

    return PersistCheck(
        True,
        normalized_kind=normalized_kind,
        logical_key=key,
        content_hash=digest,
        expires_at=expiry,
    )


# ── Idempotency ──


@dataclass
class WriteDecision:
    """What a persist should actually DO, given what is already stored."""

    #: "noop" | "create" | "update" | "reinforce"
    action: str
    item_id: str = ""
    reason: str = ""
    mentions_appended: int = 0

    @property
    def wrote(self) -> bool:
        return self.action in ("create", "update", "reinforce")


def decide_write(
    *,
    logical_key: str,
    content_hash: str,
    existing_id: str = "",
    existing_hash: str = "",
    mode: str = "upsert",
) -> WriteDecision:
    """Decide create / update / reinforce / no-op. Pure, so it is testable.

    The no-op case is the one that matters: a retried, resumed or rewound persist whose
    (key, hash) already exists writes nothing and returns the existing id. None of those
    three paths needs to know about the others, because identity is derived from content
    rather than remembered.
    """
    if not existing_id:
        return WriteDecision("create", reason=f"no item at {logical_key!r}")

    if mode == "create":
        # An explicit create against an existing key is a caller error worth surfacing
        # rather than silently upserting — they asked for a new item and would not get one.
        return WriteDecision(
            "noop",
            item_id=existing_id,
            reason=f"mode=create but {logical_key!r} already exists",
        )

    if existing_hash and existing_hash == content_hash:
        if mode == "append_evidence":
            return WriteDecision(
                "reinforce",
                item_id=existing_id,
                reason="same content — appending a mention instead of rewriting",
            )
        return WriteDecision(
            "noop",
            item_id=existing_id,
            reason="identical content already stored — idempotent no-op",
        )

    return WriteDecision("update", item_id=existing_id, reason="content changed")


# ── Typed item relations ──


@dataclass
class ItemRelation:
    """One typed edge between two knowledge items."""

    source_item_id: str
    target_item_id: str
    relation_type: str
    confidence: float = 1.0
    provenance: str = "extracted"
    created_at: str = ""

    def key(self) -> tuple[str, str, str]:
        """The upsert key. An edge is identified by its endpoints and its verb, so
        re-deriving the same edge updates it rather than duplicating it."""
        return (self.source_item_id, self.target_item_id, self.relation_type)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_item_id": self.source_item_id,
            "target_item_id": self.target_item_id,
            "relation_type": self.relation_type,
            "confidence": round(float(self.confidence), 4),
            "provenance": self.provenance,
            "created_at": self.created_at or _now(),
        }


def validate_relation(
    source_item_id: str,
    target_item_id: str,
    relation_type: str,
    *,
    provenance: str = "extracted",
    confidence: float | None = None,
) -> tuple[ItemRelation | None, str]:
    """Build a validated edge, or return why it is refused.

    A self-edge is refused: "this supersedes itself" is never meaningful, and one written
    by a confused extraction pass would make a supersession chain cyclic.

    `extracted` edges are forced to confidence 1.0 — they are deterministic by definition,
    and letting a caller supply 0.4 for one would make the provenance label meaningless.
    """
    if not source_item_id or not target_item_id:
        return None, "a relation needs both endpoints"
    if source_item_id == target_item_id:
        return None, "an item cannot relate to itself"
    verb = (relation_type or "").strip().lower()
    if verb not in RELATION_TYPES:
        return None, f"unknown relation {relation_type!r} — one of: {', '.join(RELATION_TYPES)}"
    prov = (provenance or "extracted").strip().lower()
    if prov not in RELATION_PROVENANCE:
        return None, f"unknown provenance {provenance!r}"

    score = 1.0 if prov == "extracted" else _as_float(confidence, 0.5)
    return (
        ItemRelation(
            source_item_id=source_item_id,
            target_item_id=target_item_id,
            relation_type=verb,
            confidence=max(0.0, min(1.0, score)),
            provenance=prov,
        ),
        "",
    )


# ── helpers ──


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(stamp: str) -> datetime | None:
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
