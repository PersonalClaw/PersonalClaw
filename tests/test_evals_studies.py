"""EVALUATION-SUBSTRATE ES-5 — pre-registered template A/B studies (§2).

This atom is adversarial by design: every clause in it defeats a specific way of faking a
result, so every clause here gets its own assertion AND a falsification that shows the
assertion can fail. The four that matter most, and the shape of their rails:

* **§2.1 immutable registration / pinned rubric** — the rail is the INVALIDATION, and it is
  asserted on the HASH. `test_touching_the_rubric_without_changing_it_does_NOT_invalidate`
  is the falsification of a timestamp-based implementation: a study that invalidates on
  mtime would fail that test, and one that invalidates on content passes it.
* **§2.3 blinded / position-swapped / median-of-3** — three mechanisms, three sets of
  tests. The position-swap rail asserts the OUTPUTS were actually exchanged (slot A of the
  swapped presentation is byte-identical to slot B of the direct one), never that a flag
  was set.
* **§2.3 agreement floor** — a judge that always names slot A produces zero winners rather
  than a clean sweep.
* 🔴 **§2.2 locked/ never worker-visible** — a NEGATIVE assertion, so it carries a vacuity
  floor: `test_the_leak_guard_REFUSES_a_vacuous_token_set` and
  `test_the_leak_guard_REFUSES_an_empty_scan_set` are what make the clean result mean
  something, and `test_a_new_worker_payload_text_field_is_scanned_by_default` is what keeps
  it meaning something after the payload grows a field.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import fields

import pytest

from personalclaw.evals import store, studies
from personalclaw.evals.judge_bench import JudgeCall
from personalclaw.evals.matrix import FAILED, PASSED, VERIFIER_ABSENT

RUBRIC = "correctness (target 2)\nlegibility (target 2)\n"
HYPOTHESIS = "adding the verify gate at step 3 reduces failed runs"
SUBJECT = {
    "template_id": "wf-inbox-triage",
    "old_version": 7,
    "new_version": 8,
    "diff_proposal_id": "pr-abc123",
}
OLD_BODY = "step: draft the reply\nstep: send\n"
NEW_BODY = "step: draft the reply\nstep: verify\nstep: send\n"
OLD_OUT = "ARTIFACT-FROM-THE-BASELINE-ARM"
NEW_OUT = "ARTIFACT-FROM-THE-CANDIDATE-ARM"


@pytest.fixture()
def eval_home(tmp_path, monkeypatch):
    """An isolated home + a bound model fingerprint, so the ledger's pin is complete.

    `config_dir()` re-reads `PERSONALCLAW_HOME` on every call, so the env var is the whole
    isolation. The fingerprint is patched because an empty home has no `active_models.json`
    and ES-2's `append_result` rightly refuses an unattributable row — a study test that
    silently exercised the refusal path would never assert the row it claims to write.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(
        "personalclaw.evals.pinning.model_fingerprint",
        lambda: {"chat": "Fake:model-a", "eval_judge": "Fake:model-j"},
    )
    return tmp_path


# ── fakes for the two injected seams ─────────────────────────────────────────


def _answer(winner: str, *, cannot: str = "") -> str:
    return json.dumps({"reasoning": "compared both", "winner": winner, "cannot_judge": cannot})


def _slot_texts(prompt: str) -> tuple[str, str]:
    """``(slot_a_body, slot_b_body)`` as rendered — how the fakes read a prompt."""
    a = prompt.index("CANDIDATE A")
    b = prompt.index("CANDIDATE B")
    tail = prompt.index("Decide which candidate")
    return prompt[a:b], prompt[b:tail]


class RecordingCaller:
    """A judge seam that records every prompt and answers by a supplied rule."""

    def __init__(self, decide):
        self.prompts: list[str] = []
        self._decide = decide

    async def __call__(self, prompt: str, *, use_case: str = "eval_judge") -> JudgeCall:
        index = len(self.prompts)
        self.prompts.append(prompt)
        return JudgeCall(text=self._decide(prompt, index), cost_usd=0.01, model="Fake:model-j")


def prefers(marker: str):
    """A CONTENT-following judge: it names whichever slot holds ``marker``.

    This is the honest judge. Because it follows content, its answer survives a position
    swap, which is what makes the swap agreement 1.0 for it.
    """

    def decide(prompt: str, _index: int) -> str:
        slot_a, _slot_b = _slot_texts(prompt)
        return _answer("A" if marker in slot_a else "B")

    return decide


def always_slot(slot: str):
    """A POSITION-biased judge: it names the same slot no matter what is in it."""

    def decide(_prompt: str, _index: int) -> str:
        return _answer(slot)

    return decide


def declines():
    def decide(_prompt: str, _index: int) -> str:
        return _answer("tie", cannot="the outputs are not comparable")

    return decide


def arm_runner(*, workspaces: dict | None = None, outputs: dict | None = None):
    """An arm seam returning a fixed output per arm, optionally in a per-arm workspace."""
    texts = outputs or {studies.ARM_OLD: OLD_OUT, studies.ARM_NEW: NEW_OUT}
    calls: list[studies.WorkerPayload] = []

    async def run(payload: studies.WorkerPayload) -> studies.ArmOutput:
        calls.append(payload)
        ws = ""
        if workspaces is not None:
            ws = str(workspaces[payload.arm])
        return studies.ArmOutput(output=texts[payload.arm], workspace=ws)

    run.calls = calls  # type: ignore[attr-defined]
    return run


def register(**over) -> studies.StudyRegistration:
    kwargs = dict(
        subject=dict(SUBJECT),
        hypothesis=HYPOTHESIS,
        inputs=["case-1", "case-2", "case-3"],
        rubric_text=RUBRIC,
        k=1,
    )
    kwargs.update(over)
    return studies.register_study(**kwargs)


def cases(n: int = 3) -> list[studies.StudyCase]:
    return [
        studies.StudyCase(case_id=f"case-{i + 1}", goal="Summarize the queue", case_input="inbox")
        for i in range(n)
    ]


LOCKED_CMD = {"id": "reply_file_exists", "command": "test -f reply.txt", "expect_exit_code": 0}
LOCKED_PHRASE = {
    "id": "cites_the_source",
    "path": "reply.txt",
    "required_phrases": ["Source: inbox-4711"],
}


# ── §2.1 the pre-registration is immutable ───────────────────────────────────


