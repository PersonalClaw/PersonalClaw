"""The verification call site, and the tristate it must not collapse (TASKS-SOPS §1 — S61h).

`verified_done` was built in S56 as pure decision functions. Like `materialize` before
S61f, it had no
caller — a projected node's `done_criterion` was never executed by a run.

The defect this session found in its OWN first draft is the one worth reading: the
criterion evaluator
returns a TRISTATE (`True` / `False` / `None` = could not run), and the emitter wrote
`bool(passed)`.
`bool(None)` is `False`, so a criterion whose binary was missing reported **"your check failed"** —
sending the user to debug their code when the problem is their environment. §1 projects those two to
DIFFERENT blocked kinds (`needs_input` vs `capability`) precisely because they need different fixes,
so collapsing them at the emitter threw away the distinction the taxonomy exists to make.

Measured on real runs: a passing criterion, a failing one, and a missing binary now produce three
distinguishable outcomes.
"""

import asyncio

import pytest

from personalclaw.workflows.journal import TASK_VERIFIED, ledger


def _spec(children: list) -> dict:
    return {"name": "t", "root": {"kind": "sequence", "id": "s", "children": children}}


def _action(node_id: str, criterion: str | None = None) -> dict:
    config: dict = {"provider": "bash", "with": {"command": "true"}}
    if criterion is not None:
        config["done_means"] = criterion
    return {"kind": "action", "id": node_id, "config": config}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.workflows import store as wstore

    monkeypatch.setattr(wstore, "config_dir", lambda: tmp_path)
    from personalclaw.action_providers import registry as apreg

    apreg._ensure_default_providers_registered()
    yield


def _run(spec: dict, run_id: str = "r-1"):
    from personalclaw.workflows import store as wstore
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    run = WorkflowRun(id=run_id, workflow_name="t")
    wstore.create(run)
    controller = RunController(run, spec, services=EngineServices())
    asyncio.run(controller.run_to_completion())
    return run, controller


def _verified(run_id: str = "r-1") -> list[dict]:
    return [r for r in ledger(run_id) if r["kind"] == TASK_VERIFIED]


# ── the call site fires ──


def test_a_PASSING_criterion_verifies_true():
    _run(_spec([_action("good", "true")]))
    rows = _verified()
    assert len(rows) == 1
    assert rows[0]["passed"] is True
    assert rows[0]["unrunnable"] is False


def test_a_FAILING_criterion_verifies_false():
    _run(_spec([_action("bad", "false")]))
    row = _verified()[0]
    assert row["passed"] is False
    assert row["unrunnable"] is False


def test_an_UNRUNNABLE_criterion_is_NOT_reported_as_a_failure():
    """The defect this session fixed in its own draft. `bool(None)` is `False`, so a missing binary
    reported "your check failed" — sending the user to debug their code when the problem is their
    environment. §1 gives the two different blocked kinds for exactly that reason."""
    _run(_spec([_action("ghost", "definitely-not-a-real-binary-xyz")]))
    row = _verified()[0]
    assert row["unrunnable"] is True
    assert row["passed"] is False  # it did not pass — but see the flag above for WHY


def test_the_three_outcomes_are_DISTINGUISHABLE():
    """One boolean cannot express three states. A reader of the ledger must be able to
    tell a failing
    test from an absent one without guessing."""
    _run(
        _spec(
            [
                _action("good", "true"),
                _action("bad", "false"),
                _action("ghost", "no-such-binary-xyz"),
            ]
        )
    )
    outcomes = {r["node_id"]: (r["passed"], r["unrunnable"]) for r in _verified()}
    assert outcomes["good"] == (True, False)
    assert outcomes["bad"] == (False, False)
    assert outcomes["ghost"] == (False, True)
    assert len(set(outcomes.values())) == 3


def test_a_node_with_NO_criterion_emits_NOTHING():
    """`Task.can_mark_complete`'s rule is that a task with no exit criteria is freely completable.
    Emitting `passed=True` for a node nobody wrote a check for would manufacture evidence."""
    _run(_spec([_action("plain")]))
    assert _verified() == []


def test_an_EMPTY_criterion_emits_nothing():
    _run(_spec([_action("blank", "   ")]))
    assert _verified() == []


