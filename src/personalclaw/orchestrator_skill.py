"""Generate the orchestrator SKILL.md — an always-loaded routing table.

Loaded into default personalclaw agent so delegation is transparent.
Auto-seeds metadata files for agents that lack one.
"""

import logging
from pathlib import Path

from personalclaw.agent_metadata import load, load_all, save
from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

# Agents to exclude from the roster (self-references).
_EXCLUDE = {"personalclaw", "personalclaw-orchestrator"}


def generate_orchestrator_skill(skills_loader) -> Path:
    """Write orchestrator/SKILL.md under skills_loader._dir.

    Reads configured agents from AppConfig, auto-seeds metadata,
    and builds a rich SKILL.md with delegation guidelines + roster.
    """
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()

    class _Agent:
        def __init__(self, name: str, description: str) -> None:
            self.name = name
            self.description = description

    agents = [
        _Agent(name, getattr(ac, "description", "") or "")
        for name, ac in cfg.agents.items()
        if name not in _EXCLUDE
    ]

    # Auto-seed metadata from agent description if missing.
    for a in agents:
        if not load(a.name) and a.description:
            save(a.name, a.description)
            logger.info("Auto-seeded metadata for %s from description", a.name)

    metadata = load_all()

    # Build roster section.
    roster_lines: list[str] = []
    for a in agents:
        desc = metadata.get(a.name) or a.description or "No description available"
        roster_lines.append(f"### {a.name}\n\n{desc}\n")

    roster = "\n".join(roster_lines) if roster_lines else "_No specialist agents installed._\n"
    # Render the bound orchestrator-skill prompt; fall back to the shipped template.
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    skill_content = render_use_case_prompt("orchestrator_skill", {"roster": roster})
    if skill_content is None:
        skill_content = _SKILL_TEMPLATE.format(roster=roster)

    out = skills_loader._dir / "orchestrator" / "SKILL.md"
    atomic_write(out, skill_content)
    # Remove the legacy conductor/ skill dir if a pre-rename install left one — it's
    # an always-loaded skill, so a stale copy would double-inject the routing table.
    _remove_legacy_conductor(skills_loader)
    return out


def _remove_legacy_conductor(skills_loader) -> None:
    """Delete a pre-rename ``conductor/`` generated skill dir if present. The
    feature was renamed conductor → orchestrator; the old always-loaded SKILL.md
    must not linger beside the new one."""
    import shutil

    legacy = skills_loader._dir / "conductor"
    if legacy.is_dir():
        try:
            shutil.rmtree(legacy)
            logger.info("Removed legacy conductor skill dir (renamed to orchestrator)")
        except OSError:
            logger.debug("Could not remove legacy conductor skill dir", exc_info=True)


_SKILL_TEMPLATE = """\
---
always: true
---
# Agent Delegation

You have access to specialist agents via `subagent_run(agent="<name>", task="<description>")`.

## Default behavior

You (personalclaw) are the default agent and can handle most tasks directly.
Only delegate when you are highly confident a specialist is a better fit.
When in doubt, handle it yourself.

## When to delegate

- The task clearly and specifically matches a specialist's description below
- The specialist has domain expertise or tools you lack for this exact task
- The user explicitly asks to use a specific agent

## When NOT to delegate

- You can handle the task yourself (this is the common case)
- The match to a specialist is only partial or vague
- Simple questions, general coding, file operations, or conversational tasks
- The user is in a back-and-forth conversation (don't break the flow)
- No specialist below is a strong match — handle it yourself

## Effort scaling

- Most requests → handle yourself directly
- Needs specialist tools → spawn 1 agent
- Complex multi-part task → up to 3 agents in parallel (max concurrent limit)

## Delegation quality

Write specific task descriptions. Include context the specialist needs.
- Bad: "review the code"
- Good: "Review PR #123 for security issues, focusing on auth token handling in session.py"

## Available Agents

{roster}\
"""