def test_register_study_writes_the_registration_the_pinned_rubric_and_the_locked_checks(
    eval_home,
):
    reg = register(locked_checks=[LOCKED_CMD, LOCKED_PHRASE])
    assert reg.study_id.startswith("st-")
    assert store.read_study_registration(reg.study_id)["hypothesis"] == HYPOTHESIS
    assert store.read_study_rubric(reg.study_id) == RUBRIC
    assert reg.rubric_sha256 == studies.rubric_sha256(RUBRIC)
    ids = {c["id"] for c in store.read_locked_checks(reg.study_id)}
    assert ids == {"reply_file_exists", "cites_the_source"}
    assert reg.locked_checks == (
        "locked/cites_the_source.json",
        "locked/reply_file_exists.json",
    ) or set(reg.locked_checks) == {
        "locked/reply_file_exists.json",
        "locked/cites_the_source.json",
    }


def test_a_second_registration_of_the_same_study_id_is_REFUSED(eval_home):
    reg = register()
    with pytest.raises(store.StudySealedError, match="immutable"):
        register(study_id=reg.study_id, hypothesis="a nicer hypothesis after seeing results")
    # And the original survived the attempt untouched.
    assert store.read_study_registration(reg.study_id)["hypothesis"] == HYPOTHESIS


def test_the_registration_and_pinned_rubric_are_read_only_on_disk(eval_home):
    reg = register()
    for path in (store.registration_path(reg.study_id), store.rubric_path(reg.study_id)):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert not mode & stat.S_IWUSR, f"{path.name} is writable ({oct(mode)})"


def test_locked_checks_land_0600_inside_a_0700_directory(eval_home):
    reg = register(locked_checks=[LOCKED_CMD])
    locked_dir = store.study_dir(reg.study_id) / "locked"
    assert stat.S_IMODE(locked_dir.stat().st_mode) == 0o700
    for path in locked_dir.glob("*.json"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_registration_missing_its_pinned_rubric_is_not_silently_completed(eval_home):
    reg = register()
    store.registration_path(reg.study_id).chmod(0o600)
    os.unlink(store.registration_path(reg.study_id))
    # The rubric survives, so a re-register would pin to a rubric nobody registered.
    with pytest.raises(store.StudySealedError, match="rubric.md"):
        register(study_id=reg.study_id)


def test_k_and_the_agreement_floor_default_from_EvalsConfig_not_from_literals(eval_home):
    reg = register(k=0, agreement_floor=0.0)
    from personalclaw.config.loader import AppConfig

    evals = AppConfig.load().evals
    assert reg.k == evals.study_default_k
    assert reg.agreement_floor == pytest.approx(evals.judge_agreement_floor)


# ── §2.3 a mid-study rubric edit invalidates, and it is decided on the HASH ───


@pytest.mark.asyncio
async def test_a_midstudy_rubric_edit_INVALIDATES_the_study(eval_home):
    reg = register()
    runner = arm_runner()
    result = await studies.run_study(
        reg,
        cases=cases(),
        old_template_body=OLD_BODY,
        new_template_body=NEW_BODY,
        arm_runner=runner,
        live_rubric_text=RUBRIC + "novelty (target 2)\n",
        caller=RecordingCaller(prefers(NEW_OUT)),
    )
    assert result.verdict == studies.VERDICT_INVALIDATED
    assert result.fail_reason == studies.RUBRIC_LIVE_EDITED
    # It is INVALIDATED, not merely flagged: no winner, no evidence, no proposal.
    assert result.wins == result.losses == 0
    assert result.evidence_ref == "" and result.demotion_proposal_id == ""
    # And it spent nothing: the pin is checked before arm 1 runs.
    assert runner.calls == []


@pytest.mark.asyncio
async def test_tampering_with_the_studys_own_pinned_rubric_copy_also_invalidates(eval_home):
    reg = register()
    path = store.rubric_path(reg.study_id)
    path.chmod(0o600)
    path.write_text(RUBRIC + "and be nice about it\n", encoding="utf-8")
    result = await studies.run_study(
        reg,
        cases=cases(),
        old_template_body=OLD_BODY,
        new_template_body=NEW_BODY,
        arm_runner=arm_runner(),
        live_rubric_text=RUBRIC,
        caller=RecordingCaller(prefers(NEW_OUT)),
    )
    assert result.verdict == studies.VERDICT_INVALIDATED
    assert result.fail_reason == studies.RUBRIC_PIN_TAMPERED


def test_touching_the_rubric_without_changing_it_does_NOT_invalidate(eval_home):
    """The falsification of a timestamp-based pin.

    An implementation that invalidated on mtime would fail here; one that invalidates on
    content passes. This is the test that makes "assert on the hash, not on a timestamp" a
    property of the code rather than a claim in a docstring.
    """
    reg = register()
    path = store.rubric_path(reg.study_id)
    before = path.stat().st_mtime
    os.utime(path, (before + 10_000, before + 10_000))
    assert path.stat().st_mtime != before
    state, _detail = studies.rubric_status(reg, RUBRIC)
    assert state == studies.RUBRIC_OK


def test_a_missing_pinned_rubric_is_invalidation_not_a_shrug(eval_home):
    reg = register()
    store.rubric_path(reg.study_id).chmod(0o600)
    os.unlink(store.rubric_path(reg.study_id))
    state, _detail = studies.rubric_status(reg, RUBRIC)
    assert state == studies.RUBRIC_PIN_MISSING


# ── §2.3 blinding ────────────────────────────────────────────────────────────


def test_the_pair_prompt_carries_no_version_hypothesis_or_arm_label(eval_home):
    reg = register()
    prompt = studies.render_pair_prompt(
        goal="Summarize the queue", rubric_text=RUBRIC, slot_a=OLD_OUT, slot_b=NEW_OUT
    )
    for token in reg.blinding_leak_tokens():
        assert token not in prompt, f"{token!r} un-blinds the judge"
    assert "version 7" not in prompt and "version 8" not in prompt
    assert HYPOTHESIS not in prompt
    studies.assert_blinded(reg, (prompt,))


def test_assert_blinded_RAISES_when_the_hypothesis_reaches_the_prompt(eval_home):
    reg = register()
    leaky = studies.render_pair_prompt(
        goal=f"Summarize the queue. We expect that {HYPOTHESIS}.",
        rubric_text=RUBRIC,
        slot_a=OLD_OUT,
        slot_b=NEW_OUT,
    )
    with pytest.raises(studies.LockedLeakError, match="VIOLATED"):
        studies.assert_blinded(reg, (leaky,))


def test_assert_blinded_REFUSES_a_vacuous_token_set(eval_home):
    """A blinding guard with nothing to look for would certify every prompt."""
    reg = register(hypothesis="", subject={})
    bare = studies.StudyRegistration(
        study_id="s",  # under MIN_LEAK_TOKEN_LEN, so it is filtered out too
        subject={},
        hypothesis="",
        inputs=(),
        k=1,
        rubric_sha256=reg.rubric_sha256,
    )
    assert bare.blinding_leak_tokens() == ()
    with pytest.raises(studies.LockedLeakError, match="vacuously"):
        studies.assert_blinded(bare, ("some prompt",))


# ── §2.3 position swap ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_pair_is_judged_at_BOTH_positions_with_the_outputs_ACTUALLY_exchanged(
    eval_home,
):
    """The swap is asserted on the rendered text, never on a flag.

    Slot A of the swapped presentation must be byte-identical to slot B of the direct one,
    and vice versa. A `position_swapped=True` field can be set by code that exchanges
    nothing, which is why this reads the prompts.
    """
    reg = register(k=2)
    caller = RecordingCaller(prefers(NEW_OUT))
    await studies.run_study(
        reg,
        cases=cases(2),
        old_template_body=OLD_BODY,
        new_template_body=NEW_BODY,
        arm_runner=arm_runner(),
        live_rubric_text=RUBRIC,
        caller=caller,
    )
    samples = studies.DEFAULT_JUDGE_SAMPLES
    pairs = 2 * 2  # 2 cases × k=2
    assert len(caller.prompts) == pairs * len(studies.PRESENTATIONS) * samples
    per_pair = len(studies.PRESENTATIONS) * samples
    for pair_index in range(pairs):
        block = caller.prompts[pair_index * per_pair : (pair_index + 1) * per_pair]
        direct_a, direct_b = _slot_texts(block[0])
        swapped_a, swapped_b = _slot_texts(block[samples])
        assert direct_a != swapped_a, "the slots were not exchanged"
        assert direct_a.replace("CANDIDATE A", "") == swapped_b.replace("CANDIDATE B", "")
        assert direct_b.replace("CANDIDATE B", "") == swapped_a.replace("CANDIDATE A", "")


