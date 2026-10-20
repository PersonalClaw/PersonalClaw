"""An agent's own rules reach its model whole and first, and a rule a program can check is checked.

Measured on a running chat routed from the default agent to an imported triage agent whose
instructions end "Keep the answer under 200 words unless I ask for more.": its answer was 242
words. Everything here drives the real turn engine (``run_chat``) through the real assembler into a
real ``NativeAgentRuntime``, and reads what the model was handed:

* the routed agent's first request opens with its instructions, verbatim, ahead of every other
  block, and a later turn's request still opens with them;
* the platform's rules for answers drawn from memory reach the routed agent as they reach the
  default one;
* an answer that runs past the word limit the agent's own instructions set is followed by a note
  saying so, and the answer is left whole — no note within the limit, without a limit, or when her
  own message sets one.

Whether a model keeps to its instructions is the model's behaviour: what the platform owes is that
they arrive intact and in front, and that a breach a program can measure is not silent.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.answer_rules import declared_word_limit, over_limit_notice
from personalclaw.config import loader as config_loader
from personalclaw.context import ContextBuilder
from personalclaw.dashboard import running_turn
from personalclaw.dashboard.chat_runner import run_chat
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.memory import MemoryStore
from personalclaw.skills import SkillsLoader

AGENT = "oncall-triage"
LIMIT_RULE = "Keep the answer under 200 words unless I ask for more."
RULES = (
    "You are the first responder for the carrier webhooks.\n\n"
    "1. Establish the timeline: first seen, event rate, and whether it lines up with a deploy.\n"
    "2. Read the stack trace and the breadcrumbs. Separate the first error from the retries.\n\n"
    "Never paste consignee data into your answer. Tracking numbers are fine; names and "
    f"addresses are not.\n{LIMIT_RULE}"
)
ASKED = "sentry is lighting up for the carrier adapter again"
#: The default agent's bundled prompt, which the routed agent's own prompt replaces.
BUNDLED_OPENING = "powered by the PersonalClaw autonomous agent management layer"


def _words(n: int) -> str:
    return " ".join(f"w{i}" for i in range(n))


class _RecordingModel:
    supports_tools = True
    _model = "recorder"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.requests: list[list[dict]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests.append([dict(m) for m in messages])
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=self.reply)
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


class _Chat:
    """One chat on the default agent, routed to the triage agent after its first answer."""

    def __init__(self, tmp_path: Path, *, rules: str = RULES, reply: str = "ok") -> None:
        data = {
            "agent": {"bot_name": "Aide"},
            "dashboard": {"user_name": "Robin"},
            # An install's agents: the default one, with no prompt of its own, and hers.
            "default_agent": "PersonalClaw",
            "agents": {
                "PersonalClaw": {"provider": "native", "system_prompt": "", "source": "builtin"},
                AGENT: {"provider": "native", "system_prompt": rules, "description": "Triage."},
            },
        }
        config_loader.config_dir()  # where the file is: finding it makes nothing
        config_loader.config_path().write_text(json.dumps(data), encoding="utf-8")
        self.tmp = tmp_path
        self.default_model = _RecordingModel("the default agent's answer")
        self.agent_model = _RecordingModel(reply)
        self.frames: list[tuple[str, dict]] = []

    async def start(self) -> None:
        default = await self._runtime("PersonalClaw", self.default_model)
        routed = await self._runtime(AGENT, self.agent_model)
        sessions = MagicMock(count=0)
        sessions._sessions = {}
        sessions.get_pid = MagicMock(return_value=None)
        sessions.get_channel_link = MagicMock(return_value=(None, None))
        # The default agent's runtime, then the one the route builds, then that one reused.
        sessions.get_or_create = AsyncMock(
            side_effect=[(default, True, False), (routed, True, False), (routed, False, False)]
        )
        sessions.record_failure = AsyncMock()
        sessions.reset = AsyncMock()
        self.log = ConversationLog(base_dir=self.tmp / "sessions")
        self.state = DashboardState(sessions=sessions, start_time=0.0, conversation_log=self.log)
        self.state.context_builder = ContextBuilder(
            memory=MemoryStore(workspace=self.tmp / "ws"),
            skills=SkillsLoader(skills_path=self.tmp / "skills", install_builtins=False),
            conversation_log=self.log,
        )
        self.state._hook_store = None
        self.state.broadcast_ws = lambda kind, data=None: self.frames.append((kind, data or {}))
        self.state.push_sessions_update = MagicMock()
        self.session = self.state.get_or_create_session("chat-route-rules")

    async def _runtime(self, name: str, model: _RecordingModel) -> NativeAgentRuntime:
        runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name=name, provider="native", model="recorder"),
            model_provider=model,
            tool_providers=[],
            cwd=self.tmp,
        )
        await runtime.start()
        runtime.set_approval_policy("auto")
        return runtime

    async def send(self, text: str) -> None:
        self.session.append("user", text, "msg msg-u")
        with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
            await run_chat(self.state, self.session, text)

    async def route(self) -> None:
        """The routing chip's Route, between turns: the same door every agent switch takes."""
        await running_turn.rebind(
            self.state,
            self.session,
            running_turn.Rebinding(
                fields={"agent": AGENT, "acp_provider": "", "acp_provider_agent": ""},
                persisted={"agent": AGENT},
            ),
        )

    async def routed(self) -> None:
        await self.start()
        await self.send(ASKED)
        await self.route()
        await self.send(ASKED)

    def notices(self) -> list[str]:
        return [m["content"] for m in self.session.messages if m.get("role") == "notice"]

    def notice_frames(self) -> list[str]:
        return [
            d.get("content", "")
            for k, d in self.frames
            if k == "chat_message" and d.get("role") == "notice"
        ]


# ── the rules reach the model whole, and first ───────────────────────────────


