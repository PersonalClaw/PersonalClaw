"""PCS-4 / §C4: the Anthropic adapter translates the NEUTRAL cache hint to wire syntax.

Core emits a vendor-neutral ``_cache_hint`` key (``llm/prompt_cache.py``, PCS-3) on
exactly one message. Anthropic realises a cache breakpoint as ``cache_control:
{"type": "ephemeral"}`` on a CONTENT BLOCK, so the translation must:

* put the marker on the hinted message's **LAST block** (not the first, not every one);
* make ``system=`` **BLOCK-SHAPED** when the hinted message is a hoisted system message
  (Anthropic serves ``system=`` ahead of ``messages[0]``, so that is where the stable
  head's breakpoint belongs — §C4);
* leave an **unhinted** request byte-for-byte what ships today, ``system=`` still a
  bare ``str`` (soul guardrail 2 — a non-caching provider is never penalised);
* treat a hint on an absent/empty span, or on the per-turn VOLATILE note, as a NO-OP.

The last section is the T2.5 rails sweep: vendor cache syntax lives in exactly ONE core
module. It is scoped to ACTIONABLE vendor literals (``cache_control``, ``cachePoint``,
``"type": "ephemeral"``) rather than the bare word "ephemeral", mirroring the existing
provider-boundary residue sweep's deliberate choice not to flag vendor *words* in prose —
core uses "ephemeral" for unrelated things (ephemeral ports, ephemeral sessions,
``skills/ephemeral.py``). A vacuity assertion pins that the patterns still match the one
file that is allowed to carry them, so the rail can never pass by matching nothing.
"""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from personalclaw.llm.anthropic import _VOLATILE_MESSAGE_KEY, _translate_messages
from personalclaw.llm.credentials import Credential
from personalclaw.llm.prompt_cache import CACHE_HINT_KEY, PromptCache, mark_cacheable_prefix

_EPHEMERAL = {"type": "ephemeral"}


def _hint(msg: dict, generation: int = 0) -> dict:
    """The neutral hint the marker layer applies, placed by hand on ``msg``."""
    return {**msg, CACHE_HINT_KEY: {"generation": generation}}


def _markers(obj: Any) -> list[Any]:
    """Every ``cache_control`` value reachable inside ``obj`` (blocks are nested)."""
    found: list[Any] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "cache_control":
                found.append(value)
            else:
                found.extend(_markers(value))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_markers(item))
    return found


# ── Marker placement: the LAST block of the hinted span ───────────────────────


def test_hinted_system_message_makes_system_block_shaped_with_the_marker():
    """§C4's core case: the marker forces ``system=`` from ``str`` to a text-block list."""
    messages = [_hint({"role": "system", "content": "stable head"})]

    system, out = _translate_messages(messages)

    assert isinstance(system, list)
    assert system == [{"type": "text", "text": "stable head", "cache_control": _EPHEMERAL}]
    # The marker is on the LAST (here: only) block, and nowhere in the message list.
    assert "cache_control" in system[-1]
    assert _markers(out) == []


def test_block_shaped_system_carries_the_same_text_as_the_string_form():
    """Guardrail 3: block-shaping must not change WHAT the model is told."""
    parts = [
        {"role": "system", "content": "alpha"},
        {"role": "system", "content": "beta"},
    ]
    plain_system, _ = _translate_messages(parts)
    hinted_system, _ = _translate_messages([parts[0], _hint(parts[1])])

    assert plain_system == "alpha\n\nbeta"
    assert isinstance(hinted_system, list)
    assert [b["text"] for b in hinted_system] == [plain_system]
    assert hinted_system[-1]["cache_control"] == _EPHEMERAL


