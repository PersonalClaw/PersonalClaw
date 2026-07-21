"""Unit tests for the unified loop plan walkthrough (Slice 2d(ii)) — the kind-
agnostic state machine + the goal/code Walkthrough delegates' spec projection."""

from __future__ import annotations

import pytest

from personalclaw.loop import kinds, store
from personalclaw.loop.loop import Loop
from personalclaw.planning.session import PlanSession, PlanStep, StepStatus


@pytest.fixture(autouse=True)
def _tmp_config(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: tmp_path)
    # on_finalize → decompose_sub_goals writes to the Tasks hierarchy store; isolate it.
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    import personalclaw.tasks.native as nat

    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    return tmp_path


def _approved(kind, artifact):
    return PlanStep(
        id=f"step-{kind}",
        kind=kind,
        title=kind,
        objective="",
        status=StepStatus.APPROVED.value,
        artifact=artifact,
    )


class TestGoalWalkthrough:
    def test_step_mode_and_fixed_steps(self):
        kinds.ensure_loaded()
        wt = kinds.get("goal").walkthrough()
        assert wt.step_mode == "fixed"
        steps = wt.default_steps()
        assert [s["kind"] for s in steps] == ["intent", "sub_goals", "quorum", "execution_plan"]

    def test_project_to_spec_into_unified_plan(self):
        kinds.ensure_loaded()
        loop = store.create(
            Loop(id="", name="", kind="goal", task="investigate the latency regression")
        )
        wt = kinds.get("goal").walkthrough()
        session = PlanSession(
            project_id=loop.id,
            steps=[
                _approved(
                    "intent",
                    {
                        "markdown": "Find the root cause.\nMore.",
                        "success_criteria": "RCA documented",
                    },
                ),
                _approved("sub_goals", {"sub_goals": ["profile the hot path", "check the cache"]}),
                _approved("quorum", {"roster": [{"role": "investigator"}, {"role": "fixer"}]}),
                _approved(
                    "execution_plan",
                    {"execution_plan": [{"role": "investigator", "target": "profile"}]},
                ),
            ],
        )
        spec = wt.project_to_spec(session)
        assert spec["success_criteria"] == "RCA documented"
        assert spec["summary"] == "Find the root cause."
        # sub-goals → unified plan rows (keyed by title) + kind_config.sub_goals
        assert spec["plan"] == [{"title": "profile the hot path"}, {"title": "check the cache"}]
        assert spec["kind_config"]["sub_goals"] == ["profile the hot path", "check the cache"]
        assert spec["kind_config"]["execution_plan"] == [
            {"role": "investigator", "target": "profile"}
        ]
        # two-member roster → multi_agent
        assert spec["execution"] == "multi_agent" and len(spec["roster"]) == 2

    def test_on_finalize_decomposes_sub_goals_into_linked_tasks(self):
        import asyncio

        kinds.ensure_loaded()
        loop = store.create(
            Loop(
                id="",
                name="G",
                kind="goal",
                task="investigate the latency regression",
                kind_config={
                    "goal_type": "open_ended",
                    "sub_goals": ["profile the hot path", "check the cache"],
                },
            )
        )
        wt = kinds.get("goal").walkthrough()
        asyncio.get_event_loop().run_until_complete(wt.on_finalize(loop.id))
        out = store.get(loop.id)
        # sub-goals became linked Tasks (the modern /decompose) + a backing Project
        assert len(out.linked_task_ids) == 2
        assert out.tasks_project_id != "" and "sub_goals" in out.task_list_ids
        # idempotent — a second finalize doesn't double-create
        asyncio.get_event_loop().run_until_complete(wt.on_finalize(loop.id))
        assert len(store.get(loop.id).linked_task_ids) == 2


class TestCodeWalkthrough:
    def test_step_mode_dynamic(self):
        kinds.ensure_loaded()
        wt = kinds.get("code").walkthrough()
        assert wt.step_mode == "dynamic" and wt.default_steps() == []

    def test_project_decomposition_into_plan(self):
        kinds.ensure_loaded()
        wt = kinds.get("code").walkthrough()
        session = PlanSession(
            project_id="x",
            steps=[
                _approved("problem_framing", {"markdown": "Add OAuth.\nDetail."}),
                _approved(
                    "decomposition",
                    {
                        "phases": [
                            {
                                "stage": "implementation",
                                "title": "build",
                                "objective": "wire oauth",
                                "exit_criteria": ["tests pass"],
                                "tasks": [{"title": "add provider"}],
                            },
                        ]
                    },
                ),
            ],
        )
        spec = wt.project_to_spec(session)
        assert spec["summary"] == "Add OAuth."
        assert spec["plan"] and spec["plan"][0]["stage"] == "implementation"

    def test_no_decomposition_falls_back_to_ladder(self):
        kinds.ensure_loaded()
        wt = kinds.get("code").walkthrough()
        session = PlanSession(
            project_id="x",
            steps=[
                _approved("problem_framing", {"markdown": "Idea only."}),
            ],
        )
        spec = wt.project_to_spec(session)
        assert spec["plan"]  # generic implement→verify ladder, never empty


