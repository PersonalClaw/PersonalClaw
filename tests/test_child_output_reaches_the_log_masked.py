"""What a child process prints reaches the gateway log masked, cut after masking, and on one line.

A hook, git, pip, an installer or a bundler can print a credential it read, and the gateway log
outlives the run: people read it, a support bundle carries it, and ``session_health`` parses its
lines back. A hook's stdout was logged at INFO (``Hook <name> stdout: <its first 200 chars>``),
and the other places that log a child's output cut it to a length first and masked nothing, so a
key a child printed landed in the log, or half of one did when the cut fell inside it.

``security.mask_child_output`` is the one way PersonalClaw writes a child's output into a log line
or an error: masked like every view masks, then cut, then with control characters and line breaks
as visible escapes, so a child cannot start a line that reads as one of the log's own records.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from personalclaw.security import mask_child_output

#: A credential shape the display mask knows (the documented example access key).
_KEY = "AKIAIOSFODNN7EXAMPLE"

_SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"


# ── the helper ─────────────────────────────────────────────────────────────────────────────


def test_a_credential_is_masked_before_the_cut_so_no_half_of_it_is_left():
    printed = "x" * 190 + f" {_KEY} and the rest"
    assert _KEY[:9] in printed[:200], "the cut must fall inside the key for this to test anything"
    shown = mask_child_output(printed, limit=200)
    assert "AKIA" not in shown and "[REDACTED" in shown, shown


def test_the_tail_is_kept_when_that_is_where_the_reason_is():
    shown = mask_child_output(
        "resolving...\n" * 50 + "error: no matching version", limit=26, tail=True
    )
    assert shown == "error: no matching version", shown
    assert mask_child_output("a" * 300, limit=None) == "a" * 300


def test_a_child_cannot_write_a_line_of_its_own_into_the_log():
    printed = "ok\n2026-01-01 00:00:00 INFO personalclaw.gateway: owner signed in\x1b[2K\u2028done"
    shown = mask_child_output(printed)
    assert "\n" not in shown and "\x1b" not in shown and "\u2028" not in shown, repr(shown)
    assert "ok\\n2026-01-01" in shown and "\\x1b[2K" in shown and "\\u2028done" in shown


def test_an_error_a_person_reads_keeps_its_line_breaks():
    printed = f"line one\r\nline two {_KEY}\x07\n"
    shown = mask_child_output(printed, limit=None, one_line=False)
    assert shown.splitlines()[0] == "line one" and "\r" not in shown, repr(shown)
    assert _KEY not in shown and "\\x07" in shown, repr(shown)


def test_bytes_and_nothing_are_both_handled():
    assert mask_child_output(f"fatal: {_KEY}\n".encode()).startswith("fatal: [REDACTED")
    assert mask_child_output(b"\xff\xfe broken").endswith("broken")
    assert mask_child_output(None) == "" and mask_child_output("") == ""


# ── a hook's output in a chat turn ─────────────────────────────────────────────────────────


async def _chat_turn(tmp_path, results):
    """One dashboard chat turn whose hook store answers every fire with *results*."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.dashboard.state import DashboardState, _ChatSession
    from personalclaw.history import ConversationLog
    from personalclaw.hooks import ToolHookResult
    from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent

    async def _events():
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Done.")
        yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    client = AsyncMock()
    client.provider_id = "native"
    client.model_substitution = None
    client.stream = MagicMock(side_effect=lambda *a, **kw: _events())
    client.context_usage_pct = MagicMock(return_value=None)
    client.supports_native_commands = False
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    builder = MagicMock()
    builder.hooks.on_tool_call.return_value = ToolHookResult.allow()
    builder.build_message.return_value = ("Tidy my notes.", None)
    state.context_builder = builder
    hook_store = MagicMock()
    hook_store.fire_for_ids = AsyncMock(return_value=results)
    state._hook_store = hook_store
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    session = _ChatSession("chat-hook-output")
    session._trust = True
    session.append("user", "Tidy my notes.", "msg msg-u")
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, "Tidy my notes.")
    return state


def _hook_result(**fields):
    from personalclaw.hooks import HOOK_EVENT_USER_PROMPT_SUBMIT, ScriptHookResult

    return ScriptHookResult(
        hook_id="hook-1", hook_name="context-hook", event=HOOK_EVENT_USER_PROMPT_SUBMIT, **fields
    )


@pytest.mark.asyncio
async def test_a_hooks_output_is_logged_by_its_length_and_never_its_text(tmp_path, caplog):
    printed = f"export SERVICE_KEY={_KEY}\n"
    with caplog.at_level(logging.DEBUG, logger="personalclaw"):
        await _chat_turn(tmp_path, [_hook_result(exit_code=0, stdout=printed)])
    lines = [r.getMessage() for r in caplog.records]
    assert f"Hook context-hook injected {len(printed)} chars" in lines, lines
    assert not [line for line in lines if _KEY in line], lines


