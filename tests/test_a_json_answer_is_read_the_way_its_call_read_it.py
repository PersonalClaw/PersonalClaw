"""A model's JSON answer is read one way: by the call that asked for it, by the caller's check of
its shape, and by the parser that uses it.

Measured: the morning triage ended Degraded with no proposal although a model had answered. The
one-shot call took an answer wrapped in a markdown fence as usable (its own reading strips one), the
proposal check then read the same text with ``json.loads`` and called it "not JSON", and the chain
moved on, in the end to a model that could only say "OK". Every reader of a JSON answer now reads it
through ``llm_helpers.parse_llm_json`` (an object), ``parse_llm_json_list`` (an array) or
``parse_llm_json_value`` (either): the whole answer when it is one JSON document of the kind, else
one inside a fence, else the first one in the text.

The chain tests drive the real resolution seam and guard over scripted models; no real model is
called.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest

from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.llm_helpers import parse_llm_json, parse_llm_json_list
from personalclaw.proactive.manifest import SOURCE_INBOX, CollectedItem, build_manifest
from personalclaw.routing import rates as rates_mod


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    from personalclaw.guardrails.breaker import reset_breakers

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    rates_mod._overlay_cache = None
    reset_breakers()
    yield tmp_path
    reset_breakers()
    rates_mod._overlay_cache = None


class _Model:
    """A model that answers each prompt with what the script says for its entry."""

    supports_tools = False

    def __init__(
        self, name: str, script: dict[str, Callable[[str], str]], asked: list[str]
    ) -> None:
        self.name = name
        self.script = script
        self.asked = asked

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], **_kw: Any):
        self.asked.append(self.name)
        prompt = str(messages[-1].get("content", "") if messages else "")
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=self.script[self.name](prompt))
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=663, output_tokens=212)

    async def stream(self, message: str):
        async for event in self.complete([{"role": "user", "content": message}]):
            yield event


def _capability(type_: str, **declared: Any) -> ProviderCapability:
    return ProviderCapability(
        type=type_,
        capabilities=frozenset({Capability.CHAT}),
        supports_streaming=True,
        supports_tools=False,
        supports_embeddings=False,
        supports_vision=False,
        max_context_tokens=32_768,
        **declared,
    )


@pytest.fixture
def chain(monkeypatch):
    """The background chain of the measured run: a model on this machine, then a cloud model.
    Set ``script[name]`` to what each answers a prompt with; ``asked`` records each call in order.
    """
    script: dict[str, Callable[[str], str]] = {}
    asked: list[str] = []
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **_kw: Any):
        return _Model(entry.name, script, asked)

    registry.register_type(_capability("offline-weights", in_process=True), _factory)
    registry.register_type(_capability("cloud-api"), _factory)
    registry.register_entry(ProviderEntry(name="here", type="offline-weights", model="tiny"))
    registry.register_entry(ProviderEntry(name="relay", type="cloud-api", model="swift"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr(
        "personalclaw.llm_helpers.use_case_chain", lambda uc: ["here:tiny", "relay:swift"]
    )
    return script, asked


def _fenced(payload: object) -> str:
    return "```json\n" + json.dumps(payload) + "\n```"


_PROPOSALS = {
    "proposals": [
        {
            "item_id": "1",
            "action_type": "reply_draft",
            "tier": "medium",
            "pattern_key": "reply_draft:sender:alex",
            "reasoning": "A review request is waiting on a reply.",
        }
    ]
}
_DISPOSITIONS = {
    "dispositions": [
        {"item_id": "1", "disposition": "propose", "rationale": "a review request", "rule": ""},
        {
            "item_id": "2",
            "disposition": "drop",
            "rationale": "a newsletter",
            "rule": "Skip newsletters",
        },
    ]
}
_REVIEW = CollectedItem(
    source=SOURCE_INBOX,
    source_id="inbox-a",
    title="please review my PR",
    sender="alex",
    ts="2026-08-24T02:00:00+00:00",
    # It takes a reply, so the reply proposal for it is one the digest offers.
    can_reply=True,
)
_NEWSLETTER = CollectedItem(
    source=SOURCE_INBOX,
    source_id="inbox-b",
    title="This week in the garden",
    sender="digest@example.com",
    ts="2026-08-24T03:00:00+00:00",
)


def _triage(items: list[CollectedItem], **kw: Any):
    from personalclaw.proactive.pipeline import run_triage

    return asyncio.run(run_triage(items, completion=None, deliver=lambda _digest: True, **kw))


# ── the morning triage ──────────────────────────────────────────────────────


def test_a_fenced_proposal_answer_is_a_proposal_not_a_refusal(chain):
    """The first model answers the proposals in the fence models put JSON in. The run proposes,
    and no other model is asked."""
    script, asked = chain
    script.update(here=lambda _p: _fenced(_PROPOSALS), relay=lambda _p: "OK")

    result = _triage([_REVIEW], gate_enabled=False)

    assert result.batch.degraded is False
    assert [p.item_id for p in result.proposals] == ["1"]
    assert not [r for r in result.refused if r.reason == "unparseable"]
    assert asked == ["here"]


def test_the_cloud_models_fenced_proposals_serve_when_the_local_model_says_nothing(chain):
    """The measured run: the model on this machine spent its output budget and said nothing, and
    the cloud model answered in a fence after a sentence. Those are the run's proposals."""
    script, asked = chain
    script.update(
        here=lambda _p: "",
        relay=lambda _p: "Here are my proposals.\n" + _fenced(_PROPOSALS),
    )

    result = _triage([_REVIEW], gate_enabled=False)

    assert result.batch.degraded is False
    assert [p.item_id for p in result.proposals] == ["1"]
    assert asked == ["here", "relay"], "a usable answer is not asked for a second time"


