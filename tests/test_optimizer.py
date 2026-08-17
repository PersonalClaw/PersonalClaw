"""Tests for the prompt optimizer endpoint."""

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.dashboard.handlers.optimizer import (
    _CTX_MAX_TURNS,
    _CTX_TURN_CHARS,
    MAX_CONTEXT_CHARS,
    _clip_context,
    handle_optimize,
)

# Home isolation (PERSONALCLAW_HOME → throwaway tmp dir) is provided globally by the
# autouse ``_isolate_personalclaw_home`` fixture in tests/conftest.py, so the optimizer
# system prompt (bundled ``task-prompt-optimizer``) seeds into a throwaway home here.


def _optimizer_system() -> str:
    """The optimizer system prompt as the handler resolves it (bundled
    ``task-prompt-optimizer`` rendered through the prompt engine)."""
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    return render_use_case_prompt("prompt_optimizer", {}) or ""


class TestOptimizerSystem:
    """The optimizer system prompt now lives in the prompt system (bundled
    ``task-prompt-optimizer``); assert on the rendered content."""

    def test_system_prompt_contains_length_limit(self):
        assert "250 words" in _optimizer_system()

    def test_system_prompt_instructs_the_exact_unchanged_token(self):
        """CC-5: the handler has always honored a bare ``UNCHANGED`` reply, but until
        now nothing TOLD the model to send it — a live reader of an unwritten token.
        The prompt must name the exact token the handler recognizes."""
        from personalclaw.dashboard.handlers.optimizer import _UNCHANGED_TOKEN

        text = _optimizer_system()
        assert _UNCHANGED_TOKEN in text
        assert "exactly `UNCHANGED`" in text

    def test_system_prompt_does_not_teach_echoing_an_already_good_prompt(self):
        """The examples are the strongest instruction in the file. The final example
        used to echo its input verbatim, which taught the opposite of rule 2 and cost a
        full response to say nothing."""
        text = _optimizer_system()
        assert 'OUTPUT: "UNCHANGED"' in text
        assert 'OUTPUT: "explore what\'s causing the latency spike"' not in text

    def test_system_prompt_explains_the_role_labeled_context(self):
        """CC-5: the FE now sends ``user:``/``assistant:`` labeled turns; the prompt has
        to say what that block is, or the labels are decoration."""
        text = _optimizer_system()
        assert "<context>" in text
        assert "`user:`" in text and "`assistant:`" in text
        assert "that file from earlier" in text

    def test_system_prompt_contains_preservation_rule(self):
        assert "preserve existing behavior" in _optimizer_system()

    def test_system_prompt_mentions_scope_constraint(self):
        assert "scope" in _optimizer_system().lower()

    def test_system_prompt_mentions_structure(self):
        assert "structure" in _optimizer_system().lower()