def test_slot_a_is_not_always_the_old_arm(eval_home):
    """If OLD always sat in slot A, a judge's position bias would read as a real effect."""
    assignments = {
        studies.slot_a_arm_for("st-fixed", f"case-{i}", t) for i in range(12) for t in range(5)
    }
    assert assignments == set(studies.ARMS)
    # …and the assignment is reproducible, so a study can be re-derived from its artifacts.
    assert studies.slot_a_arm_for("st-fixed", "case-1", 0) == studies.slot_a_arm_for(
        "st-fixed", "case-1", 0
    )


@pytest.mark.asyncio
async def test_a_position_biased_judge_produces_NO_winner(eval_home):
    """A judge that always names slot A flips on every swap → zero wins, not a sweep."""
    reg = register(k=1)
    result = await studies.run_study(
        reg,
        cases=cases(),
        old_template_body=OLD_BODY,
        new_template_body=NEW_BODY,
        arm_runner=arm_runner(),
        live_rubric_text=RUBRIC,
        caller=RecordingCaller(always_slot("A")),
    )
    assert result.agreement == pytest.approx(0.0)
    assert result.wins == 0 and result.losses == 0
    assert result.no_signal == 3
    assert result.verdict == studies.VERDICT_JUDGE_UNRELIABLE
    flipped = [p for c in result.cases for p in c.pairs if p.position_flipped]
    assert len(flipped) == 3


# ── §2.3 median-of-3 ─────────────────────────────────────────────────────────


def test_the_median_of_three_is_a_median_not_a_first_sample():
    assert studies.median_slot_winner(["A", "B", "B"]) == "B"
    assert studies.median_slot_winner(["B", "A", "A"]) == "A"
    assert studies.median_slot_winner(["A", "tie", "B"]) == "tie"


def test_cannot_judge_samples_are_dropped_before_the_median_not_imputed_as_a_tie():
    """Three refusals must not read as a confident tie."""
    assert studies.median_slot_winner(["cannot_judge"] * 3) == studies.WINNER_CANNOT_JUDGE
    assert studies.median_slot_winner(["cannot_judge", "B", "B"]) == "B"


def test_an_unparseable_answer_is_no_signal_never_a_slot_win():
    assert studies.parse_pair_answer("not json at all") == studies.WINNER_CANNOT_JUDGE
    assert studies.parse_pair_answer('{"winner": "A"}') == "A"
    assert (
        studies.parse_pair_answer('{"winner": "A", "cannot_judge": "no idea"}')
        == studies.WINNER_CANNOT_JUDGE
    )
    # A malformed answer that happens to mention a slot must not hand it the pair.
    assert studies.parse_pair_answer("I think CANDIDATE A wins") == studies.WINNER_CANNOT_JUDGE


@pytest.mark.asyncio
async def test_three_samples_are_taken_per_presentation_from_the_engines_own_constant(
    eval_home,
):
    from personalclaw.workflows import judge_contract

    assert studies.DEFAULT_JUDGE_SAMPLES == judge_contract.DEFAULT_JUDGE_SAMPLES == 3
    reg = register(k=1)
    caller = RecordingCaller(prefers(NEW_OUT))
    await studies.run_study(
        reg,
        cases=cases(1),
        old_template_body=OLD_BODY,
        new_template_body=NEW_BODY,
        arm_runner=arm_runner(),
        live_rubric_text=RUBRIC,
        caller=caller,
    )
    assert len(caller.prompts) == 6, "one pair = 2 positions × 3 samples"


# ── §2.3 the agreement floor and judge_unreliable routing ────────────────────


def test_an_unmeasurable_agreement_is_below_every_floor():
    assert studies.agreement_rate([]) is None
    reg = studies.StudyRegistration(
        study_id="st-x",
        subject=dict(SUBJECT),
        hypothesis=HYPOTHESIS,
        inputs=(),
        k=1,
        rubric_sha256="h",
        agreement_floor=0.6,
    )
    result = studies.decide(reg, cases=[], locked=[])
    assert result.agreement is None
    assert result.judge_below_floor is True
    assert result.verdict == studies.VERDICT_JUDGE_UNRELIABLE


def test_agreement_excludes_cannot_judge_pairs_from_the_denominator():
    """A judge that says "I cannot tell" is behaving well; it must not be scored as disagreeing."""
    agreed = studies.PairJudgement(
        case_id="c",
        trial=0,
        slot_a_arm=studies.ARM_OLD,
        direct_samples=(),
        swapped_samples=(),
        direct_winner=studies.ARM_NEW,
        swapped_winner=studies.ARM_NEW,
        outcome=studies.ARM_NEW,
        judgeable=True,
    )
    declined = studies.PairJudgement(
        case_id="c",
        trial=1,
        slot_a_arm=studies.ARM_OLD,
        direct_samples=(),
        swapped_samples=(),
        direct_winner=studies.WINNER_CANNOT_JUDGE,
        swapped_winner=studies.ARM_NEW,
        outcome=studies.OUTCOME_NO_SIGNAL,
        judgeable=False,
    )
    assert studies.agreement_rate([agreed, declined]) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_below_the_floor_files_a_calibration_item_and_NO_template_verdict(eval_home):
    reg = register(k=1)
    result = await studies.run_study(
        reg,
        cases=cases(),
        old_template_body=OLD_BODY,
        new_template_body=NEW_BODY,
        arm_runner=arm_runner(),
        live_rubric_text=RUBRIC,
        caller=RecordingCaller(always_slot("B")),
    )
    assert result.verdict == studies.VERDICT_JUDGE_UNRELIABLE
    assert result.evidence_ref == "", "an unreliable judge must not emit a pass"
    assert result.demotion_proposal_id == "", "…nor a demotion"
    assert result.calibration_ref, "…but it must produce work for the judge harness"
    items = store.read_judge_calibration_items()
    assert [i["study_id"] for i in items] == [reg.study_id]
    assert items[0]["agreement"] == pytest.approx(0.0)
    assert items[0]["agreement_floor"] == pytest.approx(reg.agreement_floor)
    assert len(items[0]["position_flipped_pairs"]) == 3


