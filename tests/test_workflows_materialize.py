"""Tests for the task projection core (TASKS-SOPS §1, S55). A materialized Task is a VIEW of a
node, not a second copy of the truth. Three properties follow, and each was measured rather
than assumed: **The projection table must be exhaustive.** An earlier version covered nine of
the engine's fourteen `InstanceState` members, and the five it missed all fell through to
`OPEN` — so a tripped circuit breaker, a scope violation and a protocol-violation block each
read on the board as ordinary work still to do. The gaps were filled from the engine's OWN
`SUCCESS_STATES`/`TERMINAL_STATES`, and a test here asserts both exhaustiveness and agreement
with those sets. **A new model field is dropped unless the provider names it.**
`NativeTaskProvider.create_task` builds its Task field-by-field, so the binding round-tripped
through `to_dict`/`from_dict` and still arrived empty from `create_task`. **`from_dict`
silently coerced an unknown status to OPEN.** Before `SKIPPED` existed, a task written with
`status: "skipped"` read back as work still to do — silently, on the board the user plans
from.
"""

import asyncio

import pytest

from personalclaw.tasks.models import Task, TaskStatus, WorkflowTaskBinding
from personalclaw.workflows.materialize import (
    ENGINE_OWNED_FIELDS,
    FANOUT_TASK_CAP,
    NON_MATERIALIZING_KINDS,
    OPT_OUT_KEY,
    STATE_TO_STATUS,
    TaskSpec,
    body_issues,
    build_body,
    fingerprint,
    managed,
    plan_materialization,
    progress_line,
    project_blocked_kind,
    project_status,
    reject_write,
    should_materialize,
)
from personalclaw.workflows.models import (
    SUCCESS_STATES,
    TERMINAL_STATES,
    InstanceState,
)


def node(node_id: str = "review", kind: str = "stage", **cfg) -> dict:
    return {"kind": kind, "id": node_id, "config": cfg}


# ── the projection table is exhaustive ──


def test_EVERY_engine_state_is_in_the_table():
    """Measured: nine of fourteen were covered, and the five missing ones fell through to OPEN —
    so a tripped circuit breaker read as ordinary work to do. A fallthrough in a projection is
    silent by construction; this test is what makes it loud.
    """
    missing = [s.value for s in InstanceState if s not in STATE_TO_STATUS]
    assert missing == [], f"engine states with no projection: {missing}"


@pytest.mark.parametrize("state", sorted(SUCCESS_STATES, key=lambda s: s.value))
def test_every_engine_SUCCESS_state_projects_to_done(state):
    """Cross-checked against the engine's own set rather than my reading of it. `no_change` is in
    `SUCCESS_STATES` — a node that inherited prior results succeeded, and filing it as open
    would put finished work back on the board.
    """
    assert project_status(state) is TaskStatus.DONE


@pytest.mark.parametrize("state", sorted(TERMINAL_STATES, key=lambda s: s.value))
def test_no_terminal_state_projects_to_IN_PROGRESS(state):
    """A terminal node is not working. Showing one as in-progress would make the board claim
    activity that has stopped, which is the specific lie a run watcher cannot detect.
    """
    assert project_status(state) is not TaskStatus.IN_PROGRESS


def test_a_running_node_is_in_progress():
    assert project_status(InstanceState.RUNNING) is TaskStatus.IN_PROGRESS


def test_a_skipped_node_is_SKIPPED_not_cancelled():
    """A declined branch is not a cancellation — nobody cancelled it, the run routed around it."""
    assert project_status(InstanceState.SKIPPED) is TaskStatus.SKIPPED


def test_a_DISCARDED_instance_is_skipped():
    """A rewind moved past it, which is what a declined branch means to someone reading the
    board."""
    assert project_status(InstanceState.DISCARDED) is TaskStatus.SKIPPED


def test_a_DEGRADED_node_is_done():
    """A degraded node SUCCEEDED with a machine-readable reason. Filing it as blocked would put
    completed work in the column the user scans for problems.
    """
    assert project_status(InstanceState.DEGRADED) is TaskStatus.DONE
    assert project_blocked_kind(InstanceState.DEGRADED) == ""


@pytest.mark.parametrize(
    "state",
    [
        InstanceState.ESCALATED,
        InstanceState.SCOPE_VIOLATION,
        InstanceState.BLOCKED,
        InstanceState.FAILED,
    ],
)
def test_a_stopped_node_is_BLOCKED(state):
    assert project_status(state) is TaskStatus.BLOCKED


