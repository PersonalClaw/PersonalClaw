"""An allowlisted config path is a PROMISE — so it either has a reader or it is gone (#465).

`_EDITABLE_CONFIG` is the runtime-editable surface: a path on it validates, persists and reads
back, which is exactly what convinces a caller the value took effect. An audit of it found
twelve paths with no reader anywhere in the tree. They were not broken in any way a test could
see — the round trip was perfect — and that is the defect: the only thing promising behaviour
was the `_meta` help text, which is the settings-copy surface a user actually reads.

Five of the twelve were wired between the filing and the first cleanup (`require_citations`,
`consolidate_min_cluster`, `consolidate_min_hours`, `self_model_enabled`, `retention_per_def`).
This file covers the two later wires and all six ruled deletions:

* **wired** `knowledge.synthesis_window` — `longrun`'s own note said
  "`KnowledgeConfig.synthesis_window` overrides it" while nothing overrode anything, so the one
  knob for the cost regression its help describes could not be turned.
* **wired** `knowledge.max_mentions_per_claim` — `Claim.add_mention` deduped by `source_ref`
  and applied no count cap, so the ceiling whose stated purpose is "a high-traffic claim cannot
  grow its evidence list without bound" did not bound it.
* **deleted** `knowledge.idempotent_persist` — its help named the reason it must not be a knob:
  content-derived identity is what stops a retried or rewound node writing a second
  near-identical article "that later reads as independent corroboration". Off would manufacture
  that corroboration. An invariant, not a setting.
* **deleted** `workflows.max_concurrent_nodes` — a bare total claiming to be "partitioned across
  typed lanes" when the two per-lane fields beside it ARE the live partition. Two knobs over one
  quantity, one honoured and one ignored.
* **deleted** `workflows.max_active_runs`, `learning.min_session_score`,
  `knowledge.lint_every_n_persists`, and `knowledge.conflict_model_pass` — four complete config
  round trips with no reachable behaviour behind them. Their producing/enforcement work is not
  part of an allowlist-hygiene fix.

🪤 A test that only proves round-trip CERTIFIES this defect rather than catching it: the
dataclass, the `load()` line, the `_meta` and the allowlist entry are all present in every inert
field. So nothing below asserts plumbing. Each test either drives a value through to an observed
behaviour change, or asserts the field's absence from both the schema and the write path.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from personalclaw.config import loader as config_loader
from personalclaw.config.learning import LearningConfig
from personalclaw.config.loader import AppConfig, KnowledgeConfig, WorkflowsConfig
from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG
from personalclaw.knowledge import semantics as sem
from personalclaw.workflows import bindings, longrun


def _knowledge(monkeypatch, **kwargs) -> None:
    """Point `AppConfig.load()` at a KnowledgeConfig built from `kwargs`.

    Patched at the loader rather than by writing a config file, for the reason the citation
    wiring tests give: a test that writes a file proves the file was written, not that the
    reader read it, and an unread file would make these assertions pass either way.
    """

    class _Cfg:
        knowledge = KnowledgeConfig(**kwargs)

    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda: _Cfg()))


def _unreadable(monkeypatch) -> None:
    def _boom():
        raise OSError("config unreadable")

    monkeypatch.setattr(AppConfig, "load", staticmethod(_boom))


# ── knowledge.synthesis_window: wired ──


def _sibling_outputs(n: int) -> list[dict]:
    """`n` significant findings, newest last. Significance is above the sibling-view floor so
    the FILTER cannot be what shortens the list — otherwise a window assertion would pass on a
    list the threshold had already cut, and prove nothing about the window."""
    return [{"summary": f"finding {i}", "significance": "high"} for i in range(n)]


@pytest.mark.parametrize("configured", [3, 7, 25])
def test_a_sibling_read_applies_the_configured_window(monkeypatch, configured):
    """The headline: the number in config is the number of items that cross the boundary.

    Parameterised across a value below, near and ABOVE the module default (20) on purpose. With
    only a below-default case, a reader that ignored config and returned `min(len, 20)` would
    pass for 3 and 7 and fail only at 25 — and 25 is also the direction a user moves the knob
    when they want MORE context, which an inert knob silently refuses.
    """
    _knowledge(monkeypatch, synthesis_window=configured)
    got = bindings._default_sibling_view(_sibling_outputs(40))
    assert len(got) == configured
    # The window keeps the NEWEST items — the last one produced must survive.
    assert got[-1]["summary"] == "finding 39"


def test_the_window_knob_changes_the_answer(monkeypatch):
    """Two configs, one input, different results. The assertion an inert knob cannot pass:
    before this change both calls returned the same 20 items."""
    _knowledge(monkeypatch, synthesis_window=5)
    narrow = bindings._default_sibling_view(_sibling_outputs(40))
    _knowledge(monkeypatch, synthesis_window=30)
    wide = bindings._default_sibling_view(_sibling_outputs(40))
    assert len(narrow) == 5 and len(wide) == 30


def test_a_bare_window_pipe_uses_the_configured_default(monkeypatch):
    """`| window` with no argument means "the default window", and the default is the user's.

    Kept consistent with `_default_sibling_view` deliberately: two spellings of "the default
    window" that disagreed would be the same class of defect one level down.
    """
    _knowledge(monkeypatch, synthesis_window=4)
    assert len(bindings._pipe_window(_sibling_outputs(40), None)) == 4


def test_an_explicit_window_pipe_still_overrides_the_config(monkeypatch):
    """`| window(N)` is a template author naming a bound. Config is the DEFAULT, not a ceiling —
    wiring the knob must not silently clamp an explicit request."""
    _knowledge(monkeypatch, synthesis_window=4)
    assert len(bindings._pipe_window(_sibling_outputs(40), 12)) == 12


def test_an_unreadable_config_keeps_the_bound(monkeypatch):
    """The failure the window exists to prevent is an UNBOUNDED sibling view, so a bad config
    read must not remove the bound — it falls back to the module default rather than to 0."""
    _unreadable(monkeypatch)
    assert bindings._synthesis_window() == longrun.DEFAULT_SYNTHESIS_WINDOW
    assert (
        len(bindings._default_sibling_view(_sibling_outputs(40)))
        == longrun.DEFAULT_SYNTHESIS_WINDOW
    )


# ── knowledge.max_mentions_per_claim: wired ──


def _claim_with(n_mentions: int) -> sem.Claim:
    claim = sem.Claim(id="c-1", statement="p99 rose to 900ms", confidence=0.8)
    for i in range(n_mentions):
        claim.add_mention(sem.Mention(source_ref=f"src-{i}", confidence=0.8, quote=""))
    return claim


def test_add_mention_stops_at_the_cap():
    """A claim at its ceiling refuses a genuinely new source rather than growing forever."""
    claim = _claim_with(3)
    accepted = claim.add_mention(
        sem.Mention(source_ref="src-new", confidence=0.9, quote=""), max_mentions=3
    )
    assert accepted is False
    assert len(claim.mentions) == 3
    assert [m.source_ref for m in claim.mentions] == ["src-0", "src-1", "src-2"]


def test_add_mention_below_the_cap_still_records():
    """The cap must not be an off-switch: under the ceiling a new source is recorded normally."""
    claim = _claim_with(2)
    assert (
        claim.add_mention(
            sem.Mention(source_ref="src-new", confidence=0.9, quote=""), max_mentions=3
        )
        is True
    )
    assert len(claim.mentions) == 3


def test_a_repeat_source_reads_as_already_spoke_not_as_full():
    """Dedup runs BEFORE the cap. Both reject, so the ORDER is the only thing distinguishing
    "this source already spoke" from "the list is full" — and at the ceiling, a re-read of an
    already-recorded source is the former."""
    claim = _claim_with(3)
    assert (
        claim.add_mention(sem.Mention(source_ref="src-1", confidence=0.9, quote=""), max_mentions=3)
        is False
    )
    assert len(claim.mentions) == 3


def test_a_nonpositive_cap_means_no_cap():
    """`max_mentions <= 0` is the uncapped form an ad-hoc caller wants, and the value an
    unreadable config returns — the failure this bounds is unbounded growth, so discarding real
    provenance on a bad config read would be the worse direction."""
    claim = _claim_with(50)
    assert (
        claim.add_mention(
            sem.Mention(source_ref="src-new", confidence=0.9, quote=""), max_mentions=0
        )
        is True
    )
    assert len(claim.mentions) == 51


def test_the_mention_cap_knob_is_read(monkeypatch):
    """The accessor is the knob's first reader; before this it had none at all."""
    _knowledge(monkeypatch, max_mentions_per_claim=7)
    assert sem.max_mentions_per_claim() == 7
    _unreadable(monkeypatch)
    assert sem.max_mentions_per_claim() == 0


