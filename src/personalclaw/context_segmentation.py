"""Topic segmentation of a conversation transcript (Context Economy §4).

The shared primitive behind the background compression service (§4) and — when it
lands — LOOP-R13's proactive in-loop compression. ONE segmenter, two callers.

A transcript is split into contiguous topic segments so a compressor can weight
attention by recency (recent topic near-verbatim, older topics folded). Two tiers,
by design (not accident):

  * **embedding-drift** — when an embed function is bound, adjacent user-turn
    embeddings whose cosine similarity drops below a threshold mark a topic
    boundary (the `agent-zero` shape). Semantic, so a topic that spans many turns
    stays one segment.
  * **deterministic turn-count fallback** — when no embedder is bound, segment
    every N user turns. Coarser, never wrong, and the DESIGNED no-model tier.

Pure + dependency-free (takes an optional `embed_fn`, never resolves one itself) so
it is trivially unit-testable and free of the config/provider graph.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

# Cosine BELOW this between adjacent user turns = a topic boundary (embedding tier).
# Mirrors the surfacing/skills family's similarity gates (0.62 match / 0.7 overlap):
# a mid gate — high enough that rambling within a topic stays together, low enough
# that a genuine subject change splits.
DEFAULT_DRIFT_THRESHOLD = 0.6
# Deterministic tier: a boundary every N user turns when no embedder is bound.
DEFAULT_TURNS_PER_SEGMENT = 8

EmbedFn = Callable[[str], "list[float] | None"]


@dataclass(frozen=True)
class Segment:
    """A contiguous run of messages on one topic.

    ``start``/``end`` are indices into the ORIGINAL message list (``end`` exclusive)
    so a caller can map a segment back to the exact lines it must archive/rewrite.
    ``messages`` is the slice itself (convenience; the same objects, not copies).
    """

    start: int
    end: int
    messages: list[dict]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _user_text(m: dict) -> str:
    return (m.get("content") or "").strip() if m.get("role") == "user" else ""


def segment_messages(
    messages: list[dict],
    *,
    embed_fn: EmbedFn | None = None,
    drift_threshold: float = DEFAULT_DRIFT_THRESHOLD,
    turns_per_segment: int = DEFAULT_TURNS_PER_SEGMENT,
) -> list[Segment]:
    """Split *messages* into contiguous topic segments.

    Boundaries are placed BEFORE a user turn that opens a new topic; every message
    between two user-turn boundaries (assistant replies, tool rows) belongs to the
    segment its preceding user turn opened. With ``embed_fn`` the split is by
    embedding drift between successive user turns; without it, every
    ``turns_per_segment`` user turns. Returns one segment covering everything when
    there are 0-1 user turns (nothing to split).
    """
    if not messages:
        return []

    # Boundary indices: the message index at which each new segment STARTS. Always
    # includes 0. We only ever place boundaries at user turns (a topic is opened by
    # what the user asks); non-user messages ride with the preceding user turn.
    user_positions = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if len(user_positions) <= 1:
        return [Segment(0, len(messages), list(messages))]

    boundaries: list[int] = [0]
    if embed_fn is not None:
        prev_vec: list[float] | None = None
        turns_seen = 0
        for pos in user_positions:
            text = _user_text(messages[pos])
            vec = embed_fn(text) if text else None
            if vec is not None and prev_vec is not None:
                if _cosine(prev_vec, vec) < drift_threshold and pos != 0:
                    boundaries.append(pos)
            # Only advance prev_vec on a real embedding, so a couple of un-embeddable
            # turns don't silently reset drift tracking.
            if vec is not None:
                prev_vec = vec
            turns_seen += 1
        # If embedding produced NO boundaries (single flat topic, or every embed
        # returned None), fall through to the deterministic tier so a huge
        # single-topic transcript still gets coarse segments to weight.
        if len(boundaries) == 1:
            boundaries = _turn_count_boundaries(user_positions, turns_per_segment)
    else:
        boundaries = _turn_count_boundaries(user_positions, turns_per_segment)

    # Materialize segments from the sorted, de-duped boundary list.
    bset = sorted(set(b for b in boundaries if 0 <= b < len(messages)))
    if bset[0] != 0:
        bset.insert(0, 0)
    segments: list[Segment] = []
    for i, start in enumerate(bset):
        end = bset[i + 1] if i + 1 < len(bset) else len(messages)
        if end > start:
            segments.append(Segment(start, end, messages[start:end]))
    return segments


def _turn_count_boundaries(user_positions: list[int], turns_per_segment: int) -> list[int]:
    """Boundary at every ``turns_per_segment``-th user turn (deterministic tier)."""
    n = max(1, turns_per_segment)
    out = [0]
    for idx, pos in enumerate(user_positions):
        if idx > 0 and idx % n == 0:
            out.append(pos)
    return out
