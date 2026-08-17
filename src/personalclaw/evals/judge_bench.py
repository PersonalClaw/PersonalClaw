"""The judge benchmark harness → tier-recommendation table (EVALUATION-SUBSTRATE §6, ES-4).

Every gate in the flywheel and the engine rests on a judge verdict, and until now the
only calibration instrument was `loop/instrument.probe_judge`: one strong/null pair, on
whatever model happened to be bound, answering one bit ("blind or not"). This module
generalizes exactly that shape across the axes that actually cost money — **fixtures ×
judge tier × `judge_samples`** — and publishes the table that says which tier each rubric
class really needs.

── What it reuses rather than re-mints ──

The judge vocabulary is not re-derived here. `workflows.judge_contract` renders the
prompt (`judge_instruction`), parses the answer (`parse_judge_json`), validates it
(`validate_verdict`) and combines N samples (`aggregate_samples`) — the same four calls a
live judge gate makes, so a tier that passes this benchmark passes on the object the
engine will actually hand it. The separation floor is
`judge_calibration.CANARY_MIN_SEPARATION`, imported rather than restated, because a
second threshold would make one judge trustworthy to the benchmark and blind to the
canary. Agreement-with-known-verdict is `judge_calibration.DivergenceRecord.direction`,
which already names the three outcomes (`agreement` / `false_pass` / `false_reject`) —
a fixture's known verdict IS a human label, so a judge disagreeing with it IS a
divergence record, and reusing that function keeps the benchmark's agreement metric and
the product's live one the same arithmetic.

── The axes are CONSUMED, and that is the load-bearing property ──

A matrix axis nothing reads produces N identical runs wearing different labels — a
fabricated comparison that looks real in every artifact. So both axes are consumed at a
named seam and tested for it:

* `judge_samples` decides how many times :func:`observe_cell` calls the judge. The
  observation records `calls`, so "the axis moved" is a recorded number rather than a
  claim, and `test_judge_samples_axis_is_consumed` fails if the loop stops honouring it.
* `tier` decides the `use_case` (:func:`use_case_for_tier`, the engine's own
  `DEFAULT_MODEL_TIERS`) that resolves the model, and the observation records the model
  the call ran on.

── Why there is no child process here ──

`run_matrix`'s subprocess spawn exists to contain ONE hazard: `EvalRunner.run_scenario`
mutates `PERSONALCLAW_WORKSPACE` in the calling process (§1.3). A judge fixture is a
fixed block of text plus a rubric — nothing is executed, no workspace is written, and
`EvalRunner` is never constructed. So this consumer runs in-process, reusing the shared
`MatrixSpec`/:func:`~personalclaw.evals.matrix.expand_cells`/
:func:`~personalclaw.evals.matrix.aggregate` and the same `matrices/<id>/` artifact sinks.
Skipping the spawn is not skipping the rail; the rail guards a seam this path does not
touch.

── Honesty rules, mechanized rather than left to the reader ──

An unmeasured property is never adequate. A rubric class with no strong/null pair reports
`separation: None` and is INADEQUATE; a class never position-swapped reports
`flip_rate: None` and is INADEQUATE. Absent is not the same as satisfied, and a rail that
matched nothing must not read clean. A cell whose judge produced no parseable verdict is
`VERIFIER_ABSENT` with its protocol errors counted — never averaged in as a wrong answer,
because "the judge could not answer" and "the judge answered wrongly" send a reader to two
different places. Cost is `None` when nothing priced the call, never `0.0`, since a free-
looking unpriced model would win "cheapest adequate tier" outright.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from personalclaw.atomic_write import atomic_write
from personalclaw.evals import pinning, store
from personalclaw.evals.matrix import (
    FAILED,
    PASSED,
    TRIAL_KEY,
    VERIFIER_ABSENT,
    CellResult,
    MatrixSpec,
    aggregate,
    expand_cells,
)
from personalclaw.sel import sel
from personalclaw.workflows.judge_calibration import (
    CANARY_MIN_SEPARATION,
    DivergenceRecord,
    assess_separation,
)
from personalclaw.workflows.judge_contract import (
    JudgeVerdict,
    aggregate_samples,
    hints_from_dict,
    judge_instruction,
    parse_judge_json,
    validate_verdict,
)

logger = logging.getLogger(__name__)

# ── the fixture vocabulary ───────────────────────────────────────────────────

#: A real judged run harvested from history: the known verdict may be either value.
FIXTURE_REAL = "real"
#: A deliberately-bad probe. Its known verdict is REJECT by construction — a null probe
#: whose label said PASS would be a bad fixture, so :func:`load_fixture_set` refuses one.
FIXTURE_NULL = "null"
#: An artifact whose own account admits a forbidden success mode (a deleted test, a
#: hardcoded value). Known verdict REJECT, and missing one disqualifies a tier outright.
FIXTURE_FORBIDDEN = "forbidden"
FIXTURE_KINDS = (FIXTURE_REAL, FIXTURE_NULL, FIXTURE_FORBIDDEN)

#: Kinds whose label is fixed by construction.
_MUST_REJECT_KINDS = (FIXTURE_NULL, FIXTURE_FORBIDDEN)

VERDICT_PASS = "PASS"
VERDICT_REJECT = "REJECT"
KNOWN_VERDICTS = (VERDICT_PASS, VERDICT_REJECT)

#: Where the fixture under judgement sits relative to its counterpart in the prompt.
#: Swapping it and re-asking is the positional-bias measurement: same artifact, same
#: rubric, different slot. A verdict that moves measured the slot, not the work.
POSITION_FIRST = "first"
POSITION_SECOND = "second"
POSITIONS = (POSITION_FIRST, POSITION_SECOND)

#: The judge tiers, spelled as the engine's `model_tier` intents (see
#: :func:`use_case_for_tier`). Ordered cheapest-intent first, which is the tie-break the
#: recommendation applies when two adequate rows cost the same.
TIERS = ("fast", "standard", "reasoning")

#: The sample counts §6 names. 5 is also `engine.MAX_JUDGE_SAMPLES`, so the top column is
#: the most a live gate can ask for — asserted in the tests rather than assumed here.
SAMPLE_COUNTS = (1, 3, 5)

# ── the agreement vocabulary, borrowed from the live divergence record ───────

DIRECTION_AGREEMENT = "agreement"
DIRECTION_FALSE_PASS = "false_pass"
DIRECTION_FALSE_REJECT = "false_reject"

# ── the adequacy floors ──────────────────────────────────────────────────────
# Deliberately module constants, not config. A floor an operator can lower is not a
# floor: the one move that makes an inadequate tier presentable is editing the number
# that called it inadequate, and this table exists to stop a judge being bound on
# wishful evidence. `EvalsConfig.judge_agreement_floor` is NOT reused — its documented
# consumer is ES-5's study verdict over position-swap agreement, a different metric on a
# different subject, and sharing one number across two would make each unreadable.

#: Agreement with the known verdict below which a tier cannot be recommended. One miss in
#: ten is the most a gate-deciding instrument can carry; the fixture sets are a dozen to
#: thirty cases, so this is 1-3 misses, not a rounding allowance.
AGREEMENT_FLOOR = 0.9

#: Position-swap flip rate above which a tier cannot be recommended. A judge whose verdict
#: moves with the slot one time in ten is reporting the prompt layout.
FLIP_RATE_CEILING = 0.10

#: Strong-vs-null separation floor. Imported, never restated — see the module docstring.
MIN_SEPARATION = CANARY_MIN_SEPARATION

# ── recommendation verdicts ──────────────────────────────────────────────────

REC_RECOMMENDED = "recommended"
REC_NO_ADEQUATE_TIER = "no_adequate_tier"
REC_COST_UNKNOWN = "cost_unknown"
RECOMMENDATION_VERDICTS = (REC_RECOMMENDED, REC_NO_ADEQUATE_TIER, REC_COST_UNKNOWN)

#: Where the shipped fixture sets live inside the installed package.
_PACKAGED_SUBDIR = "benchmarks/judge"


class JudgeBenchError(ValueError):
    """A malformed fixture set. Raised rather than defaulted: a benchmark that silently
    dropped a fixture, or accepted a null probe labelled PASS, would report a tier as
    adequate on a set that never tested it."""


# ── tiers → use cases ────────────────────────────────────────────────────────


def use_case_for_tier(tier: str) -> str:
    """The model use case a judge tier resolves to.

    Reads the engine's own `DEFAULT_MODEL_TIERS` (imported lazily — `workflows.engine`
    is a heavy module and this one is imported by a CLI path), so the benchmark measures
    the axes a template's `model_tier` actually selects rather than a parallel table that
    would drift the first time the engine's changed.
    """
    from personalclaw.workflows.engine import DEFAULT_MODEL_TIERS

    table = dict(DEFAULT_MODEL_TIERS)
    if tier not in table:
        raise JudgeBenchError(f"unknown judge tier {tier!r} (known: {', '.join(sorted(table))})")
    return table[tier]


def model_ref_for_tier(tier: str) -> str:
    """The concrete ``"Provider:model"`` a tier resolves to today, or ``""`` when nothing
    is bound. This is the ref the Models panel binds, so the recommendation names the
    exact string a user clicks rather than an intent they must translate."""
    from personalclaw.workflows.engine import resolve_axis_model

    return resolve_axis_model(use_case_for_tier(tier))


# ── fixtures ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class JudgeFixture:
    """One (artifact, rubric, known-good verdict) triple."""

    id: str
    #: The grouping key of the table. §6's unit is "per (rubric-class × tier × samples)":
    #: a convergence rubric and a deliverable rubric are different jobs, and one tier can
    #: be adequate for one and blind on the other.
    rubric_class: str
    kind: str
    #: What the judge scores — the worker's output/cycle summary, verbatim.
    artifact: str
    goal: str
    dod: str
    known_verdict: str
    #: A `runtime_hints.judge` block, parsed by the contract's own `hints_from_dict`.
    rubric: dict = field(default_factory=dict)
    #: A fixture this one is the null counterpart of (or the reverse). Only a PAIRED
    #: fixture can be position-swapped or contribute a separation number.
    pairs_with: str = ""
    #: Why this fixture exists — carried into the table's failure-mode notes so a
    #: surprising cell explains itself without opening the fixture file.
    failure_note: str = ""

    def hints(self):
        return hints_from_dict(self.rubric)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "rubric_class": self.rubric_class,
            "kind": self.kind,
            "artifact": self.artifact,
            "goal": self.goal,
            "dod": self.dod,
            "known_verdict": self.known_verdict,
            "rubric": dict(self.rubric),
            "pairs_with": self.pairs_with,
            "failure_note": self.failure_note,
        }


@dataclass(frozen=True)
class FixtureSet:
    """A named, hashable set of fixtures — the benchmark's subject."""

    name: str
    version: int
    fixtures: list[JudgeFixture]
    sha256: str = ""
    path: str = ""

    def by_id(self, fixture_id: str) -> JudgeFixture | None:
        return next((f for f in self.fixtures if f.id == fixture_id), None)

    def counterpart(self, fixture: JudgeFixture) -> JudgeFixture | None:
        """The fixture this one is paired with, resolved in BOTH directions.

        A set declares the pairing once (on the null side, conventionally); reading it
        both ways means the strong side does not have to restate it, so the two halves
        can never disagree about whether they are a pair.
        """
        if fixture.pairs_with:
            return self.by_id(fixture.pairs_with)
        return next((f for f in self.fixtures if f.pairs_with == fixture.id), None)

    def rubric_classes(self) -> list[str]:
        return sorted({f.rubric_class for f in self.fixtures})


