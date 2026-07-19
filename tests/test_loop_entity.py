"""Unified Loop entity + kind-strategy registry (Slice 1 of the loops/projects
unification). Additive foundation — nothing wires it yet; these pin the shared
spine + the per-kind seam before the engine ports onto them."""

from __future__ import annotations

import pytest

from personalclaw.loop import (
    ACTION_SOURCE_STATES,
    ACTIVE_STATUSES,
    KINDS,
    PRELAUNCH_STATUSES,
    TERMINAL_STATUSES,
    Loop,
    LoopKind,
    LoopStatus,
)
from personalclaw.loop import kinds


class TestLoopEntity:
    def test_minimal_construction_defaults(self):
        loop = Loop(id="a1", name="N", kind="goal", task="achieve the thing")
        assert loop.status == LoopStatus.READY.value
        assert loop.kind == "goal"
        assert loop.autopilot is True
        assert loop.plan == [] and loop.phase_status == {} and loop.kind_config == {}

    def test_to_dict_from_dict_round_trip(self):
        loop = Loop(id="a1", name="N", kind="code", task="add oauth", project_id="p-1",
                    kind_config={"entry_stage": "design"})
        again = Loop.from_dict(loop.to_dict())
        assert again == loop

    def test_from_dict_ignores_unknown_fields(self):
        # Forward-compatible reads: a row written by a newer schema must not crash an
        # older reader (mirrors the legacy loaders' allowlist-on-load discipline).
        loop = Loop.from_dict(
            {"id": "a1", "name": "N", "kind": "goal", "task": "t", "future_col": 99}
        )
        assert loop.id == "a1" and not hasattr(loop, "future_col")

    def test_status_set_partitions(self):
        # The three sets are mutually disjoint; together they cover every state
        # EXCEPT FAILED (the documented resumable-attention exception — not active,
        # not prelaunch, not terminal).
        assert LoopStatus.COMPLETE in TERMINAL_STATUSES
        assert TERMINAL_STATUSES.isdisjoint(ACTIVE_STATUSES)
        assert PRELAUNCH_STATUSES.isdisjoint(ACTIVE_STATUSES)
        assert PRELAUNCH_STATUSES.isdisjoint(TERMINAL_STATUSES)
        covered = PRELAUNCH_STATUSES | ACTIVE_STATUSES | TERMINAL_STATUSES
        assert set(LoopStatus) - covered == {LoopStatus.FAILED}

    def test_action_source_states_are_sane(self):
        assert LoopStatus.RUNNING in ACTION_SOURCE_STATES["pause"]
        assert LoopStatus.READY in ACTION_SOURCE_STATES["start"]
        # resume comes from the attention/paused states, never from running/terminal
        assert ACTION_SOURCE_STATES["resume"].isdisjoint(
            {LoopStatus.RUNNING, *TERMINAL_STATUSES}
        )
        # stop is valid from every active state
        assert ACTION_SOURCE_STATES["stop"] == ACTIVE_STATUSES


