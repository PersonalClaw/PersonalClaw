"""The confirmation-gate emission call site (TASKS-SOPS §4 — S61i).

S57 built the `ConfirmationRequest` record and its verbs; S61e gave the events a channel.
Neither was
emitted by a running gate — a run could park on an approval and the ledger would show
`workflow_needs_input` and nothing typed, so "how long did this gate wait" and "who answered it" had
no answer in the run's own history.

Both halves now fire from the controller, and the placement is the whole design:

* **PENDING rides `_ensure_continuation`**, which already dedups on `(path, epoch)`. The watchdog
  polls a waiting run repeatedly; a second emission site would put one "awaiting approval" row per
  poll into the ledger for a single question. Measured below: a re-poll leaves the count at 1.
* **RESOLVED fires AFTER the claim is won and the epoch checked.** Emitting earlier would log an
  approval for a race the caller LOST, and the audit would show two people approving one gate.

The id is derived from `(run, gate, epoch)` via the shipped `confirmation.request_id`,
never from the
resume token: a token is single-use and rotates, so a token-derived id would give the two halves
different values and they would never pair up.
"""

import asyncio

import pytest

from personalclaw.ledger import outcomes
from personalclaw.workflows.confirmation import ConfirmationType, request_id
from personalclaw.workflows.journal import CONFIRMATION_PENDING, CONFIRMATION_RESOLVED, ledger


def _spec(gate_config: dict) -> dict:
    return {
        "name": "t",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "gate", "id": "approve", "config": gate_config},
                {
                    "kind": "action",
                    "id": "after",
                    "config": {"provider": "bash", "with": {"command": "true"}},
                },
            ],
        },
    }


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.workflows import store as wstore

    monkeypatch.setattr(wstore, "config_dir", lambda: tmp_path)
    from personalclaw.action_providers import registry as apreg

    apreg._ensure_default_providers_registered()
    yield


async def _park_on_gate(gate_config: dict, run_id: str = "r-1"):
    """Start a run and let it park on its gate. Returns `(controller, continuations)`."""
    from personalclaw.workflows import store as wstore
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.human_input import list_continuations
    from personalclaw.workflows.models import WorkflowRun

    run = WorkflowRun(id=run_id, workflow_name="t")
    wstore.create(run)
    controller = RunController(run, _spec(gate_config), services=EngineServices())
    await controller.start()
    for _ in range(80):
        await asyncio.sleep(0.05)
        if run.status.value in ("needs_input", "paused"):
            break
    return controller, list_continuations(run_id)


def _rows(kind: str, run_id: str = "r-1") -> list[dict]:
    return [r for r in ledger(run_id) if r["kind"] == kind]


APPROVAL = {"kind": "approval", "prompt": "ok to ship?"}


# ── the pending half ──


def test_a_parked_gate_EMITS_a_pending_confirmation():
    """Before this, a run could park on an approval and its own history said nothing typed about
    it — so "how long did this gate wait" had no answer."""

    async def go():
        await _park_on_gate(APPROVAL)

    asyncio.run(go())
    rows = _rows(CONFIRMATION_PENDING)
    assert len(rows) == 1
    assert rows[0]["node_id"] == "approve"
    assert rows[0]["confirmation_id"].startswith("cr-")


def test_a_RE_POLL_does_not_emit_a_second_pending_row():
    """The watchdog polls a waiting run repeatedly. Riding `_ensure_continuation`'s existing
    `(path, epoch)` dedup means one row per QUESTION, not one per poll — a separate emission site
    would have had to re-derive that, and would have got it wrong."""

    async def go():
        controller, conts = await _park_on_gate(APPROVAL)
        before = len(_rows(CONFIRMATION_PENDING))
        controller._ensure_continuation(conts[0].instance_path)
        return before, len(_rows(CONFIRMATION_PENDING))

    before, after = asyncio.run(go())
    assert (before, after) == (1, 1)


def test_the_pending_row_classifies_an_ORDINARY_approval():
    async def go():
        await _park_on_gate(APPROVAL)

    asyncio.run(go())
    assert _rows(CONFIRMATION_PENDING)[0]["confirmation_kind"] == ConfirmationType.APPROVAL.value


def test_a_DESTRUCTIVE_gate_is_a_different_confirmation_TYPE():
    """§4 gives them different expiry policies (auto-reject vs hold) and only the ordinary one may
    be muted — so classifying a deletion as a plain approval would make it auto-APPROVE on timeout,
    the single worst behaviour available."""

    async def go():
        await _park_on_gate({**APPROVAL, "risk_category": "destructive"})

    asyncio.run(go())
    assert (
        _rows(CONFIRMATION_PENDING)[0]["confirmation_kind"]
        == ConfirmationType.DESTRUCTIVE_CONFIRM.value
    )


