"""Gate policy (WF2-R7) — who may answer, what auto-approves, how a hold behaves.

The load-bearing claims, each guarding a specific way unattended automation goes wrong:

* **auto-approve is risk-scoped, never blanket** — a scheduled run that waves through a
  DESTRUCTIVE action is a liability, and an undeclared gate defaults to DESTRUCTIVE so
  forgetting to classify one makes it ASK rather than silently proceed;
* **a remote reply must come from the run's owner** — otherwise a shared channel is a
  privilege-escalation path where anyone who can type approves someone else's deploy;
* **an unanswered remote gate DENIES** — silence is not consent;
* **an event gate does not eat its wake-up** — consuming the event and then failing
  destroys the only signal that would ever satisfy it;
* prerequisite-absent and input-invalid are DIFFERENT: one holds, one fails;
* **"always allow" is cleared on rewind** — otherwise it auto-approves the very step the
  user rewound to reconsider;
* an action provider may ASK mid-run without the author pre-placing a gate.
"""

from __future__ import annotations

import pytest

from personalclaw.tool_providers.base import RiskLevel
from personalclaw.workflows import gate_policy as GP
from personalclaw.workflows import human_input as HI
from personalclaw.workflows import journal as J
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import (
    InstanceState,
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


# ── risk classification ──────────────────────────────────────────────────────


class TestGateRisk:
    @pytest.mark.parametrize(
        "declared,expected",
        [
            ("safe", RiskLevel.SAFE),
            ("caution", RiskLevel.CAUTION),
            ("destructive", RiskLevel.DESTRUCTIVE),
        ],
    )
    def test_a_declared_risk_is_honoured(self, declared: str, expected: RiskLevel) -> None:
        assert GP.gate_risk({"risk": declared}) == expected

    def test_an_undeclared_gate_defaults_to_destructive(self) -> None:
        """Deny-by-default toward higher risk: forgetting to classify a gate must make it
        ASK, never silently auto-approve."""
        assert GP.gate_risk({}) == RiskLevel.DESTRUCTIVE
        assert GP.gate_risk({"risk": "nonsense"}) == RiskLevel.DESTRUCTIVE

    def test_destructive_is_not_auto_approvable(self) -> None:
        assert RiskLevel.DESTRUCTIVE not in GP.AUTO_APPROVABLE_RISKS


class TestUnattendedDetection:
    @pytest.mark.parametrize(
        "kind", [OriginKind.SCHEDULE, OriginKind.EVENT, OriginKind.HOOK, OriginKind.IDLE]
    )
    def test_trigger_origins_are_unattended(self, kind: OriginKind) -> None:
        assert GP.is_unattended(kind)

    @pytest.mark.parametrize("kind", [OriginKind.CHAT, OriginKind.MANUAL, OriginKind.API])
    def test_requested_origins_are_attended(self, kind: OriginKind) -> None:
        """A requester exists who can answer, even in background mode."""
        assert not GP.is_unattended(kind)

    def test_blocking_mode_is_never_unattended(self) -> None:
        assert not GP.is_unattended(OriginKind.SCHEDULE, mode="blocking")


class TestDecide:
    def test_an_unattended_run_auto_approves_a_safe_gate(self) -> None:
        verdict = GP.decide({"risk": "safe"}, "g", origin_kind=OriginKind.SCHEDULE)
        assert verdict.decision == GP.Decision.AUTO_APPROVED and verdict.approved

    def test_an_unattended_run_auto_approves_a_caution_gate(self) -> None:
        verdict = GP.decide({"risk": "caution"}, "g", origin_kind=OriginKind.SCHEDULE)
        assert verdict.approved

    def test_an_unattended_run_still_asks_for_a_destructive_gate(self) -> None:
        """An unreviewed destructive action is worse than a stalled run."""
        verdict = GP.decide({"risk": "destructive"}, "g", origin_kind=OriginKind.SCHEDULE)
        assert verdict.asks_human and not verdict.approved

    def test_an_attended_run_always_asks(self) -> None:
        verdict = GP.decide({"risk": "safe"}, "g", origin_kind=OriginKind.CHAT)
        assert verdict.asks_human

    def test_a_remembered_allow_wins_over_asking(self) -> None:
        memory = GP.AllowMemory()
        memory.remember({"risk": "destructive"}, "g")
        verdict = GP.decide(
            {"risk": "destructive"}, "g", origin_kind=OriginKind.CHAT, memory=memory
        )
        assert verdict.decision == GP.Decision.REMEMBERED and verdict.approved

    def test_the_verdict_carries_its_reasoning(self) -> None:
        """The reason is rendered to a user, so it has to say WHY, not just what."""
        verdict = GP.decide({"risk": "destructive"}, "g", origin_kind=OriginKind.SCHEDULE)
        assert "destructive" in verdict.reason and verdict.to_dict()["risk"] == "destructive"


class TestAllowMemory:
    def test_it_is_keyed_by_operation_and_target(self) -> None:
        memory = GP.AllowMemory()
        memory.remember({"operation": "deploy", "target": "prod"}, "n1")
        assert memory.allows({"operation": "deploy", "target": "prod"}, "n2")
        assert not memory.allows({"operation": "deploy", "target": "staging"}, "n1")

    def test_the_node_id_is_the_fallback_target(self) -> None:
        memory = GP.AllowMemory()
        memory.remember({}, "approve-deploy")
        assert memory.allows({}, "approve-deploy")
        assert not memory.allows({}, "approve-other")

    def test_clearing_forgets_everything(self) -> None:
        memory = GP.AllowMemory()
        memory.remember({}, "g")
        memory.clear()
        assert len(memory) == 0


# ── remote-channel gates ─────────────────────────────────────────────────────


class TestOwnerBinding:
    def _run(self, session_key: str = "owner-1") -> WorkflowRun:
        return WorkflowRun(
            id="r",
            workflow_name="w",
            origin=RunOrigin(kind=OriginKind.CHAT, session_key=session_key),
        )

    def test_a_local_answer_needs_no_channel_binding(self) -> None:
        """The gateway already authenticated a widget/CLI/HTTP caller."""
        ok, _why = GP.may_answer(self._run(), responder="", channel="")
        assert ok

    def test_the_owner_may_answer_remotely(self) -> None:
        ok, _why = GP.may_answer(self._run("owner-1"), responder="owner-1", channel="slack")
        assert ok

    def test_a_non_owner_is_refused(self) -> None:
        """Without this, a shared channel is a privilege-escalation path."""
        ok, why = GP.may_answer(self._run("owner-1"), responder="someone-else", channel="slack")
        assert not ok and "requester" in why

    def test_an_ownerless_run_refuses_remote_approval(self) -> None:
        """An unattributable approval on a shared channel is exactly what binding stops."""
        ok, why = GP.may_answer(self._run(""), responder="anyone", channel="slack")
        assert not ok and "no recorded owner" in why

    def test_an_unanswered_remote_gate_denies(self) -> None:
        """Silence is not consent — passing on timeout would ship a deploy because nobody
        was reading a channel."""
        verdict = GP.remote_timeout_decision({"risk": "destructive"})
        assert verdict.decision == GP.Decision.AUTO_DENIED and not verdict.approved


# ── event gates ──────────────────────────────────────────────────────────────


class TestEventHold:
    def test_a_satisfied_prerequisite_proceeds_and_consumes_the_event(self) -> None:
        verdict = GP.evaluate_event_gate({}, GP.HoldState(), prerequisite_met=True)
        assert not verdict.hold and not verdict.preserve_event and not verdict.give_up

    def test_an_absent_prerequisite_holds_and_PRESERVES_the_event(self) -> None:
        """The core of the rule: a gate that ate its wake-up and then failed would destroy
        the only signal that would ever satisfy it."""
        state = GP.HoldState()
        verdict = GP.evaluate_event_gate({}, state, prerequisite_met=False)
        assert verdict.hold and verdict.preserve_event and not verdict.give_up
        assert state.holds == 1

    def test_holds_are_bounded_and_give_up_loudly(self) -> None:
        """An unbounded hold is a wedge that looks like patience."""
        state = GP.HoldState()
        for _ in range(GP.DEFAULT_EVENT_HOLD_LIMIT):
            GP.evaluate_event_gate({}, state, prerequisite_met=False)
        final = GP.evaluate_event_gate({}, state, prerequisite_met=False)
        assert final.give_up and not final.hold
        assert "giving up" in final.reason

    def test_the_hold_limit_is_configurable(self) -> None:
        state = GP.HoldState()
        GP.evaluate_event_gate({"hold_limit": 1}, state, prerequisite_met=False)
        second = GP.evaluate_event_gate({"hold_limit": 1}, state, prerequisite_met=False)
        assert second.give_up

    def test_invalid_input_fails_rather_than_holding(self) -> None:
        """prerequisite-absent is NOT input-invalid: the event arrived and was wrong, so
        retrying the same payload only burns budget."""
        verdict = GP.evaluate_event_gate(
            {}, GP.HoldState(), prerequisite_met=False, input_valid=False
        )
        assert verdict.give_up and not verdict.hold
        assert not verdict.preserve_event  # the event WAS delivered; it was just bad


# ── action-node clarification ────────────────────────────────────────────────


class TestClarificationExtraction:
    def test_a_string_clarification_becomes_a_text_ask(self) -> None:
        ask = GP.clarification_from_output({"needs_input": "Which environment?"})
        assert ask == {"kind": "text", "prompt": "Which environment?"}

    def test_a_structured_clarification_is_preserved(self) -> None:
        ask = GP.clarification_from_output(
            {"needs_input": {"kind": "choice", "prompt": "env?", "choices": ["dev", "prod"]}}
        )
        assert ask["kind"] == "choice" and ask["choices"] == ["dev", "prod"]

    def test_the_clarification_alias_is_accepted(self) -> None:
        assert GP.clarification_from_output({"clarification": "which?"}) is not None

    def test_a_normal_output_is_not_a_clarification(self) -> None:
        assert GP.clarification_from_output({"count": 3}) is None
        assert GP.clarification_from_output("plain text") is None
        assert GP.clarification_from_output(None) is None


# ── controller integration ───────────────────────────────────────────────────


def _gate_spec(gate_config: dict) -> dict:
    return {
        "name": "policy",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {
                    "kind": "gate",
                    "id": "approve",
                    "config": {"kind": "approval", "prompt": "ok?", **gate_config},
                },
                {"kind": "transform", "id": "after", "config": {"expr": "done"}},
            ],
        },
    }


