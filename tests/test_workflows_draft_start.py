"""A DRAFT run can be started — the caller-driven launch a fork had no verb for (#372).

`_apply_fork` leaves its child in DRAFT deliberately and says so: *"starting it is the caller's
decision, because a fork is usually created to be edited before it runs ('try a stricter judge').
Auto-starting would race the edit it exists to receive."* The decision was never deliverable.
Fork is the entry point to the whole branch-and-edit story, so the feature was a dead end: every
use of the UI's **Fork run** button added another run that could never execute, and the only
outcome its author was offered was cancelling something that never started.

**The closed loop, measured on a real fork.** The nine run verbs all assume a run already
running, and two of them said so in their own remediation:

    POST …/run-from {"node_id": "scan"}  → 409 "resume the run before run_from"
    POST …/resume {}                     → 200 {"resumed": true, "gate_answered": false}
       ... status afterwards: still "draft"
    POST …/run-from {"node_id": "scan"}  → 409  (identical)

`resume`'s no-token path popped `pause_requested` — a key a draft never had — saved, and reported
success. So the remediation pointed at the one call that was guaranteed to do nothing, and neither
end of the loop was wrong on its own.

Three obligations follow, one per leg below: a verb that starts an existing draft; a `resume` that
refuses instead of lying; and a remediation that names `start` for a run whose phase is PRELAUNCH.

🪤 THE FAKE VERSION of this suite asserts the new route answers 202. That says nothing about the
run actually being driven (a route that flipped the status and dropped the run would pass), and
nothing about the two false-success paths that made the loop closed — so the launch leg observes
the SUPERVISOR being handed the run, and the loop legs assert the refusals directly.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.workflows import service, store
from personalclaw.workflows.models import RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


SPEC = {"name": "wf", "root": {"kind": "transform", "id": "t", "config": {"expr": "1"}}}


class _RecordingSupervisor:
    """A supervisor that records what it was asked to launch.

    The launch is the whole assertion: `start_draft_run` must hand the EXISTING run row and its
    stored spec to the supervisor, because the lineage a fork recorded is the thing being started.
    A fix that created a second run from the same def would leave the fork stranded exactly as
    before, and a status-only test could not tell the two apart.
    """

    def __init__(self) -> None:
        self.launched: list[tuple[str, dict]] = []

    def controller(self, run_id: str):
        return None

    async def launch(self, run, spec, *, depth: int = 0):
        self.launched.append((run.id, spec))
        return object()


class _State:
    """The one attribute `handlers._supervisor` reads. The supervisor is resolved from
    `app["state"].workflows` per request, not captured at registration, so a route test has to
    supply that shape rather than a bare key."""

    def __init__(self, supervisor) -> None:
        self.workflows = supervisor


def _draft(**kw) -> WorkflowRun:
    run = store.create(WorkflowRun(id="", workflow_name="wf", status=RunStatus.DRAFT, **kw))
    store.write_spec(run.id, SPEC)
    return run


# ── the start verb exists and drives the run that already exists ─────────────


async def test_a_DRAFT_run_can_be_started() -> None:
    run = _draft()
    sup = _RecordingSupervisor()
    body = await service.start_draft_run(run.id, supervisor=sup)
    assert body["ok"] is True
    assert body["started"] is True
    assert body["status"] == RunStatus.RUNNING.value
    assert sup.launched == [(run.id, SPEC)], (
        "the existing run was not handed to the supervisor — a start that mints a new run leaves "
        "the fork stranded, which is the defect (#372)"
    )


async def test_a_FORKED_run_is_the_case_this_exists_for() -> None:
    """End to end through `fork_run`, because the fork is what produces the stranded draft.

    Asserting on a hand-made draft alone would leave the reported path untested: the fork's child
    carries `forked_from` and inherits its parent's node states, and it is that row — not a fresh
    instantiation of the def — that has to start.
    """
    parent = store.create(WorkflowRun(id="", workflow_name="wf", status=RunStatus.COMPLETE))
    store.write_spec(parent.id, SPEC)
    forked = service.fork_run(parent.id, note="branched from the run view")
    assert forked["ok"] is True
    child_id = forked["child_run_id"]
    assert store.get(child_id).status is RunStatus.DRAFT

    sup = _RecordingSupervisor()
    body = await service.start_draft_run(child_id, supervisor=sup)
    assert body["ok"] is True
    assert [rid for rid, _ in sup.launched] == [child_id]


async def test_starting_a_run_that_ALREADY_LAUNCHED_is_refused_with_the_verb_that_applies() -> None:
    """`WF_RUN_NOT_PRELAUNCH`, the same code the policy-overlay editor uses — gated on the
    lifecycle PHASE, not the literal status, so the two prelaunch-only operations cannot drift.
    And the message names resume/rewind/fork, because "cannot start" with no alternative is what
    made the original loop unreadable."""
    for status in (RunStatus.RUNNING, RunStatus.COMPLETE, RunStatus.CANCELLED):
        run = store.create(WorkflowRun(id="", workflow_name="wf", status=status))
        store.write_spec(run.id, SPEC)
        body = await service.start_draft_run(run.id, supervisor=_RecordingSupervisor())
        assert body["ok"] is False
        assert body["code"] == "WF_RUN_NOT_PRELAUNCH"
        assert status.value in body["message"]
        assert "fork" in body["message"] and "resume" in body["message"]


async def test_starting_a_run_that_does_not_exist_is_NOT_FOUND() -> None:
    body = await service.start_draft_run("no-such-run", supervisor=_RecordingSupervisor())
    assert body["ok"] is False
    assert body["code"] == "WF_RUN_NOT_FOUND"


async def test_a_MISSING_SUPERVISOR_is_reported_rather_than_silently_skipped() -> None:
    """The run stays startable. Reporting `WF_NO_SUPERVISOR` is what `start_run` already does, and
    a 200 with nothing launched would be the same false success this issue is about."""
    run = _draft()
    body = await service.start_draft_run(run.id, supervisor=None)
    assert body["ok"] is False
    assert body["code"] == "WF_NO_SUPERVISOR"
    assert store.get(run.id).status is RunStatus.DRAFT


# ── the closed loop: neither end may lie ─────────────────────────────────────


def test_RESUME_no_longer_claims_success_on_a_draft_and_writes_nothing() -> None:
    """The centre of the loop. `{"resumed": true}` with the status unchanged is a specific false
    statement, and the write underneath it was real — the clear-pause path popped a key and SAVED,
    so this asserts the row is untouched as well as the refusal."""
    run = _draft()
    # The mark the clear-pause path would have popped. Same instrument the issue-679 rail uses on
    # the terminal legs, for the same reason: a status-only assertion leaves the write unexamined.
    run.extra["pause_requested"] = True
    store.save(run)

    body = service.resume_run(run.id, supervisor=_RecordingSupervisor())

    assert body["ok"] is False
    assert body["code"] == "WF_RUN_NOT_LIVE"
    assert "start" in body["message"]
    assert body.get("resumed") is None
    after = store.get(run.id)
    assert after.status is RunStatus.DRAFT
    assert after.extra.get("pause_requested") is True, "resume wrote to a run it refused"


@pytest.mark.parametrize("verb", ["rewind_run", "run_from"])
def test_the_REENTRY_remediation_names_start_for_a_draft(verb: str) -> None:
    """The other end of the loop. "resume the run before run_from" sent the caller to the call
    that did nothing; a draft's first verb is `start`."""
    run = _draft()
    body = getattr(service, verb)(run.id, "t", supervisor=_RecordingSupervisor())
    assert body["ok"] is False
    assert body["code"] == "WF_RUN_NOT_LIVE"
    assert "start the run before" in body["message"]


