"""``SupervisorPolicy`` — the parser, the authoring-time validator, and the honesty rail (PP-14).

A loop is a graph shape plus a supervisor policy. The shape existed; the policy was
implemented twice with each half incomplete. This atom lands the ONE declaration, its
tolerant parser and its typed authoring-time validator — and lands it **deliberately inert**.

The centre of this file is the two-directional honesty rail (the `WF2LOO-12` convention):
a control with no caller must SAY it has no caller, and that claim must go RED if it ever
stops being true in EITHER direction. It is a static AST census over ``src/personalclaw`` —
never an import-time probe — carrying a vacuity floor so it cannot pass by scanning nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path

from personalclaw.workflows import supervisor_policy as sp
from personalclaw.workflows.autonomy import Attention
from personalclaw.workflows.judge_contract import RubricCriterion
from personalclaw.workflows.loop_middleware import DEFAULT_LADDER, Rung
from personalclaw.workflows.scope import ScopeMode
from personalclaw.workflows.supervisor_policy import (
    SupervisorPolicy,
    parse_supervisor_policy,
)
from personalclaw.workflows.validator import validate_spec

# ── helpers ──


def _codes(supervisor: object) -> set[str]:
    """Validate a counted loop carrying `supervisor` and return the issue codes."""
    spec = {
        "name": "wf",
        "root": {
            "kind": "loop",
            "id": "lp",
            "config": {"mode": "counted", "n": 1, "supervisor": supervisor},
            "body": {"kind": "infer", "id": "step", "config": {"prompt": "go"}},
        },
    }
    return {i.code for i in validate_spec(spec).issues}


# ── the two-directional honesty rail (the heart of PP-14) ──


def _count_policy_callers(tree: ast.AST) -> int:
    """Count CALLS that construct a ``SupervisorPolicy`` or invoke ``parse_supervisor_policy``.

    Call-based, matching the `detectors.gate` census precedent: a "production caller" is code
    that WIRES the policy into behaviour, not code that merely imports the closed field set to
    validate against it. Catches both bare (`parse_supervisor_policy(...)`) and attribute
    (`sp.SupervisorPolicy(...)`) call shapes.
    """
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        else:
            name = ""
        if name in ("SupervisorPolicy", "parse_supervisor_policy"):
            count += 1
    return count


def _census() -> tuple[dict[str, int], int]:
    """Scan every ``src/personalclaw`` module except the declaration itself."""
    module_path = Path(sp.__file__).resolve()
    root = module_path.parents[1]  # .../src/personalclaw
    hits: dict[str, int] = {}
    scanned = 0
    for py in root.rglob("*.py"):
        if py.resolve() == module_path:
            continue
        scanned += 1
        n = _count_policy_callers(ast.parse(py.read_text()))
        if n:
            hits[str(py.relative_to(root))] = n
    return hits, scanned


def test_the_census_can_actually_find_a_caller():
    """Vacuity control on the DETECTOR: a green census is meaningless unless the matcher can
    detect the thing it swears is absent. This is the positive half of the two-sided floor."""
    tree = ast.parse(
        "from personalclaw.workflows.supervisor_policy import parse_supervisor_policy\n"
        "parse_supervisor_policy({})\n"
        "SupervisorPolicy()\n"
    )
    assert _count_policy_callers(tree) == 2


def test_the_census_scans_real_source_not_nothing():
    """Vacuity control on the SCAN: a rule that matched an empty file set would pass forever."""
    _hits, scanned = _census()
    assert scanned >= 50, f"census only scanned {scanned} modules — it is not seeing src/"


def test_DIRECTION_1_no_production_caller_exists_while_the_marker_claims_zero():
    """RED if a production caller of ``SupervisorPolicy`` appears while the marker still says
    zero — the marker would be lying. (Falsification probe 1 adds a fake caller under src/.)"""
    hits, _scanned = _census()
    assert not hits, (
        "SupervisorPolicy has production caller(s) but the module still claims zero — "
        f"either wire the marker (PP-15) or remove the caller: {hits}"
    )


def test_DIRECTION_2_the_inert_module_declares_itself_inert():
    """RED if the "zero callers / PP-15 wires this" marker is removed while callers are still
    zero — an inert control that stopped declaring itself inert. (Falsification probe 2 strips
    the marker.)"""
    src = Path(sp.__file__).read_text()
    assert "zero production callers" in src, "the module dropped its zero-caller marker"
    assert "PP-15" in src, "the module no longer names its wiring owner"
    assert sp.WIRING_OWNER == "PP-15"
    assert sp.HAS_ZERO_PRODUCTION_CALLERS is True


def test_the_marker_and_reality_agree():
    """The coupling invariant both probes derive from: the claim must equal the fact. Flipping
    the constant OR adding a caller breaks this, so the marker can never silently drift."""
    hits, _scanned = _census()
    assert sp.HAS_ZERO_PRODUCTION_CALLERS == (len(hits) == 0)


# ── the parser: tolerant reads, closed field set ──


def test_a_missing_field_tolerates_with_the_default():
    """An empty config yields a fully-defaulted policy — never a crash (tolerant read)."""
    pol = parse_supervisor_policy({})
    assert isinstance(pol, SupervisorPolicy)
    assert pol.escalation_ladder == DEFAULT_LADDER
    assert pol.judge_model_tier == "standard"
    assert pol.hitl_posture is Attention.AFK
    assert pol.reproduce_before_ship is False
    assert pol.budget_max_cycles == 0
    assert pol.rubric == ()


def test_a_non_dict_config_is_tolerated():
    assert parse_supervisor_policy(None) == SupervisorPolicy()
    assert parse_supervisor_policy("nonsense") == SupervisorPolicy()
    assert parse_supervisor_policy([1, 2, 3]) == SupervisorPolicy()


def test_a_malformed_value_never_raises_and_keeps_the_default():
    """A model that answers garbage for every field must run with the strict defaults."""
    pol = parse_supervisor_policy(
        {
            "judge_model_tier": "wizard",  # unknown tier → default
            "hitl_posture": "sometimes",  # unknown posture → AFK
            "marginal_value_band": "banana",  # unusable → default band
            "gates": 42,  # not a dict → default StepConfig
            "budget": "lots",  # unusable → 0
            "rubric": "not-a-list",  # → ()
        }
    )
    assert pol.judge_model_tier == "standard"
    assert pol.hitl_posture is Attention.AFK
    assert pol.marginal_value_band == (sp.DEFAULT_MARGINAL_FLOOR, sp.DEFAULT_MARGINAL_TARGET)
    assert pol.budget_max_cycles == 0
    assert pol.rubric == ()


def test_an_unknown_top_level_field_is_ignored_by_the_parser():
    """The parser is lenient; the closed-set contract is enforced by the VALIDATOR, so a spec
    with a stray key still parses to something usable rather than failing to start."""
    pol = parse_supervisor_policy({"totally_made_up": True, "judge_model_tier": "fast"})
    assert pol.judge_model_tier == "fast"
    assert not hasattr(pol, "totally_made_up")


def test_well_formed_fields_reuse_the_existing_types():
    pol = parse_supervisor_policy(
        {
            "rubric": [{"criterion": "tests pass", "target_score": 2, "weight": 1.5}],
            "escalation_ladder": ["classified_retry", "surface"],
            "failure_mutations": {"malformed_output": "return valid JSON only"},
            "gates": {"min_dwell_secs": 3, "min_findings": 2, "metric_pass": 0.9},
            "marginal_value_band": {"floor": 1.0, "target": 3.0},
            "judge_model_tier": "reasoning",
            "reproduce_before_ship": True,
            "write_scope": {"paths": ["src/"], "mode": "reject"},
            "budget": {"max_cycles": 12},
            "hitl_posture": "hitl",
        }
    )
    assert pol.rubric == (RubricCriterion(criterion="tests pass", target_score=2, weight=1.5),)
    assert pol.escalation_ladder == (Rung.CLASSIFIED_RETRY, Rung.SURFACE)
    assert pol.failure_mutations == {"malformed_output": "return valid JSON only"}
    assert pol.gates.min_dwell_secs == 3.0
    assert pol.gates.min_findings == 2
    assert pol.gates.metric_pass == 0.9
    assert pol.marginal_value_band == (1.0, 3.0)
    assert pol.judge_model_tier == "reasoning"
    assert pol.reproduce_before_ship is True
    assert pol.write_scope.allowed_paths == ("src/",)
    assert pol.write_scope.mode == ScopeMode.REJECT
    assert pol.budget_max_cycles == 12
    assert pol.hitl_posture is Attention.HITL


def test_an_out_of_range_marginal_band_is_clamped_and_ordered():
    """Reuses ``clamp_marginal`` (0-5) and never lets floor exceed target."""
    pol = parse_supervisor_policy({"marginal_value_band": {"floor": 9.0, "target": -3.0}})
    lo, hi = pol.marginal_value_band
    assert 0.0 <= lo <= hi <= 5.0


def test_a_ladder_typo_drops_the_bad_rung_and_stays_surface_terminal():
    """Reuses ``loop_middleware._resolve_ladder`` — an unknown rung is dropped, SURFACE forced."""
    pol = parse_supervisor_policy({"escalation_ladder": ["classified_retry", "teleport"]})
    assert pol.escalation_ladder == (Rung.CLASSIFIED_RETRY, Rung.SURFACE)


def test_an_unknown_failure_class_key_is_dropped_by_the_parser():
    pol = parse_supervisor_policy(
        {"failure_mutations": {"malformed_output": "ok", "made_up_class": "x"}}
    )
    assert pol.failure_mutations == {"malformed_output": "ok"}


# ── the authoring-time validator: typed WF_ codes, closed field set ──


def test_no_supervisor_key_adds_no_issues():
    """The bundled loop population declares no `supervisor` — the rule must be silent on it."""
    spec = {
        "name": "wf",
        "root": {
            "kind": "loop",
            "id": "lp",
            "config": {"mode": "counted", "n": 1},
            "body": {"kind": "infer", "id": "s", "config": {"prompt": "go"}},
        },
    }
    codes = {i.code for i in validate_spec(spec).issues}
    assert not any(c.startswith("WF_SUPERVISOR") for c in codes)


def test_a_well_formed_supervisor_is_clean():
    codes = _codes(
        {
            "rubric": [{"criterion": "x"}],
            "escalation_ladder": ["fresh_session", "surface"],
            "failure_mutations": {"wrong_work": "re-read the goal"},
            "judge_model_tier": "fast",
            "hitl_posture": "afk",
        }
    )
    assert not any(c.startswith("WF_SUPERVISOR") for c in codes)


def test_an_unknown_field_is_a_typed_error():
    """The central contract: an unknown top-level field is ``WF_SUPERVISOR_UNKNOWN_FIELD``."""
    assert "WF_SUPERVISOR_UNKNOWN_FIELD" in _codes({"made_up_field": 1})


def test_a_non_object_supervisor_is_a_typed_error():
    assert "WF_SUPERVISOR_NOT_OBJECT" in _codes(["not", "an", "object"])


def test_a_bad_judge_model_tier_is_a_typed_error():
    assert "WF_SUPERVISOR_BAD_TIER" in _codes({"judge_model_tier": "wizard"})


def test_a_bad_escalation_rung_is_a_typed_error():
    assert "WF_SUPERVISOR_BAD_RUNG" in _codes({"escalation_ladder": ["classified_retry", "warp"]})


def test_a_bad_hitl_posture_is_a_typed_error():
    assert "WF_SUPERVISOR_BAD_HITL" in _codes({"hitl_posture": "maybe"})


def test_a_bad_failure_class_key_is_a_typed_error():
    assert "WF_SUPERVISOR_BAD_FAILURE_CLASS" in _codes({"failure_mutations": {"nope": "x"}})


def test_the_validator_accumulates_every_problem_at_once():
    """Never one-error-per-turn: a spec with three problems reports three codes together."""
    codes = _codes({"made_up": 1, "judge_model_tier": "wizard", "hitl_posture": "maybe"})
    assert {
        "WF_SUPERVISOR_UNKNOWN_FIELD",
        "WF_SUPERVISOR_BAD_TIER",
        "WF_SUPERVISOR_BAD_HITL",
    } <= codes
