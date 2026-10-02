"""Every audited tool call hands its arguments to the log, so its row can say what it ran.

The security log's row of a shell call records its command, and the row of a call that changes a
file its path (``audit_subject.subject_of``), from the arguments the row's writer hands over
(``log_tool_invocation(tool_input=...)``). A writer that hands none writes a row naming its tool and
nothing else, which is how every ``bash`` row read before: ``resources`` empty, the command only in
the session's transcript.

This census reads every ``log_tool_invocation`` call in ``src/personalclaw``:

* a row whose tool is named at run time (``tool_name=event.title``, a variable) hands over its
  call's arguments, ``tool_input=``: ``None`` written out for a row that is about no call;
* a row whose tool is written in the code (a literal name, or an f-string's literal head) names a
  tool whose rows record no command or path (``audit_subject.kind_of_subject``), or hands its
  arguments over too.

So a new path that audits a shell call or a file write cannot leave out what it ran.
"""

from __future__ import annotations

import ast
from pathlib import Path

from personalclaw.audit_subject import COMMAND, FILE_PATH, kind_of_subject
from personalclaw.file_scope import PATH_TOOLS
from personalclaw.run_bounds import FILE_WRITES
from personalclaw.task_modes import PLATFORM_SHELL, SHELL_TITLE_PREFIXES, SHELL_TOOL_NAMES

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: Every name a call's row records a command or a path for, by name alone.
_SUBJECT_NAMES = frozenset(SHELL_TOOL_NAMES) | frozenset(FILE_WRITES)


def _literal(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _fixed_head(node: ast.expr | None) -> str | None:
    """An f-string name's literal head when no subject tool's name can be completed from it."""
    if not isinstance(node, ast.JoinedStr) or not node.values:
        return None
    head = _literal(node.values[0])
    if not head or head.lower().startswith(SHELL_TITLE_PREFIXES):
        return None
    if any(name.startswith(head) for name in _SUBJECT_NAMES):
        return None
    return head


def _problem(call: ast.Call) -> str:
    """Why this audit row would leave out what its call ran, or ``""`` when it cannot."""
    keywords = {k.arg: k.value for k in call.keywords if k.arg}
    if "tool_input" in keywords:
        return ""
    name, kind = keywords.get("tool_name"), keywords.get("tool_kind")
    kind_text = _literal(kind) if kind is not None else ""
    if kind_text is None:
        return "its tool's kind is read at run time, and it hands over none of the call's arguments"
    fixed = shown = _literal(name)
    if fixed is None:
        fixed = _fixed_head(name)
        if fixed is None:
            return "its tool is named at run time, and it hands over none of the call's arguments"
        shown = f"{fixed}…"
    if kind_of_subject(fixed, kind_text):
        return f"{shown!r} is a tool whose row records what it ran, and no arguments come with it"
    return ""


def _audit_calls() -> list[tuple[str, int, ast.Call]]:
    found: list[tuple[str, int, ast.Call]] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "log_tool_invocation"
            ):
                found.append((str(path.relative_to(SRC)), node.lineno, node))
    return found


def test_every_audited_tool_call_hands_the_log_what_it_ran():
    calls = _audit_calls()
    problems = [f"{where}:{line}: {why}" for where, line, call in calls if (why := _problem(call))]
    assert not problems, (
        "these audit rows would name a tool and not what it ran — hand the call's arguments over "
        "(`tool_input=`), or `tool_input=None` for a row about no call:\n  " + "\n  ".join(problems)
    )


def test_the_census_reads_the_whole_surface():
    """Vacuity floor: a census that found a handful of calls would pass the test above."""
    calls = _audit_calls()
    handing = [c for _, _, c in calls if any(k.arg == "tool_input" for k in c.keywords)]
    assert len(calls) >= 200, len(calls)
    assert len(handing) >= 40, len(handing)
    files = {where for where, _, _ in calls}
    for host in (
        "dashboard/chat_runner.py",
        "subagent.py",
        "llm_helpers.py",
        "eval/runner.py",
        "dashboard/ungated_calls.py",
        "dashboard/handlers/tools.py",
        "mcp_shared.py",
    ):
        assert host in files, f"the census no longer reads {host}"


def _parsed(source: str) -> ast.Call:
    call = ast.parse(source, mode="eval").body
    assert isinstance(call, ast.Call)
    return call


def test_the_census_flags_a_row_that_leaves_out_what_ran():
    """Positive control: the shapes the census exists to catch are caught, and the rest pass."""
    flagged = [
        'sel().log_tool_invocation(session_key="", tool_name=event.title, outcome="approved")',
        'sel().log_tool_invocation(session_key="", tool_name="bash", outcome="approved")',
        'sel().log_tool_invocation(session_key="", tool_name="write_file", outcome="denied")',
        'sel().log_tool_invocation(session_key="", tool_name=f"Running: {cmd}", outcome="ok")',
        'sel().log_tool_invocation(session_key="", tool_name="x", tool_kind=kind, outcome="ok")',
        'sel().log_tool_invocation(session_key="", tool_name="E", tool_kind="edit", outcome="x")',
        'sel().log_tool_invocation(session_key="", tool_name=title, outcome="ok", **extra)',
    ]
    for source in flagged:
        assert _problem(_parsed(source)), source
    passing = [
        'sel().log_tool_invocation(session_key="", tool_name=event.title, tool_input=event.tool_input, outcome="approved")',  # noqa: E501
        'sel().log_tool_invocation(session_key="", tool_name=name, tool_input=None, outcome="reaped")',  # noqa: E501
        'sel().log_tool_invocation(session_key="", tool_name="file_read", outcome="ok")',
        'sel().log_tool_invocation(session_key="", tool_name=f"knowledge.{tool}", outcome="ok")',
    ]
    for source in passing:
        assert not _problem(_parsed(source)), source


def test_the_classifier_knows_every_shell_and_every_file_write():
    """The census and the log ask one classifier, so it must know the whole tool universe: the
    platform's shell and every shell an agent CLI sends by name, and every native tool that
    changes a file, while the native tools that only read a path record none."""
    assert kind_of_subject(PLATFORM_SHELL) == COMMAND
    for name in SHELL_TOOL_NAMES:
        assert kind_of_subject(name) == COMMAND, name
    for name in FILE_WRITES:
        assert kind_of_subject(name) == FILE_PATH, name
    for name, (_arg, changes, _default) in PATH_TOOLS.items():
        if not changes:
            assert kind_of_subject(name) == "", name
    assert FILE_WRITES, "no native tool changes a file: the census would check nothing"
