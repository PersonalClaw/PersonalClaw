"""A stand-in for the ``anthropic`` SDK whose ``messages.stream`` has the REAL 1.x signature.

The fakes the older tests install take ``**kwargs``, so they accept any keyword a caller passes,
and that is how ``AnthropicProvider`` shipped sending ``temperature`` as an SDK keyword: every
test passed while ``anthropic`` 1.x (which dropped ``temperature``/``top_p``/``top_k`` from its
signatures) raised ``TypeError: stream() got an unexpected keyword argument 'temperature'`` on the
first sampled call. This fake's ``stream`` declares exactly the keyword-only parameters of
``anthropic==1.8.0``'s ``AsyncMessages.stream`` (read from the published wheel), so Python raises
that same ``TypeError`` for anything else, and each call is recorded as the keywords it received.

``extra_body`` is the one channel both SDK lines share for a request field the signature does not
name: 0.x and 1.x merge it into the JSON body as-is.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import Any

#: ``AsyncMessages.stream``'s keyword-only parameters in ``anthropic==1.8.0``, in order.
STREAM_KEYWORDS_1_8_0 = (
    "max_tokens",
    "messages",
    "model",
    "cache_control",
    "inference_geo",
    "metadata",
    "output_config",
    "output_format",
    "container",
    "service_tier",
    "stop_sequences",
    "system",
    "thinking",
    "tool_choice",
    "tools",
    "user_profile_id",
    "workspace_id",
    "extra_headers",
    "extra_query",
    "extra_body",
    "timeout",
)

_OMIT = object()

JUDGE_REPLY = '{"score": 0.5, "reason": "fine"}'


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages or []):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return " ".join(
                str(part.get("text", "")) for part in content if isinstance(part, dict)
            ).strip()
    return ""


def _events(text: str) -> list[Any]:
    usage = SimpleNamespace(input_tokens=11, output_tokens=3)
    return [
        SimpleNamespace(type="message_start", message=SimpleNamespace(usage=usage)),
        SimpleNamespace(
            type="content_block_start", index=0, content_block=SimpleNamespace(type="text")
        ),
        SimpleNamespace(
            type="content_block_delta", index=0, delta=SimpleNamespace(type="text_delta", text=text)
        ),
        SimpleNamespace(type="content_block_stop", index=0),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
            usage=SimpleNamespace(output_tokens=3),
        ),
    ]


class _Stream:
    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def __aenter__(self) -> "_Stream":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def __aiter__(self) -> "_Stream":
        self._iter = iter(self._events)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None


class FakeMessages:
    """``client.messages`` — records each call as the keywords it was given."""

    def __init__(self, sdk: "FakeAnthropicSDK") -> None:
        self._sdk = sdk

    def stream(  # noqa: PLR0913 — mirrors the real signature, keyword for keyword
        self,
        *,
        max_tokens: Any,
        messages: Any,
        model: Any,
        cache_control: Any = _OMIT,
        inference_geo: Any = _OMIT,
        metadata: Any = _OMIT,
        output_config: Any = _OMIT,
        output_format: Any = _OMIT,
        container: Any = _OMIT,
        service_tier: Any = _OMIT,
        stop_sequences: Any = _OMIT,
        system: Any = _OMIT,
        thinking: Any = _OMIT,
        tool_choice: Any = _OMIT,
        tools: Any = _OMIT,
        user_profile_id: Any = _OMIT,
        workspace_id: Any = _OMIT,
        extra_headers: Any = _OMIT,
        extra_query: Any = _OMIT,
        extra_body: Any = _OMIT,
        timeout: Any = _OMIT,
    ) -> _Stream:
        bound = dict(locals())
        bound.pop("self")
        given = {k: v for k, v in bound.items() if v is not _OMIT}
        # A snapshot, as the real SDK serializes the request at call time: `stream()` hands over
        # the provider's own history list, which `shutdown()` later clears.
        given["messages"] = [dict(m) for m in given["messages"]]
        self._sdk.calls.append(given)
        prompt = _last_user_text(given["messages"])
        text = self._sdk.reply_to(prompt)
        return _Stream(_events(text))


class FakeAnthropicSDK:
    """The installed fake: ``calls`` is every ``messages.stream`` call, as its keywords."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.clients: list[dict[str, Any]] = []
        self.sample_prompt = ""

    def reply_to(self, prompt: str) -> str:
        return "Blue" if self.sample_prompt and prompt == self.sample_prompt else JUDGE_REPLY

    def sample_calls(self) -> list[dict[str, Any]]:
        """The calls whose last user turn IS the sampling prompt (not a judge pass)."""
        return [c for c in self.calls if _last_user_text(c["messages"]) == self.sample_prompt]

    def module(self) -> types.ModuleType:
        sdk = self

        class AsyncAnthropic:
            def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
                sdk.clients.append({"api_key": api_key, "base_url": base_url})
                self.messages = FakeMessages(sdk)

            async def close(self) -> None:
                return None

        fake = types.ModuleType("anthropic")
        fake.AsyncAnthropic = AsyncAnthropic  # type: ignore[attr-defined]
        return fake


def install(monkeypatch: Any) -> FakeAnthropicSDK:
    """Put a fresh fake ``anthropic`` into ``sys.modules`` for this test and return it."""
    sdk = FakeAnthropicSDK()
    monkeypatch.setitem(sys.modules, "anthropic", sdk.module())
    return sdk
