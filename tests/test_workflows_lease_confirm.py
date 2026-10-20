"""The lease WRITE path and the confirmation resolve endpoint.

The lease DECISION rules were built as pure functions, and the confirmation verbs separately.
Neither had durability or a caller. This write path and endpoint add both, and the interesting
properties were measured rather than assumed:

**The lease survives real process contention.** Eight separate PROCESSES racing one task through
`claim_task`, 12 trials: 0 multi-winner. That is the property the whole mechanism exists
for — the read-then-`unlink` version of a related primitive was measured letting multiple
callers through in 36 of 40 races, and a lease that loses a race is worse than no lease at
all, because both holders believe they own the work. The in-process test below pins the same
rule; the cross-process run was a one-off measurement.

**A confirmation resolve must not pass the VERB as the gate's answer.** The engine's gate resolution
reads an approval boolean, so handing it `"reject"` would make a rejection truthy — the single worst
mistranslation available in this path. `resolve_confirmation` converts to `resolution.approved`.
"""

import asyncio
import contextlib

import pytest

from personalclaw.approval_answer import YOU
from personalclaw.workflows import pool, service
from personalclaw.workflows.pool import DEFAULT_LEASE_SECS, LeaseError

NOW = 1_700_000_000.0


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Every test writes lease files. They go under a tmp home, never the real one.

    Patches `config_dir` where `pool.leases_dir` imports it AND `store.config_dir`, because the
    workflow store binds `config_dir` at module level — measured once: patching
    only `config.loader.config_dir` left `store.save()` writing to the REAL
    `~/.personalclaw/workflows/runs.db`.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    from personalclaw.workflows import store as wstore

    monkeypatch.setattr(wstore, "config_dir", lambda: tmp_path)
    yield
    assert ".personalclaw" not in str(tmp_path)


# ── the lease write path ──


def test_a_claim_PERSISTS():
    lease, error = pool.claim_task("t-1", holder="session-a", now=NOW)
    assert error == ""
    assert lease.holder == "session-a"
    assert pool.read_lease("t-1").holder == "session-a"


def test_a_SECOND_holder_is_refused():
    """The property the mechanism exists for: without it, engine-projected tasks are
    double-executed by concurrent sessions and both holders believe they own the work."""
    pool.claim_task("t-1", holder="session-a", now=NOW)
    lease, error = pool.claim_task("t-1", holder="session-b", now=NOW + 10)
    assert lease is None
    assert error == LeaseError.HELD_BY_OTHER.value


def test_the_SAME_holder_renews():
    pool.claim_task("t-1", holder="session-a", now=NOW)
    lease, error = pool.claim_task("t-1", holder="session-a", now=NOW + 10)
    assert error == ""
    assert lease.renewals == 1
    assert pool.read_lease("t-1").renewals == 1


def test_an_EXPIRED_lease_is_taken_over_with_a_reset_count():
    """Carrying the dead holder's renewals forward would make a stuck task look actively worked."""
    pool.claim_task("t-1", holder="dead", now=NOW, ttl_seconds=60)
    lease, error = pool.claim_task("t-1", holder="live", now=NOW + 61)
    assert error == ""
    assert lease.holder == "live"
    assert lease.renewals == 0


def test_only_the_HOLDER_can_release():
    pool.claim_task("t-1", holder="session-a", now=NOW)
    released, error = pool.release_task("t-1", holder="session-b")
    assert released is False
    assert error == LeaseError.WRONG_HOLDER.value
    assert pool.read_lease("t-1") is not None


def test_a_release_DELETES_the_record():
    pool.claim_task("t-1", holder="session-a", now=NOW)
    released, error = pool.release_task("t-1", holder="session-a")
    assert released is True
    assert error == ""
    assert pool.read_lease("t-1") is None


def test_releasing_an_UNHELD_task_is_refused():
    released, error = pool.release_task("t-nothing", holder="a")
    assert released is False
    assert error == LeaseError.NOT_HELD.value


def test_reading_a_MISSING_lease_is_None_not_an_error():
    assert pool.read_lease("t-never") is None


def test_a_CORRUPT_lease_file_reads_as_UNCLAIMED():
    """Degrading to unclaimed risks a brief double-claim; degrading to claimed would strand the task
    permanently with no holder to release it — and the contention resolves while the strand does
    not."""
    path = pool._lease_path("t-bad")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert pool.read_lease("t-bad") is None


