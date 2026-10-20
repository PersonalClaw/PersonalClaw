"""A model's reasoning-channel tokens never reach the reply, and its reasoning reads as thinking.

Measured in a chat on a local Gemma 4 model through the Ollama app: the reply read "…Actually,
let me try to identify the repositories first.<channel|>I have analyzed the current
directory…". Gemma 4 wraps its reasoning in two control tokens, ``<|channel>thought\\n`` and
``<channel|>`` (its chat template's own ``strip_thinking`` splits on exactly these). Ollama's
parser separated them on a first turn, but on a turn that continued after a tool result it put
the raw opener into the message's ``thinking`` field and let a closing token through in
``content``. The app read only ``content`` and split only ``<think>`` tags, so the closing token
was shown as text, and every model's separate ``thinking`` field was dropped.

The reasoning before that token — "Wait, I notice…", "Actually, let me try…" — was shown too:
Ollama had opened the channel in ``thinking`` and let the rest of it through in ``content``.

Pinned here: the one splitter every provider runs recognizes the channel tokens as reasoning, a
closing token with no opener is removed rather than shown, and the Ollama app reads the
``thinking`` field as thinking — its own markers removed, anything after a closing token read as
the answer, and a channel it left open read as continuing in ``content`` up to its closing token.
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx
import pytest

from personalclaw.apps.native_contract import (
    NATIVE_DIR,
    load_bundle_module,
    namespaced_module_name,
)
from personalclaw.llm.events import EVENT_TEXT_CHUNK, EVENT_THINKING_CHUNK
from personalclaw.llm.registry import ProviderEntry
from personalclaw.llm.stream_tags import KIND_OUTSIDE, make_think_splitter

THINKING = "thinking"


def _runs(chunks: list[str], *, inside: bool = False) -> list[tuple[str, str]]:
    """The contiguous (kind, text) runs a fresh reasoning splitter resolves ``chunks`` into."""
    splitter = make_think_splitter(inside=inside)
    segments = [seg for chunk in chunks for seg in splitter.feed(chunk)] + splitter.flush()
    runs: list[list[str]] = []
    for seg in segments:
        if runs and runs[-1][0] == seg.kind:
            runs[-1][1] += seg.text
        else:
            runs.append([seg.kind, seg.text])
    return [(kind, text) for kind, text in runs]


# ── the splitter ──────────────────────────────────────────────────────────────


def test_a_channel_span_is_reasoning_and_its_name_is_not_content():
    assert _runs(["<|channel>thought\nCheck the repo first.<channel|>It has two branches."]) == [
        (THINKING, "Check the repo first."),
        (KIND_OUTSIDE, "It has two branches."),
    ]


def test_token_by_token_the_markers_never_leak():
    chunks = ["<|channel>", "thought", "\n", "Check the", " repo.", "<channel|>", "Two branches."]
    assert _runs(chunks) == [(THINKING, "Check the repo."), (KIND_OUTSIDE, "Two branches.")]
    assert _runs(list("<|channel>thought\nhm<channel|>ok")) == [
        (THINKING, "hm"),
        (KIND_OUTSIDE, "ok"),
    ]


def test_a_closing_token_with_no_opener_is_removed_not_shown():
    """The measured reply. What came before the token was already streamed as text; the token
    itself never is."""
    runs = _runs(["Let me identify the repositories first.", "<channel|>", "I have analyzed it."])
    assert runs == [(KIND_OUTSIDE, "Let me identify the repositories first.I have analyzed it.")]


def test_the_empty_channel_a_template_prefills_is_nothing():
    assert _runs(["<|channel>thought\n<channel|>Hello."]) == [(KIND_OUTSIDE, "Hello.")]
    assert _runs(["<|channel>thought<channel|>Hello."]) == [(KIND_OUTSIDE, "Hello.")]


def test_a_stray_think_close_is_removed_too():
    """The same shape for a model whose template opens the think tag in the prompt."""
    assert _runs(["reasoning", "</think>", "answer"]) == [(KIND_OUTSIDE, "reasoninganswer")]


def test_ordinary_angle_brackets_are_untouched():
    text = "a <b> tag, a <|pipe|> token and x < y stay as written"
    assert _runs([text]) == [(KIND_OUTSIDE, text)]


def test_a_reasoning_field_starts_as_thinking_and_its_opener_is_removed():
    """Ollama's ``thinking`` field on a turn after a tool result: the raw opener rides in it."""
    assert _runs(
        ["<|channel>", "thought", "\n", "The user wants the repo names."], inside=True
    ) == [(THINKING, "The user wants the repo names.")]


