"""A background chore whose first model is slow moves along its chain, and says what happened.

Measured on a home whose background chain was three models (a busy local model first):
knowledge insights gave up when the first model took longer than the pool's 180 s, although
the chain had already moved to the second, because one timeout wrapped the whole call; and
history consolidation gave up when the first model's guarded call timed out, because it never
let the background session it ran on fall back. Ten library items then read "insights: model
unavailable" with three models bound.

The model calls are stubbed at the resolution seam (``resolve_provider_for_use_case``) and at
``stream_and_collect``, the same seams the chain tests use; times are fractions of a second.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest
from fakes import BoundEmbedder

from personalclaw.llm_helpers import json_object_problem
from personalclaw.providers import use_cases as uc


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg, "config_path", lambda: tmp_path / "config.json")
    return tmp_path


def _chain(monkeypatch, behaviors: dict[str, object]):
    """A background chain of ``behaviors``' refs: each ref answers its string, raises its
    exception, or — for a float — takes that many seconds before answering "late"."""
    monkeypatch.setattr(uc, "_known_provider_names", lambda: {r.split(":")[0] for r in behaviors})
    uc.save_active_models({"background": list(behaviors)})
    calls: list[str] = []

    def fake_resolve(use_case, **kw):
        provider = AsyncMock()
        provider._ref = kw.get("model_override") or ""
        return provider

    async def fake_stream(provider, prompt, **_kw):
        calls.append(provider._ref)
        behavior = behaviors[provider._ref]
        if isinstance(behavior, Exception):
            raise behavior
        if isinstance(behavior, float):
            await asyncio.sleep(behavior)
            return "late"
        return behavior

    return calls, (
        patch(
            "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
            side_effect=fake_resolve,
        ),
        patch("personalclaw.llm_helpers.stream_and_collect", AsyncMock(side_effect=fake_stream)),
    )


@pytest.mark.asyncio
async def test_the_knowledge_pool_gives_each_model_its_own_time(isolated_store, monkeypatch):
    """The pool's timeout is each model's, so a first model still thinking hands the call to
    the second instead of ending it."""
    from personalclaw.knowledge.llm_pool import ProviderWorker

    calls, (resolve, stream) = _chain(
        monkeypatch, {"slow:model-a": 5.0, "next:model-b": '{"summary": "from the second model"}'}
    )
    with resolve, stream:
        out = await ProviderWorker().send_message("summarise this", timeout=0.2)

    assert out == '{"summary": "from the second model"}'
    assert calls == ["slow:model-a", "next:model-b"]


@pytest.mark.asyncio
async def test_models_that_are_all_too_slow_are_reported_as_a_timeout(isolated_store, monkeypatch):
    from personalclaw.knowledge.llm_pool import ProviderWorker, WorkerTimeout

    _calls, (resolve, stream) = _chain(monkeypatch, {"slow:a": 5.0, "slower:b": 5.0})
    with resolve, stream, pytest.raises(WorkerTimeout) as caught:
        await ProviderWorker().send_message("summarise this", timeout=0.2)

    message = str(caught.value)
    assert "no model answered within" in message
    assert "slow:a" in message and "slower:b" in message


@pytest.mark.asyncio
async def test_a_model_that_is_not_there_is_still_not_a_timeout(isolated_store, monkeypatch):
    """The vacuity arm: a failing model keeps the failure it always had."""
    from personalclaw.knowledge.llm_pool import ProviderWorker, WorkerError, WorkerTimeout

    _calls, (resolve, stream) = _chain(
        monkeypatch, {"a:m": ConnectionError("refused"), "b:m": ConnectionError("refused")}
    )
    with resolve, stream, pytest.raises(WorkerError) as caught:
        await ProviderWorker().send_message("summarise this", timeout=0.2)

    assert not isinstance(caught.value, WorkerTimeout)


def test_an_item_whose_models_were_too_slow_does_not_say_model_unavailable(tmp_path, monkeypatch):
    """The item's sentence says what happened, and names the fix it always did."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    from personalclaw.knowledge.llm_pool import WorkerTimeout
    from personalclaw.knowledge.pipeline import ensure_nodes_registered
    from personalclaw.knowledge.pipeline.runner import ingest_item
    from personalclaw.knowledge.store import KnowledgeStore

    class _SlowPool:
        async def send(self, prompt, timeout=None):
            raise WorkerTimeout("no model answered within 3 min (tried slow:a, slower:b)")

    ensure_nodes_registered()
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    iid = store.create_typed_item(item_type="note", title="N", content="body text")

    asyncio.run(ingest_item(store, iid, embedder=BoundEmbedder(), insights_pool=_SlowPool()))

    error = store.get_item(iid)["processing_error"] or ""
    assert error.startswith(
        "insights: no model answered within 3 min (tried slow:a, slower:b) "
        "(insights not refreshed — try regenerating)"
    )
    assert "model unavailable" not in error


def _timed_out() -> Exception:
    from personalclaw.guardrails.failure import ModelCallTimeout

    return ModelCallTimeout(
        use_case="background", provider="slow", model="model-a", waited_secs=300
    )


def test_history_consolidation_falls_back_down_its_chain_and_says_so(
    isolated_store, monkeypatch, caplog
):
    from personalclaw.history import ConversationLog, HistoryConsolidator

    calls, (resolve, stream) = _chain(
        monkeypatch, {"slow:model-a": _timed_out(), "next:model-b": '{"facts": ["kept"]}'}
    )
    consolidator = HistoryConsolidator(ConversationLog(isolated_store / "log"), memory=None)

    with resolve, stream, caplog.at_level(logging.WARNING, logger="personalclaw.llm_helpers"):
        result = asyncio.run(
            consolidator._call_llm(
                "consolidate this", "dashboard:chat-1", validate=json_object_problem
            )
        )

    assert result == {"facts": ["kept"]}
    assert calls == ["slow:model-a", "next:model-b"]
    said = [r.getMessage() for r in caplog.records]
    assert (
        "one_shot chain advance: background entry 0 (slow:model-a) failed (ModelCallTimeout) "
        "— trying next"
    ) in said, said


def test_a_consolidation_no_model_answered_is_one_line_with_what_happened(
    isolated_store, monkeypatch, caplog
):
    """When nothing in the chain answers, the log says so in a sentence, not a traceback of
    the HTTP client's frames."""
    from personalclaw.history import ConversationLog, HistoryConsolidator

    _calls, (resolve, stream) = _chain(
        monkeypatch, {"slow:model-a": _timed_out(), "next:model-b": _timed_out()}
    )
    consolidator = HistoryConsolidator(ConversationLog(isolated_store / "log"), memory=None)
    with resolve, stream, caplog.at_level(logging.WARNING, logger="personalclaw.history"):
        assert (
            asyncio.run(
                consolidator._call_llm("consolidate this", "k", validate=json_object_problem)
            )
            is None
        )

    (record,) = [r for r in caplog.records if "consolidation call failed" in r.getMessage()]
    assert record.exc_info is None
    assert record.getMessage().startswith(
        "LLM consolidation call failed: no model of its chain answered: slow:model-a failed "
        "before it replied"
    ), record.getMessage()
