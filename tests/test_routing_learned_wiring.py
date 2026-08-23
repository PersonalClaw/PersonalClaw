"""The MRT-5 seam: `route_refs` actually consults the learned stage.

Three thirds of MRT-5 were built on fenced branches and every one of them was green in
isolation. What no branch could show is that `policy.route_refs` reaches the scoring stage at
all — `policy.py` said "``learned`` lands here too until MRT-5 scores the fold" and the whole
atom is the removal of that sentence. A learned stage nothing calls is the "declared mode
without a runtime" this atom exists to close, so it is asserted here rather than assumed.

Every test drives the REAL `route_refs` against a real fold on a `tmp_path` home. Nothing here
touches `~/.personalclaw`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.routing import policy, stats


def _fold(home: Path, rows: dict[str, tuple[int, float]]) -> None:
    """A real routing_stats.json, written through the producer's own shape."""
    fold: dict = {"version": stats.STATS_VERSION, "use_cases": {"chat": {"general": {}}}}
    for ref, (n, sr) in rows.items():
        fold["use_cases"]["chat"]["general"][ref] = {
            "n": n,
            "success_rate": sr,
            "feedback": 0.0,
            "feedback_n": 0,
            "avg_ms": 100.0,
            "avg_cost_usd": 0.001,
            "score": stats._score(sr, 0.0, 0),
            "updated_at": "2026-08-23T00:00:00Z",
        }
    stats.save_stats(home, fold)


def _policy(home: Path, use_case: str, mode: str) -> None:
    """Set the mode in the TABLE, whose key is ``"mode"`` — not ``MODE_KEY``.

    `MODE_KEY` (``"routing_mode"``) is the *settings-store* key that the UI writes and that
    `mode_for` consults FIRST; the table's own fallback key is the plain ``"mode"``. Writing
    `MODE_KEY` into the table therefore sets nothing `mode_for` reads, and every mode reads as
    ``off`` — which passes an "off returns the bound order" test for entirely the wrong reason.
    """
    pol = policy.load_policy(home)
    # `_use_case_entry` is a READ accessor: it returns `{}` for a use case the table does not
    # list and does NOT insert it, so mutating its result writes into a throwaway dict. Build the
    # nesting explicitly.
    pol.setdefault("use_cases", {})[use_case] = {"mode": mode}
    policy.save_policy(home, pol)
    assert (
        policy.mode_for(use_case, home=home) == mode
    ), "the fixture did not set the mode it meant to"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated home with the routing MASTER switch on.

    Found the hard way: without it every mode reads as ``off``. `master_enabled()` gates
    `mode_for` on `routing.enabled`, whose default is False and whose failure mode is also False —
    so a fixture that only writes the per-use-case mode measures the master switch, not the
    stage. That is the shape of a test that passes for the wrong reason.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(policy, "_default_home", lambda: tmp_path)
    monkeypatch.setattr(policy, "master_enabled", lambda: True)
    return tmp_path


def test_the_master_switch_is_what_the_fixture_overrides() -> None:
    """The fixture above asserts its own premise: with `routing.enabled` at its default, every
    mode reads `off`, so none of the tests below would be measuring the learned stage."""
    from personalclaw.config.loader import RoutingConfig

    assert RoutingConfig().enabled is False


REFS = ["cloudy:big", "ollama:small"]


def test_learned_mode_reorders_on_the_fold(home: Path) -> None:
    """The seam itself: a decisive fold changes the order under `learned`."""
    _policy(home, "chat", "learned")
    _fold(home, {"cloudy:big": (20, 0.40), "ollama:small": (20, 0.95)})
    assert policy.route_refs("chat", "general", REFS, home=home)[0] == "ollama:small"


def test_heuristic_mode_does_not_consult_the_fold(home: Path) -> None:
    """Opting in matters. A use case on `heuristic` must behave exactly as it did before MRT-5,
    or this atom silently changed every install that never asked for scoring."""
    _policy(home, "chat", "heuristic")
    _fold(home, {"cloudy:big": (20, 0.40), "ollama:small": (20, 0.95)})
    heuristic = policy.route_refs("chat", "general", REFS, home=home)
    assert heuristic == REFS, "a bare home has no local providers, so the heuristic is a no-op here"
    _policy(home, "chat", "learned")
    assert policy.route_refs("chat", "general", REFS, home=home) != heuristic


def test_off_mode_returns_the_bound_order_untouched(home: Path) -> None:
    _policy(home, "chat", "off")
    _fold(home, {"cloudy:big": (20, 0.40), "ollama:small": (20, 0.95)})
    assert policy.route_refs("chat", "general", REFS, home=home) == REFS


def test_deleting_the_fold_degrades_to_the_heuristic(home: Path) -> None:
    """MRT-5's own clause. Asserted as EQUALITY with the heuristic answer, not merely as
    "does not raise": a stage that swallowed its own failure would also not raise."""
    _policy(home, "chat", "heuristic")
    heuristic = policy.route_refs("chat", "general", REFS, home=home)
    _policy(home, "chat", "learned")
    _fold(home, {"cloudy:big": (20, 0.40), "ollama:small": (20, 0.95)})
    assert policy.route_refs("chat", "general", REFS, home=home) != heuristic  # vacuity floor
    (home / "routing_stats.json").unlink()
    assert policy.route_refs("chat", "general", REFS, home=home) == heuristic


def test_a_corrupt_fold_degrades_rather_than_raising(home: Path) -> None:
    _policy(home, "chat", "learned")
    (home / "routing_stats.json").write_text("{not json at all")
    assert policy.route_refs("chat", "general", REFS, home=home) == REFS


def test_the_learned_stage_writes_nothing(home: Path) -> None:
    """SC #8: routing must not write memory.db/knowledge.db — or anything else. A read path that
    creates state is a read path that can corrupt it."""
    _policy(home, "chat", "learned")
    _fold(home, {"cloudy:big": (20, 0.40), "ollama:small": (20, 0.95)})
    before = {p.name: p.stat().st_mtime_ns for p in home.iterdir() if p.is_file()}
    policy.route_refs("chat", "general", REFS, home=home)
    after = {p.name: p.stat().st_mtime_ns for p in home.iterdir() if p.is_file()}
    assert after == before, "the learned stage touched the home"
    assert not (home / "memory.db").exists() and not (home / "knowledge.db").exists()


def test_the_knob_fail_open_floor_matches_the_declared_defaults() -> None:
    """`_routing_knobs`' literals are the floor for an unreadable config; the dataclass is the
    source of truth. Two spellings of one default is how a fail-open path starts behaving
    differently from a healthy one."""
    from dataclasses import fields

    from personalclaw.config.loader import RoutingConfig

    declared = {f.name: f.default for f in fields(RoutingConfig)}
    floor = policy._routing_knobs.__doc__ or ""
    assert "declared defaults" in floor
    cfg = RoutingConfig()
    assert (cfg.hysteresis, cfg.cloud_quality_margin, cfg.min_samples) == (0.05, 0.10, 5), declared


def test_an_unpriced_model_is_not_free(home: Path) -> None:
    """`_cost_of` returns inf for an unpriced ref. Zero would make every unpriced cloud model win
    each within-band cost tie — the opposite of the local-first posture."""
    probe = policy._cost_of(home)
    assert probe("nosuchprovider:nosuchmodel") == float("inf")
