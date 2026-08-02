"""Tests for the task-pool concurrency semantics (TASKS-SOPS §5 R10, R7, R16 — S60).

Two things were MEASURED before this module asserted anything.

**`TaskComplete` is a declared hook event that nothing fires.** `hooks.HOOK_EVENTS` contains it,
`validation.py` allowlists it, the hook UI renders it with `_LIFECYCLE_BASE_VARS` — and a repo-wide
search for `fire(` finds exactly one call site (`fire_tool_hooks`). So a user could configure a
"when a task finishes" hook that would never run. A test below pins the *fireability* of the payload
this module builds, and the event-name constant is asserted equal to the shipped one so a rename
cannot silently create a second vocabulary.

**Acyclicity is already server-authoritative.** `tasks/native.py` calls
`reconcile.would_create_cycle` on BOTH create (line ~245) and update (line ~311). The plan's "the
server-side write path adds the authoritative check" was already satisfied, so `plan_edges`
delegates to that function instead of shipping a second DFS — and a test asserts the delegation, not
a reimplementation, because two cycle checkers means the looser one lets a deadlock through.
"""

import pytest

from personalclaw.workflows.pool import (
    DEFAULT_LEASE_SECS,
    HOOK_EVENT_TASK_COMPLETE,
    MAX_LEASE_SECS,
    PRIORITY_WEIGHT,
    SEED_HANDOFFS,
    Blueprint,
    Candidate,
    HandOff,
    Hydration,
    Lease,
    LeaseError,
    SurfaceRoute,
    Transition,
    UnblockKind,
    Urgency,
    acquire,
    build_blueprint,
    carry_context,
    coalesce,
    explain,
    frontier,
    lifecycle_payload,
    next_task,
    plan_edges,
    plan_hydration,
    plan_unblock,
    release,
    renew,
    route,
    should_fire_completion,
    suggest_handoffs,
    sweep_expired,
)

NOW = 1_700_000_000.0


def _lease(**kw) -> Lease:
    base = dict(task_id="t-1", holder="session-a", acquired_at=NOW, ttl_seconds=600)
    base.update(kw)
    return Lease(**base)


# ── leases: the property that prevents double execution ──


def test_an_UNCLAIMED_task_can_be_acquired():
    lease, error = acquire(None, task_id="t-1", holder="session-a", now=NOW)
    assert error == ""
    assert lease.holder == "session-a"


def test_a_LIVE_lease_blocks_another_session():
    """Without this, engine-projected tasks WILL be double-executed by concurrent sessions — and
    both holders believe they own the work."""
    lease, error = acquire(_lease(), task_id="t-1", holder="session-b", now=NOW + 10)
    assert lease is None
    assert error == LeaseError.HELD_BY_OTHER.value


def test_an_EXPIRED_lease_is_takeable():
    """A crashed holder must not park a task forever; the TTL is what returns it to the pool."""
    lease, error = acquire(_lease(), task_id="t-1", holder="session-b", now=NOW + 601)
    assert error == ""
    assert lease.holder == "session-b"


def test_a_takeover_RESETS_the_renewal_count():
    """Carrying a dead holder's renewals forward would make a stuck task look actively worked."""
    stale = _lease(renewals=7)
    lease, _ = acquire(stale, task_id="t-1", holder="session-b", now=NOW + 601)
    assert lease.renewals == 0


def test_the_SAME_holder_re_acquiring_is_a_renewal_not_an_error():
    """A session that lost its in-memory lease after a restart would otherwise be locked out of its
    own task until the TTL ran down."""
    lease, error = acquire(_lease(), task_id="t-1", holder="session-a", now=NOW + 10)
    assert error == ""
    assert lease.renewals == 1


def test_an_EMPTY_holder_cannot_acquire():
    """An anonymous claim is indistinguishable from no claim, and two of them collide silently."""
    lease, error = acquire(None, task_id="t-1", holder="  ", now=NOW)
    assert lease is None
    assert error == LeaseError.NO_HOLDER_ID.value


def test_renewing_EXTENDS_the_deadline():
    lease, _ = renew(_lease(), holder="session-a", now=NOW + 300)
    assert lease.expires_at() > _lease().expires_at()


