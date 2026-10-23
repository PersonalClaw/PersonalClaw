"""The source-tree checks split from
``tests/test_every_install_path_installs_the_lock.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from pathlib import Path

import source_index
from every_install_path_installs_the_lock_shared import (
    _ROOT,
    Install,
    _commands,
    _declared,
    _from_ranges,
)

#: Where the release builds its desktop bundles: release.yml calls it as `artifacts`.
_RELEASE = _ROOT / ".github" / "workflows" / "release-artifacts.yml"


#: The installs that resolve pyproject's ranges on purpose, keyed by file and a piece of the
#: command, with the reason the lock cannot apply. Each entry must still match a command: an
#: exemption for a command that is gone is a hole waiting for the next one.
_RANGE_INSTALLS = {
    ("deploy/website/install.sh", "uv tool install"): (
        "the public installer installs the published wheel from PyPI, whose metadata carries "
        "pyproject's ranges and no lock"
    ),
    ("tools/ci/install_smoke_current.sh", "uv tool install --python"): (
        "rehearses the public installer on this checkout, so it resolves exactly what that "
        "installer will"
    ),
    (".github/workflows/clean-machine-walkthroughs.yml", "pip install --quiet personalclaw"): (
        "installs the released wheel from PyPI: a stranger's documented path is the subject"
    ),
    (".github/workflows/clean-machine-walkthroughs.yml", "pip install --quiet -e ."): (
        "installs the checkout for the self-updater walkthrough, whose subject is how each "
        "install kind is classified, not the dependency set"
    ),
    ("scripts/fresh_install_validate.sh", "personalclaw==${version}"): (
        "validates the released wheel on a clean machine, the way a user installs it"
    ),
}


def _install_files() -> list[Path]:
    """Every file here that runs an install: images, workflows, shell scripts and the Makefile."""
    files = [
        *(_ROOT / "deploy").rglob("Dockerfile*"),
        *(
            p
            for p in source_index.workflow_files()
            if p.parent == _ROOT / ".github" / "workflows" and p.suffix == ".yml"
        ),
        *(_ROOT / "scripts").glob("*.sh"),
        *(_ROOT / "deploy").rglob("*.sh"),
        # The scripts a workflow step runs from tools/ci (breadth's install smokes among them).
        *(_ROOT / "tools" / "ci").glob("*.sh"),
        _ROOT / "Makefile",
    ]
    return sorted(p for p in files if p.is_file())


def _text(path: Path) -> str:
    """A workflow through the suite's one source index; any other install file (an image, a
    script, the Makefile, none of them under its roots) read here."""
    if path.is_relative_to(_ROOT / ".github" / "workflows"):
        return source_index.text(path)
    return path.read_text(encoding="utf-8")


def _census() -> list[Install]:
    return [
        install
        for path in _install_files()
        for install in _commands(path.relative_to(_ROOT).as_posix(), _text(path))
    ]


def _exemption(install: Install) -> tuple[str, str] | None:
    for key in _RANGE_INSTALLS:
        where, piece = key
        if install.path == where and piece in install.command:
            return key
    return None


def test_no_install_path_takes_personalclaws_dependencies_from_the_ranges() -> None:
    declared = _declared()
    offenders = [
        f"{install.path}: `{install.command}` — {why}"
        for install in _census()
        if (why := _from_ranges(install, declared)) and _exemption(install) is None
    ]
    assert not offenders, (
        "these installs resolve pyproject's ranges on the day they run, so what they build is "
        "not what CI tested. Install from uv.lock (`uv sync --locked`, or `uv export --locked` "
        "then `pip install --require-hashes --no-deps -r`), then the project with --no-deps; or, "
        "if it installs a published release, add it to _RANGE_INSTALLS with why:\n"
        + "\n".join(offenders)
    )


def test_every_range_install_on_the_list_still_exists_and_still_needs_to_be_there() -> None:
    declared = _declared()
    matched = {_exemption(i): i for i in _census() if _from_ranges(i, declared)}
    stale = [
        f"{where}: {piece!r}" for where, piece in _RANGE_INSTALLS if (where, piece) not in matched
    ]
    assert not stale, "these exemptions match no range install any more; delete them: " + ", ".join(
        stale
    )


def test_the_census_reads_the_installs_that_matter() -> None:
    """Vacuity floor: a parse that found nothing would make the rule above unfailable."""
    census = _census()
    by_file = {i.path for i in census}
    assert len(census) >= 12, [i.command for i in census]
    for path in (
        "deploy/docker/Dockerfile.backend",
        ".github/workflows/release-artifacts.yml",
        ".github/workflows/ci.yml",
        "deploy/website/install.sh",
    ):
        assert path in by_file, f"the census read no install in {path}"


def test_the_desktop_bundles_sync_the_lock() -> None:
    text = _RELEASE.read_text(encoding="utf-8")
    syncs = [i for i in _commands("release-artifacts.yml", text) if i.tokens[1:2] == ("sync",)]
    assert len(syncs) >= 2, "release-artifacts.yml: expected the macOS and Linux desktop syncs"
    for sync in syncs:
        assert "--locked" in sync.tokens and "--no-editable" in sync.tokens, sync.command
