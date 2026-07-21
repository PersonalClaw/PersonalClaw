"""Design kind — design-system creation (live canvas, tokens, components, exports).

Helps the user build a comprehensive design system: PersonalClaw ships an
exhaustive default token set (every look-and-feel axis), and the loop guides the
user through choosing overrides and generating React components against it. As a
loop it follows the shared spine (understand → phase → plan → execute), but its
surfaces add a live component canvas, screenshot palette/primitive extraction,
drag-drop composition, and design-system exports (token artifacts, exportable
components, DESIGN.md). Slice 1 supplies the config/phase shape; the full
behavior + canvas land in the Design slice (last).
"""

from __future__ import annotations

import logging

from personalclaw.loop.kinds import LoopKindStrategy, register
from personalclaw.loop.loop import Loop

logger = logging.getLogger(__name__)


class DesignKind(LoopKindStrategy):
    kind = "design"
    label = "Design"
    description = "Build a design system — tokens, components, live canvas, exports."
    wants_workspace = False  # the project context dir holds the design artifacts
    default_agent = "personalclaw-loop"
    # NOT task-driven yet: until the Design slice adds the step walkthrough that emits a
    # real plan, classify returns plan=[] — so provisioning would create a Tasks Project
    # with ZERO TaskLists (the same empty-Project clutter goal avoids, cycle 26). Design
    # currently free-runs off its brief/cycle_nudge like general. Flip to True when the
    # Design slice produces a step plan that should seed per-step TaskLists.
    provisions_tasks = False

    def default_kind_config(self) -> dict:
        return {
            # Override tokens the user has chosen on top of PClaw's defaults.
            "token_overrides": {},
            # What the system is being designed for (app/site/brand notes).
            "targets": "",
            # Selected export outputs (tokens artifact / react components / DESIGN.md).
            "exports": [],
        }

    def phase_key(self, phase: dict) -> str:
        return str(phase.get("step", "")).strip() or str(phase.get("title", "")).strip()

    def _token_axes(self) -> list[str]:
        """Human-readable list of the default token system's top-level axes (drives the
        brief). Derived from the bundled default set so the brief never drifts from it."""
        try:
            from personalclaw.loop.design_tokens import default_tokens

            tree = default_tokens()
            labels = {
                "color": "color — primitive scales (50→950) + semantic roles per scheme (light/dark)",  # noqa: E501
                "typography": "typography — families, weights, sizes, line-heights, tracking, composed text styles",  # noqa: E501
                "spacing": "spacing — 4px-grid scale",
                "sizing": "sizing — icon/control/container dimensions",
                "radius": "radius — corner rounding scale",
                "border": "border — widths + styles",
                "shadow": "shadow — elevation ladder (light + dark variants)",
                "elevation": "elevation — semantic shadow+surface pairings",
                "opacity": "opacity — scale + state values (disabled/hover/pressed)",
                "blur": "blur — backdrop blur scale",
                "motion": "motion — durations, easings, composed transitions",
                "zIndex": "zIndex — named stacking layers",
                "breakpoint": "breakpoint — responsive cut points",
                "gradient": "gradient — brand/accent/subtle presets",
                "component": "component — per-component token blocks (button/input/card/badge/tooltip/modal …)",  # noqa: E501
            }
            return [labels.get(k, k) for k in tree if k not in ("$schema", "meta")]
        except Exception:
            return ["color", "typography", "spacing", "radius", "shadow", "motion", "component"]

    def deliverable_name(self, loop: Loop) -> str:
        """A design loop's document deliverable is DESIGN.md (the design-system doc the
        brief + cycle_nudge instruct the worker to maintain). On completion the watchdog
        surfaces it as a file-backed artifact in the Outputs panel — same path goal's
        REPORT.md takes; without this a finished design loop's DESIGN.md is stranded."""
        return "DESIGN.md"

    async def is_done_signal(self, loop: Loop, findings: list[dict]) -> bool | None:
        # Design done-ness is owned by on_new_cycle (advances phase_status, completes on
        # the last step). Defer the watchdog's generic signal to it.
        return None

    async def on_new_cycle(self, loop: Loop, findings: list[dict], ctx) -> bool:
        """Advance the phase trail from the worker's self-reported step. Each finding
        carries a `step` (the cycle_nudge asks for {cycle, step, …}); match it to a plan
        phase, mark every earlier phase `done` + the matched phase `active`, and COMPLETE
        once the worker reports (or passes) the LAST phase. Design has no gate — the
        worker drives progress — so this is a light status-mirror, not the SDLC gate.
        Returns True iff completed. Owns done-ness (watchdog skips its generic signal)."""
        from personalclaw.loop import store
        from personalclaw.loop.manager import write_brief

        cid = loop.id
        # Persist the worker's chosen token overrides: it writes token_overrides.json in
        # its loop dir (it has file tools, not the loop HTTP API), and we merge it into
        # kind_config so the Tokens/Palette/Exports surfaces + the next brief reflect its
        # design decisions. Without this the brief asked the worker to "record overrides"
        # with no path to actually persist them. Best-effort; merge so the user's FE-set
        # overrides aren't clobbered (worker wins per-key, like any deep-merge).
        self._ingest_worker_overrides(loop, store)
        loop = store.get(cid) or loop
        plan = loop.plan or []
        if not plan or not findings:
            return False
        keys = [self.phase_key(p) for p in plan]
        # Resolve the worker's reported step to a plan index (match step or title, case-
        # insensitively; substring either way so "palette" matches "Color palette").
        # Workers routinely prefix an ordinal ("1. Emit Primitive Token Layer",
        # "2 — Palette"), which matched NOTHING → the trail froze on foundations and the
        # 008a0a9 completion was deferred to the slow per-cycle fallback (same class as the
        # sdlc ordinal-prefix bug, fix 6c7096f). Strip a leading ordinal before matching.
        raw_reported = str(findings[-1].get("step", "")).strip().lower()
        reported = self._strip_step_ordinal(raw_reported)
        idx = -1
        # A worker often reports the step as a BARE 1-based index ("step": 1, "2") rather
        # than a phase slug/title — _strip_step_ordinal leaves a lone number untouched
        # (no trailing separator), so the label match below misses it and the trail froze.
        # Treat a pure integer as a 1-based phase index directly. (observed live: design
        # worker reported step 1,2 → phase_status stuck on the first phase.)
        if reported.isdigit():
            n = int(reported)
            if 1 <= n <= len(plan):
                idx = n - 1
        if idx < 0 and reported:
            for i, p in enumerate(plan):
                title = self._strip_step_ordinal(str(p.get("title", "")).strip().lower())
                cand = f"{p.get('step', '')} {title}".strip().lower()
                if reported == keys[i].lower() or reported in cand or cand and cand in reported:
                    idx = i
                    break
        # Still no label match? Extract a LEADING 1-based index from the raw step
        # ("step 4 — …", "4 — …", "P4: …"). The worker's step TITLE routinely drifts from
        # the plan's phase title (e.g. "Per-state component specs & keyboard/ARIA model" vs
        # the plan's "Per-state specs & keyboard model"), so the substring match misses —
        # but the ordinal it prefixes is reliable. Trust it over the time-based fallback,
        # which otherwise mis-assigns a later step to an EARLIER phase and flips a done
        # phase back to active (observed: cycle-4 "Step 4 — …" reset foundations→active).
        if idx < 0:
            import re

            m = re.match(r"^\s*(?:step|phase|p)?\s*(\d+)\b", raw_reported, re.I)
            if m:
                n = int(m.group(1))
                if 1 <= n <= len(plan):
                    idx = n - 1
        # No parseable step at all: fall back to one phase per (max_cycles / phases) cycles
        # so the trail still advances on a worker that omits `step` — but NEVER regress below
        # the furthest phase already reached (a finding with no step must not undo progress).
        if idx < 0:
            per = max(1, (loop.max_cycles or 30) // max(1, len(plan)))
            idx = min(len(plan) - 1, max(0, (loop.total_cycles - 1) // per))
        status0 = loop.phase_status or {}
        reached = max(
            (i for i, k in enumerate(keys) if status0.get(k) in ("active", "done")), default=-1
        )
        if idx < reached:
            idx = reached  # monotonic: never walk the trail backwards
        status = dict(loop.phase_status or {})
        for i, k in enumerate(keys):
            want = "done" if i < idx else "active" if i == idx else status.get(k, "")
            if k and status.get(k) != want and want:
                store.set_phase_status(cid, k, want)
        try:
            ctx.publish(cid, "phase_advance", {"active": idx, "step": keys[idx]})
        except Exception:
            pass
        # Complete when the worker reports being ON the last phase AND has produced its
        # deliverable doc (DESIGN.md) — the design analogue of the goal/code done-gate.
        if idx >= len(plan) - 1:
            # The design brief tells the worker to persist DESIGN.md via `artifact_save`
            # (kind=markdown), NOT a file write — `wants_workspace=False`, the design doc
            # lives as an artifact. So checking only the loop-dir file (read_deliverable)
            # never sees it and the loop never completes — it spins additive cycles to the
            # budget. Accept EITHER the loop-dir file OR the DESIGN.md artifact tagged
            # loop:<id> (observed: design loop stuck running w/ all 5 steps done).
            has_doc = bool(store.read_deliverable(cid).strip()) or self._has_design_artifact(cid)
            if has_doc:
                for k in keys:
                    if k:
                        store.set_phase_status(cid, k, "done")
                await ctx.complete(cid, "design system delivered")
                return True
        # Re-arm the brief so the worker targets the now-active phase.
        refreshed = store.get(cid)
        if refreshed is not None:
            write_brief(refreshed)
        return False

    @staticmethod
    def _strip_step_ordinal(s: str) -> str:
        """Strip a leading ordinal a worker prefixes onto its reported step/title —
        "1. Emit Primitive Token Layer" / "2 — Palette" / "step 3: …" → the bare label.
        Mirrors sdlc._strip_stage_ordinal so phase matching survives the prefix."""
        import re

        return (
            re.sub(r"^\s*(?:step\s*)?\d+\s*[.–—:)\-]\s*", "", (s or "").strip(), flags=re.I)
            .strip()
            .lower()
        )

    def _has_design_artifact(self, loop_id: str) -> bool:
        """True if a non-empty DESIGN.md artifact tagged ``loop:<id>`` exists. The design
        worker persists DESIGN.md via ``artifact_save`` (not a file), so the completion
        gate must accept the artifact form, not only the loop-dir file. Best-effort."""
        try:
            from personalclaw.artifacts import registry as artifact_registry

            prov = artifact_registry.get_provider()
            if prov is None:
                return False
            for art in prov.list(tag=f"loop:{loop_id}"):
                name = (getattr(art, "name", "") or "").lower()
                if "design" in name or getattr(art, "kind", "") == "markdown":
                    full = prov.get(art.slug)
                    if full and (getattr(full, "content", "") or "").strip():
                        return True
        except Exception:
            logger.debug("design artifact check failed for %s", loop_id, exc_info=True)
        return False

    def _ingest_worker_overrides(self, loop: Loop, store) -> None:
        """Read the worker's token_overrides.json from the loop dir (if any) and merge it
        into kind_config.token_overrides. The worker writes the file with its file tools;
        this is its persist path (it can't call the loop HTTP API the FE uses)."""
        import json

        try:
            d = store.safe_loop_dir(loop.id)
            if d is None:
                return
            f = d / "token_overrides.json"
            if not f.exists():
                return
            ov = json.loads(f.read_text())
            if isinstance(ov, dict) and ov:
                # merge_kind_config deep-merges, so the worker's overrides layer onto any
                # existing (FE-set) ones rather than replacing the whole token_overrides.
                store.merge_kind_config(loop.id, {"token_overrides": ov})
        except Exception:
            pass

    # The canonical design-system phases — the per-kind "space expertise" breakdown every
    # Design loop follows (understand → foundations → palette → type → components → export).
    # Used as the deterministic fallback when the LLM phase-planner is unavailable, so a
    # Design loop ALWAYS has a real phased plan (the vision's "break into phased
    # executions"), never free-runs.
    _DEFAULT_PHASES = [
        {
            "step": "foundations",
            "title": "Foundations & audit",
            "objective": "Understand the brand/product, audit references, and decide which default token axes to override.",  # noqa: E501
        },
        {
            "step": "palette",
            "title": "Color palette",
            "objective": "Set the brand/accent/neutral + semantic color scales; verify light/dark and WCAG contrast.",  # noqa: E501
        },
        {
            "step": "typography",
            "title": "Typography & spacing",
            "objective": "Choose type families, the modular size scale, weights, and the spacing/radius rhythm.",  # noqa: E501
        },
        {
            "step": "components",
            "title": "Core components",
            "objective": "Generate the core React components (button, input, card, …) styled from the tokens; render them on the canvas.",  # noqa: E501
        },
        {
            "step": "export",
            "title": "Document & export",
            "objective": "Write DESIGN.md and produce the export artifacts (token set, CSS variables, React components).",  # noqa: E501
        },
    ]

    def default_phases(self) -> list[dict]:
        """The deterministic canonical phase plan (a fresh copy) — used when a Design
        loop is created WITHOUT an LLM classify pass (e.g. the chat-agent create tool),
        so it still gets the phased breakdown the vision requires rather than free-running."""
        return [dict(p) for p in self._DEFAULT_PHASES]

    def walkthrough(self):
        """The design stepwise planning walkthrough — a DYNAMIC step list (a design pass
        authors the phases for THIS task first, then one artifact per phase), projecting
        the approved breakdown into the unified ``plan``. This is what makes Design a
        REAL planned loop (Understand → phased breakdown → execution plan → loop each
        phase) rather than the old skip-planning free-run."""
        return _DesignWalkthrough()

    async def classify(self, task: str, ask, *, skills=None, workflows=None, agents=None) -> dict:
        """Design intake — break the design task into the design-system phase sequence
        (the per-kind space expertise the vision requires). An LLM tailors the canonical
        phases to THIS task (titles/objectives, drop the irrelevant); on any failure the
        deterministic _DEFAULT_PHASES carry it, so a Design loop always has a real plan."""
        plan = await self._plan_phases(task, ask)
        return {
            "title": "",
            "summary": "",
            "classified": True,
            "intake_rigor": "auto",
            "execution": "solo",
            "roster": [],
            "strategy_id": "orchestrator",
            "clarifying_questions": [],
            "suggested_skill_ids": [],
            "suggested_workflow_ids": [],
            "marketplace_suggestions": [],
            "success_criteria": "",
            "plan": plan,
            "kind_config": {
                "token_overrides": {},
                "targets": "",
                "exports": [],
                # mirror the plan into kind_config so build_brief + the cockpit
                # render the steps even before any task-provisioning.
                "design_steps": [p["title"] for p in plan],
            },
        }

    async def _plan_phases(self, task: str, ask) -> list[dict]:
        """Tailor the canonical design phases to this task via one LLM call; fall back to
        the defaults on any failure/malformed output. Each row is {step, title, objective}
        (phase_key reads step→title)."""
        import json as _json

        # The phase-planner instruction lives in the prompt system (bundled
        # ``task-design-phases``), rendered with the task.
        from personalclaw.prompt_providers.runtime import render_use_case_prompt

        prompt = render_use_case_prompt("design_phases", {"task": task}) or ""
        if not prompt:
            return [dict(p) for p in self._DEFAULT_PHASES]
        try:
            raw = await ask(prompt)
            start, end = raw.find("["), raw.rfind("]")
            if start != -1 and end > start:
                rows = _json.loads(raw[start : end + 1])
                out = [
                    {
                        "step": str(r.get("step", "")).strip() or str(r.get("title", "")).strip(),
                        "title": str(r.get("title", "")).strip(),
                        "objective": str(r.get("objective", "")).strip(),
                    }
                    for r in rows
                    if isinstance(r, dict) and (r.get("title") or r.get("step"))
                ]
                if out:
                    return out
        except Exception:
            pass
        return [dict(p) for p in self._DEFAULT_PHASES]

    def build_brief(self, loop: Loop, context_dir: str = "") -> str:
        cfg = loop.kind_config or {}
        targets = str(cfg.get("targets", "")).strip()
        lines = ["# Design Loop Brief", "", f"**Design task:** {loop.task}", ""]
        if targets:
            lines += [f"**Designing for:** {targets}", ""]
        lines += [
            "Build the design system on top of PersonalClaw's comprehensive default "
            "token set — guide the user through choosing overrides and generating React "
            "components against it.",
            f"**Max cycles:** {loop.max_cycles}",
            "",
            "## The token system",
            "PersonalClaw ships a comprehensive default token set covering every "
            "look-and-feel axis. You DO NOT invent ad-hoc values — you choose overrides on "
            "top of these axes and reference tokens by their dotted path "
            "(e.g. `{color.primitive.brand.500}`, `{radius.lg}`, `{shadow.md}`):",
        ]
        lines += [f"- {axis}" for axis in self._token_axes()]
        lines += [
            "",
            "To CHOOSE overrides, write `token_overrides.json` in your loop dir — a partial "
            "document of the same shape as the defaults (deep-merged over them; the loop "
            "ingests it each cycle so the Tokens/Palette/Exports surfaces + this brief update). "
            'Override **semantic roles** (e.g. `{"color":{"semantic":{"light":{"brand.default":'
            '"#6d28d9"}}}}`) and **primitives** (e.g. `{"color":{"primitive":{"brand":{"500":'
            '"#6d28d9"}}}}`), never component values directly, so light/dark and every '
            "component stay in lockstep.",
        ]
        overrides = (
            cfg.get("token_overrides") if isinstance(cfg.get("token_overrides"), dict) else {}
        )
        if overrides:
            import json as _json

            lines += [
                "",
                "**Overrides chosen so far:**",
                "```json",
                _json.dumps(overrides, indent=2)[:2000],
                "```",
            ]
        if loop.plan:
            # Reflect phase_status (on_new_cycle advances it) so the RE-ARMED brief actually
            # steers: done steps are marked, the active one is flagged "← FOCUS NOW", and a
            # closing line names it. Without this the brief looked identical every cycle and
            # the worker had no signal it had already cleared earlier phases.
            ps = loop.phase_status or {}
            lines += ["", "**Design steps — work through these IN ORDER.**"]
            active_title = ""
            for i, ph in enumerate(loop.plan):
                if not isinstance(ph, dict):
                    continue
                t = str(ph.get("title", "")).strip() or str(ph.get("step", "")).strip() or "(step)"
                obj = str(ph.get("objective", "")).strip()
                key = self.phase_key(ph)
                state = ps.get(key, "")
                mark = "✓ " if state == "done" else "▶ " if state == "active" else ""
                tail = "  ← FOCUS NOW" if state == "active" else ""
                if state == "active":
                    active_title = t
                lines.append(
                    (
                        f"{i+1}. {mark}**{t}** — {obj}{tail}"
                        if obj
                        else f"{i+1}. {mark}**{t}**{tail}"
                    )
                )
            if active_title:
                lines += [
                    "",
                    f'**This cycle: advance "{active_title}".** When it\'s substantially '
                    "done, say so in your finding's `step` (use the NEXT step's name) so the loop moves on.",  # noqa: E501
                ]
        if context_dir:
            lines += [
                "",
                f"**Project context dir:** `{context_dir}` — the design artifacts "
                "(token sets, components, DESIGN.md) and shared project context live here.",
            ]
        return "\n".join(lines)

    def cycle_nudge(self, loop: Loop, loop_dir: str) -> str:
        """Per-cycle trigger for the design loop. The rich design surfaces (canvas,
        screenshot extraction, exports) layer on in the Design slice; the loop spine
        — read status/brief/guidance, advance the current design step, MUST write a
        finding — holds now so the kind runs on the unified engine."""
        return "\n".join(
            [
                f"Run the next autonomous cycle for design loop {loop.id} "
                f"(working dir for loop files: {loop_dir}). Steps: (1) check status.json — "
                "if not 'running', stop; (2) read brief.md; (3) apply + delete guidance.txt "
                "if present; (4) advance the CURRENT design step (tokens, a component, a "
                "palette decision) toward the design system.",
                "",
                f"Before you end this turn you MUST write findings/cycle_NNN.json to {loop_dir} "
                "(next sequential N) — {cycle, step, summary, key_insight, artifacts}. Save "
                f"design outputs as artifacts tagged `loop:{loop.id}` via artifact_save:",
                f"  • Each React COMPONENT → `artifact_save(kind='react', tags=['loop:{loop.id}'])` "  # noqa: E501
                "where content is JSX defining a top-level `App` component authored against the "
                "window React/ReactDOM globals (no imports/exports) — it renders live on the "
                "loop's design Canvas. Style it with the design system's token VALUES (read them "
                "from the brief / token set), so the component reflects the chosen overrides.",
                f"  • The design-system doc → `artifact_save(name='DESIGN.md', kind='markdown', "
                f"tags=['loop:{loop.id}'])`.",
                f"  • Token decisions → write/update `{loop_dir}/token_overrides.json` (partial "
                "token document; the loop ingests + merges it each cycle so the design surfaces "
                "reflect your choices). Reference its values from the components you generate.",
                "Then end the turn.",
            ]
        )


class _DesignWalkthrough:
    """Design-kind planning walkthrough — a DYNAMIC step list (design pass first, like
    code). Wraps design_plan_briefs' pure briefs/parsers; projects the approved phase
    breakdown into the unified ``plan`` so launch is identical whether the user
    classified or walked the plan."""

    step_mode = "dynamic"

    def __init__(self) -> None:
        from personalclaw.agents.defaults import LOOP_PLANNER_AGENT_NAME

        # No design-specific planner agent yet; the generic loop planner drives the
        # design pass (its briefs carry all the design-space framing).
        self.planner_agent = LOOP_PLANNER_AGENT_NAME

    def default_steps(self) -> list[dict]:
        return []  # dynamic mode — the design pass authors the steps

    def build_design_brief(self, task: str, workspace_dir: str, design_inputs=None) -> str:
        from personalclaw.loop import design_plan_briefs as pw

        return pw.build_design_brief(task, workspace_dir, design_inputs=design_inputs)

    def parse_steps_sentinel(self, raw: str):
        from personalclaw.loop import design_plan_briefs as pw

        return pw.parse_steps_sentinel(raw)

    def build_step_brief(self, task, step, *, approved, workspace_dir):
        from personalclaw.loop import design_plan_briefs as pw

        return pw.build_step_brief(task, step, approved=approved, workspace_dir=workspace_dir)

    def parse_artifact_sentinel(self, raw: str):
        from personalclaw.loop import design_plan_briefs as pw

        return pw.parse_artifact_sentinel(raw)

    def project_to_spec(self, session) -> dict:
        """Project the approved `build_plan` artifact into the unified ``plan`` (design
        phase rows {step,title,objective}) + mirror titles into kind_config.design_steps
        (the cockpit/brief render them). Falls back to the canonical default phases if
        the walkthrough produced no build_plan, so the user always lands launchable."""
        from personalclaw.loop import design_plan_briefs as pw

        bp = next((s for s in reversed(session.steps) if s.kind == "build_plan"), None)
        plan = pw.build_plan_to_phases(bp.artifact) if bp else []
        if not plan:
            plan = DesignKind().default_phases()
            logger.info(
                "design walkthrough: no build_plan for %s — using default phases",
                session.project_id,
            )
        spec: dict = {"plan": plan}
        # Carry a summary from the brief step (the design intent) if present.
        for s in session.steps:
            md = str((s.artifact or {}).get("markdown", "")).strip()
            if s.kind == "brief" and md:
                spec["summary"] = md.splitlines()[0].strip()[:300]
                break
        # Mirror the phase titles into kind_config + merge the APPROVED token system
        # (every token-step's token_overrides, deep-merged in order) so the cockpit
        # opens populated with the approved palette/type — the D4 approve→populate
        # guarantee, authoritative server-side (not reliant on the FE previewing each
        # step). Layer the approved overrides over any already on the loop.
        from personalclaw.loop import design_tokens as dt
        from personalclaw.loop import store as _store

        loop = _store.get(session.project_id)
        base = dict(loop.kind_config) if loop and loop.kind_config else {}
        base["design_steps"] = [p["title"] for p in plan]
        approved_ov = pw.collect_token_overrides(session.steps)
        if approved_ov:
            existing = _to if isinstance((_to := base.get("token_overrides")), dict) else {}
            base["token_overrides"] = dt.deep_merge(existing, approved_ov)
        spec["kind_config"] = base
        return spec


register(DesignKind())
