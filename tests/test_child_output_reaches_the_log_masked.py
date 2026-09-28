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

#: What a child's output may pass through on its way into a log line: the masker, which also
#: keeps it on one line, the refusal a transport gets in PersonalClaw's own words, and ``len``
#: (a size is not output).
_LOG_MASKERS = {"mask_child_output", "transport_refusal", "len"}
#: On its way to the owner's terminal, in a printed line or a raised error: those, and the
#: helpers built on the masker for a service command's words and a failed git step's.
_TERMINAL_MASKERS = _LOG_MASKERS | {"command_said", "_git_said"}
#: Into a record a view shows (a JSON response, an artifact): those, and the display mask the
#: views apply (the trigger handlers call it ``_redact``), since a record is not a terminal.
_RECORD_MASKERS = _TERMINAL_MASKERS | {"redact_for_display", "_redact"}


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


def _raw_child_output(
    node: ast.AST, maskers: set[str] = _LOG_MASKERS, masked: bool = False
) -> list[str]:
    """Each ``x.stdout``/``x.stderr``/``x.output`` in *node* that reaches the log unmasked.

    Masked: inside one of *maskers* (for a log line, ``mask_child_output(...)``). Not output:
    inside ``len(...)`` (a size), or the test of a conditional
    (``mask_child_output(r.stderr) if r.stderr else ...``)."""
    found: list[str] = []
    if isinstance(node, ast.Call):
        callee = node.func.attr if isinstance(node.func, ast.Attribute) else ""
        callee = callee or getattr(node.func, "id", "")
        masked = masked or callee in maskers
    if isinstance(node, ast.IfExp):
        return _raw_child_output(node.body, maskers, masked) + _raw_child_output(
            node.orelse, maskers, masked
        )
    if isinstance(node, ast.Attribute) and node.attr in _CHILD_OUTPUT and not masked:
        found.append(ast.unparse(node))
    for child in ast.iter_child_nodes(node):
        found += _raw_child_output(child, maskers, masked)
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


#: Records that keep a child's own output on purpose, and why that output is not a child's
#: words: ``file::key``.
_RECORD_EXEMPT = {
    # A commit id: what `git rev-parse HEAD` prints about the history's own repository.
    "durability/state_history.py::'head'": "a commit id the history names its HEAD with",
}