@pytest.mark.parametrize("verb", ["rewind_run", "run_from"])
def test_the_REENTRY_remediation_still_names_resume_for_a_LAUNCHED_run(verb: str) -> None:
    """🪤 The vacuity floor for the leg above: a paused run's remediation is unchanged. Swapping
    the word unconditionally would send a genuinely resumable run to a verb that refuses it."""
    run = store.create(WorkflowRun(id="", workflow_name="wf", status=RunStatus.PAUSED))
    store.write_spec(run.id, SPEC)
    body = getattr(service, verb)(run.id, "t", supervisor=_RecordingSupervisor())
    assert body["ok"] is False
    assert "resume the run before" in body["message"]


# ── the HTTP door ────────────────────────────────────────────────────────────


async def test_the_START_route_is_registered_and_distinct_from_create_and_start() -> None:
    """A route an agent cannot find is unreachable to it, and the whole defect was an absent verb.
    Asserted against the router rather than a client call so the registration itself is the claim —
    and paired with the create route, because the two are different operations on one noun.
    """
    from aiohttp import web

    from personalclaw.workflows.handlers import register_workflow_routes

    app = web.Application()
    register_workflow_routes(app)
    posts = {
        r.resource.canonical
        for r in app.router.routes()
        if r.method == "POST" and r.resource is not None
    }
    assert "/api/workflows/runs/{run_id}/start" in posts
    assert "/api/workflows/runs" in posts


async def test_the_START_route_answers_202_and_launches() -> None:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.workflows.handlers import register_workflow_routes

    run = _draft()
    sup = _RecordingSupervisor()
    app = web.Application()
    app["state"] = _State(sup)
    register_workflow_routes(app)
    async with TestClient(TestServer(app)) as client:
        r = await client.post(f"/api/workflows/runs/{run.id}/start")
        assert r.status == 202, await r.text()
        body = await r.json()
        assert body["started"] is True
    assert [rid for rid, _ in sup.launched] == [run.id]


async def test_the_START_route_translates_the_phase_refusal_to_409() -> None:
    """`run_not_prelaunch` is already in `_STATUS_MAP`; this pins that the new route rides it
    rather than falling through to the 400 default."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.workflows.handlers import register_workflow_routes

    run = store.create(WorkflowRun(id="", workflow_name="wf", status=RunStatus.COMPLETE))
    store.write_spec(run.id, SPEC)
    app = web.Application()
    app["state"] = _State(_RecordingSupervisor())
    register_workflow_routes(app)
    async with TestClient(TestServer(app)) as client:
        r = await client.post(f"/api/workflows/runs/{run.id}/start")
        assert r.status == 409
        assert (await r.json())["error"]["code"] == "run_not_prelaunch"


def test_start_draft_run_is_not_a_second_start_run() -> None:
    """Structural, and the reason this is a new verb rather than a widened one. `start_run` takes a
    def NAME and creates the row it starts; pointing it at an existing draft would mint a second
    run. The two must stay distinguishable by signature so a future caller cannot reach for the
    wrong one."""
    import inspect

    assert list(inspect.signature(service.start_draft_run).parameters)[0] == "run_id"
    assert "run_id" not in inspect.signature(service.start_run).parameters
    assert asyncio.iscoroutinefunction(service.start_draft_run)
