"""Tests for the risk registry, autonomy floors and the confirmation matrix (UP-R4/R6, S44).

The scanner is measured against the SHIPPED library, not fixtures, because its most damaging failure
mode is the false positive: a risk scanner that fires on a template doing its job gets suppressed
wholesale, and the real findings go with it. Three false positives were found that way and are
encoded below as regression tests.

The other property under test is asymmetry. Every control here fails in a chosen direction — a
destructive action is never auto-approved in any mode, an unattended run still stops at HITL nodes,
and a single failed run RESETS earned trust rather than averaging it away.
"""

import ast
import inspect
from pathlib import Path
from typing import cast

import pytest

from personalclaw.tool_providers.base import RiskLevel
from personalclaw.workflows import autonomy as autonomy_mod
from personalclaw.workflows import bundled_defs
from personalclaw.workflows.autonomy import (
    MODE_ORDER,
    RISK_SIGNALS,
    SIGNALS_BY_NAME,
    Attention,
    ConfirmationRequest,
    ConfirmationType,
    Interrupt,
    Mode,
    TrustRecord,
    build_confirmations,
    commitment,
    compile_require_hitl,
    confirmation_policy,
    offer_autonomy,
    report_only_first,
    scan_risk,
    should_interrupt,
    type_attention,
    unattended_interrupts,
)

TEMPLATES = sorted(bundled_defs.template_names())


def prompt_spec(text: str) -> dict:
    return {"root": {"kind": "stage", "id": "a", "config": {"prompt": text}}}


def action_spec(provider: str, **args) -> dict:
    return {"root": {"kind": "action", "id": "a", "config": {"provider": provider, "with": args}}}


def spec_of(name: str) -> dict:
    definition = bundled_defs.read_template(name)
    root = definition.root
    return {"root": root.to_dict() if hasattr(root, "to_dict") else root}


# ── the registry is one place ──


def test_the_registry_reuses_the_engines_risk_gradient():
    """A second private vocabulary would drift from the gradient the approval UI already renders."""
    for signal in RISK_SIGNALS:
        assert isinstance(signal.level, RiskLevel)


def test_every_signal_states_a_CONSEQUENCE_not_just_a_name():
    """An informed-consent question built from a signal name is not informed. "destructive_op hit"
    is not a decision a user can make; "this can delete data that cannot be recovered" is."""
    for signal in RISK_SIGNALS:
        assert signal.consequence
        assert signal.name not in signal.consequence


def test_signals_are_addressable_by_name():
    assert set(SIGNALS_BY_NAME) == {s.name for s in RISK_SIGNALS}


# ── real dangers must fire ──


@pytest.mark.parametrize(
    "text,signal",
    [
        ("run rm -rf /var/data", "destructive_op"),
        ("execute TRUNCATE TABLE customers", "destructive_op"),
        ("run DROP TABLE orders", "destructive_op"),
        ("git push --force to the branch", "destructive_op"),
        ("rotate the api key for the service", "credentials_or_payment"),
        ("charge the customer card for the amount", "credentials_or_payment"),
        ("deploy the new build to production", "production_target"),
        ("write the config to production", "production_target"),
        ("schedule this to run every day", "schedule_creation"),
    ],
)
def test_a_real_danger_fires(text, signal):
    assert signal in {h.signal for h in scan_risk(prompt_spec(text))}


def test_a_dangerous_provider_fires_on_its_own():
    """`bash` is dangerous by capability, whatever its command says — and a command assembled from a
    binding cannot be scanned at plan time at all."""
    assert "destructive_op" in {h.signal for h in scan_risk(action_spec("bash", command="ls"))}


def test_a_hit_names_the_node_and_the_evidence():
    """ "This plan is risky" tells a reviewer to read all twelve stages; "stage 4 touches payments"
    tells them where to look."""
    hit = scan_risk(action_spec("bash", command="rm -rf /x"))[0]
    assert hit.node_id == "a"
    assert "bash" in hit.evidence


def test_the_scan_searches_action_ARGUMENTS():
    """A `bash` node's danger is entirely in its command, not in the word "bash" — a scan that read
    only prompts would miss every action node's actual payload."""
    hits = scan_risk(action_spec("run-script", script="drop table users"))
    assert any("drop table" in h.evidence for h in hits)


# ── the false positives that were measured ──