class TestOptimizerEndpoint:
    """Test the handle_optimize handler logic."""

    @pytest.mark.asyncio
    async def test_empty_prompt_returns_unchanged(self):
        request = MagicMock()
        request.json = AsyncMock(return_value={"prompt": "", "context": ""})

        resp = await handle_optimize(request)
        data = json.loads(resp.body)
        assert data["changed"] is False
        assert data["optimized"] == ""

    @pytest.mark.asyncio
    async def test_invalid_json_returns_400(self):
        request = MagicMock()
        request.json = AsyncMock(side_effect=ValueError("bad json"))

        resp = await handle_optimize(request)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_unchanged_response_from_llm(self):
        from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK

        mock_client = AsyncMock()

        async def fake_stream(prompt):
            yield MagicMock(kind=EVENT_TEXT_CHUNK, text="UNCHANGED")
            yield MagicMock(kind=EVENT_COMPLETE)

        mock_client.stream = fake_stream

        mock_sessions = MagicMock()
        mock_sessions.get_or_create = AsyncMock(return_value=(mock_client, True, False))
        mock_sessions.release = MagicMock()

        mock_state = MagicMock()
        mock_state.sessions = mock_sessions

        request = MagicMock()
        request.json = AsyncMock(
            return_value={"prompt": "refactor the auth module to be cleaner", "context": ""}
        )
        request.app = {"state": mock_state}

        resp = await handle_optimize(request)
        data = json.loads(resp.body)
        assert data["changed"] is False
        assert data["optimized"] == "refactor the auth module to be cleaner"

    @pytest.mark.asyncio
    async def test_optimized_response_from_llm(self):
        from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK

        mock_client = AsyncMock()
        optimized_text = (
            "Refactor the auth module: extract token validation into a separate service."
        )

        async def fake_stream(prompt):
            yield MagicMock(kind=EVENT_TEXT_CHUNK, text=optimized_text)
            yield MagicMock(kind=EVENT_COMPLETE)

        mock_client.stream = fake_stream

        mock_sessions = MagicMock()
        mock_sessions.get_or_create = AsyncMock(return_value=(mock_client, True, False))
        mock_sessions.release = MagicMock()

        mock_state = MagicMock()
        mock_state.sessions = mock_sessions

        request = MagicMock()
        request.json = AsyncMock(
            return_value={"prompt": "refactor the auth module to be cleaner", "context": ""}
        )
        request.app = {"state": mock_state}

        resp = await handle_optimize(request)
        data = json.loads(resp.body)
        assert data["changed"] is True
        assert data["optimized"] == optimized_text

    @pytest.mark.asyncio
    async def test_short_prompt_still_optimized(self):
        """Explicit user action means even short prompts get optimized."""
        from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK

        mock_client = AsyncMock()

        async def fake_stream(prompt):
            yield MagicMock(
                kind=EVENT_TEXT_CHUNK, text="Confirm and proceed with the previous action."
            )
            yield MagicMock(kind=EVENT_COMPLETE)

        mock_client.stream = fake_stream

        mock_sessions = MagicMock()
        mock_sessions.get_or_create = AsyncMock(return_value=(mock_client, True, False))
        mock_sessions.release = MagicMock()

        mock_state = MagicMock()
        mock_state.sessions = mock_sessions

        request = MagicMock()
        request.json = AsyncMock(return_value={"prompt": "yes", "context": ""})
        request.app = {"state": mock_state}

        resp = await handle_optimize(request)
        data = json.loads(resp.body)
        assert data["changed"] is True
        assert data["optimized"] == "Confirm and proceed with the previous action."

    @pytest.mark.asyncio
    async def test_llm_error_returns_original(self):
        mock_sessions = MagicMock()
        mock_sessions.get_or_create = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

        mock_state = MagicMock()
        mock_state.sessions = mock_sessions

        request = MagicMock()
        request.json = AsyncMock(return_value={"prompt": "refactor the auth module", "context": ""})
        request.app = {"state": mock_state}

        resp = await handle_optimize(request)
        data = json.loads(resp.body)
        assert data["changed"] is False
        assert data["optimized"] == "refactor the auth module"

    @pytest.mark.asyncio
    async def test_quoted_response_stripped(self):
        from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK

        mock_client = AsyncMock()

        async def fake_stream(prompt):
            yield MagicMock(kind=EVENT_TEXT_CHUNK, text='"Refactor the auth module cleanly"')
            yield MagicMock(kind=EVENT_COMPLETE)

        mock_client.stream = fake_stream

        mock_sessions = MagicMock()
        mock_sessions.get_or_create = AsyncMock(return_value=(mock_client, True, False))
        mock_sessions.release = MagicMock()

        mock_state = MagicMock()
        mock_state.sessions = mock_sessions

        request = MagicMock()
        request.json = AsyncMock(return_value={"prompt": "refactor the auth module", "context": ""})
        request.app = {"state": mock_state}

        resp = await handle_optimize(request)
        data = json.loads(resp.body)
        assert data["optimized"] == "Refactor the auth module cleanly"

    @pytest.mark.asyncio
    async def test_over_cap_context_is_truncated_from_the_head(self):
        """The TAIL is what survives — which is why the composer assembles newest-last.
        (The cap was 2000 before CC-5; it is now MAX_CONTEXT_CHARS, derived from the
        composer's own turn budget.)"""
        from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK

        mock_client = AsyncMock()
        captured_prompt = []

        async def fake_stream(prompt):
            captured_prompt.append(prompt)
            yield MagicMock(kind=EVENT_TEXT_CHUNK, text="optimized result")
            yield MagicMock(kind=EVENT_COMPLETE)

        mock_client.stream = fake_stream

        mock_sessions = MagicMock()
        mock_sessions.get_or_create = AsyncMock(return_value=(mock_client, True, False))
        mock_sessions.release = MagicMock()

        mock_state = MagicMock()
        mock_state.sessions = mock_sessions

        long_context = "A" * (MAX_CONTEXT_CHARS + 1000) + "B" * 2000
        request = MagicMock()
        request.json = AsyncMock(
            return_value={
                "prompt": "refactor the auth module to be better",
                "context": long_context,
            }
        )
        request.app = {"state": mock_state}

        await handle_optimize(request)
        # The newest end (all the B's) survives; the head is dropped.
        assert "B" * 2000 in captured_prompt[0]
        assert "A" * (MAX_CONTEXT_CHARS + 1000) not in captured_prompt[0]


