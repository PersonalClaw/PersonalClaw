"""PersonalClaw skills package — marketplace abstraction, native + skills.sh clients, and SkillsLoader."""  # noqa: E501

# Re-export the pre-existing SkillsLoader and helpers from the loader module
# so existing callers (`from personalclaw.skills import SkillsLoader`) continue to work.
# Auto-register the CORE-BUILTIN marketplaces. Each module's top-level code calls
# get_default_skills_registry().register(...) on import. (skills.sh moved to a
# standalone app — apps/skills-sh/ — and registers via the app loader when installed,
# so core no longer eager-imports it.)
from personalclaw.skills import native as _native  # noqa: F401
from personalclaw.skills.loader import (  # noqa: F401
    AUTO_SKILL_MAX_PROCEDURE_CHARS,
    AutoSkillProvenance,
    SkillsLoader,
    _auto_name_from_title,
)
from personalclaw.skills.marketplace import (  # noqa: F401
    SkillEntry,
    SkillsMarketplace,
    SkillsRegistry,
    get_default_skills_registry,
)

__all__ = [
    "AUTO_SKILL_MAX_PROCEDURE_CHARS",
    "AutoSkillProvenance",
    "SkillsLoader",
    "SkillEntry",
    "SkillsMarketplace",
    "SkillsRegistry",
    "get_default_skills_registry",
    "_auto_name_from_title",
]
