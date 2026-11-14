"""What a code loop's stage gate asks its judge (``sdlc.CodeKind``'s gate): the stage's exit
criteria, numbered, and the evidence, through the bundled ``task-sdlc-stage-gate`` prompt."""

from __future__ import annotations


def _gate_prompt(
    phase: dict, stage: str, exit_criteria: list[str], evidence: str, evidence_note: str
) -> str:
    """What a stage's judge is asked: its exit criteria, numbered, and the evidence."""
    criteria = "\n".join(f"{n}. {c}" for n, c in enumerate(exit_criteria, 1))
    # The stage-gate instruction lives in the prompt system (bundled
    # ``task-sdlc-stage-gate``), rendered with the stage + evidence.
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    return (
        render_use_case_prompt(
            "sdlc_stage_gate",
            {
                "stage_title": phase.get("title", stage),
                "objective": phase.get("objective", ""),
                "criteria": criteria,
                "evidence": evidence,
                "evidence_note": evidence_note,
            },
        )
        or ""
    )
