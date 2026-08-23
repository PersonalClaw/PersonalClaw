"""Pre-registered template A/B studies — EVALUATION-SUBSTRATE §2 (ES-5).

The formal instrument for "is template v(N+1) actually better than v(N)?". Every clause
below exists because a cheaper version of it can be gamed, so the module is organized
around the four defeats rather than around the happy path:

1. **§2.1 immutable pre-registration.** The design is fixed before arm 1 runs, and
   `store.write_study_registration` refuses a second write. The load-bearing part is not
   the file mode — it is that the RUBRIC is pinned by hash: :func:`rubric_status` compares
   the live rubric AND the on-disk pinned copy against `rubric_sha256`, and either mismatch
   yields verdict ``invalidated``. A study whose rubric can be edited after the results are
   in is a study that was never registered, only narrated.

2. **§2.3 blinded, position-swapped, median-of-3.** Three separate biases, three separate
   mechanisms. Blinding: :func:`render_pair_prompt` is given the rubric and two outputs and
   *nothing else*, and :func:`assert_blinded` asserts the negative over the registration's
   own identifying strings. Position swap: EVERY pair is judged twice with the slots
   exchanged, and a pair whose winner flips is `no_signal` — not a win for whoever sat in
   slot A. Median-of-3: each presentation is sampled `DEFAULT_JUDGE_SAMPLES` times and the
   ordinal median decides, so one eccentric sample cannot carry a pair.

3. **§2.3 agreement floor.** A median-of-3 with no floor launders disagreement into a
   verdict. Below `evals.judge_agreement_floor` the study's verdict is `judge_unreliable`
   and it files a judge-calibration item instead of a template verdict.

4. 🔴 **§2.2 `locked/` checks are supervisor-side and never worker-visible.** A check whose
   text reaches the worker is a check the worker satisfies by construction, which is the
   same as not having it. So the checks run HERE, in the arm's own output workspace, after
   the run finishes — and :func:`assert_no_locked_leakage` asserts the NEGATIVE over every
   worker-visible string before a single arm is spawned. That guard REFUSES to pass
   vacuously: no locked tokens to look for, or no non-empty strings offered to scan, is
   itself an error, because a negative assertion over an empty set is not an assertion.
   :class:`WorkerPayload` derives its scanned surface from its own dataclass fields, so a
   field added later is scanned by DEFAULT — forgetting to declare it is safe rather than a
   silent hole.

Nothing here is a new verdict dialect. The engine's `judge_contract.Verdict` answers "did
this work meet its definition of done" over PASS/REJECT/RETRY/…; a study answers "is B
better than A", which that vocabulary cannot express, so the two do not overlap and neither
is translated into the other. What IS reused verbatim: `DEFAULT_JUDGE_SAMPLES` (the sample
count), `parse_judge_json` (JSON extraction and its reject-by-default posture), ES-4's
`JudgeCall`/`live_judge_caller` seam (the one place a judge costs money), and
`loop.gates.run_verify_command` (the screened tristate every locked command runs through,
so a check that cannot run reports `verifier_absent` and never a silent pass).
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Awaitable, Callable, Sequence

from personalclaw.evals import store
from personalclaw.evals.judge_bench import JudgeCall, JudgeCaller, live_judge_caller
from personalclaw.evals.matrix import FAILED, PASSED, VERIFIER_ABSENT
from personalclaw.workflows.judge_contract import DEFAULT_JUDGE_SAMPLES, parse_judge_json

logger = logging.getLogger(__name__)


# ── vocabulary ───────────────────────────────────────────────────────────────

#: The only study kind ES-5 ships. Carried on the ledger row's ``kind`` column so a reader
#: can tell a study row from a matrix row without opening an artifact.
KIND_TEMPLATE_AB = "template_ab"

ARM_OLD = "old"
ARM_NEW = "new"
ARMS = (ARM_OLD, ARM_NEW)

SLOT_A = "A"
SLOT_B = "B"

#: The two presentations of one pair. `SWAPPED` exchanges the slots; comparing the two is
#: the position-bias measurement, and it is per-pair rather than per-study because position
#: bias is a property of the pair's content, not of the run.
PRESENTATION_DIRECT = "direct"
PRESENTATION_SWAPPED = "swapped"
PRESENTATIONS = (PRESENTATION_DIRECT, PRESENTATION_SWAPPED)

#: What one presentation's median said, expressed in ARM terms. `TIE` is a real answer;
#: `CANNOT_JUDGE` is the absence of one, and the difference matters for the agreement rate
#: (see :func:`agreement_rate`).
WINNER_TIE = "tie"
WINNER_CANNOT_JUDGE = "cannot_judge"

#: What one pair, or one case, resolved to. `NO_SIGNAL` covers a position flip and a
#: judge that declined — both are "we learned nothing here", and neither is a tie.
OUTCOME_TIE = "tie"
OUTCOME_NO_SIGNAL = "no_signal"

VERDICT_WIN = "win"
VERDICT_LOSS = "loss"
VERDICT_TIE = "tie"
VERDICT_INVALIDATED = "invalidated"
VERDICT_JUDGE_UNRELIABLE = "judge_unreliable"
VERDICTS = (
    VERDICT_WIN,
    VERDICT_LOSS,
    VERDICT_TIE,
    VERDICT_INVALIDATED,
    VERDICT_JUDGE_UNRELIABLE,
)

#: Why a `loss` was a loss, when it was not the win rate. §2.1's decision rule reads "ANY
#: locked-check regression = fail regardless", so this reason outranks the metric.
FAIL_LOCKED_REGRESSION = "locked_check_regression"

#: Below this many DECIDED cases a study is labeled low-power (§2.1's "honestly-labeled
#: low-power study" for a template whose harvested suite is nearly empty). It is a LABEL,
#: never a verdict change: silently upgrading a 1-case study to "inconclusive" would hide
#: that the study ran, and silently reporting it as a win would be the lie.
LOW_POWER_CASES = 3

#: A locked token shorter than this cannot be told apart from incidental worker text, so
#: :func:`parse_locked_check` REFUSES it rather than letting the leak guard skip it. A
#: guard with a documented blind spot is worse than no guard, because it is trusted.
MIN_LEAK_TOKEN_LEN = 4

#: Default per-arm run count. Overridden by ``evals.study_default_k``.
DEFAULT_K = 5


class StudyError(ValueError):
    """A study could not be registered or its inputs are not interpretable."""


class LockedLeakError(RuntimeError):
    """🔴 §2.2 violated, or the guard could not enforce it.

    Both cases are this error on purpose. "A locked token reached worker-visible text" and
    "the guard had nothing to check, so its silence meant nothing" are the same failure of
    the same promise, and giving the vacuous case a softer type is how a guard becomes
    decorative.
    """


# ── §2.2 the locked check DSL ────────────────────────────────────────────────


@dataclass(frozen=True)
class LockedCheck:
    """One hidden validation check — MetaHarness's weighted `command`/`file_phrase` pair.

    Exactly one shape per check: a ``command`` with an ``expect_exit_code``, or a ``path``
    with ``required_phrases``. A check carrying both would have two truths and no way to
    report which one failed.
    """

    id: str
    weight: float = 1.0
    command: str = ""
    expect_exit_code: int = 0
    path: str = ""
    required_phrases: tuple[str, ...] = ()

    @property
    def is_command(self) -> bool:
        return bool(self.command)

    def to_dict(self) -> dict:
        out: dict[str, object] = {"id": self.id, "weight": self.weight}
        if self.command:
            out["command"] = self.command
            out["expect_exit_code"] = self.expect_exit_code
        else:
            out["path"] = self.path
            out["required_phrases"] = list(self.required_phrases)
        return out

    def leak_tokens(self) -> tuple[str, ...]:
        """Every substring of this check whose presence in worker text is a leak.

        The check ``id`` is in here as well as its content: an id like
        ``no_pytest_skip_added`` describes the check well enough to satisfy it.
        """
        raw = [self.id, self.command, self.path, *self.required_phrases]
        return tuple(sorted({t for t in raw if t}))


def parse_locked_check(raw: object) -> LockedCheck:
    """Parse one locked check, refusing anything the leak guard could not enforce.

    Two refusals matter more than the shape validation around them:

    * a token shorter than :data:`MIN_LEAK_TOKEN_LEN` — the guard cannot distinguish it
      from incidental prose, and skipping it quietly would leave §2.2 unenforced for
      exactly the checks whose authors were terse;
    * both shapes at once, or neither.
    """
    if not isinstance(raw, dict):
        raise StudyError(f"a locked check must be a JSON object, got {type(raw).__name__}")
    check_id = str(raw.get("id") or "").strip()
    if not check_id:
        raise StudyError("a locked check needs an `id`")
    command = str(raw.get("command") or "").strip()
    path = str(raw.get("path") or "").strip()
    phrases = raw.get("required_phrases") or []
    if not isinstance(phrases, (list, tuple)):
        raise StudyError(f"locked check {check_id!r}: `required_phrases` must be a list")
    phrase_tuple = tuple(str(p) for p in phrases if str(p).strip())
    if command and (path or phrase_tuple):
        raise StudyError(
            f"locked check {check_id!r} declares both a `command` and a file/phrase shape — "
            "one check, one truth: split it into two"
        )
    if not command:
        if not path:
            raise StudyError(
                f"locked check {check_id!r} declares neither a `command` nor a `path` — "
                "there is nothing for the supervisor to execute"
            )
        if not phrase_tuple:
            raise StudyError(
                f"locked check {check_id!r} declares a `path` with no `required_phrases` — "
                "a check that asserts nothing passes for every output"
            )
    try:
        weight = float(raw.get("weight", 1.0))
    except (TypeError, ValueError) as exc:
        raise StudyError(f"locked check {check_id!r}: `weight` must be a number") from exc
    try:
        expect = int(raw.get("expect_exit_code", 0))
    except (TypeError, ValueError) as exc:
        raise StudyError(f"locked check {check_id!r}: `expect_exit_code` must be an int") from exc
    check = LockedCheck(
        id=check_id,
        weight=weight,
        command=command,
        expect_exit_code=expect,
        path=path,
        required_phrases=phrase_tuple,
    )
    short = [t for t in check.leak_tokens() if len(t) < MIN_LEAK_TOKEN_LEN]
    if short:
        raise StudyError(
            f"locked check {check_id!r} carries token(s) shorter than {MIN_LEAK_TOKEN_LEN} "
            f"characters ({', '.join(repr(t) for t in short)}): the §2.2 leak guard cannot "
            "tell them from incidental worker text, so the check is refused rather than "
            "silently left unguarded"
        )
    return check


def load_locked_checks(study_id: str) -> tuple[LockedCheck, ...]:
    """Every parsed locked check for a study, in filename order."""
    return tuple(parse_locked_check(raw) for raw in store.read_locked_checks(study_id))


# ── §2.1 pre-registration ────────────────────────────────────────────────────


def canonical_json(data: object) -> str:
    """Key-sorted, separator-fixed JSON — the only form that gets hashed."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(text: str) -> str:
    """``sha256`` of ``text``. One function so a pin and its check never differ by encoding."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rubric_sha256(rubric_text: str) -> str:
    """The pinned rubric hash. Named separately because it is the thing §2.3 invalidates on."""
    return digest(rubric_text)


def new_study_id() -> str:
    """A fresh ``st-xxxxxxxx`` id (the §2.1 shape)."""
    return f"st-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class StudyRegistration:
    """The §2.1 pre-registration — fixed before arm 1, hashed, and never rewritten."""

    study_id: str
    subject: dict
    hypothesis: str
    inputs: tuple[str, ...]
    k: int
    rubric_sha256: str
    locked_checks: tuple[str, ...] = ()
    kind: str = KIND_TEMPLATE_AB
    metric: str = "primary: rubric median (pinned); guard: wall_secs, cost_usd, attention_events"
    decision_rule: str = (
        "win_rate > 0.5 over decided cases; ANY locked-check regression = fail regardless"
    )
    model_fingerprint: dict = field(default_factory=dict)
    budget_usd: float = 0.0
    agreement_floor: float = 0.6
    registered_ts: float = 0.0

    def to_dict(self) -> dict:
        return {
            "study_id": self.study_id,
            "kind": self.kind,
            "subject": dict(self.subject),
            "hypothesis": self.hypothesis,
            "inputs": list(self.inputs),
            "k": self.k,
            "metric": self.metric,
            "rubric_sha256": self.rubric_sha256,
            "locked_checks": list(self.locked_checks),
            "decision_rule": self.decision_rule,
            "model_fingerprint": dict(self.model_fingerprint),
            "budget_usd": self.budget_usd,
            "agreement_floor": self.agreement_floor,
            "registered_ts": self.registered_ts,
        }

    def sha256(self) -> str:
        """The registration's own hash — the study's identity in the results ledger."""
        return digest(canonical_json(self.to_dict()))

    def blinding_leak_tokens(self) -> tuple[str, ...]:
        """Strings whose appearance in a judge prompt would un-blind it (§2.3).

        The hypothesis heads the list: a judge told what the experimenter expects is a
        judge asked to confirm it. Version tokens are rendered in the several spellings a
        template body might carry them in, because ``v8`` and ``version 8`` un-blind
        equally well.
        """
        raw: list[str] = [self.hypothesis, self.study_id]
        for key in ("template_id", "diff_proposal_id"):
            value = str(self.subject.get(key) or "").strip()
            if value:
                raw.append(value)
        for key in ("old_version", "new_version"):
            version = self.subject.get(key)
            if version in (None, ""):
                continue
            raw.extend([f"v{version}", f"version {version}", f"version={version}"])
        return tuple(sorted({t.strip() for t in raw if len(t.strip()) >= MIN_LEAK_TOKEN_LEN}))


def registration_from_dict(data: object) -> StudyRegistration:
    """Rehydrate a registration read back off disk."""
    if not isinstance(data, dict):
        raise StudyError("a registration must be a JSON object")
    try:
        return StudyRegistration(
            study_id=str(data["study_id"]),
            subject=dict(data.get("subject") or {}),
            hypothesis=str(data.get("hypothesis") or ""),
            inputs=tuple(str(i) for i in (data.get("inputs") or ())),
            k=int(data["k"]),
            rubric_sha256=str(data["rubric_sha256"]),
            locked_checks=tuple(str(c) for c in (data.get("locked_checks") or ())),
            kind=str(data.get("kind") or KIND_TEMPLATE_AB),
            metric=str(data.get("metric") or ""),
            decision_rule=str(data.get("decision_rule") or ""),
            model_fingerprint=dict(data.get("model_fingerprint") or {}),
            budget_usd=float(data.get("budget_usd") or 0.0),
            agreement_floor=float(data.get("agreement_floor") or 0.6),
            registered_ts=float(data.get("registered_ts") or 0.0),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StudyError(f"unreadable registration: {exc}") from exc


def register_study(
    *,
    subject: dict,
    hypothesis: str,
    inputs: Sequence[str],
    rubric_text: str,
    locked_checks: Sequence[dict] = (),
    study_id: str = "",
    k: int = 0,
    budget_usd: float = 0.0,
    agreement_floor: float = 0.0,
) -> StudyRegistration:
    """Register a study: write ``registration.json`` + the pinned rubric + ``locked/``, once.

    Raises :class:`~personalclaw.evals.store.StudySealedError` for an already-registered
    id. Every locked check is parsed BEFORE anything is written, so a study is never left
    half-registered with a check the leak guard would have refused — the alternative is a
    sealed, immutable, unusable registration.

    ``k`` and ``agreement_floor`` default from ``EvalsConfig`` rather than from literals
    here, so the shipped defaults live in one place and a user who raised k gets it.
    """
    k_final, floor_final = _config_defaults()
    reg = StudyRegistration(
        study_id=study_id or new_study_id(),
        subject=dict(subject),
        hypothesis=str(hypothesis),
        inputs=tuple(str(i) for i in inputs),
        k=int(k or k_final),
        rubric_sha256=rubric_sha256(rubric_text),
        locked_checks=tuple(f"locked/{parse_locked_check(c).id}.json" for c in locked_checks),
        model_fingerprint=_model_fingerprint(),
        budget_usd=float(budget_usd),
        agreement_floor=float(agreement_floor or floor_final),
        registered_ts=time.time(),
    )
    if reg.k < 1:
        raise StudyError(f"k must be at least 1, got {reg.k}")
    parsed = [parse_locked_check(c) for c in locked_checks]
    store.write_study_registration(reg.study_id, reg.to_dict(), rubric_text=rubric_text)
    for check in parsed:
        store.write_locked_check(reg.study_id, check.id, check.to_dict())
    return reg


def _config_defaults() -> tuple[int, float]:
    """``(study_default_k, judge_agreement_floor)`` from config, with shipped fallbacks."""
    try:
        from personalclaw.config.loader import AppConfig

        evals = AppConfig.load().evals
        return int(evals.study_default_k), float(evals.judge_agreement_floor)
    except Exception:  # noqa: BLE001 - an unreadable config must not block a registration
        logger.debug("study defaults: config unreadable, using shipped values", exc_info=True)
        return DEFAULT_K, 0.6


def _model_fingerprint() -> dict:
    try:
        from personalclaw.evals.pinning import model_fingerprint

        return model_fingerprint()
    except Exception:  # noqa: BLE001
        logger.debug("study registration could not read model bindings", exc_info=True)
        return {}


# ── §2.3 the rubric pin, and what breaks it ──────────────────────────────────

RUBRIC_OK = "ok"
RUBRIC_LIVE_EDITED = "live_rubric_edited"
RUBRIC_PIN_TAMPERED = "pinned_rubric_tampered"
RUBRIC_PIN_MISSING = "pinned_rubric_missing"


def rubric_status(reg: StudyRegistration, live_rubric_text: str | None) -> tuple[str, str]:
    """``(status, detail)`` — is this study still interpretable against its pinned rubric?

    Checks BOTH directions, because a mid-study rubric edit can arrive from either side:

    * the LIVE rubric (the template's current one) no longer hashes to ``rubric_sha256`` —
      somebody edited the rubric while the study was open;
    * the PINNED copy on disk no longer hashes to ``rubric_sha256`` — somebody edited the
      study's own frozen copy.

    Either is ``invalidated``. Both are decided on the HASH, never on an mtime: a
    timestamp says when a file was touched, which is not the same question, and a
    touch-without-change would invalidate a perfectly good study.

    A missing pinned copy is also invalidation, not a shrug: the judge prompt renders from
    the pinned text, so a study without it cannot be re-run reproducibly.
    """
    pinned = store.read_study_rubric(reg.study_id)
    if pinned is None:
        return RUBRIC_PIN_MISSING, "the study's pinned rubric copy is gone"
    pinned_hash = rubric_sha256(pinned)
    if pinned_hash != reg.rubric_sha256:
        return (
            RUBRIC_PIN_TAMPERED,
            f"pinned rubric hashes to {pinned_hash[:12]}, registration pinned "
            f"{reg.rubric_sha256[:12]}",
        )
    if live_rubric_text is None:
        return RUBRIC_OK, ""
    live_hash = rubric_sha256(live_rubric_text)
    if live_hash != reg.rubric_sha256:
        return (
            RUBRIC_LIVE_EDITED,
            f"live rubric hashes to {live_hash[:12]}, registration pinned "
            f"{reg.rubric_sha256[:12]}",
        )
    return RUBRIC_OK, ""


# ── 🔴 §2.2 the worker-visible surface, and the guard over it ────────────────

#: Fields on :class:`WorkerPayload` that are supervisor bookkeeping and never rendered to
#: a worker. EVERYTHING ELSE is scanned by :func:`assert_no_locked_leakage`, so a field
#: added to the payload later is scanned by default. That direction is deliberate: the
#: failure mode of an allowlist is a new field nobody remembered to guard, and the failure
#: mode of this denylist is a false positive that a developer immediately notices.
SUPERVISOR_ONLY_FIELDS = frozenset({"study_id", "case_id", "arm", "trial", "workspace"})


@dataclass(frozen=True)
class WorkerPayload:
    """Everything one arm's worker session is given. Locked content is unreachable here.

    "Unreachable" is structural, not a promise: this dataclass has no reference to the
    study's ``locked/`` directory, and :meth:`worker_visible` enumerates its own text
    fields so the guard's coverage cannot drift away from the payload's shape.
    """

    study_id: str
    case_id: str
    arm: str
    trial: int
    template_body: str
    case_input: str
    workspace: str = ""

    def worker_visible(self) -> tuple[str, ...]:
        """The strings a worker can read — every text field bar the supervisor's own.

        Derived from ``dataclasses.fields`` rather than hand-listed, which is what makes
        the guard's coverage a property of the class instead of a comment about it.
        """
        out: list[str] = []
        for f in fields(self):
            if f.name in SUPERVISOR_ONLY_FIELDS:
                continue
            value = getattr(self, f.name)
            if isinstance(value, str):
                out.append(value)
            elif isinstance(value, (list, tuple)):
                out.extend(str(v) for v in value)
        return tuple(out)

    def to_dict(self) -> dict:
        return {
            "study_id": self.study_id,
            "case_id": self.case_id,
            "arm": self.arm,
            "trial": self.trial,
            "workspace": self.workspace,
        }


def locked_leak_tokens(study_id: str) -> tuple[str, ...]:
    """Every substring whose presence in worker-visible text is a §2.2 leak."""
    tokens: set[str] = set()
    for check in load_locked_checks(study_id):
        tokens.update(check.leak_tokens())
    return tuple(sorted(tokens))


def _assert_absent(
    *,
    what: str,
    tokens: Sequence[str],
    scanned: Sequence[str],
    where: str,
) -> None:
    """Assert none of ``tokens`` appears in any of ``scanned``, and REFUSE to do so vacuously.

    The two vacuity refusals are the whole point. A negative assertion over an empty token
    set passes for every input, and so does one over an empty scan set — both would report
    "clean" for a system that leaks everything. This repo's recurring defect is a control
    that exists and never fires; a guard that cannot detect its own violation is that
    defect wearing a security label.
    """
    real_tokens = [t for t in tokens if t]
    if not real_tokens:
        raise LockedLeakError(
            f"{what}: nothing to look for — the guard would have passed vacuously over "
            f"{where}. Either the study has no {what.split()[0]} content (in which case do "
            "not call the guard) or reading it failed."
        )
    real_scanned = [s for s in scanned if s]
    if not real_scanned:
        raise LockedLeakError(
            f"{what}: no non-empty strings were offered for {where}, so a clean result "
            "would mean nothing. Refusing to certify an empty scan."
        )
    hits: list[str] = []
    for token in real_tokens:
        for text in real_scanned:
            if token in text:
                hits.append(token)
                break
    if hits:
        raise LockedLeakError(
            f"{what} VIOLATED in {where}: {', '.join(repr(h) for h in sorted(hits))} "
            f"appear(s) in text the reader can see. A check the worker can read is a check "
            "it satisfies by construction."
        )


def assert_no_locked_leakage(study_id: str, worker_visible: Sequence[str]) -> None:
    """🔴 §2.2: no ``locked/`` content may appear in anything a worker sees.

    Called from :func:`run_study` before ANY arm is spawned, not only from a test — a rail
    that only fires in the suite protects the suite. Raises rather than degrading: a leak
    is a code defect, and a study that ran with leaked checks has no interpretation to
    report, so there is no honest verdict to fall back to.
    """
    _assert_absent(
        what="locked check content",
        tokens=locked_leak_tokens(study_id),
        scanned=worker_visible,
        where=f"the worker-visible payloads of study {study_id}",
    )


def assert_blinded(reg: StudyRegistration, judge_prompts: Sequence[str]) -> None:
    """§2.3: no judge prompt may carry the study's identifying strings.

    Same vacuity discipline as the locked guard, for the same reason.
    """
    _assert_absent(
        what="study-identifying content",
        tokens=reg.blinding_leak_tokens(),
        scanned=judge_prompts,
        where=f"the judge prompts of study {reg.study_id}",
    )


# ── §2.2 supervisor-side execution, in the arm's own output workspace ────────


@dataclass(frozen=True)
class LockedOutcome:
    """One locked check's result for one arm run."""

    case_id: str
    trial: int
    arm: str
    check_id: str
    outcome: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "trial": self.trial,
            "arm": self.arm,
            "check_id": self.check_id,
            "outcome": self.outcome,
            "detail": self.detail,
        }


