"""Memory heat on the ONE decay kernel (LEARN-R6f / WF2LEA-9 part 2).

The migration is a behaviour change, so both behaviours are pinned here: the numbers
the private `e^(−days/30)` curve produced and the numbers the kernel produces. A test
that only asserted "heat calls the kernel" would pass a rename, and a rename is
explicitly not what R6f asks for.

The doctrine tests are the other half. `strength` gates eviction and review only — so
the kernel's VERDICT must never reorder retrieval, and recency must never be able to
out-argue usage evidence on its own. Both are asserted against the live ranking paths
rather than against the formula, because the formula is not what a caller sees.
"""

from __future__ import annotations

import math

import pytest

from personalclaw.learning.decay import KIND_MULTIPLIERS, evaluate, strength
from personalclaw.memory_record import (
    _DECAY_PROFILES,
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    decay_profile,
)
from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import VectorMemoryStore


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Nothing here may touch the real home."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)


@pytest.fixture
def svc(tmp_path):
    store = VectorMemoryStore(db_path=tmp_path / "m.db", embedding_dim=3)
    store.init()
    store.embed_fn = lambda t: [1.0, 0.0, 0.0]
    return MemoryService.over_vector_store(store)


def _now():
    """A frozen clock. Pinned numbers need one, and `heat` takes `now` for this reason."""
    from datetime import datetime, timezone

    return datetime(2026, 1, 31, 12, 0, 0, tzinfo=timezone.utc)


def _stamp(days_ago: float) -> str:
    from datetime import timedelta

    return (_now() - timedelta(days=days_ago)).isoformat()


def _rec(kind: MemoryKind, *, days: float, visits: int = 0, importance: float = 0.5):
    return MemoryRecord(
        id=f"{kind.value}.x",
        kind=kind,
        text="t",
        importance=importance,
        recall_count=visits,
        last_accessed_at=_stamp(days),
    )


# ── The migration: old numbers vs new numbers ──

#: What the private curve produced at 30 days with no visits, for EVERY kind — it had
#: no per-kind rate and no importance axis, so one number covered all eight classes.
OLD_HEAT_30D = 0.5 * math.exp(-1.0)

#: What the kernel produces, per kind, same inputs. Pinned because these are the
#: numbers the promotion gate and the retrieval boost now see.
NEW_HEAT_30D = {
    # A user-taught approval rule is the slowest-decaying class (0.4, matching
    # "strategy") — a standing instruction outlives the digest that minted it.
    MemoryKind.APPROVAL: 0.423373,
    MemoryKind.SEMANTIC: 0.406126,
    MemoryKind.PREFERENCE: 0.406126,
    MemoryKind.SELF_PERSONA: 0.406126,
    MemoryKind.LESSON: 0.373712,
    MemoryKind.COMMITMENT: 0.373712,
    MemoryKind.NOTE: 0.329877,
    MemoryKind.PROCEDURAL: 0.303549,
    MemoryKind.EPISODIC: 0.291183,
}


def test_old_heat_curve_is_gone():
    """No kind still produces the private curve's answer. This is the behaviour change."""
    assert round(OLD_HEAT_30D, 6) == 0.18394
    for kind in MemoryKind:
        assert _rec(kind, days=30).heat(now=_now()) != pytest.approx(OLD_HEAT_30D, abs=1e-6)


@pytest.mark.parametrize("kind", list(MemoryKind))
def test_new_heat_is_the_kernel(kind):
    got = _rec(kind, days=30).heat(now=_now())
    assert got == pytest.approx(NEW_HEAT_30D[kind], abs=1e-6)
    # …and it is literally the kernel, not a coincidence at one point on the curve.
    expected = 0.5 * strength(kind=decay_profile(kind), active_days_since_use=30.0, importance=0.5)
    assert got == pytest.approx(expected, abs=1e-9)


def test_kind_now_changes_the_recency_term():
    """The private curve aged an episodic fragment exactly like a distilled fact."""
    episodic = _rec(MemoryKind.EPISODIC, days=30).heat(now=_now())
    semantic = _rec(MemoryKind.SEMANTIC, days=30).heat(now=_now())
    assert episodic < semantic
    assert KIND_MULTIPLIERS["episodic"] > KIND_MULTIPLIERS["semantic"]


def test_importance_now_slows_decay():
    """Importance is a second axis, not an exemption — and it was absent before."""
    plain = _rec(MemoryKind.SEMANTIC, days=60, importance=0.0).heat(now=_now())
    important = _rec(MemoryKind.SEMANTIC, days=60, importance=1.0).heat(now=_now())
    assert important > plain
    # Not an exemption: even at importance 1.0 the term is below a fresh record's.
    fresh = _rec(MemoryKind.SEMANTIC, days=0, importance=0.0).heat(now=_now())
    assert important < fresh


