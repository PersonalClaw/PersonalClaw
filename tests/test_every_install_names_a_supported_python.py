"""Every way of installing PersonalClaw builds its environment on a Python the release supports.

``pyproject.toml`` says ``requires-python = ">=3.12,<3.14"``, and the wheel carries it as
``Requires-Python: <3.14,>=3.12``. pip refuses a Python outside that range. **uv does not: it
ignores an upper bound.** So ``uv tool install personalclaw`` with no ``--python`` builds the tool
environment on the newest interpreter uv finds or downloads, whatever the release supports.
Measured with uv 0.12.19 and a wheel declaring ``<3.14,>=3.12``, from a file and from an index
alike: installed on CPython 3.14.7, exit 0, no warning. A fresh machine gets exactly that, because
uv downloads the newest CPython when it finds none, and on 3.14 the connector-pack parser refuses
every import (``pyproject.toml``'s comment on ``requires-python``).

The public installer said "this brings its own Python 3.12" over a ``uv tool install`` that named
no Python at all, and the README, the getting-started guide and the WSL guide repeated the
promise over the same bare command.

So the rule, read from every install command this repository hands a user or runs in CI: an
install of PersonalClaw names its Python. The installer names it once, as ``PC_PYTHON``; every
other command names the same version (or the release's own range). That version must be inside
``requires-python`` and must be one ``breadth.yml``'s legs test. And the words around the commands
must say what they do: no open-ended "Python 3.12+" while the range has an upper bound, and no
"uv brings Python X" naming a version the installer does not install.

The installer half is behavioural too: the script is run with a stub ``uv`` that records its
arguments, so what is asserted is the command it really runs, not a line of its text.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

import every_install_names_a_supported_python_shared
import pytest
from every_install_names_a_supported_python_shared import (
    _INSTALLER,
    _ROOT,
    _files,
    install_commands,
    matrix_pythons,
    names_a_supported_python,
    requires_python,
)
from packaging.specifiers import SpecifierSet
from packaging.version import Version

pc_python = every_install_names_a_supported_python_shared.pc_python


#: The files whose PROSE states which Python PersonalClaw runs on. The CHANGELOG is history and
#: is not read: a released entry records what was true of its release.
_PROSE_SOURCES = ("README.md", "AGENTS.md", "CONTRIBUTING.md", "docs/**/*.md")


# ── readers (text in, answer out, so the controls at the bottom can drive them) ─────────────


def has_upper_bound(spec: SpecifierSet) -> bool:
    return any(s.operator in ("<", "<=") for s in spec)


def open_ended_claims(text: str) -> list[str]:
    """Every "Python 3.N+" (bold or not): a promise of every version from N on."""
    return re.findall(r"\bPython\s+\**3\.\d+\+", text)


def uv_brings_claims(text: str) -> list[str]:
    """The version every "uv brings / provides / downloads … Python X.Y" sentence names."""
    return re.findall(
        r"\buv\b[^.\n]{0,80}?\b(?:brings|provides|downloads|fetches|supplies)\b[^.\n]{0,40}?"
        r"\bPython\s+\**(\d+\.\d+)",
        text,
    )


def test_the_installer_names_the_python_it_installs_on(pc_python) -> None:
    assert pc_python, (
        "deploy/website/install.sh names no PC_PYTHON, so `uv tool install` builds the tool "
        "environment on whatever Python uv finds or downloads newest, which the release may not "
        "support: uv ignores the upper bound of Requires-Python."
    )


# ── the installer runs what it says ──────────────────────────────────────────────────────────


@pytest.fixture()
def stub_uv(tmp_path: pathlib.Path) -> tuple[dict[str, str], pathlib.Path]:
    """A PATH holding the installer's harmless externals and a ``uv`` that only records its argv.

    No downloader and no real uv can be reached, and ``HOME`` does not exist, so the run can
    neither fetch nor write anything; ``personalclaw`` is absent too, so the script stops at its
    "not yet on PATH" tail instead of offering to run setup.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("cat", "uname", "printf"):
        found = next(
            (
                pathlib.Path(d) / tool
                for d in os.environ.get("PATH", "").split(os.pathsep)
                if (pathlib.Path(d) / tool).is_file()
            ),
            None,
        )
        if found:
            (bindir / tool).symlink_to(found)
    calls = tmp_path / "uv-calls"
    uv = bindir / "uv"
    uv.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$UV_CALLS"\n', encoding="utf-8")
    uv.chmod(0o755)
    env = {"PATH": str(bindir), "HOME": str(tmp_path / "nonexistent"), "UV_CALLS": str(calls)}
    return env, calls


def _run_installer(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", str(_INSTALLER)], capture_output=True, text=True, timeout=60, env=dict(env)
    )


def test_the_installer_asks_uv_for_the_python_it_names(stub_uv, pc_python) -> None:
    env, calls = stub_uv
    proc = _run_installer(env)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    installs = [
        line.split()
        for line in calls.read_text(encoding="utf-8").splitlines()
        if line.startswith("tool install")
    ]
    assert installs, f"the installer never ran `uv tool install`: {calls.read_text()!r}"
    for argv in installs:
        assert "--python" in argv and argv[argv.index("--python") + 1] == pc_python, (
            f"`uv {' '.join(argv)}` names no Python {pc_python}, so uv builds the environment on "
            "the newest Python it finds or downloads, whatever the release supports"
        )