def packaged_dir() -> Path:
    """The shipped fixture-set dir inside the installed package."""
    return Path(__file__).resolve().parent / _PACKAGED_SUBDIR


def resolve_fixture_set_path(name: str) -> Path:
    """Resolve a fixture-set name to a file: an explicit path, else the home's copy, else
    the packaged one.

    Home-wins-over-package is the resolution rule `pinning.prompt_pack_manifest` already
    applies to prompts, reused deliberately: a user who edits or adds a fixture set gets
    their own, and nothing has to be backfilled into the home for the shipped sets to be
    runnable on a fresh install.
    """
    as_path = Path(name)
    if as_path.is_file():
        return as_path
    home = store.judge_benchmarks_dir() / f"{name}.json"
    if home.is_file():
        return home
    packaged = packaged_dir() / f"{name}.json"
    if packaged.is_file():
        return packaged
    shipped = sorted(p.stem for p in packaged_dir().glob("*.json"))
    raise JudgeBenchError(
        f"judge fixture set {name!r} not found (as a path, in {store.judge_benchmarks_dir()}, "
        f"or shipped); shipped sets: {', '.join(shipped) or '<none>'}"
    )


def canonical_json(data: object) -> str:
    """Canonical form every hash in this module uses (see `scenarios.canonical_json` —
    the same rule, so reformatting a fixture file never moves its identity)."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def fixture_set_sha256(data: dict) -> str:
    import hashlib

    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def parse_fixture_set(data: dict, *, path: str = "") -> FixtureSet:
    """Validate and build a :class:`FixtureSet` from parsed JSON.

    Every refusal here is a fixture that would have produced a real-looking number from a
    test that was not run. The label rule is the sharpest of them: a `null`/`forbidden`
    fixture labelled PASS would make the strong-vs-null separation and the forbidden-mode
    disqualifier both trivially satisfiable, which is the same as deleting them.
    """
    if not isinstance(data, dict):
        raise JudgeBenchError("a fixture set must be a JSON object")
    name = str(data.get("name") or "").strip()
    if not name:
        raise JudgeBenchError("fixture set declares no `name`")
    raw_fixtures = data.get("fixtures")
    if not isinstance(raw_fixtures, list) or not raw_fixtures:
        raise JudgeBenchError(f"fixture set {name!r} has no `fixtures`")

    seen: set[str] = set()
    built: list[JudgeFixture] = []
    for raw in raw_fixtures:
        if not isinstance(raw, dict):
            raise JudgeBenchError(f"{name}: a fixture must be an object, got {type(raw).__name__}")
        fid = str(raw.get("id") or "").strip()
        if not fid:
            raise JudgeBenchError(f"{name}: a fixture has no `id`")
        if fid in seen:
            raise JudgeBenchError(f"{name}: duplicate fixture id {fid!r}")
        seen.add(fid)
        kind = str(raw.get("kind") or "").strip()
        if kind not in FIXTURE_KINDS:
            raise JudgeBenchError(
                f"{name}/{fid}: kind {kind!r} is not one of {', '.join(FIXTURE_KINDS)}"
            )
        known = str(raw.get("known_verdict") or "").strip().upper()
        if known not in KNOWN_VERDICTS:
            raise JudgeBenchError(
                f"{name}/{fid}: known_verdict {known!r} is not one of {', '.join(KNOWN_VERDICTS)}"
            )
        if kind in _MUST_REJECT_KINDS and known != VERDICT_REJECT:
            raise JudgeBenchError(
                f"{name}/{fid}: a {kind!r} fixture must be labelled {VERDICT_REJECT} — a "
                "deliberately-bad case labelled PASS makes the separation and forbidden-mode "
                "checks trivially satisfiable, which is the same as not having them"
            )
        artifact = str(raw.get("artifact") or "").strip()
        if not artifact:
            raise JudgeBenchError(f"{name}/{fid}: no `artifact` for the judge to score")
        rubric_class = str(raw.get("rubric_class") or "").strip()
        if not rubric_class:
            raise JudgeBenchError(
                f"{name}/{fid}: no `rubric_class` — it is the table's grouping key, and an "
                "unclassed fixture would average a convergence judge against a deliverable one"
            )
        rubric = raw.get("rubric")
        built.append(
            JudgeFixture(
                id=fid,
                rubric_class=rubric_class,
                kind=kind,
                artifact=artifact,
                goal=str(raw.get("goal") or ""),
                dod=str(raw.get("dod") or ""),
                known_verdict=known,
                rubric=dict(rubric) if isinstance(rubric, dict) else {},
                pairs_with=str(raw.get("pairs_with") or ""),
                failure_note=str(raw.get("failure_note") or ""),
            )
        )

    for fixture in built:
        if fixture.pairs_with and fixture.pairs_with not in seen:
            raise JudgeBenchError(
                f"{name}/{fixture.id}: pairs_with {fixture.pairs_with!r} "
                "names no fixture in the set"
            )

    return FixtureSet(
        name=name,
        version=int(data.get("version", 1) or 1),
        fixtures=built,
        sha256=fixture_set_sha256(data),
        path=path,
    )


def load_fixture_set(name: str) -> FixtureSet:
    """Resolve and parse a fixture set by name (or path)."""
    path = resolve_fixture_set_path(name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JudgeBenchError(f"fixture set {path} could not be read: {exc}") from exc
    return parse_fixture_set(data, path=str(path))


def list_fixture_sets() -> list[str]:
    """Every runnable fixture-set name — shipped plus the home's own, deduped."""
    names = {p.stem for p in packaged_dir().glob("*.json")}
    names |= {p.stem for p in store.judge_benchmarks_dir().glob("*.json")}
    return sorted(names)


