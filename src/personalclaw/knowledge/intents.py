"""Intent-driven ingestion — the Tier-3 layer over the node-graph engine.

Tier 1 (router = item-type → graph) and Tier 2 (processors = nodes) live in the
pipeline package. This is **Tier 3**: user-defined *intents* expressed in plain
natural language — "anything that could improve my homelab", "hints on how I should
invest". The user states a goal; the LLM does the rest: for each ingested item it
decides whether the item is relevant to the goal and, if so, derives a small set of
**typed fields** (so the UI can render them dynamically instead of dumping raw JSON)
plus a short free-form takeaway.

Matches are persisted as *outcomes* in a dedicated sqlite table, BY VALUE — the
takeaway, the typed fields, and a denormalized item title are copied in, with only a
soft back-reference to the source item. Deleting the item (or disconnecting an
external provider) severs the back-ref but never loses the gathered insight.

An intent may set ``propose_skill`` — when its matches repeatedly succeed, the system
can propose a reusable skill (the proposal surfaces; it never auto-creates).

Intents are persisted as JSON beside the knowledge DB (filesystem-as-truth). Matching
reuses the existing knowledge LLM pool — no new model wiring.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)

_INTENT_FILE = "intents.json"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")


def slugify_goal(goal: str) -> str:
    """Derive a stable intent id from its goal — the user never types one (vision).
    Lowercase, non-alphanumerics → hyphens, trimmed, capped at 48 chars to satisfy
    ``_ID_RE``. Empty/symbol-only goals fall back to ``intent``."""
    slug = re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-")[:48].strip("-")
    return slug or "intent"


# Standard field types the matcher may emit; the UI renders each type-aware.
FIELD_TYPES = ("string", "number", "boolean", "date", "url", "tags")


@dataclass
class Intent:
    """A user-defined, natural-language extraction intent (Tier 3).

    ``goal`` is the single thing the user writes — a plain-language statement of what
    they want tracked. There is no field schema to author; the matcher infers the
    typed fields per item.
    """

    id: str
    goal: str = ""  # natural-language statement of what to track
    enabled: bool = True
    enabled_for: list[str] = field(default_factory=list)  # item types; [] = all types
    propose_skill: bool = False

    def applies_to(self, item_type: str) -> bool:
        return self.enabled and (not self.enabled_for or item_type in self.enabled_for)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal": self.goal,
            "enabled": self.enabled,
            "enabled_for": list(self.enabled_for),
            "propose_skill": self.propose_skill,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Intent":
        # ``goal`` is the field; accept legacy ``description`` as a fallback source so a
        # pre-existing intents.json still loads with a sensible goal.
        goal = str(d.get("goal") or d.get("description") or "")
        # The id is derived from the goal when the caller doesn't supply one — the
        # backend owns slug derivation so every caller (UI, agent, direct API) behaves
        # identically and the user never authors an id.
        intent_id = str(d.get("id") or "").strip() or slugify_goal(goal)
        return cls(
            id=intent_id,
            goal=goal,
            enabled=bool(d.get("enabled", True)),
            enabled_for=[str(t) for t in d.get("enabled_for", []) if isinstance(t, str)],
            propose_skill=bool(d.get("propose_skill")),
        )


@dataclass
class IntentMatch:
    """One intent's verdict on one item — relevance, typed fields, and a takeaway."""

    intent_id: str
    relevant: bool
    takeaway: str = ""
    fields: list[dict] = field(default_factory=list)  # [{name, type, value}]

    def to_dict(self) -> dict:
        return {
            "intent_id": self.intent_id,
            "relevant": self.relevant,
            "takeaway": self.takeaway,
            "fields": list(self.fields),
        }


def _redact(text: str) -> str:
    if not text:
        return ""
    text, _ = redact_exfiltration_urls(text)
    text, _ = redact_credentials(text)
    return text


class IntentStore:
    """Persisted intent set (``<dir>/intents.json``, beside the knowledge DB)."""

    def __init__(self, path: Path):
        self._path = path

    def load(self) -> list[Intent]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(raw, list):
            return []
        out: list[Intent] = []
        for d in raw:
            if isinstance(d, dict) and d.get("id"):
                out.append(Intent.from_dict(d))
        return out

    def get(self, intent_id: str) -> Intent | None:
        for i in self.load():
            if i.id == intent_id:
                return i
        return None

    def save(self, intents: list[Intent]) -> None:
        atomic_write(self._path, json.dumps([i.to_dict() for i in intents], indent=2))

    def upsert(self, intent: Intent) -> None:
        if not _ID_RE.match(intent.id):
            raise ValueError(
                f"invalid intent id {intent.id!r} (lowercase/digits/hyphen, ≤49 chars)"
            )
        intents = [i for i in self.load() if i.id != intent.id]
        intents.append(intent)
        self.save(intents)

    def delete(self, intent_id: str) -> bool:
        intents = self.load()
        kept = [i for i in intents if i.id != intent_id]
        if len(kept) == len(intents):
            return False
        self.save(kept)
        return True


