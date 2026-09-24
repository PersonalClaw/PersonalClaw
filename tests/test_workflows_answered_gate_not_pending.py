"""An ANSWERED gate is not a pending gate (bugfix).

**The shipped defect.** `consume_continuation` claims a token by renaming the record aside, and
`list_continuations` globbed `*.json` — which matched the claimed file too. So an answered gate
kept reading as pending for the life of the run, and the user-visible consequence was that a
token-less *"approve it"* stopped working **permanently** the moment a run answered its first
gate: the next approval saw two "pending" gates and refused with `WF_AMBIGUOUS_GATE`.

Measured on `origin/main` before the fix, one live gate plus one answered gate:
`list_continuations` returned **2**, and for a run whose *only* gate was answered it returned
**1** — handing the token-less resolver an already-claimed token, so the honest
`WF_NO_PENDING_GATE` arrived as `WF_RESUME_UNKNOWN_TOKEN` instead.

**Why this is worth a module of its own: one glob answered SIX surfaces.** The token-less
resolver, the needs-input HTTP route, the blocking-mode response body, the introspection
open-asks projection, the controller's per-epoch idempotency check, and the rewind drop all
derive "what is still pending" from `list_continuations`. A claimed record leaking into it is
not one wrong number; it is an answered question re-rendered as an open one on every surface,
plus a rewind that could not reclaim the record at all.

**The shape, and why not the obvious one.** The obvious fix narrows the glob to exclude the
claim suffix — but that hard-codes a sibling's naming convention into every listing, and the
next reader to write its own `glob("*.json")` inherits the same blind spot. Instead the claim is
represented by **location**: the record moves into a `claimed/` subdirectory, which a
non-recursive `*.json` glob cannot match by construction. `os.rename` remains THE claim
primitive, same-filesystem and atomic, so the measured single-use property is untouched —
which is asserted here rather than argued (`test_ATOMICITY_survives_the_new_claim_location`).

Not chosen: `workflows/leases.py`'s shape (a field inside the file with an `expires_at`). A
lease is deliberately *reclaimable* on expiry and is set by a flock-guarded read-modify-write.
A consumed approval must never be reclaimable, and the read-modify-write is precisely the
read-then-unlink race that measured 36/40 double-winners before `os.rename` replaced it.
"""

from __future__ import annotations

import threading

import pytest

from personalclaw.workflows import human_input as HI
from personalclaw.workflows import service, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import (
    OriginKind,
    RunOrigin,
    RunStatus,
    WorkflowRun,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


def _spec(*node_ids: str) -> dict:
    """Approval gates that park indefinitely (`timeout_secs: 0`), then a step to carry on into."""
    return {
        "name": "answered-gate",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                *(
                    {
                        "kind": "gate",
                        "id": node_id,
                        "config": {
                            "kind": "approval",
                            "prompt": f"approve {node_id}?",
                            "timeout_secs": 0,
                        },
                    }
                    for node_id in node_ids
                ),
                {"kind": "transform", "id": "after", "config": {"expr": "done"}},
            ],
        },
    }


async def _parked(*node_ids: str):
    """A live run parked on its first gate, with the controller reachable as a supervisor would."""
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="answered-gate",
            mode="background",
            origin=RunOrigin(kind=OriginKind.CHAT, session_key="owner-1"),
        )
    )
    spec = _spec(*node_ids)
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices())
    status = await controller.run_to_completion(timeout=20)
    assert status == RunStatus.NEEDS_INPUT, f"the run did not park on a gate: {status}"
    return controller


class _Supervisor:
    """`service._live` only needs `.controller(run_id)`."""

    def __init__(self, controller) -> None:
        self._c = controller

    def controller(self, run_id: str):
        return self._c if run_id == self._c.run.id else None


# ── 1. the defect: an answered gate stops reading as pending ──────────────────


def test_an_ANSWERED_gate_is_not_listed_as_pending(tmp_path, monkeypatch):
    """THE reproduction. Before the fix this returned 2, which is the `WF_AMBIGUOUS_GATE`."""
    live = HI.create_continuation("r1", node_id="live", instance_path="p_live", epoch=1)
    answered = HI.create_continuation("r1", node_id="answered", instance_path="p_ans", epoch=1)
    assert HI.consume_continuation("r1", answered.token) is not None

    pending = HI.list_continuations("r1")
    assert [c.token for c in pending] == [live.token], (
        "an answered gate is still being reported as pending — the claimed record is being "
        "matched by the pending listing"
    )


def test_a_run_whose_ONLY_gate_was_answered_has_nothing_pending(tmp_path, monkeypatch):
    """The second branch the defect corrupted: this used to return the DEAD token, so the
    token-less resolver reported `WF_RESUME_UNKNOWN_TOKEN` instead of `WF_NO_PENDING_GATE`."""
    only = HI.create_continuation("r2", node_id="only", instance_path="p", epoch=1)
    HI.consume_continuation("r2", only.token)
    assert HI.list_continuations("r2") == []


# ── 2. the true positive is preserved: two LIVE gates still refuse ───────────


