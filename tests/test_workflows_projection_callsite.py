"""The engine CALL SITE for task projection (TASKS-SOPS §1 — S61f).

S55 built `materialize` as pure decision functions and S61e gave the events somewhere to go. Nothing
invoked either during a real run — `grep` for `materialize.` outside its own module found zero hits,
so every rule in it was reachable only from a test.

This session wires it into `RunController` where a node settles, and these tests drive REAL runs to
completion rather than calling the hook directly: a call site that is never reached is exactly the
defect being fixed, and only an executed run proves it fires.

Two mismatches were measured while wiring it, both of which would have made the projection silently
project NOTHING:

* `should_materialize`/`plan_materialization` read the node keys **`id`**, `kind`, `path` and
  `config`. A `node_id` key — the name the *binding* uses — is ignored by both, so every node would
  have failed the has-an-id refusal.
* `plan.create` holds **`TaskSpec`** objects, not dicts. `entry.get("task_id")` would have raised
  inside the hook's own `except`, which swallows — so the projection would have failed invisibly on
  every single node.
"""

import asyncio
import pathlib

import pytest

from personalclaw.workflows.journal import TASK_MATERIALIZED, ledger


def _spec(children: list) -> dict:
    return {"name": "t", "root": {"kind": "sequence", "id": "s", "children": children}}


def _action(node_id: str, **config) -> dict:
    return {
        "kind": "action",
        "id": node_id,
        "config": {"provider": "bash", "with": {"command": "true"}, **config},
    }


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.workflows import store as wstore

    monkeypatch.setattr(wstore, "config_dir", lambda: tmp_path)
    from personalclaw.action_providers import registry as apreg

    # `bash` is a core-native provider but is only registered on demand. Without this the probe
    # run fails with "unknown action provider 'bash'" and the success path — the one that projects —
    # never executes. Measured while writing this file.
    apreg._ensure_default_providers_registered()
    yield


def _run(spec: dict, run_id: str = "r-1") -> tuple[object, list]:
    """Execute a spec to completion and AWAIT the projection writes, returning `(run, published)`.

    Awaiting the writes is required, not tidiness: S61g moved the event emission to the write's
    completion so `task_id` is the real id, which means the write is scheduled on the loop and a
    test that returned at `run_to_completion` would race it. The controller tracks the handles for
    exactly this — a sleep-based version of this helper would be a flake generator.
    """
    from personalclaw.workflows import store as wstore
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    published: list = []
    run = WorkflowRun(id=run_id, workflow_name="t")
    wstore.create(run)
    controller = RunController(
        run, spec, services=EngineServices(publish=lambda e, b: published.append((e, b)))
    )

    async def _go() -> None:
        await controller.run_to_completion()
        if controller._projection_writes:
            await asyncio.gather(*list(controller._projection_writes))

    asyncio.run(_go())
    return run, published


def _materialized(run_id: str = "r-1") -> list[dict]:
    return [r for r in ledger(run_id) if r["kind"] == TASK_MATERIALIZED]


# ── the call site fires on a real run ──


def test_a_completed_leaf_node_PROJECTS():
    """The whole point of the session. Before this, `materialize` had no caller at all — every rule
    in it was reachable only from a unit test."""
    run, _published = _run(_spec([_action("impl")]))
    assert run.status.value == "complete"
    assert [r["node_id"] for r in _materialized()] == ["impl"]


def test_EVERY_leaf_projects_with_its_own_fingerprint():
    """Distinct fingerprints are what make dedup work; two nodes sharing one would collapse into a
    single board row and the second node's work would be invisible."""
    _run(_spec([_action("impl"), _action("verify")]))
    rows = _materialized()
    assert [r["node_id"] for r in rows] == ["impl", "verify"]
    assert rows[0]["fingerprint"] != rows[1]["fingerprint"]


def test_a_projection_reaches_BOTH_channels():
    """The SSE stream is what the board folds live; the ledger is what a rebuild reads. A node that
    projected to only one of them shows on the board and vanishes on reload, or the reverse."""
    _run(_spec([_action("impl")]))
    _run_obj, published = _run(_spec([_action("impl")]), run_id="r-2")
    assert "workflow_task_materialized" in [e for e, _b in published]
    assert _materialized("r-2")


def test_a_CONTAINER_does_not_project():
    """A board row for a `parallel` node is a row nobody can act on — the container's work IS its
    children, and they project individually."""
    _run(
        _spec(
            [
                {"kind": "parallel", "id": "fan", "children": [_action("one"), _action("two")]},
            ]
        )
    )
    names = [r["node_id"] for r in _materialized()]
    assert "fan" not in names
    assert "one" in names and "two" in names


def test_an_explicit_OPT_OUT_is_honoured():
    """The author decided. It is the first refusal `should_materialize` checks, ahead of the kind
    heuristics, because an explicit declaration outranks an inference."""
    _run(_spec([_action("kept"), _action("skipped", materialize_task=False)]))
    names = [r["node_id"] for r in _materialized()]
    assert names == ["kept"]


