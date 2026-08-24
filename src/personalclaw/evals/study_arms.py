"""What DRIVES a pre-registered template study — EVALUATION-SUBSTRATE §2 (ES-5).

:mod:`personalclaw.evals.studies` is the instrument: pre-registration, the `locked/` check
DSL, the rubric pin, the blinded position-swapped judge, the verdict. It had no driver —
``ArmRunner`` was a bare type alias and ``arm_runner`` a required kwarg with no default, so
no template arm could execute and nothing in production imported ``run_study`` at all. This
module is the driver, and it owns exactly four seams:

1. **The harvested-suite adapter** (:func:`harvested_study_cases`). §2's corpus is
   ES's harvested regression suite. ``harvest.load_harvested_suite`` RAISES
   :class:`~personalclaw.evals.harvest.EmptyHarvestError` rather than returning ``[]`` — a
   caller that got a list and compared it to a threshold would report a green it never
   measured. So the refusal is caught HERE, once, and turned into a suite that says
   ``refusal`` out loud and carries the ``low_power`` label; it is never turned into zero
   cases that read like a passing study.

2. **The arm prompt** (:func:`render_arm_prompt`) — the ONE place a template body becomes
   the text an arm executes. Bindings are resolved through the engine's own
   :func:`personalclaw.workflows.bindings.resolve`, never a second interpolation dialect,
   and the case's recorded inputs are the thing held STILL while the template varies. That
   direction matters: for a retrieval or skills A/B you hold the prompt still and vary the
   arm, which is why the harvest records ``resolved_prompt_ref``; for a TEMPLATE A/B the
   template is the variable by definition.

3. **The production ``ArmRunner``** (:class:`TemplateArmRunner` / :func:`live_arm_runner`),
   the one function here that spends money, deliberately shaped like ES-4's
   ``live_judge_caller``: one ``one_shot_completion`` on the arm's rendered prompt, wall
   time measured here, cost read back off the guard's attempt audit. Each arm run gets its
   OWN output workspace and the response is written into it, so §2.2's supervisor-side
   `locked/` checks have a real tree to check and the two arms' trees are distinct.

4. **The vacuity gate** (:func:`assert_arms_differ`). An A/B whose two arms render the same
   prompt measures nothing, and it does not fail — it reports a confident `tie`. That is
   the single worst artifact this module could produce, because it is indistinguishable
   from an honest tie. So a study whose arms render identically for EVERY case is refused
   before arm 1, and the count of cases whose arms actually differ is carried in the
   preflight where a reader sees it.

🔴 **What an arm is, stated so nobody has to infer it.** An arm here is ONE model call on
the template's rendered prompt — not a full multi-node engine run. That is a deliberate
bound, not an oversight: :func:`personalclaw.workflows.service.start_run` requires a live
supervisor, which only a running gateway wires (``gateway.py`` sets
``ActionServices.workflows`` from the workflow watchdog), so an engine-driven arm would be
permanently inert in the CLI — the one surface §2's spend preflight exists for. A
prompt-level arm is also what a refiner diff actually changes: ``refiner.check_diff``
freezes ``id``/``triggers``/surfacing metadata, so the legal ops move prompt text. A
trajectory-level arm is a real extension, and it belongs to whoever builds the headless
engine driver.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from personalclaw.evals import harvest, store, studies
from personalclaw.evals.studies import (
    DEFAULT_JUDGE_SAMPLES,
    LOW_POWER_CASES,
    ArmOutput,
    StudyCase,
    StudyError,
    StudyRegistration,
    StudyResult,
    WorkerPayload,
)

logger = logging.getLogger(__name__)

#: The spec fields an arm's prompt is composed from. `prompt` and not a longer list because
#: it is a MEASURED majority, not a guess: across the 26 bundled templates, `prompt` carries
#: 82 of the substantial strings in a node's `config` and the next candidate (`guidance`,
#: `criteria`) carries one each. A body that is not a parseable spec is used verbatim, so a
#: raw-text template arm still executes rather than silently rendering to nothing.
ARM_PROMPT_FIELDS: tuple[str, ...] = ("prompt",)

#: The use case an arm's model call routes through. A template arm is doing the template's
#: own work, so it resolves on the reasoning chain rather than the background one.
#: Overridable per runner; named here so the study's spend is attributable to one axis.
ARM_USE_CASE = "reasoning"

#: How long one arm run may take before it is reported as an unfinished arm. An arm that
#: timed out is ``ok=False``, never an empty output — an empty string handed to the judge
#: would be scored as a bad answer instead of as a missing measurement.
DEFAULT_ARM_TIMEOUT_SECS = 300.0

#: The file an arm's output lands in, inside that arm's own output workspace. This is what
#: makes a `locked/` `path`/`required_phrases` check discriminating: it reads THIS arm's
#: answer. A check whose file is absent is a FAILURE on both arms, which
#: :func:`~personalclaw.evals.studies.locked_regressions` correctly refuses to call a
#: regression (it needs old-PASS + new-FAIL), so a workspace that never got written cannot
#: manufacture a demotion proposal.
ARM_OUTPUT_FILENAME = "output.md"

#: The §2 template-A/B rubric, pinned by hash at registration. Deliberately about the
#: template's JOB and not about prose quality: a rubric that rewarded fluency would promote
#: whichever body was wordier. It names the recorded baseline as the reference because the
#: harvest deliberately does NOT mint a golden from one observed answer.
TEMPLATE_AB_RUBRIC = """\
Judge which response better carries out the recorded request, on these criteria in order:

1. Task completion — does it do what was asked, for the inputs given?
2. Faithfulness — is every claim supported by the inputs, with nothing invented?
3. Actionability — could the requester act on it without asking a follow-up?
4. Restraint — no padding, no restating the request, no hedging in place of an answer.

Compare the two responses only against each other and against these criteria. Do not
reward length, formatting flourish, or confidence. If neither is better on the criteria
above, say so rather than picking one.
"""


class StudyArmError(StudyError):
    """A study cannot be driven — its arms are identical, or its subject is unreadable.

    A subclass of :class:`~personalclaw.evals.studies.StudyError` so a caller that already
    handles "this study cannot run" keeps handling it, and so the refusal can never be
    mistaken for a verdict.
    """


# ── 1. the harvested suite, as study cases ───────────────────────────────────


@dataclass(frozen=True)
class HarvestedSuite:
    """The harvested regression suite in the shape :func:`studies.run_study` consumes.

    Three states, not two. ``cases`` non-empty is a suite. ``cases`` empty WITH a
    ``refusal`` is "the ledger held nothing to harvest" — a statement about the population.
    Both can be :attr:`low_power`, and that label travels with the suite rather than being
    re-derived by each consumer.
    """

    cases: tuple[StudyCase, ...] = ()
    #: ``name@sha256[:12]`` per case — what goes in the registration's ``inputs``, so the
    #: pre-registration names the exact corpus and a later suite cannot be swapped in.
    input_pins: tuple[str, ...] = ()
    refusal: str = ""

    @property
    def population(self) -> int:
        return len(self.cases)

    @property
    def low_power(self) -> bool:
        """Below :data:`~personalclaw.evals.studies.LOW_POWER_CASES` decided cases.

        The same threshold ``decide()`` labels the verdict with, read here so a caller can
        say "this will be low power" BEFORE it spends, not only afterwards.
        """
        return self.population < LOW_POWER_CASES


def harvested_study_cases(*, workflow_name: str = "") -> HarvestedSuite:
    """The harvested suite as :class:`~personalclaw.evals.studies.StudyCase` rows.

    ``case_input`` is the canonical JSON of the run's RECORDED inputs, not a re-rendered
    prompt: those inputs are the variable an A/B holds still, and they were already screened
    exactly once by the harvest at the point they entered
    (:func:`personalclaw.evals.harvest._screen`). Re-screening them here is the documented
    way to corrupt them — ``redact_credentials`` is not idempotent over a composed
    ``key: value`` line — so this function screens NOTHING and composes only.

    Catches :class:`~personalclaw.evals.harvest.EmptyHarvestError` on purpose. Letting it
    escape would make an empty ledger a crash in the middle of the flywheel's filing path;
    swallowing it into ``[]`` would make it a study that reports a green over zero cases.
    It becomes a suite that says so.
    """
    try:
        raw = harvest.load_harvested_suite(workflow_name=workflow_name)
    except harvest.EmptyHarvestError as exc:
        logger.info(
            "study: no harvested population%s", f" for {workflow_name!r}" if workflow_name else ""
        )
        return HarvestedSuite(refusal=str(exc))

    from personalclaw.evals import scenarios

    cases: list[StudyCase] = []
    pins: list[str] = []
    for scenario in raw:
        block = scenario.get("harvest") or {}
        name = str(scenario.get("name") or "")
        if not name:  # pragma: no cover - install_library refuses an unnamed case
            continue
        inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
        cases.append(
            StudyCase(
                case_id=name,
                goal=str(scenario.get("judge_criteria") or ""),
                case_input=studies.canonical_json(inputs),
            )
        )
        pins.append(f"{name}@{scenarios.sha256_of_scenario_data(scenario)[:12]}")
    return HarvestedSuite(cases=tuple(cases), input_pins=tuple(pins))


# ── 2. the arm prompt ────────────────────────────────────────────────────────


def _prompt_strings(spec: Any) -> list[str]:
    """Every :data:`ARM_PROMPT_FIELDS` string in a spec, in document order.

    A generic descent rather than a walk over ``models.Node``: a template body carrying an
    unexpanded ``macro`` node has no ``kind``, and ``Node.from_dict`` refuses it — which
    would make the arm for a perfectly legal body render to nothing at all.
    """
    out: list[str] = []
    if isinstance(spec, dict):
        for key, value in spec.items():
            if key in ARM_PROMPT_FIELDS and isinstance(value, str) and value.strip():
                out.append(value)
            else:
                out.extend(_prompt_strings(value))
    elif isinstance(spec, list):
        for item in spec:
            out.extend(_prompt_strings(item))
    return out


def render_arm_prompt(template_body: str, *, case_input: str) -> str:
    """One arm's executable prompt: ``template_body`` bound to the case's recorded inputs.

    Bindings resolve through the engine's own resolver, so ``{{inputs.target}}`` means here
    exactly what it means in a run. An unresolvable reference is left as its literal text
    rather than raising: a template body whose diff introduced a typo'd reference is a
    LEGITIMATE arm to measure — refusing it would quietly exclude the failure mode a study
    is best placed to catch.
    """
    from personalclaw.workflows.bindings import BindingContext, BindingError, resolve

    try:
        inputs = json.loads(case_input) if case_input.strip() else {}
    except ValueError:
        inputs = {}
    if not isinstance(inputs, dict):
        inputs = {}

    try:
        spec = json.loads(template_body)
    except ValueError:
        spec = None

    parts = _prompt_strings(spec) if spec is not None else [template_body]
    if not parts:
        # A parseable spec with no prompt field at all — use the body verbatim rather than
        # render an empty arm. An empty arm prompt would produce an empty output that the
        # judge would score as a bad answer instead of as a body it could not execute.
        parts = [template_body]

    ctx = BindingContext(inputs=inputs)
    rendered: list[str] = []
    for part in parts:
        try:
            rendered.append(str(resolve(part, ctx)))
        except (BindingError, ValueError, KeyError, TypeError):
            rendered.append(part)
    return "\n\n".join(rendered).strip()


def arm_bodies_for_ops(spec: dict, ops: Sequence[dict]) -> tuple[str, str]:
    """``(old_body, new_body)`` for a template diff — the OLD spec and the ops applied.

    Applies through ``workflows.mutations.apply_batch`` (the same function the human accept
    path uses) on a copy, and WRITES NOTHING: a study measures a candidate, and a candidate
    that had to be installed to be measured is not a candidate. A batch with issues raises
    rather than silently measuring the unmodified spec twice — that is the identical-arms
    failure wearing a different hat.
    """
    from personalclaw.workflows import mutations

    try:
        parsed = [mutations.Op.from_dict(o) for o in ops if isinstance(o, dict)]
    except ValueError as exc:
        raise StudyArmError(f"unparseable template op: {exc}") from exc
    if not parsed:
        raise StudyArmError("a template study needs at least one op to define its NEW arm")
    candidate, issues = mutations.apply_batch(parsed, spec, {})
    if issues:
        raise StudyArmError("the NEW arm could not be built: " + "; ".join(i.code for i in issues))
    return studies.canonical_json(spec), studies.canonical_json(candidate)


# ── 3. the production ArmRunner ──────────────────────────────────────────────


def arm_workspace(study_id: str, *, case_id: str, trial: int, arm: str) -> Path:
    """This arm run's OWN output workspace, created if absent.

    Per (case, trial, arm) so the two arms of a pair never share a tree — a shared workspace
    would make every `locked/` check report the same outcome for both arms, which reads as
    "the checks found nothing" and is really "the checks measured one arm twice".
    """
    slug = "".join(c if c.isalnum() or c in "-_." else "-" for c in case_id)[:80]
    path = store.study_dir(study_id) / "arms" / f"{slug}-t{trial}-{arm}"
    path.mkdir(parents=True, exist_ok=True)
    return path


class TemplateArmRunner:
    """The production :data:`~personalclaw.evals.studies.ArmRunner`.

    One model call per arm run, on the arm's rendered prompt, in the arm's own workspace.
    Injectable pieces (``completion``, ``timeout_secs``, ``use_case``) so the whole driver
    is exercisable without spending money, exactly as ``JudgeCaller`` is.

    Never raises across the boundary. Every failure becomes ``ArmOutput(ok=False, detail=…)``
    with an EMPTY output, because the alternative — an exception mid-study — abandons the
    pre-registration with arms half-run, and a fabricated empty output would be judged as a
    bad answer rather than counted as a missing one.
    """

    def __init__(
        self,
        *,
        completion: Any = None,
        use_case: str = ARM_USE_CASE,
        timeout_secs: float = DEFAULT_ARM_TIMEOUT_SECS,
    ) -> None:
        self.completion = completion
        self.use_case = use_case
        self.timeout_secs = timeout_secs

    async def __call__(self, payload: WorkerPayload) -> ArmOutput:
        import asyncio

        from personalclaw.ledger import redact

        workspace = arm_workspace(
            payload.study_id, case_id=payload.case_id, trial=payload.trial, arm=payload.arm
        )
        prompt = render_arm_prompt(payload.template_body, case_input=payload.case_input)
        if not prompt:
            return ArmOutput(
                output="",
                workspace=str(workspace),
                ok=False,
                detail="the arm's template body rendered no prompt",
            )

        completion = self.completion or _one_shot_completion
        started_ts = time.time()
        started = time.monotonic()
        try:
            text = await asyncio.wait_for(
                completion(prompt, use_case=self.use_case), timeout=self.timeout_secs
            )
        except asyncio.TimeoutError:
            return ArmOutput(
                output="",
                workspace=str(workspace),
                wall_secs=time.monotonic() - started,
                ok=False,
                detail=f"the arm did not finish within {self.timeout_secs:.0f}s",
            )
        except Exception as exc:  # noqa: BLE001 - a failed arm is a missing measurement
            logger.warning("study arm %s/%s failed", payload.case_id, payload.arm, exc_info=True)
            return ArmOutput(
                output="",
                workspace=str(workspace),
                wall_secs=time.monotonic() - started,
                ok=False,
                detail=f"the arm call failed: {exc}",
            )
        elapsed = time.monotonic() - started

        # Screened ONCE, here, at the point the model's text enters. Everything downstream
        # (the judge prompt, the workspace file) is composed FROM this value and is never
        # screened again — a second pass over a composed `key: value` line is what garbles
        # the field name (see `harvest._screen`).
        output = str(redact(str(text or "")))
        try:
            (workspace / ARM_OUTPUT_FILENAME).write_text(output, encoding="utf-8")
        except OSError:
            logger.debug("study arm could not write its output file", exc_info=True)

        cost, _model = _arm_cost_since(started_ts, self.use_case)
        return ArmOutput(
            output=output,
            workspace=str(workspace),
            wall_secs=elapsed,
            cost_usd=cost,
            ok=bool(output.strip()),
            detail="" if output.strip() else "the arm returned no text",
        )


async def _one_shot_completion(prompt: str, *, use_case: str) -> str:
    """The real model call, behind one name so the runner's default is nameable."""
    from personalclaw.llm_helpers import one_shot_completion

    return await one_shot_completion(prompt, use_case=use_case)