def _expected_exit_command(command: str, expect: int) -> str:
    """``command`` rewritten so exit 0 means "the expected code", 127 still means "absent".

    A naive ``cmd; test $? -eq N`` wrapper would turn a MISSING binary (127) into a plain
    non-zero exit, which `run_verify_command` reads as a genuine failure — the exact
    misreading its tristate exists to prevent. So 127 is re-raised explicitly before the
    comparison, and an ``expect_exit_code`` of 0 is not wrapped at all.
    """
    if expect == 0:
        return command
    return (
        f"{command}; rc=$?; " 'if [ "$rc" = "127" ]; then exit 127; fi; ' f'[ "$rc" = "{expect}" ]'
    )


def _file_phrase_outcome(check: LockedCheck, workspace: Path) -> tuple[str, str]:
    """Evaluate a ``path``/``required_phrases`` check inside ``workspace``.

    A path that resolves OUTSIDE the workspace is ``verifier_absent``, not a failure: the
    check is misconfigured, and the arm's output is not the thing at fault. A file that is
    simply not there IS a failure — the phrases the check requires are absent, which is
    the finding.
    """
    root = workspace.resolve()
    try:
        target = (root / check.path).resolve()
    except OSError as exc:  # pragma: no cover - resolve on a hostile path
        return VERIFIER_ABSENT, f"could not resolve {check.path!r}: {exc}"
    if root != target and root not in target.parents:
        return (
            VERIFIER_ABSENT,
            f"{check.path!r} resolves outside the arm's output workspace — refusing to read it",
        )
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return FAILED, f"{check.path!r} was not produced"
    except OSError as exc:
        return VERIFIER_ABSENT, f"{check.path!r} could not be read: {exc}"
    missing = [p for p in check.required_phrases if p not in text]
    if missing:
        return FAILED, f"missing phrase(s): {', '.join(repr(m) for m in missing)}"
    return PASSED, ""