def test_the_truncate_binding_pipe_is_not_a_sql_truncate():
    """Measured: bare `\\btruncate\\b` matched `| truncate(1500)` and flagged THREE shipped
    templates
    as destructive for shortening a string in a prompt. A scanner that fires on the template's own
    plumbing is one whose findings get ignored, taking the real ones with it."""
    spec = prompt_spec("summarize {{nodes.x.output | truncate(1500)}}")
    assert "destructive_op" not in {h.signal for h in scan_risk(spec)}


def test_a_prompt_ABOUT_credentials_is_not_a_credential_action():
    """Measured: bare `\\bcredential\\b` fired on `audit-sweep`'s finder, whose whole job is to look
    FOR credential-handling problems. Reading about a risk is not taking one, and capping an
    auditing
    template for doing its job is a scanner arguing with the library it protects."""
    spec = prompt_spec("look for credential handling problems in the auth module")
    assert "credentials_or_payment" not in {h.signal for h in scan_risk(spec)}


@pytest.mark.parametrize(
    "text",
    [
        "write a report about our production architecture",
        "how does production differ from staging",
        "the production team reviewed the design",
    ],
)
def test_MENTIONING_production_is_not_acting_on_it(text):
    """A scanner that cries wolf on documentation is one whose real findings get waved through."""
    assert "production_target" not in {h.signal for h in scan_risk(prompt_spec(text))}


@pytest.mark.parametrize("name", TEMPLATES)
def test_no_shipped_template_trips_a_signal_by_accident(name):
    """The only acceptable hits on the library are CAPABILITY hits — a template that genuinely uses
    `bash`. A pattern hit on a shipped template is a false positive until proven otherwise, because
    these templates were reviewed and none of them deletes anything."""
    for hit in scan_risk(spec_of(name)):
        assert "uses the" in hit.evidence, f"{name}: pattern hit {hit.signal} — {hit.evidence}"


# ── attention typing ──


def test_a_destructive_node_is_typed_HITL():
    assert type_attention(action_spec("bash", command="rm -rf /x"))["a"] == Attention.HITL


def test_an_ordinary_stage_is_AFK():
    assert type_attention(prompt_spec("summarize the findings"))["a"] == Attention.AFK


def test_an_approval_gate_is_always_HITL():
    """It exists to pause for a person."""
    spec = {"root": {"kind": "gate", "id": "ask", "config": {"kind": "approval"}}}
    assert type_attention(spec)["ask"] == Attention.HITL


def test_an_authors_explicit_require_hitl_is_never_downgraded():
    """The author knows something the scanner does not, and a scanner that overrode them would make
    the declaration useless."""
    spec = {"root": {"kind": "stage", "id": "a", "config": {"prompt": "x", "require_hitl": True}}}
    assert type_attention(spec)["a"] == Attention.HITL


def test_containers_are_not_typed():
    """A sequence needs no attention — it is a scheduling policy, and typing it would put a stop on
    something that does no work."""
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [{"kind": "stage", "id": "a", "config": {"prompt": "x"}}],
        }
    }
    assert "r" not in type_attention(spec)


# ── compiling to require_hitl ──


def test_unattended_still_stops_at_HITL_nodes():
    """The whole point of typing them: an unattended grant is "do not ask me about the routine
    parts", not "do anything"."""
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                {"kind": "stage", "id": "safe", "config": {"prompt": "summarize"}},
                {
                    "kind": "action",
                    "id": "wipe",
                    "config": {"provider": "bash", "with": {"command": "rm -rf /x"}},
                },
            ],
        }
    }
    compiled = compile_require_hitl(spec, Mode.UNATTENDED)
    assert compiled["safe"] is False
    assert compiled["wipe"] is True


def test_frame_only_compiles_to_NOTHING_not_to_all_stops():
    """It means run NOTHING. Expressing that as "stop at every node" would start a run the user
    declined."""
    assert compile_require_hitl(prompt_spec("x"), Mode.FRAME_ONLY) == {}


def test_per_stage_stops_everywhere():
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                {"kind": "stage", "id": "a", "config": {"prompt": "x"}},
                {"kind": "stage", "id": "b", "config": {"prompt": "y"}},
            ],
        }
    }
    assert all(compile_require_hitl(spec, Mode.PER_STAGE).values())


def test_first_stage_runs_one_then_stops():
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                {"kind": "stage", "id": "a", "config": {"prompt": "x"}},
                {"kind": "stage", "id": "b", "config": {"prompt": "y"}},
                {"kind": "stage", "id": "c", "config": {"prompt": "z"}},
            ],
        }
    }
    compiled = compile_require_hitl(spec, Mode.FIRST_STAGE)
    assert compiled["a"] is False
    assert compiled["b"] is True and compiled["c"] is True