def _unmasked_terminal_errors_and_records(tree: ast.AST, rel: str) -> list[str]:
    """Each ``print(...)``, raised error and dict record in *tree* that carries a child's raw
    ``stdout`` or ``stderr``. A record's ``output`` is left out: in a record it is a node's or a
    tool's result, which the log rail's wider set would misread as a child's."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print":
            raw = [r for a in node.args for r in _raw_child_output(a, _TERMINAL_MASKERS)]
            if raw:
                out.append(f"{node.lineno}: print {', '.join(raw)}")
        elif isinstance(node, ast.Raise) and node.exc is not None:
            raw = _raw_child_output(node.exc, _TERMINAL_MASKERS)
            if raw:
                out.append(f"{node.lineno}: raise {', '.join(raw)}")
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                raw = [
                    r
                    for r in _raw_child_output(value, _RECORD_MASKERS)
                    if r.endswith((".stdout", ".stderr"))
                ]
                shown = ast.unparse(key) if key is not None else "**"
                if raw and f"{rel}::{shown}" not in _RECORD_EXEMPT:
                    out.append(f"{value.lineno}: record {shown}: {', '.join(raw)}")
    return sorted(out, key=lambda hit: int(hit.split(":")[0]))


def test_no_printed_line_error_or_record_in_the_tree_carries_a_childs_raw_output():
    """What sudo, systemctl, launchctl or git printed reaches the owner's terminal through
    ``print`` and ``raise``, and what an evaluation cell's child printed reached its artifact,
    both as printed."""
    found = {}
    scanned = 0
    for path in sorted(_SRC.rglob("*.py")):
        scanned += 1
        rel = path.relative_to(_SRC).as_posix()
        hits = _unmasked_terminal_errors_and_records(
            ast.parse(path.read_text(encoding="utf-8")), rel
        )
        if hits:
            found[rel] = hits
    assert scanned > 500, f"the scan read {scanned} files: it is not reading the tree"
    assert not found, (
        "a printed line, a raised error or a written record carries what a child printed as it "
        f"printed it. Pass it through `security.mask_child_output`: {found}"
    )


def test_the_terminal_and_record_rail_sees_the_shapes_it_is_for():
    src = (
        "def f(res, proc):\n"
        "    print(f'failed:\\n{(res.stderr or \"\").strip()}')\n"
        "    raise ServiceInstallError(f'said: {(res.stderr or res.stdout).strip()}')\n"
        "    record = {'stdout_tail': (proc.stdout or '')[-2000:], 'output': proc.output}\n"
        "    print(command_said(res))\n"
        "    raise ServiceInstallError(mask_child_output(res.stderr))\n"
        "    ok = {'stdout_tail': mask_child_output(proc.stdout, tail=True)}\n"
    )
    hits = _unmasked_terminal_errors_and_records(ast.parse(src), "example.py")
    assert [h.split(":")[0] for h in hits] == ["2", "3", "4"], hits
    assert "record 'stdout_tail'" in hits[2] and "output" not in hits[2], hits


# ── the rail: every sink the log records reach masks what it writes ────────────────────────

#: The standard library's stream and file handlers. Each writes a record it could not emit to
#: stderr as it came, message and arguments unmasked (``logging.Handler.handleError``), so a sink
#: is made of ``security``'s withholding ones instead.
_FAILS_OPEN_HANDLERS = {
    "StreamHandler",
    "FileHandler",
    "RotatingFileHandler",
    "TimedRotatingFileHandler",
    "WatchedFileHandler",
}

#: The logging handler classes a sink can be made of; the tree's own subclasses join them.
_HANDLER_CLASSES = _FAILS_OPEN_HANDLERS | {"MaskedStreamHandler", "MaskedRotatingFileHandler"}


def _tail_name(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    return getattr(node, "id", "")


def _sink_problems(tree: ast.AST, handler_classes: set[str]) -> tuple[list[str], list[str]]:
    """``(problems, sinks)``: each log handler made in *tree* without the masking formatter, or
    of a class whose failure path writes the record as it came, a formatter that is not the
    masking one and a ``basicConfig`` that builds its own; and the functions that make a handler
    and give it the masking formatter."""
    problems: list[str] = []
    sinks: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _tail_name(node.func)
            if name in _FAILS_OPEN_HANDLERS:
                problems.append(
                    f"{node.lineno}: a {name}, whose failure path writes the record as it came"
                )
            # A class, so capitalised: `setFormatter` is the call that attaches one.
            if name.endswith("Formatter") and name[:1].isupper() and name != "MaskingFormatter":
                problems.append(f"{node.lineno}: a {name}, which masks nothing")
            if name == "basicConfig":
                keywords = {k.arg for k in node.keywords}
                if "format" in keywords or "handlers" not in keywords:
                    problems.append(f"{node.lineno}: a basicConfig that makes its own formatter")
    seen: set[int] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        made = {
            target.id: node.lineno
            for node in ast.walk(fn)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and _tail_name(node.value.func) in handler_classes
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        seen |= {
            id(node.value)
            for node in ast.walk(fn)
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
        }
        masked = {
            node.func.value.id
            for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and _tail_name(node.func) == "setFormatter"
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.args
            and isinstance(node.args[0], ast.Call)
            and _tail_name(node.args[0].func) == "MaskingFormatter"
        }
        for handler, line in made.items():
            if handler in masked:
                sinks.append(fn.name)
            else:
                problems.append(f"{line}: {fn.name} makes a log handler without MaskingFormatter")
    # A handler made anywhere but a name in a function (`addHandler(StreamHandler())`) is one
    # whose formatter the rail cannot see, so it cannot pass.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and _tail_name(node.func) in handler_classes
            and id(node) not in seen
        ):
            problems.append(f"{node.lineno}: a log handler made where its formatter cannot be seen")
    return problems, sinks


def test_every_log_sink_the_tree_makes_masks_what_it_writes():
    """``gateway.log``, the console stream a service manager keeps, and the Logs page's buffer
    and live stream: a record reaches each through a handler, and the handler's formatter is
    what masks it."""
    trees = {
        path.relative_to(_SRC).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(_SRC.rglob("*.py"))
    }
    handler_classes = set(_HANDLER_CLASSES)
    classes = [
        node for tree in trees.values() for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    ]
    grew = True
    while grew:  # a subclass of a handler class is one too
        before = len(handler_classes)
        handler_classes |= {
            node.name
            for node in classes
            if any(_tail_name(base) in handler_classes | {"Handler"} for base in node.bases)
        }
        grew = len(handler_classes) > before
    found: dict[str, list[str]] = {}
    sinks: set[str] = set()
    for rel, tree in trees.items():
        problems, made = _sink_problems(tree, handler_classes)
        sinks |= {f"{rel}::{fn}" for fn in made}
        if problems:
            found[rel] = problems
    assert not found, f"a log sink writes records as they are: {found}"
    for sink in (
        "cli.py::_console_log_handler",
        "cli.py::_gateway_log_handler",
        "dashboard/handlers/updates.py::install_log_ring_handler",
        "dashboard/handlers/updates.py::api_logs",
        "providers/availability_probe.py::main",
    ):
        assert sink in sinks, f"the rail no longer sees {sink}: it would pass vacuously"


def test_the_sink_rail_sees_the_shapes_it_is_for():
    src = (
        "def plain(path):\n"
        "    handler = MaskedRotatingFileHandler(path)\n"
        "    handler.setFormatter(logging.Formatter('%(message)s'))\n"
        "def masked(path):\n"
        "    handler = MaskedRotatingFileHandler(path)\n"
        "    handler.setFormatter(MaskingFormatter('%(message)s'))\n"
        "logging.basicConfig(format='%(message)s')\n"
        "logging.getLogger().addHandler(MaskedStreamHandler())\n"
        "def fails_open(path):\n"
        "    handler = RotatingFileHandler(path)\n"
        "    handler.setFormatter(MaskingFormatter('%(message)s'))\n"
    )
    problems, sinks = _sink_problems(ast.parse(src), set(_HANDLER_CLASSES))
    assert sorted(p.split(":")[0] for p in problems) == ["10", "2", "3", "7", "8"], problems
    assert "failure path" in next(p for p in problems if p.startswith("10:")), problems
    assert sinks == ["masked", "fails_open"], sinks


# ── the sinks themselves ───────────────────────────────────────────────────────────────────


def _record(msg: str, *args: object, exc: bool = False) -> logging.LogRecord:
    exc_info = None
    if exc:
        try:
            raise RuntimeError(f"the service answered with {_KEY}")
        except RuntimeError:
            import sys

            exc_info = sys.exc_info()
    return logging.LogRecord(
        "personalclaw.example", logging.ERROR, __file__, 1, msg, args, exc_info
    )


def test_gateway_log_keeps_no_credential_a_record_carried(tmp_path):
    """🔴 Before, gateway.log was written with a plain formatter: a key in a record's argument
    or in an exception's text reached the file as it was."""
    from personalclaw.cli import _gateway_log_handler

    log_file = tmp_path / "gateway.log"
    handler = _gateway_log_handler(log_file, logging.DEBUG)
    try:
        handler.emit(_record("connected with %s", _KEY))
        handler.emit(_record("the sync failed", exc=True))
    finally:
        handler.close()
    written = log_file.read_text(encoding="utf-8")
    assert "connected with [REDACTED" in written, written
    assert "Traceback" in written and "RuntimeError" in written, "the traceback is kept"
    assert _KEY not in written, written


