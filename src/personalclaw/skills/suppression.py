"""Bench-only skill suppression (EVALUATION-SUBSTRATE §3.3).

The §3.3 impact bench replays a skill's consulted runs with that skill **surfaced vs
suppressed**. The comparison only means anything if suppression genuinely removes the
skill from what the model saw — a "suppressed" arm whose prompt still carries the body
measures nothing and reports a delta of 0.0, which reads exactly like a skill that does
not matter.

So suppression is enforced at the ONE choke point every prompt path goes through:
:meth:`personalclaw.skills.loader.SkillsLoader.load_skill`, the only place a skill body
is read. Force-loaded loop skills, passively surfaced skills, and ``skill_invoke`` all
call it, so a suppressed skill's body cannot reach a prompt through any of them.

The suppression set lives in the PROCESS ENV, not in a file and not in config: it is set
by :func:`personalclaw.evals.overlay.apply_in_child` inside a throwaway eval child and
therefore cannot outlive that child or leak into the operator's gateway. An empty/absent
env var means "suppress nothing", which is the shipped behaviour — that equivalence is
what makes this safe to have in the load path at all.
"""

from __future__ import annotations

import os

from personalclaw.evals.overlay import SUPPRESSED_SKILLS_ENV

__all__ = ["SUPPRESSED_SKILLS_ENV", "is_suppressed", "suppressed_skills"]


def suppressed_skills(env: dict | None = None) -> frozenset[str]:
    """Skill names suppressed in this process. Empty when the env var is absent."""
    source = os.environ if env is None else env
    raw = str(source.get(SUPPRESSED_SKILLS_ENV) or "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def is_suppressed(name: str, env: dict | None = None) -> bool:
    """Is *name* suppressed for this process?"""
    return bool(name) and name in suppressed_skills(env)