def test_a_lease_with_NO_HOLDER_reads_as_unclaimed():
    """An anonymous claim is indistinguishable from no claim."""
    path = pool._lease_path("t-anon")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"task_id": "t-anon", "holder": ""}')
    assert pool.read_lease("t-anon") is None


def test_a_TRAVERSAL_task_id_cannot_escape_the_directory():
    """A task id arrives from an HTTP path and a provider id is not a trust boundary."""
    pool.claim_task("../../evil", holder="a", now=NOW)
    names = {p.name for p in pool.leases_dir().iterdir()}
    assert names == {".._.._evil.json"}


def test_the_default_ttl_is_applied_when_unspecified():
    lease, _ = pool.claim_task("t-1", holder="a", now=NOW)
    assert lease.ttl_seconds == DEFAULT_LEASE_SECS


def test_only_ONE_holder_wins_a_sequential_race():
    """The in-process pin of the rule. Cross-process: 8 processes × 12 trials measured 0
    multi-winner (a one-off measurement, not part of this suite)."""
    winners = []
    for i in range(8):
        lease, error = pool.claim_task("t-race", holder=f"s-{i}", now=NOW)
        if error == "":
            winners.append(lease.holder)
    assert winners == ["s-0"], f"{len(winners)} sessions believed they owned one task"


# ── the sweep ──


def test_the_sweep_FREES_expired_leases():
    pool.claim_task("t-dead", holder="x", now=NOW - 99_999, ttl_seconds=60)
    assert pool.sweep_task_leases(NOW) == ["t-dead"]
    assert pool.read_lease("t-dead") is None


def test_the_sweep_LEAVES_live_leases_alone():
    pool.claim_task("t-live", holder="x", now=NOW)
    assert pool.sweep_task_leases(NOW) == []
    assert pool.read_lease("t-live") is not None


def test_the_sweep_removes_UNPARSEABLE_files():
    """They already read as "no lease" to every reader, so removing them is cleanup rather than a
    decision — and leaving them means the directory grows forever."""
    path = pool._lease_path("t-bad")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("garbage")
    pool.sweep_task_leases(NOW)
    assert not path.exists()


def test_the_sweep_on_a_MISSING_directory_is_empty():
    assert pool.sweep_task_leases(NOW) == []


def test_the_lease_dir_is_under_the_CONFIG_dir(tmp_path):
    """A module-level absolute path would write into the real home from a test — a failure
    already paid for once."""
    assert str(pool.leases_dir()).startswith(str(tmp_path))


# ── the confirmation resolve ──


def test_an_UNKNOWN_verb_is_REFUSED_not_treated_as_a_reject():
    """A typo silently declining an approval would reject work the user meant to allow, and they
    would have no way to know why."""
    result = service.resolve_confirmation("r-1", verb="yolo", by=YOU)
    assert result["ok"] is False
    assert result["code"] == "WF_CONFIRM_VERB_INVALID"


def test_an_EMPTY_verb_is_refused():
    assert service.resolve_confirmation("r-1", verb="", by=YOU)["ok"] is False


def _a_run() -> str:
    """A real run in the store, because EVERY confirm verb now requires one.

    These tests used a bare `"r-1"` that was never stored, which worked only while skip/quit
    answered without consulting the run — see the reversal recorded in
    `test_EVERY_verb_requires_the_run_to_EXIST` below.
    """
    from personalclaw.workflows import store as wstore
    from personalclaw.workflows.models import WorkflowRun

    return wstore.create(WorkflowRun(id="", workflow_name="w")).id


@pytest.mark.parametrize("verb", ["skip", "quit"])
def test_SKIP_and_QUIT_resolve_nothing_and_consume_no_token(verb):
    """They are decisions ABOUT the queue, not answers to the gate. Consuming the token would burn a
    single-use claim on a non-answer and strand the gate forever.

    That invariant is unchanged and is what this leg is for. What changed is that the run has to
    exist first — orthogonal to consuming nothing, and the two were previously conflated."""
    result = service.resolve_confirmation(_a_run(), verb=verb, by=YOU)
    assert result["ok"] is True
    assert result["resumed"] is False
    assert "code" not in result


