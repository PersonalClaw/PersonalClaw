"""`ES-12` — judge verdict integrity: verdicts must be answerable from the evidence shown.

The T04 gap: a judge could PASS while citing evidence it was never shown, and nothing
downstream could tell — the verdict asserted a conclusion the presented evidence cannot
support. The contract now grounds every citation against the exact slice the caller showed
the judge (verbatim-span or a declared observation command), flags partial fabrication,
rejects a PASS resting ONLY on fabricated citations, and stamps each verdict with the
sha256 identity of the slice it judged.

The T04 regression fixture is `test_t04_fixture_*`: the exact fabricated-PASS shape from
the draft, asserted to have been ACCEPTED by the pre-`ES-12` contract surface (no
answerability signal existed) and REJECTED now — reproduce, then pass.
"""

from __future__ import annotations

import dataclasses

from personalclaw.workflows.judge_contract import (
    JudgeHints,
    JudgeVerdict,
    Verdict,
    adjudicate,
    evidence_hash_of,
    ungrounded_refs,
    validate_verdict,
    verdict_for_cycle,
)

EVIDENCE = (
    "=== test output ===\n"
    "collected 14 items\n"
    "14 passed in 2.31s\n"
    "=== files ===\n"
    "src/app/retry.py | 22 ++++++++-----\n"
)


def _raw_pass(refs: list[str], proof: str = "") -> dict:
    return {
        "verdict": "PASS",
        "reasoning": "looks complete",
        "scores": {},
        "evidence_refs": refs,
        "proof": proof,
        "cannot_judge": "",
    }


# ── the T04 regression fixture: reproduces, then passes ──


def test_t04_fixture_fabricated_pass_was_previously_accepted() -> None:
    """REPRODUCE: without the evidence slice (the pre-ES-12 calling convention), the same
    fabricated PASS sails through — nothing in the record even distinguishes it."""
    fabricated = ["deployment log: rollout completed on all 6 hosts"]
    v = validate_verdict(_raw_pass(fabricated), JudgeHints())
    assert v.verdict is Verdict.PASS and not v.invalid_reason
    assert v.evidence_hash == "" and v.unanswerable_refs == []


def test_t04_fixture_fabricated_pass_is_now_rejected() -> None:
    """PASS: with the slice supplied, the identical verdict is a protocol reject — its only
    citation references evidence the judge was never shown."""
    fabricated = ["deployment log: rollout completed on all 6 hosts"]
    v = validate_verdict(_raw_pass(fabricated), JudgeHints(), evidence_text=EVIDENCE)
    assert v.protocol_error
    assert "not shown" in v.invalid_reason
    assert v.unanswerable_refs == fabricated


# ── grounding rules ──


def test_verbatim_span_grounds_a_citation() -> None:
    v = validate_verdict(_raw_pass(["14 passed in 2.31s"]), JudgeHints(), evidence_text=EVIDENCE)
    assert v.verdict is Verdict.PASS and not v.protocol_error
    assert v.unanswerable_refs == []


def test_grounding_normalizes_whitespace_and_case() -> None:
    v = validate_verdict(_raw_pass(["14  PASSED   in 2.31s"]), JudgeHints(), evidence_text=EVIDENCE)
    assert v.unanswerable_refs == []


def test_declared_observation_commands_are_sanctioned_citations() -> None:
    """The judge is TOLD to run proof_command / hidden_validation_commands itself and cite
    their output — those citations are legitimately outside the slice."""
    hints = JudgeHints(proof_command="pytest tests/test_retry.py -q")
    v = validate_verdict(
        _raw_pass(["ran pytest tests/test_retry.py -q: 14 passed"]),
        hints,
        evidence_text=EVIDENCE,
    )
    assert v.unanswerable_refs == [] and v.verdict is Verdict.PASS


def test_partially_grounded_pass_is_flagged_not_rejected() -> None:
    refs = ["14 passed in 2.31s", "manual QA sign-off from the release channel"]
    v = validate_verdict(_raw_pass(refs), JudgeHints(), evidence_text=EVIDENCE)
    assert v.verdict is Verdict.PASS and not v.protocol_error
    assert v.unanswerable_refs == ["manual QA sign-off from the release channel"]


def test_short_fragments_are_never_flagged() -> None:
    # "passed" grounds against almost anything; flagging it would be noise.
    assert ungrounded_refs(["passed"], "unrelated", None) == []


def test_non_pass_verdicts_are_flagged_but_never_answerability_rejected() -> None:
    raw = _raw_pass(["totally fabricated citation text"])
    raw["verdict"] = "REJECT"
    v = validate_verdict(raw, JudgeHints(), evidence_text=EVIDENCE)
    assert v.verdict is Verdict.REJECT and not v.protocol_error
    assert v.unanswerable_refs == ["totally fabricated citation text"]


def test_pass_with_grounded_proof_survives_fabricated_refs() -> None:
    """`proof` is the judge's own observation record; when present the PASS is not resting
    ONLY on the fabricated refs, so it is flagged rather than rejected."""
    v = validate_verdict(
        _raw_pass(["fabricated citation entirely"], proof="ran the suite: 14 passed"),
        JudgeHints(),
        evidence_text=EVIDENCE,
    )
    assert v.verdict is Verdict.PASS and not v.protocol_error
    assert v.unanswerable_refs == ["fabricated citation entirely"]


# ── the evidence hash on the record ──


def test_verdict_records_carry_the_evidence_hash_they_judged() -> None:
    v = validate_verdict(_raw_pass(["14 passed in 2.31s"]), JudgeHints(), evidence_text=EVIDENCE)
    assert v.evidence_hash == evidence_hash_of(EVIDENCE)
    assert len(v.evidence_hash) == 16
    d = v.to_dict()
    assert d["evidence_hash"] == v.evidence_hash
    assert d["unanswerable_refs"] == []


def test_evidence_hash_is_slice_sensitive_and_empty_for_no_slice() -> None:
    assert evidence_hash_of(EVIDENCE) != evidence_hash_of(EVIDENCE + "x")
    assert evidence_hash_of("") == ""


def test_adjudicate_carries_hash_and_flags_from_the_primary() -> None:
    primary = JudgeVerdict(
        verdict=verdict_for_cycle(True, False),
        evidence_refs=["a" * 20],
        evidence_hash="deadbeefdeadbeef",
        unanswerable_refs=["a" * 20],
    )
    skeptic = JudgeVerdict(verdict=verdict_for_cycle(True, False))
    merged = adjudicate(primary, skeptic)
    assert merged.evidence_hash == "deadbeefdeadbeef"
    assert merged.unanswerable_refs == ["a" * 20]


def test_field_defaults_keep_legacy_construction_working() -> None:
    # Every existing construction site builds JudgeVerdict without the new fields.
    v = JudgeVerdict(verdict=Verdict.PASS)
    assert v.evidence_hash == "" and v.unanswerable_refs == []
    assert dataclasses.asdict(v)["evidence_hash"] == ""