async def run_locked_checks(
    study_id: str,
    *,
    workspace: str | Path,
    case_id: str,
    trial: int,
    arm: str,
    checks: Sequence[LockedCheck] | None = None,
) -> tuple[LockedOutcome, ...]:
    """Execute every locked check SUPERVISOR-SIDE, in one arm run's output workspace.

    Nothing is copied into the workspace: the checks are read from
    ``evals/studies/<id>/locked/`` in this process and executed with the workspace as
    ``cwd``. That is the difference between "checked in the output" and "shipped to the
    worker", and it is why :func:`assert_locked_absent_from_workspace` can assert the
    workspace never contained them.

    Every command goes through `loop.gates.run_verify_command`, so it is screened by
    `audit_bash_command` and its tristate is preserved: a check that could not run reports
    ``verifier_absent`` and NEVER a silent pass.
    """
    from personalclaw.loop.gates import run_verify_command

    root = Path(workspace)
    resolved = tuple(checks) if checks is not None else load_locked_checks(study_id)
    out: list[LockedOutcome] = []
    for check in resolved:
        if check.is_command:
            command = _expected_exit_command(check.command, check.expect_exit_code)
            verdict = await run_verify_command(command, str(root), label=f"locked:{check.id}")
            if verdict is None:
                outcome, detail = (
                    VERIFIER_ABSENT,
                    "the check could not run (screened, absent or timed out)",
                )
            elif verdict:
                outcome, detail = PASSED, ""
            else:
                outcome, detail = FAILED, f"exit code was not {check.expect_exit_code}"
        else:
            outcome, detail = _file_phrase_outcome(check, root)
        out.append(
            LockedOutcome(
                case_id=case_id,
                trial=trial,
                arm=arm,
                check_id=check.id,
                outcome=outcome,
                detail=detail,
            )
        )
    return tuple(out)


