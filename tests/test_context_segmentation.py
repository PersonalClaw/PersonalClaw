"""Topic segmentation (Context Economy §4) — the shared segmenter behind the
background compression service and (later) LOOP-R13's in-loop compression.

Two tiers: embedding-drift when an embed_fn is bound, deterministic turn-count
fallback otherwise (the designed no-model tier)."""

from __future__ import annotations

from personalclaw.context_segmentation import (
    DEFAULT_TURNS_PER_SEGMENT,
    segment_messages,
)


def _u(text):
    return {"role": "user", "content": text}


def _a(text):
    return {"role": "assistant", "content": text}


def test_empty_and_single_topic():
    assert segment_messages([]) == []
    # 0-1 user turns → one segment covering everything.
    segs = segment_messages([_u("hi"), _a("hello")])
    assert len(segs) == 1
    assert segs[0].start == 0 and segs[0].end == 2


def test_deterministic_turn_count_fallback():
    # No embedder → boundary every DEFAULT_TURNS_PER_SEGMENT user turns.
    msgs = []
    for i in range(DEFAULT_TURNS_PER_SEGMENT * 2 + 1):
        msgs.append(_u(f"q{i}"))
        msgs.append(_a(f"a{i}"))
    segs = segment_messages(msgs)  # embed_fn=None
    # 17 user turns, boundary every 8 → segments start at user turns 0, 8, 16 → 3 segments
    assert len(segs) == 3
    # Segments are contiguous and cover the whole transcript.
    assert segs[0].start == 0
    assert segs[-1].end == len(msgs)
    for i in range(len(segs) - 1):
        assert segs[i].end == segs[i + 1].start


def test_non_user_messages_ride_with_preceding_user_turn():
    msgs = [_u("q0"), _a("a0"), {"role": "tool", "content": "t"}, _u("q1"), _a("a1")]
    segs = segment_messages(msgs, turns_per_segment=1)
    # boundary every user turn → 2 segments; the tool row belongs to segment 0
    assert len(segs) == 2
    assert [m["role"] for m in segs[0].messages] == ["user", "assistant", "tool"]
    assert [m["role"] for m in segs[1].messages] == ["user", "assistant"]


def test_embedding_drift_splits_on_topic_change():
    # Two orthogonal "topics": vecs [1,0] then [0,1]. Cosine 0 < threshold → boundary.
    vectors = {
        "docker": [1.0, 0.0],
        "docker2": [0.99, 0.01],
        "taxes": [0.0, 1.0],
        "taxes2": [0.01, 0.99],
    }

    def embed(text):
        return vectors.get(text)

    msgs = [
        _u("docker"),
        _a("a0"),
        _u("docker2"),
        _a("a1"),
        _u("taxes"),
        _a("a2"),
        _u("taxes2"),
        _a("a3"),
    ]
    segs = segment_messages(msgs, embed_fn=embed, drift_threshold=0.5)
    # The docker→taxes jump (cosine ~0) is the one boundary → 2 segments.
    assert len(segs) == 2
    assert [m["content"] for m in segs[0].messages] == ["docker", "a0", "docker2", "a1"]
    assert [m["content"] for m in segs[1].messages] == ["taxes", "a2", "taxes2", "a3"]


def test_embedding_flat_topic_falls_back_to_turn_count():
    # Every user turn embeds identically (no drift) → no embedding boundary → the
    # deterministic tier kicks in so a huge single-topic transcript still segments.
    def embed(_text):
        return [1.0, 0.0]

    msgs = []
    for i in range(DEFAULT_TURNS_PER_SEGMENT * 2 + 1):
        msgs.append(_u(f"q{i}"))
        msgs.append(_a(f"a{i}"))
    segs = segment_messages(msgs, embed_fn=embed)
    assert len(segs) >= 2  # fell back to turn-count, not one giant segment
