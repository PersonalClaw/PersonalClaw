"""Agent/roster packs — staged rosters + the ``always``-tier deploy (AGENT-PACKS §4.2, AP-4).

A *roster pack* is an ordinary pack whose dominant components are agent personas, plus two
extra members the manifest does not list as components:

* ``agents/catalog.json`` — the roster: one row per persona, carrying the presentation
  fields (``label``/``icon``/``color``) and, load-bearing, an **activation tier**;
* ``agents/runbooks/<slug>.json`` — optional scenario runbooks, each naming the roster
  slugs that scenario needs.

Two rules make the difference between "a pack of agents" and a roster:

**Every slug must resolve, or the import is refused.** A catalog row or runbook that names
a persona the pack does not carry would install a roster whose one-click deploy silently
skips a member — so :func:`lint_roster` emits an ERROR naming the exact unresolved ref
(``agent:<slug>``), and the importer's existing lint gate turns that into a refusal BEFORE
any byte is written. The check runs against ORIGINAL component ids, because that is the
namespace the pack author wrote.

**Only the ``always`` tier deploys.** :func:`deploy_roster` promotes the ``always`` rows into
``config.json``'s ``agents{}`` map (the seam ``resolve_bindings`` actually reads — persona
body, voice, model and skills all land as an :class:`AgentProfile`, so a deployed agent is
selectable and behaves as its persona). ``phase-N`` and ``as-needed`` rows are installed but
stay DORMANT: their personas exist in the agent store, and nothing makes them a live agent
until a human surfaces them. A pack that could enable its whole staged roster on install
would be arming a team nobody hired.

The roster itself is staged at ``packs/staged/<pack>/roster.json`` (the same propose-don't-write
staging area §3.1 uses for triggers and config), rewritten to the FRESH ids a commit assigned,
so a deploy months later resolves the personas that actually landed.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: The one tier a one-click deploy touches (§4.2).
ACTIVATION_ALWAYS = "always"

#: Tiers a staged roster row may declare. ``always`` deploys; ``phase-N`` is a wave the user
#: advances to; ``as-needed`` surfaces contextually. Closed on purpose — an unrecognised tier
#: is an authoring error, not a new behaviour, and defaulting it to ``always`` would deploy
#: an agent the author staged deliberately.
_ACTIVATION_RE = re.compile(r"^(always|as-needed|phase-[1-9][0-9]*)$")

#: Pack members this module owns (never manifest components — they describe components).
CATALOG_MEMBER = "agents/catalog.json"
RUNBOOK_PREFIX = "agents/runbooks/"

#: The staged roster's filename under ``packs/staged/<pack>/``.
ROSTER_FILE = "roster.json"


@dataclass
class RosterEntry:
    """One roster row: which persona, how it presents, and when it activates."""

    slug: str
    name: str = ""
    description: str = ""
    label: str = ""
    icon: str = ""
    color: str = ""
    activation: str = ACTIVATION_ALWAYS
    #: The fresh local id this row's persona committed under. Equal to ``slug`` until a
    #: collision remap renames it; a deploy resolves THIS, never the author's slug.
    target: str = ""

    def __post_init__(self) -> None:
        if not self.target:
            self.target = self.slug

    @property
    def ref(self) -> str:
        """The component ref this row points at — the exact string a lint error names."""
        return f"agent:{self.slug}"

    @property
    def deploys(self) -> bool:
        return self.activation == ACTIVATION_ALWAYS

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "label": self.label,
            "icon": self.icon,
            "color": self.color,
            "activation": self.activation,
            "target": self.target,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RosterEntry":
        slug = str(raw.get("slug", "") or "").strip()
        return cls(
            slug=slug,
            name=str(raw.get("name", "") or ""),
            description=str(raw.get("description", "") or ""),
            label=str(raw.get("label", "") or ""),
            icon=str(raw.get("icon", "") or ""),
            color=str(raw.get("color", "") or ""),
            # An absent tier is the safest tier, not the deploying one.
            activation=str(raw.get("activation", "") or "as-needed").strip(),
            target=str(raw.get("target", "") or ""),
        )


@dataclass
class Runbook:
    """A scenario runbook: the roster slugs this scenario needs."""

    slug: str
    name: str = ""
    description: str = ""
    roster: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "roster": list(self.roster),
        }


# ── parsing (tolerant: a pack with no roster is the common case) ───────────────


def parse_catalog(raw: bytes | None) -> list[RosterEntry]:
    """Parse ``agents/catalog.json`` into roster rows.

    Tolerant by design — a missing, unparseable or mis-shaped catalog yields no rows, and a
    pack that carries agent components but no catalog is simply not a roster pack. What is
    NOT tolerant is a row whose slug does not resolve: that is :func:`lint_roster`'s job and
    it blocks the import.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        logger.debug("pack roster: catalog.json is not valid JSON")
        return []
    if not isinstance(data, list):
        return []
    entries: list[RosterEntry] = []
    seen: set[str] = set()
    for row in data:
        if not isinstance(row, dict):
            continue
        entry = RosterEntry.from_dict(row)
        if not entry.slug or entry.slug in seen:
            continue
        seen.add(entry.slug)
        entries.append(entry)
    return entries