def assert_locked_absent_from_workspace(study_id: str, workspace: str | Path) -> None:
    """§2.2's other half: the arm's output workspace never held the locked content either.

    §2.2 forbids the checks from a worker's "prompt, bindings, OR workspace", so the prompt
    guard alone is two thirds of the clause. Same vacuity discipline.
    """
    root = Path(workspace)
    texts: list[str] = []
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            try:
                texts.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:  # pragma: no cover - unreadable file in a scratch dir
                continue
            texts.append(str(path.relative_to(root)))
    _assert_absent(
        what="locked check content",
        tokens=locked_leak_tokens(study_id),
        scanned=texts,
        where=f"the output workspace {root}",
    )


def locked_regressions(outcomes: Sequence[LockedOutcome]) -> tuple[str, ...]:
    """Checks that PASSED on OLD and FAILED on NEW for the same case+trial.

    Strictly that pair. A check that was ``verifier_absent`` on OLD is not a baseline, so
    a NEW failure beside it is unknown rather than a regression — counting it would let a
    broken check manufacture a demotion proposal.
    """
    by_key: dict[tuple[str, int, str], dict[str, str]] = {}
    for o in outcomes:
        by_key.setdefault((o.case_id, o.trial, o.check_id), {})[o.arm] = o.outcome
    hits = [
        f"{case_id}/trial{trial}/{check_id}"
        for (case_id, trial, check_id), arms in by_key.items()
        if arms.get(ARM_OLD) == PASSED and arms.get(ARM_NEW) == FAILED
    ]
    return tuple(sorted(hits))


# ── §2.3 blinded, position-swapped, median-of-3 comparative judging ──────────

_PAIR_BODY = """You are comparing two candidate outputs for the same task, against a fixed rubric.

TASK
{goal}

RUBRIC (score both candidates against exactly these criteria)
{rubric}

CANDIDATE A
{a}

CANDIDATE B
{b}

Decide which candidate better satisfies the rubric, or that they are equivalent.
Judge the OUTPUT against the rubric only. You have no information about where either
candidate came from, and you should not speculate.

Respond with ONE JSON object and nothing else — no prose either side, no code fence:
{{
  "reasoning": "what you compared, criterion by criterion",
  "winner": "A" | "B" | "tie",
  "cannot_judge": "why you genuinely could not compare them, or an empty string"
}}

If you cannot tell, say why in `cannot_judge` and leave `winner` as "tie". A coin flip
dressed as a comparison is worse than an admission."""