def test_publish_article_unattended_still_pauses_at_its_approval():
    """The end-to-end claim on a real template: earning unattended does not remove the gate the
    author put there for a person."""
    compiled = compile_require_hitl(spec_of("publish-article"), Mode.UNATTENDED)
    assert compiled["approve"] is True


# ── floors and the consent question ──


def test_a_destructive_plan_cannot_be_offered_unattended():
    offer = offer_autonomy(action_spec("bash", command="rm -rf /x"))
    assert Mode.UNATTENDED not in offer.offered
    assert offer.ceiling == Mode.PER_STAGE


def test_a_clean_plan_can_be_offered_unattended():
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                {"kind": "stage", "id": "a", "config": {"prompt": "summarize"}},
                {"kind": "gate", "id": "g", "config": {"kind": "judge", "prompt": "ok?"}},
            ],
        }
    }
    assert Mode.UNATTENDED in offer_autonomy(spec).offered


def test_asking_for_more_than_the_ceiling_costs_exactly_one_question():
    """Silent honor and silent refusal are BOTH failures. Honoring "unattended" on a plan that
    deletes production is the obvious one; quietly downgrading it is the one that makes the user
    distrust the control and stop reading it."""
    offer = offer_autonomy(action_spec("bash", command="rm -rf /x"), requested=Mode.UNATTENDED)
    assert offer.consent_question
    assert offer.consent_question.count("?") == 1


def test_the_consent_question_names_the_consequence_not_the_signal():
    offer = offer_autonomy(action_spec("bash", command="rm -rf /x"), requested=Mode.UNATTENDED)
    assert "cannot be recovered" in offer.consent_question
    assert "destructive_op" not in offer.consent_question


def test_asking_for_no_more_than_allowed_asks_nothing():
    """A control that asks about everything trains the user to click through, which is the failure a
    consent question exists to prevent."""
    offer = offer_autonomy(action_spec("bash", command="rm -rf /x"), requested=Mode.PER_STAGE)
    assert offer.consent_question == ""


def test_a_template_floor_is_reported_when_it_exceeds_the_risk_ceiling():
    """The floor wins — it is the author's considered minimum and the scan is a heuristic — but the
    conflict is recorded rather than resolved silently."""
    offer = offer_autonomy(action_spec("bash", command="rm -rf /x"), template_floor=Mode.UNATTENDED)
    assert any("floor" in reason for reason in offer.capped_by)


def test_the_offer_never_excludes_its_own_floor():
    offer = offer_autonomy(prompt_spec("x"), template_floor=Mode.PER_STAGE)
    assert Mode.PER_STAGE in offer.offered
    assert Mode.FRAME_ONLY not in offer.offered


def test_the_capped_reason_names_the_node():
    offer = offer_autonomy(action_spec("bash", command="rm -rf /x"))
    assert any("`a`" in reason for reason in offer.capped_by)


# ── the recommendation ──


def test_a_verified_plan_defaults_toward_unattended():
    """If it goes wrong, something catches it."""
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                {"kind": "stage", "id": "a", "config": {"prompt": "do it"}},
                {"kind": "gate", "id": "g", "config": {"kind": "judge", "prompt": "ok?"}},
            ],
        }
    }
    assert offer_autonomy(spec).recommended == Mode.UNATTENDED


def test_a_destructive_plan_never_defaults_to_unattended():
    assert offer_autonomy(action_spec("bash", command="rm -rf /x")).recommended != Mode.UNATTENDED


def test_earned_trust_raises_the_default_but_not_past_the_ceiling():
    """A template that has run cleanly ten times has earned a cheaper default, not permission to
    touch production unattended."""
    risky = action_spec("bash", command="rm -rf /x")
    assert offer_autonomy(risky, earned=Mode.UNATTENDED).recommended != Mode.UNATTENDED


# ── the confirmation matrix ──


@pytest.mark.parametrize("mode", MODE_ORDER)
def test_no_mode_auto_approves_destruction(mode):
    """`unattended` means "do not ask me about the routine parts", and there is no reading of it
    that
    includes this."""
    auto, reason = confirmation_policy(ConfirmationType.WRITE, RiskLevel.DESTRUCTIVE, mode)
    assert auto is False
    assert "destructive" in reason


