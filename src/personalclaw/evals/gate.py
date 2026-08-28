"""Loop-2 regression gating — the cheap gate subset + before/after proposal columns (ES-6).

The amendment names three loops. Loop 1 (studies, §2) says *"should be better"*; Loop 3
(field metrics, §9) says *"is"*. Loop 2 is the cheap tier between a change and shipping:
a curated subset of the Loop-1 scenario library, re-run twice — once over the home as it
is, once with the candidate artifact staged — so a proposal carries ``{before, after, pin}``
before the user decides.

## What this module is NOT

It is not a second runner. Every score here comes from :func:`personalclaw.evals.runner.run_matrix`
through :mod:`personalclaw.evals.child`, one matrix per (arm × scenario) at ``trial_count=1``,
exactly as :mod:`personalclaw.evals.skills_bench` and :mod:`personalclaw.evals.ablation` do.
What this module adds is the *selection*, the *arm staging*, and the *bound*.

## How the dozen is declared, and why not a subdirectory

The amendment sketched ``evals/scenarios/gate/``. The installed library is a FLAT directory
that three readers glob (``personalclaw eval``, the matrix runner's resolver,
:func:`personalclaw.evals.scenarios.install_library`), and none of them descends: a scenario
in a subdir would be invisible to ``resolve_scenario_path`` and absent from the library
manifest. So membership is declared IN the scenario, as ``"tiers": ["gate"]`` — the same
data-keyed shape ``version``, ``fixture_home`` and the ``harvest`` provenance block already
use, and the rule ``scenarios.origin_of`` states outright: derive it by inspecting the data,
never from a side list of names. One consequence is recorded rather than hidden: adding the
field changes each scenario's ``scenario_sha256``, so a shipped scenario that joins the tier
also bumps its ``version`` (else the idempotent backfill would never reinstall it) and its
prior ledger rows sit under the older hash. That is what the pin is FOR — "did anything
change" is a pin-diff query.

## Why "fast" and "judge-light" are structural, not promised

* **Fast**: a tagged scenario over :data:`MAX_GATE_TURNS` turns is EXCLUDED with a reason.
  A gate whose cost is a claim in a docstring is a gate users learn to skip.
* **Judge-light**: the child runs ``EvalRunner(judge_enabled=False)``, which *filters* judge
  assertions out of the scored set (``eval/runner.py``). A scenario whose only assertions are
  ``judge`` therefore scores ``total_assertions == 0`` and falls back to ``1.0`` — a fabricated
  perfect score. So a tagged scenario with no non-judge assertion is excluded too. "Judge-light"
  is not a style preference here; it is the difference between a score and a fiction.

## How the bound is ENFORCED

``SpendMeter`` is the chokepoint, not a second tally:

1. an unbudgeted gate does not run *unbounded* — it does not run at all, and the proposal
   reads ``ungated``. ``0`` means UNLIMITED to :class:`~personalclaw.guardrails.budgets.Budget`,
   which is the one thing a pre-ship gate must never be;
2. before every cell, :meth:`SpendMeter.check_run` is consulted and an ``EXCEEDED`` verdict
   STOPS the sweep — the scenarios that did not run are named in the report rather than
   silently omitted;
3. after every cell, the child's OWN reported spend (``child.spend_from_home``, read inside
   the throwaway home before the parent deletes it) is charged to the meter under the gate's
   run key. Charge → check → stop, all through the meter, so the ceiling that bites is the
   real one.

The matrix spec's own ``budget_usd`` is deliberately left at 0: its preflight is DAY-scoped
(``_budget_blocks_cell`` → ``check_day``), and comparing a per-gate ceiling against a whole
day's spend would refuse the gate for spend that had nothing to do with it. One bound, one rule.

## Why "ungated" never blocks

A gate that failed closed on its own absence would stop a user shipping a proposal because
the *gate* broke — evals off, no model bound, no budget, an empty subset. Every one of those
is a legible ``ungated`` state with a reason a user can act on, and none of them touches
:func:`personalclaw.learning.proposals.accept`. The word is the atom's own; the *score cells*
render with the panels' house string ("not measured") rather than a ``0.0``.

And the pin is never invented. :func:`~personalclaw.evals.pinning.RunPin.is_complete` is a
precondition: a home with no bound model has no honest ``model_fingerprint``, and minting one
would poison every per-fingerprint baseline reading the same ``results.tsv``. An unavailable
pin is the ungated case, not a fabricated one.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from personalclaw.evals import pinning
from personalclaw.evals import scenarios as scenario_lib
from personalclaw.evals import store
from personalclaw.evals.overlay import OverlayRefusedError, throwaway_home

logger = logging.getLogger(__name__)

#: The ``kind`` column a gate run writes to ``results.tsv``, and the subject id its pin
#: carries. One value, so a later reader cannot half-match the history.
GATE_KIND = "gate"

# ── the subset declaration ───────────────────────────────────────────────────

#: The scenario field that lists which tiers a scenario belongs to.
TIERS_FIELD = "tiers"
#: The tier value that opts a scenario into the Loop-2 gate subset.
GATE_TIER = "gate"

#: Cost ceiling per gate scenario, in TURNS — one model call per turn, so this is the honest
#: unit of "fast". A tagged scenario above it is excluded with a reason rather than quietly
#: making the gate expensive.
MAX_GATE_TURNS = 2

#: The "curated dozen". A cap rather than a target: the subset is whatever qualifies, cheapest
#: first, up to this many. An unbounded gate subset stops being the cheap tier.
GATE_SUBSET_MAX = 12

# ── the two states a proposal's gate column can be in ────────────────────────

#: A gate run happened; ``before``/``after`` carry its measurement.
GATE_GATED = "gated"
#: No gate run stands behind this proposal. NEVER blocks acceptance — see the module docstring.
GATE_UNGATED = "ungated"

# ── why a proposal is ungated, in sentences a user can act on ────────────────
# These are product copy, not debug strings: each says what the absence means for the
# decision the user is about to make.

UNGATED_NOT_RUN = "no gate run yet — accept on the evidence above, or run the gate first"
UNGATED_EVALS_OFF = (
    "the eval substrate is off, so nothing re-ran — this is a judgement call on the evidence "
    "above, not on a score"
)
UNGATED_NO_BUDGET = (
    "no eval budget is set, so a gate run would have had no ceiling at all — set "
    "evals.default_budget_usd to get before/after scores"
)
UNGATED_EMPTY_SUBSET = (
    "no installed scenario is tagged for the gate tier, so there was nothing cheap to re-run"
)
UNGATED_NO_PIN = (
    "a gate run could not be pinned to a model, and an unpinned score is not evidence "
    "(missing: {missing})"
)
UNGATED_KIND = (
    "a {kind} proposal does not yet declare a candidate artifact the gate can stage, so there "
    "was nothing to score before against after"
)

# ── the arm staging seam ─────────────────────────────────────────────────────

#: Env var carrying the arm's staged files from parent to child. Read once, in the child,
#: like :data:`personalclaw.evals.overlay.OVERLAY_ENV`.
ARM_ENV = "PERSONALCLAW_GATE_ARM"

#: The home as it is — the baseline arm. Stages nothing when the candidate's paths are absent.
ARM_BEFORE = "before"
#: The home with the candidate artifact staged.
ARM_AFTER = "after"
ARM_LABELS: tuple[str, ...] = (ARM_BEFORE, ARM_AFTER)


class ArmRefusedError(ValueError):
    """An arm named a path that could escape the child's throwaway home.

    Deliberately fatal in the child (the cell becomes ``VERIFIER_ABSENT``): a staged file
    landing outside the cell is the same class of failure ``overlay.OverlayRefusedError``
    exists to prevent, and papering over it would mean measuring against live state.
    """


@dataclass(frozen=True)
class ArtifactArm:
    """One arm of the before/after comparison: home-relative paths → their content.

    Both arms carry the SAME paths, so the two runs differ only in the bytes at those paths.
    An arm whose file map is empty stages nothing — which is the honest ``before`` for a
    proposal that would create an artifact that does not exist yet.
    """

    label: str
    files: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.label not in ARM_LABELS:
            raise ValueError(f"unknown arm label {self.label!r} — expected one of {ARM_LABELS}")

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "files": dict(self.files)}

    @classmethod
    def from_dict(cls, data: dict) -> ArtifactArm:
        raw = data.get("files") or {}
        return cls(
            label=str(data.get("label", "")),
            files={str(k): str(v) for k, v in dict(raw).items()},
        )


def encode(arm: ArtifactArm) -> str:
    """Render an arm for :data:`ARM_ENV` (compact, sorted, stable)."""
    return json.dumps(arm.to_dict(), separators=(",", ":"), sort_keys=True)


def decode(text: str) -> ArtifactArm | None:
    """Parse :data:`ARM_ENV`. Absent/garbage ⇒ ``None`` (a plain cell, nothing staged)."""
    if not text:
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        logger.warning("unparseable gate arm in env; running cell unmodified")
        return None
    if not isinstance(data, dict):
        return None
    try:
        return ArtifactArm.from_dict(data)
    except ValueError:
        logger.warning("invalid gate arm in env; running cell unmodified")
        return None


def from_env(env: dict | None = None) -> ArtifactArm | None:
    """The arm this process was spawned with, if any."""
    source = os.environ if env is None else env
    return decode(str(source.get(ARM_ENV) or ""))


def spawn_env_for(base_env: dict, arm: ArtifactArm | None) -> dict:
    """``base_env`` PLUS the arm — on the COPY the caller already made.

    Returns a new dict; ``base_env`` is not mutated, and neither is ``os.environ``. Same
    contract as :func:`personalclaw.evals.overlay.spawn_env_for`, for the same reason.
    """
    env = dict(base_env)
    if arm is not None:
        env[ARM_ENV] = encode(arm)
    return env


def resolve_in_home(home: Path, relpath: str) -> Path:
    """``home / relpath``, or refuse if it could land outside ``home``.

    Absolute paths, ``..`` components and symlink escapes are all refused. The check is on
    the RESOLVED path because ``home/../x`` normalizes away and a parent-relative arm would
    otherwise write wherever it liked.
    """
    text = str(relpath or "").strip()
    if not text:
        raise ArmRefusedError("a gate arm names an empty path")
    candidate = Path(text)
    if candidate.is_absolute():
        raise ArmRefusedError(f"a gate arm names an absolute path {text!r}")
    target = (home / candidate).resolve()
    root = home.resolve()
    if target != root and not target.is_relative_to(root):
        raise ArmRefusedError(f"a gate arm path {text!r} resolves outside the cell home {root}")
    return target


def apply_in_child(arm: ArtifactArm | None) -> list[str]:
    """Stage ``arm``'s files into THIS process's throwaway home. Returns what was written.

    Refuses via :class:`~personalclaw.evals.overlay.OverlayRefusedError` when the process is
    not pointed at a throwaway home — the same negative rail the ablation overlay uses, and
    for the same reason: a misconfigured spawn is exactly how a "temporary" edit becomes a
    permanent one.

    An arm with no files returns ``[]``, which is also the honest answer to "what did the
    baseline write".
    """
    if arm is None or not arm.files:
        return []
    home = throwaway_home()
    written: list[str] = []
    for relpath, text in sorted(arm.files.items()):
        target = resolve_in_home(home, relpath)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written.append(relpath)
    return written


# ── the subset ───────────────────────────────────────────────────────────────


#: The tier reader is :func:`personalclaw.evals.scenarios.tiers_of` — the library module owns
#: every "what does this scenario declare" question and writes the manifest from it. A second
#: parser here would be a second answer to the same question, and the one that drifts is always
#: the copy furthest from the manifest.
tiers_of = scenario_lib.tiers_of


def turn_count(data: dict) -> int:
    """Total turns across every session — one model call each, so this is the cost unit."""
    return sum(len(s.get("turns") or []) for s in (data.get("sessions") or []))


def hard_assertion_count(data: dict) -> int:
    """Assertions the gate can actually score: everything except ``judge``.

    A ``judge`` assertion is FILTERED OUT by ``EvalRunner(judge_enabled=False)``, which is what
    the matrix child runs. Counting them would let a judge-only scenario claim assertions it
    never scores, and its ``total_assertions == 0`` fallback publishes ``1.0``.
    """
    total = 0
    for session in data.get("sessions") or []:
        for turn in session.get("turns") or []:
            for assertion in turn.get("assertions") or []:
                if str((assertion or {}).get("type") or "") != "judge":
                    total += 1
    return total


@dataclass(frozen=True)
class SubsetMember:
    """One gate scenario, with the two numbers that justify its membership."""

    name: str
    sha256: str
    turns: int
    hard_assertions: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "sha256": self.sha256,
            "turns": self.turns,
            "hard_assertions": self.hard_assertions,
        }


@dataclass(frozen=True)
class GateSubset:
    """The selected subset, plus every tagged scenario that did NOT make it and why.

    The exclusions ride along because a subset that silently drops a scenario the user tagged
    is a subset whose cost and coverage nobody can explain.
    """

    members: tuple[SubsetMember, ...] = ()
    excluded: tuple[tuple[str, str], ...] = ()

    @property
    def names(self) -> list[str]:
        return [m.name for m in self.members]

    @property
    def turns(self) -> int:
        """The whole subset's turn count — the gate's cost, per arm."""
        return sum(m.turns for m in self.members)

    def sha256(self) -> str:
        """The SUBSET's identity: a canonical hash over ``{name: scenario_sha256}``.

        A set-level hash rather than a per-scenario one because the gate's subject IS the set:
        add, remove or edit a member and the subject changed, which is exactly what the pin has
        to record. Same move ES-4 makes for a judge fixture SET
        (:func:`~personalclaw.evals.pinning.compute_pin_for_subject`).
        """
        import hashlib

        payload = scenario_lib.canonical_json({m.name: m.sha256 for m in self.members})
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "members": [m.to_dict() for m in self.members],
            "excluded": {name: reason for name, reason in self.excluded},
            "sha256": self.sha256(),
            "turns": self.turns,
        }


def gate_subset(*, max_scenarios: int = GATE_SUBSET_MAX) -> GateSubset:
    """Select the gate subset out of the INSTALLED scenario library.

    Membership is opt-in (``tiers: ["gate"]``) and then structurally filtered: over
    :data:`MAX_GATE_TURNS` turns, or no non-judge assertion, and the scenario is excluded with
    a reason. Ordering is cheapest-first then by name, so the cap takes the cheap ones and the
    selection is deterministic across runs — a subset that reshuffles is a before/after
    comparison over two different populations.
    """
    try:
        installed = scenario_lib.installed_dir()
        if not scenario_lib.manifest_path().exists():
            scenario_lib.install_library()
    except OSError:
        logger.warning("gate: the installed scenario library is unreadable", exc_info=True)
        return GateSubset()

    candidates: list[SubsetMember] = []
    excluded: list[tuple[str, str]] = []
    for path in sorted(installed.iterdir()):
        if path.suffix not in scenario_lib.SCENARIO_SUFFIXES:
            continue
        try:
            data = scenario_lib._read_scenario_data(path)  # noqa: SLF001 - same-package read
        except (OSError, ValueError, scenario_lib.ScenarioLibraryError):
            logger.warning("gate: skipping unparseable scenario %s", path, exc_info=True)
            continue
        if GATE_TIER not in tiers_of(data):
            continue
        turns = turn_count(data)
        hard = hard_assertion_count(data)
        if turns > MAX_GATE_TURNS:
            excluded.append(
                (
                    path.stem,
                    f"{turns} turns is over the gate's {MAX_GATE_TURNS}-turn ceiling",
                )
            )
            continue
        if hard < 1:
            excluded.append(
                (
                    path.stem,
                    "no non-judge assertion — the gate runs with the judge off, so this would "
                    "score a fabricated 1.0",
                )
            )
            continue
        candidates.append(
            SubsetMember(
                name=path.stem,
                sha256=scenario_lib.sha256_of_scenario_data(data),
                turns=turns,
                hard_assertions=hard,
            )
        )

    candidates.sort(key=lambda m: (m.turns, m.name))
    cap = max(0, int(max_scenarios))
    for extra in candidates[cap:]:
        excluded.append((extra.name, f"over the {cap}-scenario gate cap"))
    return GateSubset(members=tuple(candidates[:cap]), excluded=tuple(sorted(excluded)))


# ── the report shape the proposal card reads ─────────────────────────────────


def _arm_scores(label: str) -> dict[str, Any]:
    """An empty per-arm block: measured nothing, and says so with ``mean_score: None``."""
    return {"label": label, "mean_score": None, "scored": 0, "absent": 0, "scenarios": {}}


@dataclass
class GateReport:
    """``{before, after, pin}`` plus the honest bookkeeping around it.

    ``state`` is the load-bearing field. ``ungated`` with a ``reason`` is a first-class
    outcome, not an error: see the module docstring on why a gate must not fail closed on its
    own absence.
    """

    state: str = GATE_UNGATED
    reason: str = UNGATED_NOT_RUN
    run_id: str = ""
    ran_at: str = ""
    subset: dict[str, Any] = field(default_factory=dict)
    before: dict[str, Any] = field(default_factory=lambda: _arm_scores(ARM_BEFORE))
    after: dict[str, Any] = field(default_factory=lambda: _arm_scores(ARM_AFTER))
    pin: dict[str, Any] = field(default_factory=dict)
    spend: dict[str, Any] = field(default_factory=dict)
    bound: dict[str, Any] = field(default_factory=dict)

    @property
    def delta(self) -> float | None:
        """``after - before``, or ``None`` when either arm measured nothing.

        ``None`` and not ``0.0``: "the two arms tied" and "one arm never scored" are the same
        number and completely different facts.
        """
        b = self.before.get("mean_score")
        a = self.after.get("mean_score")
        if b is None or a is None:
            return None
        return round(float(a) - float(b), 6)

    @property
    def regressed(self) -> bool:
        """A measured DROP. Strict: a tie is not a regression, and an unmeasured pair is not one
        either — flagging an unmeasured proposal as regressed would be the reverse of the same
        dishonesty the ``ungated`` state exists to avoid."""
        delta = self.delta
        return delta is not None and delta < 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            "run_id": self.run_id,
            "ran_at": self.ran_at,
            "subset": dict(self.subset),
            "before": dict(self.before),
            "after": dict(self.after),
            "pin": dict(self.pin),
            "spend": dict(self.spend),
            "bound": dict(self.bound),
            "delta": self.delta,
            "regressed": self.regressed,
        }


def summary(report: dict | None) -> dict[str, Any]:
    """The compact ``{before, after, pin}`` the inbox ROW carries.

    An ABSENT report (``None`` or ``{}``) projects to ``ungated`` with
    :data:`UNGATED_NOT_RUN` — never to a blank cell and never to ``0.0``. That is the whole of
    clause 4: the row is the surface a user decides from, so the absence has to be legible
    THERE, not only in the detail record.

    ``pin`` is trimmed to the two parts that identify the run for a reader (the model
    fingerprint digest and the subset hash); the full pin stays on the stored proposal. It is
    trimmed, never SYNTHESIZED: an ungated report carries ``{}`` here rather than a plausible
    fingerprint, because an invented pin poisons every per-fingerprint baseline that reads the
    same ledger.
    """
    data = report or {}
    state = str(data.get("state") or GATE_UNGATED)
    before = (data.get("before") or {}).get("mean_score")
    after = (data.get("after") or {}).get("mean_score")
    delta = data.get("delta")
    pin = data.get("pin") or {}
    subset = data.get("subset") or {}
    return {
        "state": state if state in (GATE_GATED, GATE_UNGATED) else GATE_UNGATED,
        "reason": str(data.get("reason") or (UNGATED_NOT_RUN if state != GATE_GATED else "")),
        "before": None if before is None else float(before),
        "after": None if after is None else float(after),
        "delta": None if delta is None else float(delta),
        "regressed": bool(data.get("regressed")),
        "scenarios": len(subset.get("members") or []),
        "halted": bool((data.get("bound") or {}).get("halted")),
        "dollars_est": float((data.get("spend") or {}).get("dollars_est") or 0.0),
        "spend_observed": bool((data.get("spend") or {}).get("observed")),
        "pin": (
            {
                "model_fp": str(pin.get("model_fp") or ""),
                "scenario_sha256": str(pin.get("scenario_sha256") or ""),
            }
            if pin
            else {}
        ),
        "ran_at": str(data.get("ran_at") or ""),
    }


def ungated(reason: str, **extra: Any) -> GateReport:
    """An ``ungated`` report carrying WHY. The only constructor for the absence."""
    report = GateReport(state=GATE_UNGATED, reason=reason)
    for key, value in extra.items():
        setattr(report, key, value)
    return report


# ── the spend the child reported, read back from its retained artifacts ──────


def cell_spend(matrix_id: str) -> dict[str, Any]:
    """Sum the spend the CHILDREN of one matrix reported, from their retained artifacts.

    ``child.spend_from_home`` reads ``model_calls.jsonl`` inside the throwaway home before the
    parent deletes it, and ``runner._write_cell_artifact`` persists it verbatim. This reads that
    back so the PARENT can charge the meter — it is not a second counter, it is the meter's
    input.

    ``observed`` is carried through and never inferred: ``False`` means no cell reported spend,
    which is NOT the same fact as zero spend, and a gate that reported "$0.00" for a
    measurement it never saw would be claiming the run was free.
    """
    total = {"observed": False, "attempts": 0, "tokens": 0, "dollars_est": 0.0, "estimated": False}
    try:
        root = store.matrix_dir(matrix_id)
    except OSError:
        return total
    for result_path in sorted(root.glob("cell-*/result.json")):
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        spend = ((payload or {}).get("parsed") or {}).get("spend")
        if not isinstance(spend, dict) or not spend.get("observed"):
            continue
        total["observed"] = True
        total["attempts"] += int(spend.get("attempts") or 0)
        total["tokens"] += int(spend.get("tokens") or 0)
        total["dollars_est"] = round(
            float(total["dollars_est"]) + float(spend.get("dollars_est") or 0.0), 6
        )
        total["estimated"] = bool(total["estimated"]) or bool(spend.get("estimated"))
    return total


def _accumulate(into: dict[str, Any], one: dict[str, Any]) -> None:
    into["observed"] = bool(into.get("observed")) or bool(one.get("observed"))
    into["attempts"] = int(into.get("attempts") or 0) + int(one.get("attempts") or 0)
    into["tokens"] = int(into.get("tokens") or 0) + int(one.get("tokens") or 0)
    into["dollars_est"] = round(
        float(into.get("dollars_est") or 0.0) + float(one.get("dollars_est") or 0.0), 6
    )
    into["estimated"] = bool(into.get("estimated")) or bool(one.get("estimated"))


# ── the run ──────────────────────────────────────────────────────────────────


def _default_budget_usd() -> float:
    """The gate's ceiling, from ``evals.default_budget_usd``.

    No new config field: the existing knob is documented as "the default hard spend cap a
    matrix/study run refuses to exceed", and a gate run is a matrix run. Fail-CLOSED on an
    unreadable config — 0 here means "no gate", not "no ceiling".
    """
    try:
        from personalclaw.config.loader import AppConfig

        return float(getattr(AppConfig.load().evals, "default_budget_usd", 0.0) or 0.0)
    except Exception:
        logger.debug("gate: could not read evals.default_budget_usd", exc_info=True)
        return 0.0


def _evals_enabled() -> bool:
    try:
        from personalclaw.config.loader import AppConfig

        return bool(getattr(AppConfig.load().evals, "enabled", False))
    except Exception:
        logger.debug("gate: could not read evals.enabled", exc_info=True)
        return False


def _sel_log(run_id: str, *, outcome: str, resources: str = "") -> None:
    """SEL-log a gate-run lifecycle event. Best-effort — never breaks a run.

    A gate run spends the operator's money on the strength of an autonomously authored
    proposal, which is exactly the class of act the security event log exists to make
    reviewable after the fact.
    """
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller=f"gate:{run_id}",
            operation="evals_gate",
            outcome=outcome,
            source="evals",
            resources=resources,
        )
    except Exception:
        logger.debug("SEL gate log failed", exc_info=True)


def run_gate(
    *,
    run_id: str,
    arms: tuple[ArtifactArm, ArtifactArm],
    subset: GateSubset | None = None,
    budget_usd: float | None = None,
    meter=None,
    run_matrix=None,
    trials: int = 1,
    now: datetime | None = None,
) -> GateReport:
    """Re-run the gate subset over both arms and return ``{before, after, pin}``.

    Never raises for a condition the user could hit: evals off, no budget, an empty subset, no
    bound model and an unresolvable scenario all return an ``ungated`` report with a reason.
    ``run_matrix`` is injectable for the same reason ``skills_bench.bench_skill`` makes it
    injectable — the matrix boundary is where a test stops before spawning a process.
    """
    moment = now or datetime.now(tz=timezone.utc)
    if not _evals_enabled():
        return ungated(UNGATED_EVALS_OFF, run_id=run_id)

    budget = _default_budget_usd() if budget_usd is None else float(budget_usd)
    if budget <= 0.0:
        return ungated(UNGATED_NO_BUDGET, run_id=run_id)

    chosen = gate_subset() if subset is None else subset
    if not chosen.members:
        return ungated(UNGATED_EMPTY_SUBSET, run_id=run_id, subset=chosen.to_dict())

    pin = pinning.compute_pin_for_subject(GATE_KIND, chosen.sha256())
    if not pin.is_complete():
        return ungated(
            UNGATED_NO_PIN.format(missing=", ".join(pin.missing_parts())),
            run_id=run_id,
            subset=chosen.to_dict(),
        )

    if meter is None:
        from personalclaw.guardrails.budgets import get_meter

        meter = get_meter()
    if run_matrix is None:  # pragma: no cover - the default wiring
        from personalclaw.evals.runner import run_matrix as _default

        run_matrix = _default

    from personalclaw.evals.matrix import MatrixSpec
    from personalclaw.guardrails.budgets import Budget, BudgetVerdict

    ceiling = Budget(max_dollars=budget)
    run_key = f"{GATE_KIND}:{run_id}"
    report = GateReport(
        state=GATE_GATED,
        reason="",
        run_id=run_id,
        ran_at=moment.isoformat(),
        subset=chosen.to_dict(),
        pin=pin.to_dict(),
    )
    spend: dict[str, Any] = {
        "observed": False,
        "attempts": 0,
        "tokens": 0,
        "dollars_est": 0.0,
        "estimated": False,
    }
    halted = ""
    not_run: list[str] = []
    _sel_log(run_id, outcome="started", resources=f"subset={','.join(chosen.names)}")

    for arm in arms:
        block = _arm_scores(arm.label)
        for member in chosen.members:
            if halted:
                not_run.append(f"{arm.label}:{member.name}")
                continue
            verdict, why = meter.check_run(run_key, ceiling)
            if verdict is BudgetVerdict.EXCEEDED:
                halted = why or "the gate budget was exhausted"
                not_run.append(f"{arm.label}:{member.name}")
                continue
            matrix_id = f"{GATE_KIND}-{run_id}-{arm.label}-{member.name}"
            spec = MatrixSpec(
                subject=member.name,
                trial_count=max(1, int(trials)),
                scorer="assertion",
            )
            try:
                result = run_matrix(spec, matrix_id=matrix_id, artifact_arm=arm)
            except Exception:
                # One unrunnable scenario is one unmeasured cell, not a failed gate: the
                # remaining scenarios still carry signal, and a gate that aborts on the first
                # infra fault reports "ungated" for a subset that mostly ran.
                logger.warning("gate: %s scenario %s failed", arm.label, member.name, exc_info=True)
                block["scenarios"][member.name] = None
                block["absent"] += 1
                continue
            score = (result.aggregates or {}).get("mean_score")
            block["scenarios"][member.name] = None if score is None else float(score)
            if score is None:
                block["absent"] += 1
            else:
                block["scored"] += 1
            one = cell_spend(matrix_id)
            _accumulate(spend, one)
            # Charge the meter with what the child actually reported. This is the step that
            # makes the check above bind: without it `check_run` reads a total nothing wrote.
            meter.charge(
                int(one.get("tokens") or 0),
                float(one.get("dollars_est") or 0.0),
                run_key=run_key,
            )
        scored = [v for v in block["scenarios"].values() if v is not None]
        block["mean_score"] = (sum(scored) / len(scored)) if scored else None
        setattr(report, arm.label, block)

    report.spend = spend
    report.bound = {
        "budget_usd": budget,
        "run_key": run_key,
        "halted": bool(halted),
        "halt_reason": halted,
        "not_run": not_run,
    }
    _record_ledger_row(report, pin=pin, k=len(chosen.members))
    _sel_log(
        run_id,
        outcome="halted_on_budget" if halted else "completed",
        resources=f"delta={report.delta} regressed={report.regressed}",
    )
    return report


def verdict_of(report: GateReport) -> str:
    """The one-word ledger verdict: ``regression``, ``pass``, or ``verifier_absent``.

    ``verifier_absent`` when the delta is unmeasurable, matching
    :func:`personalclaw.evals.runner._matrix_verdict`'s vocabulary rather than minting a
    second word for "no measurement".
    """
    from personalclaw.evals.matrix import VERIFIER_ABSENT

    if report.delta is None:
        return VERIFIER_ABSENT
    return "regression" if report.regressed else "pass"


def _record_ledger_row(report: GateReport, *, pin: pinning.RunPin, k: int) -> None:
    """Append the gate's ``results.tsv`` row — before in ``score_old``, after in ``score_new``.

    Those two columns have existed since ES-1 for exactly this shape. Best-effort: a ledger
    write that fails must not lose a measurement the proposal card can still render.
    """
    try:
        store.append_result(
            {
                "study_id": report.run_id,
                "kind": GATE_KIND,
                "verdict": verdict_of(report),
                "score_old": report.before.get("mean_score"),
                "score_new": report.after.get("mean_score"),
                "k": k,
                "ts": report.ran_at,
            },
            pin=pin,
        )
    except Exception:
        logger.warning("gate: results.tsv row not written for %s", report.run_id, exc_info=True)


# ── proposals: deriving the two arms, and persisting the report ──────────────

#: Which proposal kinds declare a candidate artifact the gate can stage, and who renders it.
#: A dispatch table rather than an ``if`` chain so an unlisted kind is an honest ``ungated``
#: with the kind NAMED, not a silent skip.
_CANDIDATE_RENDERERS: dict[str, str] = {
    "skill": "personalclaw.learning.skill_promotion:candidate_files",
}


def _render_candidate(prop_dict: dict) -> dict[str, str] | None:
    """The files an accept of this proposal would write, or ``None`` for an ungateable kind."""
    kind = str(prop_dict.get("kind") or "")
    ref = _CANDIDATE_RENDERERS.get(kind)
    if not ref:
        return None
    module_name, _, attr = ref.partition(":")
    try:
        import importlib

        renderer = getattr(importlib.import_module(module_name), attr)
        files = renderer(prop_dict)
    except Exception:
        logger.warning("gate: candidate render failed for %s", kind, exc_info=True)
        return None
    if not isinstance(files, dict) or not files:
        return None
    return {str(k): str(v) for k, v in files.items()}


def arms_for_proposal(prop_dict: dict) -> tuple[ArtifactArm, ArtifactArm] | None:
    """Build the ``before``/``after`` arms for one proposal, or ``None`` if it is ungateable.

    ``after`` is what an accept would write; ``before`` is what is at those same paths in the
    LIVE home right now — read-only, in the parent, and empty for a path that does not exist
    yet. Both arms name the SAME paths, so the two runs differ only in the bytes at them.
    """
    after_files = _render_candidate(prop_dict)
    if after_files is None:
        return None
    from personalclaw.config.loader import config_dir

    home = Path(config_dir())
    before_files: dict[str, str] = {}
    for relpath in after_files:
        try:
            current = resolve_in_home(home, relpath)
        except ArmRefusedError:
            logger.warning("gate: candidate names an unstageable path %r", relpath)
            return None
        if current.is_file():
            try:
                before_files[relpath] = current.read_text(encoding="utf-8")
            except OSError:
                logger.debug("gate: could not read live %s", relpath, exc_info=True)
    return (
        ArtifactArm(label=ARM_BEFORE, files=before_files),
        ArtifactArm(label=ARM_AFTER, files=after_files),
    )


def gate_proposal(pid: str, **kwargs: Any) -> GateReport | None:
    """Run the gate for one proposal and PERSIST the report on it. ``None`` if it is gone.

    Persisting is what makes the card readable without re-running: the proposal file is the
    read surface, and :func:`personalclaw.learning.proposals.accept` never consults the field —
    an ungated proposal stays acceptable.
    """
    from personalclaw.learning import proposals as queue

    prop = queue.get(pid)
    if prop is None:
        return None
    prop_dict = prop.to_dict()
    arms = arms_for_proposal(prop_dict)
    if arms is None:
        report = ungated(UNGATED_KIND.format(kind=prop.kind or "?"), run_id=pid)
    else:
        report = run_gate(run_id=pid, arms=arms, **kwargs)
    queue.attach_gate(pid, report.to_dict())
    return report


__all__ = [
    "ARM_AFTER",
    "ARM_BEFORE",
    "ARM_ENV",
    "ARM_LABELS",
    "ArmRefusedError",
    "ArtifactArm",
    "GATE_GATED",
    "GATE_KIND",
    "GATE_SUBSET_MAX",
    "GATE_TIER",
    "GATE_UNGATED",
    "GateReport",
    "GateSubset",
    "MAX_GATE_TURNS",
    "OverlayRefusedError",
    "SubsetMember",
    "TIERS_FIELD",
    "UNGATED_EMPTY_SUBSET",
    "UNGATED_EVALS_OFF",
    "UNGATED_KIND",
    "UNGATED_NOT_RUN",
    "UNGATED_NO_BUDGET",
    "UNGATED_NO_PIN",
    "apply_in_child",
    "arms_for_proposal",
    "cell_spend",
    "decode",
    "encode",
    "from_env",
    "gate_proposal",
    "gate_subset",
    "hard_assertion_count",
    "resolve_in_home",
    "run_gate",
    "spawn_env_for",
    "summary",
    "tiers_of",
    "turn_count",
    "ungated",
    "verdict_of",
]