# ── the prompt ───────────────────────────────────────────────────────────────

_SINGLE_BODY = """You are judging one candidate's work against a goal and a definition of done.

GOAL: {goal}
DEFINITION OF DONE: {dod}

CANDIDATE {label} — the work under judgement:
{artifact}
"""

_PAIRED_BODY = """You are judging ONE of two candidates' work against a goal and a \
definition of done.

GOAL: {goal}
DEFINITION OF DONE: {dod}

CANDIDATE A:
{a}

CANDIDATE B:
{b}

Judge CANDIDATE {label} only. The other candidate is context, not the subject of your \
verdict.
"""


def render_bench_prompt(
    fixture: JudgeFixture,
    counterpart: JudgeFixture | None,
    *,
    position: str,
) -> str:
    """Render the judge prompt for one cell.

    The body is wrapped by `judge_instruction`, so the answer schema, the closed verdict
    vocabulary, the proof requirement and the exact rubric keys all come from the module
    that will validate the answer. A benchmark that wrote its own schema would measure a
    tier against a contract the engine does not use.

    With a counterpart present the pair is rendered as CANDIDATE A / CANDIDATE B and
    ``position`` decides which slot the fixture under judgement occupies — that swap is
    the positional-bias measurement. Without one, ``position`` must be
    :data:`POSITION_FIRST`: there is no second slot to move to, and pretending there was
    would report a flip rate of zero for a swap that never happened.
    """
    if position not in POSITIONS:
        raise JudgeBenchError(f"unknown position {position!r} (known: {', '.join(POSITIONS)})")
    if counterpart is None:
        if position != POSITION_FIRST:
            raise JudgeBenchError(
                f"fixture {fixture.id!r} has no counterpart, so it cannot be judged at "
                f"position {position!r} — an unpaired fixture is not position-swappable"
            )
        body = _SINGLE_BODY.format(
            goal=fixture.goal, dod=fixture.dod, artifact=fixture.artifact, label="A"
        )
    else:
        first, second = (
            (fixture, counterpart) if position == POSITION_FIRST else (counterpart, fixture)
        )
        body = _PAIRED_BODY.format(
            goal=fixture.goal,
            dod=fixture.dod,
            a=first.artifact,
            b=second.artifact,
            label="A" if position == POSITION_FIRST else "B",
        )
    return judge_instruction(body, fixture.hints())


# ── the judge call seam ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class JudgeCall:
    """One judge invocation's raw result and what it cost.

    ``cost_usd`` is ``None`` for "nothing priced this call", never ``0.0``: a model whose
    price is unknown must not win "cheapest adequate tier" by looking free. ``elapsed_secs``
    is always real — a clock needs no provider support.
    """

    text: str
    elapsed_secs: float = 0.0
    cost_usd: float | None = None
    model: str = ""


#: ``(prompt, *, use_case) -> JudgeCall``. Injected so every test in this module runs
#: without a provider, and so the ONE place a model is called is nameable.
JudgeCaller = Callable[..., Awaitable[JudgeCall]]