async def _run_with(gate_config: dict, *, origin: OriginKind, mode: str = "background"):
    spec = _gate_spec(gate_config)
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="policy",
            mode=mode,
            origin=RunOrigin(kind=origin, session_key="owner-1"),
        )
    )
    store.write_spec(run.id, spec)
    c = RunController(run, spec, services=EngineServices())
    status = await c.run_to_completion(timeout=20)
    return c, status


class TestControllerAutoApprove:
    async def test_a_scheduled_run_sails_through_a_safe_gate(self) -> None:
        """This is what makes an unattended run actually unattended."""
        c, status = await _run_with({"risk": "safe", "timeout_secs": 0}, origin=OriginKind.SCHEDULE)
        assert status == RunStatus.COMPLETE
        assert c.instances["root.children[0]"].state == InstanceState.DONE
        resolved = [e for e in J.ledger(c.run.id) if e.get("kind") == J.GATE_RESOLVED]
        assert len(resolved) == 1
        assert resolved[0]["policy"]["decision"] == "auto_approved"

    async def test_a_scheduled_run_still_stops_at_a_destructive_gate(self) -> None:
        c, status = await _run_with(
            {"risk": "destructive", "timeout_secs": 0}, origin=OriginKind.SCHEDULE
        )
        assert status == RunStatus.NEEDS_INPUT
        assert c.instances["root.children[0]"].state == InstanceState.WAITING

    async def test_an_undeclared_gate_stops_an_unattended_run(self) -> None:
        """The deny-by-default payoff: forgetting to classify does not create an unreviewed
        action."""
        c, status = await _run_with({"timeout_secs": 0}, origin=OriginKind.SCHEDULE)
        assert status == RunStatus.NEEDS_INPUT

    async def test_a_chat_run_stops_even_at_a_safe_gate(self) -> None:
        c, status = await _run_with({"risk": "safe", "timeout_secs": 0}, origin=OriginKind.CHAT)
        assert status == RunStatus.NEEDS_INPUT


