"""Goal kind — open-ended / verifiable / monitor research + action loops.

Done-ness is **type-driven** (the principle that no agent certifies its own work):
verifiable runs a deterministic ``verify_command``; open-ended consults a separate
judge subagent's ROI verdict; monitor never self-completes. Slice 1 supplies the
classification/config shape + phase keying; the full classify/judge/ratchet
behavior ports from the legacy ``loops/`` engine in Slice 2.
"""

from __future__ import annotations

from personalclaw.loop.goal_meta import GOAL_TYPES as _GOAL_TYPES, GRANULARITIES as _GRANULARITIES
from personalclaw.loop.kinds import register
from personalclaw.loop.loop import Loop


class GoalKind:
    kind = "goal"
    label = "Goal"
    description = "Research + action toward a goal — verifiable, open-ended, or monitoring."
    wants_workspace = False
    default_agent = "personalclaw-loop"

    def default_kind_config(self) -> dict:
        return {
            "goal_type": "open_ended",
            "granularity": "balanced",
            "sub_goals": [],
            "deliverables": [],
            "scope": [],
            "rubric": [],
            "ratchet_mode": "strict",
            "verify_command": "",
        }

    def phase_key(self, phase: dict) -> str:
        # Goal sub-goals are keyed by title (no formal stage id).
        return str(phase.get("title", "")).strip()

    def _active_phase_index(self, loop: Loop) -> int:
        """Index of the execution_plan phase the UPCOMING cycle belongs to (cycle-count
        based): walk each phase's cumulative min_cycles window; -1 with no plan."""
        plan = list((loop.kind_config or {}).get("execution_plan", []) or [])
        if not plan:
            return -1
        elapsed = 0
        for i, phase in enumerate(plan):
            try:
                need = max(1, int(phase.get("min_cycles", 1)))
            except (TypeError, ValueError):
                need = 1
            elapsed += need
            if loop.total_cycles < elapsed:
                return i
        return len(plan) - 1

    def turn_capabilities(self, loop: Loop) -> tuple[list[str], list[str]]:
        """Skills + workflows to load for the UPCOMING cycle: the active execution_plan
        phase's per-phase ids ∪ the always-on baseline (loop.skill_ids/workflow_ids).
        Bare baseline when there's no plan. Order-stable, deduped (baseline first)."""
        skills = list(loop.skill_ids or [])
        workflows = list(loop.workflow_ids or [])
        idx = self._active_phase_index(loop)
        if idx >= 0:
            phase = (loop.kind_config or {}).get("execution_plan", [])[idx]
            for sid in (phase.get("skill_ids") or []):
                if sid not in skills:
                    skills.append(str(sid))
            for wid in (phase.get("workflow_ids") or []):
                if wid not in workflows:
                    workflows.append(str(wid))
        return skills, workflows

    def turn_directive(self, loop: Loop) -> str:
        """A one-block directive naming the CURRENT execution_plan phase + its target,
        prepended to the cycle nudge so the worker focuses on this phase. Empty with no
        plan (the flat goal drives the cycle)."""
        from personalclaw.prompt_providers.runtime import render_snippet_block
        plan = list((loop.kind_config or {}).get("execution_plan", []) or [])
        idx = self._active_phase_index(loop)
        if idx < 0:
            return ""
        phase = plan[idx]
        role = str(phase.get("role", "")).strip()
        target = str(phase.get("target", "")).strip()
        agent_name = str(phase.get("agent_name", "")).strip()
        if not (role or target):
            return ""
        label = f"phase {idx + 1}/{len(plan)}" + (f" — {role}" if role else "")
        # The next-phase exit signal is shown only when there IS a next phase that
        # declares one; pre-resolve it here so the snippet just substitutes.
        next_exit = str(phase.get("phase_exit", "")).strip() if idx + 1 < len(plan) else ""
        # The phase directive text lives in the prompt system (bundled snippet
        # ``loop-goal-phase-directive``, bindable in Settings → Prompts).
        return render_snippet_block(
            "loop-goal-phase-directive",
            {"label": label, "target": target, "agent_name": agent_name, "next_exit": next_exit},
        )

    async def is_done_signal(self, loop: Loop, findings: list[dict]) -> bool | None:
        """Type-driven done-ness (no agent certifies its own work):
        - verifiable → the supervisor RUNS verify_command + reads the exit code, AND
          (when the goal has multiple sub-goals) a judge confirms EVERY sub-goal is met;
        - open_ended → a separate judge subagent's ROI verdict (lands in 2c.ii);
        - monitor → never self-completes (only a user Stop ends it).
        None = defer (can't tell yet / no signal) → the watchdog bounds by budget."""
        cfg = loop.kind_config or {}
        goal_type = str(cfg.get("goal_type", "open_ended"))
        if goal_type == "verifiable":
            from personalclaw.loop.gates import run_verify_command
            ok = await run_verify_command(str(cfg.get("verify_command", "")),
                                          loop.workspace_dir or None, label="verify")
            if ok is not True:
                return ok  # False (check failed) / None (couldn't run) → not done yet
            # The check passed — but a worker can point verify_command at a SUBSET of a
            # multi-sub-goal goal (e.g. `npm test` green after only the engine phase, while
            # the AI / UI / never-lose-proof sub-goals are unbuilt). A green command on a
            # partial build then falsely completes the whole goal (observed live: goal
            # b7abd778 marked done after phase 1/3). So when the goal declares MULTIPLE
            # sub-goals, the command passing is necessary but not sufficient — a separate
            # judge must confirm EVERY sub-goal is met before we call the goal done.
            sub_goals = [str(s).strip() for s in (cfg.get("sub_goals", []) or []) if str(s).strip()]
            if len(sub_goals) <= 1:
                return True  # single/no sub-goal → the command IS the whole goal
            return await self._all_sub_goals_met(loop, sub_goals, findings)
        if goal_type == "monitor":
            # Monitor never self-completes — only a user Stop ends it.
            return False
        return await self._assess_open_ended(loop, findings)

    async def _all_sub_goals_met(self, loop: Loop, sub_goals: list[str],
                                 findings: list[dict]) -> bool | None:
        """A strict judge over a verifiable goal's sub-goals: PASS only if the evidence
        from completed cycles shows EVERY sub-goal is met. Guards against a green
        verify_command on a partial build (the command may exercise only one sub-goal).
        Returns True (all met), False (≥1 unmet → keep going), or None (judge
        unavailable → defer; the watchdog still bounds by budget). Conservative: any
        ambiguity is NOT a pass."""
        if not findings:
            return None
        from personalclaw.loop.gates import judge_verdict, verdict_is_pass, verdict_rendered
        from personalclaw.prompt_providers.runtime import render_use_case_prompt
        recent = findings[-6:]
        evidence = "\n".join(
            f"- cycle {f.get('cycle')}: {str(f.get('summary', '') or f.get('key_insight', ''))[:300]}"
            for f in recent)
        criteria = "\n".join(f"- {s}" for s in sub_goals)
        # The sub-goal completion gate lives in the prompt system (bundled
        # ``task-subgoal-judge``, bindable in Settings → Prompts).
        prompt = render_use_case_prompt(
            "subgoal_judge",
            {"task": loop.task, "criteria": criteria, "evidence": evidence},
        )
        if not prompt:
            return None
        raw = await judge_verdict(prompt)
        if verdict_is_pass(raw):
            return True
        # A real FAIL → keep cycling. A non-verdict (judge/provider unavailable) → defer
        # (None), NOT a clean False, so the watchdog can flag a degraded done-ness brain
        # rather than silently spin; budget still caps the loop.
        return False if verdict_rendered(raw) else None

    def budget_stop_genuine(self, loop: Loop) -> bool:
        """For a MONITOR goal the cycle budget IS the intended watch window, so
        reaching it is a clean completion — not the error-flavored 'stopped before the
        goal was met' that an open_ended/verifiable budget stop implies (those may have
        unfinished work). Only monitor returns True."""
        return str((loop.kind_config or {}).get("goal_type", "open_ended")) == "monitor"

    async def _assess_open_ended(self, loop: Loop, findings: list[dict]) -> bool | None:
        """Open-ended done-ness: a SEPARATE judge subagent (never the worker) scores
        the latest cycle's done-ness + marginal value; the deterministic granularity
        dial decides returns-exhaustion. The judge advises; the supervisor (watchdog)
        decides. Ported from loops/watchdog._assess_open_ended — persists the verdict
        + marginal trail; the SSE publish is added by the watchdog in 2c(iii).
        Returns True (done / returns-exhausted), else None (defer; a judge failure
        is observable-but-not-a-clean-False so the watchdog can surface degradation)."""
        if not findings:
            return None
        from personalclaw.loop import store
        from personalclaw.loop import judge as judge_mod
        from personalclaw.loop import instrument
        from personalclaw.loop.granularity import returns_exhausted_calibrated
        finding = findings[-1]
        # P4 canary: once per loop-run, prove the done-ness judge can tell a strong cycle
        # from an empty one before trusting ANY of its verdicts. A blind judge (mis-bound
        # model / broken rubric) would otherwise complete the loop on plausible garbage. On
        # a confirmed-blind judge we DEFER (return None — never a clean False/True) and record
        # ``judge_calibrated=False``; the watchdog reads that flag and halts the loop to
        # NEEDS_INPUT with a judge_blind event (the assessment layer can't publish, so the
        # flag is the seam). A probe that can't run defers without caching (retry next cycle).
        cfg0 = loop.kind_config or {}
        if cfg0.get("judge_calibrated") is False:
            return None  # previously confirmed blind → defer; watchdog owns the halt
        if "judge_calibrated" not in cfg0:
            async def _probe_assess(goal, dod, fnd, prior):
                return await judge_mod.assess_cycle(goal, dod, fnd, prior)
            trustworthy = await instrument.probe_judge(_probe_assess)
            if trustworthy is not None:  # None = probe couldn't run → don't cache, retry next cycle
                store.set_kind_config_key(loop.id, "judge_calibrated", bool(trustworthy))
                if trustworthy is False:
                    return None  # blind → defer; watchdog surfaces judge_blind + NEEDS_INPUT
        cycle = int(finding.get("cycle", len(findings)))
        # Slice C (O-E2): give the judge whatever ground-truth anchor the goal declares —
        # a verify_command it can run itself and/or named deliverable files it can read —
        # so an open-ended goal that names concrete artifacts is scored on observed ground
        # truth, not the worker's narration. A goal with neither stays transcript-only.
        cfg = loop.kind_config or {}
        deliverables = [str(d).strip() for d in (cfg.get("deliverables", []) or []) if str(d).strip()]
        # The judge must read the SAME dir the worker wrote to — workspace_dir when the
        # loop bound a codebase, else the project context dir (an open-ended goal usually
        # has no explicit workspace_dir). Using loop.workspace_dir directly would miss the
        # deliverable for the common context-dir case and silently defeat the ground-truth
        # read (observed live: goal 0fef190e had workspace_dir='' + a deliverable).
        from personalclaw.loop.loop import effective_dir
        # The worker may write the deliverable to the loop's OWN dir when no workspace is
        # bound (observed live V6: an unbound open-ended loop wrote REPORT.md to the loop dir,
        # so a workspace-only ground-truth read wrongly reported "no proof it exists"). Give
        # the judge the loop dir as a fallback search location + resolve the kind's canonical
        # deliverable (REPORT.md) when the goal declared none, so the ground-truth read matches
        # the same file the watchdog graduates.
        _loop_dir = store.safe_loop_dir(loop.id)
        fallback_dirs = [str(_loop_dir)] if _loop_dir is not None else []
        gt_deliverables = deliverables or ([self.deliverable_name(loop)] if self.deliverable_name(loop) else [])
        try:
            verdict = await judge_mod.assess_cycle(
                loop.task, loop.success_criteria or "", finding, findings[:-1],
                verify_command=str(cfg.get("verify_command", "")),
                workspace=effective_dir(loop) or None,
                deliverables=gt_deliverables, fallback_dirs=fallback_dirs)
        except Exception:
            verdict = None
        if verdict is None:
            # No verdict → can't quality-assess. None (defer) — NOT a clean False —
            # so the watchdog can flag the done-ness brain as degraded (G3) rather
            # than silently never completing. Budget still bounds a capped loop.
            return None
        # P4 adversarial-skeptic: a HIGH-stakes verdict (a claimed completion or a claimed
        # regression) must survive a second independent judge told to REFUTE it before the
        # supervisor acts on it. A lone judge that hallucinates "done" would otherwise
        # complete the loop on plausible-but-wrong grounds; the skeptic is the majority-of-two
        # guard. Non-consequential cycles skip it (cost is paid only where it changes a decision).
        if verdict.done or verdict.regressed:
            try:
                skeptic = await judge_mod.assess_cycle_skeptic(
                    loop.task, loop.success_criteria or "", finding, findings[:-1],
                    verify_command=str(cfg.get("verify_command", "")),
                    workspace=effective_dir(loop) or None,
                    deliverables=gt_deliverables, fallback_dirs=fallback_dirs)
            except Exception:
                skeptic = None
            verdict = judge_mod.adjudicate(verdict, skeptic)
        trail = store.record_marginal_score(loop.id, verdict.marginal_value)
        # Keep the quality trail alongside (the calibrated band's variance sample + a
        # future quality-regression signal); we read the marginal trail for exhaustion.
        store.record_quality_score(loop.id, verdict.quality_score)
        granularity = str((loop.kind_config or {}).get("granularity", "balanced"))
        # Record the calibrated band on the verdict for observability (what bar this
        # cycle's marginal value was actually judged against), then persist the verdict.
        from personalclaw.loop.granularity import calibrated_band, dial_for
        _setting = dial_for(granularity)
        if _setting is not None:
            verdict.band_used = calibrated_band(trail, _setting.threshold)
        store.write_verdict(loop.id, cycle, {"cycle": cycle, **verdict.to_dict()})
        if verdict.done:
            return True
        # P4 variance-aware exhaustion: the per-cycle bar is max(2σ, dial-threshold), so a
        # noisy marginal signal must fall further below the line before the loop calls it
        # done — guarding against completing on a variance dip. Falls back to the fixed
        # dial until the trail is long enough to trust its own σ.
        if returns_exhausted_calibrated(trail, granularity):
            return True
        return False

    async def classify(self, task: str, ask, *, skills=None, workflows=None, agents=None) -> dict:
        """Wrap the goal classifier (goal_type / rigor / solo-vs-multi + roster /
        sub-goals / verify_command) and normalize to the unified shape: sub_goals →
        plan rows; goal_type/granularity/sub_goals/deliverables/verify_command →
        kind_config."""
        from personalclaw.loop import classify as goal_classify
        c = await goal_classify.classify(task, ask, skills_catalog=skills,
                                         workflows_catalog=workflows, agents_catalog=agents)
        d = c.to_dict()
        return {
            "title": d.get("title", ""), "summary": "", "classified": d.get("classified", True),
            "intake_rigor": d.get("intake_rigor", "auto"), "execution": d.get("execution", "solo"),
            "roster": d.get("roster", []), "strategy_id": d.get("strategy_id", "orchestrator"),
            # The planner's rationale for its rigor / solo-vs-multi picks — the Plan
            # Review surfaces them so the user sees WHY before approving.
            "rigor_reason": d.get("rigor_reason", ""), "strategy_reason": d.get("strategy_reason", ""),
            "clarifying_questions": d.get("clarifying_questions", []),
            "suggested_skill_ids": d.get("suggested_skill_ids", []),
            "suggested_workflow_ids": d.get("suggested_workflow_ids", []),
            "marketplace_suggestions": d.get("marketplace_suggestions", []),
            "success_criteria": d.get("success_criteria", ""),
            # sub-goals become the plan's phases (keyed by title — see phase_key).
            "plan": [{"title": s} for s in d.get("sub_goals", []) if str(s).strip()],
            "kind_config": {
                "goal_type": d.get("goal_type", "open_ended"),
                "granularity": "balanced",
                "sub_goals": d.get("sub_goals", []),
                "deliverables": d.get("deliverables", []),
                "primary_deliverable": d.get("primary_deliverable", ""),
                "verify_command": d.get("verify_command", ""),
                "execution_plan": d.get("execution_plan", []),
            },
        }

    def validate_config(self, config: dict) -> tuple[list[str], list[str]]:
        """Goal-kind pre-flight: goal_type/granularity validity, a verifiable goal's
        done-ness signal, and screening the unattended verify_command. Returns
        (errors, warnings) folded into the shared validator's result."""
        from personalclaw.security import audit_bash_command
        errors: list[str] = []
        warnings: list[str] = []
        cfg = config.get("kind_config") if isinstance(config.get("kind_config"), dict) else config
        goal_type = str(cfg.get("goal_type", "open_ended")).strip() or "open_ended"
        if goal_type not in _GOAL_TYPES:
            errors.append(f"Unknown goal type {goal_type!r}.")
        granularity = str(cfg.get("granularity", "balanced")).strip() or "balanced"
        if granularity not in _GRANULARITIES:
            errors.append(f"Unknown granularity {granularity!r}.")
        verify_command = str(cfg.get("verify_command") or "").strip()
        if goal_type == "verifiable" and not verify_command \
                and not str(config.get("success_criteria") or "").strip():
            warnings.append("Verifiable goal has no verify command or success criteria — "
                            "it can't self-complete until one is set.")
        if verify_command:
            danger = audit_bash_command(verify_command)
            if danger:
                errors.append(f"Verify command rejected — {danger}.")
        # P6: validate any optional tick-engine keys (min_dwell_secs / min_findings /
        # metric_pass / metric_hold) on the execution_plan phases, so a malformed dwell
        # or an inverted metric band is caught at intake rather than ignored at runtime.
        from personalclaw.loop.tick import validate_step_phase
        for phase in (cfg.get("execution_plan", []) or []):
            errors.extend(validate_step_phase(phase))
        return errors, warnings

    def deliverable_name(self, loop: Loop) -> str:
        """The on-disk document deliverable the worker maintains. An explicit
        ``primary_deliverable`` (a filename the goal named, e.g. SPEC.md) wins over
        the goal-type default (open_ended → REPORT.md, monitor → MONITOR_LOG.md,
        verifiable → none) so the brief/nudge/DoD/Outputs all name the SAME file the
        goal actually asks for. On completion the watchdog surfaces it as a
        file-backed artifact in the Outputs panel."""
        cfg = loop.kind_config or {}
        goal_type = str(cfg.get("goal_type", "open_ended"))
        # verifiable has no document deliverable — an explicit name never resurrects one.
        if goal_type == "verifiable":
            return ""
        primary = str(cfg.get("primary_deliverable", "") or "").strip()
        return primary or _deliverable_name(goal_type)

    def walkthrough(self):
        """The goal stepwise planning walkthrough — a FIXED ordered step list
        (intent → sub-goals → quorum → execution_plan), projecting into the unified
        loop spec. Wraps the legacy goal plan-walkthrough's pure briefs/parsers."""
        return _GoalWalkthrough()

    def build_brief(self, loop: Loop, context_dir: str = "") -> str:
        """Ported from loops/manager.write_brief — goal/sub-goals/scope/DoD/
        deliverables/context. Pure: takes the resolved project ``context_dir``.
        (Intake-clarification + orchestrator framing fold in when the manager wires
        provisioning in 2c; the durable spec the worker reads each cycle is here.)"""
        cfg = loop.kind_config or {}
        goal_type = str(cfg.get("goal_type", "open_ended"))
        sub_goals = list(cfg.get("sub_goals", []) or [])
        scope = list(cfg.get("scope", []) or [])
        deliverables = list(cfg.get("deliverables", []) or [])
        verify_command = str(cfg.get("verify_command", "")).strip()
        lines = ["# Goal Loop Brief", "", f"**Goal:** {loop.task}", "",
                 f"**Goal type:** {goal_type}", "", "**Sub-goals:**"]
        lines += [f"- {s}" for s in sub_goals] or ["- (none — derive your own from the goal)"]
        if loop.linked_task_ids:
            # Give the worker the CONCRETE list id to scope `task_list` by — without it
            # `task_list` returns the 25 most-recent tasks across the WHOLE system and
            # the worker can't reliably find (or update) its own sub-goal tasks.
            list_id = (loop.task_list_ids or {}).get("sub_goals", "")
            scope_hint = (f"`task_list {{task_list_id: \"{list_id}\"}}`" if list_id else "`task_list`")
            lines += ["",
                      "**Tracked tasks (keep these up to date).** The sub-goals above are "
                      f"tracked as Tasks in this loop's task list. Call {scope_hint} to see "
                      "them, then `task_update {id, status}` to mark each in_progress when you "
                      "start it and done when complete — do not leave finished work marked open."]
        lines += ["",
                  f"**Scope / sources allowed:** {', '.join(scope) or 'any'}",
                  f"**Max cycles:** {loop.max_cycles}"]
        if context_dir:
            lines += ["",
                      f"**Project context dir:** `{context_dir}`",
                      "Shared, durable context for the PROJECT this goal belongs to (other "
                      "loops on the same project share it). READ it at the start for prior "
                      "context; WRITE concise durable notes there (`context/notes.md`, "
                      "`context/decisions.md`) — the project's long-term memory, not this "
                      "run's scratch (that goes in your cycle finding)."]
        if loop.attended:
            lines += ["",
                      "**Clarification allowed (attended):** if the goal is genuinely "
                      "ambiguous in a way that would change your direction, you MAY ask ONE "
                      "high-leverage question — write {\"question\", \"why\"} to "
                      "questions.json and end the turn. Keep the bar high; otherwise proceed "
                      "on a best-reasoned assumption and record it."]
        else:
            lines += ["",
                      "**Unattended:** do NOT pause to ask the user. Investigate ambiguities "
                      "yourself, record the assumption in your cycle finding, and proceed. "
                      "Never write questions.json in this mode."]
        if goal_type == "verifiable" and verify_command:
            lines += ["",
                      f"**Verification check:** the supervisor runs `{verify_command}` each "
                      "cycle and reads its result. Drive your work toward making it pass; do "
                      "not self-certify — the supervisor's run is the source of truth."]
        if loop.success_criteria:
            lines += ["", f"**Definition of Done:** {loop.success_criteria}",
                      "Make real progress toward this each cycle and report evidence; a "
                      "separate check or judge — never you — decides when it is met."]
        # When a workspace is bound, the FILE deliverable belongs in the workspace (the
        # project's real working tree that downstream Design/Code loops read), NOT the
        # loop dir (findings/scratch). The cycle nudge names the loop dir as the working
        # dir for loop files, so without this an unqualified `SPEC.md` write lands in the
        # loop dir and the handoff to later loops silently breaks (observed: a goal loop
        # completed with SPEC.md in the loop dir while the bound workspace stayed empty).
        deliverable = self.deliverable_name(loop)
        ws = (loop.workspace_dir or "").strip()
        if ws:
            # Name the ACTUAL deliverable file in the example, not a hardcoded one —
            # else the worker sees "e.g. SPEC.md" while told below to maintain REPORT.md.
            eg = deliverable or "REPORT.md"
            lines += ["",
                      f"**Workspace (where file deliverables go):** `{ws}`",
                      f"Write the document deliverable (`{eg}`) at the ROOT of this "
                      "workspace — use its absolute path. The loop dir is only for your "
                      "brief, findings, and scratch; downstream loops read the WORKSPACE, so "
                      "a deliverable left in the loop dir will not be found."]
        if deliverables:
            listed = "\n".join(f"  {i+1}. {d}" for i, d in enumerate(deliverables))
            lines += ["",
                      f"**Deliverables — {len(deliverables)} SEPARATE outputs.** Produce EACH "
                      "as its own artifact (`artifact_save`, tagged "
                      f"`loop:{loop.id}`) — never combine them. Update each in place across "
                      "cycles:", listed]
        elif deliverable:
            loc = f"`{ws}/{deliverable}`" if ws else f"`{deliverable}`"
            lines += ["",
                      f"**Deliverable:** maintain {loc} as the ongoing output — full "
                      "structure on cycle 1, updated every cycle from new findings; save "
                      f"discrete presentable results via `artifact_save` tagged `loop:{loop.id}`."]
        return "\n".join(lines)

    def cycle_nudge(self, loop: Loop, loop_dir: str) -> str:
        """Ported from the legacy loops manager: the per-cycle trigger shaped by
        goal type. The finding write IS the cycle (a hard criterion — less-steerable
        ACP workers otherwise plan + write nothing); the deliverable write is
        conditional on the goal type having a document deliverable."""
        cfg = loop.kind_config or {}
        goal_type = str(cfg.get("goal_type", "open_ended"))
        deliverables = list(cfg.get("deliverables", []) or [])
        deliverable = self.deliverable_name(loop)
        lines = [
            f"Run the next autonomous cycle for goal loop {loop.id} "
            f"(working dir for loop files: {loop_dir}). Steps: (1) check status.json — "
            "if not 'running', stop; (2) read brief.md; (3) apply + delete guidance.txt "
            "if present; (4) do ONE adaptive step toward the goal.",
            "",
            f"Before you end this turn you MUST write findings/cycle_NNN.json to {loop_dir} "
            "(next sequential N) — this is the deliverable, not optional, and the turn is "
            "incomplete without it. The finding is the structured cycle record "
            "{cycle, summary, key_insight, sources_checked, new_findings_count, "
            "evidence, metric?}. Report what you DID and the EVIDENCE; do NOT write a "
            "self-verdict on whether the goal is met — a separate check decides that.",
        ]
        if deliverables:
            names = ", ".join(deliverables)
            lines.append(
                f"This goal has {len(deliverables)} SEPARATE deliverables ({names}). "
                "Save/update EACH as its own artifact via `artifact_save` (tagged "
                f"`loop:{loop.id}`) — never combine them into one document. Advance "
                "whichever deliverables this cycle's work touched."
            )
        elif deliverable:
            lines.append(
                f"Also maintain {deliverable} in {loop_dir} — create it with full section "
                "structure on cycle 1, and UPDATE it in place every later cycle, folding "
                "in this cycle's findings, so it reads as the finished deliverable by the end."
            )
        if goal_type == "verifiable":
            lines.append(
                "This is a verifiable goal: there is no document deliverable. Make real "
                "progress toward the success criteria; the supervisor runs the check itself."
            )
        if loop.execution == "multi_agent" and loop.roster:
            lines.append(
                "You are the ORCHESTRATOR (see brief.md): delegate this cycle's step to "
                "the right roster persona(s) as subagents, wait for their results, then "
                "write the finding from what they produced."
            )
        lines.append(
            "Actually write the files (use your file-write/editor tools); do not just "
            "describe them. Then end the turn."
        )
        # The cycle-nudge text lives in the prompt system (bundled snippet
        # ``loop-goal-cycle-nudge``, bindable in Settings → Prompts). The if/elif
        # precedence (multi-deliverable list wins over a single document) is preserved
        # as ``show_single_deliverable``. Falls back to the inline assembly above if the
        # snippet can't be resolved.
        from personalclaw.prompt_providers.runtime import render_snippet_block
        rendered = render_snippet_block(
            "loop-goal-cycle-nudge",
            {
                "loop_id": loop.id,
                "loop_dir": loop_dir,
                "has_deliverables": bool(deliverables),
                "deliverables_count": len(deliverables),
                "deliverables_names": ", ".join(deliverables),
                "show_single_deliverable": (not deliverables) and bool(deliverable),
                "deliverable": deliverable,
                "is_verifiable": goal_type == "verifiable",
                "is_multi_agent_with_roster": loop.execution == "multi_agent" and bool(loop.roster),
            },
        )
        return rendered or "\n".join(lines)


