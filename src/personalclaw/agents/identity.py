"""Agent identity resolution — the canonical form of "which agent is this turn?".

One function, deliberately: normalizing a turn's agent identity is needed by anything
that records, scopes, or filters by agent, and it is not workflow-specific. It lived in
``workflows/composition.py`` because the old workflow feature's ``scope_ref`` was its
first consumer; ``chat_runner`` then imported it across that boundary. Relocated here so
the identity rule outlives whichever feature happens to need it (WORKFLOWS-V2 Phase 0).
"""

from __future__ import annotations


def resolve_agent_id(
    agent: str | None, provider_kind: str | None, provider_agent: str | None
) -> str:
    """Normalize a turn's agent identity to its binding-id form.

    This matches the values the frontend agent catalog uses:

    - native turn → the bare profile name (e.g. ``default``, ``personalclaw-loop``)
    - ACP turn   → ``acp:<cli>/<modeId>`` (e.g. ``acp:claude-code/<agent>``)

    ``provider_kind`` is the resolved provider (``native`` or ``acp:<cli>``);
    ``provider_agent`` is the ACP-internal modeId/agent. Falls back to ``agent``.
    """
    kind = (provider_kind or "").strip()
    if kind.startswith("acp:"):
        cli = kind.split(":", 1)[1]
        mode = (provider_agent or "").strip()
        return f"acp:{cli}/{mode}" if mode else f"acp:{cli}"
    # native (or unknown) → bare profile name
    return (agent or provider_agent or "").strip()
