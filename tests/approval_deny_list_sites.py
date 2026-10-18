"""The approval sites ``test_every_approval_path_asks_the_deny_list_first.py`` shares.

``_DECIDES, _GATES``, which the suite's other tests read too.

Kept here, beside the suite, so a test that is not a rail can import them without importing
a rail (rails live in ``tests/rails/``, which only the rails job collects)."""

from __future__ import annotations

_DECIDES: dict[str, str] = {
    "dashboard/chat_runner.py::run_chat._let_through": (
        "a chat's call: the card, Trust, YOLO, a hook pattern; the one yes they all answer with, "
        "which first backs up the files an agent CLI's file change names"
    ),
    "llm_helpers.py::_resolve_permission": (
        "the background helper, under every approval mode: an announce, a heartbeat, a room "
        "member's turn, a one-shot call"
    ),
    "subagent.py::SubagentManager._approve_and_log": "a subagent's call, screened in its turn",
    "eval/runner.py::EvalRunner._decide_permission": "an evaluation's allowlist",
}


#: The gates that run a call no approval is asked for, each of which asks the screen: each gate,
#: the function it asks the screen through ("" when it asks in its own body), and what it is.
_GATES: dict[str, tuple[str, str]] = {
    "agents/native/runtime.py::NativeAgentRuntime._guard_and_invoke": (
        "agents/native/approval.py::deny_list_refusal",
        "a native runtime's own gate, which answers every call its approval policy waives",
    ),
    "dashboard/handlers/tools.py::api_tool_invoke": (
        "",
        "Tools → Try it and a scheduled script's tool call, by the tool's name",
    ),
}
