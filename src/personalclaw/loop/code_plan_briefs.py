"""Pure brief + parser + projection helpers for the CODE kind's stepwise planning
walkthrough — the dynamic design-pass step list, per-step briefs, the JSON parsers, and
the decomposition→stage_plan projection. No store/orchestration deps (those live in
loop.plan_walkthrough); just the deterministic pieces the code Walkthrough delegate
wraps. Lives in the unified loop package so the code kind doesn't reach into legacy
code/ (cutover Slice 2e). Legacy code.plan_walkthrough re-exports these until deletion."""

from __future__ import annotations

import logging

from personalclaw.planning.session import PlanSession, PlanStep

logger = logging.getLogger(__name__)

# so a stale design file is never mistaken for a step artifact.
STEPS_SENTINEL = "plan_steps.json"
ARTIFACT_SENTINEL = "step_artifact.json"

# Illustrative SDLC step kinds + the artifact each produces. The planner picks the
# subset (and ordering) the target needs — this is guidance, not an enum. Covers a
# full staged flow: framing → requirements → logical/physical design → units of
# work → operate (lighter targets collapse to shape → breakdown → ship).
STEP_KIND_GUIDE: tuple[tuple[str, str], ...] = (
    ("problem_framing", "problem statement, intent, success signals"),
    ("requirements", "user stories / acceptance criteria (or a BRD digest)"),
    ("context_map", "entities, boundaries, and an existing-code map (brownfield)"),
    ("design", "architecture, interfaces, data shapes, key decisions (ADRs)"),
    ("decomposition", "ordered phases → tasks with dependencies + exit criteria"),
    ("test_strategy", "what proves it works, per phase"),
    ("hardening", "prod-hardening / regression / polish phases the target needs"),
)


