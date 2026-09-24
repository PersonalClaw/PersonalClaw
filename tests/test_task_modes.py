"""chat-task-modes — the task-mode tool gate + framing (docs/plans/ports/chat-task-modes.md).

Task mode (agent/ask/plan/build) is an axis ORTHOGONAL to the approval mode: it gates
*which* tools run + how the agent frames the work. These cover the pure gate logic
(security-critical: Ask must block every mutation) + the framing selection.
"""

from __future__ import annotations

import pytest

from personalclaw.dashboard.chat_utils import task_mode_denies, task_mode_framing


class _S:
    def __init__(self, mode: str) -> None:
        self._task_mode = mode


# (mode, title, tool_kind, tool_input, expect_denied)
_CASES = [
    # agent: unrestricted
    ("agent", "write_file", "edit", "{}", False),
    ("agent", "bash", "command", '{"command":"rm -rf x"}', False),
    # plan: read-only inspection ALLOWED (so the plan is grounded), mutation DENIED
    ("plan", "read_file", "read", "{}", False),  # inspect to plan
    ("plan", "grep", "read", "{}", False),
    ("plan", "bash", "command", '{"command":"ls -la"}', False),  # read-only bash
    ("plan", "write_file", "edit", "{}", True),  # no execution
    ("plan", "bash", "command", '{"command":"rm -rf x"}', True),
    ("plan", "artifact_save", "", "{}", True),  # plan != build
    # ask: read-only allowed, every mutation denied (deny-by-default)
    ("ask", "read_file", "read", "{}", False),
    ("ask", "grep", "read", "{}", False),
    ("ask", "bash", "command", '{"command":"ls -la"}', False),  # read-only bash
    ("ask", "bash", "command", '{"command":"rm -rf x"}', True),  # mutating bash
    ("ask", "bash", "command", '{"command":"cat a > b"}', True),  # redirect = write
    ("ask", "write_file", "edit", "{}", True),
    ("ask", "artifact_save", "", "{}", True),  # 'save' verb
    ("ask", "memory_recall", "", "{}", False),  # read-ish name
    ("ask", "delete_thing", "delete", "{}", True),
    ("ask", "subagent_run", "", "{}", True),  # 'run'/'subagent'
    # build: read-only + artifact/widget/skill producers; other mutations denied
    ("build", "read_file", "read", "{}", False),
    ("build", "artifact_save", "", "{}", False),  # producer
    ("build", "widget_create", "", "{}", False),  # 'widget' hint
    ("build", "skill_invoke", "", "{}", False),  # 'skill' hint
    ("build", "bash", "command", '{"command":"rm -rf x"}', True),
    ("build", "write_file", "edit", "{}", True),
    ("build", "delete_artifact", "", "{}", True),  # TM11: destructive, NOT a producer
    ("build", "remove_widget", "", "{}", True),  # TM11: destructive despite 'widget' hint
    ("ask", "delete_artifact", "", "{}", True),  # ask never honors build hints
    # image_generate: a media PRODUCER (creates a kind:image artifact + paid call) —
    # NOT read-only, so ask/plan block it; build allows it (producing is the point).
    ("ask", "image_generate", "", '{"prompt":"a cat"}', True),  # GAP6: was wrongly read-only
    ("plan", "image_generate", "", '{"prompt":"a cat"}', True),
    ("build", "image_generate", "", '{"prompt":"a cat"}', False),  # 'image' producer hint
    ("agent", "image_generate", "", '{"prompt":"a cat"}', False),
    ("ask", "prompt_render", "read", "{}", False),  # regression: read-only stays allowed
]


@pytest.mark.parametrize("mode,title,kind,inp,want_deny", _CASES)
def test_task_mode_gate(mode, title, kind, inp, want_deny):
    denied = bool(task_mode_denies(_S(mode), title, kind, inp))
    assert denied is want_deny, f"[{mode}] {title}/{kind}: got deny={denied} want={want_deny}"


def test_agent_mode_never_denies():
    s = _S("agent")
    for title, kind in [("anything", "edit"), ("bash", "command"), ("delete_all", "delete")]:
        assert task_mode_denies(s, title, kind, "{}") == ""


def test_framing_per_mode():
    # Every mode (including Agent) states its posture explicitly — Agent's block is
    # what lifts a stale Ask/Plan/Build refusal when the user switches mid-chat.
    for mode in ("agent", "ask", "plan", "build"):
        f = task_mode_framing(_S(mode))
        assert f and mode.capitalize() in f  # mode-named framing block present
    # Agent framing must actively countermand a prior restriction, not just exist.
    agent_f = task_mode_framing(_S("agent")).lower()
    assert "lifted" in agent_f or "full execution" in agent_f
    # Restricted modes teach the one-click escalation marker (TM8); Agent doesn't.
    for mode in ("ask", "plan", "build"):
        assert "SWITCH_TO_AGENT" in task_mode_framing(_S(mode))
    assert "SWITCH_TO_AGENT" not in task_mode_framing(_S("agent"))


def test_framing_unknown_mode_is_empty():
    # A mode string with no framing entry returns '' (no spurious injection).
    assert task_mode_framing(_S("nonsense")) == ""


def test_framing_layers_on_default_system_prompt_not_replaces(tmp_path):
    """S05 C6 regression: framing threaded as system_prompt_suffix must LAYER on
    the resolved default-agent prompt — folding it into system_prompt_override
    (the old wiring) made the 4-line posture block the ENTIRE system prompt,
    silently dropping identity ({{bot_name}}), widgets, and safety rules."""
    from personalclaw.context import ContextBuilder

    cb = ContextBuilder()
    out, _ = cb.build_message(
        "hello",
        True,
        session_key="dashboard:tm-layer-test",
        agent="personalclaw",
        system_prompt_suffix=task_mode_framing(_S("agent")),
    )
    # identity line from the resolved chat prompt survived
    assert "You are " in out
    # ... and the framing is layered on top of it
    assert "Task mode: Agent" in out
    # override + suffix: both present (custom-agent path)
    out2, _ = cb.build_message(
        "hello",
        True,
        session_key="dashboard:tm-layer-test2",
        agent="personalclaw",
        system_prompt_override="You are TestBot, a custom persona.",
        system_prompt_suffix=task_mode_framing(_S("ask")),
    )
    assert "TestBot" in out2 and "Task mode: Ask" in out2