def test_the_merge_path_applies_the_configured_cap(monkeypatch):
    """End to end through the production call site: the same stored claim, confirmed by a new
    source, stops accumulating at the configured ceiling.

    Driven through `_merge_claims` rather than `add_mention` because the parameter is only
    honest if something passes it — a capped method nobody hands the config to is the inert
    knob one layer deeper.
    """
    from personalclaw.action_providers.knowledge_persist_provider import _merge_claims

    _knowledge(monkeypatch, max_mentions_per_claim=2)
    stored = _claim_with(2).to_dict()
    merged, appended = _merge_claims(
        existing=[stored],
        incoming=[{"id": "c-1", "statement": "p99 rose to 900ms", "confidence": 0.8}],
        source_ref="src-brand-new",
    )
    assert appended == 0, "the ceiling refused the mention, so nothing was appended"
    assert len(merged[0]["mentions"]) == 2

    _knowledge(monkeypatch, max_mentions_per_claim=20)
    merged, appended = _merge_claims(
        existing=[stored],
        incoming=[{"id": "c-1", "statement": "p99 rose to 900ms", "confidence": 0.8}],
        source_ref="src-brand-new",
    )
    assert appended == 1, "with headroom the same source is recorded"
    assert len(merged[0]["mentions"]) == 3


