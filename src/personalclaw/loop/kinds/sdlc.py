"""Code kind — SDLC stage-gated work in a workspace (the mini-IDE cockpit).

The task is classified to an SDLC entry stage; the plan is an ordered set of
stages each gated by explicit exit criteria (the supervisor runs the project's
``verify_command``/``test_command`` and a conservative judge over recent
findings — never the worker's self-report). Module named ``sdlc`` to avoid
shadowing the stdlib ``code`` module. Slice 1 supplies config + phase keying; the
full stage-gate + worktree behavior ports from the legacy ``code/`` engine in
Slice 2.
"""

from __future__ import annotations

import logging
import time

from personalclaw.loop.kinds import register
from personalclaw.loop.loop import Loop, LoopStatus

logger = logging.getLogger(__name__)


_POOL_CAP = 4              # max concurrent task-workers per loop
_CONFLICT_REDO_CAP = 2     # auto-resolve a task's merge conflict at most this many times
_STALL_FINDINGS = 5        # a stage grinding this many findings w/o clearing its gate is "stuck"

# Manifests that signal a workspace is buildable by a given toolchain. A verify/test
# command only gates a stage once the project it drives actually exists — else a
# planning/pre-scaffold stage hard-fails the gate (e.g. `npm run build` → ENOENT 254).
_BUILD_MANIFESTS: dict[str, tuple[str, ...]] = {
    # token found in the command → manifest file(s) whose presence means it can run
    "npm": ("package.json",), "pnpm": ("package.json",), "yarn": ("package.json",),
    "node": ("package.json",), "vite": ("package.json",), "tsc": ("package.json", "tsconfig.json"),
    "eslint": ("package.json",), "vitest": ("package.json",), "jest": ("package.json",),
    "cargo": ("Cargo.toml",), "go": ("go.mod",),
    "pytest": ("pyproject.toml", "setup.py", "setup.cfg", "tox.ini"),
    "python": ("pyproject.toml", "setup.py"), "ruff": ("pyproject.toml", "setup.cfg"),
    "make": ("Makefile", "makefile"), "gradle": ("build.gradle", "build.gradle.kts"),
    "mvn": ("pom.xml",), "maven": ("pom.xml",),
}


def _command_runnable_here(cmd: str, workspace_dir: str) -> bool:
    """Whether a verify/test command can MEANINGFULLY run in the workspace yet — i.e.
    the toolchain it invokes has its project manifest present. Returns True when we
    don't recognize the toolchain (don't suppress an unknown command — let it run and
    report its real exit code) or no workspace is bound (the runner handles cwd=None).
    Returns False only when a recognized toolchain's manifest is absent — meaning a
    planning/pre-scaffold stage where running the command would just ENOENT-fail.

    This is what makes the gate STAGE-APPROPRIATE without hard-coding stage names: the
    same `verify_command` simply doesn't gate a stage whose project isn't built yet,
    and starts gating once the scaffold stage creates the manifest."""
    import os
    cmd = (cmd or "").strip().lower()
    ws = (workspace_dir or "").strip()
    if not cmd or not ws:
        return True
    manifests: tuple[str, ...] = ()
    for token, mans in _BUILD_MANIFESTS.items():
        # word-ish match: the toolchain token appears as a command word
        if token in cmd.split() or any(seg.strip().startswith(token + " ") or seg.strip() == token
                                       for seg in cmd.replace("&&", ";").replace("||", ";").split(";")):
            manifests = mans
            break
    if not manifests:
        return True  # unrecognized toolchain → let it run (don't second-guess)
    return any(os.path.isfile(os.path.join(ws, m)) for m in manifests)