def test_SKIP_reports_still_pending():
    """Different from rejecting it: without skip a user has to answer in the order the engine
    happened to ask."""
    assert service.resolve_confirmation(_a_run(), verb="skip", by=YOU)["still_pending"] is True


def test_QUIT_is_not_still_pending():
    assert service.resolve_confirmation(_a_run(), verb="quit", by=YOU)["still_pending"] is False


@pytest.mark.parametrize("verb", ["approve", "reject", "skip", "quit"])
def test_EVERY_verb_requires_the_run_to_EXIST(verb):
    """🔴 A DELIBERATE REVERSAL of what this file used to assert, recorded rather than slipped in.

    It previously read: "It never reached the run, so a nonexistent run is not an error for these
    verbs" — skip/quit answered 200 `{"resumed": false, "still_pending": true}` for any id at all,
    while approve/reject 404'd through `resume_run`.

    The reasoning that changed it (issue 765): `still_pending: true` is a claim about a GATE, and a
    gate belongs to a run. There is no run-independent queue here — the function is keyed by
    `run_id` — so for an id that names nothing, the 200 described something that cannot exist, and a
    tool firing skip at a typo'd or already-deleted run was told it had worked. Eight sibling
    control verbs already answered 404 on the same id.

    What the old design was protecting is preserved exactly: skip/quit still consume no token and
    still resolve nothing (the leg above). "Touches the run" was conflating "reads it to check it is
    there" with "mutates its gate", and only the second was ever the invariant.
    """
    result = service.resolve_confirmation("r-missing", verb=verb, by=YOU)
    assert result["ok"] is False
    assert result["code"] == "WF_RUN_NOT_FOUND"


def test_the_gate_answer_is_the_APPROVAL_BOOLEAN_not_the_verb(monkeypatch):
    """The worst available mistranslation here: the engine's gate resolution reads a
    boolean, so passing the verb string would make `reject` truthy and approve what the
    user declined."""
    seen: dict = {}

    def fake_resume(run_id, **kw):
        seen.update(kw)
        return {"ok": True, "run_id": run_id}

    monkeypatch.setattr(service, "resume_run", fake_resume)
    service.resolve_confirmation(_a_run(), verb="reject", by=YOU)
    assert seen["answer"] is False
    service.resolve_confirmation(_a_run(), verb="approve", by=YOU)
    assert seen["answer"] is True


def test_the_result_names_the_verb_and_the_decision(monkeypatch):
    monkeypatch.setattr(service, "resume_run", lambda run_id, **kw: {"ok": True})
    result = service.resolve_confirmation(_a_run(), verb="approve", note="checked the diff", by=YOU)
    assert result["verb"] == "approve"
    assert result["approved"] is True


def test_a_resume_TOKEN_is_passed_through(monkeypatch):
    """A run with several pending gates requires a token — approving the wrong gate is worse than
    asking, and `resume_run` already owns that rule."""
    seen: dict = {}
    monkeypatch.setattr(
        service, "resume_run", lambda run_id, **kw: (seen.update(kw), {"ok": True})[1]
    )
    service.resolve_confirmation(_a_run(), verb="approve", token="tok-1", by=YOU)
    assert seen["token"] == "tok-1"


def test_resolving_rides_the_ONE_resume_path(monkeypatch):
    """There is one place a resume token is consumed, because the claim primitive lives with the
    token. A second resolve path would be a second chance to double-approve."""
    called = {"n": 0}

    def fake_resume(run_id, **kw):
        called["n"] += 1
        return {"ok": True}

    monkeypatch.setattr(service, "resume_run", fake_resume)
    service.resolve_confirmation(_a_run(), verb="approve", by=YOU)
    assert called["n"] == 1


# ── the route ──


def test_the_confirm_route_is_MOUNTED():
    from aiohttp import web

    from personalclaw.workflows.handlers import register_workflow_routes

    app = web.Application()
    register_workflow_routes(app)
    paths = {r.resource.canonical for r in app.router.routes() if r.resource}
    assert "/api/workflows/runs/{run_id}/confirm" in paths


#: A run that parks on one approval gate and, once it is approved, carries on to a last step.
APPROVAL = {
    "name": "approval-gated",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [
            {"kind": "gate", "id": "approve", "config": {"kind": "approval", "prompt": "ok?"}},
            {"kind": "transform", "id": "after", "config": {"expr": "the run carried on"}},
        ],
    },
}

