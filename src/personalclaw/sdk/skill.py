"""SDK: the skills-marketplace contract + data types.

Stable re-export of the generic skills-source infrastructure
(``personalclaw.skills.marketplace``) — a skills-source app (e.g. skills.sh)
implements ``SkillsMarketplace`` (a read-only ``search`` + ``fetch`` source) via
these, not the core module directly. Provider-agnostic: any skills registry is an
implementation of this one contract.

``SkillsRegistry`` and ``InstallResult`` are here because ``get_default_skills_registry()``
is (#3511): it returns the registry, and the registry's ``install_guarded()`` returns an
``InstallResult``. An app that installs a skill through the published accessor could not
annotate what it got back, so the only way to branch on the outcome was to read attributes
off an ``Any``.
"""

from personalclaw.skills.marketplace import (  # noqa: F401
    InstallResult,
    SkillDetail,
    SkillEntry,
    SkillsMarketplace,
    SkillsRegistry,
    get_default_skills_registry,
    read_skill_file_entry,
)

__all__ = [
    "SkillsMarketplace",
    "SkillEntry",
    "SkillDetail",
    "SkillsRegistry",
    "InstallResult",
    "get_default_skills_registry",
    "read_skill_file_entry",
]
