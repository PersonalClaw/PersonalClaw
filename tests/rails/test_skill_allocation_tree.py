"""The source-tree checks split from
``tests/test_skill_allocation.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import skill_allocation_shared
import source_index
from rails_repo import REPO

from personalclaw.learning.surfacing import count_tokens
from personalclaw.skills.allocation import CONTEXT_TIERS, resolve_tier
from personalclaw.skills.loader import SkillsLoader

_usage_store_stays_in_tmp = skill_allocation_shared._usage_store_stays_in_tmp


class TestTheCapsArePerSkillAndInAggregate:
    def test_every_bundled_skill_fits_the_cap_its_declared_tier_grants(self):
        """A ratchet on the shipped library: no bundled skill loads reduced by default.

        A skill that outgrows its declared tier must either shrink or declare a bigger
        tier; discovering it as a silent reduction in a user's turn is the wrong place.
        """
        from personalclaw.skills.allocation import full_block
        from personalclaw.skills.loader import parse_frontmatter

        bundled = REPO / "src/personalclaw/skills/bundled"
        over: list[str] = []
        skills = [
            p
            for p in source_index.under(source_index.package_any_files(), bundled)
            if p.name == "SKILL.md"
        ]
        for path in skills:
            raw = source_index.text(path)
            tier = resolve_tier(parse_frontmatter(raw).get("context_tier", ""))
            body = SkillsLoader.strip_frontmatter(raw).strip()
            tokens = count_tokens(full_block(path.parent.name, body))
            if tokens > CONTEXT_TIERS[tier]:
                over.append(f"{path.parent.name}: {tokens} tokens > {tier} cap")
        assert not over, over
        assert len(skills) >= 15, "vacuity floor: skills were found"
