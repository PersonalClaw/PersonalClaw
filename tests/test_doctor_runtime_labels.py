"""Doctor's Runtime block: one label per fact, and one version format.

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

⚠️  The Runtime block has TWO branches and they are chosen by something outside the
test: `<repo>/.venv` existing. An editable checkout and CI both have one, so only the
venv branch was ever rendered here, and the `fallback:` row a pipx or system install
actually sees was asserted by nobody. It had kept the unstripped version the venv row
was fixed for, measured on a real non-venv install:

    Runtime
      python:      ✅ /tmp/pcv-t1-venv/bin/python3.13 (3.13.14)
      backend:     ✅ 0.1.3
      fallback:    ⚠️  /…/mise/shims/python3 (Python 3.14.7)

So every test here now names the branch it drives instead of inheriting it from the
developer's disk.
"""

from __future__ import annotations

import ast
import inspect
import re
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

_ROW = re.compile(r"^ {2}([a-z][a-z ]*):", re.MULTILINE)

# Stands in for `<repo>/.venv/bin/python3`. It is never opened — `_venv_interpreter()`
# owns the does-it-exist question, which is exactly why it can be driven both ways.
_FAKE_VENV_PY = Path("/opt/personalclaw/.venv/bin/python3")


def _runtime_block(capsys, *, venv_install: bool) -> str:
    """Run ``_doctor()`` with its probes stubbed and return just the Runtime section.

    `venv_install` picks the branch: True renders the `venv python:` row, False the
    `fallback:` row a pipx or system install gets.
    """
    from personalclaw.cli_doctor import _doctor

    with (
        patch("personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"),
        patch(
            "personalclaw.cli_doctor._venv_interpreter",
            return_value=_FAKE_VENV_PY if venv_install else None,
        ),
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


@pytest.mark.parametrize("venv_install", [True, False], ids=["venv", "non-venv"])
def test_no_label_appears_twice_in_the_runtime_block(capsys, venv_install):
    labels = _ROW.findall(_runtime_block(capsys, venv_install=venv_install))
    dupes = sorted({label for label in labels if labels.count(label) > 1})
    assert not dupes, (
        f"the Runtime block repeats {dupes} — two rows under one label read as a "
        "redundant repeat rather than as two different facts"
    )


def test_the_venv_interpreter_row_says_which_python_it_is(capsys):
    block = _runtime_block(capsys, venv_install=True)
    assert "venv python:" in block, (
        "the installed venv's interpreter is a different fact from the one running "
        f"doctor, and the label is the only thing that says so:\n{block}"
    )


@pytest.mark.parametrize("venv_install", [True, False], ids=["venv", "non-venv"])
def test_the_version_is_not_prefixed_twice(capsys, venv_install):
    # `python3 --version` says "Python 3.13.14"; rendering it verbatim inside a row
    # already labelled python produced "(Python 3.13.14)". Both branches render a
    # version, so both branches have to be asked — the venv one was fixed while the
    # fallback one kept the doubled word.
    block = _runtime_block(capsys, venv_install=venv_install)
    assert "(Python " not in block, block


def test_the_non_venv_install_renders_a_bare_version_in_its_fallback_row(capsys):
    """The branch a pipx or system install takes, which no test used to reach.

    `python3 --version` is the only source for this row, so the row is the proof the
    normalisation happens for it too — not just that the doubled prefix is absent.
    """
    block = _runtime_block(capsys, venv_install=False)
    assert "venv python:" not in block, f"no venv exists on this branch:\n{block}"
    assert "fallback:    ⚠️  /usr/local/bin/python3 (3.13.14)" in block, block


def test_both_runtime_rows_get_their_version_from_the_one_normalising_probe():
    """Normalise once, at the point the string is obtained — asserted structurally.

    The defect was two call sites formatting the same `--version` stdout two ways, so
    "there is only one way to obtain it" is the property worth pinning: a future row
    that re-inlines `subprocess.run([..., "--version"])` brings the drift straight
    back, and a string assertion on today's two rows would not notice.
    """
    import personalclaw.cli_doctor as cd

    tree = ast.parse(Path(inspect.getsourcefile(cd) or "").read_text(encoding="utf-8"))
    doctor = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_doctor"
    )
    probe_calls = [
        n
        for n in ast.walk(doctor)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_probe_python_version"
    ]
    assert len(probe_calls) == 2, (
        "_doctor() renders an interpreter version in exactly two places (the venv row "
        f"and the fallback row); found {len(probe_calls)} call(s) to the helper that "
        "strips the 'Python ' prefix, so one of them formats the version itself"
    )