def test_a_NON_HOLDER_cannot_renew():
    lease, error = renew(_lease(), holder="session-b", now=NOW + 10)
    assert lease is None
    assert error == LeaseError.WRONG_HOLDER.value


def test_an_EXPIRED_lease_cannot_be_renewed():
    """Between expiry and renewal another session may already have taken the task; silently
    extending would produce two holders who both think they won."""
    lease, error = renew(_lease(), holder="session-a", now=NOW + 601)
    assert lease is None
    assert error == LeaseError.NOT_HELD.value


def test_only_the_HOLDER_may_release():
    """Allowing anyone to release would let one session drop another's live claim by racing the
    expiry boundary."""
    _none, error = release(_lease(), holder="session-b")
    assert error == LeaseError.WRONG_HOLDER.value


def test_the_holder_can_release():
    _none, error = release(_lease(), holder="session-a")
    assert error == ""


def test_releasing_an_unheld_task_is_refused_not_silent():
    _none, error = release(None, holder="session-a")
    assert error == LeaseError.NOT_HELD.value


def test_the_TTL_is_CAPPED_at_the_ceiling():
    """A caller asking for a week would park a task for a week; the ceiling is what makes a crashed
    holder recoverable the same day."""
    lease = _lease(ttl_seconds=10 * MAX_LEASE_SECS)
    assert lease.expires_at() == NOW + MAX_LEASE_SECS


def test_the_default_ttl_is_well_under_the_ceiling():
    """A holder that needs longer renews, which proves it is alive; a long initial TTL just delays
    discovering that it is not."""
    assert DEFAULT_LEASE_SECS < MAX_LEASE_SECS


def test_a_lease_round_trips():
    original = _lease(renewals=2)
    assert Lease.from_dict(original.to_dict()) == original


def test_the_serialized_lease_carries_the_DERIVED_deadline():
    """A board deciding whether a claim is stale should not have to re-derive the rule."""
    assert _lease().to_dict()["expires_at"] == NOW + 600


def test_the_sweep_returns_only_EXPIRED_ids():
    leases = [_lease(task_id="live"), _lease(task_id="dead", acquired_at=NOW - 10_000)]
    assert sweep_expired(leases, NOW) == ["dead"]


# ── leases under real contention ──


def test_only_ONE_of_many_concurrent_acquires_can_win():
    """The property, measured rather than asserted: given one lease state, N callers racing to claim
    must yield exactly one winner. (The write path is a flocked read-modify-write; this pins the
    DECISION rule that path implements.)"""
    state: Lease | None = None
    winners = []
    for holder in [f"s-{i}" for i in range(8)]:
        lease, error = acquire(state, task_id="t-1", holder=holder, now=NOW)
        if error == "":
            state = lease
            winners.append(holder)
    assert winners == ["s-0"], f"{len(winners)} sessions believed they owned one task"


# ── projections ──


def _cand(**kw) -> Candidate:
    base = dict(task_id="t", title="", priority="medium", unblocked=True)
    base.update(kw)
    return Candidate(**base)


def test_the_frontier_EXCLUDES_blocked_tasks():
    pool = [_cand(task_id="a"), _cand(task_id="b", unblocked=False)]
    assert [c.task_id for c in frontier(pool)] == ["a"]


def test_the_frontier_EXCLUDES_leased_tasks_by_default():
    """A frontier listing work another session actively holds invites exactly the double-execution
    the leases prevent."""
    pool = [_cand(task_id="a"), _cand(task_id="b", leased_by="session-x")]
    assert [c.task_id for c in frontier(pool)] == ["a"]


def test_the_board_can_ASK_for_leased_tasks():
    """The board shows claims rather than picking work, so it needs the other view."""
    pool = [_cand(task_id="b", leased_by="session-x")]
    assert len(frontier(pool, include_leased=True)) == 1


def test_higher_priority_ranks_first():
    pool = [_cand(task_id="low", priority="low"), _cand(task_id="critical", priority="critical")]
    assert frontier(pool)[0].task_id == "critical"


