"""A generated chat title is the TITLE, never the label or the scaffolding around it.

Measured by the day-56b validator on a real Ollama model: 30 of 45 auto-generated titles were
stored as ``Title: Example Site Docs`` (15 times) and ``Title: France Capital``, and one chat was
titled ``TAGS: Planned, Review`` — the tag line the same call is asked to append. On the small
bundled default model the out-of-box validator saw ``Chat title:`` and ```` ```python ````. The
parser took the first line verbatim, so every one of those became the chat's name in the sidebar,
the history page and the header.

The prompt ends on a ``Title:`` completion cue and the tag instructions are appended AFTER it, so a
model that mirrors the format it was shown writes the label back. That is model behaviour no prompt
wording fully prevents — which is why the parser is where it is fixed, and why a reply that holds
no plausible title yields ``""``: the caller then keeps the title it already had (the session key,
which the UI renders as the first-message snippet) and retries on the next turn, rather than
storing junk.
"""

from __future__ import annotations

import pytest
from chat_test_helpers import _make_state

from personalclaw.dashboard.chat_title import (
    _maybe_auto_title,
    _parse_tags_line,
    _parse_title,
)
from personalclaw.dashboard.state import _ChatSession
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent


class TestObservedLabelLeaks:
    """The exact strings the validators recorded, each of which used to be stored verbatim."""

    @pytest.mark.parametrize(
        ("reply", "title"),
        [
            ("Title: Example Site Docs", "Example Site Docs"),
            ("Title: France Capital", "France Capital"),
            ("Title: Example Site Docs\nTAGS: Planned, Review", "Example Site Docs"),
            ("Title: TYPED-DURING-FAILURE", "TYPED-DURING-FAILURE"),
        ],
    )
    def test_the_echoed_title_label_is_stripped(self, reply, title):
        assert _parse_title(reply) == title

    def test_a_tag_line_is_never_a_title(self):
        # The model wrote the tag line FIRST and no title at all.
        assert _parse_title("TAGS: Planned, Review") == ""

    def test_a_bare_label_with_nothing_after_it_is_not_a_title(self):
        # What the bundled SmolLM2-135M produced: the label, and the reply ended.
        assert _parse_title("Chat title:") == ""

    def test_an_opened_code_fence_is_not_a_title(self):
        assert _parse_title("```python") == ""

    def test_a_fenced_code_answer_is_not_a_title(self):
        reply = "```python\ndef reverse(s):\n    return s[::-1]\n```"
        assert _parse_title(reply) == ""


class TestTheTitleIsFoundPastTheScaffolding:
    @pytest.mark.parametrize(
        ("reply", "title"),
        [
            # The label on its own line, the title on the next.
            ("Chat title:\nBuild Log Failure Summary", "Build Log Failure Summary"),
            # The tag line written before the title.
            ("TAGS: Planned, Review\nExample Docs", "Example Docs"),
            # Markdown emphasis around the label, either placement of the colon.
            ("**Title:** Offsite Planning", "Offsite Planning"),
            ("**Title**: Offsite Planning", "Offsite Planning"),
            ("**Title: Offsite Planning**", "Offsite Planning"),
            ("## Title: Offsite Planning", "Offsite Planning"),
            ("# Offsite Planning", "Offsite Planning"),
            ("Conversation title: Offsite Planning", "Offsite Planning"),
            ("Suggested title - Offsite Planning", "Offsite Planning"),
            ("Here is a short title for this conversation: Offsite Planning", "Offsite Planning"),
            ("Sure! Here's a short title:\n\nOffsite Planning", "Offsite Planning"),
            ('Title: "Offsite Planning"', "Offsite Planning"),
            ("“Offsite Planning”", "Offsite Planning"),
            ("Offsite Planning.", "Offsite Planning"),
            ("- Offsite Planning", "Offsite Planning"),
            # Code after a fence is skipped; a title after the closing fence is still found.
            ("```\ncode\n```\nString Reversal", "String Reversal"),
        ],
    )
    def test_scaffolding_is_removed(self, reply, title):
        assert _parse_title(reply) == title


class TestRealTitlesSurviveUntouched:
    @pytest.mark.parametrize(
        "title",
        [
            "Offsite Planning",
            "Learning C#",
            ".NET Dependency Injection",
            "__init__ vs __new__",
            "Movie Titles: Best of 2025",
            "Title IX Compliance Questions",
            "A* Search in Python",
            "Rock 'n' Roll History",
            "Node.js Streams",
        ],
    )
    def test_a_real_title_is_not_mangled(self, title):
        assert _parse_title(title) == title


class TestNonTitlesKeepTheFallback:
    @pytest.mark.parametrize(
        "reply",
        [
            "",
            "   \n  ",
            "SKIP",
            "**SKIP**",
            "user: and another thing",
            "assistant: sure, here you go",
            "---",
            "x" * 61,
        ],
    )
    def test_no_plausible_title_yields_empty(self, reply):
        assert _parse_title(reply) == ""


class TestTheTagLineStillParses:
    def test_tags_are_read_from_a_decorated_tag_line(self):
        # One definition of "the tag line" serves both parsers: the title parser skips it, the
        # tag parser reads it — including when the model bolded the label.
        assert _parse_tags_line("Offsite Planning\n**TAGS:** Work, Events") == ["Work", "Events"]

    def test_tags_are_read_when_the_title_carried_a_label(self):
        assert _parse_tags_line("Title: Example Site Docs\nTAGS: Planned, Review") == [
            "Planned",
            "Review",
        ]


def _stream_reply(state, text):
    from unittest.mock import AsyncMock, MagicMock

    client = MagicMock()
    client.reject_tool = AsyncMock()

    async def _stream(prompt):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=text)
        yield LLMEvent(kind=EVENT_COMPLETE)

    client.stream = _stream
    state.sessions.get_or_create = AsyncMock(return_value=(client, False, False))
    state.sessions.release = MagicMock()


def _session(state):
    session = _ChatSession("chat-1-1790341852")
    session.messages = [
        {"role": "user", "content": "Link me to the example site and its docs"},
        {"role": "assistant", "content": "[Example site](https://example.com/abc) and [Docs]"},
    ]
    state._sessions[session.key] = session
    return session


class TestAutoTitleStoresOnlyARealTitle:
    @pytest.mark.asyncio
    async def test_a_junk_reply_leaves_the_chat_on_its_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = _session(state)
        _stream_reply(state, "TAGS: Planned, Review")
        await _maybe_auto_title(state, session)
        # Untitled: the title is still the key (rendered as the first-message snippet), and the
        # chat is retried on its next turn rather than frozen on junk.
        assert session.title == session.key
        assert session._titled is False
        assert session.tags == []

    @pytest.mark.asyncio
    async def test_a_labelled_reply_stores_the_title_alone(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = _session(state)
        _stream_reply(state, "Title: Example Site Docs\nTAGS: Docs")
        await _maybe_auto_title(state, session)
        assert session.title == "Example Site Docs"
        assert session._titled is True