def test_the_gate_reads_a_fenced_dispositions_answer(chain):
    from personalclaw.proactive.gate import GateRule

    script, asked = chain

    def answer(prompt: str) -> str:
        return _fenced(_DISPOSITIONS if '"dispositions"' in prompt else _PROPOSALS)

    script.update(here=answer, relay=lambda _p: "OK")

    result = _triage([_REVIEW, _NEWSLETTER], rules=[GateRule(source="*", rule="Skip newsletters")])

    assert [item.source_id for item in result.gate.dropped] == ["inbox-b"]
    assert "gate returned nothing usable: failed open" not in result.notes
    assert [p.item_id for p in result.proposals] == ["1"]
    assert asked == ["here", "here"], "one call for the gate, one for the proposals"


_TAKEN = [
    _fenced(_PROPOSALS),
    "```\n" + json.dumps(_PROPOSALS) + "\n```",
    "Here are the proposals:\n" + _fenced(_PROPOSALS) + "\nThat is all.",
    "Proposals: " + json.dumps(_PROPOSALS) + " (one item needs you, see {notes})",
]


@pytest.mark.parametrize("answer", _TAKEN)
def test_the_proposal_check_and_parser_take_every_answer_the_call_takes(answer):
    from personalclaw.proactive.proposals import parse_proposals, proposals_problem

    assert parse_llm_json(answer) is not None, "the one-shot call reads it as an object"
    assert proposals_problem(answer) == ""
    batch = parse_proposals(answer, manifest=build_manifest([_REVIEW]))
    assert batch.degraded is False
    assert [p.item_id for p in batch.proposals] == ["1"]


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        ("OK", "no JSON object"),
        ("", "no JSON object"),
        ('{"proposals": [', "no JSON object"),
        ('["proposals"]', "no JSON object"),
        ('{"summary": "Nothing needs you."}', "no 'proposals' array"),
    ],
)
def test_the_proposal_check_still_refuses_an_answer_without_proposals(answer, why):
    from personalclaw.proactive.proposals import parse_proposals, proposals_problem

    assert proposals_problem(answer) == why
    batch = parse_proposals(answer, manifest=build_manifest([_REVIEW]))
    assert batch.degraded is True
    assert [(r.reason, r.detail) for r in batch.refused] == [("unparseable", why)]


@pytest.mark.parametrize(
    "answer",
    [
        _fenced(_DISPOSITIONS),
        "Dispositions follow.\n" + _fenced(_DISPOSITIONS),
        json.dumps(_DISPOSITIONS) + " (two items, {one} rule)",
    ],
)
def test_the_gate_check_and_parser_take_every_answer_the_call_takes(answer):
    from personalclaw.proactive.gate import dispositions_problem, parse_gate_output

    manifest = build_manifest([_REVIEW, _NEWSLETTER])

    assert dispositions_problem(answer) == ""
    outcomes = parse_gate_output(answer, manifest)
    assert {k: v.disposition.value for k, v in outcomes.items()} == {"1": "propose", "2": "drop"}


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        ("OK", "no JSON object"),
        ('{"verdicts": []}', "no 'dispositions' array"),
    ],
)
def test_the_gate_check_still_refuses_an_answer_without_dispositions(answer, why):
    from personalclaw.proactive.gate import dispositions_problem

    assert dispositions_problem(answer) == why