def test_a_task_BLOCKING_others_outranks_an_equal_that_blocks_nothing():
    """The whole point of a dependency-aware pool: a medium task blocking four others is worth more
    than a medium task blocking none."""
    pool = [_cand(task_id="alone"), _cand(task_id="blocker", blocks_count=4)]
    assert frontier(pool)[0].task_id == "blocker"


def test_OVERDUE_beats_priority():
    pool = [_cand(task_id="high", priority="high"), _cand(task_id="late", overdue=True)]
    assert frontier(pool)[0].task_id == "late"


def test_the_ORDER_IS_STABLE_for_equals():
    """An unstable "next task" makes an agent thrash between two equals."""
    pool = [_cand(task_id="b"), _cand(task_id="a")]
    assert [c.task_id for c in frontier(pool)] == ["a", "b"]
    assert [c.task_id for c in frontier(list(reversed(pool)))] == ["a", "b"]


def test_next_is_the_frontier_HEAD_by_construction():
    """One function, one answer — this is what stops "what should I work on" from being
    reimplemented per surface, so the list and the pick cannot disagree."""
    pool = [_cand(task_id="a"), _cand(task_id="z", priority="critical")]
    assert next_task(pool) is frontier(pool)[0]


def test_next_on_an_EMPTY_pool_is_None():
    assert next_task([]) is None


def test_next_on_an_all_blocked_pool_is_None():
    assert next_task([_cand(unblocked=False)]) is None


def test_the_priority_vocabulary_matches_the_TASK_model():
    """Two priority scales would disagree about a task, and the looser one would win."""
    from personalclaw.tasks.models import TaskPriority

    assert set(PRIORITY_WEIGHT) == {p.value for p in TaskPriority}


def test_urgency_is_REPORTED_not_just_used_for_sorting():
    """A ranked list whose order cannot be explained is one a user overrides — and then the
    projection is decoration."""
    assert _cand(overdue=True).urgency() is Urgency.OVERDUE
    assert _cand(blocks_count=2).urgency() is Urgency.BLOCKING_OTHERS
    assert _cand(priority="high").urgency() is Urgency.HIGH_PRIORITY
    assert _cand().urgency() is Urgency.NORMAL


def test_the_explanation_names_the_reasons():
    line = explain(_cand(task_id="t-9", priority="high", blocks_count=3, overdue=True))
    assert "t-9" in line and "overdue" in line and "blocks 3" in line


# ── evented unblock ──


def test_a_COMPLETED_blocker_unblocks_its_dependent():
    """Before this, only workflow-bound tasks got unblocked; a standalone dependent sat
    misleadingly `open` after its prerequisite finished."""
    out = plan_unblock(blocker_id="a", blocker_status="done", dependents={"b": ["a"]})
    assert out == [Transition(task_id="b", kind=UnblockKind.UNBLOCK)]


def test_one_of_TWO_prerequisites_does_NOT_unblock():
    """The bug a naive "completion unblocks dependents" rule ships with: work becomes visible
    before its other prerequisite is done."""
    out = plan_unblock(
        blocker_id="a",
        blocker_status="done",
        dependents={"c": ["a", "b"]},
        statuses={"b": "open"},
    )
    assert out[0].kind is UnblockKind.NONE
    assert "waiting on b" in out[0].reason


def test_the_LAST_prerequisite_completing_unblocks():
    out = plan_unblock(
        blocker_id="a",
        blocker_status="done",
        dependents={"c": ["a", "b"]},
        statuses={"b": "done"},
    )
    assert out[0].kind is UnblockKind.UNBLOCK


def test_a_FAILED_blocker_CASCADES_with_its_reason():
    """The dependent's board card should say WHY, not just that something upstream broke."""
    out = plan_unblock(
        blocker_id="a",
        blocker_status="failed",
        blocker_reason="deploy binary missing",
        dependents={"b": ["a"]},
    )
    assert out[0].kind is UnblockKind.CASCADE_FAILED
    assert out[0].blocked_kind == "dependency_failed"
    assert "deploy binary missing" in out[0].reason


def test_a_CANCELLED_blocker_also_cascades():
    out = plan_unblock(blocker_id="a", blocker_status="cancelled", dependents={"b": ["a"]})
    assert out[0].kind is UnblockKind.CASCADE_FAILED