def render_pair_prompt(*, goal: str, rubric_text: str, slot_a: str, slot_b: str) -> str:
    """The blinded comparative judge prompt.

    Blinding is structural: this function's parameters are the task, the PINNED rubric text
    and two anonymous outputs. There is no parameter through which a version number, a
    timestamp, an arm label or the hypothesis could arrive, which is a stronger guarantee
    than remembering not to pass them. :func:`assert_blinded` then asserts the negative
    over the rendered result, so a future edit that reintroduces one is caught at the call
    site rather than at review time.
    """
    return _PAIR_BODY.format(goal=goal, rubric=rubric_text, a=slot_a, b=slot_b)


#: Slot winner → ordinal, so "the median of three" is a real median rather than a mode.
_SLOT_ORDINAL = {SLOT_A: -1, WINNER_TIE: 0, SLOT_B: 1}
_ORDINAL_SLOT = {-1: SLOT_A, 0: WINNER_TIE, 1: SLOT_B}


def parse_pair_answer(text: str) -> str:
    """One judge sample's answer as a SLOT winner: ``"A"``, ``"B"``, ``"tie"`` or cannot-judge.

    Reject-by-default, in the comparative form: an unparseable answer is
    :data:`WINNER_CANNOT_JUDGE`, never a win for whichever slot the parser saw first.
    `LLMJudge` scores a parse failure 0 because 0 is its "no" — the comparative equivalent
    of "no" is "no signal", because scoring it as a slot win would hand the study to
    whoever the malformed answer happened to mention.
    """
    data = parse_judge_json(text)
    if not isinstance(data, dict):
        return WINNER_CANNOT_JUDGE
    if str(data.get("cannot_judge") or "").strip():
        return WINNER_CANNOT_JUDGE
    raw = str(data.get("winner") or "").strip()
    upper = raw.upper()
    if upper in (SLOT_A, SLOT_B):
        return upper
    if raw.lower() == WINNER_TIE:
        return WINNER_TIE
    return WINNER_CANNOT_JUDGE


def median_slot_winner(samples: Sequence[str]) -> str:
    """The ordinal median of the sample slot-winners (§2.3's reused median-of-3).

    `judge_contract.aggregate_samples` is deliberately NOT reused here: it aggregates
    `JudgeVerdict` objects over the engine's PASS/REJECT vocabulary, and a comparative
    slot winner is not a member of it. Feeding one vocabulary's values to the other's
    aggregator is the exact mistake WF2LOO-13 merged two enums to prevent. What IS reused
    is the RULE — an odd sample count, the middle value decides — and the sample count
    itself (:data:`~personalclaw.workflows.judge_contract.DEFAULT_JUDGE_SAMPLES`).

    A sample that could not be judged carries no position, so it is dropped before the
    median; if that leaves nothing, the presentation is cannot-judge. Dropping rather than
    imputing a tie matters: three cannot-judges would otherwise read as a confident tie.
    """
    ordinals = sorted(_SLOT_ORDINAL[s] for s in samples if s in _SLOT_ORDINAL)
    if not ordinals:
        return WINNER_CANNOT_JUDGE
    return _ORDINAL_SLOT[ordinals[len(ordinals) // 2]]


def _slot_to_arm(slot_winner: str, slot_a_arm: str) -> str:
    """Translate a slot winner into an ARM winner given this presentation's mapping."""
    if slot_winner == WINNER_CANNOT_JUDGE:
        return WINNER_CANNOT_JUDGE
    if slot_winner == WINNER_TIE:
        return WINNER_TIE
    other = ARM_NEW if slot_a_arm == ARM_OLD else ARM_OLD
    return slot_a_arm if slot_winner == SLOT_A else other


@dataclass(frozen=True)
class PairJudgement:
    """One (case, trial) pair judged twice with the slots exchanged."""

    case_id: str
    trial: int
    #: Which arm sat in slot A of the DIRECT presentation. Randomized per pair and recorded
    #: HERE — outside the prompt — which is what §2.3's "randomized assignment recorded
    #: outside the prompt" means in practice.
    slot_a_arm: str
    direct_samples: tuple[str, ...]
    swapped_samples: tuple[str, ...]
    direct_winner: str
    swapped_winner: str
    outcome: str
    judgeable: bool
    cost_usd: float | None = None

    @property
    def agreed(self) -> bool:
        """Did the two presentations name the same winner? Only meaningful if judgeable."""
        return self.judgeable and self.direct_winner == self.swapped_winner

    @property
    def position_flipped(self) -> bool:
        """The measurement §2.3(b) exists for: the winner changed when the slots did."""
        return self.judgeable and self.direct_winner != self.swapped_winner

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "trial": self.trial,
            "slot_a_arm": self.slot_a_arm,
            "direct_samples": list(self.direct_samples),
            "swapped_samples": list(self.swapped_samples),
            "direct_winner": self.direct_winner,
            "swapped_winner": self.swapped_winner,
            "outcome": self.outcome,
            "judgeable": self.judgeable,
            "agreed": self.agreed,
            "position_flipped": self.position_flipped,
            "cost_usd": self.cost_usd,
        }


def slot_a_arm_for(study_id: str, case_id: str, trial: int) -> str:
    """Which arm gets slot A of the direct presentation — randomized, but reproducible.

    Seeded on (study, case, trial) so the assignment is not "OLD is always A" (which would
    let a judge's position bias read as a real effect) and is also not unrepeatable (which
    would make a study impossible to re-derive from its artifacts).
    """
    rng = random.Random(f"{study_id}:{case_id}:{trial}")
    return ARM_OLD if rng.random() < 0.5 else ARM_NEW


async def judge_pair(
    reg: StudyRegistration,
    *,
    case_id: str,
    trial: int,
    goal: str,
    rubric_text: str,
    old_output: str,
    new_output: str,
    caller: JudgeCaller,
    samples: int = DEFAULT_JUDGE_SAMPLES,
    use_case: str = "eval_judge",
) -> PairJudgement:
    """Judge one pair: two presentations × ``samples`` each, then the position agreement.

    The swap is performed on the OUTPUTS, per pair, and asserted by the resulting
    judgement's recorded slot mapping — not signalled by a flag. A flag can be set by code
    that never exchanges anything.
    """
    slot_a_arm = slot_a_arm_for(reg.study_id, case_id, trial)
    by_arm = {ARM_OLD: old_output, ARM_NEW: new_output}
    other_arm = ARM_NEW if slot_a_arm == ARM_OLD else ARM_OLD

    prompts = {
        PRESENTATION_DIRECT: render_pair_prompt(
            goal=goal,
            rubric_text=rubric_text,
            slot_a=by_arm[slot_a_arm],
            slot_b=by_arm[other_arm],
        ),
        PRESENTATION_SWAPPED: render_pair_prompt(
            goal=goal,
            rubric_text=rubric_text,
            slot_a=by_arm[other_arm],
            slot_b=by_arm[slot_a_arm],
        ),
    }
    assert_blinded(reg, tuple(prompts.values()))

    collected: dict[str, list[str]] = {}
    cost_total = 0.0
    cost_seen = False
    for presentation, prompt in prompts.items():
        answers: list[str] = []
        for _ in range(max(1, samples)):
            call: JudgeCall = await caller(prompt, use_case=use_case)
            answers.append(parse_pair_answer(call.text))
            if call.cost_usd is not None:
                cost_total += call.cost_usd
                cost_seen = True
        collected[presentation] = answers

    # The DIRECT presentation's slot A is `slot_a_arm`; the SWAPPED presentation's is the
    # other arm. Translating each with its own mapping is what makes an agreement between
    # them a statement about the CONTENT rather than about the slot.
    direct_winner = _slot_to_arm(median_slot_winner(collected[PRESENTATION_DIRECT]), slot_a_arm)
    swapped_winner = _slot_to_arm(median_slot_winner(collected[PRESENTATION_SWAPPED]), other_arm)
    judgeable = WINNER_CANNOT_JUDGE not in (direct_winner, swapped_winner)
    if not judgeable:
        outcome = OUTCOME_NO_SIGNAL
    elif direct_winner != swapped_winner:
        # §2.3(b) verbatim: a pair whose verdict flips with position is no-signal, counted
        # for neither arm. Recording it as a tie would smuggle it into the tie column and
        # make a position-biased judge look merely indecisive.
        outcome = OUTCOME_NO_SIGNAL
    elif direct_winner == WINNER_TIE:
        outcome = OUTCOME_TIE
    else:
        outcome = direct_winner
    return PairJudgement(
        case_id=case_id,
        trial=trial,
        slot_a_arm=slot_a_arm,
        direct_samples=tuple(collected[PRESENTATION_DIRECT]),
        swapped_samples=tuple(collected[PRESENTATION_SWAPPED]),
        direct_winner=direct_winner,
        swapped_winner=swapped_winner,
        outcome=outcome,
        judgeable=judgeable,
        cost_usd=cost_total if cost_seen else None,
    )


def agreement_rate(pairs: Sequence[PairJudgement]) -> float | None:
    """Position-swap agreement over the pairs that produced a winner at BOTH positions.

    ``None`` when no pair was judgeable — "we could not measure agreement" is not "0.0
    agreement", and reporting it as a number would let an unmeasurable study read as a
    catastrophically biased one (or, with the comparison the other way, as a fine one).

    A `cannot_judge` pair is excluded from the denominator rather than counted as
    disagreement: a judge that says "I cannot tell" is behaving correctly, and dragging the
    agreement rate down for it would penalize exactly the honesty §2.3 wants.
    """
    judgeable = [p for p in pairs if p.judgeable]
    if not judgeable:
        return None
    return sum(1 for p in judgeable if p.agreed) / len(judgeable)


@dataclass(frozen=True)
class CaseOutcome:
    """One input case's resolution — the unit the win rate is computed over.

    §2.1 aggregates "win/loss/tie per CASE", not per trial: the k trials of one case are
    repeated measurements of the same thing, so treating each as independent would inflate
    the sample by a factor of k and make a 1-case study look like a 5-case one.
    """

    case_id: str
    outcome: str
    pairs: tuple[PairJudgement, ...] = ()

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "outcome": self.outcome,
            "pairs": [p.to_dict() for p in self.pairs],
        }


