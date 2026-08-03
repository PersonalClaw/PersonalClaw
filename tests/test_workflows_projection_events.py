"""The task-projection events on both channels (TASKS-SOPS §7/§8 — S61e).

The FE stream unions were recorded as BLOCKED in S61c: the engine emitted none of
`task_materialized`, `confirmation_pending`, `confirmation_resolved`, `task_verified`
and `cascade_blocked`,
and registering a union member for an event nothing sends would be the same present-and-
inert control this program keeps finding. This session unblocks them by emitting on both
channels at once.

Two channels, one vocabulary. The LIVE stream (`RunController._publish`, what the FE folds)
and the REPLAYABLE ledger (`journal`, what a refiner and a diagnosis read) carry the same
fact under names that differ only by the `workflow_` prefix. A consumer folding the stream
and one reconstructing from history would otherwise need two vocabularies for one event, and
the second always drifts.

Measured before asserting: `RunController._publish` accepts ANY event name — there is no
server-side allowlist — so the FE's `WORKFLOW_LIFECYCLE` array is the only gate, and
EventSource silently drops an unregistered type. A member missing from that list is not a
component ignoring an event; it is an event the frontend never receives, with nothing
anywhere to say so.
"""

import pathlib

import pytest

from personalclaw.workflows.journal import (
    CASCADE_BLOCKED,
    CONFIRMATION_PENDING,
    CONFIRMATION_RESOLVED,
    LEDGER_KINDS,
    TASK_MATERIALIZED,
    TASK_VERIFIED,
    Journal,
    ledger,
)

SPEC = {
    "name": "t",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [
            {
                "kind": "action",
                "id": "a",
                "config": {"provider": "bash", "with": {"command": "true"}},
            }
        ],
    },
}

