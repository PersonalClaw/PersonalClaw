"""The human-input contract (WF2-R7) — durable, single-use, typed.

`needs_input` has to survive the process, the surface, and the session. The load-bearing
claims:

* **the answer is consumed ATOMICALLY** — a double-click, a retried POST, or a widget and
  an inbox racing must not replay one approval into two actions;
* **validation happens BEFORE consumption** — rejecting afterwards would already have
  destroyed the token, leaving a dead link and an unanswered gate;
* a continuation carries `resolved_inputs`, so resuming re-enters THAT step rather than
  re-executing the enclosing subgraph;
* **expiry is typed** — a stale token yields a `resume_expired` item offering a re-run,
  never a button that silently does nothing;
* **background gates time out fast, blocking gates wait** — otherwise a background run
  wedges on an approval nobody will see;
* a rewind drops pending tokens: one for a node about to re-run would land in the wrong
  epoch;
* only an `approval` ask can DENY — text/form answers are data, not verdicts.
"""

from __future__ import annotations

import time

import pytest

from personalclaw.workflows import human_input as HI
from personalclaw.workflows import journal as J
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

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


def _gate_spec(config: dict | None = None) -> dict:
    return {
        "name": "gated",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "transform", "id": "prep", "config": {"expr": {"v": 1}}},
                {
                    "kind": "gate",
                    "id": "approve",
                    "config": {
                        "kind": "approval",
                        "prompt": "Ship it?",
                        **(config or {}),
                    },
                },
                {
                    "kind": "transform",
                    "id": "after",
                    "config": {"expr": "went {{nodes.approve.output.approved}}"},
                },
            ],
        },
    }


async def _blocked(config: dict | None = None, mode: str = "background"):
    """A real run parked on a real gate."""
    spec = _gate_spec(config)
    run = store.create(WorkflowRun(id="", workflow_name="gated", mode=mode))
    store.write_spec(run.id, spec)
    c = RunController(run, spec, services=EngineServices())
    status = await c.run_to_completion(timeout=20)
    return c, status


# ── the typed ask ────────────────────────────────────────────────────────────


class TestAskPayload:
    def test_an_unknown_kind_degrades_to_approval(self) -> None:
        """Tolerant reader: an unknown kind renders as an approval rather than crashing a
        run at the moment a human is needed."""
        assert HI.Ask.from_dict({"kind": "telepathy"}).kind == HI.AskKind.APPROVAL

    def test_the_payload_round_trips(self) -> None:
        ask = HI.Ask(
            kind=HI.AskKind.FORM,
            prompt="details",
            fields=[HI.AskField(name="qty", type="number", required=True)],
        )
        assert HI.Ask.from_dict(ask.to_dict()).to_dict() == ask.to_dict()

    @pytest.mark.parametrize("answer", [True, False, {"approved": True}])
    def test_an_approval_accepts_a_boolean(self, answer) -> None:
        assert HI.Ask(kind=HI.AskKind.APPROVAL).validate_answer(answer) == ""

    def test_an_approval_rejects_prose(self) -> None:
        assert HI.Ask(kind=HI.AskKind.APPROVAL).validate_answer("sure") != ""

    def test_a_choice_must_be_one_of_the_offered_options(self) -> None:
        ask = HI.Ask(kind=HI.AskKind.CHOICE, choices=["a", "b"])
        assert ask.validate_answer("a") == ""
        assert "not one of" in ask.validate_answer("z")

    def test_text_rejects_empty(self) -> None:
        ask = HI.Ask(kind=HI.AskKind.TEXT)
        assert ask.validate_answer("something") == ""
        assert ask.validate_answer("   ") != ""

    def test_a_form_enforces_required_fields_and_types(self) -> None:
        ask = HI.Ask(
            kind=HI.AskKind.FORM,
            fields=[
                HI.AskField(name="qty", type="number", required=True),
                HI.AskField(name="note", type="string"),
            ],
        )
        assert ask.validate_answer({"qty": 3}) == ""
        assert "missing required" in ask.validate_answer({"note": "x"})
        assert "expects a number" in ask.validate_answer({"qty": "three"})

    def test_a_form_default_satisfies_a_required_field(self) -> None:
        ask = HI.Ask(
            kind=HI.AskKind.FORM,
            fields=[HI.AskField(name="qty", type="number", required=True, default=1)],
        )
        assert ask.validate_answer({}) == ""
        assert ask.apply_defaults({}) == {"qty": 1}

    def test_a_form_choice_field_is_constrained(self) -> None:
        ask = HI.Ask(
            kind=HI.AskKind.FORM,
            fields=[HI.AskField(name="env", type="choice", choices=["dev", "prod"])],
        )
        assert ask.validate_answer({"env": "dev"}) == ""
        assert "must be one of" in ask.validate_answer({"env": "staging"})