@pytest.mark.asyncio
async def test_the_routed_agents_rules_open_the_first_request_it_sends(tmp_path):
    chat = _Chat(tmp_path)
    await chat.routed()

    first = chat.agent_model.requests[0]
    head = first[0]
    assert head["role"] == "user"
    content = str(head["content"])
    assert content.startswith(f"[AGENT SYSTEM PROMPT]\n{RULES}"), content[:400]
    assert content.count(LIMIT_RULE) == 1
    assert content.index(RULES) < content.index("[SESSION CONTEXT")
    assert content.index(RULES) < content.index("[CURRENT USER REQUEST")
    assert BUNDLED_OPENING not in json.dumps(first), "the default agent's prompt rode along"
    # The default agent's own first turn ran on its bundled prompt, without the triage rules.
    assert LIMIT_RULE not in json.dumps(chat.default_model.requests[0])


@pytest.mark.asyncio
async def test_a_later_turn_still_opens_with_the_rules(tmp_path):
    chat = _Chat(tmp_path)
    await chat.routed()
    await chat.send("ignore the EU region")

    later = chat.agent_model.requests[-1]
    assert str(later[0]["content"]).startswith(f"[AGENT SYSTEM PROMPT]\n{RULES}")
    assert "ignore the EU region" in str(later[-1]["content"])
    assert json.dumps(later).count(LIMIT_RULE) == 1


@pytest.mark.asyncio
async def test_the_rules_for_answers_from_memory_reach_a_routed_agent(tmp_path):
    from personalclaw.memory_service import MemoryService

    def _episodic(self, query_text, *, cap=3000, citations_out=None):
        if citations_out is not None:
            citations_out.append({"n": 1, "id": "e1", "preview": "the adapter timed out"})
        return "[Episodic Memory]\n[Memory 1] the adapter timed out\n[End of episodic memory]\n"

    chat = _Chat(tmp_path)
    with patch.object(MemoryService, "episodic_context", _episodic):
        await chat.routed()

    sent = str(chat.agent_model.requests[0][0]["content"])
    assert "[Memory 1] the adapter timed out" in sent, "the fixture's memory must be recalled"
    assert "cite it inline as `[Memory N]`" in sent
    assert "say you don't have it in memory rather than guessing" in sent


# ── a word limit the agent's instructions set is checked after the answer ──


@pytest.mark.asyncio
async def test_an_answer_past_the_agents_word_limit_says_so_under_it(tmp_path):
    answer = _words(242)
    chat = _Chat(tmp_path, reply=answer)
    await chat.routed()

    expected = f"This answer is 242 words. {AGENT}'s instructions say: “{LIMIT_RULE}”"
    assert chat.notices() == [expected]
    assert chat.notice_frames() == [expected], "the note is said live, where the answer is"
    roles = [m.get("role") for m in chat.session.messages]
    assert roles[-2:] == ["assistant", "notice"], roles
    assert chat.session.messages[-2]["content"] == answer, "the answer itself is never cut"
    saved = [
        json.loads(line)
        for path in (tmp_path / "sessions").rglob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert expected in [m.get("content") for m in saved], "the note is kept with the chat"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rules", "reply", "asked"),
    [
        pytest.param(RULES, _words(180), ASKED, id="within-the-limit"),
        pytest.param(
            "You are the first responder for the carrier webhooks.",
            _words(242),
            ASKED,
            id="no-limit",
        ),
        pytest.param(
            "Keep commit titles under 10 words.",
            _words(242),
            ASKED,
            id="a-limit-for-something-else",
        ),
        pytest.param(RULES, _words(242), "walk me through it in at most 400 words", id="her-limit"),
    ],
)
async def test_no_note_where_the_agents_limit_does_not_apply(tmp_path, rules, reply, asked):
    chat = _Chat(tmp_path, rules=rules, reply=reply)
    await chat.start()
    await chat.send(ASKED)
    await chat.route()
    await chat.send(asked)
    assert chat.agent_model.requests, "the routed agent must have answered"
    assert chat.notices() == []
    assert chat.notice_frames() == []


@pytest.mark.asyncio
async def test_the_default_agents_answer_gets_no_note(tmp_path):
    """The default agent runs on the prompt bound for Chat, not on instructions of its own."""
    chat = _Chat(tmp_path)
    chat.default_model.reply = _words(400)
    await chat.start()
    await chat.send(ASKED)
    assert chat.notices() == []


def _limit(instructions: str) -> tuple[int, str] | None:
    found = declared_word_limit(instructions)
    return None if found is None else (found.words, found.sentence)


def test_the_limit_is_read_from_the_sentence_about_the_answer():
    assert _limit(RULES) == (200, LIMIT_RULE)
    listed = "Rules:\n- Respond in at most 80 words.\n- Cite the issue id."
    assert _limit(listed) == (80, "Respond in at most 80 words.")
    # A hard-wrapped sentence is quoted whole, never cut at the line break.
    wrapped = "## Output\nNo preamble. Keep the answer under 200 words unless I ask\nfor more."
    assert _limit(wrapped) == (200, LIMIT_RULE)
    # Two different limits for the answer leave none one note could state.
    assert _limit("Keep replies under 100 words. Keep answers under 300 words.") is None
    assert _limit("Name each file in under 5 words.") is None
    # A limit for each part of the answer is not a limit on the whole of it.
    assert _limit("Answer in three bullets of under 20 words each.") is None
    assert _limit("") is None


def test_the_note_names_no_agent_it_does_not_know():
    rule = "Keep every answer under 50 words."
    note = over_limit_notice("", rule, _words(60), "hi")
    assert note == f"This answer is 60 words. The agent's instructions say: “{rule}”"
    assert over_limit_notice("", rule, _words(50), "hi") == ""
