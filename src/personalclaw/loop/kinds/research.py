"""Research kind — deep, iterative web research toward a synthesized report.

A specialization of the open-ended Goal loop (it reuses Goal's separate-judge
done-ness, granularity dial, walkthrough, validation, and task decomposition —
no agent certifies its own research). What differs is the *subject-matter
expertise*: the worker runs a **deep-research plan** with a dynamically evolving
set of subtopics, searches the web + fetches article content, and synthesizes
findings into an output whose **template and manner are dictated by the user's
request** — scaling to as many sources as the signal demands.

The novelty over a plain open-ended goal is the per-cycle research discipline
encoded in the brief + cycle nudge (expand/prune subtopics, breadth-then-depth,
source accounting, evidence-first synthesis) and a research-shaped kind_config
(``subtopics`` / ``output_template`` / ``output_manner`` / ``source_budget``).
Engine-agnostic: this is just another ``LoopKindStrategy`` + one ``register()``.
"""

from __future__ import annotations

from personalclaw.loop.kinds import register
from personalclaw.loop.kinds.goal import GoalKind
from personalclaw.loop.loop import Loop


class ResearchKind(GoalKind):
    kind = "research"
    label = "Research"
    description = (
        "Deep web research — evolving subtopics, search + fetch across many sources, "
        "synthesized into a report in the manner you ask for."
    )
    # Research wants no required workspace (the report is the deliverable; a bound
    # workspace, when present, is where the report file lands — Goal's brief handles that).
    wants_workspace = False

    def default_kind_config(self) -> dict:
        # Research is always open-ended (returns-exhaustion on the granularity dial is
        # exactly the "how deep / how many sources" control). Carries the research-
        # specific shape the brief + nudge read.
        cfg = super().default_kind_config()
        cfg.update(
            {
                "goal_type": "open_ended",
                # The living research plan — seeded at classify, expanded/pruned each cycle.
                "subtopics": [],
                # How the user wants the output shaped (a template name, an outline, or a
                # free description) and the MANNER (tone/format/audience). Empty → the worker
                # infers a sensible report structure but still honors any stated manner.
                "output_template": "",
                "output_manner": "",
                # Soft ceiling on sources/articles the loop will pull before it must start
                # converging (0 = no cap → bounded only by the granularity dial + max_cycles).
                "source_budget": 0,
                # Breadth×depth shape of each cycle (GPT-Researcher's 3×2 default): how many
                # subtopics to advance in parallel per cycle (breadth) and how many sources
                # to fetch+read per subtopic (depth). The budget axis that turns "search the
                # web" into a bounded, predictable sweep.
                "breadth": 3,
                "depth": 2,
                # Per-tool call ceiling PER CYCLE (a budget axis separate from cycle count +
                # token budget) — keeps one cycle from runaway-calling search/fetch.
                "max_uses_per_cycle": 12,
                "primary_deliverable": "RESEARCH.md",
            }
        )
        return cfg

    async def classify(self, task: str, ask, *, skills=None, workflows=None, agents=None) -> dict:
        """Reuse Goal's planner, then pin the research shape: force open_ended, seed
        ``subtopics`` from the planner's sub-goals (the first cut of the research plan),
        and default the deliverable to RESEARCH.md so the brief/nudge/Outputs agree."""
        spec = await super().classify(task, ask, skills=skills, workflows=workflows, agents=agents)
        kc = dict(spec.get("kind_config") or {})
        kc["goal_type"] = "open_ended"
        # Sub-goals ARE the initial subtopic list for a research loop.
        kc.setdefault("subtopics", list(kc.get("sub_goals", []) or []))
        if not str(kc.get("primary_deliverable", "")).strip():
            kc["primary_deliverable"] = "RESEARCH.md"
        spec["kind_config"] = kc
        return spec

    def deliverable_name(self, loop: Loop) -> str:
        # Research always produces a document; default RESEARCH.md, but honor an explicit
        # primary_deliverable the user named (e.g. a specific report filename).
        cfg = loop.kind_config or {}
        primary = str(cfg.get("primary_deliverable", "") or "").strip()
        return primary or "RESEARCH.md"

    def turn_directive(self, loop: Loop) -> str:
        """Prepend the execution-plan phase directive (inherited) with the research
        discipline so every cycle knows it is doing deep research, not generic work."""
        base = super().turn_directive(loop)
        cfg = loop.kind_config or {}
        manner = str(cfg.get("output_manner", "")).strip()
        bits = [
            "[Deep research — expand/prune your subtopic list as findings warrant, "
            "`web_search` broadly then `web_fetch` and read the highest-signal sources' "
            "actual content (not just snippets), and track which URLs you've covered so "
            "you don't repeat them.]"
        ]
        if manner:
            bits.append(
                f"[Output manner — the report MUST follow the user's requested manner: {manner}.]"
            )
        return (base + " " + " ".join(bits)).strip() if base else " ".join(bits)

    def build_brief(self, loop: Loop, context_dir: str = "") -> str:
        """Goal's brief (goal/sub-goals/scope/DoD/deliverable/context) + a Research
        Plan section: the subtopics, the output template + manner the user asked for,
        the source budget, and the deep-research method the worker follows each cycle."""
        brief = super().build_brief(loop, context_dir)
        cfg = loop.kind_config or {}
        subtopics = [str(s).strip() for s in (cfg.get("subtopics") or []) if str(s).strip()]
        template = str(cfg.get("output_template", "")).strip()
        manner = str(cfg.get("output_manner", "")).strip()

        def _int(key: str, default: int) -> int:
            try:
                return int(cfg.get(key, default) or default)
            except (TypeError, ValueError):
                return default

        budget = _int("source_budget", 0)
        breadth = _int("breadth", 3)
        depth = _int("depth", 2)
        max_uses = _int("max_uses_per_cycle", 12)
        extra = [
            "",
            "---",
            "",
            "## Research Plan",
            "",
            "This is a **deep-research loop**. Each cycle advances a living research "
            "plan toward a synthesized report — you are expected to read MANY sources "
            "(tens to hundreds, as the signal demands), not stop at the first few.",
        ]
        extra += ["", "**Subtopics (living — expand and prune as you learn):**"]
        extra += [f"- {s}" for s in subtopics] or [
            "- (none yet — derive the initial subtopics from the request on cycle 1)"
        ]
        if template:
            extra += [
                "",
                f"**Output template / structure (REQUIRED):** {template}",
                "Shape the report to this structure exactly.",
            ]
        if manner:
            extra += [
                "",
                f"**Output manner (REQUIRED):** {manner}",
                "This governs tone, format, depth, and audience — honor it precisely; "
                "the report is judged on following the requested manner, not just on facts.",
            ]
        extra += [
            "",
            '**Tools:** use `web_search` to find sources (pass `use_case:"search-news"` '
            "for recency-sensitive subtopics) and `web_fetch` to read the actual content of a "
            "result URL (only fetch URLs that web_search surfaced; large pages paginate — "
            "follow the returned next_index). Cite the URLs you fetched, not snippets alone.",
        ]
        extra += [
            "",
            "**Method each cycle (breadth×depth):** "
            f"(1) advance the top **{breadth}** open subtopics this cycle (breadth); "
            f"(2) for each, `web_search` then `web_fetch` the most promising **{depth}** sources "
            "(depth) and read their real content; (3) extract evidence with citations; "
            "(4) update each subtopic's report section; (5) revise the subtopic list — add gaps "
            "you discovered, mark covered ones, prune dead ends; (6) record sources_checked + "
            "new_findings_count in the finding so the judge can see whether returns are still "
            f"accruing. Keep web tool calls to ~**{max_uses}** per cycle — if you need more, that "
            "is a sign to converge or split into another cycle.",
        ]
        extra += [
            "",
            "**Persist sources:** for each high-value source you fetch, save it to the "
            "knowledge base with `knowledge_create` (type `bookmark`, the source URL, a one-line "
            "summary) so the evidence is browsable + survives the loop and the report can cite "
            "back into it. Skip near-duplicates of what you've already saved.",
        ]
        if budget > 0:
            extra += [
                "",
                f"**Source budget:** aim to consult up to ~{budget} sources before "
                "converging; if returns flatten earlier, start synthesizing.",
            ]
        extra += [
            "",
            "**Convergence:** a separate judge scores marginal value each cycle; when "
            "new sources stop changing the conclusions (returns exhausted on your "
            "granularity dial), the loop completes with the report as the deliverable.",
        ]
        return brief + "\n" + "\n".join(extra)

    def cycle_nudge(self, loop: Loop, loop_dir: str) -> str:
        """Goal's per-cycle trigger + an explicit deep-research step so a less-steerable
        worker actually searches/fetches/synthesizes rather than reasoning from memory."""
        base = super().cycle_nudge(loop, loop_dir)
        addendum = (
            "This is a DEEP RESEARCH cycle: do not answer from memory. Call `web_search` to "
            "find NEW primary sources for an open subtopic, then `web_fetch` the most "
            "promising result URLs to read their actual content, and fold cited evidence "
            "into the report. In the finding, set sources_checked to the URLs you read this "
            "cycle and new_findings_count to how many materially changed the report — these "
            "drive the judge's returns assessment. Then revise the subtopic list (add "
            "discovered gaps, mark covered, prune dead ends)."
        )
        return base + "\n" + addendum

    def walkthrough(self):
        """Research-specific planning walkthrough (intent → subtopics → output
        format/manner → execution_plan) — so a research loop's Plan Review reads as a
        deep-research PLAN, not a goal decomposition. Reuses the goal walkthrough's
        machinery (planner agent, sentinels, generic brief assembly); only the step set
        + per-step artifact contract + spec projection differ."""
        return _ResearchWalkthrough()


