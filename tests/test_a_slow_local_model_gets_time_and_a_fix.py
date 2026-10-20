"""A local model is given the time to read the prompt, and a timeout says what to change.

Measured on a first chat turn against a local 12B model through the Ollama app: the model needed
about 125 s to start answering the product's own ~80 KB request, and the instance's Request
Timeout was 120 s. The turn failed, the native loop resent the identical request (another 120 s,
failed again), and the chat said "The model provider at <its address> did not answer in time,
so the request timed out. Wait a moment and try again, or pick a different model." — no word of
the setting that was the fix, or where it is.

Four rules pinned here:

* the default fits a model reading a long conversation (and matches the manifest, which is what
  every instance created from the form stores), while an unreachable server still fails fast;
* a timeout before the first byte of the answer is the provider's own typed failure, whose
  sentence names the instance, the setting, and where it is;
* any other provider's timeout or refused connection is told as one by its type, whatever its
  words — a timeout that said "Request timed out." read as an error nobody recognized;
* the loop does not resend a request that timed out before its first token: the identical
  request is read from the start again and takes as long again.
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx
import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.apps.native_contract import (
    NATIVE_DIR,
    load_bundle_module,
    namespaced_module_name,
)
from personalclaw.guardrails.failure import FailureMode, FirstTokenTimeout
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.llm.registry import ProviderEntry
from personalclaw.llm_helpers import failure_clause, humanize_provider_error

APP_NAME = "ollama-models"
ENDPOINT = "http://127.0.0.1:11434"


@pytest.fixture()
def module():
    name = namespaced_module_name(APP_NAME, "provider")
    try:
        yield load_bundle_module(NATIVE_DIR / APP_NAME, APP_NAME, "provider")
    finally:
        sys.modules.pop(name, None)


def _provider(module, answer, **options):
    provider = module._factory(
        entry=ProviderEntry(
            name="ollama",
            type="ollama",
            model="gemma4:12b",
            options={"endpoint": ENDPOINT, "default_model": "gemma4:12b", **options},
        )
    )
    timeout = provider._client.timeout
    provider._client = httpx.AsyncClient(
        base_url=ENDPOINT, transport=httpx.MockTransport(answer), timeout=timeout
    )
    return provider


def _never_answers(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


async def _drain(events):
    return [ev async for ev in events]


# ── the default ───────────────────────────────────────────────────────────────


def test_the_default_gives_a_local_model_minutes_to_start_answering(module):
    provider = module._factory(
        entry=ProviderEntry(name="ollama", type="ollama", model="gemma4:12b", options={})
    )
    timeout = provider._client.timeout

    assert timeout.read >= 600
    # An address nothing answers at is still reported in seconds, not minutes.
    assert timeout.connect <= 10


def test_the_manifest_default_is_the_providers_default(module):
    manifest = json.loads((NATIVE_DIR / APP_NAME / "app.json").read_text())
    field = manifest["provider"]["settingsSchema"]["properties"]["timeout_secs"]

    assert field["default"] == module._DEFAULT_TIMEOUT
    # The sentence names the setting by the label the form shows for it.
    assert field["x-meta"]["label"] == module._TIMEOUT_SETTING


def test_a_configured_timeout_still_wins(module):
    provider = module._factory(
        entry=ProviderEntry(
            name="ollama", type="ollama", model="gemma4:12b", options={"timeout_secs": "900"}
        )
    )

    assert provider._client.timeout.read == 900


# ── the sentence ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["stream", "complete"])
def test_no_first_token_in_time_names_the_setting_and_where_it_is(module, path):
    provider = _provider(module, _never_answers, timeout_secs=120)
    events = (
        provider.stream("hello")
        if path == "stream"
        else provider.complete([{"role": "user", "content": "hello"}])
    )

    with pytest.raises(FirstTokenTimeout) as timed_out:
        asyncio.run(_drain(events))

    sentence = humanize_provider_error(timed_out.value)
    assert sentence == (
        "gemma4:12b on Ollama at 127.0.0.1:11434 did not start answering within 120 seconds, "
        "so the request was stopped. A model can take minutes to read a long conversation "
        "before it answers: raise Request Timeout on the “ollama” instance in Settings → "
        "Providers (under Advanced), or pick a faster model in the composer's model selector."
    )
    assert timed_out.value.mode is FailureMode.TIMEOUT
    # The line a fallback and a failed chain name each model by is the same reading.
    assert failure_clause(timed_out.value) == (
        "gemma4:12b on Ollama at 127.0.0.1:11434 did not start answering within 120 seconds, "
        "so the request was stopped"
    )


def test_in_a_room_the_fix_is_the_members_agent(module):
    provider = _provider(module, _never_answers, timeout_secs=120)
    with pytest.raises(FirstTokenTimeout) as timed_out:
        asyncio.run(_drain(provider.complete([{"role": "user", "content": "hello"}])))

    sentence = humanize_provider_error(timed_out.value, room_member="Researcher")
    assert sentence.endswith(
        "raise Request Timeout on the “ollama” instance in Settings → Providers (under "
        "Advanced), or give the Researcher agent a faster model on the Agents page."
    )


class _SdkTimeout(Exception):
    """A provider SDK's timeout: its own class and its own words, raised from the HTTP client's."""


def _raised_from(outer: Exception, cause: Exception) -> Exception:
    try:
        raise outer from cause
    except Exception as exc:  # noqa: BLE001 — the raised object is the fixture
        return exc


_OTHER = httpx.Request("POST", "http://127.0.0.1:8000/v1/chat/completions")


def test_another_providers_timeout_that_says_so_is_said_as_a_timeout():
    """Every provider's timeout, not only this app's, is told as one — by type, not by words."""
    exc = _raised_from(_SdkTimeout("Request timed out."), httpx.ReadTimeout("", request=_OTHER))

    assert humanize_provider_error(exc) == (
        "The model provider at 127.0.0.1:8000 did not answer in time, so the request timed out. "
        "Wait a moment and try again, or pick a different model."
    )


def test_a_refused_connection_with_words_is_said_as_one():
    exc = httpx.ConnectError("[Errno 61] Connection refused", request=_OTHER)

    assert humanize_provider_error(exc) == (
        "Couldn't connect to the model provider at 127.0.0.1:8000. Check that it is running "
        "and reachable, then try again."
    )


def test_a_stall_after_the_answer_started_is_not_called_a_slow_start(module):
    class _Stalls(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"message": {"role": "assistant", "content": "Hel"}, "done": false}\n'
            raise httpx.ReadTimeout("stalled")

    provider = _provider(module, lambda request: httpx.Response(200, stream=_Stalls()))

    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(_drain(provider.complete([{"role": "user", "content": "hello"}])))


# ── the loop does not resend it ───────────────────────────────────────────────


class _Scripted:
    """A model whose every call fails the same way before its first token."""

    supports_tools = False
    _model = "scripted"

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.calls += 1
        raise self.error
        yield  # pragma: no cover — makes this an async generator


def _runtime(model) -> NativeAgentRuntime:
    return NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=model,
    )


async def _turn(rt: NativeAgentRuntime) -> list[AgentEvent]:
    await rt.start()
    return [ev async for ev in rt.stream("summarise the pasted log")]


def test_a_request_that_never_started_answering_is_not_sent_again():
    model = _Scripted(
        FirstTokenTimeout(
            model="gemma4:12b",
            provider="Ollama",
            endpoint=ENDPOINT,
            waited_secs=120,
            setting="Request Timeout",
            instance="ollama",
        )
    )

    with pytest.raises(FirstTokenTimeout):
        asyncio.run(_turn(_runtime(model)))

    assert model.calls == 1


def test_a_passing_blip_still_gets_its_one_retry():
    """Control: the loop's one retry for a same-instant transient is untouched."""

    class _BlipsOnce(_Scripted):
        async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
            self.calls += 1
            if self.calls == 1:
                raise httpx.RemoteProtocolError("peer closed connection")
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="done")
            yield AgentEvent(kind=EVENT_COMPLETE)

    model = _BlipsOnce(RuntimeError("unused"))
    events = asyncio.run(_turn(_runtime(model)))

    assert model.calls == 2
    assert "done" in "".join(ev.text or "" for ev in events if ev.kind == EVENT_TEXT_CHUNK)