class CodeKind:
    kind = "code"
    label = "Code"
    description = "SDLC work in a codebase — staged plan, gated execution, mini-IDE."
    wants_workspace = True
    default_agent = "personalclaw-coder"
    provisions_tasks = True   # task-driven: stages → per-stage TaskLists + seeded tasks at launch

    def __init__(self) -> None:
        # Per-(loop:task) merge-conflict auto-resolve budget (in-memory; the loser
        # rebases up to _CONFLICT_REDO_CAP times before pausing for the user).
        self._conflict_redos: dict[str, int] = {}
        # "<loop>:<stage>" keys already escalated as stalled (one-shot per stuck stage).
        self._stall_notified: set[str] = set()
        # "<loop>:<stage>" → the resolved-task count observed the last time the stall
        # check ran for that stage. A stage that keeps RESOLVING tasks (model→store→
        # service→cli, one per cycle) is making real forward progress, not spinning —
        # so the stall counter resets on progress rather than escalating a working loop
        # (observed: a multi-module build tripped the 5-finding spin cap while every
        # cycle actually completed + committed a task).
        self._stall_progress: dict[str, int] = {}

    def _is_parallel(self, loop: Loop) -> bool:
        """Parallel mode: queued work + a git workspace (worktrees available). Set
        PERSONALCLAW_CODE_PARALLEL=0 to force sequential everywhere (escape hatch)."""
        import os
        if os.environ.get("PERSONALCLAW_CODE_PARALLEL") == "0":
            return False
        if not (loop.kind_config or {}).get("queued_task_ids"):
            return False
        from personalclaw.loop import worktree
        ws = (loop.workspace_dir or "").strip()
        return bool(ws) and worktree.can_parallelize(ws)

    def _live_task_workers(self, loop: Loop, svc) -> list[str]:
        """Queued task ids that currently have an armed worker loop (occupying a pool
        slot) — loop existence, not session.running (a worker idles between cycles)."""
        from personalclaw.loop.manager import task_session_key
        return [tid for tid in ((loop.kind_config or {}).get("queued_task_ids", []) or [])
                if svc.get_by_session(task_session_key(loop.id, tid)) is not None]

    def default_kind_config(self) -> dict:
        return {
            "entry_stage": "ideation",
            "project_kind": "greenfield",   # greenfield | brownfield
            "verify_command": "",
            "test_command": "",
            "queued_task_ids": [],
        }

    def validate_config(self, config: dict) -> tuple[list[str], list[str]]:
        """Code-kind pre-flight: entry_stage/project_kind validity, brownfield needs a
        workspace, and screening the unattended verify/test commands. Returns
        (errors, warnings) folded into the shared validator's result."""
        from personalclaw.loop.sdlc_meta import ENTRY_STAGES, PROJECT_KINDS
        from personalclaw.security import audit_bash_command
        errors: list[str] = []
        warnings: list[str] = []
        cfg = config.get("kind_config") if isinstance(config.get("kind_config"), dict) else config
        entry_stage = (str(cfg.get("entry_stage", "ideation")).strip() or "ideation").lower()
        if entry_stage not in ENTRY_STAGES:
            errors.append(f"Unknown entry stage {entry_stage!r}.")
        project_kind = (str(cfg.get("project_kind", "greenfield")).strip() or "greenfield").lower()
        if project_kind not in PROJECT_KINDS:
            errors.append(f"Unknown project kind {project_kind!r}.")
        if project_kind == "brownfield" and not str(config.get("workspace_dir") or "").strip():
            warnings.append("Brownfield project — pick the codebase workspace before starting.")
        for key, label in (("verify_command", "Verify"), ("test_command", "Test")):
            cmd = str(cfg.get(key) or "").strip()
            if cmd:
                danger = audit_bash_command(cmd)
                if danger:
                    errors.append(f"{label} command rejected — {danger}.")
        # Stages collide when they share an effective key (stage id, or title for a
        # stageless row) — the store keys one TaskList + status entry per key at launch,
        # so a duplicate is SILENTLY dropped, losing a stage the plan author shaped. The
        # FE Plan Review warns + blocks on this, but a chat-tool / direct-API create
        # bypasses that surface — warn here (the shared validator path) so the dropped
        # stage isn't a silent surprise. A warning, not an error (matches the FE: launch
        # still proceeds, keeping the first of each key).
        plan = config.get("plan")
        if isinstance(plan, list):
            seen: set[str] = set()
            dups: list[str] = []
            for ph in plan:
                if not isinstance(ph, dict):
                    continue
                k = self.phase_key(ph).lower()
                if not k:
                    continue
                if k in seen:
                    dups.append(self.phase_key(ph))
                else:
                    seen.add(k)
            if dups:
                uniq = list(dict.fromkeys(dups))
                warnings.append(
                    f"Duplicate stage{'s' if len(uniq) > 1 else ''} ({', '.join(uniq)}) — "
                    "only the first of each is kept at launch; give each a distinct type or title.")
        return errors, warnings

    def launch_blocker(self, loop: Loop) -> str | None:
        """A brownfield code loop can't run without a bound workspace that EXISTS on
        disk (the codebase it changes) — the worker would otherwise launch into an
        empty project files dir, or start() would silently recreate a deleted dir
        empty and run against nothing. Greenfield provisions its own fresh dir, so
        it's fine. Returns a user-facing reason to block ``start``, or None to allow.
        Ported from the legacy launch-time ``require_workspace=True`` re-validation +
        the reaper's workspace-existence guard."""
        import os
        if str((loop.kind_config or {}).get("project_kind", "greenfield")) != "brownfield":
            return None
        ws = (loop.workspace_dir or "").strip()
        if not ws:
            return "This brownfield code loop needs a workspace — pick the codebase directory before starting."
        if not os.path.isdir(ws):
            return f"The workspace folder {ws!r} is missing (moved or deleted) — re-pick the codebase directory."
        return None

    def phase_key(self, phase: dict) -> str:
        # A code phase is keyed by its SDLC stage id, falling back to title for a
        # stageless row — matching the legacy store's `_stage_of` keying exactly.
        return (str(phase.get("stage", "")).strip() or str(phase.get("title", "")).strip())

    async def is_done_signal(self, loop: Loop, findings: list[dict]) -> bool | None:
        # The full code signal is "every stage's gate passed" (verify/test command
        # exit codes + a conservative judge over exit criteria) — the multi-stage
        # gate orchestration lands in 2c.iii. None = defer for now.
        return None

    def active_stage_index(self, loop: Loop) -> int:
        """Index of the stage the UPCOMING cycle belongs to — the first not-done
        stage, staying on the last once all are done; -1 with no plan. Keyed by the
        SAME stage-or-title key the rest of the engine writes with (phase_key)."""
        plan = loop.plan or []
        if not plan:
            return -1
        status = loop.phase_status or {}
        for i, phase in enumerate(plan):
            if status.get(self.phase_key(phase)) != "done":
                return i
        return len(plan) - 1

    def turn_capabilities(self, loop: Loop) -> tuple[list[str], list[str]]:
        """Skills + workflows for the UPCOMING cycle: the ACTIVE stage's per-stage ids
        ∪ the always-on baseline (loop.skill_ids/workflow_ids). Bare baseline with no
        plan. Order-stable, deduped (baseline first)."""
        skills = list(loop.skill_ids or [])
        workflows = list(loop.workflow_ids or [])
        idx = self.active_stage_index(loop)
        plan = loop.plan or []
        if 0 <= idx < len(plan):
            stage = plan[idx]
            for sid in (stage.get("skill_ids") or []):
                if sid not in skills:
                    skills.append(str(sid))
            for wid in (stage.get("workflow_ids") or []):
                if wid not in workflows:
                    workflows.append(str(wid))
        return skills, workflows

    def turn_directive(self, loop: Loop) -> str:
        """The active stage's directive, prepended to the cycle nudge (the generic name
        chat_runner calls across kinds — alias of stage_directive)."""
        return self.stage_directive(loop)

    def stage_directive(self, loop: Loop) -> str:
        """A one-block directive naming the CURRENT stage + objective + exit
        criteria, prepended to the cycle nudge. Empty when there's no plan.
        Ported faithfully from code.project.stage_directive."""
        plan = loop.plan or []
        idx = self.active_stage_index(loop)
        if idx < 0:
            return ""
        phase = plan[idx]
        stage = str(phase.get("stage", "")).strip()
        title = str(phase.get("title", "")).strip()
        objective = str(phase.get("objective", "")).strip()
        agent_name = str(phase.get("agent_name", "")).strip()
        exit_criteria = [str(c).strip() for c in (phase.get("exit_criteria") or []) if str(c).strip()]
        if not (stage or objective):
            return ""
        label = f"stage {idx + 1}/{len(plan)}" + (f" — {title or stage}" if (title or stage) else "")
        deliverable = str(phase.get("deliverable", "")).strip()
        from personalclaw.prompt_providers.runtime import render_snippet_block
        rendered = render_snippet_block("loop-code-stage-directive", {
            "label": label, "objective": objective,
            "criteria_joined": "; ".join(exit_criteria),
            "deliverable": deliverable, "agent_name": agent_name,
        })
        if rendered:
            return rendered
        body = f"You are in {label}."
        if objective:
            body += f" Objective: {objective}"
        if exit_criteria:
            body += " This stage is done when: " + "; ".join(exit_criteria) + "."
        if deliverable:
            body += f" Produce: {deliverable}."
        if agent_name:
            body += (
                f" Delegate this stage's work to the `{agent_name}` agent via "
                "subagent_run, then synthesize its result into your cycle finding."
            )
        return f"[Stage plan — {body}]"

    async def classify(self, task: str, ask, *, skills=None, workflows=None, agents=None) -> dict:
        """Wrap the SDLC classifier (entry stage / greenfield-vs-brownfield / stage
        plan / verify+test commands) and normalize: stage_plan → plan; entry_stage/
        project_kind/verify_command/test_command → kind_config."""
        from personalclaw.loop import code_classify as code_classify
        c = await code_classify.classify(task, ask, skills_catalog=skills,
                                         workflows_catalog=workflows, agents_catalog=agents)
        d = c.to_dict()
        return {
            "title": d.get("title", ""), "summary": d.get("summary", ""),
            "classified": d.get("classified", True),
            "intake_rigor": d.get("intake_rigor", "auto"), "execution": d.get("execution", "solo"),
            "roster": d.get("roster", []), "strategy_id": d.get("strategy_id", "orchestrator"),
            # The planner's rationale for its entry-stage / rigor picks — the Plan Review
            # surfaces them (RigorChip tooltip etc.) so the user sees WHY before approving.
            "entry_reason": d.get("entry_reason", ""), "rigor_reason": d.get("rigor_reason", ""),
            "clarifying_questions": d.get("clarifying_questions", []),
            "suggested_skill_ids": d.get("suggested_skill_ids", []),
            "suggested_workflow_ids": d.get("suggested_workflow_ids", []),
            "marketplace_suggestions": d.get("marketplace_suggestions", []),
            "success_criteria": d.get("success_criteria", ""),
            "plan": d.get("stage_plan", []),     # already phase-shaped (stage/title/exit_criteria/tasks)
            "kind_config": {
                "entry_stage": d.get("entry_stage", "ideation"),
                "project_kind": d.get("project_kind", "greenfield"),
                "verify_command": d.get("verify_command", ""),
                "test_command": d.get("test_command", ""),
                "queued_task_ids": [],
            },
        }

    def walkthrough(self):
        """The code stepwise planning walkthrough — a DYNAMIC step list (the planner
        designs the SDLC steps for THIS target first, then produces one artifact per
        step), projecting the approved decomposition into the unified ``plan``."""
        return _CodeWalkthrough()

    def build_brief(self, loop: Loop, context_dir: str = "") -> str:
        """Ported from code/manager.write_brief — the stage-plan/workspace/DoD brief.
        Pure: takes the resolved project ``context_dir`` instead of looking it up."""
        cfg = loop.kind_config or {}
        entry_stage = str(cfg.get("entry_stage", "ideation"))
        project_kind = str(cfg.get("project_kind", "greenfield"))
        verify_command = str(cfg.get("verify_command", "")).strip()
        test_command = str(cfg.get("test_command", "")).strip()
        lines = ["# Code Project Brief", "", f"**Task:** {loop.task}", ""]
        if loop.summary:
            lines += [f"**Summary:** {loop.summary}", ""]
        lines += [
            f"**Entry stage:** {entry_stage}",
            f"**Project kind:** {project_kind}",
            f"**Workspace:** {loop.workspace_dir or '(none — operate from the project files dir)'}",
            f"**Max cycles:** {loop.max_cycles}",
        ]
        if loop.workspace_dir:
            lines += ["",
                      "Work INSIDE the workspace directory above — that is the codebase. Read "
                      "before you write; verify after you edit."]
        if loop.plan:
            lines += ["",
                      "**Stage plan — work through these IN ORDER.** A stage is done when its "
                      "exit criteria are met; the supervisor advances you to the next stage. "
                      "Each stage tracks its work in its own TaskList (see below)."]
            for i, ph in enumerate(loop.plan):
                if not isinstance(ph, dict):
                    continue
                title = str(ph.get("title", "")).strip() or str(ph.get("stage", "")).strip() or "(stage)"
                objective = str(ph.get("objective", "")).strip()
                lines.append(f"{i+1}. **{title}** — {objective}" if objective else f"{i+1}. **{title}**")
                for crit in (ph.get("exit_criteria") or []):
                    lines.append(f"   - done when: {crit}")
                deliverable = str(ph.get("deliverable", "")).strip()
                if deliverable:
                    lines.append(f"   - deliverable: {deliverable}")
        if loop.task_list_ids:
            lines += ["",
                      "**Per-stage task tracking.** Each stage has a TaskList in the Tasks "
                      "system. Use the task tools to keep them honest: `task_list` to see the "
                      "current stage's tasks, `task_update {id, status}` to mark in_progress / "
                      "done as you work. Do not leave finished work marked open."]
        if context_dir:
            lines += ["",
                      f"**Project context dir:** `{context_dir}`",
                      "Shared, durable context for the PROJECT this work belongs to (other "
                      "loops on the same project share it). READ it for prior context at the "
                      "start; WRITE concise notes there (e.g. `context/decisions.md`, "
                      "`context/conventions.md`) when you make a durable decision or learn "
                      "something future work needs. High-signal long-term memory — not this "
                      "run's scratch (that goes in your finding)."]
        if loop.attended:
            lines += ["",
                      "**Clarification allowed (attended):** if the task is genuinely ambiguous "
                      "in a way that would change your direction, you MAY write "
                      "{\"question\", \"why\"} to questions.json in the loop files dir and end "
                      "the turn — the project pauses for the user. Keep the bar high; otherwise "
                      "proceed on a best-reasoned assumption."]
        else:
            lines += ["",
                      "**Unattended:** do NOT pause to ask the user. Investigate ambiguities "
                      "yourself, pick the best-reasoned answer, record the assumption in your "
                      "finding, and proceed. Never write questions.json in this mode."]
        if verify_command:
            lines += ["",
                      f"**Build check:** `{verify_command}` should pass. Drive your work toward "
                      "keeping it green; the supervisor may run it."]
        if test_command:
            lines += ["",
                      f"**Test check:** `{test_command}` is the test runner. The verification "
                      "stage is done when it passes."]
        if loop.success_criteria:
            lines += ["", f"**Definition of Done:** {loop.success_criteria}"]
        lines += ["",
                  "Never push to git, never run destructive operations, never read credential "
                  "files as text. Grind through obstacles rather than stopping at the first one."]
        return "\n".join(lines)

    @staticmethod
    def _strip_stage_ordinal(s: str) -> str:
        """Strip a leading ordinal the stage directive induces — e.g. ``1 — Scaffold``,
        ``1. Scaffold``, ``Stage 1: Scaffold``, ``2/3 Implementation`` → ``scaffold`` /
        ``implementation``. The directive names the active stage as ``stage N/M — <title>``,
        so the worker faithfully records ``"<N> — <title>"`` OR the whole ``"N/M <title>"``
        chip in its finding; without this the bare title/key never matches and the stage's
        findings read as empty → the gate can't pass → the stage never advances (observed:
        a fully-built+green Code loop wedged in 'implementation' because every finding was
        tagged ``"2/3 Implementation"``). Lowercased + ordinal-stripped + separator-folded
        (see ``_norm_stage``) for comparison.

        Handles two ordinal shapes: ``N/M`` (a progress chip like ``2/3``, optionally then
        a separator) and ``N<sep>`` (``1.`` / ``1 —`` / ``Stage 1:``)."""
        import re
        t = (s or "").strip()
        # First drop a leading "N/M" progress chip (the directive's "stage N/M …" shape),
        # with or without a following separator: "2/3 Implementation", "2/3 — Impl".
        t = re.sub(r"^\s*(?:stage\s*)?\d+\s*/\s*\d+\s*[.–—:)\-]?\s*", "", t, flags=re.I)
        # Then the plain "N<sep>" ordinal: "1.", "1 —", "Stage 1:", "2) ".
        t = re.sub(r"^\s*(?:stage\s*)?\d+\s*[.–—:)\-]\s*", "", t, flags=re.I)
        return CodeKind._norm_stage(t)

    @staticmethod
    def _norm_stage(s: str) -> str:
        """Canonicalize a stage id/title for comparison: lowercase + fold every run of
        separators (whitespace, ``_``, ``-``) to a single space. Without this an LLM that
        slugifies the directive's title "Test suite" → ``test_suite`` (or ``test-suite``)
        never matches the plan's ``verification`` id NOR its ``test suite`` title → the
        stage's findings read as empty → ``_observe_stage_metric`` scores nothing → the P6
        metric gate + rollback go SILENTLY INERT for that stage (observed live: greenfield
        code loop 4fb50978 completed with quality_scores=None / verdicts=0 because every
        verification finding was tagged ``test_suite``). Folding separators makes the id,
        the title, and any slug variant of the title collapse to one canonical key."""
        import re
        return re.sub(r"[\s_\-]+", " ", (s or "").strip().lower()).strip()

    def _findings_for_stage(self, loop: Loop, idx: int, findings: list[dict]) -> list[dict]:
        """Findings attributed to the stage at ``idx`` — matched by stage id OR title
        (the worker records either, since the cycle directive shows the title), tolerant
        of the ``"N — title"`` ordinal prefix the directive induces AND of separator-slug
        variants (``test_suite`` ≡ ``test-suite`` ≡ ``test suite``).

        A finding whose recorded stage matches NO plan stage — empty, or an LLM label the
        engine can't reconcile (e.g. ``test_suite_and_verification`` where the worker
        expanded the ``&`` in "Test suite & verification" to "and") — falls to the ACTIVE
        stage: the finding was written during a cycle, and a cycle always operates on the
        active stage, so that is its true home. This makes attribution robust to arbitrary
        LLM label drift (string-normalization alone is whack-a-mole: fold ``_``/``-`` and
        the model substitutes a synonym next) — the metric gate / rollback can't silently
        go inert just because the worker phrased the stage differently than the plan."""
        plan = loop.plan or []
        if idx < 0 or idx >= len(plan):
            return []

        def _norm_ids(phase: dict) -> set[str]:
            return {self._norm_stage(self.phase_key(phase)),
                    self._norm_stage(str(phase.get("title", "")))} - {""}

        def _cands(f: dict) -> tuple[str, str]:
            raw = str(f.get("stage", ""))
            return self._norm_stage(raw), self._strip_stage_ordinal(raw)

        def _exact(f: dict, accept: set[str]) -> bool:
            norm, stripped = _cands(f)
            return norm in accept or stripped in accept

        def _prefix_len(f: dict, accept: set[str]) -> int:
            """Longest accepted id that the finding's label starts with on a separator
            boundary — tolerates a TRAILING per-item decoration the worker appends to
            the stage title, e.g. "Build All Modules — store.py" / "Build All Modules:
            tests" for the "Build All Modules" stage. 0 = no prefix match. Without this
            the decorated label folds to a key matching NO plan stage, so every
            implementation finding falls through to the ACTIVE stage (observed: a
            fully-built multi-module loop wedged in 'decomposition' because every
            "Build All Modules — <file>" finding was attributed to stage 0, polluting
            its gate + starving the implementation/verification stages of evidence)."""
            best = 0
            for cand in _cands(f):
                for a in accept:
                    if a and cand.startswith(a) and cand[len(a):len(a) + 1] in ("", " "):
                        best = max(best, len(a))
            return best

        active_idx = self.active_stage_index(loop)
        stage_ids = [_norm_ids(p) for p in plan]

        def _owner(f: dict) -> int:
            """The plan-stage index that OWNS finding ``f`` (or -1 → falls to active).

            Priority: (1) an EXACT stage match; (2) else the MOST SPECIFIC trailing-
            decoration prefix match (longest matching title, so a stage titled "Build"
            can't steal "Build All Modules — x" from "Build All Modules"); (3) else -1
            (unrecognized/empty label → the active stage owns it)."""
            for j, s in enumerate(stage_ids):
                if _exact(f, s):
                    return j
            best_j, best_len = -1, 0
            for j, s in enumerate(stage_ids):
                pl = _prefix_len(f, s)
                if pl > best_len:
                    best_j, best_len = j, pl
            return best_j  # -1 when nothing prefixed

        out = []
        for f in findings:
            owner = _owner(f)
            if owner == idx or (owner < 0 and idx == active_idx):
                out.append(f)
        return out

    async def _escalate_stall_if_stuck(self, loop: Loop, idx: int, stage: str,
                                       findings: list[dict], ctx, *, cause: str = "gate") -> bool:
        """A stage that has produced _STALL_FINDINGS+ findings without ever advancing is
        stuck. ONCE per stuck stage: publish stage_stalled, pause the worker's nudge loop
        (stop spinning), and flip the loop to BLOCKED so the cockpit prompts a steer instead
        of nudging cycles to budget. Recoverable — a steer or resume re-arms. Returns True
        iff it escalated. Never raises into the cycle hook.

        ``cause`` tailors the steer message to WHY it stalled: ``"gate"`` = the structural
        exit criteria never cleared (busywork the gate rejects); ``"metric"`` = the exit
        criteria are met but the quality metric keeps holding below the stage's pass bar
        (refinement that can't clear the quality gate). Ported from the legacy code
        watchdog's _note_stall/_escalate_stall."""
        if not stage:
            return False
        key = f"{loop.id}:{stage}"
        if key in self._stall_notified:
            return False
        if len(self._findings_for_stage(loop, idx, findings)) < _STALL_FINDINGS:
            return False
        # Progress guard: a stage that keeps RESOLVING tasks (each module task
        # completing, one per cycle) is making real forward progress, not spinning.
        # A "stall" is findings piling up with NO ground-truth progress. So before
        # escalating, observe the stage's resolved-task count: if it has advanced
        # since the last check, the stage is progressing — re-baseline and defer the
        # stall rather than false-pausing a working multi-task build. Only when the
        # count is flat across _STALL_FINDINGS findings is the stage genuinely stuck.
        # (A stage with no queued tasks — e.g. a doc-only stage — reports 0 and is
        # gated by the finding count alone, exactly as before.)
        from personalclaw.loop import tasks_link
        resolved_now = await tasks_link.resolved_stage_task_count(loop, stage)
        if resolved_now > self._stall_progress.get(key, 0):
            self._stall_progress[key] = resolved_now
            logger.info(
                "code: stage %r for %s deferring stall — %d task(s) resolved "
                "(real progress, not spinning)", stage, loop.id, resolved_now)
            return False
        self._stall_notified.add(key)
        from personalclaw.loop import store
        from personalclaw.loop.loop import LoopStatus
        from personalclaw.loop.manager import session_key
        title = str((loop.plan[idx] or {}).get("title", "")).strip() or stage
        logger.info("code: stage %r stalled (%s) after %d findings for %s",
                    stage, cause, _STALL_FINDINGS, loop.id)
        ctx.publish(loop.id, "stage_stalled",
                    {"loop_id": loop.id, "stage": stage, "title": title,
                     "findings": _STALL_FINDINGS, "cause": cause})
        try:  # pause the worker's nudge loop so it stops spinning while it waits on the user
            nl = ctx.svc.get_by_session(session_key(loop.id))
            if nl is not None:
                await ctx.svc.update(nl.id, active=False)
        except Exception:
            logger.debug("code: stall-pause of nudge loop failed for %s", loop.id, exc_info=True)
        detail = ("its exit criteria — paused to avoid spinning. Steer it (or relax a criterion), "
                  "then resume." if cause == "gate" else
                  "its quality bar — the work meets the exit criteria but keeps scoring below the "
                  "stage's quality gate. Paused to avoid spinning; steer it (or relax the bar), then resume.")
        try:
            store.update_status(loop.id, LoopStatus.BLOCKED, error_message=(
                f"Stage '{title}' produced {_STALL_FINDINGS}+ cycles without clearing {detail}"))
        except (KeyError, store.TransitionError):
            pass
        ctx.publish(loop.id, "blocked", {"loop_id": loop.id, "stage": stage})
        return True

    # Deliverable-labels with no concrete filename → nothing to resolve on disk (the
    # gate still runs the judge). ``None`` path means "unverifiable label", distinct from
    # "named a file that's missing" (which blocks) — the caller relies on that distinction.
    @staticmethod
    def _resolve_deliverable(workspace_dir: str, deliverable: str) -> tuple[bool, str | None]:
        """Resolve a stage's declared document deliverable to a concrete on-disk path —
        the independent ground-truth locator (observe, don't trust the worker's self-report).

        Returns ``(verifiable, path)``:
          * ``(False, None)`` — the label carries no concrete filename, or the workspace
            can't be read: nothing to verify here (the judge still gates). NOT a block.
          * ``(True, "<abs path>")`` — a declared file was found on disk.
          * ``(True, None)`` — the label names a file that does NOT exist: the gate blocks.

        The deliverable field is a filename (e.g. 'PLAN.md', 'src/engine.ts') or a short
        phrase mentioning one. A filename may carry a SUBDIRECTORY path (e.g. 'src/engine.ts')
        — honored relative to the workspace (checking only the basename at the root would
        miss '<ws>/src/engine.ts' and hard-fail the gate forever). We accept a match at the
        as-given relative path, OR the basename at the root, OR the basename anywhere under
        the workspace (a worker may place it in a different but valid dir)."""
        import os
        import re
        ws = (workspace_dir or "").strip()
        if not ws or not os.path.isdir(ws):
            return (False, None)  # can't verify → don't block (the judge still gates)
        names = re.findall(r"[\w./-]+\.[A-Za-z0-9]+", deliverable or "")
        if not names:
            return (False, None)  # no concrete filename in the label → nothing to verify here
        for n in names:
            rel = n.lstrip("./")
            base = os.path.basename(n)
            # 1) the path as-given, relative to the workspace (honors 'src/engine.ts').
            p = os.path.join(ws, rel)
            if os.path.isfile(p):
                return (True, p)
            # 2) the bare basename at the workspace root (the common 'PLAN.md' case).
            p = os.path.join(ws, base)
            if os.path.isfile(p):
                return (True, p)
        # 3) basename match anywhere under the workspace — the worker may have placed the
        # file in a valid subdir the label didn't spell out. Bounded walk (skip heavy/vcs
        # dirs) so a node_modules/.git tree can't make this slow.
        wanted = {os.path.basename(n) for n in names}
        skip = {"node_modules", ".git", "dist", "build", ".venv", "__pycache__", ".next"}
        for root, dirs, files in os.walk(ws):
            dirs[:] = [d for d in dirs if d not in skip]
            hit = wanted & set(files)
            if hit:
                return (True, os.path.join(root, sorted(hit)[0]))
        return (True, None)  # a file WAS named but none found → block

    @staticmethod
    def _read_deliverable(path: str, *, max_chars: int = 6000) -> str:
        """Read a resolved deliverable's real content for the judge — the observed
        artifact, not the worker's narration (Slice B). Bounded (head of the file); binary
        or unreadable files return ""; a middle-truncation marker shows the cap was hit."""
        import os
        try:
            if os.path.getsize(path) == 0:
                return ""
            with open(path, encoding="utf-8", errors="strict") as fh:
                text = fh.read(max_chars + 1)
        except (OSError, UnicodeDecodeError):
            return ""  # binary / unreadable → nothing textual to feed the judge
        if len(text) > max_chars:
            return text[:max_chars] + "\n… (deliverable truncated for the gate)"
        return text

    def _tick_decide(self, loop: Loop, idx: int, findings: list[dict], *,
                     gate_passed: bool, metric: float | None):
        """Build the immutable TickState snapshot for the ACTIVE stage and return the
        pure ``tick.evaluate`` Decision — the single authority for advance/hold/rollback/
        complete. The snapshot is derived entirely from persisted state (plan + phase
        timings + findings + the metric the adapter just observed), so a restarted
        supervisor re-derives the same Decision (the tick restartability guarantee).

        ``prior_step_floor`` is the metric bar the PRIOR stage cleared (its
        ``metric_pass``): a metric that regresses below it means the just-done upstream
        work broke → rollback. ``step_started_at`` uses the loop's current-run start
        (the finest per-stage clock persisted today); dwell is opt-in and coarse, so this
        is sufficient and honest rather than inventing a per-stage timestamp column."""
        from personalclaw.loop import tick
        plan = loop.plan or []
        tcfg = tick.tick_config_from_plan(plan, max_cycles=loop.max_cycles or 0)
        prior_floor = None
        if idx - 1 >= 0:
            prior_floor = tick.step_config_from_phase(plan[idx - 1]).metric_pass
        stage_findings = len(self._findings_for_stage(loop, idx, findings))
        _rb = (loop.kind_config or {}).get("rollbacks_on_stage") or {}
        rollbacks = int(_rb.get(str(idx), 0)) if isinstance(_rb, dict) else 0
        state = tick.tick_state_from_snapshot(
            step_index=idx,
            step_started_at=loop.started_at or 0.0,
            findings_total=stage_findings,
            findings_at_step_start=0,   # stage_findings is ALREADY per-stage → delta is itself
            gate_passed=gate_passed,
            metric=metric,
            prior_step_floor=prior_floor,
            rollbacks_on_step=rollbacks,
            total_cycles=loop.total_cycles or 0,
        )
        return tick.evaluate(tcfg, state, time.time())

    async def _observe_stage_metric(self, loop: Loop, idx: int, stage: str,
                                    findings: list[dict], ctx) -> float | None:
        """The graded quality metric for a METRIC-GATED stage — the P4 scored judge's
        ``quality_score`` (0-5) for the latest stage finding, persisted to the quality
        trail (mirroring goal.py) so the tick metric gate + rollback reason over a real,
        restartable signal instead of the binary structural gate. Returns None (→ the
        tick metric branch stays inert) for a stage with no ``metric_pass`` OR when the
        judge can't score — never fabricates a number, and never raises into the cycle."""
        from personalclaw.loop import store, tick
        plan = loop.plan or []
        if not (0 <= idx < len(plan)):
            return None
        if tick.step_config_from_phase(plan[idx]).metric_pass is None:
            return None   # stage not metric-gated → structural gate is the whole gate
        stage_findings = self._findings_for_stage(loop, idx, findings)
        if not stage_findings:
            return None
        try:
            from personalclaw.loop import judge as judge_mod
            from personalclaw.loop.loop import effective_dir
            cfg = loop.kind_config or {}
            deliverable = str((plan[idx] or {}).get("deliverable", "")).strip()
            verdict = await judge_mod.assess_cycle(
                loop.task, "\n".join(str(c) for c in (plan[idx].get("exit_criteria") or [])),
                stage_findings[-1], stage_findings[:-1],
                verify_command=str(cfg.get("verify_command", "")),
                workspace=effective_dir(loop) or None,
                deliverables=[deliverable] if deliverable else None)
        except Exception:
            logger.debug("loop %s: stage-metric judge failed (non-fatal)", loop.id, exc_info=True)
            return None
        if verdict is None:
            return None
        cycle = int((stage_findings[-1] or {}).get("cycle", loop.total_cycles or 0))
        store.record_quality_score(loop.id, verdict.quality_score)
        store.write_verdict(loop.id, cycle, {"cycle": cycle, "stage": stage, **verdict.to_dict()})
        ctx.publish(loop.id, "cycle_verdict", {
            "loop_id": loop.id, "cycle": cycle, "stage": stage,
            "quality_score": verdict.quality_score, "done": bool(verdict.done),
            "marginal_value": verdict.marginal_value, "regressed": bool(verdict.regressed)})
        return float(verdict.quality_score)

    async def _apply_advance(self, loop: Loop, idx: int, stage: str, decision,
                             metric: float | None, ctx) -> bool:
        """Apply an ADVANCE/COMPLETE Decision: mark the stage done, reconcile its tasks,
        then either COMPLETE the loop (last stage) or activate the next stage + re-arm the
        brief/nudge. The stage-advance side-effects the pure engine can't own."""
        from personalclaw.loop import store, tasks_link
        from personalclaw.loop.manager import write_brief
        cid = loop.id
        plan = loop.plan or []
        store.set_phase_status(cid, stage, "done")
        # Cleared its gate → drop any stall flag + progress baseline so the in-memory
        # state doesn't leak (and a later re-entry of this key starts fresh).
        self._stall_notified.discard(f"{cid}:{stage}")
        self._stall_progress.pop(f"{cid}:{stage}", None)
        # The supervisor (not the worker) is the authority the stage is met — close its
        # still-open tasks so the cockpit doesn't show a done stage with open tasks.
        await tasks_link.reconcile_phase_done(cid, stage)
        from personalclaw.loop import tick
        if decision.action is tick.Action.COMPLETE or idx >= len(plan) - 1:
            await ctx.complete(cid, "all stages complete")
            return True
        next_stage = self.phase_key(plan[idx + 1])
        if next_stage:
            store.set_phase_status(cid, next_stage, "active")
        refreshed = store.get(cid)
        if refreshed is not None:
            write_brief(refreshed)     # re-arm the brief so the worker targets the new stage
        # Refresh the live worker's autonudge MESSAGE (cycle_nudge embeds the stage
        # directive, set once at start) so it names the new stage, not the old one.
        from personalclaw.loop.manager import rearm_nudge_message
        await rearm_nudge_message(ctx.svc, cid)
        ctx.publish(cid, "stage_advance", {"loop_id": cid, "completed_stage": stage,
                                           "next_stage": next_stage, "metric": metric})
        return False

    async def _apply_rollback(self, loop: Loop, idx: int, stage: str, decision, ctx) -> bool:
        """Apply a ROLLBACK Decision: the stage's quality metric regressed below the prior
        stage's floor → step back to that prior stage to re-fix the upstream work. Flip the
        current stage back to pending, re-activate the prior stage, bump the per-stage
        rollback counter (tick's rollback_cap turns runaway rollbacks into COMPLETE-blocked),
        re-arm the brief/nudge, and emit the ``rolled_back`` SSE the cockpit listens for."""
        from personalclaw.loop import store
        from personalclaw.loop.manager import rearm_nudge_message, write_brief
        cid = loop.id
        plan = loop.plan or []
        prior_idx = max(0, decision.step_index)
        prior_stage = self.phase_key(plan[prior_idx]) if 0 <= prior_idx < len(plan) else ""
        # Current stage → pending (it must be re-earned after the upstream fix); prior → active.
        store.set_phase_status(cid, stage, "pending")
        if prior_stage:
            store.set_phase_status(cid, prior_stage, "active")
        # Per-stage rollback counter (kind_config) so tick's rollback_cap can bound it.
        cfg = dict(loop.kind_config or {})
        counts = dict(cfg.get("rollbacks_on_stage", {}) or {})
        counts[str(idx)] = int(counts.get(str(idx), 0)) + 1
        store.merge_kind_config(cid, {"rollbacks_on_stage": counts})
        self._stall_notified.discard(f"{cid}:{stage}")
        self._stall_progress.pop(f"{cid}:{stage}", None)
        refreshed = store.get(cid)
        if refreshed is not None:
            write_brief(refreshed)
        await rearm_nudge_message(ctx.svc, cid)
        logger.info("loop %s: P6 ROLLBACK stage %d→%d (%s)", cid, idx, prior_idx, decision.reason)
        ctx.publish(cid, "rolled_back", {"loop_id": cid, "from_stage": stage,
                                         "to_stage": prior_stage, "reason": decision.reason,
                                         "metric": decision.metric})
        return False

    async def _stage_gate_passed(self, loop: Loop, idx: int, findings: list[dict], ctx) -> bool:
        """Whether the ACTIVE stage's exit criteria are met — the supervisor's own
        verification (never the worker's self-report). Needs work-evidence (≥1 stage
        finding); applies a STAGE-APPROPRIATE gate; then a conservative judge over the
        exit criteria + evidence. Defaults to NOT passed on any ambiguity.

        Stage-appropriate gating (the durable fix for the planning-stage hard-fail):
        a build/test command is the done-ness signal for stages that produce buildable
        CODE, NOT for a planning/scaffold-DESIGN stage whose deliverable is a doc and
        which runs before any buildable project exists. Running `npm run build` there
        exits 254 (ENOENT, no package.json) → a permanent FALSE → the stage never
        advances. So a verify/test command only gates a stage when its project is
        actually buildable in the workspace yet (a manifest exists); otherwise the
        command is skipped (can't run) and the stage gates on the judge + an independent
        deliverable-existence check (ground truth, not the worker's self-report)."""
        plan = loop.plan or []
        if idx < 0 or idx >= len(plan):
            return False
        phase = plan[idx]
        stage = self.phase_key(phase)
        exit_criteria = [str(c).strip() for c in (phase.get("exit_criteria") or []) if str(c).strip()]
        stage_findings = self._findings_for_stage(loop, idx, findings)
        work_done = len(stage_findings) >= 1
        if not exit_criteria:
            return work_done            # no explicit gate → advance once work happened
        if not work_done:
            return False
        # The dir the worker actually wrote to — workspace_dir when a codebase was bound,
        # else the project context dir. Reading loop.workspace_dir directly would miss the
        # deliverable for a context-dir loop (no explicit workspace) and silently skip the
        # ground-truth check. `effective_dir` is the one resolver both gate paths share.
        from personalclaw.loop.loop import effective_dir
        ws = effective_dir(loop)
        # Independent ground-truth check (no self-report): if the stage declares a
        # document deliverable, it MUST exist on disk before the stage can pass. This
        # gives a doc/planning stage a real gate (not transcript-only) and is the
        # observe-don't-trust requirement carried from the rehaul (O-E2).
        deliverable = str(phase.get("deliverable", "")).strip()
        check_evidence = ""
        if deliverable and ws:
            verifiable, path = self._resolve_deliverable(ws, deliverable)
            if verifiable and path is None:
                # A concrete file was named but does NOT exist → block (ground truth).
                ctx.publish(loop.id, "gate_check", {"loop_id": loop.id, "label": "deliverable",
                                                    "deliverable": deliverable, "ok": False, "stage": stage})
                return False
            if path is not None:
                # Slice B — feed the deliverable's REAL content to the judge, not just an
                # "exists" note: the gate scores the observed artifact, not the worker's
                # narration. Read is bounded; binary/empty files add only the exists note.
                content = self._read_deliverable(path)
                check_evidence += f"\nSupervisor confirmed the deliverable `{deliverable}` exists on disk."
                if content:
                    check_evidence += (
                        f"\n\n--- Deliverable content ({deliverable}), observed directly by the "
                        f"supervisor ---\n{content}\n--- end deliverable ---"
                    )
                ctx.publish(loop.id, "gate_check", {"loop_id": loop.id, "label": "deliverable",
                                                    "deliverable": deliverable, "ok": True, "stage": stage,
                                                    "content_bytes": len(content)})
        from personalclaw.loop.gates import judge_verdict, run_verify_command, verdict_is_pass, verdict_rendered
        cfg = loop.kind_config or {}
        checks = []
        if str(cfg.get("verify_command", "")).strip():
            checks.append(("build", cfg["verify_command"]))
        if stage == "verification" and str(cfg.get("test_command", "")).strip():
            checks.append(("tests", cfg["test_command"]))
        passed_a_command = False     # a deterministic check actually RAN and PASSED
        for label, cmd in checks:
            # Skip a command that can't meaningfully run yet — a planning/pre-scaffold
            # stage has no project manifest, so the command would exit ENOENT (254) and
            # hard-fail the gate forever. Treat "not buildable yet" as can't-run (fall
            # through to the judge), exactly like the 127 tool-missing tristate.
            if not _command_runnable_here(cmd, ws):
                ctx.publish(loop.id, "gate_check", {"loop_id": loop.id, "label": label,
                                                    "command": cmd, "ok": None, "stage": stage,
                                                    "skipped": "project not buildable yet"})
                continue
            ok = await run_verify_command(cmd, ws or None, label=label)
            if ok is False:
                ctx.publish(loop.id, "gate_check", {"loop_id": loop.id, "label": label,
                                                    "command": cmd, "ok": False, "stage": stage})
                return False
            if ok is True:
                passed_a_command = True
                check_evidence += f"\nSupervisor ran `{cmd}` ({label}) → PASSED."
            # ok is None → couldn't run; fall through to the judge.
        if checks:
            ctx.publish(loop.id, "gate_check", {"loop_id": loop.id, "ok": True, "stage": stage})
        recent = stage_findings[-4:]
        evidence = "\n".join(
            f"- cycle {f.get('cycle')}: {str(f.get('summary', '') or f.get('key_insight', ''))[:300]}"
            for f in recent) + check_evidence
        criteria = "\n".join(f"- {c}" for c in exit_criteria)
        # The stage-gate instruction lives in the prompt system (bundled
        # ``task-sdlc-stage-gate``), rendered with the stage + evidence.
        from personalclaw.prompt_providers.runtime import render_use_case_prompt

        prompt = render_use_case_prompt("sdlc_stage_gate", {
            "stage_title": phase.get("title", stage),
            "objective": phase.get("objective", ""),
            "criteria": criteria,
            "evidence": evidence,
        }) or ""
        raw = await judge_verdict(prompt)
        if verdict_is_pass(raw):
            return True
        # The judge said no — but distinguish a genuine FAIL from a can't-judge. If the
        # judge rendered a real verdict (FAIL), respect it: keep cycling. If it produced
        # NO parseable verdict (empty/prose — provider unavailable or the stream errored,
        # e.g. a model timeout), do NOT treat that as FAIL when a deterministic check
        # already PASSED: otherwise a flaky judge permanently blocks a stage whose build/
        # test gate is green + tasks done + no queued work (observed: a complete engine
        # stage stuck for cycles on judge timeouts). Fall back to the deterministic pass;
        # only block when there's no deterministic pass to stand on.
        if not verdict_rendered(raw) and passed_a_command:
            ctx.publish(loop.id, "gate_check", {"loop_id": loop.id, "ok": True, "stage": stage,
                                                "note": "judge unavailable; passed on deterministic checks"})
            return True
        return False

    async def autopilot_queue(self, loop: Loop) -> Loop | None:
        """Autopilot tick: queue every not-terminal task of the ACTIVE stage that
        isn't already queued, so the scheduler always has the full stage to drive.
        Respects the phase barrier (only the active stage's tasks are queued). Returns
        the refreshed loop if it queued anything, else None. Never raises. Ported from
        code/watchdog._autopilot_queue."""
        from personalclaw.loop import store, tasks_link
        cid = loop.id
        try:
            idx = self.active_stage_index(loop)
            if idx < 0:
                return None
            stage = self.phase_key(loop.plan[idx])
            list_id = tasks_link.phase_list_id(loop, stage)
            if not list_id:
                return None
            from personalclaw.tasks import registry
            tasks, _ = await registry.list_all_tasks(task_list_id=list_id, limit=500)
            queued = set((loop.kind_config or {}).get("queued_task_ids", []) or [])
            to_queue = [t.id for t in tasks
                        if t.id not in queued and not tasks_link._is_resolved(t.status)]
            if not to_queue:
                return None
            store.queue_tasks(cid, to_queue)
            return store.get(cid)
        except Exception:
            logger.debug("autopilot_queue failed for %s", cid, exc_info=True)
            return None

    async def on_new_cycle(self, loop: Loop, findings: list[dict], ctx) -> bool:
        """Per-cycle SDLC orchestration: (autopilot) keep the active stage's tasks
        queued; gate the active stage; on pass, mark it done, advance (re-arm the
        brief for the next stage) or COMPLETE on the last stage. Returns True iff the
        loop completed. The sequential core + autopilot queueing — the parallel
        task-worker SCHEDULER (worktree spawn/merge) lands in 2c(iv.e). Owns this
        cycle's done-ness (the watchdog skips its generic signal)."""
        from personalclaw.loop import store
        cid = loop.id
        # Autopilot: ensure the active stage's tasks are queued (the scheduler/worker
        # always has the full stage to drive). One-by-one mode leaves queueing to the user.
        if loop.autopilot:
            refreshed = await self.autopilot_queue(loop)
            if refreshed is not None:
                loop = refreshed
        plan = loop.plan or []
        idx = self.active_stage_index(loop)
        if idx < 0:
            # No stage plan: complete once a finding lands AND any verify/test passes
            # (the project-level "prove it" gate). Otherwise keep going.
            return await self._no_stage_done(loop, findings)
        stage = self.phase_key(plan[idx])
        # Parallel mode: run the worktree scheduler (reap+merge finished task-workers,
        # spawn ready ones). It owns this cycle until the active stage's tasks drain;
        # a merge conflict it can't auto-resolve pauses the loop (returns True).
        ws = (loop.workspace_dir or "").strip()
        if self._is_parallel(loop):
            paused = await self._schedule_parallel(loop, stage, ws, ctx)
            if paused:
                return False           # paused NEEDS_INPUT — watchdog stops the cycle
            loop = store.get(cid) or loop
        # Don't let a lenient gate advance a stage while it still has READY QUEUED
        # tasks the worker hasn't run — that would skip the user's other queued work.
        # (Only bites when work is actually queued; an empty queue = the old free-run.)
        from personalclaw.loop import tasks_link
        if await tasks_link.ready_queued_tasks(loop, stage):
            return False               # real queued work pending → keep cycling
        if self._live_task_workers(loop, ctx.svc):
            return False               # task-workers still running → wait for them
        # ── P6: the stepwise lifecycle decision IS the one pure tick.evaluate ────────
        # Observe the two inputs the decision needs — the adapter's I/O, per the tick
        # purity contract (loop/tick.py): the STRUCTURAL gate (the supervisor runs the
        # verify command + judges the exit criteria over observed ground truth, never the
        # worker's self-report) and, only for a metric-gated stage, a graded QUALITY
        # metric (the P4 scored judge, persisted to the quality trail so the metric gate +
        # rollback have a live signal). Then tick.evaluate turns (gate, metric, timing,
        # budget) into ONE Decision — no advance/hold logic is duplicated here. For a
        # plan that declares no tick keys (every plan by default) evaluate degrades to the
        # exact prior behavior: gate passed → advance/complete, else → keep executing.
        from personalclaw.loop import tick
        gate_passed = await self._stage_gate_passed(loop, idx, findings, ctx)
        metric = await self._observe_stage_metric(loop, idx, stage, findings, ctx) if gate_passed else None
        decision = self._tick_decide(loop, idx, findings, gate_passed=gate_passed, metric=metric)
        if decision.action is tick.Action.ROLLBACK:
            return await self._apply_rollback(loop, idx, stage, decision, ctx)
        if decision.action in (tick.Action.ADVANCE, tick.Action.COMPLETE):
            return await self._apply_advance(loop, idx, stage, decision, metric, ctx)
        # EXECUTE / HOLD / WAITING → keep cycling this stage. A stage that grinds
        # _STALL_FINDINGS+ cycles without ever advancing is stuck — whether it's failing
        # the STRUCTURAL gate (busywork the gate rejects) OR persistently HOLDing below
        # its quality metric_pass (refinement that never clears the bar). Both burn to
        # budget silently otherwise, so escalate ONCE either way and let the user steer /
        # relax the bar. The escalation's own finding-count threshold prevents it firing on
        # a stage that's only briefly holding. (WAITING — a worker turn still in flight —
        # is transient and shouldn't accrue toward a stall; escalate only on EXECUTE/HOLD.)
        if decision.action in (tick.Action.EXECUTE, tick.Action.HOLD):
            # HOLD is always a quality-metric hold (the structural gate passed but the
            # metric is below pass); EXECUTE here means the structural gate didn't clear.
            cause = "metric" if decision.action is tick.Action.HOLD else "gate"
            await self._escalate_stall_if_stuck(loop, idx, stage, findings, ctx, cause=cause)
        return False

    async def _get_task(self, task_id: str):
        from personalclaw.tasks import registry
        try:
            return await registry.get_task(task_id, provider_name="native")
        except Exception:
            return None

    async def _schedule_parallel(self, loop: Loop, phase_key: str, ws: str, ctx) -> bool:
        """Run the active phase's ready tasks concurrently — one worker per task in its
        own worktree, capped at _POOL_CAP. Reap+merge finished workers (freeing slots),
        then fill free slots with ready tasks. Returns True iff the loop paused
        (NEEDS_INPUT on a merge conflict that couldn't auto-resolve). Ported from
        code/watchdog._schedule_parallel onto the Loop entity + ctx."""
        from personalclaw.loop import store, tasks_link, worktree
        from personalclaw.loop.manager import (spawn_task_worker, task_session_key,
                                               teardown_task_worker)
        # 1. Reap finished task-workers.
        for tid in list((loop.kind_config or {}).get("queued_task_ids", []) or []):
            skey = task_session_key(loop.id, tid)
            sess = ctx.state._sessions.get(skey)
            if sess is None:
                task = await self._get_task(tid)
                if (task is not None and tasks_link._is_done(task.status)
                        and worktree.branch_exists(ws, tid)):
                    if await self._reap_merge_done(loop, tid, task, ws, ctx):
                        return True
                continue
            task = await self._get_task(tid)
            if task is not None and not tasks_link._is_done(task.status):
                loop_gone = ctx.svc.get_by_session(skey) is None
                if loop_gone and store.task_finding_count(loop.id, tid) > 0:
                    await tasks_link.mark_task_done(tid)
                    task = await self._get_task(tid) or task
                elif loop_gone:
                    # Worker exhausted its budget with no finding → tear down + pause.
                    await teardown_task_worker(ctx.svc, loop.id, tid)
                    store.write_question(loop.id,
                        f'Task "{task.title}" ran out of cycles without producing a result '
                        "— it may be under-specified or blocked. Steer it, or remove it, then resume.")
                    store.update_status(loop.id, LoopStatus.NEEDS_INPUT)
                    ctx.publish(loop.id, "needs_input", {"loop_id": loop.id})
                    return True
            if task is not None and tasks_link._is_done(task.status):
                await teardown_task_worker(ctx.svc, loop.id, tid)
                if await self._reap_merge_done(loop, tid, task, ws, ctx):
                    return True
        # 2. Fill free slots with ready, not-yet-running tasks.
        loop = store.get(loop.id) or loop
        slots = _POOL_CAP - len(self._live_task_workers(loop, ctx.svc))
        if slots <= 0:
            return False
        ready = await tasks_link.ready_queued_tasks(loop, phase_key)
        if not ready or not worktree.ensure_base_commit(ws):
            return False
        for t in ready[:slots]:
            if ctx.svc.get_by_session(task_session_key(loop.id, t.id)) is not None:
                continue
            wt_path = worktree.add_worktree(ws, t.id, loop.tasks_project_id)
            if wt_path is None:
                continue
            await spawn_task_worker(ctx.state, ctx.svc, loop, t, wt_path)
            ctx.publish(loop.id, "task_started", {"loop_id": loop.id, "task_id": t.id, "title": t.title})
        return False

    async def _reap_merge_done(self, loop: Loop, tid: str, task, ws: str, ctx) -> bool:
        """Merge a done task's branch into base. Clean → unqueue + task_done. Conflict
        → autopilot auto-resolves (the loser rebases: discard branch + re-open the task
        to re-run on the merged base) up to _CONFLICT_REDO_CAP, else pause NEEDS_INPUT.
        Returns True iff the merge did NOT integrate (caller stops + awaits the user)."""
        from personalclaw.loop import store, worktree
        result = worktree.merge_worktree(ws, tid, loop.tasks_project_id)
        redo_key = f"{loop.id}:{tid}"
        if result.ok:
            self._conflict_redos.pop(redo_key, None)
            store.unqueue_tasks(loop.id, [tid])
            ctx.publish(loop.id, "task_done", {"loop_id": loop.id, "task_id": tid, "merged": True})
            return False
        branch = worktree.branch_name(tid)
        conflicts = result.conflicts
        if conflicts:
            shown = ", ".join(conflicts[:5]) + ("…" if len(conflicts) > 5 else "")
            redos = self._conflict_redos.get(redo_key, 0)
            if loop.autopilot and redos < _CONFLICT_REDO_CAP:
                self._conflict_redos[redo_key] = redos + 1
                worktree.remove_worktree(ws, tid, loop.tasks_project_id)
                try:
                    from personalclaw.tasks import registry
                    await registry.update_task(tid, status="open")
                except Exception:
                    logger.warning("conflict auto-resolve: reset task %s failed", tid, exc_info=True)
                ctx.publish(loop.id, "gate_check", {"loop_id": loop.id, "ok": False, "label": "merge",
                    "output": f'Conflict in {shown} — re-running "{task.title}" on the updated base.'})
                return False
            question = (f'Task "{task.title}" keeps conflicting on {shown} after {redos} auto-retries '
                        f"— resolve it on branch {branch} in the workspace, then resume.")
            out = f"Merge conflict in: {shown} — resolve in the workspace, then resume."
        else:
            question = (f'Task "{task.title}" finished but its branch {branch} could not be merged '
                        "(a git error, not a content conflict). Check the workspace's git state, then resume.")
            out = "Merge failed (not a content conflict) — check git state, then resume."
        store.write_question(loop.id, question)
        store.update_status(loop.id, LoopStatus.NEEDS_INPUT)
        ctx.publish(loop.id, "gate_check", {"loop_id": loop.id, "ok": False, "label": "merge", "output": out})
        ctx.publish(loop.id, "needs_input", {"loop_id": loop.id})
        return True

    async def _no_stage_done(self, loop: Loop, findings: list[dict]) -> bool:
        """Completion for a code loop with NO stage plan (free-running off its brief):
        ≥1 finding AND any configured verify/test command passes. The project-level
        'prove it' gate; no judge (no stage criteria to judge against)."""
        if not findings:
            return False
        from personalclaw.loop.gates import run_verify_command
        cfg = loop.kind_config or {}
        for cmd in (str(cfg.get("verify_command", "")).strip(), str(cfg.get("test_command", "")).strip()):
            if cmd:
                ok = await run_verify_command(cmd, loop.workspace_dir or None)
                if ok is False:
                    return False        # a real failure holds completion
        return True

    def cycle_nudge(self, loop: Loop, loop_dir: str) -> str:
        """Ported from the legacy code manager: path-qualified per-cycle trigger.
        brief/status/guidance live in loop_dir, which for a brownfield project is
        NOT the worker's cwd (the bound workspace) — so qualify each path."""
        lines = [
            f"Run the next autonomous cycle for code project {loop.id} "
            f"(project files dir: {loop_dir}). Steps: (1) check {loop_dir}/status.json — "
            f"if not 'running', stop; (2) read {loop_dir}/brief.md; (3) apply + delete "
            f"{loop_dir}/guidance.txt if present; (4) do ONE adaptive step toward the "
            "CURRENT stage's objective.",
        ]
        directive = self.stage_directive(loop)
        if directive:
            lines += ["", directive]
        lines += [
            "",
            f"Before you end this turn you MUST write findings/cycle_NNN.json to {loop_dir} "
            "(next sequential N) — the deliverable of this cycle, not optional. The finding "
            "is {cycle, stage, summary, key_insight, files_touched, evidence}. Keep the "
            "stage's TaskList honest with the task_list/task_update tools as you work "
            "(mark tasks in_progress when you start them, done when complete). Report what "
            "you DID and the EVIDENCE; do NOT self-certify the stage is done — the "
            "supervisor decides that against the stage's exit criteria.",
            "",
            "Actually write the code/files (use your file-write/editor tools); do not just "
            "describe them. Then end the turn.",
        ]
        return "\n".join(lines)