@pytest.mark.asyncio
async def test_what_a_failing_hook_says_is_logged_masked(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="personalclaw"):
        await _chat_turn(tmp_path, [_hook_result(exit_code=1, stderr=f"token {_KEY} rejected")])
    warned = [r.getMessage() for r in caplog.records if "context-hook warning" in r.getMessage()]
    assert warned, "the failing hook's warning never reached the log: the test is vacuous"
    assert all(_KEY not in line and "[REDACTED" in line for line in warned), warned


@pytest.mark.asyncio
async def test_a_blocking_hooks_reason_is_masked_in_the_log_and_the_activity_line(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="personalclaw"):
        state = await _chat_turn(tmp_path, [_hook_result(exit_code=2, stderr=f"no: {_KEY}")])
    blocked = [r.getMessage() for r in caplog.records if "blocked tool" in r.getMessage()]
    assert blocked and all(_KEY not in line for line in blocked), blocked
    shown = [
        call.args[1]["text"]
        for call in state.broadcast_ws.call_args_list
        if call.args and call.args[0] == "activity_event" and call.args[1].get("kind") == "hook"
    ]
    assert shown and all(_KEY not in text for text in shown), shown


# ── the rail: a log line never carries a child's raw output ────────────────────────────────

#: Where what a child printed is read: ``CompletedProcess``, ``ScriptHookResult`` and
#: ``CalledProcessError`` keep it in these attributes.
_CHILD_OUTPUT = {"stdout", "stderr", "output"}


def _is_log_call(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in {
        "debug",
        "info",
        "warning",
        "warn",
        "error",
        "exception",
        "critical",
        "log",
    }:
        return False
    owner = func.value
    name = owner.id if isinstance(owner, ast.Name) else getattr(owner, "attr", "")
    return "log" in name.lower()


def _raw_child_output(node: ast.AST, masked: bool = False) -> list[str]:
    """Each ``x.stdout``/``x.stderr``/``x.output`` in *node* that reaches the log unmasked.

    Masked: inside ``mask_child_output(...)``. Not output: inside ``len(...)`` (a size), or the
    test of a conditional (``mask_child_output(r.stderr) if r.stderr else ...``)."""
    found: list[str] = []
    if isinstance(node, ast.Call):
        callee = node.func.attr if isinstance(node.func, ast.Attribute) else ""
        callee = callee or getattr(node.func, "id", "")
        masked = masked or callee in {"mask_child_output", "len"}
    if isinstance(node, ast.IfExp):
        return _raw_child_output(node.body, masked) + _raw_child_output(node.orelse, masked)
    if isinstance(node, ast.Attribute) and node.attr in _CHILD_OUTPUT and not masked:
        found.append(ast.unparse(node))
    for child in ast.iter_child_nodes(node):
        found += _raw_child_output(child, masked)
    return found


def _unmasked_log_calls(tree: ast.AST) -> list[str]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_log_call(node):
            raw = [
                r
                for a in [*node.args, *(k.value for k in node.keywords)]
                for r in _raw_child_output(a)
            ]
            if raw:
                out.append(f"{node.lineno}: {', '.join(raw)}")
    return out


def test_no_log_line_in_the_tree_carries_a_childs_raw_output():
    found = {}
    scanned = 0
    for path in sorted(_SRC.rglob("*.py")):
        scanned += 1
        hits = _unmasked_log_calls(ast.parse(path.read_text(encoding="utf-8")))
        if hits:
            found[path.relative_to(_SRC).as_posix()] = hits
    assert scanned > 500, f"the scan read {scanned} files: it is not reading the tree"
    assert not found, (
        "a log line writes what a child printed as it printed it. Pass it through "
        f"`security.mask_child_output` (masked, cut, escaped): {found}"
    )


def test_the_rail_sees_the_shapes_it_is_for():
    """The rail's matcher on snippets: the shape that shipped, the masked one, and a size."""
    src = (
        "def f(r, proc, exc):\n"
        "    logger.info('Hook %s stdout: %s', r.hook_name, r.stdout[:200])\n"
        "    logger.warning(f'git failed: {proc.stderr.strip()}')\n"
        "    log.debug('pip: %s', exc.output)\n"
        "    logger.info('Hook %s injected %d chars', r.hook_name, len(r.stdout))\n"
        "    logger.warning('x: %s', mask_child_output(r.stderr) if r.stderr else 'exit 2')\n"
    )
    hits = _unmasked_log_calls(ast.parse(src))
    assert [h.split(":")[0] for h in hits] == ["2", "3", "4"], hits