def _audit_cost_since(started_ts: float, use_case: str) -> tuple[float | None, str]:
    """``(cost_usd, model)`` for the attempts the guard audited since ``started_ts``.

    The model-call guard already records `dollars_est`/`model` per attempt
    (`guardrails.audit`), so the benchmark reads the audit rather than re-deriving price
    from a token count it cannot see. A total of exactly zero is returned as ``None``:
    `_estimate_dollars` documents 0.0 as "an honest unknown" for an unpriced model, and
    carrying it forward as a real price is precisely what would make an unpriced tier the
    recommended one.
    """
    try:
        from personalclaw.guardrails.audit import read_recent

        rows = [
            r
            for r in read_recent(200)
            if float(r.get("ts") or 0.0) >= started_ts and str(r.get("use_case") or "") == use_case
        ]
    except Exception:  # noqa: BLE001 - an unreadable audit means unknown cost, not a failed run
        logger.debug("judge bench could not read the attempt audit", exc_info=True)
        return None, ""
    if not rows:
        return None, ""
    total = sum(float(r.get("dollars_est") or 0.0) for r in rows)
    model = str(rows[-1].get("model") or "")
    return (total if total > 0.0 else None), model


async def live_judge_caller(prompt: str, *, use_case: str) -> JudgeCall:
    """The one function in this module that spends money.

    Routes through `one_shot_completion` on the tier's use case, so resolution is the same
    active-models chain a live gate walks and the tier axis is applied by the same bridge
    (no hardcoded provider). Wall time is measured here; cost comes from the attempt audit
    the guard writes at the bridge seam.
    """
    from personalclaw.llm_helpers import one_shot_completion

    started_ts = time.time()
    started = time.monotonic()
    text = await one_shot_completion(prompt, use_case=use_case)
    elapsed = time.monotonic() - started
    cost, model = _audit_cost_since(started_ts, use_case)
    return JudgeCall(text=str(text or ""), elapsed_secs=elapsed, cost_usd=cost, model=model)


# ── one observation ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Observation:
    """One (fixture × tier × samples × position) cell's measured result.

    ``calls`` is the axis-consumption evidence: it is what the sample axis MOVED, recorded
    rather than asserted, so a reader (and a test) can see that samples=5 cost five judge
    calls instead of one relabelled three times.
    """

    fixture_id: str
    rubric_class: str
    kind: str
    known_verdict: str
    tier: str
    samples: int
    position: str
    outcome: str
    #: The fixture this cell was rendered against, from the set's DECLARED pairing. The
    #: separation metric needs it: matching a null to "some strong in the same class" pairs
    #: `conv-null-restate` with `conv-strong-tests` the moment a class holds two pairs, and
    #: reports the wrong difference under the right-looking name.
    counterpart_id: str = ""
    verdict: str = ""
    quality_score: float | None = None
    direction: str = ""
    protocol_errors: int = 0
    sample_verdicts: list[str] = field(default_factory=list)
    calls: int = 0
    cost_usd: float | None = None
    elapsed_secs: float = 0.0
    model: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "fixture_id": self.fixture_id,
            "rubric_class": self.rubric_class,
            "kind": self.kind,
            "known_verdict": self.known_verdict,
            "tier": self.tier,
            "samples": self.samples,
            "position": self.position,
            "outcome": self.outcome,
            "counterpart_id": self.counterpart_id,
            "verdict": self.verdict,
            "quality_score": self.quality_score,
            "direction": self.direction,
            "protocol_errors": self.protocol_errors,
            "sample_verdicts": list(self.sample_verdicts),
            "calls": self.calls,
            "cost_usd": self.cost_usd,
            "elapsed_secs": round(self.elapsed_secs, 3),
            "model": self.model,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Observation":
        raw_q = data.get("quality_score")
        raw_cost = data.get("cost_usd")
        return cls(
            fixture_id=str(data.get("fixture_id", "")),
            rubric_class=str(data.get("rubric_class", "")),
            kind=str(data.get("kind", "")),
            known_verdict=str(data.get("known_verdict", "")),
            tier=str(data.get("tier", "")),
            samples=int(data.get("samples", 0) or 0),
            position=str(data.get("position", "")),
            outcome=str(data.get("outcome", VERIFIER_ABSENT)),
            counterpart_id=str(data.get("counterpart_id", "")),
            verdict=str(data.get("verdict", "")),
            quality_score=(None if raw_q is None else float(raw_q)),
            direction=str(data.get("direction", "")),
            protocol_errors=int(data.get("protocol_errors", 0) or 0),
            sample_verdicts=[str(v) for v in (data.get("sample_verdicts") or [])],
            calls=int(data.get("calls", 0) or 0),
            cost_usd=(None if raw_cost is None else float(raw_cost)),
            elapsed_secs=float(data.get("elapsed_secs", 0.0) or 0.0),
            model=str(data.get("model", "")),
            note=str(data.get("note", "")),
        )


def direction_for(judge_verdict: str, known_verdict: str, *, fixture_id: str = "") -> str:
    """Agreement / false_pass / false_reject — via the live divergence record.

    A fixture's known verdict is a human label, so "the judge disagreed with it" is
    literally a :class:`DivergenceRecord`. Constructing one means the benchmark's
    agreement metric and the product's live calibration metric are the same arithmetic,
    and a change to one can never quietly diverge from the other.
    """
    return DivergenceRecord(
        run_id="judge_bench",
        node_id=fixture_id,
        template="judge_bench",
        judge_verdict=judge_verdict,
        human_verdict=known_verdict,
    ).direction


