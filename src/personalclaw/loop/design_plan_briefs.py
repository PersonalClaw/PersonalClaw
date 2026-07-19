"""Pure brief + parser + projection helpers for the DESIGN kind's stepwise planning
walkthrough — the dynamic design-pass step list, per-step briefs, the JSON parsers, and
the breakdown→plan projection. No store/orchestration deps (those live in
loop.plan_walkthrough); just the deterministic, unit-testable pieces the design
Walkthrough delegate wraps. Mirrors code_plan_briefs / goal_plan_briefs.

A Design loop IS a loop (the vision): Understand the design task → break it into phased
executions by design-space expertise → an execution plan → loop each phase. This module
drives the planning half (Understand → phased breakdown), walked one approvable step at
a time, exactly like goal/code — so a design loop is no longer the shallow "skip planning,
free-run" case it was before."""

from __future__ import annotations

import logging

from personalclaw.planning.session import PlanSession, PlanStep

logger = logging.getLogger(__name__)

# Distinct sentinels so a stale design-pass file is never mistaken for a step artifact.
STEPS_SENTINEL = "plan_steps.json"
ARTIFACT_SENTINEL = "step_artifact.json"

# Design step kinds that decide design TOKENS — their artifact carries a
# ``token_overrides`` patch (a partial token doc) the walkthrough merges onto the loop
# so the user sees + edits the whole-system preview before approving (D3).
_TOKEN_STEP_KINDS = frozenset({"foundations", "palette", "typography"})

# The canonical design-system phases (the per-kind "space expertise" breakdown). The
# planner tailors this to the task — picking the subset + ordering that fits, inventing
# a phase kind where the task needs one — but this is the spine it works from. The LAST
# step is `build_plan` (the executable phased breakdown the loop then loops over).
STEP_KIND_GUIDE: tuple[tuple[str, str], ...] = (
    ("brief", "the design intent: product/brand, audience, mood/voice, hard constraints, accessibility targets, and what 'done' means"),
    ("foundations", "the foundational token decisions — which default axes to override (color anchors, type families, spacing/radius rhythm) and why"),
    ("palette", "the color system — brand/accent/neutral + semantic role scales, light/dark, WCAG-contrast intent"),
    ("typography", "the type + spacing system — families, the modular size scale, weights, the spacing/radius rhythm"),
    ("components", "the core component set to generate (buttons, inputs, cards, …) and how each is styled from the tokens"),
    ("build_plan", "the ordered build phases — each a phase the design loop executes (foundations → palette → type → components → document & export)"),
)


def design_inputs_block(inputs: list[dict] | None) -> list[str]:
    """Render the user's multi-modal reference inputs as brief lines instructing the
    planner to work through each. ``inputs`` is ``kind_config.design_inputs`` —
    ``[{type, ref}]`` where type ∈ {url, image, video, html, react, design_md, …} and
    ref is a URL or a filename uploaded into the planner's cwd (the loop files dir).
    Returns [] when there are none (the planner just works from the prompt)."""
    rows = [i for i in (inputs or []) if isinstance(i, dict) and str(i.get("ref", "")).strip()]
    if not rows:
        return []
    # How to consume each input type — concrete so the planner actually extracts from it.
    how = {
        "url": "FETCH it (web fetch) and extract its palette, type, spacing + component primitives",
        "image": "READ the image file (it's in your current directory) — extract its color palette, type feel, and primitives",
        "video": "the file is in your current directory — sample its frames for palette + motion/feel cues",
        "html": "READ the HTML file in your current directory — extract its CSS tokens (colors, fonts, spacing) + components",
        "react": "READ the React component file in your current directory — extract its style tokens + the component's shape",
        "design_md": "READ this DESIGN.md in your current directory — it already encodes tokens/decisions; carry them forward",
    }
    out = ["", "REFERENCE INPUTS the user provided — WORK THROUGH EACH ONE (extract its "
           "design system into your plan, don't ignore any):"]
    for i in rows:
        t = str(i.get("type", "")).strip().lower()
        ref = str(i.get("ref", "")).strip()
        out.append(f"  - [{t or 'input'}] {ref} — {how.get(t, 'inspect it and extract any design cues')}")
    return out