def test_a_FAILED_node_does_not_project():
    """A task for work that did not happen is a board row a user would try to close. The hook runs
    only on the success branch, where the output is already journaled."""
    run, _p = _run(
        _spec(
            [
                {
                    "kind": "action",
                    "id": "boom",
                    "config": {"provider": "bash", "with": {"command": "exit 3"}},
                }
            ]
        )
    )
    assert run.status.value != "complete"
    assert _materialized() == []


# ── idempotency ──


def test_projecting_the_SAME_node_twice_in_one_run_is_a_REFRESH():
    """`plan_materialization` dedups on `(run_id, node_id)` AND fingerprint. The second call must
    report a refresh rather than a second create — §1 makes idempotent recompute the normal path, so
    this is not a rare case."""
    from personalclaw.workflows import store as wstore
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    spec = _spec([_action("impl")])
    run = WorkflowRun(id="r-1", workflow_name="t")
    wstore.create(run)
    controller = RunController(run, spec, services=EngineServices())
    asyncio.run(controller.run_to_completion())

    # Re-project the same settled node through the same controller. Inside a loop, because the
    # write (and therefore the event) is now scheduled rather than inline.
    async def _reproject() -> None:
        item = next(i for i in controller.instances)
        controller._project_task(
            type("_I", (), {"node": controller.root.children[0], "path": item})(), None, None
        )
        if controller._projection_writes:
            await asyncio.gather(*list(controller._projection_writes))

    asyncio.run(_reproject())
    flags = [r["refreshed"] for r in _materialized()]
    assert flags[0] is False
    assert True in flags, "a re-projection must be recorded as a refresh, not a second create"


def test_the_controller_REMEMBERS_what_it_projected():
    """The dedup set lives on the controller because it is the single writer for its own run — a
    per-node read of the per-entity JSON store would be one file scan per settled node."""
    from personalclaw.workflows import store as wstore
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    run = WorkflowRun(id="r-1", workflow_name="t")
    wstore.create(run)
    controller = RunController(run, _spec([_action("a"), _action("b")]), services=EngineServices())
    asyncio.run(controller.run_to_completion())
    assert [b.node_id for b in controller._projected] == ["a", "b"]
    assert all(b.managed for b in controller._projected)


# ── the hook must never break the run ──


def test_a_PROJECTION_FAILURE_does_not_fail_the_run(monkeypatch):
    """The node has already succeeded and its output is already journaled, so turning a board-row
    problem into a run failure would lose real work over a presentation concern."""
    from personalclaw.workflows import materialize

    def boom(*_a, **_kw):
        raise RuntimeError("materialize exploded")

    monkeypatch.setattr(materialize, "should_materialize", boom)
    run, _published = _run(_spec([_action("impl")]))
    assert run.status.value == "complete"
    assert _materialized() == []


def test_the_step_is_still_JOURNALED_when_projection_fails(monkeypatch):
    """The durable record of the WORK must not depend on the projection succeeding."""
    from personalclaw.workflows import materialize

    monkeypatch.setattr(
        materialize, "should_materialize", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError())
    )
    _run(_spec([_action("impl")]))
    assert any(r["kind"] == "step_completed" for r in ledger("r-1"))


# ── the measured API contract ──


def test_the_hook_passes_the_keys_materialize_actually_READS():
    """Measured: `should_materialize` reads `id`, not `node_id`. Passing the binding's name would
    make every node fail the has-an-id refusal and project nothing — silently, because the hook
    swallows."""
    import inspect

    from personalclaw.workflows.controller import RunController

    source = inspect.getsource(RunController._project_task)
    assert '"id": item.node.id' in source
    assert '"node_id": item.node.id' not in source


def test_the_hook_reads_TaskSpec_attributes_not_dict_keys():
    """Measured: `plan.create` holds `TaskSpec` objects. `entry.get(...)` would raise inside the
    hook's own `except`, so the projection would fail invisibly on every node.

    Read across the projection path rather than one method: S61g split the emission into
    `_schedule_task_write`, and pinning a single function name would make this test a rename
    detector instead of a contract check.
    """
    import inspect

    from personalclaw.workflows.controller import RunController

    source = "".join(
        inspect.getsource(fn)
        for fn in (
            RunController._project_task,
            RunController._schedule_task_write,
            RunController._write_projected_task,
        )
    )
    assert "spec.binding.fingerprint" in source
    assert "spec.binding.run_id" in source


def test_the_projection_hook_runs_on_the_SUCCESS_branch_only():
    """Pinned structurally: the call sits inside the `result.state in SUCCESS_STATES` block, so a
    future edit that moves it out shows up here rather than as tasks for failed work."""
    import inspect

    from personalclaw.workflows.controller import RunController

    source = inspect.getsource(RunController)
    success_at = source.index("if result.state in SUCCESS_STATES:")
    else_at = source.index("        else:", success_at)
    assert "self._project_task(" in source[success_at:else_at]


def test_materialize_now_HAS_a_caller():
    """The inverse of the grep that motivated the session: `materialize` is imported by the
    controller. A module with no caller is a module whose rules are decoration."""
    source = pathlib.Path("src/personalclaw/workflows/controller.py").read_text(encoding="utf-8")
    assert "materialize" in source