async def observe_cell(
    fixture: JudgeFixture,
    counterpart: JudgeFixture | None,
    *,
    tier: str,
    samples: int,
    position: str,
    caller: JudgeCaller,
) -> Observation:
    """Measure ONE cell: judge ``fixture`` ``samples`` times at ``tier``, in ``position``.

    ``samples`` is consumed here and nowhere else — the loop below runs exactly that many
    times and the count lands on the observation. `aggregate_samples` then applies the
    contract's own majority/escalation/forbidden-mode rules, so a 3-sample cell is decided
    the way a live `judge_samples: 3` gate decides.

    A cell that produced no parseable verdict is ``VERIFIER_ABSENT``: the instrument
    failed, not the work, and averaging it in as a wrong answer would make an unusable
    tier look merely inaccurate.
    """
    if samples < 1:
        raise JudgeBenchError(f"samples must be >= 1, got {samples}")
    use_case = use_case_for_tier(tier)
    hints = fixture.hints()
    prompt = render_bench_prompt(fixture, counterpart, position=position)

    verdicts: list[JudgeVerdict] = []
    protocol_errors = 0
    calls = 0
    elapsed = 0.0
    costs: list[float] = []
    model = ""
    call_errors: list[str] = []

    for _ in range(samples):
        try:
            call = await caller(prompt, use_case=use_case)
        except Exception as exc:  # noqa: BLE001 - a provider fault is an absent sample
            call_errors.append(str(exc)[:200])
            calls += 1
            continue
        calls += 1
        elapsed += float(call.elapsed_secs or 0.0)
        if call.cost_usd is not None:
            costs.append(float(call.cost_usd))
        model = call.model or model
        raw = parse_judge_json(call.text)
        if raw is None:
            protocol_errors += 1
            continue
        verdict = validate_verdict(raw, hints)
        if verdict.protocol_error:
            protocol_errors += 1
        verdicts.append(verdict)

    if not verdicts:
        reason = "; ".join(call_errors[:2]) if call_errors else "no parseable judge verdict"
        return Observation(
            fixture_id=fixture.id,
            rubric_class=fixture.rubric_class,
            kind=fixture.kind,
            known_verdict=fixture.known_verdict,
            tier=tier,
            samples=samples,
            position=position,
            outcome=VERIFIER_ABSENT,
            counterpart_id=(counterpart.id if counterpart else ""),
            protocol_errors=protocol_errors,
            calls=calls,
            cost_usd=(sum(costs) if costs else None),
            elapsed_secs=elapsed,
            model=model,
            note=reason,
        )

    final = aggregate_samples(verdicts, hints)
    return Observation(
        fixture_id=fixture.id,
        rubric_class=fixture.rubric_class,
        kind=fixture.kind,
        known_verdict=fixture.known_verdict,
        tier=tier,
        samples=samples,
        position=position,
        outcome=(PASSED if final.passed else FAILED),
        counterpart_id=(counterpart.id if counterpart else ""),
        verdict=(VERDICT_PASS if final.passed else final.verdict.value),
        quality_score=final.quality_score,
        direction=direction_for(
            VERDICT_PASS if final.passed else VERDICT_REJECT,
            fixture.known_verdict,
            fixture_id=fixture.id,
        ),
        protocol_errors=protocol_errors,
        sample_verdicts=[v.verdict.value for v in verdicts],
        calls=calls,
        cost_usd=(sum(costs) if costs else None),
        elapsed_secs=elapsed,
        model=model,
        note=(fixture.failure_note if final.passed and fixture.kind != FIXTURE_REAL else ""),
    )


# ── the tier-recommendation table (pure) ─────────────────────────────────────


@dataclass(frozen=True)
class TableRow:
    """One (rubric-class × tier × samples) row of the tier-recommendation table."""

    rubric_class: str
    tier: str
    samples: int
    agreement: float | None
    scored_cells: int
    verifier_absent: int
    protocol_errors: int
    separation: float | None
    flip_rate: float | None
    swapped_fixtures: int
    false_passes: int
    false_rejects: int
    forbidden_missed: int
    cost_usd: float | None
    wall_secs: float
    calls: int
    adequate: bool
    inadequate_reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rubric_class": self.rubric_class,
            "tier": self.tier,
            "samples": self.samples,
            "agreement": self.agreement,
            "scored_cells": self.scored_cells,
            "verifier_absent": self.verifier_absent,
            "protocol_errors": self.protocol_errors,
            "separation": self.separation,
            "flip_rate": self.flip_rate,
            "swapped_fixtures": self.swapped_fixtures,
            "false_passes": self.false_passes,
            "false_rejects": self.false_rejects,
            "forbidden_missed": self.forbidden_missed,
            "cost_usd": self.cost_usd,
            "wall_secs": round(self.wall_secs, 2),
            "calls": self.calls,
            "adequate": self.adequate,
            "inadequate_reasons": list(self.inadequate_reasons),
            "notes": list(self.notes),
        }


#: The table's TSV columns, in order. A static artifact the user can open is half of §6's
#: output; the panel reads the JSON.
TABLE_COLUMNS: tuple[str, ...] = (
    "rubric_class",
    "tier",
    "samples",
    "agreement",
    "separation",
    "flip_rate",
    "cost_usd",
    "wall_secs",
    "scored_cells",
    "verifier_absent",
    "protocol_errors",
    "false_passes",
    "false_rejects",
    "forbidden_missed",
    "adequate",
    "notes",
)


