"""Long-run mechanics: the difference between a watcher that demos and one that runs for months.

An `until_cancelled` watcher looks trivial — loop, wait, synthesize — and degrades within days
for three reasons this module addresses:

**Re-processing.** Iteration 40 re-reads everything iterations 1-39 already saw, pays for it
again, and re-synthesizes conclusions it already reached. The fix is a persistent **seen-set**
keyed on stable item identity, journaled rather than held in memory so it survives resume and
restart. Held in memory it would reset on every gateway restart, which is exactly when a
long-running watcher is most likely to be interrupted.

**Unbounded sibling context.** `{{siblings.main-work.output}}` naively means "every output the
sibling ever produced", so cycle 50's prompt carries 50 cycles of findings. Every cycle costs
more than the last, and the cost is superlinear in run length. So the sibling view is
**windowed and significance-filtered by default**, with `| full` as the explicit opt-out. The
default is the safe one because the failure is invisible: nothing breaks, the run just gets
slower and more expensive until it hits a context limit hours in.

**Self-synthesis drift.** A watcher whose synthesis output feeds its own next input compounds
its own paraphrases. `reflection_count` with an eligibility ceiling bounds the lineage depth.

Everything here is pure over explicit state. The controller owns the writes; this module owns
the decisions, which is what makes them testable without an engine.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

#: Default window for a sibling view — how many of the most recent items cross the boundary.
#: `KnowledgeConfig.synthesis_window` overrides it. 20 is a compromise: enough for a synthesis
#: to see a trend, few enough that a 500-cycle run costs the same per cycle as a 5-cycle one.
DEFAULT_SYNTHESIS_WINDOW = 20

#: Items below this significance do not cross a sibling boundary. A watcher's job is to notice
#: what matters; forwarding everything makes the synthesizer do that filtering with tokens.
DEFAULT_SIGNIFICANCE_THRESHOLD = 0.7

#: A single sibling payload above this size gets compressed before injection. Chosen so a
#: windowed view of 20 items cannot itself blow a context budget.
SIBLING_PAYLOAD_CAP_CHARS = 2000

#: Lineage ceiling. An item that has already been folded into three syntheses is not eligible
#: for a fourth: past that point the watcher is summarizing its own summaries, and each pass
#: loses detail while gaining confidence — the worst possible trade.
MAX_REFLECTION_COUNT = 3

#: Pairwise diversity below this, without high confidence, means the sources agree because they
#: are echoing each other rather than because the answer is clear.
DIVERSITY_FLOOR = 0.7

#: Bounds for a cycle's self-proposed next delay. A model asking for 2 seconds would spin; one
#: asking for a week would silently stop being a watcher. Neither is a failure the user sees.
MIN_ADAPTIVE_DELAY_SECS = 30
MAX_ADAPTIVE_DELAY_SECS = 24 * 60 * 60

#: Significance strings a model actually emits, mapped to the numeric scale. Models return
#: words far more reliably than calibrated floats, so the word form is first-class rather than
#: something the template has to convert.
_SIGNIFICANCE_WORDS = {
    "critical": 1.0,
    "high": 0.9,
    "significant": 0.8,
    "medium": 0.6,
    "moderate": 0.6,
    "normal": 0.5,
    "low": 0.3,
    "minor": 0.2,
    "trivial": 0.1,
    "noise": 0.0,
}


# ── item identity (§4.1) ──


def item_guid(item: Any) -> str:
    """A stable identity for one accumulated item.

    Prefers an explicit `guid`/`id`/`url`, because a source that supplies one knows better
    than we do what makes two of its items the same. Falls back to a content hash over the
    identifying fields.

    Deliberately NOT a hash of the whole item: a feed that re-serves the same story with an
    updated `fetched_at` would look novel every cycle, and the seen-set would never suppress
    anything. That failure is silent — the watcher just keeps paying to re-process.
    """
    if item is None:
        return ""
    if isinstance(item, str):
        return _hash(item.strip()) if item.strip() else ""
    if not isinstance(item, dict):
        return _hash(json.dumps(item, sort_keys=True, default=str))

    for key in ("guid", "id", "uid", "url", "link"):
        raw = item.get(key)
        if isinstance(raw, str) and raw.strip():
            return _hash(raw.strip().lower())
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return _hash(str(raw))

    parts = [
        str(item.get(k, "") or "").strip().lower()
        for k in ("statement", "title", "headline", "summary", "content", "text")
    ]
    joined = "\x1f".join(p for p in parts if p)
    return _hash(joined) if joined else ""


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


# ── persistent seen-set (§4.1) ──


@dataclass
class SeenSet:
    """Per-watcher record of what has already been processed.

    Ordered, and pruned oldest-first at `capacity`, so a watcher running for months has a
    bounded state footprint. Order is kept because eviction order matters: dropping a RECENT
    guid means the very next cycle re-processes it, which is the one case the seen-set exists
    to prevent.

    `capacity` is generous rather than tight: re-processing one old item costs a few tokens,
    while a bounded set that evicts too eagerly reintroduces the whole problem.
    """

    guids: list[str] = field(default_factory=list)
    capacity: int = 5000

    def __post_init__(self) -> None:
        # A non-positive capacity would make `mark` a no-op and every item novel forever.
        if self.capacity <= 0:
            self.capacity = 1
        self._index: set[str] = set(self.guids)

    def __contains__(self, guid: str) -> bool:
        return guid in self._index

    def __len__(self) -> int:
        return len(self.guids)

    def mark(self, guid: str) -> bool:
        """Record a guid. Returns True when it was NEW (so a caller can count novelty)."""
        if not guid or guid in self._index:
            return False
        self.guids.append(guid)
        self._index.add(guid)
        while len(self.guids) > self.capacity:
            self._index.discard(self.guids.pop(0))
        return True

    def unseen(self, items: list[Any]) -> list[Any]:
        """The novel items, in order, WITHOUT marking them.

        Separating "which are new" from "record that we saw them" is deliberate: marking at
        read time means a cycle that crashes mid-synthesis has already suppressed the items it
        never actually processed, and they are lost for good. The controller marks after the
        cycle succeeds.
        """
        out: list[Any] = []
        local: set[str] = set()
        for item in items:
            guid = item_guid(item)
            if not guid or guid in self._index or guid in local:
                continue
            local.add(guid)
            out.append(item)
        return out

    def mark_all(self, items: list[Any]) -> int:
        return sum(1 for item in items if self.mark(item_guid(item)))

    def to_dict(self) -> dict[str, Any]:
        return {"guids": list(self.guids), "capacity": self.capacity}

    @classmethod
    def from_dict(cls, data: Any) -> SeenSet:
        if not isinstance(data, dict):
            return cls()
        raw = data.get("guids")
        guids = [str(g) for g in raw if isinstance(g, str)] if isinstance(raw, list) else []
        cap = data.get("capacity")
        return cls(guids=guids, capacity=int(cap) if isinstance(cap, int) and cap > 0 else 5000)


# ── sibling views (§4.2) ──


def significance_of(item: Any) -> float:
    """An item's significance on 0..1, from either a number or the word a model emitted.

    An item with no significance field at all reads as 1.0, NOT 0.0: the filter exists to drop
    things a producer marked unimportant, and defaulting to zero would make it silently discard
    every output from a template that never opted in.
    """
    if not isinstance(item, dict):
        return 1.0
    raw = item.get("significance", item.get("importance", None))
    if raw is None:
        return 1.0
    if isinstance(raw, bool):
        return 1.0 if raw else 0.0
    if isinstance(raw, (int, float)):
        value = float(raw)
        # A 0-100 scale is common enough that treating 90 as "above threshold 0.7" is worth
        # the normalization; without it every percentage-scaled item passes trivially.
        return max(0.0, min(1.0, value / 100.0 if value > 1.0 else value))
    word = str(raw).strip().lower()
    return _SIGNIFICANCE_WORDS.get(word, 1.0)


def sibling_view(
    outputs: list[Any],
    *,
    window: int = DEFAULT_SYNTHESIS_WINDOW,
    threshold: float = DEFAULT_SIGNIFICANCE_THRESHOLD,
    seen: SeenSet | None = None,
    full: bool = False,
) -> list[Any]:
    """What one sibling's accumulated outputs look like from another sibling.

    Filtered and windowed BY DEFAULT — see the module docstring. `full=True` is the opt-out for
    a template that genuinely needs everything.

    Window is applied LAST, after filtering: windowing first would let 20 low-significance
    items crowd out the one that mattered, so the cheap filter runs before the cap.
    """
    items = _flatten_outputs(outputs)
    if seen is not None:
        items = seen.unseen(items)
    if full:
        # `full` means FULL — no filter and no window. It promised "everything"; a version that
        # still windowed at 20 would have been an opt-out that silently didn't.
        return items
    items = [i for i in items if significance_of(i) >= threshold]
    if window > 0 and len(items) > window:
        items = items[-window:]
    return items


def _flatten_outputs(outputs: list[Any]) -> list[Any]:
    """Iteration outputs → one flat item list.

    A loop body's output is usually a dict like `{findings: [...], new_findings: 3}`, so the
    interesting items are one level down. Unwrapping the common carrier keys means a template
    writes `{{siblings.main-work.output}}` and gets findings, not a list of envelopes it then
    has to map over — the wrapped form is the one that silently produces a useless prompt.
    """
    items: list[Any] = []
    for out in outputs:
        if out is None:
            continue
        if isinstance(out, list):
            items.extend(out)
            continue
        if isinstance(out, dict):
            for key in ("findings", "items", "results", "records", "entries"):
                inner = out.get(key)
                if isinstance(inner, list):
                    items.extend(inner)
                    break
            else:
                items.append(out)
            continue
        items.append(out)
    return items


def compress_payload(value: Any, *, cap: int = SIBLING_PAYLOAD_CAP_CHARS) -> tuple[str, bool]:
    """Bound one payload's size. Returns `(text, was_compressed)`.

    Deterministic truncation with a marker, not an LLM summarize: this runs on the hot path of
    every cycle, and a model call here would add a failure mode and a cost to the very
    mechanism that exists to bound cost. The plan's LLM-summarize path is the caller's to add
    around this — a truncate is the fallback that must always work.

    Truncation keeps the HEAD, and says how much it dropped. A silent truncation reads as a
    complete list, and a synthesis built on it would report absence as evidence.
    """
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if cap <= 0 or len(text) <= cap:
        return text, False
    dropped = len(text) - cap
    return f"{text[:cap]}\n… [{dropped} chars truncated — this view is INCOMPLETE]", True


# ── convergence guard (§4.2) ──


def pairwise_diversity(items: list[Any]) -> float:
    """How different the accumulated inputs are from each other, on 0..1.

    Token-overlap based (Jaccard distance averaged pairwise) rather than embedding based: this
    is a guard, it runs every cycle, and it must not depend on an embedder being configured.

    Fewer than two items is 1.0 — maximally diverse. One source cannot echo itself, and
    reporting 0.0 would make every first cycle raise a false convergence flag.
    """
    texts = [_text_of(i) for i in items]
    tokens = [set(re.findall(r"[a-z0-9]{3,}", t.lower())) for t in texts]
    tokens = [t for t in tokens if t]
    if len(tokens) < 2:
        return 1.0
    total = 0.0
    pairs = 0
    for idx in range(len(tokens)):
        for jdx in range(idx + 1, len(tokens)):
            a, b = tokens[idx], tokens[jdx]
            union = len(a | b)
            similarity = (len(a & b) / union) if union else 0.0
            total += 1.0 - similarity
            pairs += 1
    return round(total / pairs, 4) if pairs else 1.0


def convergence_warning(items: list[Any], *, confidence: float = 0.0) -> str:
    """The "sources converged early / possible echo" flag, or "".

    High confidence suppresses it: sources agreeing on a well-established fact is not an echo,
    and a guard that fires on every clear answer gets ignored — which is worse than not having
    it, because then the real case is ignored too.
    """
    if len(items) < 2:
        return ""
    diversity = pairwise_diversity(items)
    if diversity >= DIVERSITY_FLOOR or confidence >= 0.85:
        return ""
    return (
        f"sources converged early / possible echo (diversity {diversity:.2f} < "
        f"{DIVERSITY_FLOOR}) — corroboration here may be one source repeated"
    )


# ── lineage caps (§4.4) ──


def reflection_eligible(item: Any, *, ceiling: int = MAX_REFLECTION_COUNT) -> bool:
    """May this item be folded into another synthesis?

    Without a ceiling the watcher re-synthesizes its own output: each pass loses detail while
    the prose grows more confident, so drift compounds in the direction of unfalsifiability.
    """
    if not isinstance(item, dict):
        return True
    raw = item.get("reflection_count", 0)
    count = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else 0
    return count < max(1, ceiling)


def bump_reflection(item: Any) -> Any:
    """One more synthesis pass recorded on an input item."""
    if not isinstance(item, dict):
        return item
    raw = item.get("reflection_count", 0)
    count = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else 0
    return dict(item) | {"reflection_count": count + 1}


# ── adaptive delay clamp (§4.2) ──


def clamp_delay(
    proposed: Any,
    *,
    default: int,
    minimum: int = MIN_ADAPTIVE_DELAY_SECS,
    maximum: int = MAX_ADAPTIVE_DELAY_SECS,
) -> tuple[int, str]:
    """Clamp a cycle's self-proposed next delay. Returns `(secs, adjustment_reason)`.

    A watcher that adapts its own cadence is the whole point of a long-running monitor — a
    quiet week should cost nothing. But the proposal comes from a model, so it is bounded:
    2 seconds spins the loop and burns the budget in an hour, while a week means the watcher
    silently stopped being one. Both look like a working run.

    An unparseable proposal falls back to the template's configured delay rather than to the
    minimum, because "the model returned garbage" should not make the loop faster.
    """
    if isinstance(proposed, bool) or proposed is None:
        return default, "not_proposed"
    try:
        value = int(float(proposed))
    except (TypeError, ValueError):
        return default, "unparseable"
    if value < minimum:
        return minimum, f"clamped_up_from_{value}"
    if value > maximum:
        return maximum, f"clamped_down_from_{value}"
    return value, ""


# ── buffer seal (§4.3) ──


@dataclass
class BufferState:
    """Volume-driven synthesis trigger.

    The alternative to wall-clock cadence: synthesize when the buffer FILLS, with a stale-flush
    path so a slow trickle still gets consolidated eventually. A quiet week costs zero LLM
    calls; a busy hour synthesizes promptly. Wall-clock cadence gets exactly this backwards.
    """

    items: list[Any] = field(default_factory=list)
    seal_threshold: int = 20
    seal_tokens: int = 0
    flush_stale_after_secs: int = 3600
    last_flush_at: float = 0.0

    def add(self, items: list[Any]) -> None:
        self.items.extend(items)

    def approx_tokens(self) -> int:
        text = json.dumps(self.items, ensure_ascii=False, default=str)
        return len(text) // 4  # the usual ~4 chars/token approximation

    def should_seal(self, *, now: float) -> tuple[bool, str]:
        """Fire? Returns `(seal, reason)`.

        An EMPTY buffer never seals, including on the stale path. A stale-flush of nothing
        would pay for a synthesis of no new material every hour forever — the exact cost the
        volume trigger exists to avoid.
        """
        if not self.items:
            return False, ""
        if self.seal_threshold > 0 and len(self.items) >= self.seal_threshold:
            return True, f"buffer_full:{len(self.items)}_items"
        if self.seal_tokens > 0 and self.approx_tokens() >= self.seal_tokens:
            return True, f"buffer_full:{self.approx_tokens()}_tokens"
        if self.flush_stale_after_secs > 0 and self.last_flush_at:
            age = now - self.last_flush_at
            if age >= self.flush_stale_after_secs:
                return True, f"flush_stale:{int(age)}s"
        return False, ""

    def drain(self, *, now: float) -> list[Any]:
        drained = list(self.items)
        self.items = []
        self.last_flush_at = now
        return drained

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": list(self.items),
            "seal_threshold": self.seal_threshold,
            "seal_tokens": self.seal_tokens,
            "flush_stale_after_secs": self.flush_stale_after_secs,
            "last_flush_at": self.last_flush_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> BufferState:
        if not isinstance(data, dict):
            return cls()
        raw_items = data.get("items")
        return cls(
            items=list(raw_items) if isinstance(raw_items, list) else [],
            seal_threshold=_int(data.get("seal_threshold"), 20),
            seal_tokens=_int(data.get("seal_tokens"), 0),
            flush_stale_after_secs=_int(data.get("flush_stale_after_secs"), 3600),
            last_flush_at=float(data.get("last_flush_at") or 0.0),
        )


# ── run-continuity state (§4.2, zero-setup tier) ──

#: Caps for the rolling continuity object carried between recurring runs. Small on purpose:
#: this is a nudge to build on prior work, not a memory subsystem, and an unbounded version
#: would grow into the run record forever.
CONTINUITY_SUMMARY_LINES = 5
CONTINUITY_TOPIC_CAP = 10
CONTINUITY_REF_CAP = 20
CONTINUITY_LINE_CHARS = 200


def roll_continuity(prior: Any, *, outcome: str, topics: list[str], refs: list[str]) -> dict:
    """Fold one run's outcome into the bounded continuity object for the next run.

    Newest first, so the caps drop the OLDEST context. Dropping the newest would make the
    continuity object progressively less relevant the longer a recurring workflow ran.
    """
    prior_dict = prior if isinstance(prior, dict) else {}
    raw_summary = prior_dict.get("summary")
    lines = (
        [str(x) for x in raw_summary if isinstance(x, str)] if isinstance(raw_summary, list) else []
    )
    if outcome.strip():
        lines.insert(0, outcome.strip()[:CONTINUITY_LINE_CHARS])

    return {
        "summary": lines[:CONTINUITY_SUMMARY_LINES],
        "recent_topics": _dedup_capped(
            topics, prior_dict.get("recent_topics"), CONTINUITY_TOPIC_CAP
        ),
        "recent_refs": _dedup_capped(refs, prior_dict.get("recent_refs"), CONTINUITY_REF_CAP),
    }


def continuity_header(continuity: Any) -> str:
    """The injected block, or "" when there is nothing to say.

    An empty continuity object returns "" rather than a header with nothing under it: a
    "Context from previous runs" heading followed by blank space reads to a model as "there
    was prior work and it produced nothing", which is a different and wrong claim.
    """
    if not isinstance(continuity, dict):
        return ""
    lines = [str(x) for x in (continuity.get("summary") or []) if str(x).strip()]
    topics = [str(x) for x in (continuity.get("recent_topics") or []) if str(x).strip()]
    if not lines and not topics:
        return ""
    parts = ["Context from previous runs — avoid repeating, build on prior work:"]
    parts.extend(f"- {line}" for line in lines)
    if topics:
        parts.append(f"Recently covered: {', '.join(topics)}")
    return "\n".join(parts)


def _dedup_capped(fresh: list[str], prior: Any, cap: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    prior_list = [str(x) for x in prior if isinstance(x, str)] if isinstance(prior, list) else []
    for value in [str(f) for f in fresh] + prior_list:
        key = value.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value.strip()[:CONTINUITY_LINE_CHARS])
    return out[:cap]


# ── web-item hygiene preset (§4.1) ──

#: Minimum words in a title for a scraped item to be worth a model's attention. "Read more",
#: "Click here" and bare dates are the usual junk a feed yields.
MIN_TITLE_WORDS = 3


def web_hygiene(
    items: list[Any],
    *,
    allow_domains: list[str] | None = None,
    min_title_words: int = MIN_TITLE_WORDS,
) -> list[Any]:
    """Drop scraped junk before it reaches a prompt.

    A preset rather than something each monitoring template reimplements — which is how one
    template ends up filtering and the next one pays for "Read more ›" every cycle.
    """
    allowed = [d.strip().lower().lstrip(".") for d in (allow_domains or []) if str(d).strip()]
    out: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            if isinstance(item, str) and len(item.split()) >= min_title_words:
                out.append(item)
            continue
        title = str(item.get("title", item.get("headline", "")) or "")
        if title and len(title.split()) < min_title_words:
            continue
        if allowed and not _domain_allowed(
            str(item.get("url", item.get("link", "")) or ""), allowed
        ):
            continue
        out.append(item)
    return out


def _domain_allowed(url: str, allowed: list[str]) -> bool:
    """A URL is on-domain when its host equals or is a subdomain of an allowed domain.

    Substring matching would let `evil-example.com.attacker.net` pass an `example.com` filter,
    so the check is on host boundaries.
    """
    if not url:
        return False
    host = re.sub(r"^[a-z]+://", "", url.strip().lower()).split("/")[0].split(":")[0]
    return any(host == dom or host.endswith("." + dom) for dom in allowed)


def _text_of(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("statement", "title", "summary", "content", "text", "headline"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return json.dumps(item, sort_keys=True, default=str)
    return str(item)


def _int(raw: Any, fallback: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return fallback
    return int(raw)