def test_hinted_plain_user_message_becomes_one_marked_text_block():
    messages = [
        {"role": "user", "content": "first"},
        _hint({"role": "assistant", "content": "second"}),
    ]

    system, out = _translate_messages(messages)

    assert system == ""
    # Unhinted message keeps today's bare-string content.
    assert out[0] == {"role": "user", "content": "first"}
    assert out[1] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "second", "cache_control": _EPHEMERAL}],
    }
    assert len(_markers(out)) == 1


def test_marker_lands_on_the_last_block_not_the_first():
    """An assistant tool call yields [text, tool_use] — the marker goes on tool_use."""
    messages = [
        _hint(
            {
                "role": "assistant",
                "content": "let me check",
                "tool_calls": [
                    {
                        "id": "toolu_1",
                        "type": "function",
                        "function": {"name": "w", "arguments": '{"city":"sf"}'},
                    }
                ],
            }
        )
    ]

    _, out = _translate_messages(messages)

    blocks = out[0]["content"]
    assert [b["type"] for b in blocks] == ["text", "tool_use"]
    # Exact position: last block only.
    assert "cache_control" not in blocks[0]
    assert blocks[-1]["cache_control"] == _EPHEMERAL
    assert len(_markers(out)) == 1


def test_hinted_block_shaped_content_marks_the_last_block_without_mutating_the_caller():
    """A caller that already sends blocks keeps its blocks; only a COPY is marked."""
    caller_blocks = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    messages = [_hint({"role": "user", "content": caller_blocks})]

    _, out = _translate_messages(messages)

    assert out[0]["content"][0] == {"type": "text", "text": "a"}
    assert out[0]["content"][-1] == {"type": "text", "text": "b", "cache_control": _EPHEMERAL}
    # The caller's list and its final block dict are untouched.
    assert caller_blocks == [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]


def test_hinted_tool_result_marks_its_block():
    messages = [_hint({"role": "tool", "tool_call_id": "toolu_1", "content": "sunny"})]

    _, out = _translate_messages(messages)

    assert out[0]["content"][-1] == {
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": "sunny",
        "cache_control": _EPHEMERAL,
    }


# ── No-ops: a marker is never placed on nothing ──────────────────────────────


@pytest.mark.parametrize("content", ["", None])
def test_hint_on_an_empty_span_is_a_no_op(content):
    """No block exists to carry the marker, so none is emitted — for either role."""
    system, out = _translate_messages(
        [
            _hint({"role": "system", "content": content}),
            _hint({"role": "user", "content": content}),
        ]
    )

    assert system == ""
    assert isinstance(system, str)
    assert _markers(out) == []


def test_hint_on_the_volatile_note_is_ignored():
    """Per-turn content is not cacheable: a breakpoint there guarantees a miss."""
    messages = [
        {"role": "user", "content": "stable"},
        _hint({"role": "system", "content": "turn note", _VOLATILE_MESSAGE_KEY: True}),
    ]

    system, out = _translate_messages(messages)

    assert system == ""
    assert _markers(out) == []
    # PCS-1 relocation still holds: the note ships, at the tail.
    assert out[-1] == {"role": "user", "content": "turn note"}


def test_generation_never_reaches_the_wire():
    """``generation`` is loop bookkeeping; Anthropic has no field for it."""
    msg = {"role": "user", "content": "hello"}
    gen0 = _translate_messages([_hint(msg, 0)])
    gen7 = _translate_messages([_hint(msg, 7)])

    assert gen0 == gen7
    assert CACHE_HINT_KEY not in str(gen0)


def test_neutral_hint_key_never_reaches_the_wire():
    """The hint is consumed, not forwarded — no ``_cache_hint`` on any wire dict."""
    messages = mark_cacheable_prefix(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ],
        PromptCache.EXPLICIT,
    )

    system, out = _translate_messages(messages)

    assert CACHE_HINT_KEY not in str(system)
    for msg in out:
        assert CACHE_HINT_KEY not in msg


# ── The adapter's declared posture ───────────────────────────────────────────