def build_design_brief(task: str, workspace_dir: str = "", design_inputs: list[dict] | None = None) -> str:
    """Pass 1 — design the ordered step list for THIS design task.

    The planner studies the task + every provided reference input (URL / image / video /
    HTML / React / DESIGN.md), then emits only the steps the task needs, ordered, ending
    in `build_plan` (the executable phased breakdown). Mirrors code's dynamic design
    pass, design-flavored + multi-modal."""
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    guide = "\n".join(f"  - {k}: {desc}" for k, desc in STEP_KIND_GUIDE)
    # The reference-input lines lead with a blank-line separator (index 0); the template
    # owns that blank line via its {% if %}, so pass only the body.
    inputs_lines = design_inputs_block(design_inputs)
    rendered = render_use_case_prompt(
        "design_design_brief",
        {
            "task": task.strip(),
            "design_inputs_block": "\n".join(inputs_lines[1:]) if inputs_lines else "",
            "workspace_dir": workspace_dir.strip(),
            "guide": guide,
            "steps_sentinel": STEPS_SENTINEL,
        },
    )
    if rendered is not None:
        return rendered
    # Prompt store unavailable — fall back to the shipped brief (identical text).
    lines = [
        f"DESIGN TASK TO PLAN:\n{task.strip()}",
        "",
        "You are planning a DESIGN-SYSTEM build as a phased loop. You're designing the "
        "PLANNING WALKTHROUGH — the ordered set of steps we'll walk the user through, "
        "one at a time, each producing an artifact they approve before the next runs.",
        "",
        "FIRST, understand the design task concretely (so the steps fit THIS product, "
        "not a template): who it's for, the brand/mood, the surfaces it spans, and any "
        "hard constraints (accessibility targets, existing brand colors, platform).",
    ]
    lines += inputs_lines
    if workspace_dir.strip():
        lines += [
            f"  A workspace is bound at: {workspace_dir.strip()}",
            "  Read any existing design notes, brand assets, or DESIGN.md there to ground "
            "the system in what already exists.",
        ]
    lines += [
        "",
        "THEN decide which design phases this task needs and in what order. Standard "
        "phase kinds (pick the SUBSET that fits; you may add one with a snake_case slug):",
        guide,
        "",
        "Narrate what you considered as you go (your reasoning must be visible).",
        "",
        f"When ready, WRITE the step list as JSON to `{STEPS_SENTINEL}` in your current "
        "directory, with this exact shape:",
        "{",
        '  "summary": "<1-2 sentences: your read of the task + why these phases>",',
        '  "steps": [',
        '    {"kind":"<snake_case slug>", "title":"<short human title>", '
        '"objective":"<what this phase produces, specific to THIS product>"},',
        "    ...",
        "  ]",
        "}",
        "",
        "Order matters — each phase builds on the approved ones before it. The LAST step "
        "should be `build_plan` (the executable phased breakdown). This is a single "
        "design pass: once the file is written, you are DONE.",
    ]
    return "\n".join(lines)


def _approved_block(approved: list[PlanStep]) -> str:
    """The '  - [kind] title: summary' lines for the approved artifacts so far ("" when
    none) — the dynamic block the step brief drops under its APPROVED header."""
    return "\n".join(
        f"  - [{a.kind}] {a.title}: {_summarize_artifact(a.artifact)}" for a in approved
    )


def _comments_block(step: PlanStep) -> str:
    """The '  - text' lines for the user's re-draft comments on this step ("" when none)."""
    out: list[str] = []
    for c in step.comments:
        text = str(c.get("text", "")).strip() if isinstance(c, dict) else str(c).strip()
        if text:
            out.append(f"  - {text}")
    return "\n".join(out)