# ── blocked_kind is a field, not a status explosion ──


def test_TaskStatus_gained_exactly_ONE_member():
    """A status per block reason is a state fork every surface then re-implements, and the
    surface that forgets shows a stale column. The WHY lives in `blocked_kind`.
    """
    assert {s.value for s in TaskStatus} == {
        "open",
        "in_progress",
        "done",
        "cancelled",
        "blocked",
        "skipped",
    }


def test_a_human_wait_is_needs_input():
    kind = project_blocked_kind(InstanceState.WAITING, waiting_on_human=True)
    assert kind == "needs_input"


def test_a_dependency_wait_is_NOT_needs_input():
    """The node is fine; its inputs are not. Calling it needs-input would put it in the queue of
    things the user has to answer, and there is nothing to answer.
    """
    assert project_blocked_kind(InstanceState.WAITING) == "dependency"


@pytest.mark.parametrize(
    "failure_class,expected",
    [
        ("permission", "capability"),
        ("budget", "capability"),
        ("transient", "transient"),
        ("network", "transient"),
        ("timeout", "transient"),
    ],
)
def test_a_failure_class_maps_to_a_blocked_kind(failure_class, expected):
    assert project_blocked_kind(InstanceState.FAILED, failure_class=failure_class) == expected


def test_an_UNKNOWN_failure_class_degrades_to_a_plain_block():
    """An unrecognized kind degrades to a plain `blocked` badge on every surface (R12), so
    passing it through as "" is safe — and normalizing it into a wrong kind would not be.
    """
    assert project_blocked_kind(InstanceState.FAILED, failure_class="protocol") == ""


def test_an_ESCALATED_node_carries_its_own_reason():
    """A tripped breaker is a breaker. A failure class would add nothing, and "transient" would
    be actively wrong — the breaker exists because retrying stopped helping.
    """
    assert project_blocked_kind(InstanceState.ESCALATED) == "capability"


def test_a_SCOPE_VIOLATION_wants_a_human():
    assert project_blocked_kind(InstanceState.SCOPE_VIOLATION) == "needs_input"


def test_an_unblocked_state_has_no_blocked_kind():
    for state in (InstanceState.RUNNING, InstanceState.DONE, InstanceState.SKIPPED):
        assert project_blocked_kind(state, failure_class="permission") == ""


# ── the fingerprint ──


def test_the_same_work_fingerprints_the_SAME():
    assert fingerprint(title="Review", body="check it") == fingerprint(
        title="Review", body="check it"
    )


def test_different_work_fingerprints_differently():
    assert fingerprint(title="Review", body="a") != fingerprint(title="Review", body="b")


def test_a_SOURCE_REF_wins_over_the_title():
    """Stable across a re-worded title: a rewind that re-materialized a node whose label had been
    edited would otherwise create a second task for one piece of work.
    """
    first = fingerprint(source_ref="node:review", title="Review the draft")
    second = fingerprint(source_ref="node:review", title="Review the article")
    assert first == second


def test_the_fingerprint_is_short_enough_to_eyeball():
    assert len(fingerprint(title="x")) == 16


# ── which nodes earn a task ──


@pytest.mark.parametrize("kind", sorted(NON_MATERIALIZING_KINDS))
def test_a_container_or_plumbing_node_earns_NO_task(kind):
    """A board row nobody can act on makes the actionable rows harder to find."""
    ok, why = should_materialize(node(kind=kind))
    assert ok is False
    assert why


def test_a_stage_earns_a_task():
    assert should_materialize(node(kind="stage"))[0] is True


def test_an_EXPLICIT_opt_out_wins():
    """ "This stage is internal" is an author's judgement, and no heuristic gets it right for a
    helper judge.
    """
    ok, why = should_materialize(node(kind="stage", **{OPT_OUT_KEY: False}))
    assert ok is False
    assert OPT_OUT_KEY in why


def test_a_node_with_no_id_earns_no_task():
    """A task the engine could not address is a task it could never update."""
    assert should_materialize({"kind": "stage", "id": "", "config": {}})[0] is False


# ── the task TITLE comes from the author's label (#382) ──