def test_unattended_auto_approves_everything_short_of_destruction():
    for ctype in (
        ConfirmationType.READ,
        ConfirmationType.WRITE,
        ConfirmationType.OUTWARD,
        ConfirmationType.SPEND,
    ):
        auto, _ = confirmation_policy(ctype, RiskLevel.CAUTION, Mode.UNATTENDED)
        assert auto is True


def test_per_stage_auto_approves_reads_only():
    """A read has nothing to approve, and asking about it is the noise that makes a user stop
    reading
    the questions that matter."""
    assert confirmation_policy(ConfirmationType.READ, RiskLevel.SAFE, Mode.PER_STAGE)[0] is True
    assert confirmation_policy(ConfirmationType.WRITE, RiskLevel.SAFE, Mode.PER_STAGE)[0] is False


def test_a_confirmation_carries_a_resolvable_id():
    """An untyped "yes" arriving with no id is an answer to whatever asked most recently, which is
    how the wrong action gets approved."""
    spec = action_spec("bash", command="rm -rf /x")
    requests = build_confirmations(spec, Mode.UNATTENDED)
    assert requests
    assert all(r.request_id for r in requests)


def test_a_knowledge_read_is_classified_as_a_read():
    """Treating every action as a write would make a retrieve-heavy plan stop constantly for
    nothing."""
    spec = action_spec("knowledge-retrieve", query="x")
    assert build_confirmations(spec, Mode.PER_STAGE) == []


def test_a_knowledge_write_is_classified_as_a_write():
    spec = action_spec("knowledge-persist", title="x", content="y")
    assert build_confirmations(spec, Mode.PER_STAGE)


def test_confirmations_are_computed_at_plan_time():
    """ "This will stop you twice" is a fact a user should have before approving, not a
    discovery made
    while waiting."""
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                {
                    "kind": "action",
                    "id": "w1",
                    "config": {"provider": "knowledge-persist", "with": {}},
                },
                {
                    "kind": "action",
                    "id": "w2",
                    "config": {"provider": "knowledge-persist", "with": {}},
                },
            ],
        }
    }
    assert len(build_confirmations(spec, Mode.PER_STAGE)) == 2


# ── the two interrupts ──


def test_only_two_interrupts_exist():
    """ "Anything surprising" is not a taxonomy — it is a licence to stop whenever, which makes
    unattended mode a slower per-stage mode.

    Two, not three: WF2UNI-13 deleted `CONFLICTING` because no signal a `ConfirmationRequest`
    carries says "these requirements contradict each other", and a documented stop nothing can
    produce reads to an auditor like a stop that exists.
    """
    assert {i.value for i in Interrupt} == {"irreversible", "uninferable"}


def test_an_irreversible_action_interrupts_an_unattended_run():
    request = ConfirmationRequest(
        request_id="c1",
        node_id="a",
        confirmation_type=ConfirmationType.DESTRUCTIVE,
        risk=RiskLevel.DESTRUCTIVE,
        question="delete?",
    )
    stop, which, _ = should_interrupt(mode=Mode.UNATTENDED, confirmation=request)
    assert stop and which == Interrupt.IRREVERSIBLE


def test_an_outward_write_interrupts_even_though_it_is_only_CAUTION():
    """The deliberate asymmetry: a delayed message costs patience, a wrong one costs trust."""
    request = ConfirmationRequest(
        request_id="c1",
        node_id="a",
        confirmation_type=ConfirmationType.OUTWARD,
        risk=RiskLevel.CAUTION,
        question="send?",
    )
    stop, which, _ = should_interrupt(mode=Mode.UNATTENDED, confirmation=request)
    assert stop and which == Interrupt.IRREVERSIBLE


def test_a_read_does_not_interrupt():
    request = ConfirmationRequest(
        request_id="c1",
        node_id="a",
        confirmation_type=ConfirmationType.READ,
        risk=RiskLevel.SAFE,
        question="read?",
    )
    assert should_interrupt(mode=Mode.UNATTENDED, confirmation=request)[0] is False


def test_spend_proceeds_under_the_budget():
    """A run that stopped to ask permission for each model call would not be unattended."""
    request = ConfirmationRequest(
        request_id="c1",
        node_id="a",
        confirmation_type=ConfirmationType.SPEND,
        risk=RiskLevel.SAFE,
        question="spend?",
    )
    assert should_interrupt(mode=Mode.UNATTENDED, confirmation=request)[0] is False