def test_text_after_a_closing_token_in_a_reasoning_field_is_the_answer():
    assert _runs(["<|channel>thought\nPlan it.", "<channel|>", "Two repos."], inside=True) == [
        (THINKING, "Plan it."),
        (KIND_OUTSIDE, "Two repos."),
    ]


# ── the Ollama app ────────────────────────────────────────────────────────────

APP_NAME = "ollama-models"
ENDPOINT = "http://127.0.0.1:11434"


@pytest.fixture()
def module():
    name = namespaced_module_name(APP_NAME, "provider")
    try:
        yield load_bundle_module(NATIVE_DIR / APP_NAME, APP_NAME, "provider")
    finally:
        sys.modules.pop(name, None)


def _answering(module, frames: list[dict]):
    """An Ollama provider whose server streams ``frames`` (each a ``message`` object)."""
    body = (
        "".join(
            json.dumps({"message": {"role": "assistant", **frame}, "done": False}) + "\n"
            for frame in frames
        )
        + json.dumps({"done": True, "prompt_eval_count": 10, "eval_count": 5})
        + "\n"
    )
    provider = module._factory(
        entry=ProviderEntry(
            name="ollama",
            type="ollama",
            model="gemma4:12b",
            options={"endpoint": ENDPOINT, "default_model": "gemma4:12b"},
        )
    )

    async def _no_window(model):
        return None

    provider._served_window = _no_window
    provider._client = httpx.AsyncClient(
        base_url=ENDPOINT,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)),
    )
    return provider


def _said(provider, path: str) -> tuple[str, str]:
    """``(visible text, thinking)`` a turn streamed on ``path``."""

    async def _collect():
        events = (
            provider.stream("hello")
            if path == "stream"
            else provider.complete([{"role": "user", "content": "hello"}])
        )
        return [ev async for ev in events]

    events = asyncio.run(_collect())
    text = "".join(ev.text for ev in events if ev.kind == EVENT_TEXT_CHUNK)
    thinking = "".join(ev.text for ev in events if ev.kind == EVENT_THINKING_CHUNK)
    return text, thinking


@pytest.mark.parametrize("path", ["stream", "complete"])
def test_the_measured_reply_shows_no_channel_token(module, path):
    provider = _answering(
        module,
        [
            {"content": "Actually, let me try to identify the repositories first."},
            {"content": "<channel|>"},
            {"content": "I have analyzed the current directory."},
        ],
    )

    text, _ = _said(provider, path)

    assert "channel" not in text
    assert text.endswith("first.I have analyzed the current directory.")


@pytest.mark.parametrize("path", ["stream", "complete"])
def test_the_thinking_field_is_thinking_without_its_markers(module, path):
    provider = _answering(
        module,
        [
            {"thinking": "<|channel>"},
            {"thinking": "thought"},
            {"thinking": "\n"},
            {"thinking": "The user wants the repository names."},
            {"content": "Two repositories: kettle and lamp."},
        ],
    )

    text, thinking = _said(provider, path)

    assert thinking == "The user wants the repository names."
    assert text == "Two repositories: kettle and lamp."


def test_an_answer_left_in_the_thinking_field_is_still_shown(module):
    provider = _answering(
        module,
        [{"thinking": "<|channel>thought\nPlan it."}, {"thinking": "<channel|>Two repos."}],
    )

    text, thinking = _said(provider, "complete")

    assert (thinking, text) == ("Plan it.", "Two repos.")


@pytest.mark.parametrize("path", ["stream", "complete"])
def test_reasoning_let_through_in_the_content_is_thinking_up_to_the_closing_token(module, path):
    """The measured reply in full: the thinking-aloud paragraphs before the token were shown as
    the answer, because the channel the reasoning field opened was closed in the content."""
    provider = _answering(
        module,
        [
            {"thinking": "<|channel>thought\n"},
            {"thinking": "The user wants a standup."},
            {"content": "Wait, I notice that I'm in a workspace.\n\n"},
            {"content": "Actually, let me try to identify the repositories first.<chan"},
            {"content": "nel|>I have analyzed the current directory."},
        ],
    )

    text, thinking = _said(provider, path)

    assert text == "I have analyzed the current directory."
    assert thinking == (
        "The user wants a standup.Wait, I notice that I'm in a workspace.\n\n"
        "Actually, let me try to identify the repositories first."
    )


def test_an_answer_with_no_reasoning_markers_is_not_held_back(module):
    """Held only while the reasoning field has a channel open: an ordinary answer streams as it
    arrives."""
    reasoning = module._Reasoning()

    events = reasoning.feed({"thinking": "Plan it.", "content": "Two repos."})

    assert [(ev.kind, ev.text) for ev in events] == [
        (EVENT_THINKING_CHUNK, "Plan it."),
        (EVENT_TEXT_CHUNK, "Two repos."),
    ]
