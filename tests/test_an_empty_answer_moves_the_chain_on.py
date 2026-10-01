"""A model that answers with nothing has failed, and the next model of its chain is asked.

Measured: the morning source digest came out "Digest synthesis was unavailable" because the local
background model spent its whole 4,096-token output budget and returned no text. The chain walk
took the empty string as an answer, so the next model of the chain (bound for exactly this) was
never asked, and the digest read the empty completion as its own failure.

These tests drive the real resolution seam and guard over fake models; no real model is called.
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
    """A model that answers every prompt with ``text`` — ``""`` for one that says nothing."""

    supports_tools = False

    def __init__(self, name: str, text: str, asked: list[str]) -> None:
        self.name = name
        self.text = text
        self.asked = asked

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], **_kw: Any):
        self.asked.append(self.name)
        if self.text:
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=self.text)
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=8_852, output_tokens=4_096)

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
def chain(monkeypatch) -> list[str]:
    """A background chain whose first model answers nothing and whose second answers."""
    asked: list[str] = []
    answers = {"here": "", "relay": "Three feeds published; two are worth reading."}
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **_kw: Any):
        return _Model(entry.name, answers[entry.name], asked)

    registry.register_type(_capability("offline-weights", in_process=True), _factory)
    registry.register_type(_capability("cloud-api"), _factory)
    registry.register_entry(ProviderEntry(name="here", type="offline-weights", model="tiny"))
    registry.register_entry(ProviderEntry(name="relay", type="cloud-api", model="swift"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr(
        "personalclaw.llm_helpers.use_case_chain", lambda uc: ["here:tiny", "relay:swift"]
    )
    return asked


def test_an_empty_answer_from_the_first_model_is_answered_by_the_next(chain):
    from personalclaw.llm_helpers import one_shot_completion

    answer = asyncio.run(one_shot_completion("Summarise these feeds.", use_case="background"))

    assert answer == "Three feeds published; two are worth reading."
    assert chain == ["here", "relay"]


def test_the_digest_is_synthesised_by_the_next_model(chain):
    """The measured digest, end to end: its narrative comes from the chain's next model."""
    from personalclaw.knowledge.source_digest import UNSYNTHESISED_BODY, _synthesise

    narrative = asyncio.run(_synthesise("Summarise these feeds.", None))

    assert narrative != UNSYNTHESISED_BODY
    assert narrative == "Three feeds published; two are worth reading."


def test_an_inbox_reply_the_first_model_left_empty_is_drafted_by_the_next(
    chain, monkeypatch, tmp_path
):
    """Measured: Generate draft on the Inbox page ran almost three minutes and showed an empty
    drafted reply, twice: the local model spent its whole output budget and wrote nothing, and the
    next model of the chain was never asked."""
    import time

    from personalclaw.inbox import InboxItem, InboxState, InboxStore
    from personalclaw.inbox_service import InboxService

    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: tmp_path)
    item = InboxItem(
        id="C1_1700000000.1",
        channel="C1",
        channel_name="#general",
        thread_ts=None,
        message="Can you send me the plan for Saturday?",
        sender_id="U2",
        sender_name="Sam",
        created_at=time.time(),
        can_reply=True,
    )
    store = InboxStore()
    store.items[item.id] = item
    svc = InboxService(state=InboxState(), store=store, user_name="Alex")

    asyncio.run(svc.draft_reply(item.id, instructions="Say the plan is on its way."))

    assert store.items[item.id].draft == "Three feeds published; two are worth reading."
    assert chain == ["here", "relay"]


def test_a_knowledge_node_moves_on_from_an_empty_answer(chain):
    from personalclaw.knowledge.pipeline.nodes._llm import complete_text

    text = asyncio.run(complete_text("background", "Extract the entities."))

    assert text == "Three feeds published; two are worth reading."
    assert chain == ["here", "relay"]