def test_anthropic_declares_explicit(fake_anthropic_module):
    """The native loop reads this attr by getattr, exactly as it reads supports_tools."""
    from personalclaw.llm.anthropic import AnthropicProvider

    assert AnthropicProvider.prompt_cache is PromptCache.EXPLICIT
    inst = AnthropicProvider(model="claude-x", credential=_cred())
    assert getattr(inst, "prompt_cache", PromptCache.NONE) is PromptCache.EXPLICIT


# ── Request kwargs: byte-identical when unhinted (soul guardrail 2) ──────────


class _FakeStreamIter:
    def __init__(self, events: list[Any]) -> None:
        self._events = events

    def __aiter__(self) -> _FakeStreamIter:
        self._iter = iter(self._events)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _FakeStreamCM:
    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def __aenter__(self) -> _FakeStreamIter:
        return _FakeStreamIter(self._events)

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeMessages:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def stream(self, **kwargs: Any) -> _FakeStreamCM:
        self.calls.append(kwargs)
        return _FakeStreamCM([])


class _FakeAsyncAnthropic:
    def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
        self.messages = _FakeMessages()

    async def close(self) -> None:  # pragma: no cover - not driven here
        return None


@pytest.fixture
def fake_anthropic_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    fake = types.ModuleType("anthropic")
    fake.AsyncAnthropic = _FakeAsyncAnthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return fake


def _cred() -> Credential:
    return Credential(name="x", kind="api_key", secret="sk-test", source="env")


_CONVERSATION: list[dict] = [
    {"role": "system", "content": "be terse"},
    {"role": "user", "content": "weather in sf?"},
    {
        "role": "assistant",
        "content": "checking",
        "tool_calls": [
            {
                "id": "toolu_1",
                "type": "function",
                "function": {"name": "w", "arguments": '{"city":"sf"}'},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "toolu_1", "content": "sunny"},
]

# The exact kwargs this conversation produced BEFORE PCS-4, written out by hand from the
# pre-PCS-4 translation rules: system concatenated into a bare ``str``; plain messages as
# ``{role, content}``; tool_calls → [text, tool_use]; tool → a user turn of tool_result.
_PRE_PCS4_KWARGS: dict[str, Any] = {
    "model": "claude-x",
    "messages": [
        {"role": "user", "content": "weather in sf?"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "checking"},
                {"type": "tool_use", "id": "toolu_1", "name": "w", "input": {"city": "sf"}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "sunny"}],
        },
    ],
    "max_tokens": 4096,
    "system": "be terse",
}


async def _capture_kwargs(messages: list[dict]) -> dict[str, Any]:
    from personalclaw.llm.anthropic import AnthropicProvider

    provider = AnthropicProvider(model="claude-x", credential=_cred())
    fake = _FakeMessages()
    provider._client.messages = fake
    async for _ in provider.complete(messages):
        pass
    assert len(fake.calls) == 1
    return fake.calls[0]


@pytest.mark.asyncio
async def test_unhinted_request_kwargs_are_byte_identical_to_today(fake_anthropic_module):
    """No hint anywhere ⇒ exactly the pre-PCS-4 kwargs, ``system`` still a bare str."""
    kwargs = await _capture_kwargs(_CONVERSATION)

    assert kwargs == _PRE_PCS4_KWARGS
    assert type(kwargs["system"]) is str
    assert _markers(kwargs) == []
    assert "cache_control" not in str(kwargs)


@pytest.mark.asyncio
async def test_a_none_provider_posture_still_yields_the_same_kwargs(fake_anthropic_module):
    """The marker layer at PromptCache.NONE hands the list back untouched, so the
    request that reaches the wire is the pre-PCS-4 one."""
    kwargs = await _capture_kwargs(mark_cacheable_prefix(_CONVERSATION, PromptCache.NONE))

    assert kwargs == _PRE_PCS4_KWARGS


