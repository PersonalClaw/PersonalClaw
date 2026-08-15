"""AG-13 — the fourteen autonomy knobs consolidated into one declarative policy.

The load-bearing deliverable is the **behaviour-preservation matrix**: a table maps each of
the fourteen knobs to the ONE ``SupervisorPolicy`` field it now lives on
(``supervisor_policy.POLICY_KNOB_MAP``), and the matrix test below asserts, for a matrix of
shipped workflow-run profiles × bundled loop kinds × a template that sets a knob, that the
field each knob maps to carries that knob's CURRENT value — read from the knob's own home, so
the comparison is non-circular. If any composed value differs from today's for any run, that
is a write-scope/autonomy defect, not an improvement: this atom changes *where* autonomy is
declared, never *what* it permits.

The three falsification targets are called out at their tests:

1. ``test_the_behaviour_preservation_matrix`` — remap a knob's field (or the builder that
   fills it) and this reds, naming the knob.
2. ``test_tightest_wins_a_profile_cannot_widen_the_ceiling`` — make a looser policy win in the
   composition and this reds. Widening is the dangerous direction.
3. ``test_a_dotdot_pattern_does_not_widen_the_write_scope`` — ``normpath`` a write-scope
   PATTERN and this reds (``/a/**/../b`` collapses to ``/a/b``, silently widening an allow).
"""

from __future__ import annotations

from dataclasses import MISSING, fields
from typing import Any

import pytest

from personalclaw.config.loader import LoopsConfig
from personalclaw.guardrails import ceiling as C
from personalclaw.guardrails.budgets import Budget
from personalclaw.guardrails.policy import (
    HEADLESS,
    INTERACTIVE,
    SafetyProfile,
)
from personalclaw.loop.loop import Loop
from personalclaw.workflows.autonomy import Attention, Mode
from personalclaw.workflows.loop_middleware import DEFAULT_LADDER
from personalclaw.workflows.supervisor_policy import (
    POLICY_KNOB_MAP,
    BreakerLimits,
    SupervisorPolicy,
    WriteScope,
    compose,
    consolidate,
    resolve_field,
    write_scope_allows,
)


def _field_default(cls: type, name: str) -> Any:
    """Read a dataclass field's shipped default WITHOUT constructing the class (``Loop``
    takes required args). This is the knob's home talking, not a value copied into the test."""
    for f in fields(cls):
        if f.name == name:
            if f.default is not MISSING:
                return f.default
            return f.default_factory()  # type: ignore[misc]
    raise AssertionError(f"{cls.__name__} has no field {name!r}")


# ── today's answer, read from each knob's OWN home (never hardcoded) ──

LOOP_ATTENDED = _field_default(Loop, "attended")  # False
LOOP_MAX_CYCLES = _field_default(Loop, "max_cycles")  # 30
LOOP_IDLE_SECS = _field_default(Loop, "idle_secs")  # 120
LOOP_TRUST_TTL = _field_default(LoopsConfig, "trust_ttl_secs")  # 86400

# The five bundled loop KINDS ship (loop/kinds/): goal, code, design, research, general.
# None of them overrides attended / max_cycles / idle_secs / trust_ttl_secs — every kind
# inherits the Loop dataclass defaults above (verified: kinds read loop.max_cycles/attended,
# they do not re-default them). So each kind's row is the default-loop context.
BUNDLED_LOOP_KINDS = ("goal", "code", "design", "research", "general")


def _today_for(*, profile: SafetyProfile, attended: bool, max_cycles: int) -> dict[str, Any]:
    """The value each of the fourteen knobs holds TODAY for a run with this profile / loop
    posture, keyed by the knob name in POLICY_KNOB_MAP. Read from the homes, not invented."""
    hitl = Attention.HITL if attended else Attention.AFK
    return {
        "RunBudget": profile.budget.max_tokens,  # RunBudget default 0 == Budget default 0
        "runtime_hints.execution.single_active_feature": False,
        "require_hitl": hitl,
        "gate_policy auto-approval": profile.approval,
        "confirmation matrix + per-stage mute": hitl,
        "autonomy risk registry / floors / earned trust": Mode.FRAME_ONLY,
        "allowed_write_paths": (),
        "resilience breaker config": BreakerLimits(),
        "escalation_cfg.ladder": DEFAULT_LADDER,
        "loop trust_ttl_secs": LOOP_TRUST_TTL,
        "loop attended": hitl,
        "max_cycles": max_cycles,
        "idle_secs": LOOP_IDLE_SECS,
        "SafetyProfile": profile,
    }


class _Run:
    """One row of the matrix: a human name, the consolidated policy, and today's answers."""

    def __init__(self, name: str, policy: SupervisorPolicy, today: dict[str, Any]) -> None:
        self.name = name
        self.policy = policy
        self.today = today