# ── CC-5: role-labeled, newest-last context that survives the cap ──


def _stub_request(prompt: str, context: str = "", reply: str = "optimized result"):
    """A mocked request whose stubbed model streams ``reply``.

    Returns ``(request, captured)``; ``captured`` collects the full prompt the handler
    actually hands the model, which is the only place the context's shape is observable.
    """
    from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK

    captured: list[str] = []

    async def fake_stream(full_prompt):
        captured.append(full_prompt)
        yield MagicMock(kind=EVENT_TEXT_CHUNK, text=reply)
        yield MagicMock(kind=EVENT_COMPLETE)

    mock_client = AsyncMock()
    mock_client.stream = fake_stream
    mock_sessions = MagicMock()
    mock_sessions.get_or_create = AsyncMock(return_value=(mock_client, True, False))
    mock_sessions.release = MagicMock()
    mock_state = MagicMock()
    mock_state.sessions = mock_sessions

    request = MagicMock()
    request.json = AsyncMock(return_value={"prompt": prompt, "context": context})
    request.app = {"state": mock_state}
    return request, captured


def _labeled_context(n: int, body_chars: int = 600) -> str:
    """``n`` role-labeled turns, one per line, newest LAST — the shape
    web/src/pages/chat/optimizerContext.ts emits, at a size that exercises the cap."""
    lines = []
    for i in range(n):
        role = "assistant" if i % 2 else "user"
        lines.append(f"{role}: turn{i} " + "z" * body_chars)
    return "\n".join(lines)