def test_the_console_the_service_manager_keeps_masks_too():
    """launchd writes the console stream to its log files and systemd to its journal."""
    import io

    from personalclaw.cli import _console_log_handler

    handler = _console_log_handler()
    stream = io.StringIO()
    handler.setStream(stream)
    handler.emit(_record("token %s rejected", _KEY))
    assert "[REDACTED" in stream.getvalue() and _KEY not in stream.getvalue(), stream.getvalue()


def test_an_evaluation_cells_artifact_keeps_its_childs_output_masked(tmp_path, monkeypatch):
    """🔴 Before, a cell's ``result.json`` kept the last 2000 characters its child printed on
    stdout and stderr as they were."""
    import json
    import types

    from test_evals_matrix_runner import write_pinnable_home

    from personalclaw.evals import runner as runner_mod
    from personalclaw.evals import store as evals_store
    from personalclaw.evals.matrix import MatrixSpec

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    write_pinnable_home(tmp_path)

    def child(args, **_kwargs):
        return types.SimpleNamespace(
            returncode=1,
            stdout=f"loading\nkey {_KEY}\n",
            stderr=f"Traceback …\nRuntimeError: sign-in refused for {_KEY}\x1b[2K\n",
        )

    monkeypatch.setattr(runner_mod.subprocess, "run", child)
    runner_mod.run_matrix(MatrixSpec(subject="s", axes={}, trial_count=1), matrix_id="m-tails")

    artifact = json.loads(
        (evals_store.matrix_dir("m-tails") / "cell-0000" / "result.json").read_text("utf-8")
    )
    assert _KEY not in artifact["stdout_tail"] and _KEY not in artifact["stderr_tail"], artifact
    assert "sign-in refused for [REDACTED" in artifact["stderr_tail"], artifact
    assert "\n" in artifact["stderr_tail"] and "\\x1b[2K" in artifact["stderr_tail"], artifact


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