def build_step_brief(
    task: str,
    step: PlanStep,
    *,
    approved: list[PlanStep] | None = None,
    workspace_dir: str = "",
) -> str:
    """Pass 2 — produce the artifact for ONE design-planning step."""
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    approved = approved or []
    rendered = render_use_case_prompt(
        "design_step_brief",
        {
            "task": task.strip(),
            "step_title": step.title,
            "step_kind": step.kind,
            "objective": step.objective.strip(),
            "approved_block": _approved_block(approved),
            "comments_block": _comments_block(step),
            "workspace_dir": workspace_dir.strip(),
            "artifact_sentinel": ARTIFACT_SENTINEL,
            "artifact_contract": _artifact_contract(step.kind),
        },
    )
    if rendered is not None:
        return rendered
    # Prompt store unavailable — fall back to the shipped brief (identical text).
    lines = [
        f"OVERALL DESIGN TASK:\n{task.strip()}",
        "",
        f"CURRENT PLANNING STEP: {step.title}  (kind: {step.kind})",
    ]
    if step.objective.strip():
        lines.append(f"Objective: {step.objective.strip()}")
    if approved:
        lines += ["", "APPROVED ARTIFACTS SO FAR (build on these — stay consistent):"]
        for a in approved:
            lines.append(f"  - [{a.kind}] {a.title}: {_summarize_artifact(a.artifact)}")
    if step.comments:
        lines += ["", "THE USER COMMENTED ON YOUR LAST DRAFT OF THIS STEP — address every point:"]
        for c in step.comments:
            text = str(c.get("text", "")).strip() if isinstance(c, dict) else str(c).strip()
            if text:
                lines.append(f"  - {text}")
    if workspace_dir.strip():
        lines += ["", f"Workspace (read it as needed): {workspace_dir.strip()}"]
    lines += [
        "",
        "Produce THIS STEP'S ARTIFACT as JSON written to "
        f"`{ARTIFACT_SENTINEL}` in your current directory.",
        "",
        _artifact_contract(step.kind),
        "",
        "Ground every choice in the actual product — no filler. This is a single pass "
        "for THIS step: once the file is written, you are DONE (the user reviews it next).",
    ]
    return "\n".join(lines)


def _artifact_contract(kind: str) -> str:
    """The expected artifact JSON for a design-planning step kind. `build_plan` has a
    strict shape (it projects into the executable phase plan); other kinds are a
    markdown view + key_points (+ any structured fields that fit)."""
    if kind == "build_plan":
        return (
            "For `build_plan`, use this exact shape (it becomes the executable design "
            "loop plan):\n"
            "{\n"
            '  "markdown": "<human-readable summary of the build phases>",\n'
            '  "phases": [\n'
            '    {"step":"<snake_case slug, e.g. foundations|palette|typography|'
            'components|export>", "title":"<phase title>", '
            '"objective":"<what this phase produces for THIS product>"}\n'
            "  ]\n"
            "}\n"
            "Phases run in order — the design loop works each until its goal is met, "
            "then advances. End with a document & export phase (DESIGN.md + token/React "
            "exports)."
        )
    if kind in _TOKEN_STEP_KINDS:
        # Token-bearing steps emit a MACHINE-READABLE token_overrides patch (a partial
        # design-token document) so the walkthrough can merge it onto the loop, render a
        # live whole-system preview, and let the user edit the values before approving.
        axis = {
            "foundations": "the foundational anchors you're overriding — e.g. color.primitive.brand.500, "
                           "typography.family.sans, radius.md, spacing rhythm",
            "palette": "the color system — color.primitive.<brand|accent|neutral>.<step> hexes "
                       "and any color.semantic.<light|dark>.<role> values",
            "typography": "type + spacing — typography.family.*, typography.size.*, typography.weight.*, "
                          "spacing.*, radius.*",
        }.get(kind, "the tokens this step decides")
        return (
            f"For `{kind}`, write a JSON object with:\n"
            '  "markdown": "<the artifact as readable markdown — the primary view>",\n'
            '  "key_points": ["<the few load-bearing decisions>", ...],\n'
            '  "token_overrides": { <a PARTIAL design-token document — only the paths you '
            "are setting, nested exactly like the default token set> }\n"
            f"Set {axis}. Use real values (hex colors, rem/px sizes), nested by token path "
            '(e.g. {"color":{"primitive":{"brand":{"500":"#d65f2e"}}},"radius":{"md":"0.5rem"}}). '
            "These merge onto PersonalClaw's default tokens — for NAMED LEAF tokens (a hex, a "
            "single radius/family/duration) set ONLY what you're changing. "
            "BUT for an ORDERED MAGNITUDE SCALE whose numeric KEY encodes its value — "
            "spacing.* (default: 1=0.25rem, 2=0.5rem, 3=0.75rem, 4=1rem … a 4px grid) and "
            "typography.size.* — you must NOT set a partial subset of keys: a partial patch "
            "deep-merges onto the default keys, and if your numbering convention differs from "
            "the default's the merged scale becomes non-monotonic and self-contradicting "
            "(e.g. your 4=0.5rem landing next to the default 3=0.75rem, so step 3 > step 4). "
            "For such a scale, EITHER omit it entirely to inherit the default unchanged, OR "
            "redefine the FULL scale yourself using one consistent convention across every key. "
            "Ground all values in the reference inputs (the fetched site / image palette / DESIGN.md)."
        )
    return (
        f"For `{kind}`, write a JSON object with:\n"
        '  "markdown": "<the artifact as readable markdown — the primary view>",\n'
        '  "key_points": ["<the few load-bearing decisions>", ...]\n'
        "Add any structured fields that fit this kind (e.g. components → "
        '"components":[...]).'
    )


