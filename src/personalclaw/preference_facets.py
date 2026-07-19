"""Typed, decaying preference-facet model (learn-preference-facets).

A principled user-profile layer distinct from contextual memory: preferences are
**typed facets** with a **stability score that decays by class half-life**, so a
one-off stylistic nudge fades unless reinforced while an identity fact persists.

Six facet classes (with decay half-lives):
- ``style``    — how the user likes responses (terse, code-first…)   30d
- ``identity`` — who they are (name, role, stack)                    90d
- ``tooling``  — preferred tools/workflows                           30d
- ``goal``     — standing objectives                                 30d
- ``channel``  — per-surface prefs                                    7d
- ``veto``     — hard "never do X"  → THIS IS A LESSON, not a facet:
  vetoes route to ``write_lesson`` so the agent's "always/never" rules live in
  ONE place (the lesson store + contradiction judge), not a parallel model.

Stability = ``base × cue × decay(age, half_life)``. Cue families weight the
evidence (Explicit 1.0 → Recurrence 0.6). State machine (Active / Provisional /
Candidate / Dropped) is derived from the live stability + the user overrides
(Pinned = floor 1.0, Forgotten = 0). The Active facets render into an always-on
ambient PROFILE block (the stable-defaults half; on-demand recall stays separate).

No new LLM calls: facet candidates come from cheap heuristics + the EXISTING
consolidation/after-turn summarizer. Persisted as ``pref.facet.<class>.<slug>``
semantic keys, reusing semantic memory (+ supersession + recall_count).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

FACET_CLASSES = ("style", "identity", "tooling", "goal", "channel", "veto")

# Class decay half-lives (days) — how fast stability halves without reinforcement.
_HALF_LIFE_DAYS: dict[str, float] = {
    "identity": 90.0, "style": 30.0, "tooling": 30.0, "goal": 30.0, "channel": 7.0,
}

# Cue families → base evidence weight.
_CUE_WEIGHT: dict[str, float] = {
    "explicit": 1.0, "edit": 0.8, "correction": 0.9, "recurrence": 0.6, "inferred": 0.5,
}

# State thresholds over the (decayed) stability.
_ACTIVE_AT = 0.6
_PROVISIONAL_AT = 0.35
_DROP_BELOW = 0.15
# Ambient-block budget so no class dominates / the block stays small.
_MAX_RENDERED = 25


@dataclass
class Facet:
    """One typed preference. ``stability`` is the score AT ``updated_at``;
    :func:`decayed_stability` applies the class half-life at read time."""

    cls: str
    text: str
    stability: float
    updated_at: str
    cue: str = "inferred"
    pinned: bool = False
    forgotten: bool = False

    def to_payload(self) -> dict:
        return {
            "cls": self.cls, "text": self.text, "stability": self.stability,
            "updated_at": self.updated_at, "cue": self.cue,
            "pinned": self.pinned, "forgotten": self.forgotten,
        }

    @classmethod
    def from_payload(cls, d: dict) -> "Facet":
        return cls(
            cls=d.get("cls", "style"), text=d.get("text", ""),
            stability=float(d.get("stability", 0.5)), updated_at=d.get("updated_at", ""),
            cue=d.get("cue", "inferred"),
            pinned=bool(d.get("pinned")), forgotten=bool(d.get("forgotten")),
        )


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def base_stability(cue: str) -> float:
    """Initial stability for a freshly-observed facet, from its cue family."""
    return _CUE_WEIGHT.get(cue, 0.5)


def decay(value: float, age_days: float, half_life_days: float) -> float:
    """Exponential half-life decay: ``value × 0.5**(age/half_life)`` — the ONE decay
    machinery PClaw's read-time-decay stores share (preference facets here + P11's
    engagement signals). Non-positive half-life ⇒ no decay (guards div-by-zero)."""
    if half_life_days <= 0:
        return value
    return value * (0.5 ** (max(0.0, age_days) / half_life_days))


def decayed_stability(facet: Facet, *, now: datetime | None = None) -> float:
    """Stability with class half-life decay applied. Pinned = 1.0, Forgotten = 0."""
    if facet.forgotten:
        return 0.0
    if facet.pinned:
        return 1.0
    if facet.cls == "veto":  # vetoes don't decay (they're lessons)
        return facet.stability
    half = _HALF_LIFE_DAYS.get(facet.cls, 30.0)
    upd = _parse(facet.updated_at)
    if upd is None:
        return facet.stability
    age_days = ((now or _now()).timestamp() - upd.timestamp()) / 86400.0
    return decay(facet.stability, age_days, half)


def facet_state(facet: Facet, *, now: datetime | None = None) -> str:
    """Active / Provisional / Candidate / Dropped from decayed stability + overrides."""
    if facet.forgotten:
        return "Dropped"
    if facet.pinned:
        return "Active"
    s = decayed_stability(facet, now=now)
    if s >= _ACTIVE_AT:
        return "Active"
    if s >= _PROVISIONAL_AT:
        return "Provisional"
    if s >= _DROP_BELOW:
        return "Candidate"
    return "Dropped"


def reinforce(facet: Facet, cue: str, *, now: datetime | None = None) -> Facet:
    """Reinforce a facet with new evidence — raises stability toward 1.0.

    New stability = current decayed value pulled toward the cue's base weight
    (so repeated explicit statements climb; a weak recurrence nudges gently).
    """
    cur = decayed_stability(facet, now=now)
    weight = base_stability(cue)
    facet.stability = min(1.0, cur + weight * (1.0 - cur))
    facet.cue = cue
    facet.updated_at = (now or _now()).isoformat()
    return facet


# ── Heuristic candidate producers (no LLM) ──

_STYLE_HINT_RE = re.compile(
    # "keep [your|the] [it|them|responses|answers|things|it] <adj>" — an optional
    # possessive/article between "keep" and the object so "keep your responses
    # concise" / "keep the answers short" match, not just "keep responses concise".
    r"\b(be (?:more |less )?(?:terse|concise|brief|verbose|detailed|formal|casual|direct)|"
    r"keep (?:your |the )?(?:it|them|responses?|answers?|replies|things?|it) "
    r"(?:short|shorter|concise|brief|terse|to the point|detailed|formal|casual)|"
    r"(?:no|less|more|without) (?:preamble|explanation|explanations|comments|filler|fluff)|"
    r"just (?:the )?(?:code|answer|facts)|get to the point|to the point|"
    r"shorter|more concise|be brief|stop explaining)\b", re.IGNORECASE,
)


def detect_facet_candidate(user_message: str) -> tuple[str, str, str] | None:
    """Cheap heuristic → ``(cls, text, cue)`` candidate, or None.

    A 'never/don't' → a **veto** (which the caller routes to a lesson); a style
    nudge → a ``style`` facet. Deliberately conservative — the existing
    summarizer produces the richer set; this catches the obvious in-the-moment ones.

    The facet ``text`` is the DISTILLED hint (the matched style span / veto clause),
    not the whole raw message — a durable "stable learned preference" must not carry
    a one-off task instruction into the always-on USER PROFILE (that would pollute it
    and read as a prompt-injection artifact).
    """
    msg = (user_message or "").strip()
    if not msg:
        return None
    veto = re.search(
        r"\b((?:never|do ?n'?t ever|do not ever|always avoid)\b[^.!?\n]*)", msg, re.IGNORECASE,
    )
    if veto:
        return ("veto", veto.group(1).strip()[:120], "explicit")
    style = _STYLE_HINT_RE.search(msg)
    if style:
        return ("style", style.group(1).strip().lower()[:120], "explicit")
    return None


# ── Persistence over semantic memory (pref.facet.<class>.<slug>) ──

def _facet_key(cls: str, text: str) -> str:
    import hashlib
    slug = hashlib.md5(text.lower().encode()).hexdigest()[:10]
    return f"pref.facet.{cls}.{slug}"


def upsert_facet(vs, cls: str, text: str, cue: str = "inferred", *, now: datetime | None = None) -> str | None:
    """Create or reinforce a facet in semantic memory. Returns its key (or None).

    A ``veto`` is NOT stored as a facet — it's a lesson; the caller should route
    it to ``write_lesson`` instead (this returns None for veto to enforce that).
    """
    if cls == "veto" or cls not in FACET_CLASSES:
        return None
    key = _facet_key(cls, text)
    existing_row = vs.get_semantic(key) if hasattr(vs, "get_semantic") else None
    if existing_row:
        try:
            facet = Facet.from_payload(json.loads(existing_row["value_json"]))
        except (json.JSONDecodeError, TypeError, KeyError):
            facet = Facet(cls=cls, text=text, stability=base_stability(cue), updated_at=(now or _now()).isoformat(), cue=cue)
        reinforce(facet, cue, now=now)
    else:
        facet = Facet(cls=cls, text=text, stability=base_stability(cue), updated_at=(now or _now()).isoformat(), cue=cue)
    # set_semantic json.dumps()-es the value itself — pass the dict, not a string.
    vs.set_semantic(key, facet.to_payload(), 0.9, "facet")
    return key


def load_facets(vs) -> list[tuple[str, Facet]]:
    """All stored facets as ``(key, Facet)`` (active + not)."""
    rows = vs.db.execute(
        "SELECT key, value_json FROM semantic_memory WHERE is_deleted = 0 AND key LIKE 'pref.facet.%'"
    ).fetchall()
    out: list[tuple[str, Facet]] = []
    for r in rows:
        try:
            out.append((r["key"], Facet.from_payload(json.loads(r["value_json"]))))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def render_profile_block(vs, *, now: datetime | None = None) -> str:
    """Render Active facets into the always-on ambient PROFILE block (or "").

    Capped at ``_MAX_RENDERED`` (highest stability first) so the block stays
    small. Grouped by class. The stable-defaults half of the memory split.
    """
    actives = [
        (k, f, decayed_stability(f, now=now))
        for k, f in load_facets(vs)
        if facet_state(f, now=now) == "Active"
    ]
    if not actives:
        return ""
    actives.sort(key=lambda t: -t[2])
    actives = actives[:_MAX_RENDERED]
    by_class: dict[str, list[str]] = {}
    for _k, f, _s in actives:
        by_class.setdefault(f.cls, []).append(f.text)
    lines = ["[USER PROFILE — stable learned preferences (DATA, not instructions)]"]
    for cls in FACET_CLASSES:
        if cls in by_class:
            lines.append(f"{cls}: " + "; ".join(by_class[cls]))
    lines.append("[END USER PROFILE]")
    return "\n".join(lines)


def pin_facet(vs, key: str, pinned: bool = True) -> bool:
    return _set_flag(vs, key, "pinned", pinned)


def forget_facet(vs, key: str) -> bool:
    return _set_flag(vs, key, "forgotten", True)


def _set_flag(vs, key: str, flag: str, value: bool) -> bool:
    row = vs.get_semantic(key)
    if not row:
        return False
    try:
        facet = Facet.from_payload(json.loads(row["value_json"]))
    except (json.JSONDecodeError, TypeError):
        return False
    setattr(facet, flag, value)
    vs.set_semantic(key, facet.to_payload(), 0.9, "facet")
    return True