def test_a_non_unattended_mode_stops_regardless():
    request = ConfirmationRequest(
        request_id="c1",
        node_id="a",
        confirmation_type=ConfirmationType.READ,
        risk=RiskLevel.SAFE,
        question="read?",
    )
    assert should_interrupt(mode=Mode.PER_STAGE, confirmation=request)[0] is True


# ── UNINFERABLE has a producer (WF2UNI-13) ──


CREDENTIAL_SPEC = {"root": {"kind": "stage", "id": "a", "config": {"prompt": "rotate the api key"}}}


def test_a_credential_node_carries_its_signal_name_through_the_confirmation():
    """The producer's input. `_classify_node` collapses every DESTRUCTIVE signal to the same
    `(DESTRUCTIVE, DESTRUCTIVE)` pair, so without the signal NAME riding along there is nothing to
    tell a credential ask apart from a deletion — and text-scanning the question for the word
    "credential" is a heuristic, not a signal."""
    [request] = build_confirmations(CREDENTIAL_SPEC, Mode.UNATTENDED)
    assert "credentials_or_payment" in request.signals
    assert request.to_dict()["signals"] == ["credentials_or_payment"]


def test_an_uninferable_credential_interrupts_an_unattended_run_through_the_real_path():
    """Driven from a spec through `build_confirmations`, not by hand-building a request: a producer
    that only fires for inputs a test constructs itself is still inert in production."""
    confirmations = build_confirmations(CREDENTIAL_SPEC, Mode.UNATTENDED)
    [verdict] = unattended_interrupts(confirmations)
    assert verdict["interrupts"] is True
    assert verdict["interrupt"] == Interrupt.UNINFERABLE.value
    assert verdict["node_id"] == "a"


def test_uninferable_relabels_a_stop_and_never_relaxes_one():
    """The whole safety argument for the new branch in one assertion: `credentials_or_payment` is a
    DESTRUCTIVE-level signal, so the request stops on risk alone. Checking the signal first changes
    WHICH interrupt is reported, never WHETHER the run stops."""
    [request] = build_confirmations(CREDENTIAL_SPEC, Mode.UNATTENDED)
    assert request.risk == RiskLevel.DESTRUCTIVE
    stripped = ConfirmationRequest(
        request_id=request.request_id,
        node_id=request.node_id,
        confirmation_type=request.confirmation_type,
        risk=request.risk,
        question=request.question,
        signals=(),
    )
    with_signal = should_interrupt(mode=Mode.UNATTENDED, confirmation=request)
    without_signal = should_interrupt(mode=Mode.UNATTENDED, confirmation=stripped)
    assert with_signal[0] is without_signal[0] is True
    assert with_signal[1] == Interrupt.UNINFERABLE
    assert without_signal[1] == Interrupt.IRREVERSIBLE


def test_the_counterfactual_reports_the_stops_unattended_would_skip():
    """What the surface is FOR: at `per_stage` a write stops, at `unattended` it does not, and the
    user picking between the two is choosing exactly that."""
    spec = {
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [
                {"kind": "action", "id": "w", "config": {"provider": "knowledge-persist"}},
                {"kind": "action", "id": "s", "config": {"provider": "send-message"}},
            ],
        }
    }
    verdicts = {
        v["node_id"]: v for v in unattended_interrupts(build_confirmations(spec, Mode.PER_STAGE))
    }
    assert verdicts["w"]["interrupts"] is False
    assert verdicts["s"]["interrupts"] is True
    assert verdicts["s"]["interrupt"] == Interrupt.IRREVERSIBLE.value


def test_the_plan_surface_carries_the_interrupt_verdicts():
    """The production reader. `_autonomy_surface` is the only consumer of this module in the run-up
    to approval; a verdict it does not emit is a verdict nobody sees."""
    from personalclaw.mcp_workflows import _autonomy_surface

    surface = _autonomy_surface({"inputs": {}, "root": CREDENTIAL_SPEC["root"], "metadata": {}})
    assert surface["unattended_interrupts"] == [
        {
            "request_id": "cr-a",
            "node_id": "a",
            "interrupts": True,
            "interrupt": "uninferable",
            "reason": surface["confirmations"][0]["question"],
        }
    ]