def test_a_task_is_titled_with_the_NODE_level_label():
    """Where every definition actually writes it — and where nothing read it.

    `plan_materialization` read `config.label`. A census of the bundled set found **92**
    materializing nodes carrying a node-level `label` and **ZERO** carrying `config.label`, so the
    `node_id` fallback was not a fallback: it was the only branch anything ever took, and it
    masked the miss. The user's board filled with `recall`, `weigh`, `gaps`, `n`, `nested` — and
    because `node_id` is unique only within a run, four separate tasks read as four identical
    rows.
    """
    plan = plan_materialization(
        "r-1",
        [{"kind": "action", "id": "recall", "label": "What do we already know?", "config": {}}],
    )
    assert [s.title for s in plan.create] == ["What do we already know?"]


def test_a_CONFIG_level_label_still_wins_over_the_node_id():
    """The key the code used to read is still read, second. A `config` blob is the node's own
    free-form space, and an author who put the label there meant it."""
    plan = plan_materialization("r-1", [node("weigh", label="Weigh the evidence")])
    assert [s.title for s in plan.create] == ["Weigh the evidence"]


def test_the_NODE_label_wins_over_a_CONFIG_label():
    """One node, two labels. The node-level key is the declared contract (`Node.label`); the
    config key is the tolerated alternative, so the declared one is the tiebreak."""
    plan = plan_materialization(
        "r-1",
        [{"kind": "action", "id": "n", "label": "declared", "config": {"label": "tolerated"}}],
    )
    assert [s.title for s in plan.create] == ["declared"]


def test_an_UNLABELLED_node_still_falls_back_to_its_id():
    """The fallback is sensible and stays. A node with no label has no better name than its id,
    and dropping the task over a missing label would lose the work."""
    plan = plan_materialization("r-1", [node("plain")])
    assert [s.title for s in plan.create] == ["plain"]


def test_label_is_a_DECLARED_field_on_the_node_model():
    """Not an `extra` key it merely survived as. The value was reachable the whole time — this is
    what makes reading it a contract rather than a dict-key guess, and it is why `to_dict` now
    round-trips it in the open."""
    from personalclaw.workflows.models import Node as SpecNode

    parsed = SpecNode.from_dict({"kind": "action", "id": "recall", "label": "Recall it"})
    assert parsed.label == "Recall it"
    assert "label" not in parsed.extra
    assert parsed.to_dict()["label"] == "Recall it"


def test_every_BUNDLED_materializing_node_carries_a_label_the_projection_can_read():
    """The census, as a rail. 🪤 The fake version of this test asserts one node's title and says
    nothing about the other 91 — which is how a defect measured across the whole bundled set went
    46 days without being noticed. So this walks the shipped definitions and requires that any
    label an author wrote is the title a user would see.
    """
    import json
    import pathlib

    import personalclaw

    bundled = pathlib.Path(personalclaw.__file__).parent / "workflows" / "bundled"
    checked = 0
    unread: list[str] = []

    def visit(raw: dict, wf: str) -> None:
        nonlocal checked
        if not isinstance(raw, dict):
            return
        ok, _why = should_materialize(raw)
        if ok and raw.get("label"):
            checked += 1
            plan = plan_materialization("r-census", [raw])
            if not plan.create or plan.create[0].title != raw["label"]:
                unread.append(f"{wf}:{raw.get('id')}")
        for child in raw.get("children") or []:
            visit(child, wf)
        if isinstance(raw.get("body"), dict):
            visit(raw["body"], wf)
        for case in (raw.get("cases") or {}).values():
            visit(case, wf)
        if isinstance(raw.get("default"), dict):
            visit(raw["default"], wf)

    for path in sorted(bundled.glob("*/workflow.json")):
        visit(json.loads(path.read_text(encoding="utf-8")).get("root") or {}, path.parent.name)

    assert checked >= 80, (
        f"only {checked} labelled materializing nodes found in the bundled set; the census "
        "measured 92, so this rail is reading the wrong tree or the wrong key"
    )
    assert not unread, f"{len(unread)} labelled nodes would still be titled with their id: {unread}"


# ── the materialization plan ──


def test_a_plan_creates_one_task_per_work_node():
    plan = plan_materialization("r-1", [node("a"), node("b"), node("root", kind="sequence")])
    assert [s.binding.node_id for s in plan.create] == ["a", "b"]
    assert any("root" in s for s in plan.skipped)


