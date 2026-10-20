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

from sel_subject_census_shared import _problem

from personalclaw.audit_subject import COMMAND, FILE_PATH, kind_of_subject
from personalclaw.file_scope import PATH_TOOLS
from personalclaw.run_bounds import FILE_WRITES
from personalclaw.task_modes import PLATFORM_SHELL, SHELL_TOOL_NAMES


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