# ── the one reading ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("answer", "value"),
    [
        ('{"a": 1}', {"a": 1}),
        ('  {"a": 1}\n', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('```\n{"a": 1}\n```', {"a": 1}),
        ('Here it is:\n```json\n{"a": 1}\n```\nAnything else?', {"a": 1}),
        ('Answer: {"a": "a {brace} in a string"} (as asked {sic})', {"a": "a {brace} in a string"}),
        ('{"a": 1} {"b": 2}', {"a": 1}),
        ('First {not json}, then {"a": 1}.', {"a": 1}),
        ('[{"a": 1}]', {"a": 1}),
        # An object quoted before the answer does not beat the answer the model fenced.
        ('The item says {"a": 0}. My answer:\n```json\n{"a": 1}\n```', {"a": 1}),
    ],
)
def test_an_object_is_read_from_each_shape_models_answer_in(answer, value):
    assert parse_llm_json(answer) == value


@pytest.mark.parametrize(
    "answer", ["", "   ", "OK", "not json {", '"just text"', "[1, 2]", "{'a': 1}", None, 42]
)
def test_no_object_is_read_from_an_answer_that_holds_none(answer):
    assert parse_llm_json(answer) is None


@pytest.mark.parametrize(
    ("answer", "value"),
    [
        ('["a", "b"]', ["a", "b"]),
        ("[]", []),
        ('```json\n["a"]\n```', ["a"]),
        ('Sure:\n```json\n["a"]\n```', ["a"]),
        ('Here [see below]: ["a"]', ["a"]),
        ('{"items": ["a"]}', ["a"]),
    ],
)
def test_an_array_is_read_the_same_way(answer, value):
    assert parse_llm_json_list(answer) == value


@pytest.mark.parametrize(
    ("answer", "value"),
    [
        ('{"a": 1}', {"a": 1}),
        ("[1, 2]", [1, 2]),
        ('"just text"', "just text"),
        ("42", 42),
        ("```json\n42\n```", 42),
        ('Steps [1, 2], then {"a": 1}', [1, 2]),
        ("no JSON here", None),
    ],
)
def test_any_value_is_read_for_a_reader_that_takes_an_object_or_an_array(answer, value):
    from personalclaw.llm_helpers import parse_llm_json_value

    assert parse_llm_json_value(answer) == value


def test_an_answer_already_read_is_itself():
    """An injected completion may hand over the object it already read."""
    from personalclaw.llm_helpers import parse_llm_json_value

    assert parse_llm_json({"a": 1}) == {"a": 1}
    assert parse_llm_json_list(["a"]) == ["a"]
    assert parse_llm_json(["a"]) is None
    assert parse_llm_json_value({"a": 1}) == {"a": 1}


def test_an_answer_of_nested_or_unclosed_openers_is_no_value_and_raises_nothing():
    from personalclaw.llm_helpers import parse_llm_json_value

    assert parse_llm_json("{" * 50_000) is None
    assert parse_llm_json('{"a":' * 50_000) is None
    assert parse_llm_json_list("[" * 50_000) is None
    assert parse_llm_json_value('[{"a":' * 50_000) is None


# ── every other reader of a JSON answer ─────────────────────────────────────


def test_a_prompt_card_answered_in_a_fence_is_imported(chain):
    """The same split as the triage's: the call asks for an object, takes the fenced answer, and
    the card's own reading used to refuse it as "not JSON"."""
    from personalclaw.packs.prompt_cards import convert_card

    script, asked = chain
    card = {"target": "skill", "slug": "standup-notes", "name": "Standup notes"}
    script.update(here=lambda _p: _fenced(card), relay=lambda _p: "OK")

    assert asyncio.run(convert_card("Summarize my standup notes in three bullets.")) == card
    assert asked == ["here"]


def test_inbox_sorting_reads_a_fenced_answer_after_a_lead_in():
    from personalclaw.inbox_sorting import parse_verdicts, verdicts_problem

    answer = "Sorted:\n" + _fenced(
        {"verdicts": [{"message": 1, "classification": "needs_reply", "confidence": "high"}]}
    )

    assert verdicts_problem(answer, 1) == ""
    assert parse_verdicts(answer, 1) == {1: ("needs_reply", "high")}