def test_an_ALREADY_MATERIALIZED_node_is_not_duplicated():
    """Per-file JSON storage gives no atomic check-and-create, so a resume has to recognize its
    own earlier work — dedup by lookup, not by transaction.
    """
    existing = [
        Task(id="t1", title="a", workflow_binding=WorkflowTaskBinding(run_id="r-1", node_id="a"))
    ]
    plan = plan_materialization("r-1", [node("a"), node("b")], existing_tasks=existing)
    assert [s.binding.node_id for s in plan.create] == ["b"]
    assert any("already materialized" in e for e in plan.existing)


def test_a_MATCHING_FINGERPRINT_dedups_across_a_different_node_id():
    """What a rewind-then-replan produces: the same work under a new node id. Checking only the
    (run, node) pair would duplicate it on the board.
    """
    print_key = fingerprint(title="Review", body="check it")
    existing = [
        Task(
            id="t1",
            title="Review",
            workflow_binding=WorkflowTaskBinding(
                run_id="r-1", node_id="old", fingerprint=print_key
            ),
        )
    ]
    plan = plan_materialization(
        "r-1", [node("new", label="Review", prompt="check it")], existing_tasks=existing
    )
    assert plan.create == []
    assert any(print_key in e for e in plan.existing)


def test_a_DIFFERENT_run_materializes_its_own_task():
    """Two runs of one template are two pieces of work. Deduping across runs would make the
    second run look already-done.
    """
    existing = [
        Task(id="t1", title="a", workflow_binding=WorkflowTaskBinding(run_id="r-1", node_id="a"))
    ]
    plan = plan_materialization("r-2", [node("a")], existing_tasks=existing)
    assert len(plan.create) == 1


def test_a_task_with_NO_binding_does_not_block_materialization():
    """A standalone task the user happens to have named similarly is not the run's work."""
    plan = plan_materialization("r-1", [node("a")], existing_tasks=[Task(id="t1", title="a")])
    assert len(plan.create) == 1


def test_existing_matches_are_REPORTED_not_silently_skipped():
    """A resume reporting "0 tasks created" with no detail is indistinguishable from one that
    failed to materialize anything.
    """
    existing = [
        Task(id="t1", title="a", workflow_binding=WorkflowTaskBinding(run_id="r-1", node_id="a"))
    ]
    plan = plan_materialization("r-1", [node("a")], existing_tasks=existing)
    assert plan.existing


# ── the fan-out cap ──


def test_a_big_fanout_is_CAPPED():
    """Twenty is a readable column; two hundred is a column nobody opens."""
    plan = plan_materialization("r-1", [node(f"item{i}") for i in range(60)])
    assert len(plan.create) == FANOUT_TASK_CAP
    assert plan.capped == 60 - FANOUT_TASK_CAP


def test_the_cap_NOTE_says_how_much_is_not_shown():
    """A board that is complete but unreadable is worse than one that says how much it is hiding."""
    plan = plan_materialization("r-1", [node(f"item{i}") for i in range(30)])
    assert str(FANOUT_TASK_CAP) in plan.cap_note
    assert "30" in plan.cap_note


def test_a_small_fanout_is_not_capped():
    plan = plan_materialization("r-1", [node(f"item{i}") for i in range(5)])
    assert plan.capped == 0
    assert plan.cap_note == ""


def test_the_cap_is_configurable_per_call():
    plan = plan_materialization("r-1", [node(f"i{i}") for i in range(10)], cap=3)
    assert len(plan.create) == 3


def test_a_zero_cap_still_materializes_ONE():
    """A cap of zero would show an empty board for a fan-out that is running, which is a board
    that lies by omission.
    """
    plan = plan_materialization("r-1", [node("a"), node("b")], cap=0)
    assert len(plan.create) == 1


def test_the_progress_line_names_BLOCKED_separately():
    """ "18 of 200" and "18 of 200, 3 blocked" call for different actions, and the first hides the
    second.
    """
    assert progress_line(18, 200, 3) == "18 of 200 complete, 3 blocked"
    assert progress_line(18, 200) == "18 of 200 complete"


def test_an_empty_fanout_has_no_progress_line():
    assert progress_line(0, 0) == ""


# ── managed vs produced vs standalone ──


def test_a_managed_task_is_engine_owned():
    task = Task(id="t", title="x", workflow_binding=WorkflowTaskBinding(run_id="r", node_id="n"))
    assert managed(task) is True


def test_a_PRODUCED_task_is_not_managed():
    """`managed=False` WITH a binding: the workflow created the work but tracks nothing about it.
    Collapsing this into "managed" would make the engine responsible for work it only
    suggested.
    """
    task = Task(
        id="t",
        title="x",
        workflow_binding=WorkflowTaskBinding(run_id="r", node_id="n", managed=False),
    )
    assert managed(task) is False