#: The Temporary chat live in the gateway, and the ordinary one, by the names their keys carry.
TEMPORARY_CHAT = "tmp-chat"
ORDINARY_CHAT = "c1"

#: What an agent's tool presents: the gateway's internal secret, for the chat it acts for.
AGENT_TOOL = {"X-Internal-Secret": "s", "X-Session-Key": f"dashboard:{ORDINARY_CHAT}"}


async def _parked(watchdog, *, chat: str = "") -> str:
    """A run driven to a real approval gate through the real supervisor; its id. *chat* is the
    Temporary chat that started it, whose mode the run keeps (none: a run you started)."""
    from personalclaw.workflows import ownership
    from personalclaw.workflows import store as wstore
    from personalclaw.workflows.models import RunOrigin, RunStatus, WorkflowRun

    run = WorkflowRun(id="", workflow_name=APPROVAL["name"])
    if chat:
        run.origin = RunOrigin(session_key=f"dashboard:{chat}")
        run.extra = ownership.stamp_run_mode({}, ownership.MemoryMode.TEMPORARY)
    run = wstore.create(run)
    wstore.write_spec(run.id, APPROVAL)
    controller = await watchdog.launch(run, APPROVAL)
    assert await controller.wait_for_terminal(timeout=15) == RunStatus.NEEDS_INPUT
    return run.id


def _still_waits(run_id: str) -> bool:
    """Whether the run still waits on its one gate, unanswered."""
    from personalclaw.workflows import store as wstore
    from personalclaw.workflows.human_input import list_continuations
    from personalclaw.workflows.models import RunStatus

    return len(list_continuations(run_id)) == 1 and wstore.get(run_id).status == (
        RunStatus.NEEDS_INPUT
    )


async def _carried_on(watchdog, run_id: str) -> bool:
    """Whether the answered run went on past its gate to its last step, and ended there."""
    from personalclaw.workflows import store as wstore
    from personalclaw.workflows.models import RunStatus

    ended = await watchdog.controller(run_id).wait_for_terminal(timeout=15)
    return ended == RunStatus.COMPLETE and (
        wstore.read_output(run_id, "root.children[1]") == "the run carried on"
    )


@contextlib.asynccontextmanager
async def _gateway(tmp_path):
    """The gateway's workflow routes over its dashboard state, with the real supervisor in it and
    two live chats, a Temporary one and an ordinary one. Requests are admitted as you, as
    `auth_mode=none` admits a loopback caller; the chat a request is for is the one it names in
    `X-Session-Key`. Yields `(client, watchdog)`."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from chat_test_helpers import _make_state

    from personalclaw.workflows.controller import EngineServices
    from personalclaw.workflows.handlers import register_workflow_routes
    from personalclaw.workflows.watchdog import WorkflowWatchdog

    watchdog = WorkflowWatchdog(None, EngineServices())
    state = _make_state(tmp_path)
    state.workflows = watchdog
    state.get_or_create_session(TEMPORARY_CHAT, memory_mode="temporary")
    state.get_or_create_session(ORDINARY_CHAT)

    @web.middleware
    async def signed_in(request, handler):
        request["user"] = "owner"
        return await handler(request)

    app = web.Application(middlewares=[signed_in])
    app["state"] = state
    register_workflow_routes(app)
    async with TestClient(TestServer(app)) as client:
        yield client, watchdog


async def _post(client, run_id: str, verb: str, body: dict, headers: dict | None = None):
    """POST *body* to the run's `/{verb}` route: `(status, answer)`."""
    resp = await client.post(f"/api/workflows/runs/{run_id}/{verb}", json=body, headers=headers)
    return resp.status, await resp.json()


def _rows(operation: str) -> list[tuple[str, str, str]]:
    """The security log's rows for *operation*, oldest first: `(caller, outcome, resources)`."""
    from personalclaw.sel import sel

    return [
        (row["caller_identity"], row["outcome"], row["resources"])
        for row in reversed(sel().recent(200))
        if row.get("operation") == operation
    ]