def _arm_cost_since(started_ts: float, use_case: str) -> tuple[float | None, str]:
    """``(cost_usd, model)`` from the guard's attempt audit.

    Reuses ES-4's derivation rather than re-deriving it: two answers to "what did this model
    call cost" is one answer too many, and the judge-bench version already encodes the
    subtlety that a total of exactly 0.0 is an honest unknown, not a free call.
    """
    from personalclaw.evals.judge_bench import _audit_cost_since

    return _audit_cost_since(started_ts, use_case)


#: The module-level default, so ``run_study`` can fall back to a production runner the way
#: it already falls back to ``live_judge_caller``. The asymmetry it removes was the whole of
#: ES-5's "no template arm can execute".
live_arm_runner = TemplateArmRunner()


# ── 4. the vacuity gate and the spend preflight ──────────────────────────────


def arms_differ_count(
    cases: Sequence[StudyCase], *, old_template_body: str, new_template_body: str
) -> int:
    """How many cases render a DIFFERENT prompt for OLD than for NEW."""
    differing = 0
    for case in cases:
        old = render_arm_prompt(old_template_body, case_input=case.case_input)
        new = render_arm_prompt(new_template_body, case_input=case.case_input)
        if old != new:
            differing += 1
    return differing


def assert_arms_differ(
    cases: Sequence[StudyCase], *, old_template_body: str, new_template_body: str
) -> int:
    """Refuse a study whose two arms execute the same prompt for every case.

    Returns the differing-case count so a caller can report it. Raises
    :class:`StudyArmError` when it is zero AND there was at least one case to measure — an
    empty case list is a low-power suite, which is a different (and already labelled)
    statement, so refusing it here would report the wrong problem.
    """
    if not cases:
        return 0
    differing = arms_differ_count(
        cases, old_template_body=old_template_body, new_template_body=new_template_body
    )
    if differing == 0:
        raise StudyArmError(
            "this study's two arms render an IDENTICAL prompt for every case, so it cannot "
            "measure anything — and it would not fail, it would report a confident `tie`. "
            "Check that the NEW arm's ops actually change a prompt field."
        )
    return differing