def parse_runbooks(members: dict[str, bytes]) -> list[Runbook]:
    """Parse every ``agents/runbooks/<slug>.json`` member into a :class:`Runbook`."""
    books: list[Runbook] = []
    for name in sorted(members):
        if not name.startswith(RUNBOOK_PREFIX) or not name.endswith(".json"):
            continue
        slug = name[len(RUNBOOK_PREFIX) : -len(".json")]
        if not slug or "/" in slug:
            continue
        try:
            data = json.loads(members[name].decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            logger.debug("pack roster: runbook %s is not valid JSON", name)
            continue
        if not isinstance(data, dict):
            continue
        roster = [str(s).strip() for s in (data.get("roster") or []) if str(s).strip()]
        books.append(
            Runbook(
                slug=slug,
                name=str(data.get("name", "") or ""),
                description=str(data.get("description", "") or ""),
                roster=roster,
            )
        )
    return books


# ── the lint gate (a broken slug BLOCKS the import, named exactly) ─────────────


def lint_roster(
    entries: list[RosterEntry],
    runbooks: list[Runbook],
    agent_ids: set[str],
) -> list[Any]:
    """Findings for a roster whose slugs must all resolve. Returns ``LintFinding`` rows.

    ``agent_ids`` are the ORIGINAL ids of the agent components the pack carries. Every
    catalog row and every runbook roster slug must appear there; one that does not is an
    ERROR whose ``detail`` names the exact unresolved ref (``agent:<slug>``) so the refusal
    message tells the author which reference to fix rather than "roster invalid".

    An unrecognised activation tier is also an ERROR: silently coercing it would either
    deploy an agent the author staged, or dormant-ise one they meant to deploy.
    """
    from personalclaw.packs.lint import LintFinding

    findings: list[Any] = []
    for entry in entries:
        if entry.slug not in agent_ids:
            findings.append(
                LintFinding(
                    "error",
                    "unresolved_roster_slug",
                    f"roster:{entry.slug}",
                    f"roster catalog names {entry.ref!r} which the pack does not carry",
                )
            )
        if not _ACTIVATION_RE.match(entry.activation):
            findings.append(
                LintFinding(
                    "error",
                    "invalid_activation",
                    f"roster:{entry.slug}",
                    f"activation {entry.activation!r} is not one of "
                    "always | phase-N | as-needed",
                )
            )
    known = {e.slug for e in entries}
    for book in runbooks:
        for slug in book.roster:
            if slug in known or slug in agent_ids:
                continue
            findings.append(
                LintFinding(
                    "error",
                    "unresolved_roster_slug",
                    f"runbook:{book.slug}",
                    f"runbook names 'agent:{slug}' which the pack does not carry",
                )
            )
    return findings


def remap_entries(entries: list[RosterEntry], remap: dict[tuple[str, str], str]) -> None:
    """Point each row's ``target`` at the fresh id its persona committed under.

    Called after the importer assigns fresh ids, on the PARSED rows — the author's ``slug``
    is preserved (it is what a lint error and a re-export name), while ``target`` carries
    what a deploy must actually look up.
    """
    for entry in entries:
        entry.target = remap.get(("agent", entry.slug), entry.slug)


# ── staging + deploy ──────────────────────────────────────────────────────────


def roster_path(home: Path, stage: str) -> Path:
    """Where a pack's staged roster lives (``packs/staged/<pack>/roster.json``)."""
    return home / "packs" / "staged" / stage / ROSTER_FILE


def serialize_roster(entries: list[RosterEntry], runbooks: list[Runbook]) -> str:
    """The staged roster document — stable key order so a re-stage is a no-op diff."""
    return json.dumps(
        {
            "entries": [e.to_dict() for e in entries],
            "runbooks": [b.to_dict() for b in runbooks],
        },
        indent=2,
        ensure_ascii=False,
    )


def load_roster(stage: str, home: Path | None = None) -> tuple[list[RosterEntry], list[Runbook]]:
    """Read a pack's staged roster. Missing/corrupt reads as an empty roster (fail soft:
    a deploy surface that 500s on a corrupt file is worse than one that shows nothing)."""
    if home is None:
        from personalclaw.config.loader import config_dir

        home = config_dir()
    path = roster_path(home, stage)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return [], []
    if not isinstance(data, dict):
        return [], []
    entries = [RosterEntry.from_dict(r) for r in (data.get("entries") or []) if isinstance(r, dict)]
    runbooks = [
        Runbook(
            slug=str(r.get("slug", "") or ""),
            name=str(r.get("name", "") or ""),
            description=str(r.get("description", "") or ""),
            roster=[str(s) for s in (r.get("roster") or [])],
        )
        for r in (data.get("runbooks") or [])
        if isinstance(r, dict)
    ]
    return [e for e in entries if e.slug], runbooks


def deploy_roster(stage: str, home: Path | None = None) -> dict[str, list[str]]:
    """One-click team deploy: promote the ``always`` tier into ``config.json agents{}``.

    Returns ``{"deployed": [...], "dormant": [...], "missing": [...]}``:

    * **deployed** — the ``always`` rows now live in ``agents{}`` as an
      :class:`AgentProfile` carrying the persona's description/prompt/voice/model/skills, so
      ``resolve_bindings`` binds them and the agent is selectable;
    * **dormant** — every ``phase-N``/``as-needed`` row, deliberately untouched. This is the
      staged-roster contract: those personas are installed, not hired;
    * **missing** — an ``always`` row whose persona is not in the agent store. Reported, not
      raised: the import lint already refuses a pack with an unresolved slug, so reaching
      this means the persona was deleted after install, and the honest answer is to name it.

    Idempotent: re-deploying rewrites the same profile rather than duplicating it.
    """
    from personalclaw.config.loader import AgentProfile, AppConfig, config_dir

    if home is None:
        home = config_dir()
    entries, _ = load_roster(stage, home)
    deployed: list[str] = []
    dormant: list[str] = []
    missing: list[str] = []

    cfg = AppConfig.load()
    for entry in entries:
        if not entry.deploys:
            dormant.append(entry.target)
            continue
        defn = _load_persona(home, entry.target)
        if defn is None:
            missing.append(entry.target)
            continue
        cfg.agents[entry.target] = AgentProfile(
            description=entry.description or defn.description,
            system_prompt=defn.system_prompt,
            voice=defn.voice,
            model=defn.model,
            skills=list(defn.skills),
            source=f"pack:{stage}",
            specialty=defn.specialty,
            route_hints=defn.route_hints,
        )
        deployed.append(entry.target)

    if deployed:
        cfg.save()
    _audit_deploy(stage, deployed, dormant, missing)
    return {"deployed": deployed, "dormant": dormant, "missing": missing}


def _load_persona(home: Path, slug: str):
    """The committed :class:`AgentDefinition` for ``slug``, or None."""
    from personalclaw.agents.marketplace import AgentDefinition

    path = home / "agents" / slug / "agent.json"
    try:
        return AgentDefinition.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, UnicodeDecodeError, TypeError):
        return None


def _audit_deploy(stage: str, deployed: list[str], dormant: list[str], missing: list[str]) -> None:
    """SEL-audit the deploy: it grants live agents, which is a state change worth a trail."""
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="packs.roster",
            operation="pack_roster_deploy",
            outcome="completed" if deployed else "noop",
            source="dashboard",
            resources=f"{stage}: deployed={','.join(deployed)} dormant={len(dormant)}",
            error=f"missing: {','.join(missing)}" if missing else "",
        )
    except Exception:  # pragma: no cover - audit is best-effort
        logger.debug("pack roster deploy audit failed", exc_info=True)
