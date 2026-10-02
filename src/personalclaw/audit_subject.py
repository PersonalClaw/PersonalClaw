"""What a tool call's audit row says it ran: a shell call's command, the file a write changes.

The security log is the tamper-evident answer to "what did it run" (:mod:`personalclaw.sel`), and a
row that named only its tool (``bash``, ``write_file``) could not give it: the command and the path
lived in the session's transcript alone. So the row of a shell call records its command, and the
row of a call that changes a file records the file it names (:func:`subject_of`), on every path
that audits a tool call, which hands the call's arguments to the log (``tool_input``).

That text is the call's own, and a command can carry a credential (a token in a header, a login in
a URL), so what the log keeps of it is :func:`audit_text`: masked the way a call's title is masked
where it is shown, on one line, and cut at a bound that says how much it cut.
"""

from __future__ import annotations

import json
import shlex
from typing import Any

#: The longest text one field of an audit row keeps, its cut marker included.
SUBJECT_MAX_CHARS = 500

#: The tool kinds an agent CLI reports for a call that changes a file (the Agent Client Protocol's
#: ``ToolKind``). A report, not a declaration: here it decides only what the audit row records.
FILE_CHANGE_KINDS: frozenset[str] = frozenset({"edit", "delete", "move"})

#: The arguments an agent CLI's file tools name their file by.
_PATH_ARGS = ("path", "file_path", "filePath", "notebook_path", "notebookPath")


def cut_marker(cut: int) -> str:
    """What ends a text :func:`audit_text` cut: how many characters it left out."""
    return f"…[cut: {cut:,} more characters]"


def audit_text(text: object, limit: int = SUBJECT_MAX_CHARS) -> str:
    """*text* as the audit log stores it: masked, on one line, and at most *limit* characters.

    Masked the way a call's title is masked where it is shown, exfiltration URLs and then
    credential shapes (``security.redact_or_withhold``, which withholds the whole text when the
    masker fails), BEFORE it is cut, so a credential that straddles the cut is masked whole. Then
    written on one line with every control character a visible escape
    (``security.mask_child_output``, the writer of text fit for a log line, whose own mask finds
    nothing left to mask), so a command cannot start a line that reads as a record of its own in a
    viewer or a terminal. A longer text keeps its start and ends with :func:`cut_marker`.
    """
    from personalclaw.security import WITHHELD_TEXT, mask_child_output, redact_or_withhold

    try:
        line = mask_child_output(redact_or_withhold(str(text or "")), limit=None)
    except Exception:  # noqa: BLE001 - a text its masker failed on is withheld, never stored
        return WITHHELD_TEXT
    if len(line) <= limit:
        return line
    # The most of the start that fits beside the marker saying how much is left out.
    keep = limit
    while keep > 0 and keep + len(cut_marker(len(line) - keep)) > limit:
        keep -= 1
    return line[:keep] + cut_marker(len(line) - keep)


def _arguments(tool_input: object) -> dict[str, Any]:
    """A call's arguments as a mapping: the native loop hands a dict, an agent CLI its JSON text."""
    if isinstance(tool_input, dict):
        return tool_input
    if isinstance(tool_input, str) and tool_input.lstrip().startswith("{"):
        try:
            parsed = json.loads(tool_input)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _diff_path(tool_input: object) -> str:
    """The file a unified diff names (``+++ <path>``): what an agent CLI's edit is handed on as
    when its frame declared the change (``acp.translate.make_unified_diff``)."""
    if not isinstance(tool_input, str):
        return ""
    for line in tool_input.splitlines()[:4]:
        if line.startswith("+++ "):
            return line[4:].strip()
    return ""


#: What :func:`kind_of_subject` answers for a shell call, and for a call that changes a file.
COMMAND, FILE_PATH = "command", "path"


def kind_of_subject(tool_name: str, tool_kind: str = "") -> str:
    """Which subject a call of this tool records: :data:`COMMAND` for a shell call
    (``task_modes.is_shell_invocation``), :data:`FILE_PATH` for a native tool that changes a file
    (``run_bounds.FILE_WRITES``) or an agent CLI's call it reports as changing one
    (:data:`FILE_CHANGE_KINDS`), and ``""`` for any other call, whose row names the tool alone."""
    from personalclaw.run_bounds import FILE_WRITES
    from personalclaw.task_modes import is_shell_invocation

    name, kind = str(tool_name or ""), str(tool_kind or "").lower()
    if is_shell_invocation(name, kind):
        return COMMAND
    return FILE_PATH if name in FILE_WRITES or kind in FILE_CHANGE_KINDS else ""


def subject_of(tool_name: str, tool_kind: str, tool_input: object) -> str:
    """What one tool call ran or changed, in its own words, or ``""`` for any other call.

    * A shell call: its command, read by the one scoped extractor (``task_modes.shell_command``);
      a command an agent CLI hands as a list of words is joined as a shell would read it, and a
      ``Running: <command>`` title carries its own.
    * A native tool that changes a file: the path it is given (``run_bounds.FILE_WRITES``).
    * An agent CLI's call it reports as changing a file: the file its arguments name, or the file
      the diff it was handed on as names.

    Unmasked: the log masks it as it writes it (:func:`audit_text`). Never raises: a call whose
    arguments cannot be read has no subject, and its row is still written.
    """
    try:
        from personalclaw.run_bounds import FILE_WRITES
        from personalclaw.task_modes import SHELL_TITLE_PREFIXES, shell_command

        name, kind = str(tool_name or ""), str(tool_kind or "")
        subject = kind_of_subject(name, kind)
        args = _arguments(tool_input)
        if subject == COMMAND:
            command = shell_command(name, kind, tool_input)
            words = args.get("command")
            if not command and isinstance(words, list) and words:
                command = shlex.join(str(word) for word in words)
            if not command and name.lower().startswith(SHELL_TITLE_PREFIXES):
                command = name.split(":", 1)[1].strip()
            return command
        if subject == FILE_PATH and name in FILE_WRITES:
            path = args.get(FILE_WRITES[name])
            return path if isinstance(path, str) else ""
        if subject == FILE_PATH:
            named = [args[k] for k in _PATH_ARGS if isinstance(args.get(k), str) and args[k]]
            return ", ".join(dict.fromkeys(named)) or _diff_path(tool_input)
    except Exception:  # noqa: BLE001 - see the docstring: the row is written without a subject
        return ""
    return ""


__all__ = [
    "COMMAND",
    "FILE_CHANGE_KINDS",
    "FILE_PATH",
    "SUBJECT_MAX_CHARS",
    "audit_text",
    "cut_marker",
    "kind_of_subject",
    "subject_of",
]