def case_outcome(case_id: str, pairs: Sequence[PairJudgement]) -> CaseOutcome:
    """Resolve one case from its k pair judgements by majority of arm wins."""
    new_wins = sum(1 for p in pairs if p.outcome == ARM_NEW)
    old_wins = sum(1 for p in pairs if p.outcome == ARM_OLD)
    ties = sum(1 for p in pairs if p.outcome == OUTCOME_TIE)
    if new_wins == old_wins == ties == 0:
        outcome = OUTCOME_NO_SIGNAL
    elif new_wins > old_wins:
        outcome = ARM_NEW
    elif old_wins > new_wins:
        outcome = ARM_OLD
    else:
        outcome = OUTCOME_TIE
    return CaseOutcome(case_id=case_id, outcome=outcome, pairs=tuple(pairs))


# ── §2.4 the verdict, and what it does ───────────────────────────────────────


@dataclass(frozen=True)
class StudyResult:
    """One study's decided outcome — the payload of ``verdict.json`` and the ledger row."""

    study_id: str
    kind: str
    verdict: str
    wins: int = 0
    losses: int = 0
    ties: int = 0
    no_signal: int = 0
    win_rate: float | None = None
    agreement: float | None = None
    agreement_floor: float = 0.6
    #: Carried SEPARATELY from ``verdict`` so a deterministic locked-check regression can
    #: be the verdict without erasing the fact that the judge was also unreliable. Two
    #: facts, two side effects: the template gets a demotion proposal and the judge gets a
    #: calibration item, because both are true.
    judge_below_floor: bool = False
    low_power: bool = False
    fail_reason: str = ""
    detail: str = ""
    k: int = 0
    cases: tuple[CaseOutcome, ...] = ()
    locked_outcomes: tuple[LockedOutcome, ...] = ()
    locked_regressions: tuple[str, ...] = ()
    ledger_row_written: bool = False
    evidence_ref: str = ""
    demotion_proposal_id: str = ""
    calibration_ref: str = ""

    @property
    def decided_cases(self) -> int:
        return sum(1 for c in self.cases if c.outcome in (*ARMS, OUTCOME_TIE))

    def to_dict(self) -> dict:
        return {
            "study_id": self.study_id,
            "kind": self.kind,
            "verdict": self.verdict,
            "wins": self.wins,
            "losses": self.losses,
            "ties": self.ties,
            "no_signal": self.no_signal,
            "win_rate": self.win_rate,
            "agreement": self.agreement,
            "agreement_floor": self.agreement_floor,
            "judge_below_floor": self.judge_below_floor,
            "low_power": self.low_power,
            "fail_reason": self.fail_reason,
            "detail": self.detail,
            "k": self.k,
            "decided_cases": self.decided_cases,
            "cases": [c.to_dict() for c in self.cases],
            "locked_outcomes": [o.to_dict() for o in self.locked_outcomes],
            "locked_regressions": list(self.locked_regressions),
            "ledger_row_written": self.ledger_row_written,
            "evidence_ref": self.evidence_ref,
            "demotion_proposal_id": self.demotion_proposal_id,
            "calibration_ref": self.calibration_ref,
        }


def decide(
    reg: StudyRegistration,
    *,
    cases: Sequence[CaseOutcome],
    locked: Sequence[LockedOutcome],
    rubric_state: str = RUBRIC_OK,
    rubric_detail: str = "",
) -> StudyResult:
    """Turn the measurements into one verdict, in a fixed and documented precedence.

    1. **Rubric pin broken → ``invalidated``.** Checked first because a study whose rubric
       moved has no interpretation, so nothing downstream of it means anything.
    2. **Locked-check regression → ``loss``.** §2.1's decision rule says "ANY locked-check
       regression = fail regardless", and this outranks the agreement floor too: a
       deterministic regression is knowledge even when the judge is noise, and discarding
       it because the judge was bad would throw away the one measurement that did not
       depend on the judge.
    3. **Agreement below the floor → ``judge_unreliable``.** No winner is declared from
       judgements that flip with position.
    4. Otherwise the win rate decides, over DECIDED cases only.
    """
    wins = sum(1 for c in cases if c.outcome == ARM_NEW)
    losses = sum(1 for c in cases if c.outcome == ARM_OLD)
    ties = sum(1 for c in cases if c.outcome == OUTCOME_TIE)
    no_signal = sum(1 for c in cases if c.outcome == OUTCOME_NO_SIGNAL)
    pairs = [p for c in cases for p in c.pairs]
    agreement = agreement_rate(pairs)
    floor = reg.agreement_floor
    # `None` (unmeasurable) is below every floor: a study that could not establish whether
    # its judge agrees with itself has not established that it is reliable.
    below_floor = agreement is None or agreement < floor
    decided = wins + losses
    win_rate = (wins / decided) if decided else None
    regressions = locked_regressions(locked)
    decided_cases = wins + losses + ties
    low_power = decided_cases < LOW_POWER_CASES

    # The measurements, built ONCE. Each branch below only names the verdict and why, via
    # `replace` — a `**base` splat typed the whole measurement block as `object` and put the
    # five verdict branches beyond mypy's reach, which is the wrong trade for the one
    # function whose branch selection IS the atom.
    measured = StudyResult(
        study_id=reg.study_id,
        kind=reg.kind,
        verdict=VERDICT_TIE,
        wins=wins,
        losses=losses,
        ties=ties,
        no_signal=no_signal,
        win_rate=win_rate,
        agreement=agreement,
        agreement_floor=floor,
        judge_below_floor=below_floor,
        low_power=low_power,
        k=reg.k,
        cases=tuple(cases),
        locked_outcomes=tuple(locked),
        locked_regressions=regressions,
    )
    if rubric_state != RUBRIC_OK:
        return replace(
            measured,
            verdict=VERDICT_INVALIDATED,
            fail_reason=rubric_state,
            detail=rubric_detail,
        )
    if regressions:
        return replace(
            measured,
            verdict=VERDICT_LOSS,
            fail_reason=FAIL_LOCKED_REGRESSION,
            detail="locked check(s) regressed: " + ", ".join(regressions),
        )
    if below_floor:
        seen = "unmeasurable" if agreement is None else f"{agreement:.2f}"
        return replace(
            measured,
            verdict=VERDICT_JUDGE_UNRELIABLE,
            detail=f"position-swap agreement {seen} is below the {floor:.2f} floor",
        )
    if win_rate is None:
        return replace(
            measured, verdict=VERDICT_TIE, detail="no case produced a decided win or loss"
        )
    if win_rate > 0.5:
        return replace(measured, verdict=VERDICT_WIN)
    if win_rate < 0.5:
        return replace(measured, verdict=VERDICT_LOSS, fail_reason="win_rate")
    return replace(measured, verdict=VERDICT_TIE, detail="wins and losses are level")