@pytest.mark.asyncio
async def test_a_temporary_chat_confirms_only_what_it_may_resume(tmp_path):
    """This IS a resume with a verb vocabulary on top, so it is guarded by the same operation as
    resume (`workflow_run_resume`). A separate permission would let a caller who may not answer a
    gate answer it through the other door.

    The guard also names the run, so a Temporary chat answers a run of its own and no other: on
    a run you started, confirm refuses it exactly as resume does, writes the same refusal row
    (`workflow_run_resume`, naming the run), and leaves the gate waiting; on the run the chat
    started, its approve is taken and the run carries on."""
    chat = {"X-Session-Key": f"dashboard:{TEMPORARY_CHAT}"}
    async with _gateway(tmp_path) as (client, watchdog):
        yours = await _parked(watchdog)
        its_own = await _parked(watchdog, chat=TEMPORARY_CHAT)

        resumed = await _post(client, yours, "resume", {"answer": True}, chat)
        confirmed = await _post(client, yours, "confirm", {"verb": "approve"}, chat)
        assert resumed[0] == confirmed[0] == 403, (resumed, confirmed)
        assert confirmed[1]["error"]["code"] == "restricted_session", confirmed
        assert confirmed[1] == resumed[1], "the two doors refused it differently"
        assert _still_waits(yours), "a Temporary chat answered a run you started"
        refused = [row for row in _rows("workflow_run_resume") if row[1] == "denied"]
        assert refused == [(f"dashboard:{TEMPORARY_CHAT}", "denied", yours)] * 2

        status, answer = await _post(client, its_own, "confirm", {"verb": "approve"}, chat)
        assert status == 200, answer
        assert answer["approved"] is True
        assert await _carried_on(watchdog, its_own)


@pytest.mark.asyncio
async def test_a_confirm_is_audited_with_who_sent_it_and_the_verb(tmp_path):
    """ "Who approved this, and what did they say" is the question an audit exists to answer.
    Each confirm writes one row naming the work it was sent for (your own pages:
    `dashboard:ui`), the run and the verb as it was sent, and whether it applied: a typo'd verb
    is a failure, a skip and an approve a success."""
    async with _gateway(tmp_path) as (client, watchdog):
        run_id = await _parked(watchdog)

        assert (await _post(client, run_id, "confirm", {"verb": "yolo"}))[0] == 400
        assert (await _post(client, run_id, "confirm", {"verb": "skip"}))[0] == 200
        assert _still_waits(run_id)
        from_a_chat = {"X-Session-Key": f"dashboard:{ORDINARY_CHAT}"}
        status, answer = await _post(client, run_id, "confirm", {"verb": "approve"}, from_a_chat)
        assert status == 200, answer
        assert await _carried_on(watchdog, run_id)

    assert _rows("workflow_run_confirm") == [
        ("dashboard:ui", "failure", f"{run_id}:yolo"),
        ("dashboard:ui", "success", f"{run_id}:skip"),
        (f"dashboard:{ORDINARY_CHAT}", "success", f"{run_id}:approve"),
    ]


@pytest.mark.asyncio
async def test_a_channel_in_the_body_changes_nothing_about_who_answers(tmp_path):
    """`channel` marks a REMOTE reply the engine owner-binds. Passing one through from an untrusted
    body would let a caller claim to be a channel and get the remote path's different rules — the
    reason `api_run_resume` does not forward it either.

    So the answer is judged by what the request proved, whatever channel its body names: an
    agent's tool that names one is still refused as an agent's tool, and the gate waits; your own
    approve that names one is still your local answer, taken on a run with no recorded owner (a
    remote reply to such a run is refused), and the run carries on."""
    remote = {"verb": "approve", "channel": "telegram"}
    async with _gateway(tmp_path) as (client, watchdog):
        run_id = await _parked(watchdog)

        status, answer = await _post(client, run_id, "confirm", remote, AGENT_TOOL)
        assert status == 403, answer
        assert answer["error"]["code"] == "approval_owner_only", answer
        assert _still_waits(run_id), "an agent's tool answered by naming a channel"

        status, answer = await _post(client, run_id, "confirm", remote)
        assert status == 200, answer
        assert answer["approved"] is True
        assert await _carried_on(watchdog, run_id)


def test_asyncio_free_service_call_is_synchronous():
    """`resolve_confirmation` is sync like `resume_run`: the controller call it delegates to is, and
    an async wrapper would invite a caller to await something that never yields."""
    assert not asyncio.iscoroutinefunction(service.resolve_confirmation)