def test_a_STANDALONE_task_is_not_managed():
    assert managed(Task(id="t", title="x")) is False


def test_a_produced_task_KEEPS_its_provenance():
    """The middle case has three configurations for a reason: dropping the binding would lose
    where the task came from, and the board could not link it back to the run.
    """
    task = Task(
        id="t",
        title="x",
        workflow_binding=WorkflowTaskBinding(run_id="r-9", node_id="n", managed=False),
    )
    assert task.workflow_binding.run_id == "r-9"


# ── the write façade rejects, it does not merge ──


def test_a_user_STATUS_write_on_a_managed_task_is_refused():
    """Two writers on one status field produce a board that disagrees with the run it is showing,
    and the user believes the board.
    """
    task = Task(id="t", title="x", workflow_binding=WorkflowTaskBinding(run_id="r-7", node_id="n"))
    why = reject_write(task, {"status": "done"})
    assert why
    assert "r-7" in why


def test_the_refusal_names_the_ALTERNATIVE():
    """A refusal that does not say what to do instead reads as the feature being broken."""
    task = Task(id="t", title="x", workflow_binding=WorkflowTaskBinding(run_id="r", node_id="n"))
    why = reject_write(task, {"status": "done"})
    assert "workflow_skip" in why or "workflow_rewind" in why


@pytest.mark.parametrize("field_name", sorted(ENGINE_OWNED_FIELDS))
def test_every_engine_owned_field_is_protected(field_name):
    """Not just status: a user edit to `evidence` would be a human asserting the machine's
    finding."""
    task = Task(id="t", title="x", workflow_binding=WorkflowTaskBinding(run_id="r", node_id="n"))
    assert reject_write(task, {field_name: "anything"})


def test_a_USER_owned_field_is_still_writable_on_a_managed_task():
    """The engine owns the projection, not the whole task. A user must still be able to add a
    note or change the assignee on work the run is driving.
    """
    task = Task(id="t", title="x", workflow_binding=WorkflowTaskBinding(run_id="r", node_id="n"))
    assert reject_write(task, {"assignee": "me", "labels": ["urgent"]}) == ""


def test_a_STANDALONE_task_accepts_any_write():
    assert reject_write(Task(id="t", title="x"), {"status": "done"}) == ""


def test_a_PRODUCED_task_accepts_a_status_write():
    """Nobody tracks its completion, so the user is the only one who can say it is done."""
    task = Task(
        id="t",
        title="x",
        workflow_binding=WorkflowTaskBinding(run_id="r", node_id="n", managed=False),
    )
    assert reject_write(task, {"status": "done"}) == ""


# ── the body contract ──


def test_a_body_leads_with_BEHAVIOR():
    """Someone picking up the task needs to know what it IS before what proves it. A body that
    opened with acceptance criteria reads as a checklist for work nobody described.
    """
    body = build_body("Add retry to the ingest path", ["retries are bounded", "a test covers it"])
    assert body.index("What to build") < body.index("Acceptance")


def test_acceptance_criteria_render_as_CHECKBOXES():
    """A checkbox is a thing a person can tick; a sentence is not. The `done_criterion` the
    engine runs is a separate machine check, deliberately not the same field.
    """
    body = build_body("x", ["first", "second"])
    assert "- [ ] first" in body
    assert "- [ ] second" in body


def test_blocked_by_is_listed_when_present():
    body = build_body("x", ["ok"], blocked_by=["t-123"])
    assert "Blocked by" in body
    assert "t-123" in body


def test_a_body_with_no_blockers_omits_the_section():
    """An empty "Blocked by" heading reads as "nothing is blocking this", which is a claim."""
    assert "Blocked by" not in build_body("x", ["ok"])


def test_a_CODE_SNIPPET_in_a_body_is_flagged():
    """File paths and code snippets go stale the moment the tree moves, and a body that
    confidently names a moved file sends the reader to the wrong place.
    """
    issues = body_issues("Do the thing\n```python\nprint(1)\n```\n\nAcceptance: it works")
    assert any("stale" in i for i in issues)


def test_a_body_with_no_ACCEPTANCE_section_is_flagged():
    """Without one, "done" is whatever the reader decides."""
    assert any("acceptance" in i for i in body_issues("Just build it"))


def test_a_conforming_body_is_clean():
    assert body_issues(build_body("Add retry", ["retries are bounded"])) == []