# Canonical deliverable doc name by goal type (ported from loops.store.deliverable_name):
# verifiable goals have no document (the check/code is the output).
_DELIVERABLES = {"open_ended": "REPORT.md", "monitor": "MONITOR_LOG.md", "verifiable": ""}


def _deliverable_name(goal_type: str) -> str:
    return _DELIVERABLES.get(goal_type, "REPORT.md")


class _GoalWalkthrough:
    """Goal-kind planning walkthrough — a FIXED step list. Wraps the legacy goal
    plan-walkthrough's pure briefs/parsers; projects approved artifacts into the
    UNIFIED loop spec (sub-goals → ``plan`` rows + ``kind_config.sub_goals``)."""

    step_mode = "fixed"

    def __init__(self) -> None:
        from personalclaw.agents.defaults import LOOP_PLANNER_AGENT_NAME
        self.planner_agent = LOOP_PLANNER_AGENT_NAME

    def default_steps(self) -> list[dict]:
        from personalclaw.loop import goal_plan_briefs as pw
        return pw.default_steps()

    def build_design_brief(self, task: str, workspace_dir: str, design_inputs=None) -> str:
        return ""  # fixed mode — no design pass

    def parse_steps_sentinel(self, raw: str):
        return None  # fixed mode — no design pass

    def build_step_brief(self, task, step, *, approved, workspace_dir):
        from personalclaw.loop import goal_plan_briefs as pw
        return pw.build_step_brief(task, step, approved=approved)

    def parse_artifact_sentinel(self, raw: str):
        from personalclaw.loop import goal_plan_briefs as pw
        return pw.parse_artifact_sentinel(raw)

    def project_to_spec(self, session) -> dict:
        """Project intent/sub_goals/quorum/execution_plan into the unified spec:
        success_criteria + summary top-level, sub_goals → ``plan`` rows (keyed by
        title) AND ``kind_config.sub_goals``/``execution_plan``, roster → execution."""
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
            elif step.kind == "sub_goals":
                sg = [str(x).strip() for x in (art.get("sub_goals") or []) if str(x).strip()]
                if sg:
                    spec["plan"] = [{"title": s} for s in sg]
                    kc_patch["sub_goals"] = sg
            elif step.kind == "quorum":
                roster = [r for r in (art.get("roster") or [])
                          if isinstance(r, dict) and str(r.get("role", "")).strip()]
                if roster:
                    spec["roster"] = roster
                    spec["execution"] = "multi_agent" if len(roster) > 1 else "solo"
            elif step.kind == "execution_plan":
                phases = [p for p in (art.get("execution_plan") or [])
                          if isinstance(p, dict) and (str(p.get("role", "")).strip()
                                                      or str(p.get("target", "")).strip())]
                if phases:
                    kc_patch["execution_plan"] = phases
        if kc_patch:
            # Merge into the loop's existing kind_config (preserve goal_type/granularity).
            from personalclaw.loop import store as _store
            loop = _store.get(session.project_id)
            base = dict(loop.kind_config) if loop and loop.kind_config else {}
            base.update(kc_patch)
            spec["kind_config"] = base
        return spec

    async def on_finalize(self, loop_id: str) -> None:
        """After the walkthrough projects the spec, turn the approved sub-goals into
        linked Tasks (the modern replacement for the dropped /decompose) so the worker
        tracks them via task_list/task_update + the completion reconcile closes them."""
        from personalclaw.loop import tasks_link
        await tasks_link.decompose_sub_goals(loop_id)


register(GoalKind())