def test_the_model_that_stood_in_says_the_first_answered_nothing(chain, monkeypatch):
    from personalclaw.llm_helpers import one_shot_completion
    from personalclaw.providers import provider_bridge

    said: list[str] = []
    real = provider_bridge.stamp_substitution

    def _stamp(provider, substitution):
        said.append(substitution.sentence())
        real(provider, substitution)

    monkeypatch.setattr(provider_bridge, "stamp_substitution", _stamp)
    asyncio.run(one_shot_completion("Summarise these feeds.", use_case="background"))

    assert said == ["ran on relay:swift instead of here:tiny: it answered with nothing"]


def test_an_empty_answer_with_nothing_to_move_on_to_is_a_failure(chain):
    """A caller never has to tell an empty string from a failure: with one model, an empty answer
    raises, as a failed call does."""
    from personalclaw.llm_helpers import one_shot_completion

    with pytest.raises(Exception) as caught:
        asyncio.run(
            one_shot_completion("Summarise these feeds.", use_case="background", model="here:tiny")
        )

    assert "finished without answering" in str(caught.value)


def test_an_answer_in_the_wrong_shape_moves_on_and_the_last_one_is_still_readable(monkeypatch):
    """A typed call whose first model cannot produce the shape is answered by the next; when none
    can, the caller still gets the last model's text to salvage."""
    from personalclaw.guardrails.failure import OutputContractError
    from personalclaw.llm_helpers import one_shot_completion

    asked: list[str] = []
    answers = {"here": "Sure! Here is the label you asked for.", "relay": '{"label": "reply"}'}
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **_kw: Any):
        return _Model(entry.name, answers[entry.name], asked)

    registry.register_type(_capability("offline-weights", in_process=True), _factory)
    registry.register_type(_capability("cloud-api"), _factory)
    registry.register_entry(ProviderEntry(name="here", type="offline-weights", model="tiny"))
    registry.register_entry(ProviderEntry(name="relay", type="cloud-api", model="swift"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr(
        "personalclaw.llm_helpers.use_case_chain", lambda uc: ["here:tiny", "relay:swift"]
    )

    raw = asyncio.run(one_shot_completion("Label this.", use_case="background", output_type=dict))
    assert raw == '{"label": "reply"}'

    answers["relay"] = "I would call it a reply."
    with pytest.raises(OutputContractError) as caught:
        asyncio.run(one_shot_completion("Label this.", use_case="background", output_type=dict))
    assert caught.value.raw == "I would call it a reply."


def test_a_skill_review_no_model_answered_is_recorded_as_one_that_could_not_run(monkeypatch):
    """Measured: the skill review got seven empty answers, and the Proposals page read "the
    reviewer ran and had nothing worth proposing". When every model of the chain answers nothing,
    the pass is recorded as one that could not run, with why: never as a clean no-action."""
    from personalclaw import after_turn_review as atr
    from personalclaw.skills import proposals

    asked: list[str] = []
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **_kw: Any):
        return _Model(entry.name, "", asked)

    registry.register_type(_capability("offline-weights", in_process=True), _factory)
    registry.register_type(_capability("cloud-api"), _factory)
    registry.register_entry(ProviderEntry(name="here", type="offline-weights", model="tiny"))
    registry.register_entry(ProviderEntry(name="relay", type="cloud-api", model="swift"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr(
        "personalclaw.llm_helpers.use_case_chain", lambda uc: ["here:tiny", "relay:swift"]
    )

    asyncio.run(
        atr.run_skill_ladder_review(
            session_key="dashboard:chat-3",
            user_message="How do I rotate the feed cache?",
            assistant_text="Run the rotate command, then restart the poller.",
            loaded_skills=[],
        )
    )

    last = proposals.last_review()
    assert asked == ["here", "relay"]
    assert last is not None and last["verdict"] == "provider_error"
    assert last["detail"] == (
        "here:tiny failed before it replied (it answered with nothing), "
        "and so did relay:swift (it answered with nothing)"
    )