def test_what_the_installer_says_is_what_it_installs(stub_uv, pc_python) -> None:
    """It printed "this brings its own Python 3.12" over a command that asked uv for no Python."""
    env, _ = stub_uv
    proc = _run_installer(env)
    said = re.findall(r"\bPython (\d+\.\d+)", proc.stdout + proc.stderr)
    assert said, f"the installer no longer says which Python it installs on:\n{proc.stdout}"
    assert set(said) == {pc_python}, (
        f"the installer says Python {sorted(set(said))} and installs on {pc_python}:\n"
        f"{proc.stdout}"
    )


def test_the_installer_text_names_no_other_python(pc_python) -> None:
    """The header and every comment too: the file is read by whoever checks it before running it."""
    text = _INSTALLER.read_text(encoding="utf-8")
    other = sorted(set(re.findall(r"\bPython (\d+\.\d+)", text)) - {pc_python})
    assert not other, f"install.sh names Python {other}, but it installs on {pc_python}"


def test_the_installers_python_is_one_the_release_supports(pc_python) -> None:
    spec = requires_python()
    assert pc_python and spec.contains(Version(pc_python)), (
        f"PC_PYTHON={pc_python} is outside requires-python {spec}: every install would land on a "
        "Python the release refuses everywhere except uv"
    )


# ── every other command and sentence agrees with it ────────────────────────────────────────


def test_no_doc_promises_every_python_from_a_version_on() -> None:
    spec = requires_python()
    assert has_upper_bound(spec), "requires-python has no upper bound; this rule has nothing to say"
    claims = [
        f"{path.relative_to(_ROOT).as_posix()}: {claim!r}"
        for path in _files(_PROSE_SOURCES)
        for claim in open_ended_claims(path.read_text(encoding="utf-8"))
    ]
    assert not claims, (
        f"requires-python is {spec}, so a newer Python is refused (or, under uv, unsupported), "
        "and these sentences promise it anyway:\n" + "\n".join(claims)
    )


def test_every_uv_brings_python_sentence_names_the_installers_python(pc_python) -> None:
    claims = [
        f"{path.relative_to(_ROOT).as_posix()}: Python {version}"
        for path in _files(_PROSE_SOURCES)
        for version in uv_brings_claims(path.read_text(encoding="utf-8"))
        if version != pc_python
    ]
    assert not claims, f"uv installs PersonalClaw on {pc_python}, not:\n" + "\n".join(claims)


# ── controls: each reader must be able to say no ─────────────────────────────────────────────


def test_the_command_reader_flags_a_bare_install_and_admits_a_named_one() -> None:
    spec = SpecifierSet(">=3.12,<3.14")
    doc = (
        "| uv | `uv tool install personalclaw` | x |\n"
        "uv tool install 'personalclaw[bedrock]'\n"
        "pipx install personalclaw\n"
        "uv tool install --python 3.13 personalclaw\n"
        "pipx install --python python3.13 personalclaw\n"
        "uv tool upgrade --python '<3.14,>=3.12' personalclaw\n"
        "uv tool install ruff\n"
        "the `uv tool install` path\n"
    )
    commands = install_commands("doc.md", doc)
    assert len(commands) == 6, commands
    verdicts = [names_a_supported_python(c, "3.13", spec) for c in commands]
    assert verdicts == [False, False, False, True, True, True], list(zip(commands, verdicts))


def test_the_command_reader_reads_the_installer_and_the_workflow_shapes() -> None:
    spec = SpecifierSet(">=3.12,<3.14")
    script = (
        '# uv tool install --upgrade "$PC_PACKAGE>=$PC_MIN_VERSION"\n'
        '    uv tool install --upgrade "$PC_PACKAGE>=$PC_MIN_VERSION"\n'
        '    uv tool install --upgrade --python "$PC_PYTHON" "$PC_PACKAGE>=$PC_MIN_VERSION"\n'
    )
    commands = install_commands("install.sh", script)
    assert [names_a_supported_python(c, "3.13", spec) for c in commands] == [False, True], commands
    workflow = "          uv tool install .\n          uv tool install --python 3.12 .\n"
    commands = install_commands("breadth.yml", workflow)
    assert [names_a_supported_python(c, "3.13", spec) for c in commands] == [False, False]


def test_the_prose_readers_catch_the_sentences_that_shipped() -> None:
    assert open_ended_claims("a Python 3.12+ aiohttp backend") == ["Python 3.12+"]
    assert open_ended_claims("| Python **3.12+** on their machine |") == ["Python **3.12+"]
    assert open_ended_claims("Python 3.12 or 3.13") == []
    assert uv_brings_claims("anyone — `uv` brings its own Python 3.12 |") == ["3.12"]
    assert uv_brings_claims("with `uv`, which brings its own Python 3.12:") == ["3.12"]
    assert uv_brings_claims("`uv` downloads Python 3.13 when it is missing") == ["3.13"]
    assert uv_brings_claims("Python 3.12 or 3.13, in a venv") == []


def test_the_matrix_reader_reads_the_python_list() -> None:
    assert matrix_pythons('      matrix:\n        python: ["3.12", "3.13"]\n') == ["3.12", "3.13"]
    assert matrix_pythons('      matrix:\n        python: ["3.12.15", "3.13.16"]\n') == [
        "3.12",
        "3.13",
    ]
    assert matrix_pythons("      matrix:\n        shard: [1, 2]\n") == []
