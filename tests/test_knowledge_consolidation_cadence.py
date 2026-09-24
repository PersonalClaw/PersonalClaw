"""The consolidation cadence: do the two config knobs actually reach the gate?

Before KL-14's consolidation clause, `knowledge.consolidate_min_hours` and
`knowledge.consolidate_min_cluster` round-tripped fully (dataclass + `_meta` + `load()` +
the `_EDITABLE_CONFIG` PATCH allowlist) and had **no reader anywhere**. The provider gated
itself on `consolidation.MIN_HOURS_BETWEEN_PASSES` / `MIN_CLUSTER_SIZE` — module constants
whose values happen to equal the config defaults, which is exactly why nothing looked wrong.
A user could widen the floor in Settings and the next pass ran on the constant.

So every assertion here goes through the provider or the pass and observes the OUTCOME. None
of them read the config back: reading back a value you just wrote proves the round-trip, which
was never broken, and would have passed before the fix.

**No test here makes a model call.** The model seam is `_apply` / `_write_summary`, reached
only when `action_config` carries `apply: true` AND a non-empty `summaries` list. Every call
below either omits `apply` (the provider's documented DRY-RUN default) or goes through
`run_consolidation_pass`, which passes `{}`. `test_pass_never_reaches_the_model_seam` pins
that shut by making `_apply` raise if anything ever calls it.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.action_providers import knowledge_maintain_provider as kmp
from personalclaw.action_providers.base import ActionContext
from personalclaw.knowledge import consolidation

# Six items the token metric (Jaccard, floor 0.30, no embedder) groups into ONE cluster of 6,
# sized above the default `consolidate_min_cluster` of 5 so a raised floor has somewhere to bite.
#
# The shared core has to be LARGE relative to each item's unique tail, and the tails have to be
# real: measured, an earlier fixture whose items differed only by a trailing index was collapsed
# to a single survivor by `pre_dedup` before `cluster_items` ever saw it, so the planner returned
# no clusters and every count assertion here read 0. These six sit at min pairwise similarity
# 0.65 — comfortably clear of both the 0.30 cluster floor and the near-duplicate hash.
_SHARED = (
    "postgres connection pooling pgbouncer transaction mode prepared statements "
    "session backend client ceiling idle rotation"
)
_TAILS = (
    "restarting drops idle clients",
    "server cursors break midway",
    "max conn binds first",
    "listen notify stops working",
    "advisory locks leak across",
    "temp tables vanish early",
)


def _items() -> list[consolidation.Item]:
    return [
        consolidation.Item(
            id=f"item-{n}",
            kind="fact",
            title=f"Pooling note {n}",
            summary=_SHARED,
            content=f"{_SHARED} {tail}",
        )
        for n, tail in enumerate(_TAILS)
    ]


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Never touch the real ~/.personalclaw."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))


@pytest.fixture(autouse=True)
def _no_store_or_embedder(monkeypatch):
    """Stub the store seam so the gate, not sqlite, is what these tests measure.

    `_load_items` and `_open_store` are the provider's only two doors to the database, and
    `_similarity_for` is its only door to an embedder — pinned to None so clustering uses the
    deterministic token floor and no test can reach a real embedding backend.
    """
    monkeypatch.setattr(kmp, "_open_store", lambda: object())
    monkeypatch.setattr(kmp, "_load_items", lambda store: _items())
    monkeypatch.setattr(kmp, "_similarity_for", lambda store: None)
    # Nothing has ever been consolidated, so the min-hours gate cannot bind by default.
    # Individual tests override this to put a recent pass on the clock.
    monkeypatch.setattr(kmp, "_hours_since_last_pass", lambda store: 10_000.0)


def _write_config(tmp_path, **knowledge):
    """Write a real config.json under the isolated home."""
    (tmp_path / "config.json").write_text(json.dumps({"knowledge": knowledge}))


def _run(action_config=None):
    """Execute the consolidate provider synchronously and return its decoded payload."""
    import asyncio

    provider = kmp.KnowledgeConsolidateActionProvider()
    result = asyncio.run(
        provider.execute(action_config or {}, ActionContext(event="", context="", payload={}))
    )
    assert result.success, result.error
    return json.loads(result.stdout)


# ── the min-hours knob reaches the gate ──


def test_min_hours_knob_declines_a_recent_pass(tmp_path, monkeypatch):
    """A high `consolidate_min_hours` DECLINES a store whose last pass was recent.

    This is the assertion that failed before the fix: the provider defaulted to
    `MIN_HOURS_BETWEEN_PASSES` (6), so a 48-hour floor was invisible and 12h-ago was admitted.
    """
    monkeypatch.setattr(kmp, "_hours_since_last_pass", lambda store: 12.0)
    _write_config(tmp_path, consolidate_min_hours=48)

    payload = _run()

    assert payload["ran"] is False
    assert "floor 48" in payload["reason"]


def test_min_hours_knob_admits_the_same_store_when_lowered(tmp_path, monkeypatch):
    """Same store, same clock, low floor — admitted. The knob is the only variable."""
    monkeypatch.setattr(kmp, "_hours_since_last_pass", lambda store: 12.0)
    _write_config(tmp_path, consolidate_min_hours=1)

    payload = _run()

    assert payload["ran"] is True


def test_explicit_min_hours_overrides_a_high_knob(tmp_path, monkeypatch):
    """A node naming `min_hours: 1` still wins over a 48h config floor."""
    monkeypatch.setattr(kmp, "_hours_since_last_pass", lambda store: 12.0)
    _write_config(tmp_path, consolidate_min_hours=48)

    payload = _run({"min_hours": 1})

    assert payload["ran"] is True


def test_explicit_min_hours_overrides_a_low_knob(tmp_path, monkeypatch):
    """The override binds in BOTH directions — a node may also be stricter than the knob.

    Without this direction the "override" could be a max() and still pass the test above.
    """
    monkeypatch.setattr(kmp, "_hours_since_last_pass", lambda store: 12.0)
    _write_config(tmp_path, consolidate_min_hours=1)

    payload = _run({"min_hours": 48})

    assert payload["ran"] is False
    assert "floor 48" in payload["reason"]


# ── the min-cluster knob reaches `plan_consolidation(min_size=...)` ──


def test_min_cluster_knob_reaches_the_planner(tmp_path):
    """A floor above the available cluster size drops the cluster.

    Six mutually-similar items cluster at the default floor of 5. Raising the knob to 50 must
    leave nothing planned — which only happens if the knob reached `min_size`.
    """
    _write_config(tmp_path, consolidate_min_cluster=50)

    payload = _run()

    assert payload["ran"] is True
    assert payload["plan"]["clusters"] == []


def test_min_cluster_knob_admits_the_cluster_when_lowered(tmp_path):
    """Same six items, low floor — the cluster survives."""
    _write_config(tmp_path, consolidate_min_cluster=2)

    payload = _run()

    assert len(payload["plan"]["clusters"]) == 1
    assert payload["plan"]["clusters"][0]["size"] == 6


def test_explicit_min_cluster_size_overrides_the_knob(tmp_path):
    """Both directions again: the node's `min_cluster_size` wins either way."""
    _write_config(tmp_path, consolidate_min_cluster=50)
    assert len(_run({"min_cluster_size": 2})["plan"]["clusters"]) == 1

    _write_config(tmp_path, consolidate_min_cluster=2)
    assert _run({"min_cluster_size": 50})["plan"]["clusters"] == []