def test_follow_ups_are_read_by_the_check_and_the_parser_alike():
    from personalclaw.dashboard.chat_followups import _followups_problem, _parse_followups

    answer = 'Sure, here you go:\n```json\n["Show me an example", "How do I test this?"]\n```'

    assert _followups_problem(answer) == ""
    assert _parse_followups(answer) == ["Show me an example", "How do I test this?"]
    # Nothing to suggest is an answer the prompt asks for.
    assert _followups_problem("[]") == ""
    assert _parse_followups("[]") == []
    assert _followups_problem("No follow-ups come to mind.") != ""


def test_suggestions_are_read_from_a_fenced_answer_after_a_lead_in():
    from personalclaw.suggestions import _parse_suggestions, _suggestions_problem

    answer = 'Here are some ideas:\n```json\n["Check CI status", "Review the open PR"]\n```'

    assert _suggestions_problem(answer) == ""
    assert _parse_suggestions(answer) == ["Check CI status", "Review the open PR"]


def test_the_skill_review_reads_a_decision_followed_by_braces_in_prose():
    from personalclaw import after_turn_review as atr

    answer = '{"action": "none"} (nothing here is a reusable {procedure})'

    async def completion(_prompt: str) -> str:
        return answer

    assert atr._ladder_problem(answer) == ""
    outcome, _detail, _summary = asyncio.run(
        atr._ladder_pass(
            session_key="dashboard:chat-3",
            user_message="How do I rotate the feed cache?",
            assistant_text="Run the rotate command, then restart the poller.",
            loaded_skills=[],
            completion=completion,
        )
    )
    assert outcome == "no_action"


def test_a_sync_merge_answered_after_a_lead_in_is_read():
    from personalclaw.durability import conflict_merge

    answer = "Here is the merge:\n" + _fenced(
        {"merged": {"title": "Groceries"}, "rationale": "the newer title"}
    )

    assert conflict_merge._merge_problem(answer) == ""
    assert conflict_merge._parse(answer) == ({"title": "Groceries"}, "the newer title")


class _Pool:
    """A knowledge pool whose model gives every prompt one answer."""

    def __init__(self, answer: str) -> None:
        self.answer = answer

    async def send(self, _prompt: str, timeout: float | None = None) -> str:
        return self.answer

    async def send_batch(self, prompts: list[str], timeout: float | None = None) -> list[str]:
        return [self.answer for _ in prompts]


def test_an_extraction_answered_as_a_one_item_list_is_read_by_the_check_and_the_parser_alike():
    """The check found the object inside the list and the parser did not, so the item kept an
    empty graph while the chain had taken the answer as usable."""
    from personalclaw.knowledge.extractor import EntityExtractor

    answer = json.dumps(
        [
            {
                "title": "Feed cache",
                "entities": [{"name": "feedsmith"}],
                "relations": [],
                "category": "runbook",
                "summary": "Rotate the cache.",
            }
        ]
    )

    assert EntityExtractor.answer_problem(answer) == ""
    result = asyncio.run(EntityExtractor(_Pool(answer)).extract("Rotate the feedsmith cache."))
    assert result["title"] == "Feed cache"
    assert result["entities"] == [{"name": "feedsmith"}]


def test_knowledge_insights_and_intents_read_an_object_followed_by_braces_in_prose():
    from personalclaw.knowledge.insights import InsightsExtractor
    from personalclaw.knowledge.intents import Intent, match_intent

    insights = 'Insights: {"summary": "The digest escapes twice."} (see {notes})'
    match = 'Result: {"relevant": true, "takeaway": "Release on Monday"} (per {the note})'

    assert InsightsExtractor.answer_problem(insights) == ""
    got = asyncio.run(InsightsExtractor(_Pool(insights)).extract("Feedsmith escapes HTML twice."))
    assert got["summary"] == "The digest escapes twice."
    found = asyncio.run(
        match_intent(
            Intent(id="releases", goal="Track release dates"),
            "The release is on Monday.",
            pool=_Pool(match),
        )
    )
    assert found is not None and found.takeaway == "Release on Monday"


def test_the_loop_composers_read_an_analysis_with_braces_around_it():
    from personalclaw.loop import classify as goal_classify
    from personalclaw.loop import code_classify

    goal = 'Analysis: {"title": "Regression test", "goal_type": "open_ended"} (rigor {low})'
    code = 'Plan {draft}: {"title": "Fix the double escape", "summary": "One escape too many."}'

    async def ask_goal(_prompt: str) -> str:
        return goal

    async def ask_code(_prompt: str) -> str:
        return code

    analysis = asyncio.run(goal_classify.classify("write a regression test", ask_goal))
    assert analysis.classified is True
    assert analysis.title == "Regression test"
    plan = asyncio.run(code_classify.classify("fix the double escape", ask_code))
    assert plan.title == "Fix the double escape"


