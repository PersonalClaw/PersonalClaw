"""The source-tree checks split from
``tests/test_a_warning_prints_on_stderr.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from pathlib import Path

import source_index
from a_warning_prints_on_stderr_shared import _Site, _warnings_on_stdout, warning_constants

import personalclaw

PACKAGE = Path(personalclaw.__file__).parent

_A_DOCTOR_SECTION = "a `doctor` section: its ⚠ lines are its findings, and its exit is its verdict"

#: The narrowest functions whose ⚠ lines on stdout are their report's own findings, and why.
REPORTS = {
    ("auth/cli.py", "_print_status"): "`auth status`: names a login that cannot succeed",
    ("cli_commands.py", "_audit_report"): (
        "`security audit`'s findings are its report; from CLI PR 2 it exits 1 on them"
    ),
    ("cli_commands.py", "_verify_verdict"): (
        "`security verify`'s verdict is its report, and its exit 1 says tampered (rule 9)"
    ),
    ("cli_commands.py", "_memory_audit_report"): (
        "`memory audit`'s findings are its report; from CLI PR 2 it exits 1 on them"
    ),
    ("cli_commands.py", "_print_retrieval_report"): (
        "`retrieval-eval`'s report: a drifted corpus or a dead arm is its finding (exit 0)"
    ),
    ("cli_doctor.py", "_doctor_credentials"): _A_DOCTOR_SECTION,
    ("cli_doctor.py", "_doctor_timezone"): _A_DOCTOR_SECTION,
    ("cli_doctor.py", "_doctor_session_lifetime"): _A_DOCTOR_SECTION,
    ("cli_doctor.py", "_doctor_external_vector_store"): _A_DOCTOR_SECTION,
}

_THE_REMINDER = "the data-handling reminder (`constants.DATA_WARNING`)"

#: The narrowest functions that print the standing data-handling reminder on stdout, and why it
#: belongs in what they print.
REMINDERS = {
    ("cli_setup.py", "_setup"): f"`setup`, an interactive command, opens with {_THE_REMINDER}",
    ("cli_chat.py", "_interactive"): f"`chat`, an interactive command, opens with {_THE_REMINDER}",
    ("cli_server.py", "_say_updated"): (
        f"a finished `update`'s transcript, a checkout's or a wheel's, keeps {_THE_REMINDER}"
    ),
    ("gateway.py", "GatewayOrchestrator.run"): (
        f"the gateway's start banner, a stream and not a value, carries {_THE_REMINDER}"
    ),
}


def _package_sites() -> set[_Site]:
    trees = list(source_index.package_trees())
    warned = frozenset().union(*(warning_constants(tree) for _, tree in trees))
    assert "DATA_WARNING" in warned, "the walk reads no warning constant"
    sites: set[_Site] = set()
    for path, tree in trees:
        sites |= _warnings_on_stdout(tree, path.relative_to(PACKAGE).as_posix(), warned)
    return sites


def test_no_command_prints_a_warning_on_stdout():
    unexpected = sorted(
        (s for s in _package_sites() if (s.module, s.function) not in REPORTS | REMINDERS),
        key=lambda s: (s.module, s.line),
    )
    assert not unexpected, "a warning printed on stdout (give it file=sys.stderr):\n" + "\n".join(
        f"  {s.module}:{s.line} in {s.function}: {s.text}" for s in unexpected
    )


def test_every_named_report_is_still_there():
    """A named function that no longer prints a ⚠ line excuses nothing, so a stale entry is
    removed rather than left to excuse the next warning written there."""
    warning = {(s.module, s.function) for s in _package_sites()}
    named = set(REPORTS) | set(REMINDERS)
    assert named <= warning, named - warning
