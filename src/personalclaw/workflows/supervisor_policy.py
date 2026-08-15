"""``SupervisorPolicy`` — the declaration of a loop's convergence policy (PP-14).

A loop is not a second engine. **A loop is a graph shape plus a supervisor policy.** The
shape already exists (`loop` node kind, `LoopMode`, `foreach` with `max_concurrency`); the
POLICY had no home, which is why it got implemented twice with each side missing what the
other had — `loop/` carries the marginal-value band and reproduce-before-ship, while
`workflows/` carries the pre-tier, the proof precondition, the actor matrix and the
five-rung escalation ladder. This module is the ONE declaration those two halves converge
on: it reuses the types that already exist (`RubricCriterion` and `clamp_marginal` from
`judge_contract`, `Rung`/`FailureClass` from `loop_middleware`, `StepConfig` from
`loop.tick`, `Attention` from `autonomy`, `ScopeMode` from `scope`) rather than minting a
parallel vocabulary.

DELIBERATELY INERT — this module has **zero production callers**.
================================================================
It parses and it validates; nothing in the engine reads a ``SupervisorPolicy`` yet. **PP-15
is the wiring owner** — it widens `loop/tick.evaluate` into the single convergence brain and
makes ``SupervisorPolicy`` the source of the thresholds `evaluate` reads. Until PP-15 lands,
no ``frontier()``, no controller and no loop kind may call this: a convergence decision wired
into the engine before its home is widened would mint the very second brain this program
exists to retire.

This is the honesty-marker convention this program established (`WF2LOO-12`): **a control
with no caller must SAY it has no caller.** The claim is railed in BOTH drift directions by
``tests/test_workflows_validator_supervisor.py``:

* if a production caller of ``SupervisorPolicy`` appears while this marker still claims zero,
  the rail goes RED (the marker would be lying); and
* if this marker is removed while callers are still zero, the rail goes RED (an inert control
  that has stopped declaring itself inert).

A "production caller" is code outside this module and outside ``tests/`` that CONSTRUCTS a
``SupervisorPolicy`` or invokes :func:`parse_supervisor_policy` — i.e. code that wires the
policy into runtime behaviour. The authoring-time validator in ``workflows/validator.py``
consults the closed field set below to emit ``WF_SUPERVISOR_*`` codes; consulting the
contract is not invoking the policy, so it is not a caller.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from personalclaw.loop.tick import StepConfig
from personalclaw.workflows.autonomy import Attention
from personalclaw.workflows.judge_contract import (
    MARGINAL_MIN,
    SCORE_MAX,
    RubricCriterion,
    clamp_marginal,
)
from personalclaw.workflows.loop_middleware import (
    DEFAULT_LADDER,
    FailureClass,
    Rung,
    _resolve_ladder,
)
from personalclaw.workflows.scope import ScopeMode

logger = logging.getLogger(__name__)

#: The honesty marker (`WF2LOO-12`). ``True`` while this module has zero production callers.
#: PP-15 flips it when it wires the policy in. The rail asserts this claim matches reality in
#: both directions, so the constant can never quietly disagree with the code.
HAS_ZERO_PRODUCTION_CALLERS = True

#: Named in the docstring and here so the wiring owner is discoverable from code, not only prose.
WIRING_OWNER = "PP-15"


# ── The closed field set — the contract ──
#
# Missing/blank fields parse to a sane default (tolerant reads); an UNKNOWN field is a typed
# ``WF_SUPERVISOR_UNKNOWN_FIELD`` at authoring time. The closed set IS the contract, so it is
# the single source of truth the validator imports rather than a second copy.
POLICY_FIELDS: frozenset[str] = frozenset(
    {
        "rubric",
        "escalation_ladder",
        "failure_mutations",
        "gates",
        "marginal_value_band",
        "judge_model_tier",
        "reproduce_before_ship",
        "write_scope",
        "budget",
        "hitl_posture",
    }
)

#: Judge model tiers — the SAME vocabulary the LLM-kind ``model_tier`` lint uses
#: (``WF_BAD_MODEL_TIER``). Reused, not re-minted.
SUPERVISOR_MODEL_TIERS: frozenset[str] = frozenset({"reasoning", "standard", "fast"})

#: Valid enum VALUES, derived from the reused enums so the validator never restates them.
LADDER_RUNG_VALUES: frozenset[str] = frozenset(r.value for r in Rung)
FAILURE_CLASS_VALUES: frozenset[str] = frozenset(c.value for c in FailureClass)
HITL_POSTURE_VALUES: frozenset[str] = frozenset(a.value for a in Attention)
SCOPE_MODE_VALUES: frozenset[str] = frozenset({ScopeMode.WARN, ScopeMode.REJECT})

#: The default marginal-value band, seeded from the ``balanced`` granularity preset's
#: ``marginal_threshold`` of 2.0. A band is (floor, target): a cycle below the floor is not
#: worth continuing; at/above the target it may stop.
DEFAULT_MARGINAL_FLOOR = MARGINAL_MIN
DEFAULT_MARGINAL_TARGET = 2.0

#: Default judge model tier — matches the workflow ``model_tier`` default.
DEFAULT_MODEL_TIER = "standard"


@dataclass(frozen=True)
class WriteScope:
    """A declared write scope: the allowed paths plus what to do on an escape.

    Composes ``scope.ScopeMode`` — the enforcement mechanism (`scope.allowed_write_paths`,
    `scope.diff`) already exists; this only DECLARES the intent a wired supervisor would feed it.
    """

    allowed_paths: tuple[str, ...] = ()
    mode: str = ScopeMode.WARN


@dataclass(frozen=True)
class SupervisorPolicy:
    """The full convergence policy a loop node declares — parsed, not yet wired.

    Every field reuses a type that already lives in the tree. The declaration's only new
    idea is putting all ten in ONE place, so PP-15 has a single object to read instead of the
    per-kind Python that supplies these thresholds twice today.
    """

    #: What "good" means — the machine-checkable rubric (``judge_contract.RubricCriterion``).
    rubric: tuple[RubricCriterion, ...] = ()
    #: The escalation ladder, in order (``loop_middleware.Rung``). Always SURFACE-terminal.
    escalation_ladder: tuple[Rung, ...] = DEFAULT_LADDER
    #: FailureClass value → the corrective instruction a ``classified_retry`` injects.
    failure_mutations: dict[str, str] = field(default_factory=dict)
    #: Dwell/metric convergence gates (``loop.tick.StepConfig``).
    gates: StepConfig = field(default_factory=StepConfig)
    #: The diminishing-returns band (floor, target) on the 0-5 ``marginal_value`` scale.
    marginal_value_band: tuple[float, float] = (DEFAULT_MARGINAL_FLOOR, DEFAULT_MARGINAL_TARGET)
    #: Which model tier judges this loop (reasoning|standard|fast).
    judge_model_tier: str = DEFAULT_MODEL_TIER
    #: Whether a completed cycle must be independently reproduced before it ships
    #: (``loop.instrument.reproduce_confirm``).
    reproduce_before_ship: bool = False
    #: The filesystem write scope this loop may touch.
    write_scope: WriteScope = field(default_factory=WriteScope)
    #: Hard cycle budget; ``0`` = uncapped (``loop.tick.TickConfig.max_cycles`` semantics).
    budget_max_cycles: int = 0
    #: Whether the loop needs a person present (``autonomy.Attention``).
    hitl_posture: Attention = Attention.AFK


def _parse_rubric(raw: Any) -> tuple[RubricCriterion, ...]:
    """Reuse ``RubricCriterion``; a malformed entry is dropped, never fatal."""
    if not isinstance(raw, list):
        return ()
    out: list[RubricCriterion] = []
    for item in raw:
        if isinstance(item, dict) and item.get("criterion"):
            try:
                out.append(
                    RubricCriterion(
                        criterion=str(item["criterion"]),
                        target_score=int(item.get("target_score", SCORE_MAX) or SCORE_MAX),
                        weight=float(item.get("weight", 1.0) or 1.0),
                    )
                )
            except (TypeError, ValueError):
                logger.debug("dropping malformed rubric criterion %r", item)
    return tuple(out)


def _parse_failure_mutations(raw: Any) -> dict[str, str]:
    """Keep only ``FailureClass → str`` entries; an unknown class is dropped (the validator
    flags it at authoring time)."""
    if not isinstance(raw, dict):
        return {}
    return {
        str(k): str(v)
        for k, v in raw.items()
        if str(k) in FAILURE_CLASS_VALUES and isinstance(v, str)
    }


def _parse_gates(raw: Any) -> StepConfig:
    """Reuse ``loop.tick.StepConfig``; each field defaults, none raises."""
    if not isinstance(raw, dict):
        return StepConfig()

    def _num(key: str, default: float | None) -> float | None:
        value = raw.get(key)
        return float(value) if isinstance(value, (int, float)) else default

    dwell = _num("min_dwell_secs", 0.0) or 0.0
    findings = raw.get("min_findings")
    return StepConfig(
        min_dwell_secs=dwell,
        min_findings=int(findings) if isinstance(findings, (int, float)) else 0,
        metric_pass=_num("metric_pass", None),
        metric_hold=_num("metric_hold", None),
    )


def _parse_band(raw: Any) -> tuple[float, float]:
    """A (floor, target) pair on the reused 0-5 ``clamp_marginal`` scale."""
    floor, target = DEFAULT_MARGINAL_FLOOR, DEFAULT_MARGINAL_TARGET
    if isinstance(raw, dict):
        if "floor" in raw:
            floor = clamp_marginal(raw.get("floor"))
        if "target" in raw:
            target = clamp_marginal(raw.get("target"))
    elif isinstance(raw, (list, tuple)) and len(raw) == 2:
        floor, target = clamp_marginal(raw[0]), clamp_marginal(raw[1])
    # A band whose floor exceeds its target is nonsense; clamp it back to a point rather than
    # crash — the validator is the place to complain, the parser only ever produces a usable band.
    return (min(floor, target), max(floor, target))


def _parse_write_scope(raw: Any) -> WriteScope:
    if not isinstance(raw, dict):
        return WriteScope()
    paths_raw = raw.get("paths")
    paths = tuple(str(p) for p in paths_raw if p) if isinstance(paths_raw, list) else ()
    mode = raw.get("mode")
    mode = str(mode) if str(mode) in SCOPE_MODE_VALUES else ScopeMode.WARN
    return WriteScope(allowed_paths=paths, mode=mode)


def _parse_budget(raw: Any) -> int:
    if isinstance(raw, dict):
        raw = raw.get("max_cycles")
    if isinstance(raw, (int, float)) and raw >= 0:
        return int(raw)
    return 0


def parse_supervisor_policy(raw: Any) -> SupervisorPolicy:
    """Parse a loop node's ``supervisor`` config into a :class:`SupervisorPolicy`.

    Lenient by design (`WF2-R12` / the ``hints_from_dict`` pattern): missing or blank fields
    become sane defaults and a malformed value NEVER raises — an author's typo should run with
    the strict defaults, not fail to start. UNKNOWN top-level fields are ignored here; the
    closed-set contract is enforced by the authoring-time validator, which can report every
    problem at once instead of one-error-per-turn.

    Deliberately inert: PP-15 is the only intended caller (see the module docstring).
    """
    if not isinstance(raw, dict):
        return SupervisorPolicy()

    tier = raw.get("judge_model_tier")
    tier = str(tier) if str(tier) in SUPERVISOR_MODEL_TIERS else DEFAULT_MODEL_TIER

    hitl_raw = raw.get("hitl_posture")
    try:
        hitl = Attention(str(hitl_raw)) if hitl_raw is not None else Attention.AFK
    except ValueError:
        hitl = Attention.AFK

    return SupervisorPolicy(
        rubric=_parse_rubric(raw.get("rubric")),
        escalation_ladder=_resolve_ladder({"ladder": raw.get("escalation_ladder")}),
        failure_mutations=_parse_failure_mutations(raw.get("failure_mutations")),
        gates=_parse_gates(raw.get("gates")),
        marginal_value_band=_parse_band(raw.get("marginal_value_band")),
        judge_model_tier=tier,
        reproduce_before_ship=bool(raw.get("reproduce_before_ship")),
        write_scope=_parse_write_scope(raw.get("write_scope")),
        budget_max_cycles=_parse_budget(raw.get("budget")),
        hitl_posture=hitl,
    )