def test_a_design_loop_reads_its_phases_after_a_bracketed_lead_in():
    from personalclaw.loop.kinds.design import DesignKind, _phases_problem

    rows = [{"stage": "discover", "title": "Discover", "objective": "Map the flows"}]
    answer = "Phases [draft]:\n" + _fenced(rows)

    async def ask(_prompt: str) -> str:
        return answer

    assert _phases_problem(answer) == ""
    assert asyncio.run(DesignKind()._plan_phases("a settings page", ask)) == rows


def test_the_scoping_pass_reads_its_questions_with_braces_around_them():
    from personalclaw.grill import assess_goal

    answer = 'Assessment: {"ambiguous": true, "questions": ["Which repository?"]} (I asked {one})'

    async def ask(_prompt: str) -> str:
        return answer

    assert asyncio.run(assess_goal("ship the fix", ask)) == (True, ["Which repository?"])


def test_a_workflow_infer_node_reads_its_object_with_braces_after_it():
    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.engine import dispatch_infer
    from personalclaw.workflows.models import InstanceState, Node

    async def answer(prompt, *, use_case="background", output_type=None):
        return 'Result: {"verdict": "PASS"} (see {notes})'

    node = Node.from_dict({"kind": "infer", "id": "i", "config": {"prompt": "p", "output": "json"}})
    result = asyncio.run(dispatch_infer(node, BindingContext(), completion=answer))

    assert result.state == InstanceState.DONE
    assert result.output == {"verdict": "PASS"}


def test_a_judge_reads_its_verdict_after_a_brace_in_its_reasoning():
    from personalclaw.workflows.judge_contract import parse_judge_json

    assert parse_judge_json('Scores {see below}: {"verdict": "PASS", "overall": 4}') == {
        "verdict": "PASS",
        "overall": 4,
    }
    assert parse_judge_json("PASS") is None, "a bare verdict word is still no answer"


def test_a_planners_step_file_is_read_after_a_brace_in_its_lead_in():
    from personalclaw.loop.code_plan_briefs import parse_steps_sentinel

    text = 'Plan {draft}: {"summary": "Fix it", "steps": [{"kind": "triage", "title": "Triage"}]}'

    assert parse_steps_sentinel(text) == (
        "Fix it",
        [{"kind": "triage", "title": "Triage", "objective": ""}],
    )


def test_a_workflow_planners_fenced_emission_is_read():
    from personalclaw.workflows.generation import parse_emission
    from personalclaw.workflows.revision import parse_revision

    spec = {"root": {"kind": "stage", "id": "write", "config": {"prompt": "write it"}}}
    patch = {"op": "remove", "node_id": "write"}

    assert parse_emission(_fenced(spec)) == (spec, "")
    patches, no_update = parse_revision("Here is the change:\n" + _fenced([patch]))
    assert (no_update, [(p.op, p.node_id) for p in patches]) == (False, [("remove", "write")])


def test_the_loop_judge_reads_its_verdict_after_a_brace_in_its_reasoning():
    from personalclaw.loop.judge import _parse_verdict
    from personalclaw.workflows.judge_contract import Verdict

    verdict = _parse_verdict('Verdict {see the notes}: {"done": true, "regressed": false}')

    assert verdict is not None
    assert verdict.verdict is Verdict.PASS


class _JudgeModel:
    served_ref = "relay:swift"

    def __init__(self, answer: str) -> None:
        self.answer = answer

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def stream(self, _prompt: str):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=self.answer)
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=10, output_tokens=5)


def test_the_eval_judge_reads_its_score_after_a_brace_in_its_reasoning():
    from personalclaw.eval.judge import LLMJudge

    judge = LLMJudge(
        lambda _key: _JudgeModel('Score {as asked}: {"score": 4, "reason": "on point"}'),
        prompt_template="{scenario_description} {criteria} {user_message} {assistant_response}",
    )

    async def scenario():
        await judge.start()
        try:
            return await judge.judge_turn("a turn", "be right", "hi", "hello")
        finally:
            await judge.shutdown()

    verdict = asyncio.run(scenario())
    assert (verdict.score, verdict.reason) == (4.0, "on point")