def test_TWO_LIVE_gates_are_still_reported_as_pending(tmp_path, monkeypatch):
    """The vacuity partner for the reproduction above.

    The false positive must not be fixed by deleting the true one: genuine ambiguity is still
    ambiguity. This stays green under the mutation that reds
    `test_an_ANSWERED_gate_is_not_listed_as_pending`, which is what proves that test is
    measuring the CLAIM and not just the count.
    """
    a = HI.create_continuation("r3", node_id="a", instance_path="p_a", epoch=1)
    b = HI.create_continuation("r3", node_id="b", instance_path="p_b", epoch=1)
    assert {c.token for c in HI.list_continuations("r3")} == {a.token, b.token}


# ── 3. atomicity survives the new claim location ─────────────────────────────


def test_ATOMICITY_survives_the_new_claim_location(tmp_path, monkeypatch):
    """Re-established by measurement, not by argument.

    The rename is load-bearing: read-then-unlink measured 36/40 trials with more than one
    winner, `os.rename` measured 0/40. Moving the destination into a subdirectory keeps it a
    single same-filesystem rename, and the `mkdir` that precedes it is idempotent and decides
    nothing — but "should still be atomic" is exactly the kind of claim that turns out false, so
    it is raced here.
    """
    multi = 0
    trials = 25
    for trial in range(trials):
        cont = HI.create_continuation(f"race{trial}", node_id="a", instance_path="p", epoch=1)
        got: dict[int, object] = {}
        barrier = threading.Barrier(8)

        def claim(index: int, run=f"race{trial}", token=cont.token) -> None:
            barrier.wait()
            got[index] = HI.consume_continuation(run, token)

        threads = [threading.Thread(target=claim, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if sum(1 for v in got.values() if v is not None) != 1:
            multi += 1
    assert multi == 0, f"{multi}/{trials} trials let more than one caller consume one approval"


def test_the_claim_is_a_LOCATION_not_a_filename_convention(tmp_path, monkeypatch):
    """The structural property the fix rests on: no listing has to know the claim convention.

    If the claimed record ever becomes a sibling FILE again, a `*.json` listing can match it and
    all six consumers regress together. Asserting the shape is what stops that.
    """
    cont = HI.create_continuation("r4", node_id="a", instance_path="p", epoch=1)
    HI.consume_continuation("r4", cont.token)

    assert (HI._claimed_dir("r4") / f"{cont.token}.json").is_file()
    # A non-recursive glob cannot reach into a subdirectory — that is the whole mechanism.
    assert list(HI._dir("r4").glob("*.json")) == []
    assert HI._claimed_dir("r4").is_dir(), "the claim store must be a directory, not a suffix"


# ── 4. the consumers ─────────────────────────────────────────────────────────


def test_a_REWIND_drops_the_pending_token_and_keeps_the_claimed_one(tmp_path, monkeypatch):
    """`drop_continuations` iterates the pending listing, so the defect reached it too: it saw
    claimed records, tried to unlink a path that the rename had already moved, and silently
    counted nothing — so a claimed record could never be reclaimed and the count was right only
    by accident. Now it drops exactly the live tokens, and the audit trail survives."""
    answered = HI.create_continuation("r5", node_id="done", instance_path="p", epoch=1)
    HI.consume_continuation("r5", answered.token)
    HI.create_continuation("r5", node_id="live", instance_path="p", epoch=2)

    assert HI.drop_continuations("r5") == 1, "the live token was not dropped"
    assert HI.list_continuations("r5") == []
    assert (
        HI._claimed_dir("r5") / f"{answered.token}.json"
    ).is_file(), "the rewind destroyed the audit record of an answer it did not need to touch"


async def test_a_SECOND_token_less_approval_reports_NO_PENDING_not_unknown_token(
    tmp_path, monkeypatch
):
    """The user-visible symptom, end to end on a live run.

    A run answers its gate token-lessly ("approve it"), then is asked again. Before the fix the
    answered record still read as pending, so the resolver picked the dead token and the user got
    `WF_RESUME_UNKNOWN_TOKEN` — a lie about the token rather than the truth about the run.
    """
    controller = await _parked("approve")
    sup = _Supervisor(controller)

    first = service.resume_run(controller.run.id, supervisor=sup, answer=True)
    assert first["ok"], f"the token-less approval did not land: {first}"

    again = service.resume_run(controller.run.id, supervisor=sup, answer=True)
    assert not again["ok"]
    assert (
        again["code"] == "WF_NO_PENDING_GATE"
    ), f"an answered run reported {again['code']!r}; the answered gate is still reading as pending"


async def test_the_needs_input_ROUTE_stops_offering_an_answered_gate(tmp_path, monkeypatch):
    """The inbox surface: a card a user can click must correspond to a gate that can be answered.

    Driven through the same projection the HTTP route builds, so an answered approval cannot come
    back as an open question with a live-looking button.
    """
    controller = await _parked("approve")
    token = HI.list_continuations(controller.run.id)[0].token
    assert controller.resume(token, True)["ok"]

    assert (
        HI.list_continuations(controller.run.id) == []
    ), "the needs-input inbox would still render a card for a gate that has been answered"