def test_the_classification_reads_the_AUTHOR_s_declaration():
    """Kept with the author who made it rather than inferred from prompt text at render time — a
    heuristic on the wording would reclassify a gate when someone edited its prose."""
    from personalclaw.workflows.controller import _confirmation_kind

    assert _confirmation_kind({"risk_category": "irreversible"}) == "destructive_confirm"
    assert _confirmation_kind({"kind": "question"}) == "needs_input"
    assert _confirmation_kind({}) == "approval"


def test_an_UNKNOWN_risk_word_falls_back_to_approval():
    """APPROVAL's expiry policy is HOLD — the run waits for a human rather than auto-resolving
    something this build could not classify."""
    from personalclaw.workflows.controller import _confirmation_kind

    assert _confirmation_kind({"risk_category": "vibes"}) == "approval"


# ── the resolution half ──


def test_answering_a_gate_emits_a_PAIRED_resolution():
    """Same `confirmation_id` on both halves, or the ledger cannot answer "how long did this wait"
    and "who answered" as one question."""

    async def go():
        controller, conts = await _park_on_gate(APPROVAL)
        controller.resume(conts[0].token, True, responder="dashboard:chat-1")

    asyncio.run(go())
    pending = _rows(CONFIRMATION_PENDING)[0]
    resolved = _rows(CONFIRMATION_RESOLVED)[0]
    assert resolved["confirmation_id"] == pending["confirmation_id"]


def test_an_APPROVAL_records_the_verb_the_boolean_and_the_RESOLVER():
    """ "Who approved this" is the question an audit exists to answer."""

    async def go():
        controller, conts = await _park_on_gate(APPROVAL)
        controller.resume(conts[0].token, True, responder="dashboard:chat-1")

    asyncio.run(go())
    row = _rows(CONFIRMATION_RESOLVED)[0]
    assert row["verb"] == "approve"
    assert row["approved"] is True
    assert row["resolved_by"] == "dashboard:chat-1"


def test_a_DENIAL_records_reject_and_false():
    """Both, not just the boolean: recording only `approved` would leave an audit unable to tell a
    deliberate reject from an expiry auto-reject."""

    async def go():
        controller, conts = await _park_on_gate(APPROVAL)
        controller.resume(conts[0].token, False, responder="prober")

    asyncio.run(go())
    row = _rows(CONFIRMATION_RESOLVED)[0]
    assert row["verb"] == "reject"
    assert row["approved"] is False


def test_an_UNATTRIBUTED_resolution_says_dashboard_not_empty():
    """An empty resolver reads as "no resolver", indistinguishable from an unrecorded one. An HTTP
    caller is already authenticated by the gateway, so `dashboard` is the honest default."""

    async def go():
        controller, conts = await _park_on_gate(APPROVAL)
        controller.resume(conts[0].token, True)

    asyncio.run(go())
    assert _rows(CONFIRMATION_RESOLVED)[0]["resolved_by"] == "dashboard"


def test_a_LOST_race_emits_NO_resolution():
    """The second caller's claim fails, and the ledger must not show two approvals for one gate —
    which is exactly what emitting before the claim would produce."""

    async def go():
        controller, conts = await _park_on_gate(APPROVAL)
        first = controller.resume(conts[0].token, True, responder="a")
        second = controller.resume(conts[0].token, True, responder="b")
        return first, second

    first, second = asyncio.run(go())
    assert first.get("ok") is True
    assert second.get("ok") is False
    assert len(_rows(CONFIRMATION_RESOLVED)) == 1


def test_an_unanswered_gate_has_pending_but_NO_resolution():
    async def go():
        await _park_on_gate(APPROVAL)

    asyncio.run(go())
    assert _rows(CONFIRMATION_PENDING)
    assert _rows(CONFIRMATION_RESOLVED) == []


# ── the escalation's OUTCOME: was interrupting the user worth it? (PP-9) ──


def test_a_parked_gate_OPENS_an_escalation_OUTCOME():
    """`confirmation_pending` records that we ASKED. This records the BET — that asking was worth
    it — on the general outcome facility, keyed to the same `confirmation_id` so the answer grades
    it. Without it the platform records what it did to the user and never whether it landed.

    Emitted at the pending half's site, so it inherits that site's `(path, epoch)` dedup.
    """

    async def go():
        await _park_on_gate(APPROVAL)

    asyncio.run(go())
    (question,) = outcomes.open_questions(ledger("r-1"))
    assert question.producer == outcomes.PRODUCER_ESCALATION
    # ledger-sourced: the ground truth is an event this run writes itself, so it grades on a box
    # with no vector store at all
    assert question.metric_source == outcomes.SOURCE_LEDGER
    assert question.metric == CONFIRMATION_RESOLVED
    assert question.match == {"confirmation_id": _rows(CONFIRMATION_PENDING)[0]["confirmation_id"]}
    assert question.horizon_secs > 0.0