def test_a_cascade_does_NOT_wait_for_sibling_prerequisites():
    """An unrecoverable upstream failure blocks the dependent regardless of its other edges —
    waiting would leave it `open` and workable when it is not."""
    out = plan_unblock(
        blocker_id="a",
        blocker_status="failed",
        dependents={"c": ["a", "b"]},
        statuses={"b": "open"},
    )
    assert out[0].kind is UnblockKind.CASCADE_FAILED


def test_an_UNRELATED_task_is_untouched():
    out = plan_unblock(blocker_id="a", blocker_status="done", dependents={"z": ["other"]})
    assert out == []


def test_a_NON_TERMINAL_status_changes_nothing():
    out = plan_unblock(blocker_id="a", blocker_status="in_progress", dependents={"b": ["a"]})
    assert out == []


def test_a_cascade_BURST_coalesces_into_one_notification():
    """N alerts for one upstream failure is the noise that makes a user mute the channel."""
    out = plan_unblock(
        blocker_id="a",
        blocker_status="failed",
        blocker_reason="boom",
        dependents={"b": ["a"], "c": ["a"], "d": ["a"]},
    )
    transitions, summary = coalesce(out)
    assert len(transitions) == 3
    assert summary.startswith("3 tasks blocked by one upstream failure")


def test_a_SINGLE_cascade_keeps_its_specific_reason():
    out = plan_unblock(
        blocker_id="a", blocker_status="failed", blocker_reason="boom", dependents={"b": ["a"]}
    )
    _t, summary = coalesce(out)
    assert "boom" in summary


def test_pure_unblocks_produce_NO_notification():
    out = plan_unblock(blocker_id="a", blocker_status="done", dependents={"b": ["a"]})
    assert coalesce(out)[1] == ""


# ── task lifecycle events ──


def test_the_event_name_is_the_SHIPPED_one():
    """Measured: `TaskComplete` already exists in `hooks.HOOK_EVENTS` and `validation.py`'s
    allowlist, and nothing fires it. A second name here would be a vocabulary the hook UI does not
    render, so a user could never configure against it."""
    from personalclaw.hooks import HOOK_EVENTS

    assert HOOK_EVENT_TASK_COMPLETE == "TaskComplete"
    assert HOOK_EVENT_TASK_COMPLETE in HOOK_EVENTS


def test_the_event_is_ALLOWLISTED_for_hooks():
    from personalclaw.validation import ALLOWED_HOOK_EVENTS

    assert HOOK_EVENT_TASK_COMPLETE in ALLOWED_HOOK_EVENTS


def test_the_payload_matches_the_fire_SIGNATURE():
    """Uses `fire(event, context=...)`'s existing shape rather than adding hook variables: the UI
    renders a fixed `vars` tuple per event, so a new variable is one no user can discover."""
    import inspect

    from personalclaw.hooks import ScriptHookStore

    params = inspect.signature(ScriptHookStore.fire).parameters
    payload = lifecycle_payload(task_id="t-1", title="Ship it", status="done")
    assert set(payload) <= set(params)


def test_the_payload_carries_workflow_PROVENANCE():
    payload = lifecycle_payload(
        task_id="t-1", title="x", status="done", run_id="r-1", node_id="deploy"
    )
    assert "run=r-1" in payload["context"]
    assert "node=deploy" in payload["context"]


def test_a_long_title_is_BOUNDED_in_the_context():
    payload = lifecycle_payload(task_id="t", title="x" * 500, status="done")
    assert len(payload["context"]) < 300


def test_completion_is_EDGE_triggered():
    """An idempotent projection recompute is the NORMAL path per §1, so a level-triggered fire
    would emit a hook per rebuild."""
    assert should_fire_completion("in_progress", "done") is True
    assert should_fire_completion("done", "done") is False


def test_a_non_completion_transition_does_not_fire():
    assert should_fire_completion("open", "in_progress") is False


def test_reopening_a_task_does_not_fire():
    assert should_fire_completion("done", "open") is False


# ── write-time acyclicity, delegated ──


