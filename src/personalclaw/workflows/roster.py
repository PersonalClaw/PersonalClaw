"""Agent roster — a slug-keyed catalog PROJECTION over the config agents (WORK-R16).

Not a second registry. `AgentDefinition`s live in `config.json agents{}` (the `agent` entity's
source_of_truth is config), so a roster that stored its own copy of an agent would be a second
source of truth that drifts the moment a user renames one. This module derives the catalog on every
call and owns no state.

Two things the projection buys that reading `config.agents` directly does not:

* **Slugs.** Templates and routing policy reference agents by SLUG — a stable, filename-shaped stem
  — never by display name. A display name is presentation; renaming one must not break every
  template that mentioned it.
* **A drift check.** `unresolved_slugs` answers "does every slug something references still resolve
  to a real agent?". Checked by the test suite (our CI gate), because a template pointing at an
  agent the user deleted fails at RUN time otherwise — the most expensive moment to discover it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Everything that is not slug-shaped collapses to a hyphen. Slugs are lowercase so two agents
#: differing only in case cannot occupy two catalog rows.
_UNSAFE = re.compile(r"[^a-z0-9]+")

#: How an entry reaches a run. `always` is offered to every run, `conditional` only when a
#: template/routing rule names it, `on-demand` only on an explicit request. Staging exists so a
#: simple run is not handed an oversized persona set — the measured cost of a wide roster.
ACTIVATIONS = ("always", "conditional", "on-demand")


def slugify(name: str) -> str:
    """An agent's stable catalog key.

    Derived from the name rather than stored, so the catalog needs no migration when an agent is
    added: the derivation IS the key. Empty input yields `"agent"` rather than an empty string,
    because an empty key would silently merge every unnamed entry into one row.
    """
    stem = _UNSAFE.sub("-", (name or "").strip().lower()).strip("-")
    return stem or "agent"


@dataclass
class RosterEntry:
    """One catalog row.

    `name` is the config key (what `spawn(agent=...)` takes) and `label` is for display; they are
    separate fields because collapsing them is what makes a rename break a template.
    """

    slug: str
    name: str
    description: str = ""
    label: str = ""
    icon: str = ""
    capabilities: list[str] = field(default_factory=list)
    model_tier_hint: str = ""
    activation: str = "conditional"
    reserved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "label": self.label,
            "icon": self.icon,
            "capabilities": list(self.capabilities),
            "model_tier_hint": self.model_tier_hint,
            "activation": self.activation,
            "reserved": self.reserved,
        }


def _entry(name: str, profile: Any, reserved_names: frozenset[str]) -> RosterEntry:
    description = str(getattr(profile, "description", "") or "")
    tools = [str(t) for t in (getattr(profile, "tools", None) or [])]
    skills = [str(s) for s in (getattr(profile, "skills", None) or [])]
    return RosterEntry(
        slug=slugify(name),
        name=name,
        description=description,
        # The config key doubles as the label when no separate display name exists. Deriving a
        # prettier one would invent a name the user never chose and cannot search for.
        label=name,
        icon="",
        capabilities=sorted({*tools, *skills}),
        model_tier_hint=str(getattr(profile, "model", "") or ""),
        # A reserved system agent is part of the platform and always available; a user's own agent
        # is offered only when something names it, which is what keeps a simple run's roster small.
        activation="always" if name in reserved_names else "conditional",
        reserved=name in reserved_names,
    )


def catalog(agents: dict[str, Any] | None = None) -> list[RosterEntry]:
    """The roster, slug-ordered.

    `agents` defaults to the live config, and is injectable so the drift check and its tests can run
    against a given set without a home. Two config agents whose names slugify identically would
    collide; the FIRST in sorted order wins and the second is dropped, deliberately — silently
    merging them would make one agent's description describe the other.
    """
    from personalclaw.agents.defaults import RESERVED_AGENT_NAMES

    if agents is None:
        from personalclaw.config.loader import AppConfig

        agents = AppConfig.load().agents or {}

    seen: dict[str, RosterEntry] = {}
    for name in sorted(agents):
        entry = _entry(str(name), agents[name], RESERVED_AGENT_NAMES)
        seen.setdefault(entry.slug, entry)
    return [seen[slug] for slug in sorted(seen)]


def resolve(slug: str, agents: dict[str, Any] | None = None) -> RosterEntry | None:
    """The entry for a slug, or None. Slug-keyed lookup is the ONLY supported resolution path."""
    target = slugify(slug)
    for entry in catalog(agents):
        if entry.slug == target:
            return entry
    return None


def unresolved_slugs(referenced: list[str], agents: dict[str, Any] | None = None) -> list[str]:
    """The drift check: which referenced slugs do NOT resolve to a real agent.

    Returns the unresolved names rather than a bool so a failing gate can SAY which reference broke
    — a check reporting only "something drifted" sends a reader back to grep for it.
    """
    known = {entry.slug for entry in catalog(agents)}
    missing = {slugify(s) for s in referenced if s and slugify(s) not in known}
    return sorted(missing)


def referenced_slugs(spec: dict[str, Any]) -> list[str]:
    """Every agent slug a workflow spec names, from any node depth.

    Walks the whole tree because `agent` is a per-node config key: checking only the root would
    pass a spec whose fifth leaf points at a deleted agent.
    """
    found: set[str] = set()

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            config = node.get("config")
            if isinstance(config, dict):
                agent = str(config.get("agent", "") or "").strip()
                if agent:
                    found.add(slugify(agent))
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(spec)
    return sorted(found)
