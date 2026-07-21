"""Native (filesystem-bundled) skills marketplace.

Implements ``SkillsMarketplace`` against a directory of skills shipped
inside the package itself (``personalclaw/skills/bundled/``). Each
subdirectory containing a ``SKILL.md`` is treated as one skill; the
directory name is the skill id.

This marketplace ships with the package — no API key, no network, no
CLI fallback — so a fresh ``pip install personalclaw`` always has a working
catalog of curated skills.
"""

import logging
from importlib import resources
from pathlib import Path

from personalclaw.skills.marketplace import (
    SkillDetail,
    SkillEntry,
    SkillsMarketplace,
    _parse_description,
    get_default_skills_registry,
    read_skill_file_entry,
)

logger = logging.getLogger(__name__)

_BUNDLED_PKG = "personalclaw.skills.bundled"
_SKILL_FILENAME = "SKILL.md"


def _bundled_root() -> Path:
    """Return the on-disk path of the bundled skills directory.

    Uses ``importlib.resources.files`` so the lookup works whether the
    package is installed editable (``pip install -e``), as a wheel, or
    run from a source checkout.
    """
    return Path(str(resources.files(_BUNDLED_PKG)))


class NativeSkillsMarketplace(SkillsMarketplace):
    """Bundled-on-disk skills marketplace.

    The catalog is the set of subdirectories under
    ``personalclaw/skills/bundled/`` that contain a ``SKILL.md``.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or _bundled_root()

    @property
    def marketplace_type(self) -> str:
        return "native"

    @property
    def trust_tier(self) -> str:
        # Native/bundled + user-local skills are trusted provenance — the scan runs but
        # its warnings are advisory (a bundled skill's `curl` isn't a community risk).
        # The DANGEROUS floor still applies (never downgraded).
        return "trusted"

    def _iter_skill_dirs(self) -> list[Path]:
        if not self._root.is_dir():
            return []
        out: list[Path] = []
        for entry in sorted(self._root.iterdir()):
            if entry.is_dir() and (entry / _SKILL_FILENAME).is_file():
                out.append(entry)
        return out

    def search(self, query: str, limit: int = 20) -> list[SkillEntry]:
        q = query.strip().lower()
        results: list[SkillEntry] = []
        for skill_dir in self._iter_skill_dirs():
            name = skill_dir.name
            description = _parse_description(skill_dir / _SKILL_FILENAME)
            if q and q not in name.lower() and q not in description.lower():
                continue
            results.append(
                SkillEntry(
                    id=name,
                    name=name,
                    description=description,
                    source="native",
                )
            )
            if len(results) >= limit:
                break
        return results

    def fetch(self, skill_id: str) -> SkillDetail:
        skill_dir = self._root / skill_id
        if not skill_dir.is_dir() or not (skill_dir / _SKILL_FILENAME).is_file():
            raise RuntimeError(f"Native skill not found: {skill_id!r}")

        files: list[dict[str, object]] = []
        for f in sorted(skill_dir.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(skill_dir).as_posix()
            try:
                files.append(read_skill_file_entry(f, rel))
            except OSError:
                logger.warning("Skipping unreadable file in native skill %s: %s", skill_id, rel)
                continue

        return SkillDetail(
            id=skill_id,
            name=skill_id,
            files=files,
            audit_status="pass",
        )


get_default_skills_registry().register("native", NativeSkillsMarketplace())

# Also register user-installed skills as searchable
from personalclaw.skills.loader import skills_dir as _user_skills_dir  # noqa: E402

get_default_skills_registry().register(
    "installed", NativeSkillsMarketplace(root=_user_skills_dir())
)


def create_provider(config=None):
    """Extension factory for native skills provider."""
    from personalclaw.skills.loader import SkillsLoader

    return SkillsLoader()
