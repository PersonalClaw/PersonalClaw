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


# (mode, declared, title, tool_kind, tool_input, builds, expect_denied). `declared` is what the
# tool declares (its RiskLevel value; "" = nothing, which is every ACP CLI's own tool).
_CASES = [
    # agent: unrestricted
    ("agent", "caution", "write_file", "", "{}", False, False),
    ("agent", "destructive", "bash", "", '{"command":"rm -rf x"}', False, False),
    # plan: read-only inspection ALLOWED (so the plan is grounded), mutation DENIED
    ("plan", "safe", "read_file", "", "{}", False, False),
    ("plan", "safe", "grep", "", "{}", False, False),
    ("plan", "destructive", "bash", "", '{"command":"ls -la"}', False, False),  # read-only bash
    ("plan", "caution", "write_file", "", "{}", False, True),
    ("plan", "destructive", "bash", "", '{"command":"rm -rf x"}', False, True),
    ("plan", "caution", "artifact_save", "", "{}", True, True),  # plan != build
    # ask: declared reads allowed, every change denied (deny-by-default)
    ("ask", "safe", "read_file", "", "{}", False, False),
    ("ask", "destructive", "bash", "", '{"command":"ls -la"}', False, False),  # read-only bash
    ("ask", "destructive", "bash", "", '{"command":"rm -rf x"}', False, True),
    ("ask", "destructive", "bash", "", '{"command":"cat a > b"}', False, True),  # redirect
    ("ask", "caution", "write_file", "", "{}", False, True),
    ("ask", "caution", "artifact_save", "", "{}", True, True),
    ("ask", "safe", "memory_recall", "", "{}", False, False),
    ("ask", "destructive", "memory_forget", "", "{}", False, True),
    ("ask", "caution", "subagent_run", "", "{}", False, True),
    # The measured holes: a change whose NAME carries no write-shaped word used to pass.
    ("ask", "caution", "memory_remember", "", '{"rule":"x"}', False, True),
    ("ask", "caution", "computer_click", "", '{"element_index":1}', False, True),
    ("ask", "caution", "workflow_start", "", '{"name":"w"}', False, True),
    # The declaration decides, never the name: a read-shaped name that declares a change is one,
    # and a name that declares nothing is not a read — nor is an ACP CLI's "read" kind.
    ("ask", "caution", "read_file", "", "{}", False, True),
    ("ask", "", "memory_recall", "", "{}", False, True),
    ("ask", "", "Read /etc/hosts", "read", "{}", False, True),
    # An app's `run_script` that declares CAUTION does not become a read because its argument
    # reads like one; only the platform's own shell has its command screened.
    ("ask", "caution", "run_script", "", '{"command":"ls"}', False, True),
    # ...while an ACP CLI's shell call still does (kind execute, the command text decides).
    ("ask", "", "Terminal", "execute", '{"command":"ls"}', False, False),
    # build: reads + the tools that declare they build the deliverable; other changes denied
    ("build", "safe", "read_file", "", "{}", False, False),
    ("build", "caution", "artifact_save", "", "{}", True, False),
    ("build", "caution", "image_generate", "", '{"prompt":"a cat"}', True, False),
    ("build", "safe", "skill_invoke", "", "{}", False, False),
    ("build", "destructive", "bash", "", '{"command":"rm -rf x"}', False, True),
    ("build", "caution", "write_file", "", "{}", False, True),
    # Build never admits a destructive tool, whatever it declares about building.
    ("build", "destructive", "artifact_delete", "", "{}", True, True),
    # A producer-shaped NAME admits nothing: only the declaration does.
    ("build", "caution", "widget_create", "", "{}", False, True),
    ("build", "", "Write /tmp/image.png", "edit", "{}", False, True),
    ("ask", "caution", "image_generate", "", '{"prompt":"a cat"}', True, True),
    ("plan", "caution", "image_generate", "", '{"prompt":"a cat"}', True, True),
    ("agent", "caution", "image_generate", "", '{"prompt":"a cat"}', True, False),
    ("ask", "safe", "prompt_render", "", "{}", False, False),
]


@pytest.mark.parametrize("mode,declared,title,kind,inp,builds,want_deny", _CASES)
def test_task_mode_gate(mode, declared, title, kind, inp, builds, want_deny):
    denied = bool(task_mode_denies(_S(mode), declared, title, kind, inp, builds=builds))
    assert denied is want_deny, f"[{mode}] {declared}/{title}/{kind}: got deny={denied}"


def test_agent_mode_never_denies():
    s = _S("agent")
    for declared, title, kind in [
        ("caution", "anything", "edit"),
        ("destructive", "bash", "command"),
        ("", "delete_all", "delete"),
    ]:
        assert task_mode_denies(s, declared, title, kind, "{}") == ""


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