@pytest.mark.asyncio
async def test_a_consistent_judge_clears_the_floor_and_produces_a_win(eval_home):
    reg = register(k=1)
    result = await studies.run_study(
        reg,
        cases=cases(),
        old_template_body=OLD_BODY,
        new_template_body=NEW_BODY,
        arm_runner=arm_runner(),
        live_rubric_text=RUBRIC,
        caller=RecordingCaller(prefers(NEW_OUT)),
    )
    assert result.agreement == pytest.approx(1.0)
    assert result.verdict == studies.VERDICT_WIN
    assert result.wins == 3 and result.losses == 0
    assert result.win_rate == pytest.approx(1.0)
    assert result.judge_below_floor is False


# ── 🔴 §2.2 locked/ is supervisor-side and never worker-visible ──────────────


def test_a_locked_token_shorter_than_the_guard_can_see_is_REFUSED_at_registration(eval_home):
    """The guard is never weakened; the input is refused instead."""
    with pytest.raises(studies.StudyError, match="shorter than"):
        studies.parse_locked_check({"id": "ok", "command": "test -f x"})
    with pytest.raises(studies.StudyError, match="shorter than"):
        studies.parse_locked_check(
            {"id": "has_a_long_id", "path": "out.txt", "required_phrases": ["ok"]}
        )


def test_a_locked_check_with_both_shapes_or_neither_is_refused(eval_home):
    with pytest.raises(studies.StudyError, match="both"):
        studies.parse_locked_check(
            {
                "id": "double_shape",
                "command": "true",
                "path": "a.txt",
                "required_phrases": ["hello"],
            }
        )
    with pytest.raises(studies.StudyError, match="neither"):
        studies.parse_locked_check({"id": "empty_check"})
    with pytest.raises(studies.StudyError, match="asserts nothing"):
        studies.parse_locked_check({"id": "phraseless", "path": "a.txt"})


@pytest.mark.asyncio
async def test_no_locked_content_appears_in_ANY_worker_visible_string(eval_home):
    """The positive rail — and it runs through `run_study`, which is the CALL SITE."""
    reg = register(k=1, locked_checks=[LOCKED_CMD, LOCKED_PHRASE])
    runner = arm_runner()
    result = await studies.run_study(
        reg,
        cases=cases(),
        old_template_body=OLD_BODY,
        new_template_body=NEW_BODY,
        arm_runner=runner,
        live_rubric_text=RUBRIC,
        caller=RecordingCaller(prefers(NEW_OUT)),
    )
    assert result.verdict == studies.VERDICT_WIN
    tokens = studies.locked_leak_tokens(reg.study_id)
    assert tokens, "vacuity floor: the study must actually have locked tokens"
    assert "Source: inbox-4711" in tokens and "test -f reply.txt" in tokens
    seen = [s for payload in runner.calls for s in payload.worker_visible()]
    assert seen, "vacuity floor: the arms must actually have been given text"
    for token in tokens:
        for text in seen:
            assert token not in text


@pytest.mark.asyncio
async def test_the_leak_guard_RAISES_when_a_locked_phrase_is_planted_in_a_template_body(
    eval_home,
):
    """The falsification: plant the required phrase where the worker can read it."""
    reg = register(k=1, locked_checks=[LOCKED_PHRASE])
    runner = arm_runner()
    with pytest.raises(studies.LockedLeakError, match="VIOLATED"):
        await studies.run_study(
            reg,
            cases=cases(1),
            old_template_body=OLD_BODY,
            new_template_body=NEW_BODY + "always write 'Source: inbox-4711' in the reply\n",
            arm_runner=runner,
            live_rubric_text=RUBRIC,
            caller=RecordingCaller(prefers(NEW_OUT)),
        )
    assert runner.calls == [], "the guard must fire BEFORE the first arm is spawned"


@pytest.mark.asyncio
async def test_the_leak_guard_also_catches_a_planted_COMMAND_and_a_planted_check_ID(eval_home):
    reg = register(k=1, locked_checks=[LOCKED_CMD])
    for leak in ("test -f reply.txt", "reply_file_exists"):
        with pytest.raises(studies.LockedLeakError, match="VIOLATED"):
            studies.assert_no_locked_leakage(reg.study_id, (f"do the work, then {leak}",))


@pytest.mark.asyncio
async def test_a_study_whose_DECLARED_locked_checks_are_missing_REFUSES_to_run(eval_home):
    """The restore hole the export exclusion opens, closed loudly.

    `locked/` is `derived_within` on the `evals` inventory entry, so a home restored from a
    snapshot carries the registration and not the answer keys. Running anyway would produce
    an artifact indistinguishable from an honest §2.2 study.
    """
    reg = register(k=1, locked_checks=[LOCKED_CMD, LOCKED_PHRASE])
    locked_dir = store.study_dir(reg.study_id) / "locked"
    for path in locked_dir.glob("*.json"):
        path.chmod(0o600)
        os.unlink(path)
    runner = arm_runner()
    with pytest.raises(studies.StudyError, match="only 0 could be loaded"):
        await studies.run_study(
            reg,
            cases=cases(1),
            old_template_body=OLD_BODY,
            new_template_body=NEW_BODY,
            arm_runner=runner,
            live_rubric_text=RUBRIC,
            caller=RecordingCaller(prefers(NEW_OUT)),
        )
    assert runner.calls == []