# ── §2.4 persistence + the two side effects ──────────────────────────────────


def _pin_for(reg: StudyRegistration):
    """The study's :class:`RunPin` — subject id/hash are the template and the registration.

    The registration's own hash is the subject hash: it identifies the exact DESIGN that
    produced the row, which is what a reader of `results.tsv` needs in order to know that
    two rows are comparable.
    """
    from personalclaw.evals.pinning import compute_pin_for_subject

    subject_id = str(reg.subject.get("template_id") or reg.study_id)
    return compute_pin_for_subject(subject_id, reg.sha256())


def persist(reg: StudyRegistration, result: StudyResult) -> StudyResult:
    """Write ``verdict.json`` + ``runs.json`` + one ``results.tsv`` row, for EVERY outcome.

    §2.4's append-only honesty rule: wins, losses, `invalidated` and `judge_unreliable`
    alike. ``verdict.json`` is written FIRST, because the ledger row can legitimately be
    refused (ES-2's pin requirement) and losing the verdict to a missing model binding
    would be the worse failure. When the row IS refused the result says so on
    ``ledger_row_written`` — a silent gap in an append-only ledger is exactly the dishonesty
    the rule exists to prevent.
    """
    store.write_study_verdict(reg.study_id, result.to_dict())
    store.write_study_runs(reg.study_id, [c.to_dict() for c in result.cases])
    written = False
    try:
        store.append_result(
            {
                "study_id": reg.study_id,
                "kind": reg.kind,
                "verdict": result.verdict,
                # Comparative study: the "scores" are the share of decided cases each arm
                # took. There is no absolute score to report, and inventing one would make
                # a study row look averageable against a scenario row.
                "score_old": None if result.win_rate is None else round(1.0 - result.win_rate, 4),
                "score_new": None if result.win_rate is None else round(result.win_rate, 4),
                "k": reg.k,
                "ts": f"{time.time():.0f}",
            },
            pin=_pin_for(reg),
        )
        written = True
    except Exception:  # noqa: BLE001 - a refused row must not lose the verdict
        logger.warning(
            "study %s: verdict.json written but the results.tsv row was refused",
            reg.study_id,
            exc_info=True,
        )
    return replace(result, ledger_row_written=written)


def emit_evidence(reg: StudyRegistration, result: StudyResult) -> str:
    """The evidence unit a PASS emits (§2.4 → §4's trust ladder). Returns its path or ``""``."""
    if result.verdict != VERDICT_WIN:
        return ""
    payload = {
        "kind": "study_pass",
        "study_id": reg.study_id,
        "study_kind": reg.kind,
        "subject": dict(reg.subject),
        "hypothesis": reg.hypothesis,
        "verdict": result.verdict,
        "win_rate": result.win_rate,
        "wins": result.wins,
        "losses": result.losses,
        "ties": result.ties,
        "agreement": result.agreement,
        "agreement_floor": result.agreement_floor,
        "low_power": result.low_power,
        "k": reg.k,
        "rubric_sha256": reg.rubric_sha256,
        "registration_sha256": reg.sha256(),
        "ts": time.time(),
    }
    return str(store.write_study_evidence(reg.study_id, payload))


def file_demotion_proposal(reg: StudyRegistration, result: StudyResult) -> str:
    """A FAIL auto-files a demotion/revert through the unified queue. Returns its id or ``""``.

    Filed as a ``RETIREMENT`` with ``occurrences=1``/``min_evidence=1``, exactly as
    `learning.attribution._file_revert` files an attribution-driven revert: a revert IS a
    retirement of the accepted change, and one study is a first-class signal rather than a
    pattern that must recur three times. The strength is ``causal`` rather than
    ``correlated`` — a k-paired, position-swapped, randomized comparison is a causal design,
    and labelling it as weakly as a drift correlation would understate the strongest
    evidence the substrate can produce.
    """
    if result.verdict != VERDICT_LOSS:
        return ""
    from personalclaw.learning import proposals

    target = str(reg.subject.get("template_id") or "")
    old_version = reg.subject.get("old_version")
    reason = (
        result.detail
        or f"win rate {result.win_rate:.2f} over {result.decided_cases} decided case(s)"
    )
    body = (
        f"Pre-registered study {reg.study_id} FAILED the candidate change to {target}. "
        f"Hypothesis under test: {reg.hypothesis or '(none recorded)'}. Result: {reason}. "
        f"Position-swap agreement "
        f"{'unmeasurable' if result.agreement is None else f'{result.agreement:.2f}'} "
        f"against a {result.agreement_floor:.2f} floor; "
        f"{result.wins} win / {result.losses} loss / {result.ties} tie / "
        f"{result.no_signal} no-signal over k={reg.k}."
        + (f" Revert to version {old_version}." if old_version not in (None, "") else "")
    )
    _verdict, proposal = proposals.enqueue(
        kind=proposals.Kind.RETIREMENT.value,
        title=f"Revert {target or reg.study_id} — study {reg.study_id} scored {result.verdict}",
        body=body,
        target=target,
        provenance="inferred",
        evidence_refs=[f"evals/studies/{reg.study_id}/verdict.json"],
        evidence_strength="causal",
        confidence=0.7,
        tags=["study", "revert", reg.kind],
        occurrences=1,
        min_evidence=1,
    )
    return proposal.id if proposal is not None else ""


def file_judge_calibration(reg: StudyRegistration, result: StudyResult) -> str:
    """Below-floor agreement files a calibration item for §6's benchmark. Path or ``""``.

    §2.3: "a bad judge produces work for the judge harness, never a fake win". Filed
    whenever the agreement was below the floor — including when the VERDICT was a
    deterministic locked-check loss, because the judge being unreliable is a separate fact
    from the template being worse, and the fix for it lives in a different harness.
    """
    if not result.judge_below_floor:
        return ""
    flipped = [
        f"{p.case_id}/trial{p.trial}" for c in result.cases for p in c.pairs if p.position_flipped
    ]
    payload = {
        "kind": "judge_calibration_request",
        "source": "study",
        "study_id": reg.study_id,
        "agreement": result.agreement,
        "agreement_floor": result.agreement_floor,
        "model_fingerprint": dict(reg.model_fingerprint),
        "position_flipped_pairs": sorted(flipped),
        "pairs_judged": sum(len(c.pairs) for c in result.cases),
        "study_verdict": result.verdict,
        "ts": time.time(),
    }
    return str(store.write_judge_calibration_item(reg.study_id, payload))


# ── the orchestration ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ArmOutput:
    """One arm run's result — the output the judge compares and the workspace to check in."""

    output: str
    workspace: str = ""
    wall_secs: float = 0.0
    cost_usd: float | None = None
    ok: bool = True
    detail: str = ""


