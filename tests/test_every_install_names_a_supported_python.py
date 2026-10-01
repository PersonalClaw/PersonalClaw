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
``requires-python`` and must be one ``full.yml``'s matrix tests. And the words around the commands
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
import tomllib

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_INSTALLER = _ROOT / "deploy" / "website" / "install.sh"
_PYPROJECT = _ROOT / "pyproject.toml"
_FULL = _ROOT / ".github" / "workflows" / "full.yml"

#: Where a user, a contributor or CI is handed a command that installs PersonalClaw.
_COMMAND_SOURCES = (
    "README.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "docs/**/*.md",
    "deploy/**/*.md",
    "deploy/website/install.sh",
    ".github/workflows/*.yml",
)

#: The files whose PROSE states which Python PersonalClaw runs on. The CHANGELOG is history and
#: is not read: a released entry records what was true of its release.
_PROSE_SOURCES = ("README.md", "AGENTS.md", "CONTRIBUTING.md", "docs/**/*.md")

#: Files that hand a user a PersonalClaw install command today. The census below must find one in
#: each, or the rule over the census is vacuous.
_MUST_CARRY_A_COMMAND = frozenset(
    {
        "README.md",
        "docs/guides/getting-started.md",
        "docs/guides/platforms.md",
        "deploy/website/install.sh",
        ".github/workflows/full.yml",
    }
)

#: ``uv tool install|upgrade …`` and ``pipx install|reinstall …``, up to the end of the command.
_COMMAND = re.compile(r"\b(?:uv tool (?:install|upgrade)|pipx (?:install|reinstall))\b[^`\n|&;)]*")


# ── readers (text in, answer out, so the controls at the bottom can drive them) ─────────────


def installer_python(text: str) -> str | None:
    """The installer's ``PC_PYTHON="X.Y"``, or ``None`` when it names none."""
    match = re.search(r'^PC_PYTHON="([^"]*)"\s*$', text, flags=re.MULTILINE)
    return match.group(1) if match else None


def requires_python() -> SpecifierSet:
    project = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]
    return SpecifierSet(project["requires-python"])


def has_upper_bound(spec: SpecifierSet) -> bool:
    return any(s.operator in ("<", "<=") for s in spec)


def matrix_pythons(text: str) -> list[str]:
    """The ``python: [...]`` list of ``full.yml``'s test matrix."""
    match = re.search(r"^\s*python:\s*\[([^\]]*)\]", text, flags=re.MULTILINE)
    return re.findall(r"[\d.]+", match.group(1)) if match else []


def _files(patterns: tuple[str, ...]) -> list[pathlib.Path]:
    found: set[pathlib.Path] = set()
    for pattern in patterns:
        found.update(p for p in _ROOT.glob(pattern) if p.is_file())
    return sorted(found)


def install_commands(path: str, text: str) -> list[str]:
    """Every command in *text* that installs PersonalClaw itself (a shell or YAML comment is not).

    PersonalClaw is named by its package (extras and specifiers included), by the installer's
    ``$PC_PACKAGE``, or as ``.`` — a checkout. A command for some other tool is not this rule's.
    """
    commands = []
    code = path.endswith((".sh", ".yml"))
    for line in text.splitlines():
        if code and line.lstrip().startswith("#"):
            continue
        for command in _COMMAND.findall(line):
            words = [w.strip("'\"") for w in command.split()]
            if any(w == "." or re.match(r"(?:personalclaw|\$PC_PACKAGE)\b", w) for w in words):
                commands.append(command.strip())
    return commands


def named_python(command: str) -> str | None:
    """The value of ``--python`` in *command* (quotes dropped), or ``None`` when it names none."""
    match = re.search(r"--python[ =](\"[^\"]*\"|'[^']*'|\S+)", command)
    return match.group(1).strip("'\"") if match else None


def names_a_supported_python(command: str, version: str, spec: SpecifierSet) -> bool:
    """The installer's version, its constant, the matching interpreter name, or the whole range."""
    value = named_python(command)
    if value is None:
        return False
    if value in (version, f"python{version}", "$PC_PYTHON"):
        return True
    try:
        return SpecifierSet(value) == spec
    except ValueError:
        return False


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


@pytest.fixture(scope="module")
def pc_python() -> str:
    """The installer's ``PC_PYTHON``; ``""`` when it names none, which the first case reports."""
    return installer_python(_INSTALLER.read_text(encoding="utf-8")) or ""


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


def test_the_installers_python_is_one_ci_tests(pc_python) -> None:
    tested = matrix_pythons(_FULL.read_text(encoding="utf-8"))
    assert tested, "found no `python: [...]` matrix in full.yml, so this check reads nothing"
    assert pc_python in tested, (
        f"PC_PYTHON={pc_python} is not in full.yml's matrix {tested}: the installer would put "
        "every new user on a Python no test ran on"
    )


# ── every other command and sentence agrees with it ────────────────────────────────────────


def test_every_install_command_names_a_supported_python(pc_python) -> None:
    spec = requires_python()
    offenders = []
    for path in _files(_COMMAND_SOURCES):
        rel = path.relative_to(_ROOT).as_posix()
        for command in install_commands(rel, path.read_text(encoding="utf-8")):
            if not names_a_supported_python(command, pc_python, spec):
                offenders.append(f"{rel}: `{command}`")
    assert not offenders, (
        f"these install PersonalClaw without naming a Python ({pc_python}, as the installer does, "
        f"or the range {spec}); uv then picks the newest it can find or download:\n"
        + "\n".join(offenders)
    )


def test_the_census_finds_the_commands_users_are_handed() -> None:
    """Vacuity floor: a census that matched nothing would make the rule above unfailable."""
    found = {
        path.relative_to(_ROOT).as_posix()
        for path in _files(_COMMAND_SOURCES)
        if install_commands(path.relative_to(_ROOT).as_posix(), path.read_text(encoding="utf-8"))
    }
    missing = sorted(_MUST_CARRY_A_COMMAND - found)
    assert not missing, f"the census found no PersonalClaw install command in {missing}"


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
    commands = install_commands("full.yml", workflow)
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
    assert matrix_pythons("      matrix:\n        shard: [1, 2]\n") == []