def build_design_brief(task: str, workspace_dir: str = "") -> str:
    """Pass 1 — design the ordered step list for THIS target.

    The planner investigates first, then emits ONLY the steps the target needs
    (skip what doesn't apply: a bugfix may need just context_map + decomposition).
    """
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    guide = "\n".join(f"  - {k}: {desc}" for k, desc in STEP_KIND_GUIDE)
    rendered = render_use_case_prompt(
        "code_design_brief",
        {
            "task": task.strip(),
            "workspace_dir": workspace_dir.strip(),
            "guide": guide,
            "steps_sentinel": STEPS_SENTINEL,
        },
    )
    if rendered is not None:
        return rendered
    # Prompt store unavailable — fall back to the shipped brief (identical text).
    lines = [
        f"TASK TO PLAN:\n{task.strip()}",
        "",
        "You are designing the PLANNING WALKTHROUGH for this task — the ordered set "
        "of steps we'll walk the user through, one at a time, each producing an "
        "artifact the user approves before the next step runs.",
        "",
        "FIRST, investigate the real context (so the steps fit reality, not a "
        "template):",
    ]
    if workspace_dir.strip():
        lines += [
            f"  A workspace is bound at: {workspace_dir.strip()}",
            "  Read its key files (READMEs, plans/ or docs/, ROADMAP/BACKLOG, config, "
            "AGENTS.md) to learn the conventions + the SPECIFIC items this targets.",
        ]
    else:
        lines += [
            "  No local workspace. Gather context from where the task points — "
            "internal docs/wikis/tickets via MCP, web/code search — whatever you have.",
        ]
    lines += [
        "",
        "THEN decide which steps this target needs and in what order. Standard step "
        "kinds (pick the SUBSET that fits — skip any that don't apply):",
        guide,
        "  (You may also invent a step kind if the target needs one — use a short "
        "snake_case slug.)",
        "",
        "Narrate what you read/found as you go (your investigation must be visible).",
        "",
        f"When ready, WRITE the step list as JSON to `{STEPS_SENTINEL}` in your current "
        "directory, with this exact shape:",
        "{",
        '  "summary": "<1-2 sentences: what you found + why these steps>",',
        '  "steps": [',
        '    {"kind":"<snake_case slug>", "title":"<short human title>", '
        '"objective":"<what this step produces, referencing REAL discovered items>"},',
        "    ...",
        "  ]",
        "}",
        "",
        "Order matters — each step builds on the approved artifacts before it. The "
        "LAST step should be `decomposition` (the executable phase/task breakdown). "
        "This is a single design pass: once the file is written, you are DONE.",
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
    """Pass 2 — produce the artifact for ONE step.

    Carries the overall task, the prior approved artifacts (so the step builds on
    them), and any user comments on this step (re-draft feedback). The planner
    emits the artifact JSON for this step only.
    """
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    approved = approved or []
    rendered = render_use_case_prompt(
        "code_step_brief",
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
        f"OVERALL TASK:\n{task.strip()}",
        "",
        f"CURRENT PLANNING STEP: {step.title}  (kind: {step.kind})",
    ]
    if step.objective.strip():
        lines.append(f"Objective: {step.objective.strip()}")
    if approved:
        lines += [
            "",
            "APPROVED ARTIFACTS SO FAR (build on these — stay consistent with them):",
        ]
        for a in approved:
            lines.append(f"  - [{a.kind}] {a.title}: {_summarize_artifact(a.artifact)}")
    if step.comments:
        lines += [
            "",
            "THE USER COMMENTED ON YOUR LAST DRAFT OF THIS STEP — address every point:",
        ]
        for c in step.comments:
            text = str(c.get("text", "")).strip() if isinstance(c, dict) else str(c).strip()
            if text:
                lines.append(f"  - {text}")
    if workspace_dir.strip():
        lines += ["", f"Workspace (read it as needed): {workspace_dir.strip()}"]
    lines += [
        "",
        "Investigate anything you still need, then PRODUCE THIS STEP'S ARTIFACT as "
        f"JSON written to `{ARTIFACT_SENTINEL}` in your current directory.",
        "",
        _artifact_contract(step.kind),
        "",
        "Ground every claim in real, discovered detail — no placeholders. Narrate "
        "your investigation. This is a single pass for THIS step: once the file is "
        "written, you are DONE (the user reviews it next).",
    ]
    return "\n".join(lines)


def _artifact_contract(kind: str) -> str:
    """The expected artifact JSON shape for a step kind. Decomposition has a strict
    shape (it projects into execution phases/tasks); other kinds are structured but
    flexible — always a JSON object with a ``markdown`` field for the human view
    plus kind-specific structured fields the FE can render."""
    if kind == "decomposition":
        return (
            "For `decomposition`, use this exact shape (it becomes the executable "
            "plan):\n"
            "{\n"
            '  "markdown": "<human-readable summary of the breakdown>",\n'
            '  "phases": [\n'
            '    {"stage":"<canonical: implementation|verification|review|...>", '
            '"title":"<phase title>", "objective":"<what this phase achieves>", '
            '"exit_criteria":["<checkable condition>", ...], '
            '"tasks":[{"title":"<task naming a REAL file/item>", '
            '"description":"<how>", "depends_on":[<indexes of tasks in THIS phase '
            'this one needs>]}, ...]}\n'
            "  ]\n"
            "}\n"
            "Phases run in order (a phase starts only when the prior is fully done); "
            "tasks within a phase run in parallel as their dependencies clear."
        )
    return (
        f"For `{kind}`, write a JSON object with:\n"
        '  "markdown": "<the artifact as readable markdown — the primary view>",\n'
        '  "key_points": ["<the few load-bearing conclusions>", ...]\n'
        "Add any extra structured fields that fit this kind (e.g. requirements → "
        '"stories":[...]; design → "decisions":[...]; context_map → "entities":[...]).'
    )


def _summarize_artifact(artifact: dict) -> str:
    """A one-line digest of an approved artifact for the step brief context."""
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
    """Parse pass-1 output → ``(summary, [{kind,title,objective}, ...])`` or None.

    Tolerates code-fenced / prose-wrapped JSON (reuses the deep_plan parser). A
    step needs at least a ``kind`` or a ``title`` to count; blank entries drop.
    """
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
    return summary, steps[:12]  # cap — a sane walkthrough isn't dozens of steps


def parse_artifact_sentinel(text: str) -> dict | None:
    """Parse pass-2 output → the step's artifact dict, or None if nothing parseable.

    Always returns a dict with at least a ``markdown`` string when parseable, so
    the FE has a human view even if the model omitted structured fields.
    """
    from personalclaw.loop.code_classify import _parse_obj

    data = _parse_obj(text or "")
    if not isinstance(data, dict):
        return None
    # Normalize: ensure markdown is a string; keep all other fields as-authored.
    if "markdown" in data and not isinstance(data["markdown"], str):
        data["markdown"] = str(data["markdown"])
    return data


def seed_steps(session: PlanSession, steps: list[dict]) -> PlanSession:
    """Populate a session's ordered steps from a parsed design pass. Assigns stable
    ids (``step-0``, ``step-1``, …). Replaces any existing steps (the design pass is
    authoritative for the step list)."""
    session.steps = [
        PlanStep(
            id=f"step-{i}",
            kind=str(s.get("kind", "step")),
            title=str(s.get("title", "")) or f"Step {i + 1}",
            objective=str(s.get("objective", "")),
        )
        for i, s in enumerate(steps)
    ]
    return session


def decomposition_to_stage_plan(artifact: dict) -> list[dict]:
    """Project an approved `decomposition` artifact's ``phases`` into the engine's
    ``stage_plan`` shape (so execution seeds phase TaskLists from it). Returns the
    raw phase list for the store/normalizer to validate; empty if none."""
    if not isinstance(artifact, dict):
        return []
    phases = artifact.get("phases")
    if not isinstance(phases, list):
        return []
    out: list[dict] = []
    # Disambiguate repeated canonical stage ids. A decomposition routinely gives SEVERAL
    # phases the SAME stage (e.g. 5 "implementation" phases P1..P5 with distinct titles);
    # downstream keys a phase by its stage id (phase_key), so duplicates collapse to ONE
    # key — active_stage_index never advances past the first, the worker is told
    # "stage 1/N" forever and grinds the first phase to budget (observed: code loop stuck
    # on P1 Scaffold for cycles). Suffix the 2nd+ occurrence (`implementation-2`, …) so
    # each approved phase keeps a distinct stage id while staying recognizably its stage.
    _stage_seen: dict[str, int] = {}
    for p in phases:
        if not isinstance(p, dict):
            continue
        # Do NOT force a blank stage id to "implementation": multiple phases without
        # an explicit stage would then ALL become "implementation" and the store's
        # dedupe_stage_plan (keys by stage id) would collapse the user-approved plan
        # to a single stage. Leave it blank — a stageless row with an objective is
        # kept as-is and keyed by its title downstream (provision/seed/gating all use
        # `stage_id or title`), so every approved phase survives as its own stage.
        # A bare-STRING exit_criteria (the planner emitting "all tests pass" instead
        # of ["all tests pass"] — a common LLM shape) must be WRAPPED before iterating:
        # `for c in "all tests pass"` walks CHARACTERS, shredding it into 12 single-
        # letter criteria that then surface in the user-approved plan + the cockpit
        # "Done when" list + seeded task exit_criteria. The store's dedupe_stage_plan
        # has this same guard, but it runs AFTER this projection — by then the string
        # is already a char list, so its wrap never fires. Guard here at the source.
        ec = p.get("exit_criteria")
        if isinstance(ec, str):
            ec = [ec]
        elif not isinstance(ec, list):
            ec = []
        stage = str(p.get("stage", "")).strip()
        if stage:
            n = _stage_seen.get(stage, 0) + 1
            _stage_seen[stage] = n
            if n > 1:
                stage = f"{stage}-{n}"  # keep distinct: implementation, implementation-2, …
        out.append({
            "stage": stage,
            "title": str(p.get("title", "")).strip(),
            "objective": str(p.get("objective", "")).strip(),
            "exit_criteria": [str(c).strip() for c in ec if str(c).strip()],
            "tasks": p.get("tasks") if isinstance(p.get("tasks"), list) else [],
        })
    return out


def gate_commands_from_test_strategy(artifact: dict) -> tuple[str, str]:
    """Derive ``(verify_command, test_command)`` from an approved ``test_strategy``
    artifact's structured ``ci_gate`` — so the SDLC stage gate has a DETERMINISTIC
    check (run the build/CI chain + the test/coverage runner) instead of resting
    entirely on the LLM judge. Returns ``("", "")`` if nothing usable is present.

    Why this exists: ``_stage_gate_passed`` builds its deterministic ``checks`` from
    ``kind_config.verify_command``/``test_command``; with both empty the gate is
    judge-ONLY and the flaky-judge bridge can't fire (it needs a passed command to
    stand on). A *verification* stage's exit criteria are precisely test-execution
    claims ("coverage 100%", "never-loses passes") a conservative judge won't accept
    on transcript alone — so a judge-only verification stage can never advance even
    when the real CI is green (observed live: a fully-tested engine stage stuck for
    cycles). The planner already authored the exact commands in ``ci_gate``; this
    lifts them into the gate.

    ``ci_gate`` shape (planner-authored): ``[{"order":N,"step":"typecheck|lint|
    coverage|build|test|...","cmd":"<shell>"}, ...]``. Mapping:
    - ``test_command`` ← the test/coverage step's cmd (the gate runs it on
      ``verification`` stages; it's the never-loses + coverage proof).
    - ``verify_command`` ← the remaining gate steps (typecheck/lint/build) chained
      with ``&&`` in ``order`` — the build-soundness check the gate runs on EVERY
      stage. (We keep test out of verify_command so the heavier exhaustive suite only
      gates the verification stages, matching the planner's intent + the engine's
      ``stage == "verification"`` test branch.)
    """
    if not isinstance(artifact, dict):
        return "", ""
    gate = artifact.get("ci_gate")
    if not isinstance(gate, list):
        return "", ""
    steps: list[tuple[int, str, str]] = []
    for i, g in enumerate(gate):
        if not isinstance(g, dict):
            continue
        cmd = str(g.get("cmd", "")).strip()
        if not cmd:
            continue
        step = str(g.get("step", "")).strip().lower()
        order = g.get("order")
        order = order if isinstance(order, int) else i
        steps.append((order, step, cmd))
    if not steps:
        return "", ""
    steps.sort(key=lambda t: t[0])
    # A step is a "test" run if its label or command names the test/coverage runner.
    def _is_test(step: str, cmd: str) -> bool:
        hay = f"{step} {cmd}".lower()
        return any(k in hay for k in ("coverage", "vitest", "test", "jest", "pytest"))
    test_cmds = [c for _, s, c in steps if _is_test(s, c)]
    verify_cmds = [c for _, s, c in steps if not _is_test(s, c)]
    test_command = test_cmds[0] if test_cmds else ""
    # Prefer a single declared CI umbrella if the planner gave one; else chain the
    # non-test gate steps in order (typecheck && lint && build).
    verify_command = " && ".join(verify_cmds) if verify_cmds else ""
    return verify_command, test_command


def _slug(raw) -> str:
    """Coerce a value into a short snake_case slug (kind), or '' if unusable."""
    s = str(raw or "").strip().lower()
    out = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_"):
            out.append("_")
    slug = "".join(out).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug[:40]