def test_the_locked_answer_keys_are_excluded_from_exports_and_snapshots(eval_home):
    """§1.1/§2.2's export exclusion, asserted against the field's LIVE readers.

    The declaration alone proved nothing before — `derived_within` shipped with no reader at
    all until it was wired — so this asserts the glob is on the entry AND that both copy
    paths resolve it for `evals`.
    """
    from personalclaw import snapshot
    from personalclaw.durability import inventory as inv

    entry = next(e for e in inv.INVENTORY if e.id == "evals")
    assert "studies/*/locked" in entry.derived_within
    assert snapshot._derived_within("evals") == entry.derived_within
    from personalclaw.portability import _is_derived_within

    assert _is_derived_within("evals", "studies/st-1/locked") is True
    assert _is_derived_within("evals", "studies/st-1/locked/check_01.json") is True
    # …and it excludes ONLY the answer keys. The verdict must still travel, or a study could
    # not be cited as evidence on another machine.
    assert _is_derived_within("evals", "studies/st-1/verdict.json") is False
    assert _is_derived_within("evals", "results.tsv") is False


def test_the_leak_guard_REFUSES_a_vacuous_token_set(eval_home):
    """A negative assertion over an empty token set passes for every input."""
    reg = register()  # deliberately no locked checks
    assert studies.locked_leak_tokens(reg.study_id) == ()
    with pytest.raises(studies.LockedLeakError, match="vacuously"):
        studies.assert_no_locked_leakage(reg.study_id, ("a whole prompt",))


def test_the_leak_guard_REFUSES_an_empty_scan_set(eval_home):
    """…and so does a negative assertion over nothing to scan."""
    reg = register(locked_checks=[LOCKED_CMD])
    with pytest.raises(studies.LockedLeakError, match="Refusing to certify an empty scan"):
        studies.assert_no_locked_leakage(reg.study_id, ())
    with pytest.raises(studies.LockedLeakError, match="Refusing to certify an empty scan"):
        studies.assert_no_locked_leakage(reg.study_id, ("", ""))


def test_a_new_worker_payload_text_field_is_scanned_by_DEFAULT():
    """The guard's coverage is a property of the class, not a hand-maintained list.

    Every field of `WorkerPayload` is either declared supervisor-only or scanned. A field
    added later and forgotten therefore lands in the SCANNED set — a false positive a
    developer notices immediately, rather than a silent hole nobody ever does.
    """
    names = {f.name for f in fields(studies.WorkerPayload)}
    scanned = names - studies.SUPERVISOR_ONLY_FIELDS
    assert scanned, "vacuity floor: something must be scanned"
    assert studies.SUPERVISOR_ONLY_FIELDS <= names, "a stale name in the denylist guards nothing"
    payload = studies.WorkerPayload(
        study_id="st-1",
        case_id="c",
        arm=studies.ARM_OLD,
        trial=0,
        template_body="BODY-MARKER",
        case_input="INPUT-MARKER",
        workspace="/tmp/ws",
    )
    visible = payload.worker_visible()
    assert set(visible) == {"BODY-MARKER", "INPUT-MARKER"}
    assert "/tmp/ws" not in visible and "st-1" not in visible


# ── §2.2 supervisor-side execution in the child output workspace ─────────────


@pytest.mark.asyncio
async def test_locked_checks_execute_in_the_arms_OUTPUT_workspace(eval_home, tmp_path):
    ws = tmp_path / "arm-ws"
    ws.mkdir()
    (ws / "reply.txt").write_text("hello — Source: inbox-4711\n", encoding="utf-8")
    reg = register(locked_checks=[LOCKED_CMD, LOCKED_PHRASE])
    outcomes = await studies.run_locked_checks(
        reg.study_id, workspace=ws, case_id="case-1", trial=0, arm=studies.ARM_NEW
    )
    assert {o.check_id: o.outcome for o in outcomes} == {
        "cites_the_source": PASSED,
        "reply_file_exists": PASSED,
    }
    # And the same checks FAIL honestly on an empty workspace.
    empty = tmp_path / "empty-ws"
    empty.mkdir()
    failed = await studies.run_locked_checks(
        reg.study_id, workspace=empty, case_id="case-1", trial=0, arm=studies.ARM_NEW
    )
    assert {o.outcome for o in failed} == {FAILED}


@pytest.mark.asyncio
async def test_a_locked_command_that_cannot_run_is_verifier_absent_never_a_pass(
    eval_home, tmp_path
):
    ws = tmp_path / "ws"
    ws.mkdir()
    reg = register(
        locked_checks=[
            {"id": "needs_a_missing_binary", "command": "pclaw-no-such-binary --check"},
        ]
    )
    outcomes = await studies.run_locked_checks(
        reg.study_id, workspace=ws, case_id="c", trial=0, arm=studies.ARM_NEW
    )
    assert [o.outcome for o in outcomes] == [VERIFIER_ABSENT]


@pytest.mark.asyncio
async def test_a_screened_locked_command_is_verifier_absent_not_a_silent_pass(eval_home, tmp_path):
    """§2.2: execution goes through `audit_bash_command`, and a refusal is not a pass."""
    ws = tmp_path / "ws"
    ws.mkdir()
    reg = register(locked_checks=[{"id": "destructive_check", "command": "rm -rf /"}])
    outcomes = await studies.run_locked_checks(
        reg.study_id, workspace=ws, case_id="c", trial=0, arm=studies.ARM_NEW
    )
    assert [o.outcome for o in outcomes] == [VERIFIER_ABSENT]


@pytest.mark.asyncio
async def test_a_nonzero_expect_exit_code_still_distinguishes_absent_from_failed(
    eval_home, tmp_path
):
    """The wrapper must not launder a missing binary (127) into a plain failure."""
    ws = tmp_path / "ws"
    ws.mkdir()
    reg = register(
        locked_checks=[
            {
                "id": "expects_exit_one",
                "command": "grep -q FORBIDDEN reply.txt",
                "expect_exit_code": 1,
            },
            {
                "id": "expects_one_but_absent",
                "command": "pclaw-no-such-binary -q",
                "expect_exit_code": 1,
            },
        ]
    )
    (ws / "reply.txt").write_text("clean output\n", encoding="utf-8")
    outcomes = {
        o.check_id: o.outcome
        for o in await studies.run_locked_checks(
            reg.study_id, workspace=ws, case_id="c", trial=0, arm=studies.ARM_NEW
        )
    }
    assert outcomes["expects_exit_one"] == PASSED
    assert outcomes["expects_one_but_absent"] == VERIFIER_ABSENT