def _matrix() -> list[_Run]:
    runs: list[_Run] = []
    # Workflow runs: unattended (HEADLESS) and human-watched (INTERACTIVE). A workflow run has
    # no loop cycle cap, so budget_max_cycles is 0 (uncapped) — RunBudget carries no cycle cap.
    runs.append(
        _Run(
            "workflow-run:headless",
            consolidate(profile=HEADLESS),
            _today_for(profile=HEADLESS, attended=False, max_cycles=0),
        )
    )
    runs.append(
        _Run(
            "workflow-run:interactive",
            consolidate(profile=INTERACTIVE, attended=True),
            _today_for(profile=INTERACTIVE, attended=True, max_cycles=0),
        )
    )
    # Every bundled loop kind: unattended, inheriting the Loop defaults (max_cycles=30 etc.).
    for kind in BUNDLED_LOOP_KINDS:
        runs.append(
            _Run(
                f"loop:{kind}",
                consolidate(
                    profile=HEADLESS,
                    attended=LOOP_ATTENDED,
                    max_cycles=LOOP_MAX_CYCLES,
                    idle_secs=LOOP_IDLE_SECS,
                    trust_ttl_secs=LOOP_TRUST_TTL,
                ),
                _today_for(profile=HEADLESS, attended=LOOP_ATTENDED, max_cycles=LOOP_MAX_CYCLES),
            )
        )
    # The one template that sets an autonomy knob: code-project declares
    # single_active_feature=true (verified against the 19 bundled templates).
    code_project = consolidate(profile=HEADLESS, single_active_feature=True)
    today_cp = _today_for(profile=HEADLESS, attended=False, max_cycles=0)
    today_cp["runtime_hints.execution.single_active_feature"] = True
    runs.append(_Run("template:code-project", code_project, today_cp))
    return runs


# ── 1. the map is complete and every field it names exists ──


def test_the_map_covers_the_fourteen_knobs_and_every_field_resolves():
    assert len(POLICY_KNOB_MAP) == 14, "AG-13 consolidates exactly fourteen knobs"
    knobs = [m.knob for m in POLICY_KNOB_MAP]
    assert len(set(knobs)) == 14, "a knob appears twice in the map"
    probe = SupervisorPolicy()
    for m in POLICY_KNOB_MAP:
        resolve_field(probe, m.field_path)  # AttributeError here = a field_path typo
    # Consolidation, not a fork: several knobs collapse onto one field — that IS the win.
    # Three HITL knobs share hitl_posture; the profile + gate posture share `autonomy`.
    heads = [m.field_path.split(".")[0] for m in POLICY_KNOB_MAP]
    assert heads.count("hitl_posture") == 3, "require_hitl + confirmation + attended → one field"
    assert heads.count("autonomy") >= 3, "RunBudget + gate approval + SafetyProfile → one object"


# ── 2. the behaviour-preservation matrix (FALSIFICATION 1) ──


def test_the_behaviour_preservation_matrix():
    """For every run in the matrix, the field each of the fourteen knobs maps to carries that
    knob's CURRENT value. Composed under the shipped default posture (no operator ceiling), so
    this is exactly what every install runs. FALSIFICATION 1: remap a knob's ``field_path`` in
    POLICY_KNOB_MAP, or change the builder that fills it, and one of these assertions reds
    naming the knob and the run."""
    runs = _matrix()
    assert len(runs) == 8, "2 workflow profiles + 5 loop kinds + 1 template"
    for run in runs:
        composed = compose(C.OPEN_CEILING, run.policy)
        for m in POLICY_KNOB_MAP:
            got = resolve_field(composed, m.field_path)
            want = run.today[m.knob]
            assert got == want, (
                f"[{run.name}] knob {m.knob!r} → {m.field_path}: consolidated {got!r} "
                f"!= today's {want!r} — this atom must not change what a run permits"
            )


def test_the_matrix_is_not_vacuous():
    """A guard on the matrix itself: it must actually observe the non-default values, or a
    remap could hide in a sea of shared defaults. Assert the distinguishing values are present
    in at least one run."""
    runs = _matrix()
    approvals = {r.today["gate_policy auto-approval"] for r in runs}
    assert approvals == {"hook_based", "ask"}, "both profiles' approval postures are exercised"
    assert any(r.today["max_cycles"] == LOOP_MAX_CYCLES for r in runs), "a loop cap (30) is seen"
    assert any(r.today["max_cycles"] == 0 for r in runs), "an uncapped workflow run is seen"
    assert any(
        r.today["runtime_hints.execution.single_active_feature"] for r in runs
    ), "the one WIP=1 template is seen"
    assert any(r.today["loop attended"] == Attention.HITL for r in runs), "an attended run is seen"