# The goal walkthrough delegate is reused wholesale (planner agent, fixed-mode hooks);
# only the steps/briefs/projection are research-specific (overridden below).
from personalclaw.loop.kinds.goal import _GoalWalkthrough  # noqa: E402


class _ResearchWalkthrough(_GoalWalkthrough):
    """Research-kind planning walkthrough — a FIXED step list keyed to deep research."""

    def default_steps(self) -> list[dict]:
        from personalclaw.loop import research_plan_briefs as pw

        return pw.default_steps()

    def build_step_brief(self, task, step, *, approved, workspace_dir):
        from personalclaw.loop import research_plan_briefs as pw

        return pw.build_step_brief(task, step, approved=approved)

    def project_to_spec(self, session) -> dict:
        """Project intent/subtopics/output/execution_plan into the unified spec:
        success_criteria + summary top-level; subtopics → ``plan`` rows (so the worker
        tracks them as tasks, like goal sub-goals) AND ``kind_config.subtopics``; the
        output step → ``kind_config.output_template``/``output_manner``; execution_plan →
        ``kind_config.execution_plan``."""
        spec: dict = {}
        kc_patch: dict = {}
        for step in session.steps:
            art = step.artifact or {}
            if step.kind == "intent":
                sc = str(art.get("success_criteria", "")).strip()
                if sc:
                    spec["success_criteria"] = sc
                md = str(art.get("markdown", "")).strip()
                if md:
                    spec["summary"] = md.splitlines()[0].strip()[:300]
            elif step.kind == "subtopics":
                subs = [str(x).strip() for x in (art.get("subtopics") or []) if str(x).strip()]
                if subs:
                    spec["plan"] = [{"title": s} for s in subs]
                    kc_patch["subtopics"] = subs
                    # subtopics double as the initial sub_goals so the shared task
                    # decomposition (tasks_link) tracks them, exactly as goal does.
                    kc_patch["sub_goals"] = subs
            elif step.kind == "output":
                tmpl = str(art.get("output_template", "")).strip()
                manner = str(art.get("output_manner", "")).strip()
                if tmpl:
                    kc_patch["output_template"] = tmpl
                if manner:
                    kc_patch["output_manner"] = manner
            elif step.kind == "execution_plan":
                phases = [
                    p
                    for p in (art.get("execution_plan") or [])
                    if isinstance(p, dict)
                    and (str(p.get("role", "")).strip() or str(p.get("target", "")).strip())
                ]
                if phases:
                    kc_patch["execution_plan"] = phases
        if kc_patch:
            from personalclaw.loop import store as _store

            loop = _store.get(session.project_id)
            base = dict(loop.kind_config) if loop and loop.kind_config else {}
            base.update(kc_patch)
            spec["kind_config"] = base
        return spec


register(ResearchKind())