class TestContextCap:
    """The cap is an arithmetic contract shared with the composer, and the interesting
    case is not what survives but what gets DROPPED."""

    def test_cap_matches_the_frontend_context_budget(self):
        """Two literals, one number, across a language boundary — so read the frontend's
        constants instead of trusting the comment. Drift here means the handler quietly
        starts cutting well-formed contexts again."""
        ts = (
            Path(__file__).resolve().parents[1] / "web/src/pages/chat/optimizerContext.ts"
        ).read_text()
        fe_turns = int(re.search(r"CTX_MAX_TURNS = (\d+)", ts).group(1))
        fe_chars = int(re.search(r"CTX_TURN_CHARS = (\d+)", ts).group(1))
        assert (fe_turns, fe_chars) == (_CTX_MAX_TURNS, _CTX_TURN_CHARS)
        assert MAX_CONTEXT_CHARS == 4129  # CTX_BUDGET_CHARS in optimizerContext.ts

    def test_a_conforming_context_is_not_touched_at_all(self):
        ctx = _labeled_context(10, body_chars=390)
        assert len(ctx) <= MAX_CONTEXT_CHARS
        assert _clip_context(ctx) == ctx

    def test_over_cap_drops_the_oldest_turns_whole_and_keeps_the_newest(self):
        ctx = _labeled_context(20)  # ~12k chars — three times the cap
        assert len(ctx) > MAX_CONTEXT_CHARS
        kept = _clip_context(ctx)

        assert len(kept) <= MAX_CONTEXT_CHARS
        # Every surviving line still wears its role label: nothing was decapitated.
        for line in kept.split("\n"):
            assert re.match(r"^(user|assistant): turn\d+ ", line), line
        # The NEWEST turn survived and is still last — the end a naive head-slice loses.
        assert kept.split("\n")[-1].startswith("assistant: turn19 ")
        # The oldest turns are gone, whole.
        assert "turn0 " not in kept

    def test_a_naive_character_slice_would_decapitate_the_oldest_survivor(self):
        """The behaviour this guards against, stated directly: the plain tail slice the
        handler used to do lands mid-line, so the oldest survivor arrives as an
        unattributed fragment."""
        ctx = _labeled_context(20)
        naive = ctx[-MAX_CONTEXT_CHARS:]
        assert not re.match(r"^(user|assistant): ", naive)  # the defect
        assert re.match(r"^(user|assistant): ", _clip_context(ctx))  # the fix

    def test_a_context_with_no_line_boundary_keeps_the_raw_tail(self):
        """Vacuity guard for the newline snap: some other caller (the loop composer, an
        app) can send one unbroken blob. A fragment beats an empty context there —
        there is no attribution left to lose."""
        blob = "q" * (MAX_CONTEXT_CHARS + 500)
        kept = _clip_context(blob)
        assert kept == blob[-MAX_CONTEXT_CHARS:]
        assert len(kept) == MAX_CONTEXT_CHARS

    @pytest.mark.asyncio
    async def test_handler_sends_the_labels_and_the_newest_turn(self):
        """Assert on what the handler actually hands the model, not on a helper's return
        value: role labels only matter if they reach the prompt."""
        request, captured = _stub_request("add a test for that file", _labeled_context(20))
        await handle_optimize(request)

        sent = captured[0]
        # Anchor on the exact delimiters the handler writes — the system prompt also
        # mentions "<context>" (it has to explain the block to the model).
        block = sent.split("<context>\n")[1].split("\n</context>")[0]
        assert "user: " in block and "assistant: " in block
        assert "turn19 " in block  # newest present
        assert "turn0 " not in block  # oldest dropped
        assert block.strip().split("\n")[-1].startswith("assistant: turn19 ")

    @pytest.mark.asyncio
    async def test_non_string_context_is_a_400_not_a_500(self):
        request = MagicMock()
        request.json = AsyncMock(return_value={"prompt": "do a thing", "context": {"a": 1}})
        resp = await handle_optimize(request)
        assert resp.status == 400


class TestUnchangedContract:
    """An already-specific prompt comes back ``changed:false`` off the bare token. These
    stub the model's reply, so they prove the PLUMBING honors the contract — not that a
    real model obeys the instruction (see the prompt-text assertions above for that)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "reply",
        [
            "UNCHANGED",
            "UNCHANGED.",
            "unchanged",
            "**UNCHANGED**",
            '"UNCHANGED"',
            "UNCHANGED\n",
            "UNCHANGED — the prompt is already specific and scoped.",
        ],
    )
    async def test_token_reply_keeps_the_users_prompt(self, reply):
        original = "Run pytest tests/test_optimizer.py -q and report the failing assertions"
        request, _ = _stub_request(original, reply=reply)
        resp = await handle_optimize(request)
        data = json.loads(resp.body)
        assert data["changed"] is False
        assert data["optimized"] == original

    @pytest.mark.asyncio
    async def test_a_rewrite_mentioning_the_word_unchanged_is_still_a_rewrite(self):
        """Vacuity guard for the leniency: a substring check would swallow legitimate
        rewrites, which is how an optimizer ships inert."""
        reply = "Refactor the auth module, leaving the public API unchanged."
        request, _ = _stub_request("refactor auth", reply=reply)
        resp = await handle_optimize(request)
        data = json.loads(resp.body)
        assert data["changed"] is True
        assert data["optimized"] == reply
