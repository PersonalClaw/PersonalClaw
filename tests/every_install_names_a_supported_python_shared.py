"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_every_install_names_a_supported_python.py``

Its checks:
``tests/rails/test_every_install_names_a_supported_python_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pathlib
import re
import tomllib

import pytest
from packaging.specifiers import SpecifierSet

_ROOT = pathlib.Path(__file__).resolve().parents[1]


_INSTALLER = _ROOT / "deploy" / "website" / "install.sh"


_PYPROJECT = _ROOT / "pyproject.toml"


#: ``uv tool install|upgrade …`` and ``pipx install|reinstall …``, up to the end of the command.
_COMMAND = re.compile(r"\b(?:uv tool (?:install|upgrade)|pipx (?:install|reinstall))\b[^`\n|&;)]*")


def installer_python(text: str) -> str | None:
    """The installer's ``PC_PYTHON="X.Y"``, or ``None`` when it names none."""
    match = re.search(r'^PC_PYTHON="([^"]*)"\s*$', text, flags=re.MULTILINE)
    return match.group(1) if match else None


def requires_python() -> SpecifierSet:
    project = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]
    return SpecifierSet(project["requires-python"])


def matrix_pythons(text: str) -> list[str]:
    """The Python minors (``X.Y``) of every ``python: [...]`` matrix list in a workflow.

    The matrix names exact patches (``3.13.16``), which the installer's ``PC_PYTHON`` never
    does: it names the minor uv picks the newest patch of, so the two meet at the minor.
    """
    found: list[str] = []
    for listed in re.findall(r"^\s*python:\s*\[([^\]]*)\]", text, flags=re.MULTILINE):
        found += re.findall(r"\d+\.\d+(?:\.\d+)?", listed)
    return sorted({".".join(v.split(".")[:2]) for v in found})


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


@pytest.fixture(scope="module")
def pc_python() -> str:
    """The installer's ``PC_PYTHON``; ``""`` when it names none, which the first case reports."""
    return installer_python(_INSTALLER.read_text(encoding="utf-8")) or ""