@pytest.mark.asyncio
async def test_a_locked_path_escaping_the_workspace_is_verifier_absent(eval_home, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    secret = tmp_path / "outside.txt"
    secret.write_text("Source: inbox-4711\n", encoding="utf-8")
    reg = register(
        locked_checks=[
            {
                "id": "escapes_the_box",
                "path": "../outside.txt",
                "required_phrases": ["Source: inbox-4711"],
            },
        ]
    )
    outcomes = await studies.run_locked_checks(
        reg.study_id, workspace=ws, case_id="c", trial=0, arm=studies.ARM_NEW
    )
    assert [o.outcome for o in outcomes] == [VERIFIER_ABSENT]
    assert "outside" in outcomes[0].detail


def test_the_output_workspace_never_held_the_locked_content(eval_home, tmp_path):
    """§2.2 forbids the checks from the worker's workspace too, not only its prompt."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "reply.txt").write_text("hello — Source: inbox-4711\n", encoding="utf-8")
    reg = register(locked_checks=[LOCKED_CMD])
    studies.assert_locked_absent_from_workspace(reg.study_id, ws)
    # Falsification: write the check itself into the workspace.
    (ws / "hints.md").write_text("run `test -f reply.txt` to be safe\n", encoding="utf-8")
    with pytest.raises(studies.LockedLeakError, match="VIOLATED"):
        studies.assert_locked_absent_from_workspace(reg.study_id, ws)


# ── §2.1 ANY locked-check regression = fail regardless ───────────────────────


@pytest.mark.asyncio
async def test_a_locked_regression_FAILS_the_study_regardless_of_the_win_rate(eval_home, tmp_path):
    """The judge loves the candidate; a locked check says it broke. The check wins."""
    old_ws = tmp_path / "old"
    new_ws = tmp_path / "new"
    old_ws.mkdir()
    new_ws.mkdir()
    (old_ws / "reply.txt").write_text("baseline reply\n", encoding="utf-8")
    # The NEW arm never produced reply.txt → the check that passed on OLD now fails.
    reg = register(k=1, locked_checks=[LOCKED_CMD])
    result = await studies.run_study(
        reg,
        cases=cases(),
        old_template_body=OLD_BODY,
        new_template_body=NEW_BODY,
        arm_runner=arm_runner(workspaces={studies.ARM_OLD: old_ws, studies.ARM_NEW: new_ws}),
        live_rubric_text=RUBRIC,
        caller=RecordingCaller(prefers(NEW_OUT)),
    )
    assert result.win_rate == pytest.approx(1.0), "the judge did prefer the candidate"
    assert result.verdict == studies.VERDICT_LOSS
    assert result.fail_reason == studies.FAIL_LOCKED_REGRESSION
    assert result.locked_regressions
    assert result.demotion_proposal_id, "a fail auto-files a demotion/revert proposal"
    assert result.evidence_ref == ""


def test_a_verifier_absent_baseline_is_NOT_a_regression():
    """A broken check must not manufacture a demotion proposal."""
    absent_then_failed = [
        studies.LockedOutcome("c", 0, studies.ARM_OLD, "chk", VERIFIER_ABSENT),
        studies.LockedOutcome("c", 0, studies.ARM_NEW, "chk", FAILED),
    ]
    assert studies.locked_regressions(absent_then_failed) == ()
    passed_then_failed = [
        studies.LockedOutcome("c", 0, studies.ARM_OLD, "chk", PASSED),
        studies.LockedOutcome("c", 0, studies.ARM_NEW, "chk", FAILED),
    ]
    assert studies.locked_regressions(passed_then_failed) == ("c/trial0/chk",)


def test_a_locked_regression_outranks_the_agreement_floor_but_keeps_BOTH_facts():
    """A deterministic regression is knowledge even when the judge was noise."""
    reg = studies.StudyRegistration(
        study_id="st-both",
        subject=dict(SUBJECT),
        hypothesis=HYPOTHESIS,
        inputs=(),
        k=1,
        rubric_sha256="h",
        agreement_floor=0.6,
    )
    result = studies.decide(
        reg,
        cases=[studies.CaseOutcome("c", studies.OUTCOME_NO_SIGNAL, ())],
        locked=[
            studies.LockedOutcome("c", 0, studies.ARM_OLD, "chk", PASSED),
            studies.LockedOutcome("c", 0, studies.ARM_NEW, "chk", FAILED),
        ],
    )
    assert result.verdict == studies.VERDICT_LOSS
    assert result.fail_reason == studies.FAIL_LOCKED_REGRESSION
    assert result.judge_below_floor is True, "the second fact must not be erased"


# ── §2.4 what a verdict does ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_pass_emits_an_evidence_unit_and_a_pinned_results_tsv_row(eval_home):
    reg = register(k=1)
    result = await studies.run_study(
        reg,
        cases=cases(),
        old_template_body=OLD_BODY,
        new_template_body=NEW_BODY,
        arm_runner=arm_runner(),
        live_rubric_text=RUBRIC,
        caller=RecordingCaller(prefers(NEW_OUT)),
    )
    assert result.verdict == studies.VERDICT_WIN
    evidence = store.read_study_evidence(reg.study_id)
    assert evidence["kind"] == "study_pass"
    assert evidence["rubric_sha256"] == reg.rubric_sha256
    assert evidence["registration_sha256"] == reg.sha256()
    assert result.ledger_row_written is True
    rows = store.read_results()
    assert len(rows) == 1
    row = rows[0]
    assert row["study_id"] == reg.study_id
    assert row["kind"] == studies.KIND_TEMPLATE_AB
    assert row["verdict"] == studies.VERDICT_WIN
    assert row["score_new"] == "1.0" and row["score_old"] == "0.0"
    assert row["k"] == "1"
    assert row["model_fp"], "the row is pinned"
    assert row["scenario_id"] == SUBJECT["template_id"]
    assert row["scenario_sha256"] == reg.sha256()


@pytest.mark.asyncio
async def test_a_LOSS_on_the_win_rate_auto_files_a_demotion_proposal(eval_home):
    reg = register(k=1)
    result = await studies.run_study(
        reg,
        cases=cases(),
        old_template_body=OLD_BODY,
        new_template_body=NEW_BODY,
        arm_runner=arm_runner(),
        live_rubric_text=RUBRIC,
        caller=RecordingCaller(prefers(OLD_OUT)),
    )
    assert result.verdict == studies.VERDICT_LOSS
    assert result.win_rate == pytest.approx(0.0)
    assert result.demotion_proposal_id
    from personalclaw.learning import proposals

    prop = proposals.get(result.demotion_proposal_id)
    assert prop is not None, "the demotion must land in the shared human-gated queue"
    assert prop in proposals.list_pending(proposals.Kind.RETIREMENT.value)
    assert prop.kind == proposals.Kind.RETIREMENT.value
    assert prop.target == SUBJECT["template_id"]
    assert prop.evidence_strength == "causal"
    assert reg.study_id in prop.body
    assert f"evals/studies/{reg.study_id}/verdict.json" in prop.evidence_refs


@pytest.mark.asyncio
async def test_EVERY_outcome_writes_a_verdict_json_including_invalidated(eval_home):
    reg = register(k=1)
    await studies.run_study(
        reg,
        cases=cases(),
        old_template_body=OLD_BODY,
        new_template_body=NEW_BODY,
        arm_runner=arm_runner(),
        live_rubric_text="a rubric nobody registered",
        caller=RecordingCaller(prefers(NEW_OUT)),
    )
    verdict = store.read_study_verdict(reg.study_id)
    assert verdict["verdict"] == studies.VERDICT_INVALIDATED
    assert store.read_study_runs(reg.study_id) == []
    rows = store.read_results()
    assert [r["verdict"] for r in rows] == [studies.VERDICT_INVALIDATED]
    # An unmeasured score is BLANK, never 0 — a 0 would be averageable.
    assert rows[0]["score_new"] == "" and rows[0]["score_old"] == ""


@pytest.mark.asyncio
async def test_a_refused_ledger_pin_is_reported_not_hidden(eval_home, monkeypatch):
    """ES-2's pin requirement wins, but losing the verdict to it would be worse."""
    monkeypatch.setattr("personalclaw.evals.pinning.model_fingerprint", dict)
    reg = register(k=1)
    result = await studies.run_study(
        reg,
        cases=cases(1),
        old_template_body=OLD_BODY,
        new_template_body=NEW_BODY,
        arm_runner=arm_runner(),
        live_rubric_text=RUBRIC,
        caller=RecordingCaller(prefers(NEW_OUT)),
    )
    assert result.ledger_row_written is False
    assert store.read_results() == []
    assert store.read_study_verdict(reg.study_id)["verdict"] == result.verdict


@pytest.mark.asyncio
async def test_a_low_power_study_is_LABELLED_not_silently_upgraded(eval_home):
    reg = register(k=1, inputs=["case-1"])
    result = await studies.run_study(
        reg,
        cases=cases(1),
        old_template_body=OLD_BODY,
        new_template_body=NEW_BODY,
        arm_runner=arm_runner(),
        live_rubric_text=RUBRIC,
        caller=RecordingCaller(prefers(NEW_OUT)),
    )
    assert result.verdict == studies.VERDICT_WIN, "it still reports what it measured"
    assert result.low_power is True
    assert result.decided_cases == 1


# ── the Learning-page view ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_study_view_publishes_the_verdict_agreement_and_per_run_artifacts(eval_home):
    reg = register(k=2, locked_checks=[LOCKED_CMD])
    await studies.run_study(
        reg,
        cases=cases(2),
        old_template_body=OLD_BODY,
        new_template_body=NEW_BODY,
        arm_runner=arm_runner(),
        live_rubric_text=RUBRIC,
        caller=RecordingCaller(prefers(NEW_OUT)),
    )
    view = studies.latest_study_view()
    assert view["study_id"] == reg.study_id
    assert view["status"] == "complete"
    assert view["verdict"]["verdict"] == studies.VERDICT_WIN
    assert view["verdict"]["agreement"] == pytest.approx(1.0)
    assert view["verdict"]["agreement_floor"] == pytest.approx(reg.agreement_floor)
    assert len(view["runs"]) == 2
    assert len(view["runs"][0]["pairs"]) == 2, "k=2 pairs per case are drillable"
    assert view["runs"][0]["pairs"][0]["slot_a_arm"] in studies.ARMS
    assert view["locked_check_count"] == 1
    assert view["evidence"]["kind"] == "study_pass"
    index = studies.study_index()
    assert [r["study_id"] for r in index] == [reg.study_id]
    assert index[0]["verdict"] == studies.VERDICT_WIN


def test_the_view_never_publishes_the_rubric_text_or_the_locked_checks(eval_home):
    """A read-only API that served the locked checks would defeat §2.2 in one curl."""
    reg = register(locked_checks=[LOCKED_CMD, LOCKED_PHRASE])
    view = studies.study_view(reg.study_id)
    blob = json.dumps(view)
    tokens = studies.locked_leak_tokens(reg.study_id)
    assert tokens, "vacuity floor"
    for token in tokens:
        assert token not in blob, f"{token!r} is published by the view"
    assert RUBRIC not in blob
    assert (
        view["rubric_sha256"] == reg.rubric_sha256
    ), "the hash identifies it without publishing it"
    assert view["status"] == "registered" and view["verdict"] is None


def test_an_unregistered_study_view_is_None(eval_home):
    assert studies.study_view("st-nope") is None
    assert studies.latest_study_view() is None
    assert studies.study_index() == []


def test_the_registration_hash_is_canonical_and_order_independent(eval_home):
    reg = register()
    restored = studies.registration_from_dict(store.read_study_registration(reg.study_id))
    assert restored == reg
    assert restored.sha256() == reg.sha256()


# ── 🔴 §2.1 the registration SEAL — what makes every pin above more than decor ─
#
# Every rail above this line reads its own threshold out of `registration.json`:
# `rubric_sha256` pins the rubric, `agreement_floor` sets the judge floor, `k` sets the
# design. Before the seal, that file and the pinned rubric it pins lived in the same
# directory with the same owner, so the whole of §2.1 was defeated by editing the file the
# checks are read FROM — no forgery of a hash required, just a text editor. These tests
# assert the seal on the two things a self-referential hash can never do: catch an edit to
# the design, and catch a rubric forgery whose own hash check comes back clean.


def _rewrite_registration(study_id: str, **fields) -> studies.StudyRegistration:
    """Edit `registration.json` in place the way an experimenter with a shell would.

    Deliberately goes AROUND `register_study`: the API refuses a second registration
    (`StudySealedError`), so an attack that had to call it never starts. The file is the
    attack surface, so the file is what these tests edit — and the ``0400`` mode is a
    tripwire, not a lock, which is exactly why the two `chmod` calls here are honest.
    """
    path = store.registration_path(study_id)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.update(fields)
    path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(stat.S_IRUSR)
    return studies.registration_from_dict(raw)


def test_the_seal_is_recorded_OUTSIDE_the_study_directory(eval_home):
    """The positive floor, and the property the whole mechanism rests on.

    A hash stored beside the file it pins is pinned by nothing: whoever can edit the
    registration can edit a sidecar in the same directory in the same breath.
    """
    reg = register()
    journal = store.study_seals_path()
    assert journal.is_file(), "registering a study must seal it"
    assert store.study_dir(reg.study_id) not in journal.parents
    assert journal.parent == store.evals_root()
    assert store.read_study_seal(reg.study_id) == reg.sha256()
    assert studies.seal_status(reg) == (studies.SEAL_OK, "")
    assert studies.study_view(reg.study_id)["seal_status"] == studies.SEAL_OK


@pytest.mark.asyncio
async def test_an_edited_registration_is_INVALIDATED_and_spends_NOTHING(eval_home):
    """The cheat this closes: lower the agreement floor after seeing `judge_unreliable`.

    `agreement_floor` is not documentation — `decide` reads the verdict threshold out of it.
    So an edited registration is not a cosmetic problem, it is a different study wearing the
    registered one's id.
    """
    reg = register()
    tampered = _rewrite_registration(reg.study_id, agreement_floor=0.01)
    assert tampered.agreement_floor == 0.01, "vacuity floor: the mutation applied"
    state, detail = studies.seal_status(tampered)
    assert state == studies.SEAL_TAMPERED
    assert reg.sha256()[:12] in detail, "the detail names what was registered"

    runner = arm_runner()
    caller = RecordingCaller(prefers(NEW_OUT))
    result = await studies.run_study(
        reg=tampered,
        cases=cases(1),
        old_template_body=OLD_BODY,
        new_template_body=NEW_BODY,
        arm_runner=runner,
        caller=caller,
    )
    assert result.verdict == studies.VERDICT_INVALIDATED
    assert result.fail_reason == studies.SEAL_TAMPERED
    # The CALL SITE assertion: deleting the seal check in `run_study` makes these two red.
    assert runner.calls == [], "a study that may not be interpreted may not spend"
    assert caller.prompts == []
    assert result.evidence_ref == "" and result.demotion_proposal_id == ""
    # §2.4's append-only honesty applies to this outcome too.
    assert (store.read_study_verdict(reg.study_id) or {})["fail_reason"] == studies.SEAL_TAMPERED
    assert [r["verdict"] for r in store.read_results()] == [studies.VERDICT_INVALIDATED]


@pytest.mark.asyncio
async def test_a_forged_rubric_pin_that_the_RUBRIC_CHECK_calls_OK_is_still_invalidated(eval_home):
    """🔴 The discriminating test — the one thing `rubric_status` structurally cannot do.

    Rewrite the pinned rubric AND set `rubric_sha256` to the new rubric's hash. Both live in
    `evals/studies/<id>/`, so after two edits every hash in that directory agrees with every
    other one and the four-way rubric check returns `ok`. The first assertion below is that
    floor: it proves this study is invisible to the pre-seal implementation, so the red the
    seal produces is a red nothing else was catching.
    """
    reg = register()
    forged_rubric = "correctness (target 0)\nlegibility (target 0)\n"
    rubric_path = store.rubric_path(reg.study_id)
    rubric_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    rubric_path.write_text(forged_rubric, encoding="utf-8")
    rubric_path.chmod(stat.S_IRUSR)
    forged = _rewrite_registration(reg.study_id, rubric_sha256=studies.rubric_sha256(forged_rubric))

    assert studies.rubric_status(forged, forged_rubric) == (
        studies.RUBRIC_OK,
        "",
    ), "floor: the rubric check is defeated by this pair, which is why the seal exists"

    runner = arm_runner()
    result = await studies.run_study(
        reg=forged,
        cases=cases(1),
        old_template_body=OLD_BODY,
        new_template_body=NEW_BODY,
        arm_runner=runner,
        live_rubric_text=forged_rubric,
        caller=RecordingCaller(prefers(NEW_OUT)),
    )
    assert result.verdict == studies.VERDICT_INVALIDATED
    assert result.fail_reason == studies.SEAL_TAMPERED
    assert runner.calls == []


@pytest.mark.asyncio
async def test_an_UNSEALED_registration_is_invalidated_not_tolerated(eval_home):
    """ "No seal" and "the seal was deleted" are indistinguishable, so both invalidate.

    The permissive reading of an indistinguishable pair is the whole hole: an attacker who
    can edit the registration can also delete a journal, and would then be *rewarded* for it.
    """
    reg = register()
    store.study_seals_path().unlink()
    assert store.read_study_seal(reg.study_id) is None
    assert studies.seal_status(reg)[0] == studies.SEAL_UNSEALED

    runner = arm_runner()
    result = await studies.run_study(
        reg=reg,
        cases=cases(1),
        old_template_body=OLD_BODY,
        new_template_body=NEW_BODY,
        arm_runner=runner,
        caller=RecordingCaller(prefers(NEW_OUT)),
    )
    assert result.verdict == studies.VERDICT_INVALIDATED
    assert result.fail_reason == studies.SEAL_UNSEALED
    assert runner.calls == []


def test_an_APPENDED_forged_seal_cannot_override_the_first(eval_home):
    """First row wins, so the cheapest attack on the journal is a no-op.

    Floor in the same test: a seal appended for a study that had none IS read back. Without
    that, "first wins" would be indistinguishable from a read that never sees appended rows
    at all, and the negative assertion would be vacuous.
    """
    reg = register()
    tampered = _rewrite_registration(reg.study_id, hypothesis="something else entirely")
    store.append_study_seal(reg.study_id, tampered.sha256(), ts=1.0)
    assert store.read_study_seal(reg.study_id) == reg.sha256()
    assert studies.seal_status(tampered)[0] == studies.SEAL_TAMPERED

    store.append_study_seal("st-never-registered", "deadbeef", ts=2.0)
    assert store.read_study_seal("st-never-registered") == "deadbeef", "floor: appends ARE read"


def test_a_registration_can_never_be_on_disk_without_its_seal(eval_home, monkeypatch):
    """Ordering rail: seal first, registration second.

    The other order has a live failure mode — a registration written and then a seal write
    that fails leaves a study that is permanently `invalidated` and cannot be re-registered
    under its own id, which is the one state with no remedy.
    """
    monkeypatch.setattr(
        store,
        "append_study_seal",
        lambda *a, **k: (_ for _ in ()).throw(OSError("journal is read-only")),
    )
    with pytest.raises(OSError):
        register()
    assert list(store.studies_dir().glob("*/registration.json")) == []


def test_a_zero_agreement_floor_ROUND_TRIPS_or_the_seal_calls_an_honest_study_tampered(
    eval_home, monkeypatch
):
    """Found by the vacuity floor above, not by reading: `float(x or 0.6)` ate a real 0.0.

    A floor of 0 is what a user who does not want the agreement gate configures, and the CLI
    verifies the seal against the registration REHYDRATED FROM DISK. So a lossy read is not
    cosmetic once a seal exists — it invalidates an honest study, permanently, with a message
    accusing its owner of tampering. On the `or 0.6` read this test is red: 0.0 comes back as
    0.6, the rehydrated registration hashes differently, and `seal_status` says tampered.
    """
    monkeypatch.setattr(studies, "_config_defaults", lambda: (5, 0.0))
    reg = register(k=0, agreement_floor=0.0)
    assert reg.agreement_floor == 0.0, "vacuity floor: the zero floor was actually registered"
    restored = studies.registration_from_dict(store.read_study_registration(reg.study_id))
    assert restored.agreement_floor == 0.0, "a zero floor must survive the read"
    assert studies.seal_status(restored) == (studies.SEAL_OK, "")
