"""Capability-discovery power-ups (Platform-Legibility §6).

The dashboard widget's data source. It answers one question the self-description
machinery made computable for the first time: *of the capabilities this instance
has, which has the user never touched?* — then proposes ONE of them at a time as
a two-sentence mini-lesson with a "try it" deep link.

Inputs, all existing or free by-products:

* the §1 manifest ``tools[]`` — the **denominator** (every registered tool, with
  its description + worked examples from ``TOOL_META``);
* :class:`~personalclaw.legibility.tool_usage.ToolUsageStore` — the tools the user
  has actually invoked (the "touched" set);
* per-capability dismissals persisted in ``entity_settings/legibility.json`` (the
  notifications-settings pattern) — a dismissed capability never resurfaces.

**Propose-don't-write (the soul guardrail):** the lesson is a deterministic
template over the manifest entry — no LLM call, no config write, no capability
ever enabled on the user's behalf. The widget only points; the user acts.

A capability is teachable only if the manifest gives it a worked ``example`` —
that both guarantees a concrete "try it" and filters the low-level surface down
to things worth surfacing. Ordering is deterministic (by name) so the "next"
power-up is stable across polls until the user touches or dismisses the current one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# entity_settings key + field for dismissals (the notifications.json pattern).
_ENTITY = "legibility"
_DISMISSED_FIELD = "dismissed_power_ups"


@dataclass(frozen=True)
class PowerUp:
    """One proposed capability lesson."""

    id: str  # stable, e.g. "tool:knowledge_add"
    kind: str  # "tool" (only tools today; kept for forward shape)
    name: str
    title: str
    provider: str
    lesson: str  # two-sentence deterministic mini-lesson
    try_it: dict[str, Any] = field(default_factory=dict)  # {route, query, label}

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "title": self.title,
            "provider": self.provider,
            "lesson": self.lesson,
            "try_it": dict(self.try_it),
        }


def _first_sentence(text: str, *, limit: int = 220) -> str:
    """The first sentence of a description, trimmed — sentence 1 of the lesson."""
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    for end in (". ", "? ", "! "):
        i = text.find(end)
        if 0 < i < limit:
            return text[: i + 1].strip()
    return (text if len(text) <= limit else text[: limit - 1].rstrip() + "…").strip()


def _lesson_for(tool: dict[str, Any]) -> str:
    """Two-sentence deterministic lesson: what it does + how to reach for it."""
    what = _first_sentence(tool.get("description", ""))
    if what and what[-1] not in ".?!":
        what += "."
    name = tool.get("name", "this tool")
    return (
        f"{what} You haven't used {name} yet — open it to see its inputs and "
        "try it on a real task."
    ).strip()


def build_power_up(tool: dict[str, Any]) -> PowerUp:
    """Assemble a :class:`PowerUp` from a manifest ``tools[]`` entry."""
    name = str(tool.get("name", ""))
    return PowerUp(
        id=f"tool:{name}",
        kind="tool",
        name=name,
        title=name,
        provider=str(tool.get("provider", "")),
        lesson=_lesson_for(tool),
        # Deep link into the Tools page focused on this tool (the ?open= param
        # ToolsPage already honors) — the "try it" affordance, propose-only.
        try_it={"route": "tools", "query": {"open": name}, "label": "Open in Tools"},
    )


def _teachable_tools(manifest_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Manifest tools worth surfacing: a description AND ≥1 worked example.

    Requiring an example filters the low-level surface to capabilities the
    manifest documents well enough to teach, and guarantees the lesson has a
    concrete thing to point at. Sorted by name for a stable "next" pick.
    """
    out = [
        t
        for t in manifest_tools
        if str(t.get("description", "")).strip() and list(t.get("examples", ()))
    ]
    out.sort(key=lambda t: str(t.get("name", "")))
    return out


def select_power_up(
    manifest_tools: list[dict[str, Any]],
    *,
    used: set[str],
    dismissed: set[str],
) -> tuple[PowerUp | None, int, int]:
    """Pick the next untouched, non-dismissed capability to propose.

    Returns ``(power_up | None, untouched_count, teachable_total)``. ``None`` when
    every teachable capability has been touched or dismissed — the widget then
    renders its "you've explored everything" empty state.
    """
    teachable = _teachable_tools(manifest_tools)
    untouched = [t for t in teachable if str(t.get("name", "")) not in used]
    candidates = [t for t in untouched if f"tool:{t.get('name', '')}" not in dismissed]
    chosen = build_power_up(candidates[0]) if candidates else None
    return chosen, len(untouched), len(teachable)


# ── dismissal persistence (entity_settings/legibility.json) ─────────────────


def load_dismissed() -> set[str]:
    """The set of dismissed power-up ids (empty on any read error)."""
    from personalclaw.providers.entity_routes import _load_entity_settings

    raw = _load_entity_settings(_ENTITY)
    ids = raw.get(_DISMISSED_FIELD, [])
    return {str(x) for x in ids} if isinstance(ids, list) else set()


def dismiss(power_up_id: str) -> set[str]:
    """Persist *power_up_id* as dismissed; returns the full dismissed set."""
    from personalclaw.providers.entity_routes import (
        _load_entity_settings,
        _save_entity_settings,
    )

    current = _load_entity_settings(_ENTITY)
    existing = current.get(_DISMISSED_FIELD, [])
    ids = {str(x) for x in existing} if isinstance(existing, list) else set()
    ids.add(str(power_up_id))
    current[_DISMISSED_FIELD] = sorted(ids)
    _save_entity_settings(_ENTITY, current)
    return ids


async def compute_power_up() -> dict[str, Any]:
    """The live power-up payload for ``GET /api/legibility/power-ups``.

    Honors the ``legibility.power_ups`` kill switch server-side (a disabled
    instance returns ``enabled: false`` with no proposal), reads the live tool
    surface via the manifest generator, and applies the touched + dismissed sets.
    """
    from personalclaw.config.loader import AppConfig

    if not AppConfig.load().legibility.power_ups:
        return {"enabled": False, "power_up": None, "untouched_count": 0, "total": 0}

    from personalclaw.legibility.tool_usage import ToolUsageStore
    from personalclaw.manifest import build_manifest

    manifest = await build_manifest(None)  # tools only — routes not needed here
    tools = list(manifest.get("tools", []))
    used = ToolUsageStore().used_names()
    dismissed = load_dismissed()
    power_up, untouched, total = select_power_up(tools, used=used, dismissed=dismissed)
    return {
        "enabled": True,
        "power_up": power_up.to_dict() if power_up else None,
        "untouched_count": untouched,
        "total": total,
    }