class TestKindRegistry:
    def test_all_kinds_register(self):
        kinds.ensure_loaded()
        assert set(kinds.registered_kinds()) == {"general", "goal", "code", "design", "research"}
        assert set(kinds.registered_kinds()) == set(KINDS)

    def test_kind_enum_matches_registry(self):
        kinds.ensure_loaded()
        assert {k.value for k in LoopKind} == set(kinds.registered_kinds())

    @pytest.mark.parametrize("kind", ["general", "goal", "code", "design", "research"])
    def test_strategy_contract(self, kind):
        import asyncio
        kinds.ensure_loaded()
        s = kinds.get(kind)
        assert s.kind == kind
        assert isinstance(s.label, str) and s.label
        assert isinstance(s.description, str) and s.description
        assert isinstance(s.wants_workspace, bool)
        assert isinstance(s.default_agent, str) and s.default_agent
        assert isinstance(s.default_kind_config(), dict)
        # phase_key is pure + string-returning for a representative phase
        assert isinstance(s.phase_key({"stage": "implementation", "title": "Impl"}), str)
        # is_done_signal is async + returns the tristate (bool | None); with no
        # config (no verify command) every kind defers (None), never raises.
        loop = Loop(id="x", name="n", kind=kind, task="t")
        assert asyncio.get_event_loop().run_until_complete(s.is_done_signal(loop, [])) in (True, False, None)

    def test_code_phase_key_prefers_stage_then_title(self):
        kinds.ensure_loaded()
        s = kinds.get("code")
        assert s.phase_key({"stage": "verification", "title": "V"}) == "verification"
        assert s.phase_key({"stage": "", "title": "Spike"}) == "Spike"

    def test_goal_phase_key_is_title(self):
        kinds.ensure_loaded()
        s = kinds.get("goal")
        assert s.phase_key({"title": "Investigate latency"}) == "Investigate latency"

    def test_get_unknown_kind_raises(self):
        with pytest.raises(KeyError):
            kinds.get("nope")
        assert kinds.get_or_none("nope") is None

    @pytest.mark.parametrize("kind", ["general", "goal", "code", "design", "research"])
    def test_cycle_nudge_has_the_hard_finding_contract(self, kind):
        # Every kind's per-cycle trigger restates the non-negotiable contract: read
        # status/brief/guidance, do one step, and MUST write a cycle finding. This is
        # what keeps less-steerable ACP workers from planning + writing nothing.
        kinds.ensure_loaded()
        loop = Loop(id="abcd1234", name="n", kind=kind, task="do the thing")
        nudge = kinds.get(kind).cycle_nudge(loop, "/loopdir")
        assert "findings/cycle_NNN.json" in nudge
        assert "status.json" in nudge and "end the turn" in nudge.lower()

    def test_general_cycle_nudge_asks_for_files_touched(self):
        # A General loop's primary output is often files it writes to the workspace
        # (e.g. a STATUS.md), so its finding contract must capture files_touched —
        # that's what lets the cockpit surface those workspace deliverables (parity
        # with the sdlc/manager finding contracts).
        kinds.ensure_loaded()
        s = kinds.get("general")
        nudge = s.cycle_nudge(Loop(id="abcd1234", name="n", kind="general", task="t"), "/d")
        assert "files_touched" in nudge

    def test_goal_cycle_nudge_is_type_shaped(self):
        kinds.ensure_loaded()
        s = kinds.get("goal")
        verif = s.cycle_nudge(Loop(id="abcd1234", name="n", kind="goal", task="t",
                                   kind_config={"goal_type": "verifiable"}), "/d")
        assert "verifiable goal" in verif  # no document deliverable
        multi = s.cycle_nudge(Loop(id="abcd1234", name="n", kind="goal", task="t",
                                   execution="multi_agent", roster=[{"role": "r"}],
                                   kind_config={"goal_type": "open_ended"}), "/d")
        assert "ORCHESTRATOR" in multi

    def test_code_cycle_nudge_carries_stage_directive_and_qualified_paths(self):
        kinds.ensure_loaded()
        s = kinds.get("code")
        loop = Loop(id="abcd1234", name="n", kind="code", task="t",
                    plan=[{"stage": "implementation", "title": "Impl",
                           "objective": "build it", "exit_criteria": ["builds"]}])
        nudge = s.cycle_nudge(loop, "/proj")
        assert "[Stage plan —" in nudge and "stage 1/1" in nudge
        assert "/proj/status.json" in nudge          # path-qualified for brownfield cwd
        assert s.active_stage_index(loop) == 0

    def test_code_active_stage_index_skips_done(self):
        kinds.ensure_loaded()
        s = kinds.get("code")
        loop = Loop(id="abcd1234", name="n", kind="code", task="t",
                    plan=[{"stage": "design", "title": "D"}, {"stage": "implementation", "title": "I"}],
                    phase_status={"design": "done"})
        assert s.active_stage_index(loop) == 1  # first not-done

    @pytest.mark.parametrize("kind", ["general", "goal", "code", "design", "research"])
    def test_build_brief_is_pure_and_nonempty(self, kind):
        # build_brief is PURE (no store/projects coupling) — the manager passes the
        # resolved context_dir. Every kind returns a non-empty brief that surfaces the
        # project context dir when one is given.
        kinds.ensure_loaded()
        loop = Loop(id="abcd1234", name="n", kind=kind, task="build the thing carefully")
        brief = kinds.get(kind).build_brief(loop, context_dir="/proj/ctx")
        assert isinstance(brief, str) and brief.strip()
        assert "/proj/ctx" in brief

    def test_code_brief_carries_stage_plan_and_checks(self):
        kinds.ensure_loaded()
        loop = Loop(id="abcd1234", name="n", kind="code", task="add oauth", workspace_dir="/ws",
                    plan=[{"stage": "implementation", "title": "Impl", "objective": "build it",
                           "exit_criteria": ["builds"]}],
                    kind_config={"verify_command": "make lint", "test_command": "pytest"})
        brief = kinds.get("code").build_brief(loop)
        assert "Stage plan" in brief and "make lint" in brief and "pytest" in brief

    def test_goal_brief_type_shaped(self):
        kinds.ensure_loaded()
        s = kinds.get("goal")
        # verifiable: a verify check, no document deliverable
        verif = s.build_brief(Loop(id="abcd1234", name="n", kind="goal", task="t",
                                   kind_config={"goal_type": "verifiable", "verify_command": "make ci"}))
        assert "Verification check" in verif and "Deliverable:" not in verif
        # open_ended: maintains REPORT.md
        oe = s.build_brief(Loop(id="abcd1234", name="n", kind="goal", task="t",
                                kind_config={"goal_type": "open_ended"}))
        assert "REPORT.md" in oe

    def test_goal_brief_directs_file_deliverable_into_bound_workspace(self):
        # A goal loop with a bound workspace must tell the worker to write its file
        # deliverable INTO the workspace (downstream loops read it there), naming the
        # absolute path — else an unqualified write lands in the loop dir and the
        # handoff silently breaks (live repro: SPEC.md in loop dir, workspace empty).
        kinds.ensure_loaded()
        s = kinds.get("goal")
        ws = "/Users/dev/projects/ProductionGradeTicTacToe"
        brief = s.build_brief(Loop(id="abcd1234", name="n", kind="goal", task="spec it",
                                   workspace_dir=ws, kind_config={"goal_type": "open_ended"}))
        assert ws in brief                       # absolute workspace path stated
        assert f"{ws}/REPORT.md" in brief        # deliverable qualified into the workspace
        # No workspace bound → no workspace directive, deliverable stays bare
        none = s.build_brief(Loop(id="abcd1234", name="n", kind="goal", task="t",
                                  kind_config={"goal_type": "open_ended"}))
        assert "Workspace (where file deliverables go)" not in none

    def test_goal_deliverable_name_matches_brief_and_nudge(self):
        # The brief, the cycle nudge, and deliverable_name (which drives the completion
        # artifact registration) must all name the SAME file per goal type — else a
        # monitor's MONITOR_LOG.md is instructed but never surfaced (or vice-versa).
        kinds.ensure_loaded()
        s = kinds.get("goal")
        for gt, fname in [("open_ended", "REPORT.md"), ("monitor", "MONITOR_LOG.md")]:
            loop = Loop(id="abcd1234", name="n", kind="goal", task="watch the queue",
                        kind_config={"goal_type": gt})
            assert s.deliverable_name(loop) == fname
            assert fname in s.build_brief(loop)
            assert fname in s.cycle_nudge(loop, "/d")
        # verifiable has no document deliverable in any of the three
        vloop = Loop(id="abcd1234", name="n", kind="goal", task="t",
                     kind_config={"goal_type": "verifiable", "verify_command": "make ci"})
        assert s.deliverable_name(vloop) == ""

    def test_goal_primary_deliverable_overrides_default_filename(self):
        # When the goal NAMES its output file (e.g. SPEC.md), that filename must win
        # over the open_ended default (REPORT.md) across deliverable_name + brief +
        # nudge — else the worker is told to maintain REPORT.md while the goal/DoD
        # demand SPEC.md (an observed self-contradiction in the brief). The workspace
        # example must name the SAME file, not a hardcoded one.
        kinds.ensure_loaded()
        s = kinds.get("goal")
        ws = "/Users/dev/projects/ProductionGradeTicTacToe"
        loop = Loop(id="abcd1234", name="n", kind="goal", task="produce the spec",
                    workspace_dir=ws,
                    kind_config={"goal_type": "open_ended", "primary_deliverable": "SPEC.md"})
        assert s.deliverable_name(loop) == "SPEC.md"
        brief = s.build_brief(loop)
        assert f"{ws}/SPEC.md" in brief          # deliverable + workspace example both SPEC.md
        assert "REPORT.md" not in brief          # the default never leaks in
        assert "SPEC.md" in s.cycle_nudge(loop, "/d")
        # An explicit name never resurrects a deliverable for a verifiable goal.
        vloop = Loop(id="abcd1234", name="n", kind="goal", task="t",
                     kind_config={"goal_type": "verifiable", "verify_command": "make ci",
                                  "primary_deliverable": "SPEC.md"})
        assert s.deliverable_name(vloop) == ""
        # Multi-output goals ignore primary_deliverable (they use `deliverables`).
        none = kinds.get("goal").classify  # sanity: attribute exists
        assert none is not None

    def test_turn_capabilities_and_directive_per_kind(self):
        # chat_runner loads each cycle's capabilities + prepends a directive via the
        # kind's turn_capabilities/turn_directive (unified, no per-engine branch).
        kinds.ensure_loaded()
        # goal: execution_plan phase caps ∪ baseline; directive names the phase
        g = kinds.get("goal")
        gl = Loop(id="abcd1234", name="n", kind="goal", task="t", skill_ids=["base"], total_cycles=0,
                  kind_config={"execution_plan": [{"role": "investigator", "target": "profile",
                                                   "min_cycles": 2, "skill_ids": ["phase-sk"]}]})
        gsk, _ = g.turn_capabilities(gl)
        assert "base" in gsk and "phase-sk" in gsk
        assert "phase 1/1" in g.turn_directive(gl)
        # code: active stage caps ∪ baseline; directive names the stage
        c = kinds.get("code")
        cl = Loop(id="abcd1234", name="n", kind="code", task="t", skill_ids=["base"],
                  plan=[{"stage": "implementation", "title": "I", "objective": "build", "skill_ids": ["impl-sk"]}],
                  phase_status={"implementation": "active"})
        csk, _ = c.turn_capabilities(cl)
        assert "base" in csk and "impl-sk" in csk
        assert "stage 1/1" in c.turn_directive(cl)
        # no plan → bare baseline, empty directive
        plain = Loop(id="abcd1234", name="n", kind="goal", task="t", skill_ids=["only"])
        assert g.turn_capabilities(plain) == (["only"], []) and g.turn_directive(plain) == ""

    def test_tracked_tasks_block_injects_concrete_list_id(self):
        kinds.ensure_loaded()
        s = kinds.get("goal")
        # linked + a known list id → the brief scopes task_list by that id (so the
        # worker can find its tasks, not the 25 most-recent system-wide).
        linked = s.build_brief(Loop(id="abcd1234", name="n", kind="goal", task="t",
                                    linked_task_ids=["t-1", "t-2"],
                                    task_list_ids={"sub_goals": "tl-xyz"}))
        assert "Tracked tasks" in linked and 'task_list_id: "tl-xyz"' in linked
        # not linked → no tracked-tasks block at all
        plain = s.build_brief(Loop(id="abcd1234", name="n", kind="goal", task="t"))
        assert "Tracked tasks" not in plain

    def test_attended_vs_unattended_brief(self):
        kinds.ensure_loaded()
        s = kinds.get("goal")
        att = s.build_brief(Loop(id="abcd1234", name="n", kind="goal", task="t", attended=True))
        un = s.build_brief(Loop(id="abcd1234", name="n", kind="goal", task="t", attended=False))
        assert "Clarification allowed" in att
        assert "Unattended" in un and "Never write questions.json" in un

    def test_classify_preserves_planner_reason_fields(self, monkeypatch):
        # The planner's entry/rigor/strategy rationale must survive classify
        # normalization — the Plan Review surfaces it (RigorChip tooltip etc.).
        import asyncio
        kinds.ensure_loaded()

        async def _run(coro):
            return await coro

        # code: stub the legacy classifier to a known CodeClassification
        from personalclaw.loop import code_classify

        async def _fake_code(task, ask, **kw):
            return code_classify.CodeClassification(
                title="t", entry_stage="bugfix", entry_reason="clear defect",
                intake_rigor="minimal", rigor_reason="crisp + narrow")
        monkeypatch.setattr(code_classify, "classify", _fake_code)
        cd = asyncio.get_event_loop().run_until_complete(
            kinds.get("code").classify("fix the crash", None))
        assert cd["entry_reason"] == "clear defect" and cd["rigor_reason"] == "crisp + narrow"

        # goal: stub its classifier
        from personalclaw.loop import classify as goal_classify

        async def _fake_goal(task, ask, **kw):
            return goal_classify.Classification(
                title="t", goal_type="open_ended", intake_rigor="thorough",
                rigor_reason="ambiguous", strategy_reason="needs a panel")
        monkeypatch.setattr(goal_classify, "classify", _fake_goal)
        gd = asyncio.get_event_loop().run_until_complete(
            kinds.get("goal").classify("research caching", None))
        assert gd["rigor_reason"] == "ambiguous" and gd["strategy_reason"] == "needs a panel"

    def test_register_is_idempotent(self):
        # Re-loading the bundled strategies must not duplicate or error (dev reload).
        kinds.ensure_loaded()
        before = set(kinds.registered_kinds())
        kinds.ensure_loaded()
        assert set(kinds.registered_kinds()) == before
