"""Doctor's Runtime block: one label per fact.

The block printed ``python:`` TWICE — once for the interpreter RUNNING doctor
(``sys.executable``) and once for the INSTALLED venv's interpreter, which are
different facts that can legitimately disagree (doctor launched from a shim while
the gateway venv lives elsewhere). Two rows sharing a label read as a redundant
repeat rather than as two answers, and the venv row's value came straight from
``python3 --version`` stdout, so it rendered ``(Python 3.13.14)`` next to the row
above's ``(3.13.14)`` for what looked like the same thing.

Measured on a real run before the fix:

    Runtime
      python:      ✅ /…/.venv/bin/python (3.13.14)
      backend:   ✅ 0.1.3
      python:      ✅ /…/.venv/bin/python3 (Python 3.13.14)

A duplicated label in the primary diagnostic surface is the kind of defect that
survives forever because each row is individually correct.
"""

from __future__ import annotations

import re
import urllib.error
from unittest.mock import patch

_ROW = re.compile(r"^ {2}([a-z][a-z ]*):", re.MULTILINE)


def _runtime_block(capsys) -> str:
    """Run ``_doctor()`` with its probes stubbed and return just the Runtime section."""
    from personalclaw.cli_doctor import _doctor

    with (
        patch("personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"),
        patch(
            "subprocess.run",
            return_value=type(
                "R",
                (),
                {
                    "returncode": 0,
                    "stdout": "Python 3.13.14",
                    "stderr": "",
                    "check_returncode": lambda self: None,
                },
            )(),
        ),
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")),
        patch("personalclaw.cli_doctor.is_local_bind", return_value=True),
    ):
        try:
            _doctor()
        except SystemExit:
            pass
    out = capsys.readouterr().out
    assert "\nRuntime\n" in out, out
    block = out.split("\nRuntime\n", 1)[1]
    # Up to the next section heading (a line with no leading indent).
    return block.split("\n\n", 1)[0]


def test_no_label_appears_twice_in_the_runtime_block(capsys):
    labels = _ROW.findall(_runtime_block(capsys))
    dupes = sorted({label for label in labels if labels.count(label) > 1})
    assert not dupes, (
        f"the Runtime block repeats {dupes} — two rows under one label read as a "
        "redundant repeat rather than as two different facts"
    )


def test_the_venv_interpreter_row_says_which_python_it_is(capsys):
    block = _runtime_block(capsys)
    assert "venv python:" in block, (
        "the installed venv's interpreter is a different fact from the one running "
        f"doctor, and the label is the only thing that says so:\n{block}"
    )


def test_the_version_is_not_prefixed_twice(capsys):
    # `python3 --version` says "Python 3.13.14"; rendering it verbatim inside a row
    # already labelled python produced "(Python 3.13.14)".
    assert "(Python " not in _runtime_block(capsys)
