"""The source-tree checks split from
``tests/test_a_cli_refusal_prints_on_stderr.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from pathlib import Path

import source_index
from a_cli_refusal_prints_on_stderr_shared import _refusals_on_stdout, _Site

import personalclaw

PACKAGE = Path(personalclaw.__file__).parent


#: Functions whose stdout before a failing status is their real output, and why.
REPORTS_WITH_A_STATUS = {
    ("apps/quality.py", "main"): "the verification report is the output; 1 says what failed",
    ("cli_commands.py", "_security"): "`security verify` reports the log; 1 says it is tampered",
    ("cli_doctor.py", "_doctor"): "the doctor's report ends on its verdict and what to fix",
    ("inbound/auth.py", "_show_token"): "`inbound token show` answers either way; 1 = none works",
    ("evals/child.py", "main"): "the result line is the protocol its parent reads on stdout",
    ("evals/optimize.py", "main"): "JSON on stdout is the bash action's protocol, errors too",
}


def _package_sites() -> set[_Site]:
    sites: set[_Site] = set()
    for path, tree in source_index.package_trees():
        module = path.relative_to(PACKAGE).as_posix()
        sites |= _refusals_on_stdout(tree, module)
    return sites


def test_no_command_prints_a_refusal_on_stdout():
    unexpected = sorted(
        (s for s in _package_sites() if (s.module, s.function) not in REPORTS_WITH_A_STATUS),
        key=lambda s: (s.module, s.line),
    )
    assert not unexpected, "a refusal printed on stdout (give it file=sys.stderr):\n" + "\n".join(
        f"  {s.module}:{s.line} in {s.function}: {s.text}" for s in unexpected
    )


def test_every_named_report_is_still_there():
    """A named function that no longer reports before a failing status excuses nothing, so a
    stale entry is removed rather than left to excuse the next refusal written there."""
    reporting = {(s.module, s.function) for s in _package_sites()}
    assert set(REPORTS_WITH_A_STATUS) <= reporting, set(REPORTS_WITH_A_STATUS) - reporting