def test_plan_edges_DELEGATES_to_the_shipped_checker():
    """Measured: `tasks/native.py` already calls `reconcile.would_create_cycle` on create AND
    update. A second DFS here would be a second answer, and the looser one lets a deadlock through
    (AionUI's shipped A-blocks-B/B-blocks-A bug)."""
    calls = {}
    from personalclaw.tasks import reconcile

    real = reconcile.would_create_cycle

    def spy(tasks, task_id, new_prereq_ids):
        calls["args"] = (task_id, list(new_prereq_ids))
        return real(tasks, task_id, new_prereq_ids)

    reconcile.would_create_cycle = spy  # type: ignore[assignment]
    try:
        plan_edges({}, task_id="a", new_prereq_ids=["b"])
    finally:
        reconcile.would_create_cycle = real  # type: ignore[assignment]
    assert calls["args"] == ("a", ["b"])


def test_a_SAFE_edge_set_reports_no_cycle():
    cycle, error = plan_edges({}, task_id="a", new_prereq_ids=[])
    assert cycle == []
    assert error == ""


def test_a_MISSING_checker_does_not_read_as_safe(monkeypatch):
    """Fails closed: "cycle check unavailable" must not be indistinguishable from "no cycle", or a
    broken import silently disables the deadlock guard."""
    import builtins

    real_import = builtins.__import__

    def blocked(name, *args, **kw):
        if name == "personalclaw.tasks":
            raise ImportError("nope")
        return real_import(name, *args, **kw)

    monkeypatch.setattr(builtins, "__import__", blocked)
    cycle, error = plan_edges({}, task_id="a", new_prereq_ids=["b"])
    assert error.startswith("cycle check unavailable")


# ── hand-off edges (R7) ──


def test_a_completing_def_SUGGESTS_its_declared_successor():
    """Without declared edges, "now run the bugfix SOP" is something a user has to remember — and
    what a user remembers is not a procedure."""
    edges = suggest_handoffs("incident-response")
    assert [e.target_def for e in edges] == ["bug-fix"]


def test_review_to_fix_requires_an_EXPLICIT_user_request():
    """The plan calls this out: a review that auto-proposes fixing what it just criticized reads as
    the system arguing with itself."""
    assert suggest_handoffs("code-review") == []
    assert suggest_handoffs("code-review", user_requested=True)[0].target_def == "bug-fix"


def test_a_def_with_no_edges_suggests_nothing():
    assert suggest_handoffs("unknown-def") == []


def test_the_seed_edges_are_the_ones_the_plan_names():
    assert set(SEED_HANDOFFS) == {"incident-response", "bug-fix", "code-review"}


def test_a_handoff_carries_ONLY_declared_fields():
    """An allowlist rather than the whole outcome: passing everything would carry a previous run's
    credentials and artifacts into a new run's inputs, and a hand-off is exactly the seam where
    nobody would look for that."""
    edge = HandOff(target_def="bug-fix", context_fields=["incident_id"])
    carried = carry_context(edge, {"incident_id": "i-1", "api_key": "sk-live-secret"})
    assert carried == {"incident_id": "i-1"}


def test_a_missing_declared_field_is_simply_absent():
    edge = HandOff(target_def="x", context_fields=["gap"])
    assert carry_context(edge, {}) == {}


def test_a_handoff_round_trips_to_dict():
    payload = HandOff(target_def="x", condition="c", context_fields=["f"]).to_dict()
    assert payload["target_def"] == "x"
    assert payload["context_fields"] == ["f"]


# ── blueprint sessions (R16) ──


def test_a_blueprint_numbers_its_steps():
    """The checklist has to be readable as a checklist; unnumbered prose is what the passive digest
    already does."""
    bp = build_blueprint(def_name="backup", title="Backup", steps=["Snapshot", "Verify"])
    assert [m["text"] for m in bp.messages] == ["1. Snapshot", "2. Verify"]


def test_the_steps_are_ASSISTANT_messages():
    """System messages are invisible, and an invisible checklist is the same as no checklist."""
    bp = build_blueprint(def_name="d", title="t", steps=["a"])
    assert {m["role"] for m in bp.messages} == {"assistant"}


def test_the_digest_LEADS_so_the_user_reads_why_first():
    bp = build_blueprint(def_name="d", title="t", steps=["a"], digest="Why this matters")
    assert bp.messages[0]["text"] == "Why this matters"