def _mean(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def _separation_for(observations: list[Observation]) -> tuple[float | None, list[str]]:
    """Mean strong-minus-null quality separation over the pairs present in ``observations``.

    Pairs come from the set's DECLARED pairing, carried on
    :attr:`Observation.counterpart_id`, matched within one position. Both halves are
    required: matching a null to "some strong in the same rubric class" was measured to
    pair `conv-null-restate` with `conv-strong-tests` the moment a class held two pairs —
    the wrong difference under the right-looking name.

    ``None`` when no pair produced both scores. `assess_separation` supplies the verdict
    text, so the benchmark and the loop canary describe a blind judge in the same words.
    """
    notes: list[str] = []
    by_key = {(o.fixture_id, o.position): o for o in observations}
    seps: list[float] = []
    for obs in sorted(observations, key=lambda o: (o.fixture_id, o.position)):
        if obs.kind != FIXTURE_NULL or obs.quality_score is None:
            continue
        # The DECLARED counterpart, in the SAME slot. Same slot because comparing a strong
        # verdict from slot A against a null verdict from slot B would fold positional bias
        # into the separation number and report one effect as the other.
        strong = by_key.get((obs.counterpart_id, obs.position))
        if (
            strong is None
            or strong.kind != FIXTURE_REAL
            or strong.known_verdict != VERDICT_PASS
            or strong.quality_score is None
        ):
            continue
        result = assess_separation(strong.quality_score, obs.quality_score)
        if result.separation is not None:
            seps.append(result.separation)
            if result.blind:
                note = f"{strong.fixture_id} vs {obs.fixture_id}: {result.detail}"
                # One note per PAIR, not per slot: the same collapse observed in both
                # positions is one finding, and printing it twice buries the others.
                if note not in notes:
                    notes.append(note)
    if not seps:
        notes.append(
            "no strong/null pair produced both scores — separation is UNMEASURED, which is "
            "not the same as adequate"
        )
        return None, notes
    return round(sum(seps) / len(seps), 4), notes


def _flip_rate_for(observations: list[Observation]) -> tuple[float | None, int, list[str]]:
    """``(flip_rate, swapped_fixtures, notes)`` over fixtures observed in BOTH positions.

    A fixture judged in only one slot contributes nothing — counting it as "did not flip"
    is how a swap that never ran reports a clean zero.
    """
    notes: list[str] = []
    positions: dict[str, dict[str, Observation]] = {}
    for obs in observations:
        if obs.outcome == VERIFIER_ABSENT:
            continue
        positions.setdefault(obs.fixture_id, {})[obs.position] = obs
    both = {
        fid: slots
        for fid, slots in positions.items()
        if POSITION_FIRST in slots and POSITION_SECOND in slots
    }
    if not both:
        notes.append(
            "no fixture was judged in both positions — the position-swap flip rate is "
            "UNMEASURED; pair a fixture and re-run before trusting this tier"
        )
        return None, 0, notes
    flips = 0
    for fid, slots in sorted(both.items()):
        if slots[POSITION_FIRST].verdict != slots[POSITION_SECOND].verdict:
            flips += 1
            notes.append(
                f"{fid}: verdict flipped with position "
                f"({slots[POSITION_FIRST].verdict} first, {slots[POSITION_SECOND].verdict} second)"
            )
    return round(flips / len(both), 4), len(both), notes


@dataclass(frozen=True)
class RowStats:
    """The measured numbers :func:`adequacy` judges. A typed record rather than a loose dict
    so that adding a floor cannot silently read a key nothing writes."""

    agreement: float | None
    separation: float | None
    flip_rate: float | None
    verifier_absent: int
    protocol_errors: int
    false_passes: int
    false_rejects: int
    forbidden_missed: int


def adequacy(stats: RowStats) -> tuple[bool, list[str]]:
    """Every reason a row falls short, not just the first.

    An operator choosing a judge needs the whole list: a tier that is both below the
    agreement floor AND misses a forbidden-mode case has two different problems, and a
    first-wins report would send them to fix the cheaper one.
    """
    reasons: list[str] = []
    if stats.agreement is None:
        reasons.append(
            f"no cell produced a verdict ({stats.verifier_absent} verifier_absent, "
            f"{stats.protocol_errors} protocol errors) — nothing was measured"
        )
    elif stats.agreement < AGREEMENT_FLOOR:
        reasons.append(
            f"agreement with the known verdict {stats.agreement:.2f} < {AGREEMENT_FLOOR} "
            f"({stats.false_passes} false pass, {stats.false_rejects} false reject)"
        )
    if stats.separation is None:
        reasons.append("strong-vs-null separation was never measured (no paired fixture scored)")
    elif stats.separation < MIN_SEPARATION:
        reasons.append(
            f"strong-vs-null separation {stats.separation:.2f} < {MIN_SEPARATION} — this judge "
            "does not distinguish strong work from nothing, so its verdicts carry no information"
        )
    if stats.flip_rate is None:
        reasons.append("position-swap flip rate was never measured (no fixture judged both ways)")
    elif stats.flip_rate > FLIP_RATE_CEILING:
        reasons.append(
            f"position-swap flip rate {stats.flip_rate:.2f} > {FLIP_RATE_CEILING} — the verdict "
            "moves with the prompt slot"
        )
    if stats.forbidden_missed:
        reasons.append(
            f"{stats.forbidden_missed} forbidden-success-mode fixture(s) PASSED — a "
            "disqualifier is a fact, so this alone rules the tier out"
        )
    return (not reasons), reasons


def build_table(observations: list[Observation]) -> list[TableRow]:
    """The tier-recommendation table. Pure, deterministic, no clock and no I/O.

    Grouped per (rubric-class × tier × samples) — §6's unit. Deterministic ordering means
    two runs over the same ``observations.json`` render byte-identically, which is what
    makes the table quotable: a table that moved between renders could not recommend
    anything.
    """
    groups: dict[tuple[str, str, int], list[Observation]] = {}
    for obs in observations:
        groups.setdefault((obs.rubric_class, obs.tier, obs.samples), []).append(obs)

    rows: list[TableRow] = []
    for (rubric_class, tier, samples), group in groups.items():
        scored = [o for o in group if o.outcome != VERIFIER_ABSENT]
        absent = [o for o in group if o.outcome == VERIFIER_ABSENT]
        agreements = [1.0 if o.direction == DIRECTION_AGREEMENT else 0.0 for o in scored]
        separation, sep_notes = _separation_for(scored)
        flip_rate, swapped, flip_notes = _flip_rate_for(group)
        costs = [o.cost_usd for o in group if o.cost_usd is not None]
        forbidden_missed = [
            o for o in scored if o.kind == FIXTURE_FORBIDDEN and o.verdict == VERDICT_PASS
        ]
        stats = RowStats(
            agreement=(None if not agreements else round(_mean(agreements) or 0.0, 4)),
            separation=separation,
            flip_rate=flip_rate,
            verifier_absent=len(absent),
            protocol_errors=sum(o.protocol_errors for o in group),
            false_passes=sum(1 for o in scored if o.direction == DIRECTION_FALSE_PASS),
            false_rejects=sum(1 for o in scored if o.direction == DIRECTION_FALSE_REJECT),
            forbidden_missed=len(forbidden_missed),
        )
        adequate, reasons = adequacy(stats)

        notes = list(sep_notes) + list(flip_notes)
        for obs in forbidden_missed:
            notes.append(
                f"{obs.fixture_id}: forbidden-mode case PASSED"
                + (f" — {obs.note}" if obs.note else "")
            )
        for obs in absent:
            notes.append(f"{obs.fixture_id} at position {obs.position}: absent — {obs.note}")
        spread = {
            tuple(sorted(set(o.sample_verdicts))) for o in scored if len(o.sample_verdicts) > 1
        }
        for combo in sorted(spread):
            if len(combo) > 1:
                notes.append(
                    f"samples disagreed within a cell ({'/'.join(combo)}) — the recorded "
                    "nondeterminism budget for this row"
                )
        if not costs:
            notes.append(
                "cost UNKNOWN — nothing priced these calls, so this row cannot be ranked by price"
            )

        rows.append(
            TableRow(
                rubric_class=rubric_class,
                tier=tier,
                samples=samples,
                agreement=stats.agreement,
                scored_cells=len(scored),
                verifier_absent=stats.verifier_absent,
                protocol_errors=stats.protocol_errors,
                separation=separation,
                flip_rate=flip_rate,
                swapped_fixtures=swapped,
                false_passes=stats.false_passes,
                false_rejects=stats.false_rejects,
                forbidden_missed=stats.forbidden_missed,
                cost_usd=(round(sum(costs), 6) if costs else None),
                wall_secs=sum(o.elapsed_secs for o in group),
                calls=sum(o.calls for o in group),
                adequate=adequate,
                inadequate_reasons=reasons,
                notes=notes,
            )
        )
    return sorted(
        rows,
        key=lambda r: (r.rubric_class, TIERS.index(r.tier) if r.tier in TIERS else 99, r.samples),
    )


def render_table_tsv(rows: list[TableRow]) -> str:
    """The table as TSV — the static artifact half of §6's output."""

    def cell(value: object) -> str:
        if value is None:
            return "unknown"
        if isinstance(value, list):
            value = " | ".join(str(v) for v in value)
        return str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")

    lines = ["\t".join(TABLE_COLUMNS)]
    for row in rows:
        data = row.to_dict()
        lines.append("\t".join(cell(data.get(col)) for col in TABLE_COLUMNS))
    return "\n".join(lines) + "\n"


# ── the recommendation ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Recommendation:
    """ "Bind this rubric class's judge to this tier at this sample count" — or why not."""

    rubric_class: str
    verdict: str
    tier: str = ""
    samples: int = 0
    use_case: str = ""
    model_ref: str = ""
    cost_usd: float | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rubric_class": self.rubric_class,
            "verdict": self.verdict,
            "tier": self.tier,
            "samples": self.samples,
            "use_case": self.use_case,
            "model_ref": self.model_ref,
            "cost_usd": self.cost_usd,
            "notes": list(self.notes),
        }