class TestGateTimeouts:
    def test_background_gates_time_out_fast(self) -> None:
        """A background run parked forever on an approval nobody watches is wedged."""
        assert HI.gate_timeout_secs({}, mode="background") == (
            HI.DEFAULT_BACKGROUND_GATE_TIMEOUT_SECS
        )

    def test_blocking_gates_wait_long(self) -> None:
        """A human is right there; timing out under them would discard an answer."""
        assert HI.gate_timeout_secs({}, mode="blocking") == HI.DEFAULT_BLOCKING_GATE_TIMEOUT_SECS

    def test_an_explicit_timeout_always_wins(self) -> None:
        for mode in ("background", "blocking"):
            assert HI.gate_timeout_secs({"timeout_secs": 7}, mode=mode) == 7

    def test_zero_means_wait_indefinitely(self) -> None:
        assert HI.gate_timeout_secs({"timeout_secs": 0}, mode="background") == 0


# ── continuations ────────────────────────────────────────────────────────────


class TestContinuations:
    def test_a_continuation_persists_and_reloads(self) -> None:
        run = store.create(WorkflowRun(id="", workflow_name="c"))
        cont = HI.create_continuation(
            run.id,
            node_id="approve",
            instance_path="root.children[1]",
            epoch=0,
            resolved_inputs={"prep": {"v": 1}},
        )
        loaded = HI.load_continuation(run.id, cont.token)
        assert loaded is not None
        assert loaded.resolved_inputs == {"prep": {"v": 1}}

    def test_tokens_are_unguessable(self) -> None:
        """A guessable token is an approval anyone can forge, and it authorizes real
        action."""
        tokens = {HI.new_token() for _ in range(50)}
        assert len(tokens) == 50
        assert all(len(t) >= 30 for t in tokens)

    def test_consuming_deletes_the_record(self) -> None:
        run = store.create(WorkflowRun(id="", workflow_name="c"))
        cont = HI.create_continuation(run.id, node_id="g", instance_path="p", epoch=0)
        assert HI.consume_continuation(run.id, cont.token) is not None
        assert HI.load_continuation(run.id, cont.token) is None

    def test_a_second_consume_returns_none(self) -> None:
        """The atomicity that stops one approval becoming two actions."""
        run = store.create(WorkflowRun(id="", workflow_name="c"))
        cont = HI.create_continuation(run.id, node_id="g", instance_path="p", epoch=0)
        first = HI.consume_continuation(run.id, cont.token)
        second = HI.consume_continuation(run.id, cont.token)
        assert first is not None and second is None

    def test_expiry_is_detected(self) -> None:
        run = store.create(WorkflowRun(id="", workflow_name="c"))
        cont = HI.create_continuation(
            run.id, node_id="g", instance_path="p", epoch=0, ttl_secs=1, now=time.time() - 100
        )
        assert HI.load_continuation(run.id, cont.token).expired

    def test_a_zero_ttl_never_expires(self) -> None:
        run = store.create(WorkflowRun(id="", workflow_name="c"))
        cont = HI.create_continuation(run.id, node_id="g", instance_path="p", epoch=0, ttl_secs=0)
        assert not HI.load_continuation(run.id, cont.token).expired

    @pytest.mark.parametrize("bad", ["../escape", "a/b", "a\\b", ""])
    def test_a_traversal_token_is_refused(self, bad: str) -> None:
        """A token arrives from an HTTP path and is not a trust boundary."""
        run = store.create(WorkflowRun(id="", workflow_name="c"))
        assert HI.load_continuation(run.id, bad) is None
        assert HI.consume_continuation(run.id, bad) is None

    def test_dropping_by_prefix_only_removes_matching_tokens(self) -> None:
        run = store.create(WorkflowRun(id="", workflow_name="c"))
        HI.create_continuation(run.id, node_id="a", instance_path="root.children[0]", epoch=0)
        keep = HI.create_continuation(
            run.id, node_id="b", instance_path="root.children[9]", epoch=0
        )
        assert HI.drop_continuations(run.id, instance_prefix="root.children[0]") == 1
        assert HI.load_continuation(run.id, keep.token) is not None

    def test_the_expired_item_offers_a_concrete_next_move(self) -> None:
        """A dead token that does nothing is indistinguishable from a bug."""
        cont = HI.Continuation(token="t", run_id="r", node_id="approve", instance_path="p")
        item = HI.expired_item(cont)
        assert item["kind"] == "resume_expired"
        assert "re-run" in item["remediation"]

    def test_the_handoff_bundle_has_a_fixed_shape(self) -> None:
        bundle = HI.handoff_bundle(scope="wf", status="blocked", risks=["may deploy"])
        assert set(bundle) == {
            "scope",
            "status",
            "outstanding",
            "checks_run",
            "next_steps",
            "risks",
        }