@dataclass(frozen=True)
class StudyPreflight:
    """What a study would spend, before it spends it.

    Modelled on ``judge-bench``'s preflight for the same reason: the shipped shape is
    ``cases x k x 2`` arm calls PLUS ``cases x k x 2 x samples`` judge calls, which is a
    three-digit number for a suite of ten, and a user who sees it can narrow the run.
    """

    cases: int
    k: int
    samples: int
    differing_cases: int
    low_power: bool
    refusal: str = ""

    @property
    def arm_calls(self) -> int:
        return self.cases * self.k * 2

    @property
    def judge_calls(self) -> int:
        # Every pair is judged in BOTH positions (§2.3's position swap), each position
        # sampled `samples` times. Counting one position would understate the spend by half.
        return self.cases * self.k * 2 * self.samples

    def render(self) -> str:
        if self.refusal:
            return f"Refusing: {self.refusal}"
        lines = [
            f"  cases:   {self.cases}"
            + (f" ({self.differing_cases} with differing arms)" if self.cases else ""),
            f"  k:       {self.k}",
            f"  samples: {self.samples}",
            f"  model calls (the spend): {self.arm_calls} arm + {self.judge_calls} judge",
        ]
        if self.low_power:
            lines.append(
                f"  low_power: fewer than {LOW_POWER_CASES} cases — the verdict will be "
                "labelled, not suppressed"
            )
        return "\n".join(lines)