class _CodeWalkthrough:
    """Code-kind planning walkthrough — a DYNAMIC step list (design pass first).
    Wraps the legacy code plan-walkthrough's pure briefs/parsers; projects the
    approved decomposition into the UNIFIED ``plan`` + ``summary``."""

    step_mode = "dynamic"

    def __init__(self) -> None:
        from personalclaw.agents.defaults import CODE_PLANNER_AGENT_NAME
        self.planner_agent = CODE_PLANNER_AGENT_NAME

    def default_steps(self) -> list[dict]:
        return []  # dynamic mode — the design pass authors the steps

    def build_design_brief(self, task: str, workspace_dir: str, design_inputs=None) -> str:
        from personalclaw.loop import code_plan_briefs as pw
        return pw.build_design_brief(task, workspace_dir)

    def parse_steps_sentinel(self, raw: str):
        from personalclaw.loop import code_plan_briefs as pw
        return pw.parse_steps_sentinel(raw)

    def build_step_brief(self, task, step, *, approved, workspace_dir):
        from personalclaw.loop import code_plan_briefs as pw
        return pw.build_step_brief(task, step, approved=approved, workspace_dir=workspace_dir)

    def parse_artifact_sentinel(self, raw: str):
        from personalclaw.loop import code_plan_briefs as pw
        return pw.parse_artifact_sentinel(raw)

    def project_to_spec(self, session) -> dict:
        """Project the approved `decomposition` artifact into the unified ``plan``
        (phase rows) + a summary from the problem_framing step. Falls back to a
        generic implement→verify ladder if the walkthrough produced no decomposition,
        so the user always lands on a launchable Plan Review."""
        from personalclaw.loop import code_plan_briefs as pw
        decomp = next((s for s in reversed(session.steps) if s.kind == "decomposition"), None)
        stage_plan = pw.decomposition_to_stage_plan(decomp.artifact) if decomp else []
        if not stage_plan:
            from personalclaw.loop.code_classify import _fallback_classification
            stage_plan = _fallback_classification().stage_plan
            logger.info("code walkthrough: no decomposition for %s — using fallback ladder",
                        session.project_id)
        spec: dict = {"plan": stage_plan}
        for s in session.steps:
            md = str((s.artifact or {}).get("markdown", "")).strip()
            if s.kind == "problem_framing" and md:
                spec["summary"] = md.splitlines()[0].strip()[:300]
                break
        # Lift the planner-authored CI/test commands out of the approved test_strategy
        # into kind_config so the SDLC stage gate has a DETERMINISTIC check (run the
        # build chain + the test/coverage runner) instead of resting entirely on the
        # LLM judge. Without this a verification stage — whose exit criteria ARE
        # test-execution claims a conservative judge won't take on transcript alone —
        # can never advance even when real CI is green (observed: fully-tested engine
        # stage stuck for cycles). Only set when the planner actually authored commands.
        ts = next((s for s in reversed(session.steps) if s.kind == "test_strategy"), None)
        if ts is not None:
            verify_command, test_command = pw.gate_commands_from_test_strategy(ts.artifact or {})
            if verify_command or test_command:
                # MERGE into the existing kind_config (update_spec replaces the column
                # wholesale, so a bare {verify,test} dict would clobber entry_stage/
                # project_kind/queued_task_ids set at create). session.project_id == loop id.
                from personalclaw.loop import store
                existing = dict((store.get(session.project_id) or Loop(id="", name="", kind="code", task="")).kind_config or {})
                existing["verify_command"] = verify_command
                existing["test_command"] = test_command
                spec["kind_config"] = existing
        return spec


register(CodeKind())