def _summarize_artifact(artifact: dict) -> str:
    if not isinstance(artifact, dict):
        return ""
    md = str(artifact.get("markdown", "")).strip()
    if md:
        first = md.splitlines()[0].strip()
        return (first[:160] + "…") if len(first) > 160 else first
    kp = artifact.get("key_points")
    if isinstance(kp, list) and kp:
        return "; ".join(str(p).strip() for p in kp[:3])[:160]
    return ""


def parse_steps_sentinel(text: str) -> tuple[str, list[dict]] | None:
    """Parse pass-1 output → ``(summary, [{kind,title,objective}, ...])`` or None."""
    from personalclaw.loop.code_classify import _parse_obj

    data = _parse_obj(text or "")
    if not isinstance(data, dict):
        return None
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list):
        return None
    steps: list[dict] = []
    for s in raw_steps:
        if not isinstance(s, dict):
            continue
        kind = _slug(s.get("kind"))
        title = str(s.get("title", "")).strip()[:120]
        if not (kind or title):
            continue
        steps.append({
            "kind": kind or "step",
            "title": title or kind.replace("_", " ").title(),
            "objective": str(s.get("objective", "")).strip()[:400],
        })
    if not steps:
        return None
    summary = str(data.get("summary", "")).strip()[:300]
    return summary, steps[:8]  # a sane design walkthrough isn't dozens of steps


def parse_artifact_sentinel(text: str) -> dict | None:
    """Parse pass-2 output → the step's artifact dict, or None if nothing parseable."""
    from personalclaw.loop.code_classify import _parse_obj

    data = _parse_obj(text or "")
    if not isinstance(data, dict):
        return None
    if "markdown" in data and not isinstance(data["markdown"], str):
        data["markdown"] = str(data["markdown"])
    return data


def build_plan_to_phases(artifact: dict) -> list[dict]:
    """Project an approved `build_plan` artifact's ``phases`` into the design loop's
    ``plan`` shape ({step,title,objective} — design.phase_key reads step→title). Empty
    if none. The shape the design strategy's on_new_cycle + cockpit already consume."""
    if not isinstance(artifact, dict):
        return []
    phases = artifact.get("phases")
    if not isinstance(phases, list):
        return []
    out: list[dict] = []
    for p in phases:
        if not isinstance(p, dict):
            continue
        step = _slug(p.get("step")) or _slug(p.get("title"))
        title = str(p.get("title", "")).strip() or step.replace("_", " ").title()
        if not (step or title):
            continue
        out.append({
            "step": step or title.lower().replace(" ", "_"),
            "title": title,
            "objective": str(p.get("objective", "")).strip(),
        })
    return out


def collect_token_overrides(steps) -> dict:
    """Deep-merge the ``token_overrides`` patches from every approved token-step
    (foundations/palette/typography), in step order, into one override document — the
    AUTHORITATIVE approved design system. project_to_spec merges this into the loop's
    kind_config.token_overrides on finalize, so the cockpit opens populated with the
    approved system regardless of whether the FE previewed each step (D3 merges
    client-side as a live convenience; this is the server-side guarantee for D4)."""
    from personalclaw.loop import design_tokens as dt
    out: dict = {}
    for s in steps:
        if getattr(s, "kind", "") not in _TOKEN_STEP_KINDS:
            continue
        ov = (getattr(s, "artifact", None) or {}).get("token_overrides")
        if isinstance(ov, dict) and ov:
            out = dt.deep_merge(out, ov)
    return out


def _slug(raw) -> str:
    """Coerce a value into a short snake_case slug, or '' if unusable."""
    s = str(raw or "").strip().lower()
    out = [ch if ch.isalnum() else "_" for ch in s if ch.isalnum() or ch in (" ", "-", "_")]
    slug = "".join(out).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug[:40]