def test_a_RE_POLL_does_not_open_a_SECOND_escalation_outcome():
    """One question per gate, not one per watchdog poll — the reason it is emitted beside the
    pending half rather than at a site of its own."""

    async def go():
        controller, conts = await _park_on_gate(APPROVAL)
        controller._ensure_continuation(conts[0].instance_path)
        return ledger("r-1")

    assert len(outcomes.open_questions(asyncio.run(go()))) == 1


def test_ANSWERING_the_gate_MEASURES_the_escalation_outcome():
    """The pair closing end to end: the user approves, the controller writes the
    `confirmation_resolved` the question named, and that event's `approved` boolean IS the
    measurement (1.0 against a baseline of 1.0 — the interruption landed)."""

    async def go():
        controller, conts = await _park_on_gate(APPROVAL)
        controller.resume(conts[0].token, True, responder="dashboard:chat-1")
        return ledger("r-1")

    events = asyncio.run(go())
    (question,) = outcomes.open_questions(events)
    assert outcomes.measure_from_events(question, events) == 1.0


def test_a_DENIED_gate_measures_as_a_LOST_bet_not_an_unreadable_one():
    """A rejection is a MEASUREMENT (0.0 against a baseline of 1.0 → score −1), not an
    inconclusive. Collapsing the two would make "we interrupted the user and they said no"
    indistinguishable from "nobody ever looked", which are opposite facts about the same gate."""

    async def go():
        controller, conts = await _park_on_gate(APPROVAL)
        controller.resume(conts[0].token, False, responder="prober")
        return ledger("r-1")

    events = asyncio.run(go())
    (question,) = outcomes.open_questions(events)
    measured = outcomes.measure_from_events(question, events)
    assert measured == 0.0
    assert outcomes.resolution_for(measured) == outcomes.MEASURED
    assert outcomes.score(measured, question.baseline) == pytest.approx(-1.0)


# ── the id contract ──


def test_the_id_comes_from_the_SHIPPED_request_id():
    """Two id schemes for one record is the failure where the halves never pair up, and nobody
    notices until someone asks how long a gate waited."""
    from personalclaw.workflows.controller import _confirmation_id

    assert _confirmation_id("r", "g", 1) == request_id("r", "g", 1)


def test_the_id_is_STABLE_across_polls():
    from personalclaw.workflows.controller import _confirmation_id

    assert _confirmation_id("r", "g", 1) == _confirmation_id("r", "g", 1)


def test_the_EPOCH_is_in_the_id_so_a_rewind_asks_a_NEW_question():
    """A rewound gate is being asked about different work. Deriving from the resume token instead
    would break this: a token rotates per poll, so the two halves would disagree."""
    from personalclaw.workflows.controller import _confirmation_id

    assert _confirmation_id("r", "g", 1) != _confirmation_id("r", "g", 2)


def test_the_id_is_NOT_the_resume_token():
    """Structural: a token-derived id changes when the token rotates, and pending/resolved would
    never match."""
    import inspect

    from personalclaw.workflows.controller import RunController

    source = inspect.getsource(RunController._ensure_continuation)
    assert "_confirmation_id(" in source
    assert "confirmation_id=cont.token" not in source


# ── both channels ──


def test_the_pending_event_also_reaches_the_LIVE_stream():
    """The ledger is what a rebuild reads; the stream is what an open view folds. A gate that
    appeared in only one would show on the board and vanish on reload, or the reverse."""

    async def go():
        from personalclaw.workflows import store as wstore
        from personalclaw.workflows.controller import EngineServices, RunController
        from personalclaw.workflows.models import WorkflowRun

        published: list = []
        run = WorkflowRun(id="r-1", workflow_name="t")
        wstore.create(run)
        controller = RunController(
            run,
            _spec(APPROVAL),
            services=EngineServices(publish=lambda e, b: published.append((e, b))),
        )
        await controller.start()
        for _ in range(80):
            await asyncio.sleep(0.05)
            if run.status.value in ("needs_input", "paused"):
                break
        return [e for e, _b in published]

    events = asyncio.run(go())
    assert "workflow_confirmation_pending" in events


def test_the_needs_input_event_is_STILL_emitted():
    """The typed record is additive. The existing `workflow_needs_input` frame is what the inbox and
    the resume affordance already bind to, and replacing it would break both."""

    async def go():
        from personalclaw.workflows import store as wstore
        from personalclaw.workflows.controller import EngineServices, RunController
        from personalclaw.workflows.models import WorkflowRun

        published: list = []
        run = WorkflowRun(id="r-1", workflow_name="t")
        wstore.create(run)
        controller = RunController(
            run,
            _spec(APPROVAL),
            services=EngineServices(publish=lambda e, b: published.append((e, b))),
        )
        await controller.start()
        for _ in range(80):
            await asyncio.sleep(0.05)
            if run.status.value in ("needs_input", "paused"):
                break
        return [e for e, _b in published]

    events = asyncio.run(go())
    assert "workflow_needs_input" in events