def preflight(
    reg: StudyRegistration,
    *,
    cases: Sequence[StudyCase],
    old_template_body: str,
    new_template_body: str,
    samples: int = DEFAULT_JUDGE_SAMPLES,
    refusal: str = "",
) -> StudyPreflight:
    """The spend preview. Never calls a model and never raises."""
    try:
        differing = arms_differ_count(
            cases, old_template_body=old_template_body, new_template_body=new_template_body
        )
    except Exception:  # noqa: BLE001 - a preview must not be the thing that fails
        logger.debug("study preflight could not render the arms", exc_info=True)
        differing = 0
    return StudyPreflight(
        cases=len(cases),
        k=reg.k,
        samples=samples,
        differing_cases=differing,
        low_power=len(cases) < LOW_POWER_CASES,
        refusal=refusal,
    )


# ── the orchestration: register, then (deliberately, separately) run ─────────


def register_template_study(
    *,
    workflow_name: str,
    hypothesis: str,
    suite: HarvestedSuite | None = None,
    proposal_id: str = "",
    old_version: int = 0,
    new_version: int = 0,
    locked_checks: Sequence[dict] = (),
    budget_usd: float = 0.0,
) -> StudyRegistration:
    """Pre-register a template A/B over the harvested suite. Spends nothing.

    Registration is free and MUST precede arm 1 — that is the whole of §2.1 — so this is
    what the flywheel's filing path calls. Running is a separate, deliberate invocation with
    its own spend preflight (``personalclaw study --run``), because an agent tool call that
    silently started a three-digit model-call matrix is the exact thing the preflight exists
    to prevent.

    The suite's refusal is recorded ON the registration's subject rather than dropped: a
    study registered over an empty population is a legitimate artifact, and one that hid why
    it was empty is not.
    """
    resolved = suite if suite is not None else harvested_study_cases(workflow_name=workflow_name)
    subject: dict[str, Any] = {
        "template_id": workflow_name,
        "diff_proposal_id": proposal_id,
        "old_version": old_version,
        "new_version": new_version,
        "corpus": "harvested",
        "corpus_population": resolved.population,
        "low_power_at_registration": resolved.low_power,
    }
    if resolved.refusal:
        subject["corpus_refusal"] = resolved.refusal
    return studies.register_study(
        subject=subject,
        hypothesis=hypothesis,
        inputs=resolved.input_pins or ("harvested-suite:empty",),
        rubric_text=TEMPLATE_AB_RUBRIC,
        locked_checks=locked_checks,
        budget_usd=budget_usd,
    )