# ── `run_consolidation_pass` — the host's sync entry point ──


def test_pass_returns_a_positive_count_when_it_finds_work(tmp_path):
    """VACUITY. The pass must return a POSITIVE number when there is real work.

    Without this, a `return 0` stub would satisfy every "declined returns 0" assertion above
    and the whole file would pass while measuring nothing.
    """
    _write_config(tmp_path, consolidate_min_cluster=2)

    assert kmp.run_consolidation_pass(batch_size=0) == 1


def test_declined_pass_returns_zero_without_erroring(tmp_path, monkeypatch):
    """A gate that says "not yet" is a normal outcome: 0, no exception."""
    monkeypatch.setattr(kmp, "_hours_since_last_pass", lambda store: 1.0)
    _write_config(tmp_path, consolidate_min_hours=48)

    assert kmp.run_consolidation_pass(batch_size=0) == 0


def test_pass_honours_the_min_cluster_knob(tmp_path):
    """The knob reaches the gate THROUGH the pass, not just through a direct execute()."""
    _write_config(tmp_path, consolidate_min_cluster=50)

    assert kmp.run_consolidation_pass(batch_size=0) == 0


def test_pass_never_returns_negative_on_failure(monkeypatch):
    """An exploding store costs 0, never a raised exception into the host's tick."""

    def _boom():
        raise RuntimeError("store is gone")

    monkeypatch.setattr(kmp, "_open_store", _boom)

    assert kmp.run_consolidation_pass(batch_size=0) == 0


def test_unreadable_config_does_not_break_the_pass(tmp_path, monkeypatch):
    """`AppConfig.load` raising must fall back to the constants, not into the host."""
    from personalclaw.config.loader import AppConfig

    def _boom(cls=None):
        raise OSError("config.json is mid-write")

    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: _boom()))

    # The constants admit this store (nothing consolidated, cluster of 6 >= floor of 5), so a
    # clean fallback is observable as a normal positive result rather than a swallowed 0.
    assert kmp.run_consolidation_pass(batch_size=0) == 1


def test_unreadable_config_falls_back_to_the_constants(monkeypatch):
    """The fallback is the CONSTANTS specifically, not zero and not a hardcoded pair."""
    from personalclaw.config.loader import AppConfig

    monkeypatch.setattr(
        AppConfig, "load", classmethod(lambda cls: (_ for _ in ()).throw(OSError("nope")))
    )

    assert kmp._config_gate_defaults() == (
        consolidation.MIN_HOURS_BETWEEN_PASSES,
        consolidation.MIN_CLUSTER_SIZE,
    )


def test_pass_never_reaches_the_model_seam(tmp_path, monkeypatch):
    """The cadence is a DRY RUN: `_apply` (the model-call + archive path) is never entered.

    `run_consolidation_pass` deliberately does not apply — applying writes a model-authored
    summary and archives every input item, and no config knob authorises that unattended.
    """

    async def _never(*args, **kwargs):
        raise AssertionError("the cadence must not apply — _apply reached")

    monkeypatch.setattr(kmp, "_apply", _never)
    _write_config(tmp_path, consolidate_min_cluster=2)

    assert kmp.run_consolidation_pass(batch_size=0) == 1


def test_pass_signature_matches_the_host_contract():
    """Keyword-only `batch_size`, int return — what `maintenance.register_pass` calls."""
    import inspect

    sig = inspect.signature(kmp.run_consolidation_pass)
    assert list(sig.parameters) == ["batch_size"]
    assert sig.parameters["batch_size"].kind is inspect.Parameter.KEYWORD_ONLY
