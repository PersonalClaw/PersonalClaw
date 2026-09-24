"""Tests for the llm_helpers module — shared LLM interaction utilities."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.acp.client import AcpError
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm_helpers import (
    PromptBusyExhaustedError,
    ToolApprovalPolicy,
    humanize_provider_error,
    parse_llm_json,
    parse_llm_json_list,
    save_conversation_turn,
    stream_and_collect,
)


class TestParseLlmJson:
    def test_valid_json(self) -> None:
        assert parse_llm_json('{"key": "value"}') == {"key": "value"}

    def test_json_with_fences(self) -> None:
        text = '```json\n{"key": "value"}\n```'
        assert parse_llm_json(text) == {"key": "value"}

    def test_json_with_plain_fences(self) -> None:
        text = '```\n{"key": "value"}\n```'
        assert parse_llm_json(text) == {"key": "value"}

    def test_empty_string(self) -> None:
        assert parse_llm_json("") is None

    def test_whitespace_only(self) -> None:
        assert parse_llm_json("   \n  ") is None

    def test_invalid_json(self) -> None:
        assert parse_llm_json("not json") is None

    def test_returns_none_for_list(self) -> None:
        assert parse_llm_json("[1, 2, 3]") is None

    def test_returns_none_for_string(self) -> None:
        assert parse_llm_json('"just a string"') is None

    def test_nested_fences(self) -> None:
        text = '```json\n{"code": "```"}\n```'
        # Should handle gracefully — the inner ``` gets split
        result = parse_llm_json(text)
        # May or may not parse, but should not raise
        assert result is None or isinstance(result, dict)

    def test_whitespace_around_json(self) -> None:
        text = '  \n  {"a": 1}  \n  '
        assert parse_llm_json(text) == {"a": 1}


class TestParseLlmJsonList:
    def test_valid_list(self) -> None:
        assert parse_llm_json_list('[{"title": "a"}]') == [{"title": "a"}]

    def test_list_with_fences(self) -> None:
        text = '```json\n[{"title": "a"}]\n```'
        assert parse_llm_json_list(text) == [{"title": "a"}]

    def test_empty_string(self) -> None:
        assert parse_llm_json_list("") is None

    def test_returns_none_for_dict(self) -> None:
        assert parse_llm_json_list('{"key": "value"}') is None

    def test_invalid_json(self) -> None:
        assert parse_llm_json_list("not json") is None


class TestSaveConversationTurn:
    def test_saves_user_and_assistant(self) -> None:
        log = MagicMock()
        save_conversation_turn(log, "key1", "hello", "world")
        assert log.append.call_count == 2
        log.append.assert_any_call("key1", "user", "hello", source_thread=None, source_user=None)
        log.append.assert_any_call(
            "key1", "assistant", "world", source_thread=None, source_user=None
        )

    def test_saves_with_provenance(self) -> None:
        log = MagicMock()
        save_conversation_turn(log, "key1", "hello", "world", source_thread="t1", source_user="u1")
        log.append.assert_any_call("key1", "user", "hello", source_thread="t1", source_user="u1")
        log.append.assert_any_call(
            "key1", "assistant", "world", source_thread="t1", source_user="u1"
        )

    def test_skips_empty_assistant(self) -> None:
        log = MagicMock()
        save_conversation_turn(log, "key1", "hello", "")
        assert log.append.call_count == 1
        log.append.assert_called_once_with(
            "key1", "user", "hello", source_thread=None, source_user=None
        )


class TestToolApprovalPolicy:
    def test_enum_values(self) -> None:
        assert ToolApprovalPolicy.AUTO_APPROVE.value == "auto_approve"
        assert ToolApprovalPolicy.REJECT_ALL.value == "reject_all"
        assert ToolApprovalPolicy.HOOK_BASED.value == "hook_based"


# ── Prompt-busy retry tests ──


def _make_provider(events=None, error=None):
    """Create a mock ModelProvider that yields events or raises."""
    provider = AsyncMock()
    provider.cancel = AsyncMock()
    provider.shutdown = AsyncMock()

    async def _stream(msg):
        if error:
            raise error
        for e in events or []:
            yield e

    provider.stream = _stream
    return provider


class TestStreamAndCollectPromptBusy:
    @pytest.mark.asyncio
    async def test_retries_on_prompt_busy_then_succeeds(self) -> None:
        """First call raises 'already in progress', second succeeds."""
        call_count = 0

        async def _stream(msg):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise AcpError("Prompt error: {'data': 'Prompt already in progress'}")
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="ok")
            yield LLMEvent(kind=EVENT_COMPLETE)

        provider = AsyncMock()
        provider.cancel = AsyncMock()
        provider.shutdown = AsyncMock()
        provider.stream = _stream

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await stream_and_collect(provider, "test")

        assert result == "ok"
        assert call_count == 2
        provider.cancel.assert_awaited_once()
        provider.shutdown.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_shuts_down_provider_after_retries_exhausted(self) -> None:
        """After all retries fail, provider.shutdown() is called."""
        provider = _make_provider(error=AcpError("already in progress"))

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(PromptBusyExhaustedError),
        ):
            await stream_and_collect(provider, "test")

        provider.shutdown.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_busy_error_raises_immediately(self) -> None:
        """Non-busy AcpError is not retried."""
        provider = _make_provider(error=AcpError("some other error"))

        with pytest.raises(AcpError, match="some other error"):
            await stream_and_collect(provider, "test")

        provider.cancel.assert_not_awaited()
        provider.shutdown.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_normal_stream_no_retry(self) -> None:
        """Normal stream completes without retry."""
        provider = _make_provider(
            events=[
                LLMEvent(kind=EVENT_TEXT_CHUNK, text="hello"),
                LLMEvent(kind=EVENT_COMPLETE),
            ]
        )

        result = await stream_and_collect(provider, "test")

        assert result == "hello"
        provider.cancel.assert_not_awaited()


class TestOneShotCompletion:
    """``one_shot_completion`` must resolve through the use-case bridge (which reads
    the active model selection in active_models.json) — NOT the old config.json
    ``use_cases`` map, which is empty in the real app, so every classify silently
    fell back to a bare unconfigured provider."""

    @pytest.mark.asyncio
    async def test_resolves_via_use_case_bridge(self) -> None:
        from personalclaw import llm_helpers

        provider = _make_provider(
            events=[
                LLMEvent(kind=EVENT_TEXT_CHUNK, text='{"ok": true}'),
                LLMEvent(kind=EVENT_COMPLETE),
            ]
        )
        provider.start = AsyncMock()
        with patch(
            "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
            return_value=provider,
        ) as resolve:
            out = await llm_helpers.one_shot_completion("hi", use_case="background")
        assert out == '{"ok": true}'
        # "background" is a REAL chat sub-category now (MODEL-USE-CASES-V2) — it
        # resolves its own axis (→ chat fallback when unbound), never
        # "chat"/"code_tools" (which route the native agent runtime).
        assert resolve.call_args.args[0] == "background"
        provider.start.assert_awaited()
        provider.shutdown.assert_awaited()

    @pytest.mark.asyncio
    async def test_falls_back_to_first_registry_entry_when_bridge_fails(self) -> None:
        from personalclaw import llm_helpers

        provider = _make_provider(
            events=[
                LLMEvent(kind=EVENT_TEXT_CHUNK, text="hello"),
                LLMEvent(kind=EVENT_COMPLETE),
            ]
        )
        provider.start = AsyncMock()
        registry = MagicMock()
        entry = MagicMock()
        entry.name = "Bedrock"
        registry.list_entries.return_value = [entry]
        registry.build.return_value = provider
        with (
            patch(
                "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
                side_effect=RuntimeError("no active selection"),
            ),
            patch("personalclaw.llm.registry.get_default_registry", return_value=registry),
        ):
            out = await llm_helpers.one_shot_completion("hi")
        assert out == "hello"
        registry.build.assert_called_once()


# ── AG-9: native structured output reaches ONLY a natively capable provider ──
#
# The seam this exercises is half-owned by the apps repo: the ollama app already POPS an
# ``output_type`` build kwarg and normalizes it into ollama's own ``format=<schema>``.
# Core's half is the decision of WHO gets the key. Every assertion below is on the KWARGS
# DICT handed to the resolution seam, not on downstream behaviour, because "the provider
# constrained generation" is unobservable from core — what core can be held to is that it
# sent the constraint to exactly the providers that advertised the capability, and to no
# others. A kwarg core does not send cannot be honoured; a kwarg it sends to a provider
# that never advertised the capability rides into the request body unconsumed (both wire
# clients copy ``extra_options`` onto the request) and dies in the JSON encoder.


class _GradedStub:
    """The minimum ``one_shot_completion`` drives, with scripted response texts."""

    def __init__(self, texts: list[str] | None = None) -> None:
        self._texts = list(texts or ['{"ok": true}'])
        self.streamed = 0

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def stream(self, message: str):
        text = self._texts[min(self.streamed, len(self._texts) - 1)]
        self.streamed += 1
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=text)
        yield LLMEvent(kind=EVENT_COMPLETE)


def _graded_capability(type_: str, grade):
    from personalclaw.llm.capabilities import Capability, ProviderCapability

    return ProviderCapability(
        type=type_,
        capabilities=frozenset({Capability.CHAT, Capability.SUMMARIZATION}),
        supports_streaming=True,
        supports_tools=False,
        supports_embeddings=False,
        supports_vision=False,
        max_context_tokens=8192,
        structured_output=grade,
    )


@pytest.fixture
def graded_registry(monkeypatch):
    """A registry spanning all three grades plus one entry whose type is UNREGISTERED.

    Built fresh per test rather than mutating the process-global default registry — a
    leaked provider type would change another test's capability answer, and
    ``register_type`` raises on a duplicate so a leak also breaks the next registration.

    ``Broken`` is the raising-lookup case, and it is a REAL shape, not a contrivance:
    ``register_entry`` deliberately stores an entry whose type isn't registered yet
    (an app that loads after ``sync_entries_from_config``), so ``capability_of`` raising
    is what core sees for a provider whose app isn't loaded.
    """
    from personalclaw.llm.capabilities import StructuredOutput
    from personalclaw.llm.registry import ProviderEntry, ProviderRegistry

    reg = ProviderRegistry()
    reg.register_type(_graded_capability("ollama", StructuredOutput.JSON_SCHEMA), lambda **kw: None)
    reg.register_type(_graded_capability("cloudwire", StructuredOutput.NONE), lambda **kw: None)
    reg.register_type(_graded_capability("jsonmode", StructuredOutput.JSON_MODE), lambda **kw: None)
    reg.register_entry(ProviderEntry(name="Ollama", type="ollama", model="qwen3:8b"))
    reg.register_entry(ProviderEntry(name="Cloud", type="cloudwire", model="big-1"))
    reg.register_entry(ProviderEntry(name="Modey", type="jsonmode", model="mid-1"))
    reg.register_entry(ProviderEntry(name="Broken", type="not_registered_anywhere", model="x"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: reg)
    return reg


@pytest.fixture
def kwargs_seen(monkeypatch):
    """Record the kwargs of every resolution attempt; fail the ones the test names.

    ``fail_prefixes`` is how a test forces the CHAIN WALK to advance: the loop treats a
    resolution exception as a dead entry and moves to the next one, which is the only way
    to observe entry N and entry N+1 in the same call.
    """
    calls: list[dict] = []
    stubs: list[_GradedStub] = []
    fail_prefixes: list[str] = []
    texts: list[str] = ['{"ok": true}']

    def _fake_resolve(use_case, **kwargs):
        calls.append({"use_case": use_case, **kwargs})
        override = str(kwargs.get("model_override") or "")
        if any(override.startswith(p) for p in fail_prefixes):
            raise RuntimeError(f"pretend {override} is down")
        stub = _GradedStub(texts)
        stubs.append(stub)
        return stub

    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.resolve_provider_for_use_case", _fake_resolve
    )
    return SimpleNamespace(calls=calls, stubs=stubs, fail_prefixes=fail_prefixes, texts=texts)


def _pin_chain(monkeypatch, chain: list[str]) -> None:
    """Pin the use case's resolution CHAIN without touching ``active_models.json``.

    Patched rather than written to a temp home on purpose: the real reader consults
    ``config_dir()``, and a test that forgets to isolate it reads the developer's own
    bindings — which would make this test's chain shape depend on the machine.
    """
    monkeypatch.setattr("personalclaw.providers.use_cases.resolution_chain", lambda uc: list(chain))


class TestOneShotNativeStructuredOutput:
    @pytest.mark.asyncio
    async def test_json_schema_provider_receives_output_type(self, graded_registry, kwargs_seen):
        """The capable provider is SENT the constraint — the whole point of AG-9."""
        from personalclaw.llm_helpers import one_shot_completion

        out = await one_shot_completion(
            "hi", use_case="background", model="Ollama:qwen3:8b", output_type=dict
        )

        assert out == '{"ok": true}'
        assert len(kwargs_seen.calls) == 1
        assert kwargs_seen.calls[0]["output_type"] is dict

    @pytest.mark.asyncio
    async def test_none_provider_is_sent_nothing(self, graded_registry, kwargs_seen):
        """A provider advertising NONE must not see the key AT ALL.

        Asserted on the kwargs dict, not on behaviour: the observable consequence of
        getting this wrong is a corrupt request body far downstream, so the contract has
        to be stated where the decision is made. The rest of the kwargs are asserted too
        — the gate must withhold ``output_type`` without disturbing the budget.
        """
        from personalclaw.llm_helpers import one_shot_completion

        await one_shot_completion(
            "hi", use_case="background", model="Cloud:big-1", output_type=dict
        )

        assert "output_type" not in kwargs_seen.calls[0]
        assert kwargs_seen.calls[0]["max_tokens"] > 0

    @pytest.mark.asyncio
    async def test_json_mode_provider_is_sent_nothing(self, graded_registry, kwargs_seen):
        """JSON_MODE is DELIBERATELY excluded, so the exclusion is ratcheted here.

        ``JSON_MODE`` means OpenAI-wire ``response_format={"type": "json_object"}`` — a
        different request field that nothing derives from ``output_type``, and one that
        cannot express ``output_type=list`` at all. Loosening the gate to ``!= NONE``
        would send an unconsumed key; this test is what stops that from looking harmless.
        """
        from personalclaw.llm_helpers import one_shot_completion

        await one_shot_completion(
            "hi", use_case="background", model="Modey:mid-1", output_type=dict
        )

        assert "output_type" not in kwargs_seen.calls[0]

    @pytest.mark.asyncio
    async def test_mixed_chain_decides_per_entry(self, graded_registry, kwargs_seen, monkeypatch):
        """Entry 0 is NONE, entry 1 is JSON_SCHEMA — each gets its OWN answer.

        This is the test a decide-once-up-front implementation fails. Deciding before the
        walk would either withhold the constraint from the capable fallback or hand it to
        the incapable head; only a per-entry decision produces one call without the key
        followed by one call with it.
        """
        from personalclaw.llm_helpers import one_shot_completion

        _pin_chain(monkeypatch, ["Cloud:big-1", "Ollama:qwen3:8b"])
        kwargs_seen.fail_prefixes.append("Cloud")

        out = await one_shot_completion("hi", use_case="background", output_type=dict)

        assert out == '{"ok": true}'
        assert [c["model_override"] for c in kwargs_seen.calls] == [
            "Cloud:big-1",
            "Ollama:qwen3:8b",
        ]
        assert "output_type" not in kwargs_seen.calls[0]
        assert kwargs_seen.calls[1]["output_type"] is dict

    @pytest.mark.asyncio
    async def test_reverse_mixed_chain_also_decides_per_entry(
        self, graded_registry, kwargs_seen, monkeypatch
    ):
        """The mirror image: a capable HEAD that dies must not leak the key to the
        incapable fallback. Both orders are asserted because a wrong implementation that
        caches the first entry's answer passes one direction and fails the other."""
        from personalclaw.llm_helpers import one_shot_completion

        _pin_chain(monkeypatch, ["Ollama:qwen3:8b", "Cloud:big-1"])
        kwargs_seen.fail_prefixes.append("Ollama")

        await one_shot_completion("hi", use_case="background", output_type=dict)

        assert kwargs_seen.calls[0]["output_type"] is dict
        assert "output_type" not in kwargs_seen.calls[1]

    @pytest.mark.asyncio
    async def test_single_entry_plain_path_also_decides(
        self, graded_registry, kwargs_seen, monkeypatch
    ):
        """The third funnel: a ONE-entry chain takes the plain path, not the walk.

        Included because the plain path passes no ``model_override`` at all — it is the
        branch most easily left behind when a change is only tested through the pin.
        """
        from personalclaw.llm_helpers import one_shot_completion

        _pin_chain(monkeypatch, ["Ollama:qwen3:8b"])

        await one_shot_completion("hi", use_case="background", output_type=dict)

        assert len(kwargs_seen.calls) == 1
        assert "model_override" not in kwargs_seen.calls[0]
        assert kwargs_seen.calls[0]["output_type"] is dict

    @pytest.mark.asyncio
    async def test_raising_capability_lookup_degrades_and_still_completes(
        self, graded_registry, kwargs_seen
    ):
        """An unresolvable capability sends NOTHING and does not break the call.

        ``Broken``'s type is registered nowhere, so ``capability_of`` raises. The
        completion must still succeed on the universal parse-with-retry path — an
        introspection failure that turned into a failed completion would be a worse
        regression than the missing optimization.
        """
        from personalclaw.llm_helpers import one_shot_completion

        out = await one_shot_completion(
            "hi", use_case="background", model="Broken:x", output_type=dict
        )

        assert out == '{"ok": true}'
        assert "output_type" not in kwargs_seen.calls[0]

    @pytest.mark.asyncio
    async def test_unqualified_and_unknown_refs_degrade(self, graded_registry, kwargs_seen):
        """A bare id, a colon-bearing bare id, and an unknown provider all send nothing.

        ``gpt-oss:20b`` is the trap: it parses as a provider-qualified ref but its prefix
        names no entry, so treating the split as authoritative would look up a provider
        that does not exist. The ``get_entry`` miss is what makes it fall through.
        """
        from personalclaw.llm_helpers import one_shot_completion

        for pin in ("just-a-model-id", "gpt-oss:20b", "NoSuchProvider:m"):
            await one_shot_completion("hi", use_case="background", model=pin, output_type=dict)

        assert len(kwargs_seen.calls) == 3
        assert all("output_type" not in c for c in kwargs_seen.calls)

    @pytest.mark.asyncio
    async def test_output_type_none_changes_the_kwargs_not_at_all(
        self, graded_registry, kwargs_seen
    ):
        """``output_type=None`` sends byte-for-byte today's kwargs.

        Proven by DIFFING the two calls rather than hardcoding the budget: the untyped
        call must equal the typed call with ``output_type`` removed, so a stray extra
        kwarg on the default path can't hide behind a loose assertion.
        """
        from personalclaw.llm_helpers import one_shot_completion

        await one_shot_completion("hi", use_case="background", model="Ollama:qwen3:8b")
        await one_shot_completion(
            "hi", use_case="background", model="Ollama:qwen3:8b", output_type=dict
        )

        untyped, typed = kwargs_seen.calls
        assert "output_type" not in untyped
        assert {k: v for k, v in typed.items() if k != "output_type"} == untyped

    @pytest.mark.asyncio
    async def test_parse_retry_still_fires_for_a_natively_constrained_call(
        self, graded_registry, kwargs_seen
    ):
        """The constraint LAYERS with parse-and-retry; it does not replace it.

        A native constraint is a request, not a promise — constrained decoding is measured
        in this repo to return valid-but-empty output — so the retry must still fire on a
        provider that WAS sent the schema. If the parse were skipped because the provider
        "guarantees" the shape, the unparseable first response would be returned as the
        answer and a caught failure would have become a silent one.
        """
        from personalclaw.llm_helpers import one_shot_completion

        kwargs_seen.texts[:] = ["not json at all", '{"recovered": true}']

        out = await one_shot_completion(
            "hi", use_case="background", model="Ollama:qwen3:8b", output_type=dict
        )

        assert out == '{"recovered": true}'
        assert kwargs_seen.calls[0]["output_type"] is dict  # the constraint WAS sent
        assert kwargs_seen.stubs[0].streamed == 2  # and the net still fired

    @pytest.mark.asyncio
    async def test_contract_error_still_raised_for_a_natively_constrained_call(
        self, graded_registry, kwargs_seen
    ):
        """And when the retry also misses, the constrained call raises like any other."""
        from personalclaw.guardrails.failure import OutputContractError
        from personalclaw.llm_helpers import one_shot_completion

        kwargs_seen.texts[:] = ["still not json"]

        with pytest.raises(OutputContractError):
            await one_shot_completion(
                "hi", use_case="background", model="Ollama:qwen3:8b", output_type=dict
            )

        assert kwargs_seen.calls[0]["output_type"] is dict


