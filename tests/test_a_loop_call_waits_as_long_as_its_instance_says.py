"""A loop's model call waits as long as its instance's Request Timeout says, and a timeout says why.

Measured: a Code loop on a local model failed after four cycles with "The turn failed with an
error PersonalClaw doesn't recognize. … Details: model call for use case 'loops' (provider
'ollama') exceeded 300s". The Ollama instance's Request Timeout was 600 s. Two things:

* the spend guard every automated call rides put its own fixed 300 s clock on the WHOLE call,
  under the instance's own setting — which says it is how long a request waits for the model to
  start answering, and then between the parts of its answer, and "not a cap on the whole
  answer" — so the setting the user raised governed nothing;
* the guard's timeout reached the chat as an error nobody recognized, naming neither the cause
  nor a fix.
"""

from __future__ import annotations

import asyncio
import sys

import httpx
import pytest

from personalclaw.apps.native_contract import (
    NATIVE_DIR,
    load_bundle_module,
    namespaced_module_name,
)
from personalclaw.guardrails.model_call import ModelCallGuard
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent, ModelProvider
from personalclaw.llm.registry import ProviderEntry
from personalclaw.llm_helpers import failure_clause, humanize_provider_error


class _Slow(ModelProvider):
    """Answers after *delay* seconds, or never."""

    def __init__(self, *, delay: float = 0.0, hang: bool = False) -> None:
        self._delay = delay
        self._hang = hang

    async def start(self):
        pass

    async def shutdown(self):
        pass

    async def stream(self, message):
        if self._hang:
            await asyncio.Event().wait()
        await asyncio.sleep(self._delay)
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="done")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=1, output_tokens=1)

    async def approve_tool(self, request_id):  # pragma: no cover - never called
        pass

    async def reject_tool(self, request_id):  # pragma: no cover - never called
        pass

    def context_usage_pct(self):
        return 0.0


async def _text(provider) -> str:
    return "".join([e.text async for e in provider.stream("hi") if e.kind == EVENT_TEXT_CHUNK])


@pytest.fixture(autouse=True)
def _audit_home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)


def _metered(inner, *, provider: str, model: str = "gemma4:12b", **kw):
    """The guard exactly as a loop worker's model gets it: the resolution seam's own wrap."""
    from personalclaw.providers.provider_bridge import metered

    return metered(inner, use_case="loops", provider_name=provider, model=model, **kw)


@pytest.mark.asyncio
async def test_a_loop_call_to_an_instance_with_a_request_timeout_is_bounded_by_it_alone(
    monkeypatch,
):
    from personalclaw.guardrails import model_call

    monkeypatch.setattr(model_call, "_DEFAULT_TIMEOUT_SECS", 0.05)
    inner = _Slow(delay=0.3)
    inner.request_timeout_secs = 600.0
    guard = _metered(inner, provider="ollama-own-wait")

    assert guard._timeout_secs == 0, "the guard put a clock of its own under the instance's"
    assert guard.request_timeout_secs == 600.0
    assert await asyncio.wait_for(_text(guard), timeout=10) == "done"


@pytest.mark.asyncio
async def test_a_provider_that_keeps_no_wait_of_its_own_is_stopped_at_the_guards_ceiling(
    monkeypatch,
):
    from personalclaw.guardrails import model_call

    monkeypatch.setattr(model_call, "_DEFAULT_TIMEOUT_SECS", 1.0)
    guard = _metered(_Slow(hang=True), provider="cloud-no-wait", model="m1")

    with pytest.raises(Exception) as caught:
        await asyncio.wait_for(_text(guard), timeout=10)

    sentence = humanize_provider_error(caught.value)
    assert "doesn't recognize" not in sentence, sentence
    assert "m1 on cloud-no-wait" in sentence and "1 second" in sentence, sentence
    assert "Loops" in sentence and "Settings → Models" in sentence, sentence
    # One reading of the failure: the clause a fallback line shows opens the same sentence.
    clause = failure_clause(caught.value).rstrip("…")
    assert sentence.lower().startswith(clause.lower()), (clause, sentence)


@pytest.mark.asyncio
async def test_a_callers_own_short_clock_is_kept():
    """A routed local attempt gives a stalled local model a short try so the next model in the
    chain answers quickly; that clock is the caller's, and the instance's own wait does not
    lift it."""
    inner = _Slow(delay=0.5)
    inner.request_timeout_secs = 600.0
    guard = ModelCallGuard(
        inner, use_case="loops", provider_name="ollama-routed", model="m", timeout_secs=0.05
    )

    with pytest.raises(Exception) as caught:
        await asyncio.wait_for(_text(guard), timeout=10)
    assert "did not finish answering" in humanize_provider_error(caught.value)


# ── the Ollama instance declares the wait it keeps ────────────────────────────


@pytest.fixture()
def ollama():
    name = namespaced_module_name("ollama-models", "provider")
    try:
        yield load_bundle_module(NATIVE_DIR / "ollama-models", "ollama-models", "provider")
    finally:
        sys.modules.pop(name, None)


def _instance(module, **options):
    provider = module._factory(
        entry=ProviderEntry(
            name="ollama",
            type="ollama",
            model="gemma4:12b",
            options={
                "endpoint": "http://127.0.0.1:11434",
                "default_model": "gemma4:12b",
                **options,
            },
        )
    )
    provider._client = httpx.AsyncClient(
        base_url="http://127.0.0.1:11434",
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )
    return provider


@pytest.mark.parametrize(("configured", "declared"), [("600", 600.0), ("1800", 1800.0)])
def test_an_ollama_instance_declares_its_request_timeout(ollama, configured, declared):
    assert _instance(ollama, timeout_secs=configured).request_timeout_secs == declared


def test_a_provider_that_declares_nothing_keeps_no_wait_of_its_own():
    assert _Slow().request_timeout_secs is None