# ── 3. tightest-wins under Ceiling ∩ Profile (FALSIFICATION 2) ──


def test_tightest_wins_a_profile_cannot_widen_the_ceiling():
    """The composition is the SAME Ceiling ∩ Profile model every dispatch seam uses. A profile
    may only NARROW. FALSIFICATION 2: make a looser policy win (compose the wrong direction)
    and this reds — widening is the dangerous direction this atom exists to make impossible."""
    loose = consolidate(
        profile=SafetyProfile(name="loose", approval="auto", budget=Budget(max_tokens=10_000_000))
    )
    ceiling = C.parse_ceiling(
        {"scopes": {"approval": {"value": "ask"}, "budget": {"max_tokens": 1000}}}
    )
    composed = compose(ceiling, loose)
    assert composed.autonomy.approval == "ask", "a tighter ceiling approval must win"
    assert composed.autonomy.approval != "auto", "the loose profile value must NOT survive"
    assert composed.autonomy.budget.max_tokens == 1000, "a tighter ceiling token cap must win"
    # A profile that is ALREADY tighter than the ceiling keeps its own (narrowing is allowed).
    tight = consolidate(profile=SafetyProfile(name="tight", approval="ask"))
    loose_ceiling = C.parse_ceiling({"scopes": {"approval": {"value": "auto"}}})
    assert compose(loose_ceiling, tight).autonomy.approval == "ask"


def test_open_ceiling_is_identity_so_consolidation_is_not_a_behaviour_change():
    """No operator ceiling (the posture every install ships with) leaves the policy untouched:
    consolidating the declaration cannot, by itself, change what a run permits."""
    policy = consolidate(profile=INTERACTIVE, max_cycles=LOOP_MAX_CYCLES, attended=True)
    composed = compose(C.OPEN_CEILING, policy)
    assert composed == policy
    assert composed.autonomy is policy.autonomy


# ── 4. the normpath-a-pattern gotcha (FALSIFICATION 3) ──


def test_a_dotdot_pattern_does_not_widen_the_write_scope():
    """PLATFORM-HARDENING-FLOORS §5, lifted verbatim: NEVER ``normpath`` a write-scope PATTERN.
    ``/a/**/../b`` collapses under normpath to ``/a/b``, silently widening an allow to a path
    the author never granted. FALSIFICATION 3: ``normpath`` the pattern in ``write_scope_allows``
    and the first assertion reds."""
    policy = consolidate(write_scope=WriteScope(allowed_paths=("/a/**/../b",)))
    # The widening a normpath-ing matcher would introduce:
    assert write_scope_allows(policy, "/a/b") is False
    # ...and it holds when the item itself carries a '..' that normalizes into /a/b:
    assert write_scope_allows(policy, "/a/x/../b") is False
    # Non-vacuous control: a clean pattern DOES admit a real path inside it (so the test above
    # is not passing merely because the matcher matches nothing).
    clean = consolidate(write_scope=WriteScope(allowed_paths=("/a/b/**",)))
    assert write_scope_allows(clean, "/a/b/c") is True
    assert write_scope_allows(clean, "/a/elsewhere") is False
    # An empty scope is unconfined (today's deny-only posture), not a brick.
    assert write_scope_allows(consolidate(), "/anywhere/at/all") is True


# ── 5. SafetyProfile is subsumed, not forked — its five live callers keep working ──


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.delenv(C.CEILING_PATH_ENV, raising=False)
    C.reset_ceiling()
    return tmp_path


def test_safetyprofile_is_subsumed_not_forked(home):
    """AG-5 landed ``SafetyProfile`` with live (non-test) readers. AG-13 must not fork it: the
    consolidated object holds the SAME type, so those readers keep working unchanged."""
    from personalclaw.guardrails.policy import (
        approval_policy_for_session,
        ceiling_permits_approval,
        profile_for_session,
        rung_ceiling_for_profile,
    )
    from personalclaw.llm_helpers import ToolApprovalPolicy

    # The consolidated policy carries the real SafetyProfile type (not a parallel copy).
    assert isinstance(SupervisorPolicy().autonomy, SafetyProfile)
    assert SupervisorPolicy().autonomy is HEADLESS

    # The live readers still resolve exactly as before (SafetyProfile itself is untouched).
    assert profile_for_session("cron:x").name == HEADLESS.name
    assert profile_for_session("chat-1").name == INTERACTIVE.name
    assert approval_policy_for_session("cron:x") is ToolApprovalPolicy.HOOK_BASED
    assert ceiling_permits_approval("auto") is True  # no operator ceiling → the grant stands
    assert rung_ceiling_for_profile(HEADLESS) == "auto_with_undo"
    assert rung_ceiling_for_profile(INTERACTIVE) == "autonomous"