# ── controller integration ───────────────────────────────────────────────────


class TestBlockedRun:
    async def test_a_gate_parks_the_run_in_needs_input(self) -> None:
        c, status = await _blocked({"timeout_secs": 0})
        assert status == RunStatus.NEEDS_INPUT
        assert c.instances["root.children[1]"].state == InstanceState.WAITING

    async def test_a_continuation_is_minted_with_the_resolved_inputs(self) -> None:
        """The field that makes resume re-enter the STEP rather than the subgraph."""
        c, _status = await _blocked({"timeout_secs": 0})
        conts = HI.list_continuations(c.run.id)
        assert len(conts) == 1
        assert conts[0].node_id == "approve"
        assert conts[0].instance_path == "root.children[1]"

    async def test_the_handoff_bundle_records_what_already_ran(self) -> None:
        c, _status = await _blocked({"timeout_secs": 0})
        cont = HI.list_continuations(c.run.id)[0]
        assert "root.children[0]" in cont.handoff["checks_run"]

    async def test_only_one_continuation_per_epoch(self) -> None:
        """A run passes through needs_input repeatedly as the watchdog polls; a token per
        poll would leave a pile of individually-valid approval links for one question."""
        c, _status = await _blocked({"timeout_secs": 0})
        c._ensure_continuation("root.children[1]")
        c._ensure_continuation("root.children[1]")
        assert len(HI.list_continuations(c.run.id)) == 1


class TestSurfaceAndTimeoutCoexist:
    """Regression pair. Surfacing promptly and honouring the unattended deadline are
    SEPARATE behaviours; an earlier cut collapsed them and silently broke one or the other
    depending on which was written last."""

    async def test_a_gate_with_a_deadline_still_surfaces_immediately(self) -> None:
        """A run that parks quietly for 45s and only then surfaces is a run nobody knows to
        answer."""
        c, status = await _blocked({"timeout_secs": 60})
        assert status == RunStatus.NEEDS_INPUT
        assert HI.list_continuations(c.run.id)

    async def test_a_surfaced_gate_can_still_time_out(self) -> None:
        """And the deadline is not decorative — a surfaced run is waiting, not finished."""
        spec = _gate_spec({"timeout_secs": 1})
        run = store.create(WorkflowRun(id="", workflow_name="gated"))
        store.write_spec(run.id, spec)
        c = RunController(run, spec, services=EngineServices())
        assert await c.run_to_completion(timeout=20) == RunStatus.FAILED
        assert c.instances["root.children[1]"].failure.terminal_reason == "timed_out_unattended"

    async def test_a_plain_wait_is_not_surfaced_as_needs_input(self) -> None:
        """A `wait` is parked on the CLOCK and resolves itself; asking a human to answer it
        would be asking them about something nobody asked."""
        spec = {
            "name": "w",
            "root": {"kind": "wait", "id": "w", "config": {"duration_secs": 0.2}},
        }
        run = store.create(WorkflowRun(id="", workflow_name="w"))
        store.write_spec(run.id, spec)
        c = RunController(run, spec, services=EngineServices())
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
        assert HI.list_continuations(run.id) == []