def build_match_prompt(intent: Intent, content: str, *, max_chars: int = 12000) -> str:
    """Render the relevance+extraction prompt for one NL intent against content.

    The instruction lives in the prompt system as the native-knowledge app's
    ``knowledge_intent_match`` prompt (bindable in Settings → Prompts), seeded by
    the app on enable. Rendered with the intent goal, allowed field types, and the
    capped content."""
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    return (
        render_use_case_prompt(
            "knowledge_intent_match",
            {
                "goal": intent.goal,
                "field_types": ", ".join(FIELD_TYPES),
                "content": content[:max_chars],
            },
        )
        or ""
    )


def _coerce_fields(raw) -> list[dict]:
    """Validate/normalize the model's field list into [{name, type, value}]."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for f in raw:
        if not isinstance(f, dict):
            continue
        name = str(f.get("name") or "").strip()
        if not name:
            continue
        ftype = str(f.get("type") or "string").strip().lower()
        if ftype not in FIELD_TYPES:
            ftype = "string"
        value = f.get("value")
        if ftype == "tags":
            value = [_redact(str(v)) for v in value] if isinstance(value, list) else []
        elif ftype in ("string", "date", "url"):
            value = _redact(str(value)) if value is not None else ""
        # number/boolean pass through as-is (parsed JSON scalars)
        out.append({"name": name[:80], "type": ftype, "value": value})
        if len(out) >= 6:
            break
    return out


async def match_intent(
    intent: Intent, content: str, *, pool=None, raise_on_error: bool = False
) -> IntentMatch | None:
    """Run one intent against *content*. Returns a relevant IntentMatch or None.

    By default an LLM error (cold/unavailable pool, timeout) is swallowed to None —
    the ingest-time path wants graceful per-item degradation. A retroactive run sets
    ``raise_on_error=True`` so it can tell "model couldn't evaluate" apart from a
    genuine not-relevant verdict and report that honestly instead of a silent 0-match.
    """
    if not pool or not (content or "").strip() or not intent.goal.strip():
        return None
    try:
        resp = await pool.send(build_match_prompt(intent, content), timeout=180.0)
        parsed = _parse_json(resp)
    except Exception:
        logger.debug("intent %s match failed", intent.id, exc_info=True)
        if raise_on_error:
            raise
        return None
    if not isinstance(parsed, dict) or not parsed.get("relevant"):
        return None
    return IntentMatch(
        intent_id=intent.id,
        relevant=True,
        takeaway=_redact(str(parsed.get("takeaway") or "")),
        fields=_coerce_fields(parsed.get("fields")),
    )


async def run_intents(
    intents: list[Intent],
    item_type: str,
    content: str,
    *,
    pool=None,
) -> list[IntentMatch]:
    """Run every applicable intent against *content*. Returns the relevant matches.

    Intents that don't apply, or whose content isn't relevant, are omitted. No LLM
    pool → empty list (graceful).
    """
    if not pool or not (content or "").strip():
        return []
    applicable = [i for i in intents if i.applies_to(item_type)]
    if not applicable:
        return []
    # Applicable intents are independent → match them concurrently rather than N
    # sequential LLM calls (the intent stage is the per-item cost that grows with the
    # number of intents). The pool applies its own backpressure.
    import asyncio

    results = await asyncio.gather(
        *(match_intent(i, content, pool=pool) for i in applicable),
        return_exceptions=True,
    )
    return [m for m in results if isinstance(m, IntentMatch)]


def _parse_json(response: str) -> object:
    for text in (response, _code_block(response)):
        if text:
            try:
                return json.loads(text)
            except (json.JSONDecodeError, ValueError):
                pass
    m = re.search(r"\{[\s\S]*\}", response or "")
    if m:
        try:
            return json.loads(m.group())
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def _code_block(response: str) -> str | None:
    m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", response or "")
    return m.group(1) if m else None
