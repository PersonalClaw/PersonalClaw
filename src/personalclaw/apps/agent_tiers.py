"""What an app's agent work may use: the tiers ``permissions.agent`` names, narrowest first.

Each is its own install-consent sentence (``web/src/pages/apps/installConsent.tsx``) and is held
where the app's agent work runs (``handlers/apps.api_app_agent_run``, an app's scheduled job
``apps/app_crons.start_job``, ``subagent_tier``, and the ``agent_work`` routes in
``apps/permissions.ROUTE_AUTHZ``):

* ``text``  — the model is handed the task the app sends and nothing else of the owner's, and gets
  no tools: it answers in text (a summary, an extraction, a draft).
* ``read``  — an agent with read-only tools: it may read her files and data, and change nothing.
* ``tools`` — an agent with her tools. The app approves none of its calls, so each one that needs
  approval asks her.

No tier lets an app's agent approve its own calls, so an app's agent never outranks her approval
settings or the operator ceiling: a tier only narrows what the agent may use. ``true``, the one
boolean the tiers replaced (every tool, nothing asked), names none of them.
"""

from __future__ import annotations

import json

AGENT_TEXT = "text"
AGENT_READ = "read"
AGENT_TOOLS = "tools"
AGENT_TIERS: tuple[str, ...] = (AGENT_TEXT, AGENT_READ, AGENT_TOOLS)


def agent_tier_covers(held: str, needed: str) -> bool:
    """Whether an app holding agent tier *held* may do work that needs tier *needed*.

    A name that is not a tier, on either side, covers nothing: a typo never widens a grant."""
    if held not in AGENT_TIERS or needed not in AGENT_TIERS:
        return False
    return AGENT_TIERS.index(held) >= AGENT_TIERS.index(needed)


def capability_class(tier: str) -> str:
    """The subagent capability class a run at *tier* is held to: ``text`` no tools at all
    (``subagent_tier.CAPABILITY_TEXT``), ``read`` the research class's read-only tools, ``tools``
    the mutating class's every tool. A name that is not a tier holds the narrowest, no tools."""
    from personalclaw.subagent import CAPABILITY_MUTATING, CAPABILITY_RESEARCH
    from personalclaw.subagent_tier import CAPABILITY_TEXT

    return {AGENT_READ: CAPABILITY_RESEARCH, AGENT_TOOLS: CAPABILITY_MUTATING}.get(
        tier, CAPABILITY_TEXT
    )


def declared_agent(value: object) -> tuple[str, str]:
    """``(tier, raw)`` for a manifest's ``permissions.agent``: the tier it names, or ``""`` and the
    value as written when it names none. Absent and ``false`` declare no agent at all."""
    if value is None or value is False or value == "":
        return "", ""
    if isinstance(value, str) and value in AGENT_TIERS:
        return value, ""
    return "", json.dumps(value, default=str)