def test_an_empty_body_is_not_flagged():
    """A task with no body yet is a task being drafted, not a contract violation."""
    assert body_issues("") == []


def test_the_lint_is_ADVISORY():
    """A body with a snippet is still a body; dropping the task to enforce formatting would lose
    the work. The finding is reported so the author can fix the staleness.
    """
    spec = TaskSpec(
        title="x", binding=WorkflowTaskBinding(run_id="r", node_id="n"), body="```code```"
    )
    assert body_issues(spec.body)
    assert spec.to_fields()["description"] == "```code```"


# ── the round trip through the REAL task store ──


@pytest.fixture()
def task_home(monkeypatch, tmp_path):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


def test_a_binding_SURVIVES_create_and_reload(task_home):
    """Measured: `NativeTaskProvider.create_task` builds its Task field-by-field, so the binding
    round-tripped through `to_dict`/`from_dict` and still arrived EMPTY from `create_task`
    until the provider named it too.
    """
    from personalclaw.tasks import registry

    async def run():
        binding = WorkflowTaskBinding(
            run_id="r-55", node_id="review", node_path="root.children[0]", fingerprint="abc123"
        )
        created = await registry.create_task(
            title="Review the draft", workflow_binding=binding, done_criterion="a judge passes"
        )
        assert created.workflow_binding == binding
        assert created.done_criterion == "a judge passes"
        reloaded = await registry.get_task(created.id)
        assert reloaded.workflow_binding == binding
        return reloaded

    reloaded = asyncio.run(run())
    assert reloaded.workflow_binding.managed is True


def test_a_DICT_form_binding_is_accepted(task_home):
    """Both shapes arrive in practice — the engine passes the dataclass, a REST/tool caller
    passes JSON. Refusing either would push the coercion to every call site, and the site that
    forgot would create a task the engine does not own while the board shows it as managed.
    """
    from personalclaw.tasks import registry

    async def run():
        return await registry.create_task(
            title="From JSON", workflow_binding={"run_id": "r", "node_id": "n", "managed": False}
        )

    created = asyncio.run(run())
    assert created.workflow_binding is not None
    assert created.workflow_binding.managed is False


def test_the_ENGINE_completion_path_persists_evidence(task_home):
    from personalclaw.tasks import registry

    async def run():
        created = await registry.create_task(
            title="X", workflow_binding=WorkflowTaskBinding(run_id="r", node_id="n")
        )
        await registry.update_task(
            created.id, status="done", evidence=[{"kind": "gate", "node": "check"}]
        )
        return await registry.get_task(created.id)

    reloaded = asyncio.run(run())
    assert reloaded.status is TaskStatus.DONE
    assert reloaded.evidence == [{"kind": "gate", "node": "check"}]
    assert reloaded.workflow_binding is not None


# ── the status coercion that used to be silent ──


def test_a_SKIPPED_status_no_longer_degrades_to_open():
    """Measured before adding the member: `from_dict` coerced an unknown status to OPEN, so a
    skipped task read back as work still to do — silently, on the board the user plans from.
    """
    assert (
        Task.from_dict({"id": "t", "title": "x", "status": "skipped"}).status is TaskStatus.SKIPPED
    )


def test_a_GENUINELY_unknown_status_still_degrades_safely():
    """Tolerance is still right for a value this build does not know — OPEN keeps the work
    visible, which is the recoverable direction.
    """
    assert Task.from_dict({"id": "t", "title": "x", "status": "quantum"}).status is TaskStatus.OPEN


def test_every_projection_field_round_trips():
    task = Task(
        id="t",
        title="x",
        status=TaskStatus.SKIPPED,
        workflow_binding=WorkflowTaskBinding(
            run_id="r", node_id="n", managed=False, fingerprint="f"
        ),
        blocked_kind="capability",
        preview="waiting on a token",
        done_criterion="tests pass",
        evidence=[{"kind": "log"}],
        attempts=[{"attempt": 1}],
    )
    assert Task.from_dict(task.to_dict()) == task


def test_a_PRE_EXISTING_task_json_still_loads():
    """Additive with empty defaults: task files written before this session have none of these
    keys and must read back as standalone, unblocked, with no criterion.
    """
    old = {"id": "t-old", "title": "Legacy", "status": "open", "priority": "medium"}
    task = Task.from_dict(old)
    assert task.workflow_binding is None
    assert task.blocked_kind == ""
    assert task.evidence == []