class TestHumanizeProviderError:
    """humanize_provider_error — clean, actionable text for known provider failures,
    passthrough for the rest (never hide a real error)."""

    def test_billing_credits_mapped(self):
        raw = (
            "Error code: 400 - {'type': 'error', 'error': {'message': "
            "'Your credit balance is too low to access the Anthropic API.'}}"
        )
        out = humanize_provider_error(Exception(raw))
        assert "out of credits" in out.lower()
        assert "credit balance is too low" not in out  # raw blob removed

    def test_rate_limit_mapped(self):
        assert (
            "rate-lim" in humanize_provider_error(Exception("Error code: 429 rate limit")).lower()
        )

    def test_auth_mapped(self):
        assert "auth" in humanize_provider_error(Exception("401 invalid x-api-key")).lower()

    def test_model_not_found_mapped(self):
        assert "model id" in humanize_provider_error(Exception("model not found: x")).lower()

    def test_unrecognized_passes_through(self):
        # A real, novel error must NOT be hidden — returned verbatim.
        raw = "some brand new failure mode nobody mapped"
        assert humanize_provider_error(Exception(raw)) == raw

    def test_overlong_unrecognized_is_trimmed(self):
        raw = "x" * 900
        out = humanize_provider_error(Exception(raw))
        assert len(out) <= 501 and out.endswith("…")

    def test_none_safe(self):
        assert humanize_provider_error(None) == ""