def recommend(
    rows: list[TableRow], *, resolve_ref: Callable[[str], str] | None = None
) -> list[Recommendation]:
    """The cheapest ADEQUATE row per rubric class — or an honest refusal.

    Three outcomes, and the two refusals matter more than the recommendation:

    * :data:`REC_NO_ADEQUATE_TIER` — nothing cleared the floors. The notes carry every
      tier's reasons, because "no tier works" is only actionable with them.
    * :data:`REC_COST_UNKNOWN` — adequate rows exist but none has a price, so "cheapest"
      is unanswerable. Naming a tier anyway would be an ordering invented from nothing.
    * :data:`REC_RECOMMENDED` — the lowest-cost adequate row, tie-broken by fewer samples
      then cheaper tier intent, which is the same order a cost-conscious operator would
      apply by hand.

    ``resolve_ref`` is injected so the recommendation can name the concrete
    ``Provider:model`` without a live provider in tests; it defaults to the engine's
    resolver.
    """
    resolver = resolve_ref or model_ref_for_tier
    out: list[Recommendation] = []
    classes = sorted({r.rubric_class for r in rows})
    for rubric_class in classes:
        in_class = [r for r in rows if r.rubric_class == rubric_class]
        adequate = [r for r in in_class if r.adequate]
        if not adequate:
            notes = [
                f"{r.tier}/samples={r.samples}: " + "; ".join(r.inadequate_reasons)
                for r in in_class
            ]
            out.append(
                Recommendation(rubric_class=rubric_class, verdict=REC_NO_ADEQUATE_TIER, notes=notes)
            )
            continue
        priced = [r for r in adequate if r.cost_usd is not None]
        if not priced:
            out.append(
                Recommendation(
                    rubric_class=rubric_class,
                    verdict=REC_COST_UNKNOWN,
                    notes=[
                        f"{r.tier}/samples={r.samples} is adequate but unpriced" for r in adequate
                    ]
                    + [
                        "no adequate row carries a cost, so the cheapest one cannot be "
                        "identified — bind by hand or price the models first"
                    ],
                )
            )
            continue
        best = sorted(
            priced,
            key=lambda r: (
                r.cost_usd,
                r.samples,
                TIERS.index(r.tier) if r.tier in TIERS else 99,
            ),
        )[0]
        try:
            use_case = use_case_for_tier(best.tier)
        except JudgeBenchError:
            use_case = ""
        ref = ""
        if use_case:
            try:
                ref = resolver(best.tier)
            except Exception:  # noqa: BLE001 - an unresolvable ref is a blank, not a failure
                logger.debug("could not resolve a model ref for tier %s", best.tier, exc_info=True)
        notes = [
            f"cheapest adequate: {best.tier} at samples={best.samples}, "
            f"agreement {best.agreement}, separation {best.separation}, "
            f"flip rate {best.flip_rate}"
        ]
        notes.extend(best.notes)
        out.append(
            Recommendation(
                rubric_class=rubric_class,
                verdict=REC_RECOMMENDED,
                tier=best.tier,
                samples=best.samples,
                use_case=use_case,
                model_ref=ref,
                cost_usd=best.cost_usd,
                notes=notes,
            )
        )
    return out


# ── the run ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BenchResult:
    """A whole benchmark run: the spec, the observations, the table, the recommendations."""

    bench_id: str
    spec: MatrixSpec
    observations: list[Observation]
    table: list[TableRow]
    recommendations: list[Recommendation]
    aggregates: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "bench_id": self.bench_id,
            "spec": self.spec.to_dict(),
            "observations": [o.to_dict() for o in self.observations],
            "table": [r.to_dict() for r in self.table],
            "recommendations": [r.to_dict() for r in self.recommendations],
            "aggregates": dict(self.aggregates),
        }


def build_specs(
    fixture_set: FixtureSet,
    *,
    tiers: tuple[str, ...] = TIERS,
    sample_counts: tuple[int, ...] = SAMPLE_COUNTS,
    budget_usd: float = 0.0,
) -> tuple[MatrixSpec, MatrixSpec | None]:
    """``(paired_spec, unpaired_spec)`` — the axes, split by swappability.

    TWO specs rather than one because the position axis does not apply to every fixture.
    Crossing an unpaired fixture with `position` would either burn a duplicate model call
    on an identical prompt or record a fabricated second slot; splitting means every cell
    the product yields is a cell that can actually run. Both go through the shared
    :func:`~personalclaw.evals.matrix.expand_cells`, so the crossing rule stays one rule.
    """
    for tier in tiers:
        use_case_for_tier(tier)  # raises on an unknown tier before anything spends
    paired_ids = [f.id for f in fixture_set.fixtures if fixture_set.counterpart(f) is not None]
    unpaired_ids = [f.id for f in fixture_set.fixtures if fixture_set.counterpart(f) is None]
    paired = MatrixSpec(
        subject=fixture_set.name,
        axes={
            "fixture": paired_ids,
            "tier": list(tiers),
            "judge_samples": list(sample_counts),
            "position": list(POSITIONS),
        },
        trial_count=1,
        scorer="judge",
        budget_usd=budget_usd,
    )
    if not unpaired_ids:
        return paired, None
    unpaired = MatrixSpec(
        subject=fixture_set.name,
        axes={
            "fixture": unpaired_ids,
            "tier": list(tiers),
            "judge_samples": list(sample_counts),
            "position": [POSITION_FIRST],
        },
        trial_count=1,
        scorer="judge",
        budget_usd=budget_usd,
    )
    return paired, unpaired


def bench_cells(paired: MatrixSpec, unpaired: MatrixSpec | None) -> list[dict]:
    """Every runnable coordinate dict across both specs, with the trial key stripped.

    Public because the CLI prints the cell/call preflight from it: a user must be able to
    see what a matrix will spend BEFORE it spends it, and that count has to come from the
    same expansion the run walks."""
    combos = list(expand_cells(paired))
    if unpaired is not None:
        combos.extend(expand_cells(unpaired))
    return [{k: v for k, v in combo.items() if k != TRIAL_KEY} for combo in combos]


def new_bench_id() -> str:
    """A sortable, filesystem-safe run id."""
    return "judge-bench-" + datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


