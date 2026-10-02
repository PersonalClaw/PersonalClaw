"""An answer in the wrong shape is the model failing the call, and the chain's next model answers.

Measured: the morning triage's proposal step ran 150 s on the local background model and came
back as JSON with no ``proposals`` array. The call took it as an answer (it parsed), the run went
Degraded, and the next model of the chain, bound for exactly this, was never asked.

The caller names the shape it needs inside the one-shot call (``validate=``, or ``expecting`` for a
call made through a completion function it was handed), and the chain decides. These tests drive
the real resolution seam and guard over fake models; no real model is called.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
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
    """A model that gives every prompt the answer its entry was set up with."""

    supports_tools = False

    def __init__(self, name: str, answers: dict[str, str], asked: list[str]) -> None:
        self.name = name
        self.answers = answers
        self.asked = asked

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], **_kw: Any):
        self.asked.append(self.name)
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=self.answers[self.name])
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=900, output_tokens=120)

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
    """A background chain of a local model and the cloud model after it. Set ``answers`` to say
    what each answers; ``asked`` records each call in order."""
    answers: dict[str, str] = {}
    asked: list[str] = []
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **_kw: Any):
        return _Model(entry.name, answers, asked)

    registry.register_type(_capability("offline-weights", in_process=True), _factory)
    registry.register_type(_capability("cloud-api"), _factory)
    registry.register_entry(ProviderEntry(name="here", type="offline-weights", model="tiny"))
    registry.register_entry(ProviderEntry(name="relay", type="cloud-api", model="swift"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr(
        "personalclaw.llm_helpers.use_case_chain", lambda uc: ["here:tiny", "relay:swift"]
    )
    return answers, asked


def _proposals_problem(text: str) -> str:
    import json

    try:
        payload = json.loads(text)
    except ValueError:
        return "not JSON"
    return "" if isinstance(payload.get("proposals"), list) else "no 'proposals' array"


def test_valid_json_in_the_wrong_shape_is_answered_by_the_next_model(chain):
    from personalclaw.llm_helpers import one_shot_completion

    answers, asked = chain
    answers.update(here='{"summary": "Nothing needs you."}', relay='{"proposals": []}')

    answer = asyncio.run(
        one_shot_completion(
            "Propose.", use_case="background", output_type=dict, validate=_proposals_problem
        )
    )

    assert answer == '{"proposals": []}'
    assert asked == ["here", "relay"], "the model that missed is not asked twice"


def test_the_last_model_is_reminded_once_and_its_miss_says_what_was_wrong(chain):
    from personalclaw.guardrails.failure import OutputContractError
    from personalclaw.llm_helpers import one_shot_completion

    answers, asked = chain
    answers.update(here='{"summary": "Nothing needs you."}')

    with pytest.raises(OutputContractError) as caught:
        asyncio.run(
            one_shot_completion(
                "Propose.", use_case="background", model="here:tiny", validate=_proposals_problem
            )
        )

    assert asked == ["here", "here"]
    assert caught.value.why == "no 'proposals' array"
    assert caught.value.raw == '{"summary": "Nothing needs you."}'


def test_the_triage_proposals_come_from_the_next_model(chain):
    """The measured run, end to end: the proposal step's answer comes from the chain's next model,
    and the digest is not degraded."""
    from personalclaw.proactive.manifest import SOURCE_INBOX, CollectedItem
    from personalclaw.proactive.pipeline import run_triage

    answers, asked = chain
    answers.update(here='{"summary": "Nothing needs you."}', relay='{"proposals": []}')
    items = [
        CollectedItem(
            source=SOURCE_INBOX,
            source_id="inbox-b",
            title="please review my PR",
            sender="alex",
            ts="2026-08-24T02:00:00+00:00",
        )
    ]

    result = asyncio.run(
        run_triage(items, gate_enabled=False, completion=None, deliver=lambda _digest: True)
    )

    assert result.batch.degraded is False
    assert asked == ["here", "relay"]


def test_a_schedule_the_first_model_could_not_write_comes_from_the_next(chain, monkeypatch):
    from personalclaw.nl_to_cron import nl_to_cron

    answers, asked = chain
    answers.update(here="Every Monday at quarter past three.", relay="15 15 * * 1")

    schedule = asyncio.run(nl_to_cron("every Monday at 15:15", zone="UTC"))

    assert schedule.expr == "15 15 * * 1"
    assert asked == ["here", "relay"]


def test_the_loop_composers_analysis_comes_from_the_next_model(chain):
    from personalclaw.loop import classify as goal_classify

    answers, asked = chain
    answers.update(
        here='["write", "a", "test"]',
        relay='{"title": "Regression test for the double escape", "goal_type": "open_ended"}',
    )

    async def ask(prompt: str) -> str:
        from personalclaw.llm_helpers import one_shot_completion

        return await one_shot_completion(prompt, use_case="background")

    result = asyncio.run(goal_classify.classify("write a regression test for the escape", ask))

    assert result.classified is True
    assert result.title == "Regression test for the double escape"
    assert asked == ["here", "relay"]


def test_the_skill_review_reads_the_next_models_decision(chain):
    from personalclaw import after_turn_review as atr

    answers, asked = chain
    answers.update(here="Looks like a fine turn to me.", relay='{"action": "none"}')

    outcome, _detail, _summary = asyncio.run(
        atr._ladder_pass(
            session_key="dashboard:chat-3",
            user_message="How do I rotate the feed cache?",
            assistant_text="Run the rotate command, then restart the poller.",
            loaded_skills=[],
            completion=None,
        )
    )

    assert outcome == "no_action"
    assert asked == ["here", "relay"]


def test_knowledge_insights_come_from_the_next_model(chain):
    from personalclaw.knowledge.insights import InsightsExtractor
    from personalclaw.knowledge.llm_pool import LLMPool

    answers, asked = chain
    answers.update(
        here="I found two points worth keeping.",
        relay='{"summary": "The digest escapes twice.", "key_points": ["Remove one escape"]}',
    )

    async def scenario() -> dict:
        pool = LLMPool(pool_size=1)
        try:
            return await InsightsExtractor(pool).extract("Feedsmith escapes HTML twice.")
        finally:
            await pool.shutdown()

    insights = asyncio.run(scenario())

    assert insights["summary"] == "The digest escapes twice."
    assert asked == ["here", "relay"]


#: (file, function) of every one-shot call whose caller reads a structured answer. Each names the
#: shape inside the call, so the chain moves on from a model that missed it: ``validate=`` on a
#: direct call, ``expecting(...)`` around a call made through a completion function it was handed.
STRUCTURED = [
    ("proactive/pipeline.py", "run_triage"),  # the triage gate and its proposals
    ("nl_to_cron.py", "nl_to_cron"),  # a schedule
    ("knowledge/insights.py", "extract"),  # an item's insights
    ("knowledge/extractor.py", "extract"),  # an item's entities
    ("knowledge/extractor.py", "extract_batch"),
    ("knowledge/intents.py", "match_intent"),  # whether an intent matches
    ("loop/classify.py", "classify"),  # a goal loop's analysis
    ("loop/code_classify.py", "classify"),  # a code loop's analysis
    ("loop/kinds/design.py", "_plan_phases"),  # a design loop's phases
    ("grill.py", "assess_goal"),  # clarifying questions
    ("grill.py", "grill"),  # the decomposition
    ("after_turn_review.py", "_ladder_pass"),  # the skill review's decision
    ("durability/conflict_merge.py", "draft_proposals"),  # a sync conflict's merge
    ("inbox_sorting.py", "_sort"),  # a batch of inbox messages' verdicts
]


@pytest.mark.parametrize(("path", "function"), STRUCTURED)
def test_a_structured_caller_names_its_shape_inside_the_call(path, function):
    import ast
    from pathlib import Path

    tree = ast.parse(
        (Path(__file__).resolve().parents[1] / "src" / "personalclaw" / path).read_text("utf-8")
    )
    bodies = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function
    ]
    assert bodies, f"{path}:{function} is gone; update the census"
    names_shape = False
    for body in bodies:
        for node in ast.walk(body):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if name == "expecting":
                    names_shape = True
                if name == "one_shot_completion" and any(
                    k.arg == "validate" for k in node.keywords
                ):
                    names_shape = True
    assert names_shape, f"{path}:{function} reads a structured answer without naming its shape"
