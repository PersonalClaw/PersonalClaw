"""SDK: the skills-marketplace contract + data types.

Stable re-export of the generic skills-source infrastructure
(``personalclaw.skills.marketplace``) — a skills-source app (e.g. skills.sh)
implements ``SkillsMarketplace`` (a read-only ``search`` + ``fetch`` source) via
these, not the core module directly. Provider-agnostic: any skills registry is an
implementation of this one contract.
"""

from personalclaw.skills.marketplace import (  # noqa: F401
    SkillDetail,
    SkillEntry,
    SkillsMarketplace,
    get_default_skills_registry,
    read_skill_file_entry,
)

__all__ = [
    "SkillsMarketplace",
    "SkillEntry",
    "SkillDetail",
    "get_default_skills_registry",
    "read_skill_file_entry",
]