def test_visit_term_is_unchanged():
    """Only the recency term moved. Nine visits still reads ~1.0 on the log term."""
    rec = _rec(MemoryKind.SEMANTIC, days=0, visits=9)
    assert rec.heat(now=_now()) == pytest.approx(0.7 * 1.0 + 0.5 * 1.0, abs=1e-6)


def test_idle_days_is_none_for_an_unstamped_record():
    """None, not 0.0 — "never touched" must not read as "touched just now"."""
    bare = MemoryRecord(id="x", kind=MemoryKind.SEMANTIC)
    bare.created_at = ""
    bare.updated_at = ""
    assert bare.idle_days(now=_now()) is None
    assert bare.heat(now=_now()) == pytest.approx(0.0, abs=1e-9)


# ── The closed enum, enumerated exhaustively ──


def test_every_memory_kind_has_a_profile_the_kernel_knows():
    assert set(_DECAY_PROFILES) == set(MemoryKind)
    for kind, profile in _DECAY_PROFILES.items():
        assert profile in KIND_MULTIPLIERS, f"{kind.value} maps to unknown profile {profile}"


def test_an_unmapped_kind_raises_rather_than_defaulting():
    """A new memory class with no decay decision must not silently age like a skill."""
    with pytest.raises(ValueError, match="no decay profile"):
        decay_profile("teleportation")  # type: ignore[arg-type]


# ── Doctrine: strength gates eviction and review ONLY ──


def test_the_kernels_verdict_never_reorders_retrieval(svc):
    """A record the kernel would PRUNE still ranks by its heat.

    Would fail if the eviction verdict leaked into ranking: `used` is 30 active days
    idle and prunable, `unused` is fresh and healthy, and `used` must still come
    first because it is the one with usage evidence.
    """
    profile = decay_profile(MemoryKind.PROCEDURAL)
    used_verdict = evaluate(kind=profile, active_days_since_use=400.0, importance=0.0)
    unused_verdict = evaluate(kind=profile, active_days_since_use=0.0, importance=0.0)
    assert used_verdict.prune, "the premise: the kernel would evict the used record"
    assert not unused_verdict.prune

    keys: dict[str, str] = {}
    for tool, visits, days in (("decayed-and-used", 9, 400.0), ("fresh-and-unused", 0, 0.0)):
        key = svc.record_procedural(tool=tool, task_shape="shape", outcome="success")
        keys[tool] = key
        svc._vs.db.execute(
            # `updated_at` is the semantic table's recency stamp — `heat` reads
            # last_accessed_at → updated_at → created_at, and this table has no
            # last_accessed_at column.
            "UPDATE semantic_memory SET scope=?, recall_count=?, updated_at=? WHERE key=?",
            (MemoryScope.GLOBAL.value, visits, _stamp(days), key),
        )
    svc._vs.db.commit()

    priors = svc.procedural_priors()
    assert len(priors) == 2
    assert priors[0]["key"] == keys["decayed-and-used"], (
        "the prunable-but-used record must rank first — the eviction verdict is not a "
        "ranking input"
    )


def test_strength_alone_cannot_win_a_rank():
    """Maximum recency with no usage evidence loses to usage with heavy decay.

    This is the feedback loop the doctrine forbids: if recency could win outright, a
    thing would surface because it surfaced, and nothing unpopular could ever recover.
    """
    fresh_unused = _rec(MemoryKind.SEMANTIC, days=0, visits=0).heat(now=_now())
    stale_used = _rec(MemoryKind.SEMANTIC, days=365, visits=9).heat(now=_now())
    assert stale_used > fresh_unused
    # The margin is structural (0.7 usage vs 0.5 recency), not incidental.
    assert 0.7 * (math.log1p(9) / math.log(10)) > 0.5 * 1.0


def test_memory_ranking_modules_never_import_the_eviction_verdict():
    """A source-level rail: `evaluate`/`DecayVerdict` may not reach a ranking path.

    `heat` legitimately uses `strength`; the VERDICT is a different object with a
    different job, and importing it here is how the doctrine would quietly break.
    """
    from pathlib import Path

    import personalclaw.memory_service as ms
    import personalclaw.memory_vault as mv

    for module in (ms, mv):
        src = Path(module.__file__).read_text()
        assert "DecayVerdict" not in src, f"{module.__name__} imports the eviction verdict"
        assert "evaluate_decay" not in src