class TestInterruptExhaustiveness:
    """Two ratchets. A new `ConfirmationType` must declare whether an unattended run stops for it,
    and a new `Interrupt` must have something that produces it — the second is the one WF2UNI-13
    exists to install, because `UNINFERABLE` and `CONFLICTING` were both documented and
    unproducible."""

    @pytest.mark.parametrize("kind", list(ConfirmationType), ids=lambda k: k.value)
    def test_every_confirmation_type_has_a_branch(self, kind: ConfirmationType) -> None:
        request = ConfirmationRequest(
            request_id="c1",
            node_id="a",
            confirmation_type=kind,
            risk=RiskLevel.CAUTION,
            question="?",
        )
        stop, _which, reason = should_interrupt(mode=Mode.UNATTENDED, confirmation=request)
        assert isinstance(stop, bool) and reason

    def test_an_unhandled_confirmation_type_raises_rather_than_proceeding(self) -> None:
        """Proof the ratchet can fail. The dangerous default here is `return False` — a new type
        that fell through the old tail would have been waved through an unattended run."""
        request = ConfirmationRequest(
            request_id="c1",
            node_id="a",
            confirmation_type=cast(ConfirmationType, "future"),
            risk=RiskLevel.CAUTION,
            question="?",
        )
        with pytest.raises(AssertionError, match="no branch for ConfirmationType"):
            should_interrupt(mode=Mode.UNATTENDED, confirmation=request)

    def test_the_source_branches_on_every_confirmation_type_by_name(self) -> None:
        """Read out of the SOURCE: a fallthrough shared by two types would satisfy the parametrized
        test above while leaving the next type's semantics undeclared."""
        named = self._attributes_named_in("should_interrupt", "ConfirmationType")
        assert named == {member.name for member in ConfirmationType}

    def test_every_interrupt_member_is_produced(self) -> None:
        """The anti-inertness ratchet: an `Interrupt` member no code path returns is a stop that
        cannot happen, and it is indistinguishable — from the docstring alone — from one that can.
        """
        named = self._attributes_named_in("should_interrupt", "Interrupt")
        assert named == {member.name for member in Interrupt}

    @staticmethod
    def _attributes_named_in(function: str, enum_name: str) -> set[str]:
        source = Path(inspect.getsourcefile(autonomy_mod) or "").read_text(encoding="utf-8")
        fn = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == function
        )
        return {
            node.attr
            for node in ast.walk(fn)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == enum_name
        }


# ── earned trust ──


def test_trust_is_earned_over_several_clean_runs():
    """One lucky run does not grant it."""
    assert TrustRecord(template="t", clean_runs=1).earned == Mode.PER_STAGE
    assert TrustRecord(template="t", clean_runs=3).earned == Mode.UNATTENDED


def test_a_single_failure_RESETS_trust_rather_than_averaging_it():
    """A template that broke once is one whose next run deserves eyes, and averaging that
    away is how earned trust becomes a rubber stamp."""
    assert TrustRecord(template="t", clean_runs=20, failed_runs=1).earned is None


def test_an_unrun_template_has_earned_nothing():
    assert TrustRecord(template="t").earned is None


def test_a_first_run_is_report_only():
    """A template nobody has run is one nobody has seen the output of. The cost is one
    extra approval on first use; the alternative is discovering the behaviour by having
    it happen."""
    assert report_only_first(None) is True
    assert report_only_first(TrustRecord(template="t")) is True
    assert report_only_first(TrustRecord(template="t", clean_runs=1)) is False


def test_the_last_choice_is_remembered():
    """A user who picks per-stage three times running should not be asked a fourth."""
    record = TrustRecord(template="t", clean_runs=2, last_choice=Mode.PER_STAGE)
    assert record.to_dict()["last_choice"] == "per_stage"


# ── the combined commitment ──


def test_the_commitment_stamps_all_three_choices_together():
    """Unattended-in-a-sandbox and unattended-on-the-real-filesystem are different grants,
    and a user who approved the first has not approved the second."""
    stamped = commitment(mode=Mode.UNATTENDED, executor="claude", environment="worktree")
    assert stamped == {
        "mode": "unattended",
        "executor": "claude",
        "environment": "worktree",
        "stamped": True,
    }


# ── the wired plan tool ──


def test_the_plan_tool_ships_the_autonomy_surface():
    bundled_defs.register_bundled_provider()
    import json

    from personalclaw.mcp_workflows import _plan

    out = _plan({"goal": "implement the retry logic with tests"})
    body = json.loads(out[out.find("{") :])
    autonomy = body.get("autonomy") or {}
    assert autonomy.get("offered")
    assert autonomy.get("recommended")
    assert "require_hitl" in body
    # `code-project` uses `bash`, so unattended must not be on the table.
    assert "unattended" not in autonomy["offered"]