@pytest.mark.asyncio
async def test_hinted_request_differs_from_today_only_by_the_marked_block(
    fake_anthropic_module,
):
    """The EXPLICIT path adds a marker and nothing else.

    ``mark_cacheable_prefix`` picks the last non-tool, non-volatile message — here the
    assistant tool call — so the breakpoint lands on its trailing ``tool_use`` block.
    """
    hinted = mark_cacheable_prefix(_CONVERSATION, PromptCache.EXPLICIT)
    kwargs = await _capture_kwargs(hinted)

    assert _markers(kwargs) == [_EPHEMERAL]
    # system= stays a bare str: the hinted message is not a system message here.
    assert type(kwargs["system"]) is str
    assert kwargs["system"] == _PRE_PCS4_KWARGS["system"]
    # Strip the one added key and the request is byte-identical to today's.
    stripped = kwargs["messages"][1]["content"][-1].copy()
    stripped.pop("cache_control")
    assert stripped == _PRE_PCS4_KWARGS["messages"][1]["content"][-1]
    assert kwargs["messages"][0] == _PRE_PCS4_KWARGS["messages"][0]
    assert kwargs["messages"][2] == _PRE_PCS4_KWARGS["messages"][2]


@pytest.mark.asyncio
async def test_block_shaped_system_reaches_the_wire(fake_anthropic_module):
    """End-to-end: a hinted system message ships ``system=`` as a marked block list.

    A single stable system message IS the last non-tool, non-volatile message, so the
    neutral marker layer selects it without any hand-placement.
    """
    hinted = mark_cacheable_prefix(
        [{"role": "system", "content": "stable head"}], PromptCache.EXPLICIT
    )
    kwargs = await _capture_kwargs(hinted)

    assert kwargs["system"] == [
        {"type": "text", "text": "stable head", "cache_control": _EPHEMERAL}
    ]
    assert kwargs["messages"] == []
    assert _markers(kwargs) == [_EPHEMERAL]


# ── T2.5 rails sweep: vendor cache syntax lives in ONE core module ───────────

_CORE = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
#: The ONLY core file permitted to name Anthropic's cache syntax. `llm/anthropic.py` is
#: one of the two in-core protocol clients enumerated in
#: docs/architecture/provider-boundary.md, so this creates no new boundary exception.
_ALLOWED = {"llm/anthropic.py"}

#: ACTIONABLE vendor cache syntax — a wire key or a marker literal, never a bare word.
_VENDOR_CACHE_SYNTAX = [
    re.compile(r"cache_control"),
    re.compile(r"cachePoint"),  # Bedrock's shape — belongs in the bedrock app (PCS-8)
    re.compile(r"""["']type["']\s*:\s*["']ephemeral["']"""),
]


def _offenders(root: Path) -> list[str]:
    out: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if any(pat.search(text) for pat in _VENDOR_CACHE_SYNTAX):
            out.append(str(path.relative_to(root)))
    return out


def test_vendor_cache_syntax_only_in_the_anthropic_adapter():
    """A ``cache_control`` / ``cachePoint`` / ephemeral-literal anywhere else fails."""
    found = set(_offenders(_CORE))
    assert found - _ALLOWED == set(), (
        "vendor cache syntax escaped the provider edge into: "
        f"{sorted(found - _ALLOWED)} — translate at the adapter, keep core neutral"
    )


def test_the_sweep_is_not_vacuous():
    """The patterns must still match the one file allowed to carry them.

    Without this, a rename of Anthropic's marker would leave a rail that matches
    nothing and passes forever.
    """
    assert set(_offenders(_CORE)) == _ALLOWED


def test_the_neutral_marker_module_stays_vendor_free():
    """The seam PCS-3 owns must never learn a vendor's syntax (its own rail's twin)."""
    src = (_CORE / "llm" / "prompt_cache.py").read_text(encoding="utf-8")
    for pat in _VENDOR_CACHE_SYNTAX:
        assert not pat.search(src)
    assert "ephemeral" not in src