class TestControllerRemoteAnswers:
    async def test_the_owner_may_answer_from_a_channel(self) -> None:
        c, _status = await _run_with({"timeout_secs": 0}, origin=OriginKind.CHAT)
        token = HI.list_continuations(c.run.id)[0].token
        result = c.resume(token, True, responder="owner-1", channel="slack")
        assert result["ok"] and result["approved"]

    async def test_a_non_owner_channel_reply_is_refused_without_touching_the_token(self) -> None:
        c, _status = await _run_with({"timeout_secs": 0}, origin=OriginKind.CHAT)
        token = HI.list_continuations(c.run.id)[0].token
        refused = c.resume(token, True, responder="intruder", channel="slack")
        assert not refused["ok"] and refused["code"] == "WF_RESUME_NOT_OWNER"
        # The token survives, so the real owner can still answer.
        assert c.resume(token, True, responder="owner-1", channel="slack")["ok"]

    async def test_a_local_answer_needs_no_responder(self) -> None:
        c, _status = await _run_with({"timeout_secs": 0}, origin=OriginKind.CHAT)
        token = HI.list_continuations(c.run.id)[0].token
        assert c.resume(token, True)["ok"]


class TestControllerAlwaysAllow:
    async def test_always_allow_is_remembered_within_the_run(self) -> None:
        c, _status = await _run_with({"timeout_secs": 0}, origin=OriginKind.CHAT)
        token = HI.list_continuations(c.run.id)[0].token
        c.resume(token, True, always_allow=True)
        assert len(c._allow_memory) == 1

    async def test_a_rewind_clears_the_memory(self) -> None:
        """Otherwise it auto-approves the very step the user rewound to reconsider."""
        c, _status = await _run_with({"timeout_secs": 0}, origin=OriginKind.CHAT)
        token = HI.list_continuations(c.run.id)[0].token
        c.resume(token, True, always_allow=True)
        assert len(c._allow_memory) == 1
        c.submit_mutation([{"op": "rewind", "node_id": "approve"}], confirm=True)
        c._drain_mutations()
        assert len(c._allow_memory) == 0


