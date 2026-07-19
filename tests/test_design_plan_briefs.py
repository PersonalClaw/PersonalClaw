"""Unit tests for design_plan_briefs — the design kind's dynamic planning-walkthrough
briefs/parsers/projection, including the D2 multi-modal intake block."""

from __future__ import annotations

from personalclaw.loop import design_plan_briefs as pw


def test_design_brief_lists_multimodal_inputs():
    # D2: each provided reference input must appear in the design-pass brief with a
    # concrete "how to consume it" instruction so the planner works through every one.
    brief = pw.build_design_brief("a warm recipe-app system", "", [
        {"type": "url", "ref": "https://example.com"},
        {"type": "image", "ref": "moodboard.png"},
        {"type": "react", "ref": "Button.tsx"},
        {"type": "design_md", "ref": "DESIGN.md"},
    ])
    assert "REFERENCE INPUTS" in brief
    assert "https://example.com" in brief and "FETCH" in brief
    assert "moodboard.png" in brief and "READ the image" in brief
    assert "Button.tsx" in brief and "React component" in brief
    assert "DESIGN.md" in brief


def test_design_brief_no_inputs_omits_block():
    brief = pw.build_design_brief("a system", "", None)
    assert "REFERENCE INPUTS" not in brief
    # still a valid design pass brief ending in build_plan
    assert "build_plan" in brief


def test_design_inputs_block_skips_blank_refs():
    block = pw.design_inputs_block([{"type": "url", "ref": ""}, {"type": "image", "ref": "x.png"}])
    joined = "\n".join(block)
    assert "x.png" in joined and joined.count("- [") == 1  # blank-ref row dropped


def test_parse_steps_sentinel_roundtrip():
    parsed = pw.parse_steps_sentinel(
        '{"summary":"warm","steps":[{"kind":"brief","title":"Brief","objective":"intent"},'
        '{"kind":"build_plan","title":"Build plan","objective":"phases"}]}')
    assert parsed is not None
    summary, steps = parsed
    assert summary == "warm" and [s["kind"] for s in steps] == ["brief", "build_plan"]


def test_build_plan_to_phases_projection():
    phases = pw.build_plan_to_phases({"phases": [
        {"step": "foundations", "title": "Foundations", "objective": "anchors"},
        {"title": "Document & export", "objective": "DESIGN.md"},  # step derived from title
    ]})
    assert phases[0]["step"] == "foundations"
    assert phases[1]["step"] == "document_export" and phases[1]["title"] == "Document & export"


def test_build_plan_to_phases_empty_on_garbage():
    assert pw.build_plan_to_phases({}) == []
    assert pw.build_plan_to_phases({"phases": "nope"}) == []


def test_token_step_contract_emits_token_overrides():
    # D3: foundations/palette/typography artifacts carry a machine-readable
    # token_overrides patch the walkthrough merges + previews. Other kinds don't.
    for k in ("foundations", "palette", "typography"):
        c = pw._artifact_contract(k)
        assert "token_overrides" in c and "partial design-token document" in c.lower(), k
    for k in ("components", "build_plan", "brief"):
        assert "token_overrides" not in pw._artifact_contract(k), k