class TestDesignWalkthrough:
    """Design is now a REAL planned loop (not skip-planning): a DYNAMIC walkthrough
    authors the phased breakdown, projecting the approved build_plan into the loop plan."""

    def test_step_mode_dynamic(self):
        kinds.ensure_loaded()
        wt = kinds.get("design").walkthrough()
        assert wt.step_mode == "dynamic" and wt.default_steps() == []

    def test_project_build_plan_into_plan(self):
        kinds.ensure_loaded()
        loop = store.create(
            Loop(id="", name="", kind="design", task="build a warm design system for a recipe app")
        )
        wt = kinds.get("design").walkthrough()
        session = PlanSession(
            project_id=loop.id,
            steps=[
                _approved("brief", {"markdown": "Warm, accessible recipe-app system.\nMore."}),
                _approved(
                    "build_plan",
                    {
                        "phases": [
                            {
                                "step": "foundations",
                                "title": "Foundations",
                                "objective": "warm anchors",
                            },
                            {
                                "step": "export",
                                "title": "Document & export",
                                "objective": "DESIGN.md",
                            },
                        ]
                    },
                ),
            ],
        )
        spec = wt.project_to_spec(session)
        assert spec["summary"] == "Warm, accessible recipe-app system."
        # build_plan phases → unified design plan rows (keyed by step→title)
        assert [p["step"] for p in spec["plan"]] == ["foundations", "export"]
        # phase titles mirrored into kind_config.design_steps (cockpit/brief render them)
        assert spec["kind_config"]["design_steps"] == ["Foundations", "Document & export"]

    def test_finalize_merges_approved_token_overrides_into_kind_config(self):
        # D4 approve→populate: every approved token-step's token_overrides deep-merge
        # into kind_config.token_overrides on finalize, so the cockpit opens populated
        # with the approved system (authoritative server-side, not reliant on the FE).
        kinds.ensure_loaded()
        loop = store.create(Loop(id="", name="", kind="design", task="a warm system"))
        wt = kinds.get("design").walkthrough()
        session = PlanSession(
            project_id=loop.id,
            steps=[
                _approved(
                    "palette",
                    {"token_overrides": {"color": {"primitive": {"brand": {"500": "#d65f2e"}}}}},
                ),
                _approved(
                    "typography",
                    {"token_overrides": {"typography": {"family": {"sans": "Inter, sans-serif"}}}},
                ),
                _approved("build_plan", {"phases": [{"step": "export", "title": "Export"}]}),
            ],
        )
        spec = wt.project_to_spec(session)
        ov = spec["kind_config"]["token_overrides"]
        assert ov["color"]["primitive"]["brand"]["500"] == "#d65f2e"  # palette merged
        assert ov["typography"]["family"]["sans"] == "Inter, sans-serif"  # typography merged

    def test_no_build_plan_falls_back_to_default_phases(self):
        kinds.ensure_loaded()
        loop = store.create(Loop(id="", name="", kind="design", task="a design system"))
        wt = kinds.get("design").walkthrough()
        session = PlanSession(
            project_id=loop.id,
            steps=[
                _approved("brief", {"markdown": "Just a brief."}),
            ],
        )
        spec = wt.project_to_spec(session)
        assert spec["plan"]  # canonical default phases, never empty
        assert spec["kind_config"]["design_steps"]


class TestStepPassRetry:
    """A step pass where the planner NARRATES the artifact (no write_file → no sentinel)
    must auto-retry ONCE before reverting to pending — else the walkthrough silently
    dead-ends at a pending step with no recovery (observed live on a design step-0)."""

    def _seed(self, kind="design"):
        kinds.ensure_loaded()
        loop = store.create(Loop(id="", name="", kind=kind, task="design a tic-tac-toe system"))
        session = PlanSession(
            project_id=loop.id,
            steps=[
                PlanStep(
                    id="step-0",
                    kind="brief",
                    title="Brief",
                    objective="o",
                    status=StepStatus.PENDING.value,
                    artifact=None,
                ),
            ],
        )
        store.write_plan_session(session)
        return loop

    def test_retries_once_then_succeeds(self, monkeypatch):
        import asyncio

        from personalclaw.loop import plan_walkthrough as pw

        loop = self._seed()
        calls = {"n": 0}

        async def _fake_run_pass(state, svc, lp, wt, *, brief, sentinel, timeout_secs=None):
            calls["n"] += 1
            # 1st call: planner pasted a code block (no sentinel written) → parse None.
            # 2nd call (the correction): it actually wrote the artifact.
            if calls["n"] == 1:
                return '```json\n{"markdown":"narrated, not written"}\n```'
            return "wrote step_artifact.json"

        def _fake_parse(raw):
            return {"markdown": "real artifact"} if "wrote" in (raw or "") else None

        monkeypatch.setattr(pw, "_run_pass", _fake_run_pass)
        monkeypatch.setattr(
            type(kinds.get("design").walkthrough()),
            "parse_artifact_sentinel",
            lambda self, raw: _fake_parse(raw),
        )
        step = asyncio.get_event_loop().run_until_complete(
            pw.run_step_pass(object(), object(), loop.id, "step-0")
        )
        assert calls["n"] == 2, "must retry exactly once after a no-sentinel pass"
        assert step is not None and step.status == StepStatus.AWAITING_REVIEW.value
        assert step.artifact == {"markdown": "real artifact"}

    def test_reverts_to_pending_when_retry_also_fails(self, monkeypatch):
        import asyncio

        from personalclaw.loop import plan_walkthrough as pw

        loop = self._seed()
        calls = {"n": 0}

        async def _fake_run_pass(state, svc, lp, wt, *, brief, sentinel, timeout_secs=None):
            calls["n"] += 1
            return "still just chatting, no file"

        monkeypatch.setattr(pw, "_run_pass", _fake_run_pass)
        monkeypatch.setattr(
            type(kinds.get("design").walkthrough()),
            "parse_artifact_sentinel",
            lambda self, raw: None,
        )
        step = asyncio.get_event_loop().run_until_complete(
            pw.run_step_pass(object(), object(), loop.id, "step-0")
        )
        assert calls["n"] == 2, "tries the original + exactly one retry, then gives up"
        assert step is None
        session = store.read_plan_session(loop.id)
        assert session.steps[0].status == StepStatus.PENDING.value  # honest revert
