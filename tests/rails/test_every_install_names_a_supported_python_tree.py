"""The source-tree checks split from
``tests/test_every_install_names_a_supported_python.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import every_install_names_a_supported_python_shared
from every_install_names_a_supported_python_shared import (
    _ROOT,
    _files,
    install_commands,
    matrix_pythons,
    names_a_supported_python,
    requires_python,
)

pc_python = every_install_names_a_supported_python_shared.pc_python


_BREADTH = _ROOT / ".github" / "workflows" / "breadth.yml"


#: Where a user, a contributor or CI is handed a command that installs PersonalClaw.
_COMMAND_SOURCES = (
    "README.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "docs/**/*.md",
    "deploy/**/*.md",
    "deploy/website/install.sh",
    ".github/workflows/*.yml",
    # The scripts a workflow step runs as `sh <path>`: breadth's install smokes among them.
    "tools/ci/*.sh",
)


#: Files that hand a user a PersonalClaw install command today. The census below must find one in
#: each, or the rule over the census is vacuous.
_MUST_CARRY_A_COMMAND = frozenset(
    {
        "README.md",
        "docs/guides/getting-started.md",
        "docs/guides/platforms.md",
        "deploy/website/install.sh",
        # breadth.yml's install-smoke runs it: the checkout's own install, at a named Python.
        "tools/ci/install_smoke_current.sh",
    }
)


def test_the_installers_python_is_one_ci_tests(pc_python) -> None:
    tested = matrix_pythons(_BREADTH.read_text(encoding="utf-8"))
    assert tested, "found no `python: [...]` matrix in breadth.yml, so this check reads nothing"
    assert pc_python in tested, (
        f"PC_PYTHON={pc_python} is not in breadth.yml's legs {tested}: the installer would put "
        "every new user on a Python no test ran on"
    )


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
