"""Tests for the chat-callable SDLC tools — create + status for Code projects and
Goal Loops (the chat agent's plan→create→start→watch surface). Start is exercised
lightly (it needs the live dashboard state + autonudge service, which aren't up in a
unit test) — we assert it degrades cleanly to a clear error rather than crashing.

Code projects and Goal Loops are both kinds of the ONE unified Loop (kind ``code`` /
``goal``); the tools build a create body and reuse the unified store/manager/validation.
The unified loop store is redirected to a tmp config dir per test.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.agents.native import sdlc_tools


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _tmp_config(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: tmp_path)
    return tmp_path


# ── Code project create ──

def test_code_create_makes_a_ready_draft():
    r = _run(sdlc_tools.code_project_create({
        "task": "Add a /healthz endpoint to the Flask service",
        "project_kind": "greenfield", "entry_stage": "implementation",
        "stage_plan": [{"stage": "implementation", "title": "Impl", "objective": "add it"}],
    }))
    assert r.success and "draft" in r.output.lower()
    # the created project is persisted as READY (not running)
    from personalclaw.loop import store
    pid = [p.id for p in store.list_all() if p.kind == "code"][0]
    assert store.get(pid).status == "ready"
    assert pid in r.output  # the id is surfaced for a follow-up start


def test_code_create_dedupes_duplicate_stages():
    # Two stages sharing an effective key (stage || title) would collide at launch (one
    # TaskList + status entry per key) and corrupt stage advancement. A chat-tool create
    # bypasses the FE's Plan-Review dup-guard, so the tool runs the agent's plan through
    # _normalize_plan — which DEDUPES the collision at the source (keeps the first),
    # rather than persisting both rows + merely warning. The created plan has ONE
    # implementation stage.
    r = _run(sdlc_tools.code_project_create({
        "task": "Build a small CLI but with two implementation stages by mistake",
        "stage_plan": [
            {"stage": "implementation", "title": "First", "objective": "a"},
            {"stage": "implementation", "title": "Second", "objective": "b"},
        ],
    }))
    assert r.success
    from personalclaw.loop import store
    pid = [p.id for p in store.list_all() if p.kind == "code"][0]
    plan = store.get(pid).plan
    assert len(plan) == 1 and plan[0]["stage"] == "implementation"
    # the output reports the deduped count (1 stage), not the 2 the agent sent
    assert "1 stage" in r.output.lower()


def test_code_create_drops_unkeyable_stage_row():
    # A row with neither a valid stage id NOR an objective has no effective downstream
    # key — _normalize_plan drops it so it can't key into stage_status/task_list_ids as
    # '' (colliding with any other unkeyable row). The good row survives.
    r = _run(sdlc_tools.code_project_create({
        "task": "Add structured logging to the worker service",
        "stage_plan": [
            {"stage": "implementation", "title": "Do it", "objective": "wire it up"},
            {"stage": "", "title": "", "objective": ""},
        ],
    }))
    assert r.success
    from personalclaw.loop import store
    pid = [p.id for p in store.list_all() if p.kind == "code"][0]
    plan = store.get(pid).plan
    assert len(plan) == 1 and plan[0]["stage"] == "implementation"


def test_code_create_rejects_vague_task():
    r = _run(sdlc_tools.code_project_create({"task": "fix it"}))
    assert r.success is False and "vague" in r.error.lower()


def test_code_create_brownfield_no_workspace_is_draft_with_note():
    # Brownfield w/o a workspace is allowed as a DRAFT (pickable later), but the tool
    # flags that a workspace is needed before starting.
    r = _run(sdlc_tools.code_project_create({
        "task": "Refactor the auth module in my service", "project_kind": "brownfield",
        "stage_plan": [{"stage": "implementation", "title": "x", "objective": "y"}],
    }))
    assert r.success and "workspace" in r.output.lower()


# ── Goal Loop create ──

def test_goal_create_makes_a_ready_draft():
    r = _run(sdlc_tools.goal_loop_create({
        "goal": "Research and summarize the top 5 vector databases",
        "sub_goals": ["survey options", "benchmark", "write summary"],
    }))
    assert r.success and "draft" in r.output.lower()
    from personalclaw.loop import store
    lid = [g.id for g in store.list_all() if g.kind == "goal"][0]
    assert store.get(lid).status == "ready"
    assert lid in r.output


def test_goal_create_rejects_vague_goal():
    r = _run(sdlc_tools.goal_loop_create({"goal": "do stuff"}))
    assert r.success is False and "vague" in r.error.lower()


def test_goal_create_validates_before_persist():
    # The chat tool must run the same validation as the HTTP create — a bad goal_type
    # or over-cap cycles is rejected, not silently persisted.
    from personalclaw.loop import store
    bad_type = _run(sdlc_tools.goal_loop_create({
        "goal": "Research the best caching strategy for our API", "goal_type": "nonsense"}))
    assert bad_type.success is False and "goal type" in bad_type.error.lower()
    over_cap = _run(sdlc_tools.goal_loop_create({
        "goal": "Research the best caching strategy for our API", "max_cycles": 99999}))
    assert over_cap.success is False and "cap" in over_cap.error.lower()
    assert store.list_all() == []  # neither bad loop was persisted


def test_goal_create_relays_cost_warning():
    # A high (but under-cap) cycle count triggers the validator's non-blocking cost
    # estimate — the tool must RELAY it (the user's spend), not silently drop it. Still
    # creates the draft.
    r = _run(sdlc_tools.goal_loop_create({
        "goal": "Continuously monitor the competitor pricing pages", "max_cycles": 80}))
    assert r.success
    assert "cost" in r.output.lower() or "$" in r.output


# ── general + design kinds via the shared create tool (the unified loop coverage) ──

def test_loop_create_general_kind():
    # The chat tool covers general loops too (kind='general'), not just goal — else the
    # chat agent could only spawn 2 of the 4 loop kinds.
    from personalclaw.loop import store
    r = _run(sdlc_tools.goal_loop_create({
        "kind": "general", "goal": "Tidy up the scratch notes directory each morning"}))
    assert r.success and "draft" in r.output.lower()
    lid = [g.id for g in store.list_all() if g.kind == "general"][0]
    assert store.get(lid).kind == "general" and store.get(lid).status == "ready"
    assert f"/#/loops/{lid}" in r.output  # non-code → /#/loops deep link → widget renders


def test_loop_create_design_kind_seeds_phase_plan():
    # A chat-created Design loop skips the LLM classify pass, so the tool must seed the
    # design kind's deterministic canonical phases — else it free-runs with no phased
    # breakdown (violating the vision). The relay leads with the phase count.
    from personalclaw.loop import store
    r = _run(sdlc_tools.goal_loop_create({
        "kind": "design", "goal": "Build a calm, accessible design system for a finance app"}))
    assert r.success and "phase" in r.output.lower()
    lid = [g.id for g in store.list_all() if g.kind == "design"][0]
    loop = store.get(lid)
    assert loop.kind == "design"
    assert len(loop.plan or []) >= 3  # the canonical foundations→…→export phases
    assert loop.kind_config.get("design_steps")  # mirrored for the cockpit/brief


def test_loop_create_rejects_unknown_kind():
    # code is NOT accepted here (it has its own specialized tool with distinct args).
    from personalclaw.loop import store
    r = _run(sdlc_tools.goal_loop_create({"kind": "code", "goal": "Implement the billing module"}))
    assert r.success is False and "kind must be" in r.error.lower()
    assert store.list_all() == []


def test_sdlc_status_reads_general_loop_phases():
    # general/design are phase-planned; status must report phase progress, not the empty
    # sub_goals that previously mislabeled them "open-ended".
    from personalclaw.loop import store
    _run(sdlc_tools.goal_loop_create({"kind": "design", "goal": "A bold editorial design system for a magazine"}))
    lid = [g.id for g in store.list_all() if g.kind == "design"][0]
    r = _run(sdlc_tools.sdlc_status({"id": lid}))
    assert r.success and "open-ended" not in r.output  # phased, not sub-goal-driven
    assert f"/#/loops/{lid}" in r.output


# ── status ──

def test_sdlc_status_reads_code_project():
    cr = _run(sdlc_tools.code_project_create({
        "task": "Add a /healthz endpoint to the Flask service",
        "stage_plan": [{"stage": "implementation", "title": "Impl", "objective": "add it"}],
    }))
    from personalclaw.loop import store
    pid = [p.id for p in store.list_all() if p.kind == "code"][0]
    r = _run(sdlc_tools.sdlc_status({"id": pid}))
    assert r.success and pid in r.output and "status:" in r.output
    # carries the cockpit deep-link so the in-chat widget renders the live card
    assert f"/#/code/{pid}" in r.output
    assert "ready" in r.output

def test_sdlc_status_reads_goal_loop():
    _run(sdlc_tools.goal_loop_create({"goal": "Research the top 5 vector databases out there"}))
    from personalclaw.loop import store
    lid = [g.id for g in store.list_all() if g.kind == "goal"][0]
    r = _run(sdlc_tools.sdlc_status({"id": lid}))
    assert r.success and lid in r.output
    assert f"/#/loops/{lid}" in r.output  # deep-link → widget renders


def test_sdlc_status_unknown_id():
    r = _run(sdlc_tools.sdlc_status({"id": "deadbeef"}))
    assert r.success is False and "no loop with id" in r.error.lower()


def test_sdlc_status_surfaces_pending_question_when_needs_input():
    """needs_input is exactly when the user asks the chat agent 'what's it stuck on?'
    — the status must relay the actual question, not just the bare status word."""
    import json
    from personalclaw.loop import store
    from personalclaw.loop.loop import LoopStatus
    _run(sdlc_tools.code_project_create({
        "task": "Add a /healthz endpoint to the Flask service",
        "stage_plan": [{"stage": "implementation", "title": "Impl", "objective": "add it"}],
    }))
    pid = [p.id for p in store.list_all() if p.kind == "code"][0]
    # park it on the user with a real pending question (questions.json + status)
    d = store.loop_dir(pid)
    (d / "questions.json").write_text(json.dumps(
        {"question": "Which port should /healthz bind to?", "why": "the brief didn't say"}))
    store.update_status(pid, LoopStatus.NEEDS_INPUT)
    r = _run(sdlc_tools.sdlc_status({"id": pid}))
    assert r.success
    assert "Which port should /healthz bind to?" in r.output
    assert "the brief didn't say" in r.output


def test_sdlc_status_surfaces_block_reason():
    """blocked/failed carry a persisted reason (the stall/gate explanation) — relay it
    so the agent can tell the user WHY, not just that it stopped."""
    from personalclaw.loop import store
    from personalclaw.loop.loop import LoopStatus
    _run(sdlc_tools.code_project_create({
        "task": "Add a /healthz endpoint to the Flask service",
        "stage_plan": [{"stage": "implementation", "title": "Impl", "objective": "add it"}],
    }))
    pid = [p.id for p in store.list_all() if p.kind == "code"][0]
    store.update_status(pid, LoopStatus.BLOCKED,
                        error_message="Build gate failed: pytest exited 1 on test_health")
    r = _run(sdlc_tools.sdlc_status({"id": pid}))
    assert r.success
    assert "Build gate failed" in r.output


def test_sdlc_status_flags_ended_early_complete():
    """A 'complete' run carrying an error_message finished non-genuinely (budget
    exhausted) — the tool must relay 'Ended early', not a misleading bare 'complete'."""
    from personalclaw.loop import store
    from personalclaw.loop.loop import LoopStatus
    _run(sdlc_tools.code_project_create({
        "task": "Add a /healthz endpoint to the Flask service",
        "stage_plan": [{"stage": "implementation", "title": "Impl", "objective": "add it"}],
    }))
    pid = [p.id for p in store.list_all() if p.kind == "code"][0]
    store.update_status(pid, LoopStatus.COMPLETE,
                        error_message="Cycle budget exhausted before all stages cleared")
    r = _run(sdlc_tools.sdlc_status({"id": pid}))
    assert r.success
    assert "ended early" in r.output.lower() and "budget exhausted" in r.output.lower()


def test_create_tools_carry_when_to_use_guidance():
    # The create tools must tell the agent WHEN to reach for them, not just the args —
    # without it the agent has the tools but no trigger, so the feature never fires
    # from chat. Guard the guidance so a future edit can't silently strip it.
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
    defs = {d.name: d for d in _run(NativeBuiltinToolProvider().list_tools())}
    # The unified project_run_create now carries the WHEN-to-use trigger for all kinds.
    assert "USE WHEN" in defs["project_run_create"].description


# ── start degrades cleanly without a live gateway ──

def test_code_start_without_gateway_is_clean_error(monkeypatch):
    monkeypatch.setattr(sdlc_tools, "_state", lambda: None)
    monkeypatch.setattr(sdlc_tools, "_svc", lambda: None)
    cr = _run(sdlc_tools.code_project_create({
        "task": "Add a /healthz endpoint to the Flask service",
        "stage_plan": [{"stage": "implementation", "title": "Impl", "objective": "add it"}],
    }))
    from personalclaw.loop import store
    pid = [p.id for p in store.list_all() if p.kind == "code"][0]
    r = _run(sdlc_tools.code_project_start({"project_id": pid}))
    assert r.success is False and "unavailable" in r.error.lower()


def test_code_start_unknown_id():
    r = _run(sdlc_tools.code_project_start({"project_id": "deadbeef"}))
    assert r.success is False


def test_code_start_on_running_is_clear_already_running(monkeypatch):
    # Starting an already-running project isn't a real failure — the tool says so (so the
    # agent reassures + offers sdlc_status) rather than the bare "can't start in 'running'".
    from personalclaw.loop import store
    from personalclaw.loop.loop import LoopStatus
    _run(sdlc_tools.code_project_create({
        "task": "Add a /healthz endpoint to the Flask service",
        "stage_plan": [{"stage": "implementation", "title": "Impl", "objective": "add it"}]}))
    pid = [p.id for p in store.list_all() if p.kind == "code"][0]
    store.update_status(pid, LoopStatus.RUNNING)
    r = _run(sdlc_tools.code_project_start({"project_id": pid}))
    assert r.success is False and "already running" in r.error.lower()
    assert any("sdlc_status" in h for h in (r.recovery_hints or []))


def test_code_start_on_terminal_is_clear_finished(monkeypatch):
    from personalclaw.loop import store
    from personalclaw.loop.loop import LoopStatus
    _run(sdlc_tools.code_project_create({
        "task": "Add a /metrics endpoint to the Flask service",
        "stage_plan": [{"stage": "implementation", "title": "Impl", "objective": "add it"}]}))
    pid = [p.id for p in store.list_all() if p.kind == "code"][-1]
    store.update_status(pid, LoopStatus.COMPLETE)
    r = _run(sdlc_tools.code_project_start({"project_id": pid}))
    assert r.success is False and "finished" in r.error.lower()


def test_goal_start_revalidates_a_fresh_start(monkeypatch):
    # A FRESH start (ready) re-validates like the HTTP start action — a since-invalidated
    # config is blocked before manager.start. Create a valid loop, then corrupt its
    # goal_type on disk so the re-validation trips.
    from personalclaw.loop import store
    _run(sdlc_tools.goal_loop_create({"goal": "Research the best caching strategy for the API"}))
    lid = [g.id for g in store.list_all() if g.kind == "goal"][0]
    # goal_type lives in kind_config under the unified model — corrupt it there.
    kc = dict(store.get(lid).kind_config)
    kc["goal_type"] = "nonsense"
    store.update_spec(lid, {"kind_config": kc})
    # gateway "available" so we'd reach manager.start if validation didn't block first
    monkeypatch.setattr(sdlc_tools, "_state", lambda: object())
    monkeypatch.setattr(sdlc_tools, "_svc", lambda: object())
    r = _run(sdlc_tools.goal_loop_start({"loop_id": lid}))
    assert r.success is False and "goal type" in r.error.lower()
