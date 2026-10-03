"""The ``ask_user`` tool: the agent asks its owner a question, and waits for her answer.

An agent that needs her decision to go on (which of two approaches, which of several things she
meant) asks it as a question with options, AskUserQuestion's shape: ``{"questions": [{"question",
"header", "options": [{"label", "description"}], "multiSelect"}]}``. The question is put to her by
:mod:`personalclaw.owner_questions`: a card in the chat, a row in her Inbox and in Mission
Control's Your turn. The call returns her answer (her choices, and anything she typed), her Skip,
or that nobody answered in her window, and the turn goes on from there.

Only the owner of an open chat is asked. Work nobody watches never sees the tool (it is
``interactive``, so an unattended run's toolset leaves it out), and a call from anywhere else (a
background task, a loop's or a workflow's step, a chat carried on a chat channel) is told to ask in
its reply instead. Asking changes nothing of hers, so the call is declared a read: it asks no
approval, and Ask and Plan mode let the agent ask what it needs to know.
"""

from __future__ import annotations

from typing import Any

from personalclaw.constants import DASHBOARD_SESSION_PREFIX
from personalclaw.owner_questions import (
    ANSWERED,
    ASK_USER_TOOL,
    CANCELLED,
    answer_lines,
    asker,
    normalize,
)
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from personalclaw.validation import ValidationError

PROVIDER = "personalclaw-question-tools"


def _schema() -> dict[str, Any]:
    option = {
        "type": "object",
        "properties": {
            "label": {"type": "string", "description": "The choice, in a few words."},
            "description": {"type": "string", "description": "What choosing it means."},
        },
        "required": ["label"],
    }
    question = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question, as a full sentence."},
            "header": {"type": "string", "description": "A short label for it (a few words)."},
            "options": {"type": "array", "items": option, "minItems": 2, "maxItems": 6},
            "multiSelect": {
                "type": "boolean",
                "description": "True when the user may choose more than one option.",
            },
        },
        "required": ["question", "options"],
    }
    return {
        "type": "object",
        "properties": {
            "questions": {"type": "array", "items": question, "minItems": 1, "maxItems": 4}
        },
        "required": ["questions"],
    }


class QuestionToolProvider(ToolProvider):
    """Puts the agent's question to the owner of the chat it runs in."""

    @property
    def name(self) -> str:
        return PROVIDER

    @property
    def display_name(self) -> str:
        return "Questions for you"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=ASK_USER_TOOL,
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                interactive=True,
                description=(
                    "Ask the user a question and wait for the answer, when you need their "
                    "decision to go on: which option to take, or which of several things they "
                    "meant. The question shows in the chat as a card with your options and a box "
                    "for an answer of their own, and in their Inbox; the call returns what they "
                    "chose and wrote, or that they skipped it. Ask only what you cannot sensibly "
                    "decide for them, and only while they are in the chat; for anything else, ask "
                    "in your reply. Args: questions (1-4), each {question, header (a few words), "
                    "options (2-6, each {label, description}), multiSelect (true when more than "
                    "one option may be chosen)}."
                ),
                parameters=_schema(),
            )
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        if tool_name != ASK_USER_TOOL:
            return ToolResult(success=False, error=f"Unknown tool: {tool_name}")
        from personalclaw.inbox_providers.native_source import get_dashboard_state
        from personalclaw.mcp_core import get_current_session_key

        state = get_dashboard_state()
        registry = getattr(state, "owner_questions", None)
        key = get_current_session_key()
        why = "no chat is running here" if registry is None else registry.cannot_ask(key)
        if registry is None or why:
            return ToolResult(
                success=False,
                error=f"Nobody can answer a question here: {why}.",
                recovery_hints=["Ask the question in your reply instead."],
            )
        try:
            questions = normalize(arguments)
        except ValidationError as exc:
            return ToolResult(
                success=False,
                error=f"The question could not be asked: {exc}",
                recovery_hints=[
                    "Send questions as a list, each with its question and at least two options "
                    "that each have a label."
                ],
            )
        from personalclaw.auto_denials import window_words

        session = state.get_session(key.removeprefix(DASHBOARD_SESSION_PREFIX))
        outcome = await registry.ask(key, questions, asked_by=asker(session), arguments=arguments)
        text = answer_lines(questions, outcome, window_words(state.approval_window_secs()))
        if outcome.kind == CANCELLED:
            return ToolResult(success=False, error=text)
        return ToolResult(
            success=True, output=text, metadata={"answered": outcome.kind == ANSWERED}
        )


def create_question_tools_provider(config: dict[str, Any] | None = None) -> QuestionToolProvider:
    """The bundled Questions for you app's provider."""
    return QuestionToolProvider()