def test_a_first_sighting_is_seeded_even_with_an_unreadable_config(monkeypatch):
    """The seeding `add_mention` is deliberately uncapped. A fresh claim holds no mentions so a
    ceiling cannot bind — but passing an unreadable config's 0 through would be indistinguishable
    from a cap of zero to a future reader, and a claim nobody is recorded as having said reads as
    unsourced. That is the state the seeding exists to prevent."""
    from personalclaw.action_providers.knowledge_persist_provider import _merge_claims

    _unreadable(monkeypatch)
    merged, appended = _merge_claims(
        existing=[],
        incoming=[{"id": "c-9", "statement": "fresh", "confidence": 0.5}],
        source_ref="src-0",
    )
    assert appended == 1
    assert len(merged[0]["mentions"]) == 1


# ── the six deletions: gone from BOTH the schema and the write path ──


@pytest.mark.parametrize(
    "path,owner,field",
    [
        ("knowledge.conflict_model_pass", KnowledgeConfig, "conflict_model_pass"),
        ("knowledge.idempotent_persist", KnowledgeConfig, "idempotent_persist"),
        ("knowledge.lint_every_n_persists", KnowledgeConfig, "lint_every_n_persists"),
        ("learning.min_session_score", LearningConfig, "min_session_score"),
        ("workflows.max_active_runs", WorkflowsConfig, "max_active_runs"),
        ("workflows.max_concurrent_nodes", WorkflowsConfig, "max_concurrent_nodes"),
    ],
)
def test_a_deleted_knob_is_gone_from_the_schema_and_the_allowlist(path, owner, field):
    """Both halves, in one assertion per field, because either alone leaves a lie.

    A field removed from the dataclass but left on the allowlist makes PATCH the only surface
    that still believes in it; left on the dataclass but off the allowlist it round-trips
    through `config.json` while the UI cannot reach it. The clean break is both.
    """
    assert path not in _EDITABLE_CONFIG, f"{path} is still writable"
    names = {f.name for f in dataclasses.fields(owner)}
    assert field not in names, f"{owner.__name__}.{field} is still declared"


def test_stored_values_for_deleted_knobs_are_ignored(tmp_path, monkeypatch):
    """An installed home may still carry every removed key. Explicit load mappings ignore them."""
    monkeypatch.setattr(config_loader, "config_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "knowledge": {
                    "conflict_model_pass": False,
                    "idempotent_persist": False,
                    "lint_every_n_persists": 999,
                },
                "learning": {"min_session_score": 0.9},
                "workflows": {
                    "max_active_runs": 1,
                    "max_concurrent_nodes": 64,
                },
            }
        ),
        encoding="utf-8",
    )

    cfg = AppConfig.load()
    for owner, fields in [
        (
            cfg.knowledge,
            ("conflict_model_pass", "idempotent_persist", "lint_every_n_persists"),
        ),
        (cfg.learning, ("min_session_score",)),
        (cfg.workflows, ("max_active_runs", "max_concurrent_nodes")),
    ]:
        for field in fields:
            assert not hasattr(owner, field)


def test_the_per_lane_caps_are_what_actually_bounds_concurrency():
    """The reason `max_concurrent_nodes` was deleted rather than wired: the quantity it claimed
    to partition is already partitioned, by two live fields it never consulted."""
    caps = WorkflowsConfig(max_concurrent_llm_nodes=9, max_concurrent_io_nodes=3).lane_caps()
    assert caps["llm"] == 9 and caps["io"] == 3
