"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_a_declared_read_asks_nobody.py``

Its checks:
``tests/rails/test_a_declared_read_asks_nobody_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from personalclaw.agents.native.tools import InProcessMcpToolProvider
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.tool_providers.base import ToolResult


class _Recording(InProcessMcpToolProvider):
    """A real registry's definitions, which are what is under test, recording each call it
    would have made."""

    def __init__(self, module: str) -> None:
        super().__init__(module=module, provider_name="under-test")
        self.invoked: list[str] = []

    async def invoke(self, tool_name, arguments):
        self.invoked.append(tool_name)
        return ToolResult(success=True, output="ran")


class _OneCall:
    """A model that calls one tool, then answers."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, tool: str, args: str) -> None:
        self._turns = [
            [
                AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title=tool, tool_input=args),
                AgentEvent(kind=EVENT_COMPLETE),
            ],
            [AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)],
        ]
        self.calls = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        turn = self._turns[min(self.calls, len(self._turns) - 1)]
        self.calls += 1
        for ev in turn:
            yield ev