class TestResume:
    async def test_approving_completes_the_gate_and_unblocks_the_run(self) -> None:
        c, _status = await _blocked({"timeout_secs": 0})
        token = HI.list_continuations(c.run.id)[0].token
        result = c.resume(token, True)
        assert result["ok"] and result["approved"]
        assert c.instances["root.children[1]"].state == InstanceState.DONE
        # The run can now finish, and the downstream node reads the answer.
        c.run.status = RunStatus.RUNNING
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
        # Interpolated into a string, so JSON-ish lowercase — the answer reached the node.
        assert c._outputs["after"] == "went true"

    async def test_denying_fails_the_gate_with_a_typed_reason(self) -> None:
        c, _status = await _blocked({"timeout_secs": 0})
        token = HI.list_continuations(c.run.id)[0].token
        result = c.resume(token, False)
        assert result["ok"] and not result["approved"]
        inst = c.instances["root.children[1]"]
        assert inst.state == InstanceState.FAILED
        assert inst.failure.terminal_reason == "denied"

    async def test_a_double_resume_is_refused(self) -> None:
        """The headline: two clicks must not become two deployments."""
        c, _status = await _blocked({"timeout_secs": 0})
        token = HI.list_continuations(c.run.id)[0].token
        first = c.resume(token, True)
        second = c.resume(token, True)
        assert first["ok"]
        assert not second["ok"] and second["code"] == "WF_RESUME_UNKNOWN_TOKEN"

    async def test_an_invalid_answer_does_not_consume_the_token(self) -> None:
        """Rejecting after consumption would leave a dead link and an unanswered gate."""
        c, _status = await _blocked({"timeout_secs": 0})
        token = HI.list_continuations(c.run.id)[0].token
        bad = c.resume(token, "yes please")
        assert not bad["ok"] and bad["code"] == "WF_RESUME_INVALID_ANSWER"
        # The token survives, so the user can correct their answer.
        assert c.resume(token, True)["ok"]

    async def test_an_unknown_token_is_refused(self) -> None:
        c, _status = await _blocked({"timeout_secs": 0})
        assert c.resume("nope", True)["code"] == "WF_RESUME_UNKNOWN_TOKEN"

    async def test_an_expired_token_yields_a_typed_item(self) -> None:
        c, _status = await _blocked({"timeout_secs": 0})
        cont = HI.list_continuations(c.run.id)[0]
        cont.expires_at = time.time() - 10
        HI.save_continuation(cont)
        result = c.resume(cont.token, True)
        assert not result["ok"] and result["code"] == "WF_RESUME_EXPIRED"
        assert result["item"]["kind"] == "resume_expired"
        # Consumed, so the dead token cannot be retried forever.
        assert HI.load_continuation(c.run.id, cont.token) is None

    async def test_the_resolution_is_journaled_with_the_answer(self) -> None:
        """A later reader needs to know WHO decided WHAT, not merely that it continued."""
        c, _status = await _blocked({"timeout_secs": 0})
        token = HI.list_continuations(c.run.id)[0].token
        c.resume(token, True)
        resolved = [e for e in J.ledger(c.run.id) if e.get("kind") == J.GATE_RESOLVED]
        assert len(resolved) == 1
        assert resolved[0]["approved"] is True and resolved[0]["node_id"] == "approve"

    async def test_a_stale_epoch_answer_is_refused(self) -> None:
        """The node was rewound under the token: applying the answer would land it in the
        wrong epoch, which is worse than refusing."""
        c, _status = await _blocked({"timeout_secs": 0})
        cont = HI.list_continuations(c.run.id)[0]
        c.instances[cont.instance_path].epoch = 5
        assert c.resume(cont.token, True)["code"] == "WF_RESUME_STALE_EPOCH"

    async def test_a_form_answer_is_stored_and_never_read_as_a_denial(self) -> None:
        """Text/form answers are DATA, not verdicts — an empty-ish value must not fail a
        gate the user genuinely answered."""
        c, _status = await _blocked(
            {
                "timeout_secs": 0,
                "ask_kind": "form",
                "fields": [{"name": "note", "type": "string"}],
            }
        )
        token = HI.list_continuations(c.run.id)[0].token
        result = c.resume(token, {"note": ""})
        assert result["ok"] and result["approved"]


class TestRewindDropsTokens:
    async def test_rewinding_the_gate_drops_its_pending_token(self) -> None:
        """A token for a node about to re-run would resume a step that no longer exists in
        that form."""
        c, _status = await _blocked({"timeout_secs": 0})
        assert HI.list_continuations(c.run.id)
        c.submit_mutation([{"op": "rewind", "node_id": "approve"}], confirm=True)
        c._drain_mutations()
        assert HI.list_continuations(c.run.id) == []

    async def test_rewinding_an_unrelated_node_keeps_the_token(self) -> None:
        """The other half, and the reason dropping is prefix-scoped: `prep` feeds nothing
        the gate reads, so its rewind does not invalidate the question a human is answering.
        Dropping every token on any rewind would cancel approvals for no reason."""
        c, _status = await _blocked({"timeout_secs": 0})
        token = HI.list_continuations(c.run.id)[0].token
        c.submit_mutation([{"op": "rewind", "node_id": "prep"}], confirm=True)
        c._drain_mutations()
        assert HI.load_continuation(c.run.id, token) is not None