PROJECTION_KINDS = (
    TASK_MATERIALIZED,
    CONFIRMATION_PENDING,
    CONFIRMATION_RESOLVED,
    TASK_VERIFIED,
    CASCADE_BLOCKED,
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Journal writes go under a tmp home. `store.config_dir` is the module-level bind that
    matters — patching only `config.loader.config_dir` leaves the store on the real home."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.workflows import store as wstore

    monkeypatch.setattr(wstore, "config_dir", lambda: tmp_path)
    yield


def _controller(published: list):
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    services = EngineServices(publish=lambda ev, body: published.append((ev, body)))
    return RunController(WorkflowRun(id="r-1", workflow_name="t"), SPEC, services=services)


# ── the ledger vocabulary ──


@pytest.mark.parametrize("kind", PROJECTION_KINDS)
def test_each_projection_kind_is_in_LEDGER_KINDS(kind):
    """`LEDGER_KINDS` is the set a downstream refiner reads and a drift test asserts. A kind outside
    it is written to disk and read by nothing."""
    assert kind in LEDGER_KINDS


def test_the_kind_names_have_no_workflow_prefix():
    """The prefix belongs to the SSE channel, not the ledger. Two prefixed vocabularies would make
    `ledger(kinds=...)` filtering silently return nothing."""
    assert not any(k.startswith("workflow_") for k in PROJECTION_KINDS)


# ── the journal emitters ──


def test_materialization_records_the_task_and_its_fingerprint():
    """ "Why does this task exist" is a question the ledger is the only place to answer."""
    Journal("r-1").task_materialized("root.children[0]", "a", task_id="t-1", fingerprint="fp")
    row = ledger("r-1")[0]
    assert row["kind"] == TASK_MATERIALIZED
    assert row["task_id"] == "t-1"
    assert row["fingerprint"] == "fp"
    assert row["refreshed"] is False


def test_a_REFRESH_is_distinguishable_from_a_first_materialization():
    """§1 makes idempotent recompute the NORMAL path, so a reader counting materializations
    over-counts the run's output on every rewind without this flag."""
    j = Journal("r-1")
    j.task_materialized("root.children[0]", "a", task_id="t-1")
    j.task_materialized("root.children[0]", "a", task_id="t-1", refreshed=True)
    flags = [r["refreshed"] for r in ledger("r-1")]
    assert flags == [False, True]


def test_a_pending_confirmation_is_recorded_when_the_WAIT_STARTS():
    """A run unanswered for a week and one answered instantly are indistinguishable from
    the resolution alone — and the wait is the number a user cares about."""
    Journal("r-1").confirmation_pending("root.children[0]", "a", confirmation_id="cr-1")
    row = ledger("r-1")[0]
    assert row["kind"] == CONFIRMATION_PENDING
    assert row["confirmation_id"] == "cr-1"


def test_a_resolution_records_BOTH_the_verb_and_the_boolean():
    """The boolean is what the engine acted on; the verb is what the user chose. Recording only the
    boolean would leave an audit unable to tell a reject from an expiry auto-reject — the exact
    distinction §4's per-type expiry policy exists to create."""
    Journal("r-1").confirmation_resolved(
        "root.children[0]",
        "a",
        confirmation_id="cr-1",
        verb="reject",
        approved=False,
        resolved_by="dashboard:chat-1",
    )
    row = ledger("r-1")[0]
    assert row["verb"] == "reject"
    assert row["approved"] is False
    assert row["resolved_by"] == "dashboard:chat-1"


def test_an_unattributed_resolution_says_UNKNOWN_rather_than_empty():
    """An empty resolver reads as "no resolver", indistinguishable from an unrecorded one."""
    Journal("r-1").confirmation_resolved(
        "root.children[0]", "a", confirmation_id="cr-1", verb="approve", approved=True
    )
    assert ledger("r-1")[0]["resolved_by"] == "unknown"


def test_verification_records_the_CRITERION_that_was_checked():
    """ "Verification failed" without naming what was checked is a finding a user cannot act on."""
    Journal("r-1").task_verified(
        "root.children[0]", "a", task_id="t-1", passed=False, criterion="tests green"
    )
    row = ledger("r-1")[0]
    assert row["passed"] is False
    assert row["criterion"] == "tests green"


def test_a_cascade_is_ONE_event_carrying_every_blocked_id():
    """N events for one upstream failure would make the run look like it failed N times, and §1
    already debounces the notification — two collapse points would disagree."""
    Journal("r-1").cascade_blocked(
        "root.children[0]", "a", blocked_task_ids=["t-2", "t-3"], cause="a failed: boom"
    )
    rows = ledger("r-1")
    assert len(rows) == 1
    assert rows[0]["blocked_task_ids"] == ["t-2", "t-3"]
    assert "boom" in rows[0]["cause"]


# ── both channels, one vocabulary ──


def test_the_controller_emits_on_BOTH_channels():
    """The live stream and the replayable ledger must carry the same fact. A consumer folding the
    stream and one reconstructing from history would otherwise need two vocabularies."""
    published: list = []
    ctl = _controller(published)
    ctl.publish_task_materialized("root.children[0]", "a", task_id="t-1")
    assert [e for e, _b in published] == ["workflow_task_materialized"]
    assert [r["kind"] for r in ledger("r-1")] == [TASK_MATERIALIZED]


def test_every_stream_name_is_the_ledger_kind_PREFIXED():
    """Mechanical, so a rename cannot drift the two apart silently."""
    published: list = []
    ctl = _controller(published)
    ctl.publish_task_materialized("root.children[0]", "a", task_id="t-1")
    ctl.publish_confirmation_pending("root.children[0]", "a", confirmation_id="cr-1")
    ctl.publish_confirmation_resolved(
        "root.children[0]", "a", confirmation_id="cr-1", verb="approve", approved=True
    )
    ctl.publish_task_verified("root.children[0]", "a", task_id="t-1", passed=True)
    ctl.publish_cascade_blocked("root.children[0]", "a", blocked_task_ids=["t-2"], cause="x")
    stream = [e for e, _b in published]
    kinds = [r["kind"] for r in ledger("r-1")]
    assert stream == [f"workflow_{k}" for k in kinds]


def test_every_published_event_carries_the_DEDUP_identity():
    """`_publish` stamps `event_id`/`seq`/`epoch` centrally. Without them a re-emit is a second row
    and a rewind duplicates the board — invisible until it happens."""
    published: list = []
    ctl = _controller(published)
    ctl.publish_task_verified("root.children[0]", "a", task_id="t-1", passed=True)
    _event, body = published[0]
    assert {"run_id", "event_id", "seq", "epoch"} <= set(body)


def test_a_BROKEN_observer_does_not_kill_the_run():
    """A publish observer is exactly the kind of thing that fails in the field, and
    `_publish` already swallows — this asserts the projection emitters inherit that
    rather than bypassing it."""
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    def boom(_event, _body):
        raise RuntimeError("observer exploded")

    ctl = RunController(
        WorkflowRun(id="r-1", workflow_name="t"), SPEC, services=EngineServices(publish=boom)
    )
    ctl.publish_task_materialized("root.children[0]", "a", task_id="t-1")
    # The ledger write still happened: the durable record must not depend on a live observer.
    assert [r["kind"] for r in ledger("r-1")] == [TASK_MATERIALIZED]


def test_NO_publish_service_still_writes_the_ledger():
    """A run with no observer (a CLI run, a replay) must still produce its history."""
    from personalclaw.workflows.controller import RunController
    from personalclaw.workflows.models import WorkflowRun

    ctl = RunController(WorkflowRun(id="r-1", workflow_name="t"), SPEC)
    ctl.publish_cascade_blocked("root.children[0]", "a", blocked_task_ids=["t-2"], cause="x")
    assert [r["kind"] for r in ledger("r-1")] == [CASCADE_BLOCKED]


# ── the FE union is the only gate ──


def test_the_FE_union_REGISTERS_every_projection_event():
    """EventSource SILENTLY DROPS an event type nobody listened for, and `_publish` has no
    server-side allowlist — so this array is the only thing between an emitted event and
    a frontend that never sees it. A missing member produces no error anywhere."""
    source = pathlib.Path("web/src/pages/workflows/useWorkflowStream.ts").read_text(
        encoding="utf-8"
    )
    for kind in PROJECTION_KINDS:
        assert f"'workflow_{kind}'" in source, f"workflow_{kind} is emitted but never registered"


def test_the_publish_seam_has_no_server_side_allowlist():
    """Pinning the measured fact that makes the test above load-bearing. If an allowlist is ever
    added, THIS test should fail and the FE-union test becomes belt-and-braces rather than the only
    guard."""
    import inspect

    from personalclaw.workflows.controller import RunController

    source = inspect.getsource(RunController._publish)
    assert "ALLOWED" not in source and "allowlist" not in source.lower()