async def run_judge_bench(
    set_name: str,
    *,
    caller: JudgeCaller | None = None,
    tiers: tuple[str, ...] = TIERS,
    sample_counts: tuple[int, ...] = SAMPLE_COUNTS,
    bench_id: str = "",
    budget_usd: float = 0.0,
    resolve_ref: Callable[[str], str] | None = None,
) -> BenchResult:
    """Run the whole benchmark and persist every artifact under ``matrices/<bench_id>/``.

    Cells run SEQUENTIALLY, like `run_matrix`'s: this is a single-user machine and the
    judge calls are the cost. The pin is computed FIRST and refused if incomplete, so a
    run whose result could never enter the ledger does not burn spend — the same rule
    `run_matrix` applies, reached through
    :func:`~personalclaw.evals.pinning.compute_pin_for_subject` because the subject is a
    fixture set rather than a scenario.
    """
    fixture_set = load_fixture_set(set_name)
    bench_id = bench_id or new_bench_id()
    pin = pinning.compute_pin_for_subject(fixture_set.name, fixture_set.sha256)
    if not pin.is_complete():
        raise store.PinRequiredError(
            f"refusing to run judge bench {bench_id}: incomplete RunPin "
            f"(missing: {', '.join(pin.missing_parts())})"
        )

    paired, unpaired = build_specs(
        fixture_set, tiers=tiers, sample_counts=sample_counts, budget_usd=budget_usd
    )
    bench_dir = store.matrix_dir(bench_id)
    store.write_matrix_experiment(bench_id, paired.to_dict())
    pinning.write_pin(bench_dir, pin)
    _sel_log(bench_id, fixture_set, outcome="started")

    judge = caller or live_judge_caller
    observations: list[Observation] = []
    for coords in bench_cells(paired, unpaired):
        fixture = fixture_set.by_id(str(coords["fixture"]))
        if fixture is None:  # pragma: no cover - ids come from the set itself
            continue
        observations.append(
            await observe_cell(
                fixture,
                fixture_set.counterpart(fixture),
                tier=str(coords["tier"]),
                samples=int(coords["judge_samples"]),
                position=str(coords["position"]),
                caller=judge,
            )
        )

    table = build_table(observations)
    recommendations = recommend(table, resolve_ref=resolve_ref)
    cells = [
        CellResult(
            coords={
                "fixture": o.fixture_id,
                "tier": o.tier,
                "judge_samples": o.samples,
                "position": o.position,
            },
            outcome=o.outcome,
            score=(
                (1.0 if o.direction == DIRECTION_AGREEMENT else 0.0)
                if o.outcome != VERIFIER_ABSENT
                else None
            ),
            artifact_ref=str(bench_dir),
        )
        for o in observations
    ]
    aggregates = aggregate(cells)
    store.write_matrix_aggregates(bench_id, aggregates)
    store.write_matrix_trials(bench_id, cells)
    write_bench_artifacts(bench_id, observations, table, recommendations)
    store.append_result(
        {
            "study_id": bench_id,
            "kind": "judge_bench",
            "verdict": _bench_verdict(recommendations),
            "score_new": aggregates.get("mean_score"),
            "k": max(sample_counts) if sample_counts else 0,
            "ts": datetime.now(tz=timezone.utc).isoformat(),
        },
        pin=pin,
    )
    _sel_log(bench_id, fixture_set, outcome="completed")
    return BenchResult(
        bench_id=bench_id,
        spec=paired,
        observations=observations,
        table=table,
        recommendations=recommendations,
        aggregates=aggregates,
    )


def _bench_verdict(recommendations: list[Recommendation]) -> str:
    """One ledger word: ``pass`` when every rubric class got a tier, else the weakest
    outcome present. A benchmark that recommended nothing is not a passing run."""
    if not recommendations:
        return VERIFIER_ABSENT
    verdicts = {r.verdict for r in recommendations}
    if verdicts == {REC_RECOMMENDED}:
        return "pass"
    if REC_NO_ADEQUATE_TIER in verdicts:
        return "fail"
    return REC_COST_UNKNOWN


def write_bench_artifacts(
    bench_id: str,
    observations: list[Observation],
    table: list[TableRow],
    recommendations: list[Recommendation],
) -> None:
    """Persist the drill-down and the published table.

    ``observations.json`` is the RAW record; ``table.json``/``table.tsv`` are derived from
    it by pure functions, so a reader who distrusts the table can recompute it and a
    reader who distrusts a cell can find the observation that produced it.
    """
    d = store.matrix_dir(bench_id)
    atomic_write(
        d / "observations.json",
        json.dumps([o.to_dict() for o in observations], indent=2, sort_keys=True),
    )
    atomic_write(
        d / "table.json",
        json.dumps(
            {
                "columns": list(TABLE_COLUMNS),
                "rows": [r.to_dict() for r in table],
                "floors": {
                    "agreement": AGREEMENT_FLOOR,
                    "separation": MIN_SEPARATION,
                    "flip_rate": FLIP_RATE_CEILING,
                },
            },
            indent=2,
            sort_keys=True,
        ),
    )
    atomic_write(d / "table.tsv", render_table_tsv(table))
    atomic_write(
        d / "recommendations.json",
        json.dumps([r.to_dict() for r in recommendations], indent=2, sort_keys=True),
    )


def read_observations(bench_id: str) -> list[Observation] | None:
    """A persisted run's raw observations, or ``None``."""
    path = store.matrix_dir(bench_id) / "observations.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return [Observation.from_dict(d) for d in data] if isinstance(data, list) else None


def read_table(bench_id: str) -> dict | None:
    """A persisted run's published table, or ``None``."""
    path = store.matrix_dir(bench_id) / "table.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_recommendations(bench_id: str) -> list[dict]:
    path = store.matrix_dir(bench_id) / "recommendations.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def list_bench_runs() -> list[str]:
    """Persisted judge-bench run ids, newest first (the id sorts chronologically)."""
    return sorted(
        (
            p.name
            for p in store.matrices_dir().iterdir()
            if p.is_dir() and (p / "table.json").exists()
        ),
        reverse=True,
    )


def latest_bench_view() -> dict | None:
    """The panel's payload: the newest run's table + recommendations, or ``None``.

    ``None`` rather than an empty table: "no benchmark has run" and "the benchmark found
    nothing" are different facts, and an empty table would render as the second.
    """
    runs = list_bench_runs()
    if not runs:
        return None
    bench_id = runs[0]
    table = read_table(bench_id)
    if table is None:
        return None
    pin = pinning.read_pin(store.matrix_dir(bench_id))
    return {
        "bench_id": bench_id,
        "columns": table.get("columns") or list(TABLE_COLUMNS),
        "rows": table.get("rows") or [],
        "floors": table.get("floors") or {},
        "recommendations": read_recommendations(bench_id),
        "pin": pin.to_dict() if pin else None,
        "runs": runs,
    }


def _sel_log(bench_id: str, fixture_set: FixtureSet, *, outcome: str) -> None:
    """SEL-log a benchmark lifecycle event (§10). Best-effort — never breaks a run."""
    try:
        sel().log_api_access(
            caller=f"judge_bench:{bench_id}",
            operation="evals_judge_bench",
            outcome=outcome,
            source="evals",
            resources=f"set={fixture_set.name} sha={fixture_set.sha256[:12]}",
        )
    except Exception:
        logger.debug("SEL judge-bench log failed", exc_info=True)