async def run_registered_study(
    study_id: str,
    *,
    old_template_body: str = "",
    new_template_body: str = "",
    samples: int = DEFAULT_JUDGE_SAMPLES,
    arm_runner: Any = None,
    caller: Any = None,
) -> StudyResult:
    """Run an already-registered study end to end over its harvested suite.

    The registration is the source of truth for the corpus and the arms: the subject names
    the template and the proposal, so a run cannot quietly substitute a different template
    than the one that was pre-registered. Bodies may be passed in (a caller that already
    holds them), otherwise they are derived from the named proposal's ops.
    """
    raw = store.read_study_registration(study_id)
    if raw is None:
        raise StudyArmError(f"no registered study {study_id!r}")
    reg = studies.registration_from_dict(raw)
    workflow_name = str(reg.subject.get("template_id") or "")

    if not (old_template_body and new_template_body):
        old_template_body, new_template_body = await arm_bodies_for_study(reg)

    suite = harvested_study_cases(workflow_name=workflow_name)
    assert_arms_differ(
        suite.cases, old_template_body=old_template_body, new_template_body=new_template_body
    )
    return await studies.run_study(
        reg,
        cases=suite.cases,
        old_template_body=old_template_body,
        new_template_body=new_template_body,
        arm_runner=arm_runner,
        live_rubric_text=TEMPLATE_AB_RUBRIC,
        caller=caller,
        samples=samples,
    )


