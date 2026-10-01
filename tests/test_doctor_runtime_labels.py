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

That `fallback:` row then turned out to say nothing at all. Measured in the published image:

    Runtime
      python:      ✅ /opt/venv/bin/python (3.13.15)
      backend:     ✅ 0.2.0
      fallback:    ⚠️  /opt/venv/bin/python3 (3.13.15)

A warning with no words, about the same venv as the row above. It was whatever `python3` the
PATH named, there only so the dependency check had an interpreter to run on — and no part of
PersonalClaw runs its gateway dependencies on that one. An install without a checkout has one
interpreter, the one running doctor, so that is where the check runs and there is no second row.

The `python:` row's ✅ was then a certificate nothing had checked. A fresh `uv tool install
personalclaw` built its environment on CPython 3.14.7, though the wheel declares
`Requires-Python: <3.14,>=3.12` (uv does not enforce an upper bound), and doctor printed:

    Runtime
      python:      ✅ /…/uv/tools/personalclaw/bin/python (3.14.7)

So each interpreter row is now judged against the INSTALLED release's own `Requires-Python`,
read from its metadata. The tests below hand doctor a range that excludes, then includes, the
interpreter running them, and watch the verdict follow the range rather than any version doctor
could have been told in advance.
"""

from __future__ import annotations

import ast
import email.message
import importlib.metadata
import inspect
import re
import sys
import urllib.error
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

_ROW = re.compile(r"^ {2}([a-z][a-z ]*):", re.MULTILINE)

# Stands in for `<repo>/.venv/bin/python3`. It is never opened — `_venv_interpreter()`
# owns the does-it-exist question, which is exactly why it can be driven both ways.
_FAKE_VENV_PY = Path("/opt/personalclaw/.venv/bin/python3")


def _doctor_output(
    capsys, *, venv_install: bool, runs: list[list[str]] | None = None
) -> tuple[str, int]:
    """Run ``_doctor()`` with its probes stubbed; return everything it printed and its exit status.

    `venv_install` picks the branch: True renders the `venv python:` row, False the
    block a pipx, uv tool or container install gets. `runs`, when given, collects the argv
    of every subprocess doctor started.
    """
    from personalclaw.cli_doctor import _doctor

    answer = type(
        "R",
        (),
        {
            "returncode": 0,
            "stdout": "Python 3.13.14",
            "stderr": "",
            "check_returncode": lambda self: None,
        },
    )()

    def run(argv, *a, **kw):  # noqa: ANN001 — subprocess.run's own signature
        if runs is not None:
            runs.append([str(part) for part in argv])
        return answer

    with (
        patch("personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"),
        patch(
            "personalclaw.cli_doctor._venv_interpreter",
            return_value=_FAKE_VENV_PY if venv_install else None,
        ),
        patch("subprocess.run", side_effect=run),
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")),
        patch("personalclaw.cli_doctor.is_local_bind", return_value=True),
    ):
        code = 0
        try:
            _doctor()
        except SystemExit as exc:
            code = int(exc.code or 0)
    return capsys.readouterr().out, code


def _runtime_block(capsys, *, venv_install: bool, runs: list[list[str]] | None = None) -> str:
    """Just the Runtime section of a stubbed ``_doctor()`` run (see :func:`_doctor_output`)."""
    out, _ = _doctor_output(capsys, venv_install=venv_install, runs=runs)
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


def test_an_install_without_a_checkout_checks_the_interpreter_it_runs_on(capsys):
    """The branch a pipx, uv tool or container install takes: one interpreter, no second row.

    The dependency check is the one this block exists for, and it has to run where the gateway
    runs — the interpreter running doctor — not on whatever `python3` the PATH names.
    """
    runs: list[list[str]] = []
    block = _runtime_block(capsys, venv_install=False, runs=runs)
    assert "venv python:" not in block, f"no venv exists on this branch:\n{block}"
    assert "fallback:" not in block, block
    assert "⚠️" not in block, f"nothing here is wrong, so nothing may warn:\n{block}"
    assert "deps:        ✅ websockets, aiohttp available" in block, block
    checks = [argv for argv in runs if "import websockets, aiohttp" in argv]
    assert checks and all(argv[0] == sys.executable for argv in checks), runs


def test_the_venv_row_gets_its_version_from_the_one_normalising_probe():
    """Normalise once, at the point the string is obtained — asserted structurally.

    The defect was two call sites formatting the same `--version` stdout two ways, so
    "there is only one way to obtain it" is the property worth pinning: a future row
    that re-inlines `subprocess.run([..., "--version"])` brings the drift straight
    back, and a string assertion on today's row would not notice.
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
    inline_version = [
        n for n in ast.walk(doctor) if isinstance(n, ast.Constant) and n.value == "--version"
    ]
    assert len(probe_calls) == 1 and not inline_version, (
        "_doctor() renders an interpreter version in exactly one place (the venv row); "
        f"found {len(probe_calls)} call(s) to the helper that strips the 'Python ' prefix "
        f"and {len(inline_version)} inline `--version`, so a row formats the version itself"
    )


# ── the interpreter is judged against the installed release's own Requires-Python ──────────

_MAJOR, _MINOR = sys.version_info[:2]
_RUNNING = "{}.{}.{}".format(*sys.version_info[:3])
#: A range whose upper bound is the running interpreter's own minor: the shape of the real
#: defect (`<3.14` under 3.14.7), wherever the suite happens to run.
_EXCLUDES_RUNNING = f">=3.0,<{_MAJOR}.{_MINOR}"
_INCLUDES_RUNNING = f">={_MAJOR}.{_MINOR},<{_MAJOR}.{_MINOR + 1}"


@contextmanager
def _installed_requires_python(requires: str | None) -> Iterator[None]:
    """Make the installed `personalclaw` distribution declare *requires* (None: not installed).

    Every other distribution's metadata is read as it really is, so nothing else doctor asks
    of `importlib.metadata` changes under it.
    """
    real = importlib.metadata.metadata

    def metadata(name: str):  # noqa: ANN202 — importlib.metadata.metadata's own signature
        if name != "personalclaw":
            return real(name)
        if requires is None:
            raise importlib.metadata.PackageNotFoundError(name)
        meta = email.message.Message()
        meta["Name"] = "personalclaw"
        meta["Requires-Python"] = requires
        return meta

    with patch("importlib.metadata.metadata", side_effect=metadata):
        yield


def _python_row(block: str, label: str = "python") -> str:
    """The row *label* heads, with the `Fix:` continuation line beneath it when there is one."""
    match = re.search(rf"^ {{2}}{label}: .*(?:\n {{15}}Fix: .*)?", block, re.MULTILINE)
    assert match, f"no `{label}:` row in the Runtime block:\n{block}"
    return match.group(0)


def test_an_interpreter_outside_the_installed_range_fails_the_doctor(capsys):
    """The defect itself: 3.14.7 under `<3.14` was certified ✅ and doctor exited 0."""
    with _installed_requires_python(_EXCLUDES_RUNNING):
        out, code = _doctor_output(capsys, venv_install=False)
    row = _python_row(out.split("\nRuntime\n", 1)[1])
    assert (
        "✅" not in row and "❌" in row
    ), f"doctor certified a Python the installed release does not support:\n{row}"
    assert (
        _RUNNING in row and _EXCLUDES_RUNNING in row
    ), f"the row must say which interpreter it judged and against what range:\n{row}"
    assert "\n               Fix: " in row, f"a failed row must hand back the fix:\n{row}"
    assert (
        code == 1 and "python version" in out.rsplit("❌ Fix these issues:", 1)[-1]
    ), f"an unsupported interpreter must fail the doctor (exit {code}):\n{out[-400:]}"


def test_an_interpreter_inside_the_range_is_certified_against_that_range(capsys):
    """The ✅ is licensed by the comparison, so it names the range it was compared with."""
    with _installed_requires_python(_INCLUDES_RUNNING):
        block = _runtime_block(capsys, venv_install=False)
    row = _python_row(block)
    assert row.startswith("  python:      ✅ ") and _INCLUDES_RUNNING in row, row
    assert "Fix:" not in row, row


def test_the_range_comes_from_the_metadata_not_from_doctor(capsys):
    """Two ranges, two verdicts, one interpreter: nothing in doctor knows a version up front."""
    verdicts = []
    for requires in (_INCLUDES_RUNNING, _EXCLUDES_RUNNING):
        with _installed_requires_python(requires):
            verdicts.append("✅" in _python_row(_runtime_block(capsys, venv_install=False)))
    assert verdicts == [True, False], verdicts


def test_with_no_installed_metadata_the_row_certifies_nothing(capsys):
    """A source tree run without an install has no Requires-Python to compare with.

    That is a fact to state, not a pass to award, and not a failure either: nothing was found
    wrong.
    """
    with _installed_requires_python(None):
        out, code = _doctor_output(capsys, venv_install=False)
    row = _python_row(out.split("\nRuntime\n", 1)[1])
    assert "✅" not in row and "❌" not in row, row
    assert "metadata" in row, f"the row must say why nothing was checked:\n{row}"
    assert "python version" not in out, out[-400:]


def test_a_uv_tool_install_is_handed_the_uv_command_that_moves_it(capsys, tmp_path):
    """`uv tool upgrade --python <range>` rebuilds the tool environment on a matching Python.

    It keeps what the install was made with (extras, `--with`, index options: uv's receipt), so
    it is the one command that fixes the environment without dropping anything from it.
    """
    (tmp_path / "uv-receipt.toml").write_text("[tool]\n", encoding="utf-8")
    with (
        _installed_requires_python(_EXCLUDES_RUNNING),
        patch("personalclaw.python_support.environment_root", return_value=tmp_path),
    ):
        row = _python_row(_runtime_block(capsys, venv_install=False))
    assert row.endswith(f"Fix: uv tool upgrade --python '{_EXCLUDES_RUNNING}' personalclaw"), row


def test_any_other_install_is_told_the_range_to_rebuild_on(capsys, tmp_path):
    """pip, pipx or a checkout's venv: there is no one command, so the fix states what must hold."""
    with (
        _installed_requires_python(_EXCLUDES_RUNNING),
        patch("personalclaw.python_support.environment_root", return_value=tmp_path),
    ):
        row = _python_row(_runtime_block(capsys, venv_install=False))
    fix = row.split("Fix: ", 1)[-1]
    assert "uv tool" not in fix and _EXCLUDES_RUNNING in fix, row


def test_the_venv_interpreter_is_judged_by_the_same_range(capsys):
    """The checkout's venv row prints a version too, so it answers to the same comparison."""
    with _installed_requires_python(">=3.0,<3.13"):
        block = _runtime_block(capsys, venv_install=True)
    row = _python_row(block, "venv python")
    assert "❌" in row and "3.13.14" in row and ">=3.0,<3.13" in row, row