class TestControllerClarification:
    async def test_an_action_asking_for_input_parks_the_run(self) -> None:
        """No pre-placed gate: the provider knows it needs input, the author does not."""

        class P:
            async def execute(self, cfg, ctx, timeout=30):
                class R:
                    success = True
                    stdout = '{"needs_input": {"kind": "choice", "prompt": "env?", '
                    stdout += '"choices": ["dev", "prod"]}}'
                    outcome = ""
                    error = ""
                    exit_code = 0
                    stderr = ""
                    agent_error = None

                return R()

        spec = {
            "name": "clar",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [{"kind": "action", "id": "a", "config": {"provider": "p"}}],
            },
        }
        run = store.create(WorkflowRun(id="", workflow_name="clar"))
        store.write_spec(run.id, spec)
        c = RunController(run, spec, services=EngineServices(get_provider=lambda n: P()))
        status = await c.run_to_completion(timeout=20)
        assert status == RunStatus.NEEDS_INPUT
        assert c.instances["root.children[0]"].state == InstanceState.WAITING
        assert c.run.attention["choices"] == ["dev", "prod"]

    async def test_a_normal_action_output_completes(self) -> None:
        class P:
            async def execute(self, cfg, ctx, timeout=30):
                class R:
                    success = True
                    stdout = '{"count": 3}'
                    outcome = ""
                    error = ""
                    exit_code = 0
                    stderr = ""
                    agent_error = None

                return R()

        spec = {
            "name": "ok",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [{"kind": "action", "id": "a", "config": {"provider": "p"}}],
            },
        }
        run = store.create(WorkflowRun(id="", workflow_name="ok"))
        store.write_spec(run.id, spec)
        c = RunController(run, spec, services=EngineServices(get_provider=lambda n: P()))
        assert await c.run_to_completion(timeout=20) == RunStatus.COMPLETE
