"""A skill's reinstall leaves exactly the new version's files.

Every install, reinstall and update of a skill folder goes through one writer
(``marketplace.install_skill_files``): a marketplace install from the Skills page or the command
line, a pack's install and update, an app's skills, an import from another tool. It wrote the new
files into the folder that was there and removed nothing, so a file the new version dropped stayed
beside it: a script the author took out was still on disk for an agent to read or run, and the
install record, which lists only the new files, read the fresh install as changed at once. A
version refused half way through had already overwritten the files written before the refusal.

Pinned here: after a reinstall the folder holds the new version's files and nothing else, its
record matches them, and a refused version leaves the installed copy exactly as it was.
"""

from __future__ import annotations

import pytest

from personalclaw.skills.loader import skills_dir
from personalclaw.skills.marketplace import (
    SkillDetail,
    SkillNotFoundError,
    SkillsMarketplace,
    install_scanned,
    verify_skill_integrity,
)

NAME = "invoice-check"
V1 = {
    "SKILL.md": "---\nname: invoice-check\ndescription: Check an invoice.\n---\n# Check\n",
    "scripts/totals.sh": "echo totals\n",
    "reference/old-rates.md": "Rates for last year.\n",
}
V2 = {
    "SKILL.md": "---\nname: invoice-check\ndescription: Check an invoice.\n---\n# Check v2\n",
    "scripts/totals.sh": "echo totals and tax\n",
}


class _Catalog(SkillsMarketplace):
    """A catalogue publishing one version of the skill at a time."""

    def __init__(self) -> None:
        self.version: dict[str, str] = {}

    def search(self, query: str, limit: int = 20) -> list:
        return []

    def fetch(self, skill_id: str) -> SkillDetail:
        if skill_id != NAME:
            raise SkillNotFoundError(skill_id)
        files = [{"path": p, "contents": c} for p, c in self.version.items()]
        return SkillDetail(id=NAME, name=NAME, files=files)


def _files() -> dict[str, str]:
    folder = skills_dir() / NAME
    return {
        p.relative_to(folder).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p.name != ".pclaw-lock.json"
    }


def test_a_reinstall_leaves_no_file_the_new_version_dropped():
    catalog = _Catalog()
    catalog.version = V1
    install_scanned(catalog, "catalog.example", NAME, skills_dir())
    assert _files() == V1

    catalog.version = V2
    install_scanned(catalog, "catalog.example", NAME, skills_dir())

    assert _files() == V2, "the reinstall kept a file the new version dropped"
    assert verify_skill_integrity(skills_dir() / NAME).state == "intact"


def test_a_refused_version_leaves_the_installed_copy_as_it_was():
    catalog = _Catalog()
    catalog.version = V1
    install_scanned(catalog, "catalog.example", NAME, skills_dir())
    record = (skills_dir() / NAME / ".pclaw-lock.json").read_bytes()

    # A version whose SKILL.md is refused, listed after a file that would be written first.
    catalog.version = {
        "reference/new-rates.md": "Rates for this year.\n",
        "SKILL.md": "# no frontmatter, so no name\n",
    }
    with pytest.raises(ValueError):
        install_scanned(catalog, "catalog.example", NAME, skills_dir())

    assert _files() == V1, "a refused version changed the installed copy"
    assert (skills_dir() / NAME / ".pclaw-lock.json").read_bytes() == record
    leftovers = [p.name for p in skills_dir().iterdir() if p.name != NAME]
    assert leftovers == [], f"the refused install left {leftovers} in the library"


def test_a_first_install_writes_the_version_and_its_record():
    """The control: a skill that was not there is installed as before."""
    catalog = _Catalog()
    catalog.version = V2
    install_scanned(catalog, "catalog.example", NAME, skills_dir())

    assert _files() == V2
    assert verify_skill_integrity(skills_dir() / NAME).state == "intact"
