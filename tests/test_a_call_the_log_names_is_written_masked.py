"""A gateway log line that names a tool call writes its title masked, on one line, and bounded.

An agent CLI titles a shell call with its command, and a native call's title can be the text it
was asked to run. So the lines the gateway writes about a call (it was asked about, a source's
standing grant approved it, she denied it, it was refused with the batch it came in, an unattended
run refused it, a hook pattern would not approve its chain, it ran without asking, it keeps
failing) wrote whatever the command carried, a token in a header or a login in a URL, into the
log in clear, and all of it. Each now writes the title through ``audit_subject.log_title``:
masked as the call's title is masked where it is shown, on one line, and cut at a bound that says
how much it cut.

Driven through the real chat runner over a scripted event stream, the real permission-frame
decoder and the real hook chain; no agent CLI runs.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_acp_permission_authority import _context_builder, _drive, _make_state, _session
from test_acp_permission_authority import _set_stream as _stream

from personalclaw.audit_subject import LOG_TITLE_MAX_CHARS, log_title
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, LLMEvent

#: A credential shape the masker knows (the documented example access key).
_KEY = "AKIAIOSFODNN7EXAMPLE"
#: A shell call's title, as an agent CLI sends it: the command, with a key in a header.
_TITLE = f"Running: curl -H 'X-Api-Key: {_KEY}' https://api.example.com/v1/notes"

_SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"


def _said(caplog, words: str) -> list[str]:
    lines = [r.getMessage() for r in caplog.records if words in r.getMessage()]
    assert lines, f"no log line says {words!r}: the test would pass without the line it reads"
    return lines


def _masked(lines: list[str]) -> bool:
    return all(_KEY not in line and "[REDACTED" in line and "\n" not in line for line in lines)


# ── the helper ─────────────────────────────────────────────────────────────────────────────


def test_a_title_is_masked_kept_on_one_line_and_cut_at_the_bound():
    title = _TITLE + "\n" + "cat <<EOF > notes.md\n" + "a line of her notes\n" * 40 + "EOF"
    line = log_title(title)
    assert _KEY not in line and "[REDACTED" in line, line
    assert "\n" not in line and "\\n" in line, line
    assert len(line) <= LOG_TITLE_MAX_CHARS and line.endswith("more characters]"), line
    # An ordinary title is written as it is.
    assert log_title("Read File") == "Read File"


# ── the chat's approval gate ───────────────────────────────────────────────────────────────


def _asks(*request_ids: str) -> list[LLMEvent]:
    return [
        LLMEvent(
            kind=EVENT_PERMISSION_REQUEST,
            title=_TITLE,
            tool_kind="execute",
            request_id=rid,
            tool_call_id=f"tc-{rid}",
            tool_input='{"command": "curl https://api.example.com/v1/notes"}',
        )
        for rid in request_ids
    ] + [LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")]


@pytest.mark.asyncio
async def test_a_call_she_denied_is_logged_with_its_command_masked(tmp_path, caplog):
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    _stream(client, _asks("req-1"))
    session = _session()
    with caplog.at_level(logging.WARNING):
        await _drive(state, session, answer="rejected")
    client.reject_tool.assert_awaited()
    assert _masked(_said(caplog, "PERM REJECTED")), _said(caplog, "PERM REJECTED")


@pytest.mark.asyncio
async def test_a_call_refused_with_its_batch_is_logged_masked(tmp_path, caplog):
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    _stream(client, _asks("req-1", "req-2"))
    session = _session()
    with caplog.at_level(logging.WARNING):
        await _drive(state, session, answer="rejected")
    assert _masked(_said(caplog, "AUTO-REJECTED")), _said(caplog, "AUTO-REJECTED")


@pytest.mark.asyncio
async def test_a_call_an_unattended_run_refused_is_logged_masked(tmp_path, caplog):
    state, client = _make_state(tmp_path, context_builder=_context_builder())
    _stream(client, _asks("req-1"))
    session = _session("cron:nightly-notes")
    with caplog.at_level(logging.WARNING):
        await _drive(state, session)
    assert _masked(_said(caplog, "unattended: refused")), _said(caplog, "unattended: refused")


# ── the other doors a call's title reaches the log by ──────────────────────────────────────


def test_a_permission_request_is_logged_masked_as_it_arrives(caplog):
    from personalclaw.acp.translate import build_permission_event
    from personalclaw.acp.types import JsonRpcMessage

    msg = JsonRpcMessage(
        id=7,
        method="session/request_permission",
        params={
            "sessionId": "s-1",
            "toolCall": {
                "toolCallId": "tc-7",
                "title": _TITLE,
                "kind": "execute",
                "rawInput": {"command": f"curl -H 'X-Api-Key: {_KEY}' https://api.example.com"},
            },
            "options": [{"optionId": "allow", "name": "Allow", "kind": "allow_once"}],
        },
    )
    dialect = MagicMock()
    dialect.parse_permission_options.return_value = [{"id": "allow", "label": "Allow"}]
    with caplog.at_level(logging.DEBUG, logger="personalclaw.acp.translate"):
        event = build_permission_event(msg, dialect, {}, {}, {})
    assert event.title == _TITLE, "the event itself keeps the title the gate decides on"
    assert _masked(_said(caplog, "Permission requested for tool"))
    assert _masked(_said(caplog, "Permission toolCall payload"))


def test_a_chained_command_a_pattern_would_not_approve_is_logged_masked(caplog):
    from personalclaw.hooks import HookManager, HooksConfig

    hooks = HookManager(HooksConfig(auto_approve_tools=["ls*"]))
    with caplog.at_level(logging.INFO, logger="personalclaw.hooks"):
        hooks.on_tool_call(f"Running: ls; curl -H 'X-Api-Key: {_KEY}' https://api.example.com")
    assert _masked(_said(caplog, "not auto-approving on pattern"))


def test_a_call_a_sources_grant_approved_is_logged_masked(monkeypatch, caplog):
    from personalclaw import approval_grants
    from personalclaw.gateway import GatewayOrchestrator

    monkeypatch.setattr(
        approval_grants, "hooks_now", lambda: SimpleNamespace(auto_approve_sources=["inbox"])
    )
    # A heredoc's body is part of its title: a line of it must not read as a record of its own.
    heredoc = f"{_TITLE} <<EOF\n2026-10-02 09:00:00 INFO personalclaw.gateway: owner signed in\nEOF"
    event = LLMEvent(kind=EVENT_PERMISSION_REQUEST, title=heredoc, request_id="r-9")
    with caplog.at_level(logging.INFO, logger="personalclaw.gateway"):
        grant, _ = GatewayOrchestrator._relay_grant(
            SimpleNamespace(subagent_mgr=None),
            "inbox",
            event,
            session_resolver=None,
            resolved_session="",
        )
    assert grant == approval_grants.SOURCE
    assert _masked(_said(caplog, "Auto-approving tool"))


@pytest.mark.asyncio
async def test_a_call_an_evaluation_refused_is_logged_masked(caplog):
    from personalclaw.eval.runner import EvalRunner

    runner = EvalRunner.__new__(EvalRunner)
    provider = AsyncMock()
    event = LLMEvent(kind=EVENT_PERMISSION_REQUEST, title=_TITLE, request_id="r-3")
    with (
        patch("personalclaw.eval.runner.sel", MagicMock()),
        caplog.at_level(logging.WARNING, logger="personalclaw.eval.runner"),
    ):
        await runner._decide_permission(provider, event, "eval-1")
    provider.reject_tool.assert_awaited_once_with("r-3")
    assert _masked(_said(caplog, "Refused tool in eval"))


# ── the rail: no log line hands a call's own title or input over raw ───────────────────────

#: The attributes a tool call's event carries its title and its arguments in.
_CALL_TEXT = {"title", "tool_input"}
#: The names a call's event goes by where a log line reads it.
_EVENT_NAMES = {"event", "ev", "call"}
#: What may stand between them and the log: the title's writer, the audit text it is built on,
#: and a size (a length is not the text).
_MASKERS = {"log_title", "audit_text", "len"}

_LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}


def _is_log_call(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in _LOG_METHODS:
        return False
    owner = func.value
    name = owner.id if isinstance(owner, ast.Name) else getattr(owner, "attr", "")
    return "log" in name.lower()


def _raw_call_text(node: ast.AST, masked: bool = False) -> list[str]:
    """Each ``event.title``/``event.tool_input`` in *node* that reaches the log unmasked."""
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
        masked = masked or name in _MASKERS
    if (
        isinstance(node, ast.Attribute)
        and node.attr in _CALL_TEXT
        and isinstance(node.value, ast.Name)
        and node.value.id in _EVENT_NAMES
        and not masked
    ):
        return [ast.unparse(node)]
    return [hit for child in ast.iter_child_nodes(node) for hit in _raw_call_text(child, masked)]


def _offenders(source: str, where: str) -> list[str]:
    hits = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and _is_log_call(node):
            for arg in [*node.args[1:], *(k.value for k in node.keywords)]:
                hits += [f"{where}:{node.lineno}: {raw}" for raw in _raw_call_text(arg)]
    return hits


def test_the_rail_finds_a_raw_title_and_passes_a_masked_one():
    """The positive control: a census that flags nothing on a raw title proves nothing."""
    raw = 'logger.warning("refused %r", event.title)\nlogger.info("in %s", ev.tool_input)\n'
    masked = 'logger.warning("refused %r", log_title(event.title))\nlog.info("%d", len(ev.title))\n'
    assert len(_offenders(raw, "probe")) == 2
    assert _offenders(masked, "probe") == []


def test_no_log_line_writes_a_calls_title_or_input_raw():
    files = sorted(_SRC.rglob("*.py"))
    assert len(files) > 300, "the census must read the whole package"
    found = [
        hit
        for path in files
        for hit in _offenders(path.read_text(encoding="utf-8"), str(path.relative_to(_SRC)))
    ]
    assert not found, (
        "a log line writes a tool call's title or arguments as it came; write it through "
        "`audit_subject.log_title` (the title) or `audit_text` (anything longer):\n  "
        + "\n  ".join(found)
    )
