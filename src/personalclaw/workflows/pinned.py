"""Pinned artifacts — the dashboard's pin list (WORK-CONTAINERS §6.5d, R13 — WF2WOR-7).

**Why a list and not a tile registry.** The dashboard has NO tile registry: the bento grid and
per-user layout persistence were deliberately retired, and widgets are hard-imported by
``DashboardPage.tsx``. So "pin to dashboard" is NOT a layout feature here. Pinning registers an
artifact slug in this list, and ONE hard-imported ``PinnedArtifacts`` widget renders it — the
established pattern (one component in ``pages/dashboard/widgets/``). Inventing a per-tile registry
to serve one feature would rebuild the exact machinery that was removed.

**The store** lives at ``entity_settings/pinned_artifacts.json``, via the shared entity-settings
helpers, following the ``channel_trust`` precedent: user/entity state belongs in
``entity_settings/*.json``, never in ``config.json``.

**A pin holds a REFERENCE, never a copy.** It stores the slug and when it was pinned — not the
artifact's name or content. A denormalized copy would go stale the moment the artifact was renamed
or revised, and a dashboard widget showing a title the artifact no longer has is worse than one
showing nothing: it is confidently wrong. The widget resolves each slug through the artifacts API
at render time, which is also how a DELETED artifact self-heals off the surface.

**Order is the user's, not the clock's.** Pins render newest-first because that is what a pin
means here — "I care about this now" — and re-pinning an already-pinned slug MOVES it rather than
duplicating it. A list that could hold one slug twice would render two identical cards.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

#: entity_settings key. One file, one list.
_ENTITY = "pinned_artifacts"

#: How many pins the dashboard will hold. A bound rather than unlimited: the widget is a glance
#: surface, and a hundred pins is a list nobody reads — the same reasoning that keeps the
#: introspection timeline to a whitelist of kinds. Pinning past the cap evicts the OLDEST pin,
#: because the newest pin is the one the user just asked for.
MAX_PINS = 12


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> list[dict[str, Any]]:
    """The pin list, newest-first. A corrupt or missing store reads as empty.

    Fail-OPEN for the store, deliberately: a malformed pin file must never break the dashboard.
    There is nothing security-bearing here — a pin is a bookmark, so the worst case of a bad read
    is an empty widget, and crashing the dashboard over a bookmark would be the real bug.
    """
    from personalclaw.providers.entity_routes import _load_entity_settings

    raw = _load_entity_settings(_ENTITY)
    pins = raw.get("pins") if isinstance(raw, dict) else None
    if not isinstance(pins, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in pins:
        if not isinstance(entry, dict):
            continue
        slug = str(entry.get("slug") or "")
        if not slug:
            continue
        out.append(
            {
                "slug": slug,
                "pinned_at": str(entry.get("pinned_at") or ""),
                # The run that produced it, when a run did. Carried so the widget can deep-link
                # back to the producing cockpit — the lineage direction §2.5 already established
                # for the outbox. Empty for a hand-created artifact.
                "run_id": str(entry.get("run_id") or ""),
            }
        )
    return out


def _save(pins: list[dict[str, Any]]) -> None:
    from personalclaw.providers.entity_routes import _save_entity_settings

    _save_entity_settings(_ENTITY, {"pins": pins[:MAX_PINS]})


def list_pins() -> list[dict[str, Any]]:
    """Every pin, newest-first. References only — the caller resolves each slug."""
    return _load()


def is_pinned(slug: str) -> bool:
    """Whether this slug is pinned. Used to render the pin control's current state."""
    target = (slug or "").strip()
    return bool(target) and any(p["slug"] == target for p in _load())


def pin(slug: str, *, run_id: str = "") -> list[dict[str, Any]]:
    """Pin an artifact, or MOVE an existing pin to the front. Returns the new list.

    Idempotent by slug rather than append-only: a list that could hold one slug twice would
    render two identical cards, and "pin" is not a verb a user expects to accumulate.
    """
    target = (slug or "").strip()
    if not target:
        return _load()
    pins = [p for p in _load() if p["slug"] != target]
    pins.insert(0, {"slug": target, "pinned_at": _now(), "run_id": (run_id or "").strip()})
    # The cap evicts the OLDEST, because the pin the user just created is the one they want.
    pins = pins[:MAX_PINS]
    _save(pins)
    return pins


def unpin(slug: str) -> list[dict[str, Any]]:
    """Remove a pin. Unpinning something that was never pinned is a no-op, not an error —
    a double-click on Unpin should not produce a failure the user has to read."""
    target = (slug or "").strip()
    pins = [p for p in _load() if p["slug"] != target]
    _save(pins)
    return pins