async def arm_bodies_for_study(reg: StudyRegistration) -> tuple[str, str]:
    """``(old_body, new_body)`` for a registered study, from its own subject.

    Reads the live template and the proposal's typed ops. Both are refusals rather than
    fallbacks: measuring a template that no longer exists, or a proposal whose ops are gone,
    would produce two identical arms and a confident tie.
    """
    workflow_name = str(reg.subject.get("template_id") or "")
    proposal_id = str(reg.subject.get("diff_proposal_id") or "")
    if not workflow_name:
        raise StudyArmError(f"study {reg.study_id} names no template to measure")
    spec = await _live_spec(workflow_name)
    if spec is None:
        raise StudyArmError(f"no workflow definition named {workflow_name!r}")
    ops = _proposal_ops(proposal_id)
    if not ops:
        raise StudyArmError(
            f"study {reg.study_id} has no typed ops to build its NEW arm from "
            f"(proposal {proposal_id!r})"
        )
    return arm_bodies_for_ops(spec, ops)


async def _live_spec(workflow_name: str) -> dict | None:
    """The stored spec for a template, across every registered def provider.

    Registers the native and bundled providers first, idempotently. Without this the CLI
    surface is INERT and says so in the wrong words: nothing registers a workflow def
    provider outside the gateway boot, so ``personalclaw study --run`` refused every study
    with "no workflow definition named X" while the definition sat on disk. Found by driving
    the command, not by reading it — the in-process drive registered a provider itself and
    could never have seen it.
    """
    from personalclaw.workflows import defs as defs_mod
    from personalclaw.workflows.bundled_defs import register_bundled_provider
    from personalclaw.workflows.native_defs import register_native_provider

    register_native_provider()
    register_bundled_provider()
    for name in defs_mod.list_providers():
        provider = defs_mod.get_provider(name)
        if provider is None:  # pragma: no cover - registry mutated mid-iteration
            continue
        try:
            found = await provider.get_def(workflow_name)
        except Exception:  # noqa: BLE001 - one bad provider must not hide the others
            logger.debug("study: provider %s could not read %r", name, workflow_name, exc_info=True)
            continue
        if found is None:
            continue
        if isinstance(found, dict):
            return found
        to_dict = getattr(found, "to_dict", None)
        if callable(to_dict):
            return dict(to_dict())
    return None


def _proposal_ops(proposal_id: str) -> list[dict]:
    """The typed ops on a filed template_diff proposal, or ``[]``."""
    if not proposal_id:
        return []
    try:
        from personalclaw.learning import proposals

        prop = proposals.get(proposal_id)
    except Exception:  # noqa: BLE001 - an unreadable queue is a refusal upstream
        logger.debug("study: could not read proposal %r", proposal_id, exc_info=True)
        return []
    if prop is None:
        return []
    manifest = getattr(prop, "change_manifest", None)
    ops = getattr(manifest, "targeted_fix", None)
    if ops is None and isinstance(manifest, dict):
        ops = manifest.get("targeted_fix")
    return [o for o in ops if isinstance(o, dict)] if isinstance(ops, list) else []