def test_the_criterion_TEXT_is_recorded():
    """ "Verification failed" without naming what was checked is a finding a user cannot act on, and
    the criterion is the def author's own words."""
    _run(_spec([_action("bad", "false")]))
    assert _verified()[0]["criterion"] == "false"


def test_a_long_criterion_is_bounded_in_the_record():
    _run(_spec([_action("bad", "false " + "x" * 500)]))
    assert len(_verified()[0]["criterion"]) <= 200


# ── the tristate at the decision layer ──


def test_an_unparseable_criterion_is_UNRUNNABLE_not_failed():
    """The author wrote something the engine could not read, which is a different problem from the
    work being wrong — and a different fix."""
    from personalclaw.workflows import store as wstore
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    run = WorkflowRun(id="r-1", workflow_name="t")
    wstore.create(run)
    controller = RunController(run, _spec([]), services=EngineServices())
    assert asyncio.run(controller._run_criterion(12345)) is None


def test_a_criterion_of_None_is_unrunnable():
    from personalclaw.workflows import store as wstore
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    run = WorkflowRun(id="r-1", workflow_name="t")
    wstore.create(run)
    controller = RunController(run, _spec([]), services=EngineServices())
    assert asyncio.run(controller._run_criterion(None)) is None


def test_an_UNREADABLE_file_check_is_unrunnable_not_a_missing_phrase():
    """`evaluate_file_phrase` treats an unreadable file as None: the phrase may well be there in a
    file this process cannot see, and reporting "the phrase is missing" would be a claim
    about content
    nobody read."""
    from personalclaw.workflows.controller import RunController

    assert RunController._read_criterion_file("/nonexistent/path/xyz") is None


def test_a_readable_file_is_returned_as_text(tmp_path):
    from personalclaw.workflows.controller import RunController

    target = tmp_path / "out.txt"
    target.write_text("all green")
    assert RunController._read_criterion_file(str(target)) == "all green"


def test_the_projection_maps_the_tristate_to_DIFFERENT_blocked_kinds():
    """The reason the collapse mattered: §1 sends a failed check and an unrunnable one to different
    remedies. `capability` points at the environment; the other points at the work."""
    from personalclaw.tasks.models import TaskStatus
    from personalclaw.workflows import verified_done as vd

    failed = vd.Verdict(results=[vd.CheckResult(kind="command", passed=False, weight=1.0)])
    unrunnable = vd.Verdict(results=[vd.CheckResult(kind="command", passed=None, weight=1.0)])
    _s1, kind_failed = vd.project_verified_status(failed)
    status_unrunnable, kind_unrunnable = vd.project_verified_status(unrunnable)
    assert kind_failed != kind_unrunnable
    assert kind_unrunnable == "capability"
    assert status_unrunnable is TaskStatus.BLOCKED


def test_a_criterion_free_verdict_is_freely_completable():
    """Matching the shipped `Task.can_mark_complete` seam rather than inventing a parallel rule."""
    from personalclaw.tasks.models import TaskStatus
    from personalclaw.workflows import verified_done as vd

    status, kind = vd.project_verified_status(vd.Verdict())
    assert status is TaskStatus.DONE
    assert kind == ""


# ── containment ──


def test_verification_does_not_block_the_TICK():
    """A criterion is a shell command — `pytest -q` is the canonical authoring shape. Running it
    inline in the sync settle path would block the whole tick on someone else's test suite, so it is
    scheduled like the write."""
    import inspect

    from personalclaw.workflows.controller import RunController

    source = inspect.getsource(RunController._schedule_verification)
    assert "loop.create_task" in source


def test_a_VERIFICATION_FAILURE_does_not_fail_the_run(monkeypatch):
    """The node succeeded and its output is journaled. A broken criterion must not
    retroactively fail
    work that completed."""
    from personalclaw.loop import gates

    async def boom(*_a, **_kw):
        raise RuntimeError("verifier exploded")

    monkeypatch.setattr(gates, "run_verify_command", boom)
    run, _ctl = _run(_spec([_action("impl", "true")]))
    assert run.status.value == "complete"


def test_verification_is_awaited_by_the_completion_DRAIN():
    """Tracked in the same in-flight set as the writes, so `run_to_completion` drains it —
    otherwise a
    caller that closed its loop would lose the verification exactly as S61g lost the board row."""
    _run(_spec([_action("good", "true")]))
    assert _verified()