#: ``(payload) -> ArmOutput``. Injected so the whole protocol is exercisable without a
#: model or a workflow engine, and so the ONE place a template arm executes is nameable —
#: the same seam discipline as ES-4's :data:`JudgeCaller`.
ArmRunner = Callable[..., Awaitable[ArmOutput]]


@dataclass(frozen=True)
class StudyCase:
    """One input case: its goal (rendered into the judge prompt) and the arm inputs."""

    case_id: str
    goal: str
    case_input: str = ""


async def run_study(
    reg: StudyRegistration,
    *,
    cases: Sequence[StudyCase],
    old_template_body: str,
    new_template_body: str,
    arm_runner: ArmRunner,
    live_rubric_text: str | None = None,
    caller: JudgeCaller | None = None,
    samples: int = DEFAULT_JUDGE_SAMPLES,
) -> StudyResult:
    """Run a registered study end to end and persist its verdict.

    Order is load-bearing:

    1. **Rubric pin first.** A study whose rubric moved is `invalidated` BEFORE a single
       arm runs — it would spend real money producing numbers nobody may interpret.
    2. **The 🔴 §2.2 leak guard next**, over every worker-visible payload of every arm,
       before any of them is spawned. Raises :class:`LockedLeakError` rather than
       degrading: there is no honest verdict for a study that leaked its own checks, so
       there is nothing to fall back to.
    3. Then the k paired runs, the locked checks supervisor-side in each arm's own output
       workspace, the blinded position-swapped judging, and the decision.
    """
    pinned_rubric = store.read_study_rubric(reg.study_id)
    state, detail = rubric_status(reg, live_rubric_text)
    if state != RUBRIC_OK:
        return persist(
            reg, decide(reg, cases=[], locked=[], rubric_state=state, rubric_detail=detail)
        )

    checks = load_locked_checks(reg.study_id)
    if len(checks) < len(reg.locked_checks):
        # 🔴 The declared checks are the study's anti-gaming half. Running without them
        # would produce a verdict that LOOKS like a §2.2 study and is not one — the worst
        # available outcome, because the artifact is indistinguishable from an honest study.
        # This is reachable in practice: `locked/` is `derived_within` on the `evals`
        # inventory entry, so a home restored from a snapshot has the registration and not
        # the keys. Refusing makes that restore loud instead of quietly permissive.
        raise StudyError(
            f"study {reg.study_id} declares {len(reg.locked_checks)} locked check(s) but only "
            f"{len(checks)} could be loaded. The hidden checks never leave the machine that "
            "registered the study (they are excluded from exports and snapshots), so this "
            "study cannot be run here — register a new one with its own checks."
        )
    payloads: list[WorkerPayload] = []
    for case in cases:
        for trial in range(reg.k):
            for arm, body in ((ARM_OLD, old_template_body), (ARM_NEW, new_template_body)):
                payloads.append(
                    WorkerPayload(
                        study_id=reg.study_id,
                        case_id=case.case_id,
                        arm=arm,
                        trial=trial,
                        template_body=body,
                        case_input=case.case_input,
                    )
                )
    if checks and payloads:
        # The CALL SITE of the anti-gaming rail. Every worker-visible string of every arm,
        # checked once, before the first spawn.
        assert_no_locked_leakage(
            reg.study_id, tuple(s for p in payloads for s in p.worker_visible())
        )

    judge = caller or live_judge_caller
    rubric_for_judge = pinned_rubric or ""
    case_outcomes: list[CaseOutcome] = []
    locked: list[LockedOutcome] = []
    by_key = {(p.case_id, p.trial, p.arm): p for p in payloads}
    for case in cases:
        pairs: list[PairJudgement] = []
        for trial in range(reg.k):
            outputs: dict[str, ArmOutput] = {}
            for arm in ARMS:
                payload = by_key[(case.case_id, trial, arm)]
                outputs[arm] = await arm_runner(payload)
                if checks and outputs[arm].workspace:
                    locked.extend(
                        await run_locked_checks(
                            reg.study_id,
                            workspace=outputs[arm].workspace,
                            case_id=case.case_id,
                            trial=trial,
                            arm=arm,
                            checks=checks,
                        )
                    )
            pairs.append(
                await judge_pair(
                    reg,
                    case_id=case.case_id,
                    trial=trial,
                    goal=case.goal,
                    rubric_text=rubric_for_judge,
                    old_output=outputs[ARM_OLD].output,
                    new_output=outputs[ARM_NEW].output,
                    caller=judge,
                    samples=samples,
                )
            )
        case_outcomes.append(case_outcome(case.case_id, pairs))

    result = persist(reg, decide(reg, cases=case_outcomes, locked=locked))
    return replace(
        result,
        evidence_ref=emit_evidence(reg, result),
        demotion_proposal_id=file_demotion_proposal(reg, result),
        calibration_ref=file_judge_calibration(reg, result),
    )


# ── the Learning-page view ───────────────────────────────────────────────────


def study_view(study_id: str) -> dict | None:
    """One study's registration + verdict + per-run artifacts, or ``None`` if unregistered.

    The rubric TEXT is not in here and the ``locked/`` checks are not in here. The rubric's
    hash identifies it without publishing it; the locked checks are the §2.2 secret, and a
    read-only API that served them would defeat the clause the study is built around — the
    user's own dashboard is one `curl` away from a worker's context.
    """
    reg_raw = store.read_study_registration(study_id)
    if reg_raw is None:
        return None
    reg = registration_from_dict(reg_raw)
    verdict = store.read_study_verdict(study_id)
    return {
        "study_id": study_id,
        "kind": reg.kind,
        "subject": dict(reg.subject),
        "hypothesis": reg.hypothesis,
        "k": reg.k,
        "inputs": list(reg.inputs),
        "metric": reg.metric,
        "decision_rule": reg.decision_rule,
        "rubric_sha256": reg.rubric_sha256,
        "registration_sha256": reg.sha256(),
        "agreement_floor": reg.agreement_floor,
        "budget_usd": reg.budget_usd,
        "registered_ts": reg.registered_ts,
        "locked_check_count": len(reg.locked_checks),
        "status": "registered" if verdict is None else "complete",
        "verdict": verdict,
        "runs": store.read_study_runs(study_id) or [],
        "evidence": store.read_study_evidence(study_id),
    }


def latest_study_view() -> dict | None:
    """The newest registered study's view, or ``None`` when none has been registered."""
    for study_id in store.list_study_ids():
        view = study_view(study_id)
        if view is not None:
            return view
    return None


def study_index() -> list[dict]:
    """A compact row per study for the Learning page's list.

    ``verdict``/``agreement`` are ``None`` for a registered-but-unrun study rather than
    absent keys, so a frontend renders "not run yet" from data instead of from a missing
    property.
    """
    rows: list[dict] = []
    for study_id in store.list_study_ids():
        reg_raw = store.read_study_registration(study_id)
        if reg_raw is None:  # pragma: no cover - raced unlink between listing and read
            continue
        reg = registration_from_dict(reg_raw)
        verdict = store.read_study_verdict(study_id) or {}
        rows.append(
            {
                "study_id": study_id,
                "kind": reg.kind,
                "subject": dict(reg.subject),
                "hypothesis": reg.hypothesis,
                "k": reg.k,
                "registered_ts": reg.registered_ts,
                "verdict": verdict.get("verdict"),
                "agreement": verdict.get("agreement"),
                "agreement_floor": reg.agreement_floor,
                "win_rate": verdict.get("win_rate"),
                "low_power": bool(verdict.get("low_power")),
                "fail_reason": verdict.get("fail_reason") or "",
                "locked_regressions": list(verdict.get("locked_regressions") or []),
            }
        )
    return rows