def test_EMPTY_steps_are_dropped():
    bp = build_blueprint(def_name="d", title="t", steps=["a", "  ", ""])
    assert len(bp.messages) == 1


def test_a_blueprint_serializes_with_the_FE_key_name():
    """`openOnFirstLoad` is the plan's declared key; renaming it here would silently not open."""
    assert "openOnFirstLoad" in Blueprint(id="b", title="t").to_dict()


def test_hydration_is_REPLACE_not_merge():
    bp = build_blueprint(def_name="d", title="t", steps=["a", "b"])
    messages, record, replaced = plan_hydration(bp, session_id="s-1", now=NOW)
    assert replaced is True
    assert len(messages) == 2
    assert record.session_id == "s-1"


def test_RE_hydrating_the_same_blueprint_is_a_NO_OP():
    """The defensive case is a client that retries the open. A merge would duplicate every step, and
    duplicated instructions read as a system that has lost its place."""
    bp = build_blueprint(def_name="d", title="t", steps=["a"])
    _m, record, _r = plan_hydration(bp, session_id="s-1", now=NOW)
    messages, again, replaced = plan_hydration(bp, session_id="s-1", now=NOW + 5, existing=record)
    assert replaced is False
    assert messages == []
    assert again is record


def test_a_DIFFERENT_session_hydrates_fresh():
    bp = build_blueprint(def_name="d", title="t", steps=["a"])
    _m, record, _r = plan_hydration(bp, session_id="s-1", now=NOW)
    _m2, _r2, replaced = plan_hydration(bp, session_id="s-2", now=NOW, existing=record)
    assert replaced is True


def test_a_DIFFERENT_blueprint_hydrates_fresh():
    first = build_blueprint(def_name="a", title="t", steps=["x"])
    second = build_blueprint(def_name="b", title="t", steps=["y"])
    _m, record, _r = plan_hydration(first, session_id="s-1", now=NOW)
    _m2, _r2, replaced = plan_hydration(second, session_id="s-1", now=NOW, existing=record)
    assert replaced is True


def test_the_hydration_record_uses_the_declared_keys():
    payload = Hydration(template_id="bp-x", session_id="s", hydrated_at=NOW).to_dict()
    assert set(payload) == {"templateId", "sessionId", "hydratedAt"}


# ── mode routing ──


def test_a_GATED_def_is_a_RUN_not_a_blueprint():
    """A blueprint has no engine, so there is nothing to pause — rendering a gate as a numbered
    message would show the user an approval that approves nothing."""
    assert (
        route(surface_mode="passive", has_gates=True, max_turns=1, has_schema=False, guided=True)
        is SurfaceRoute.RUN
    )


def test_a_MULTI_TURN_def_is_a_run():
    assert (
        route(surface_mode="passive", has_gates=False, max_turns=3, has_schema=False)
        is SurfaceRoute.RUN
    )


def test_a_SCHEMA_bearing_def_is_a_run():
    assert (
        route(surface_mode="passive", has_gates=False, max_turns=1, has_schema=True)
        is SurfaceRoute.RUN
    )


def test_a_GUIDED_lightweight_def_is_a_BLUEPRINT():
    assert (
        route(surface_mode="passive", has_gates=False, max_turns=1, has_schema=False, guided=True)
        is SurfaceRoute.BLUEPRINT
    )


def test_an_ordinary_lightweight_def_stays_PASSIVE():
    assert (
        route(surface_mode="passive", has_gates=False, max_turns=1, has_schema=False)
        is SurfaceRoute.PASSIVE
    )


def test_an_OFF_def_never_routes_to_a_run_or_blueprint():
    """`off` means the def does not surface itself; routing it to a run would start work the user
    switched off."""
    assert (
        route(surface_mode="off", has_gates=True, max_turns=9, has_schema=True, guided=True)
        is SurfaceRoute.PASSIVE
    )


@pytest.mark.parametrize("mode", ["passive", "suggest"])
def test_routing_is_independent_of_which_surfacing_mode_is_on(mode):
    assert (
        route(surface_mode=mode, has_gates=True, max_turns=1, has_schema=False) is SurfaceRoute.RUN
    )
