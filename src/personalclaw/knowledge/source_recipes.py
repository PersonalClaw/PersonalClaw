"""Source recipes — the "is your site already covered?" directory (WATCHED-SOURCES §7.2).

Recipes are **data, not code**: bundled JSON files under ``knowledge/sources/recipes/``, each
naming a site shape, the provider that polls it, the spec to start from, and how to recognise
a URL that fits. This is the html2rss feed-directory workflow: a user pastes a URL, and before
anyone tunes a selector we check whether somebody already worked this site out.

**Why a recipe is not a preset.** ``feed_source.PRESETS`` answers "how do I parse THIS shape of
endpoint" (a JSON Feed, an Algolia response) and is keyed by format. A recipe answers "what
should I do with THIS URL a human pasted" and is keyed by a URL pattern with capture groups —
so ``https://github.com/astral-sh/uv`` becomes a concrete releases-Atom spec without the user
knowing GitHub publishes one. A recipe's ``spec`` may well *name* a preset; the two compose.

**The capture groups are the whole mechanism.** A recipe's ``matchPatterns`` are regexes with
NAMED groups, and ``{{group}}`` in any string in ``spec`` is substituted from the match. A
recipe that cannot fill every placeholder is REFUSED rather than resolved with a blank — a spec
with a hole in its URL is a fetch of the wrong thing, which is worse than no suggestion.

**A bundled recipe is only shipped if its own provider accepts it.**
``test_connector_pack.py`` resolves every bundled recipe against a sample URL and puts the
result through the owning provider's ``validate_spec``. A recipe the provider would refuse at
save time is a broken recipe, and shipping one turns the create flow's most helpful moment into
its most confusing.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Providers a recipe may target, mapped to the ``kind`` its WatchedSource row carries.
#: A closed map: a recipe naming a provider nothing registered would be a create-flow
#: suggestion that fails on save, so an unknown provider is a validation error rather than a
#: row the user discovers is dead.
RECIPE_PROVIDERS: dict[str, str] = {
    "watched-feed": "feed",
    "watched-page": "web",
}

#: A ``{{group}}`` reference inside a recipe's spec.
_PLACEHOLDER_RE = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")

#: Keys a recipe file may carry. Closed so a typo (``matchPattern`` for ``matchPatterns``)
#: is a loud validation error rather than a recipe that silently matches nothing.
RECIPE_KEYS = frozenset(
    {
        "id",
        "displayName",
        "description",
        "provider",
        "itemType",
        "enrichment",
        "matchPatterns",
        "urlGuidance",
        "spec",
        "tags",
    }
)


@dataclass
class SourceRecipe:
    """One bundled recipe (§7.2)."""

    id: str
    display_name: str
    description: str = ""
    provider: str = ""
    item_type: str = "bookmark"
    enrichment: str = ""
    match_patterns: list[str] = field(default_factory=list)
    url_guidance: str = ""
    spec: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        """The WatchedSource ``kind`` this recipe creates, derived from its provider."""
        return RECIPE_PROVIDERS.get(self.provider, "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "displayName": self.display_name,
            "description": self.description,
            "provider": self.provider,
            "kind": self.kind,
            "itemType": self.item_type,
            "enrichment": self.enrichment,
            "matchPatterns": list(self.match_patterns),
            "urlGuidance": self.url_guidance,
            "spec": dict(self.spec),
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SourceRecipe":
        return cls(
            id=str(raw.get("id", "") or ""),
            display_name=str(raw.get("displayName", "") or ""),
            description=str(raw.get("description", "") or ""),
            provider=str(raw.get("provider", "") or ""),
            item_type=str(raw.get("itemType", "bookmark") or "bookmark"),
            enrichment=str(raw.get("enrichment", "") or ""),
            match_patterns=[str(p) for p in (raw.get("matchPatterns") or []) if str(p).strip()],
            url_guidance=str(raw.get("urlGuidance", "") or ""),
            spec=dict(raw["spec"]) if isinstance(raw.get("spec"), dict) else {},
            tags=[str(t) for t in (raw.get("tags") or []) if str(t).strip()],
        )


@dataclass
class RecipeMatch:
    """A recipe that fits a pasted URL, with its spec already resolved."""

    recipe: SourceRecipe
    spec: dict[str, Any]
    groups: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.recipe.id,
            "displayName": self.recipe.display_name,
            "description": self.recipe.description,
            "provider": self.recipe.provider,
            "kind": self.recipe.kind,
            "itemType": self.recipe.item_type,
            "enrichment": self.recipe.enrichment,
            "spec": dict(self.spec),
            "groups": dict(self.groups),
        }


def recipes_dir() -> Path:
    """The bundled recipe directory. Shipped data, so it lives beside the code that reads it
    (and is listed in ``pyproject.toml``'s ``package-data`` — a wheel without that line ships
    an empty directory, which is why the count assertion in the tests is load-bearing)."""
    return Path(__file__).resolve().parent / "sources" / "recipes"


def validate_recipe(raw: dict[str, Any], *, where: str = "recipe") -> list[str]:
    """Errors in one recipe document (empty means valid). Data validation, no code run."""
    errors: list[str] = []
    if not isinstance(raw, dict):
        return [f"{where} must be a JSON object"]
    unknown = sorted(set(raw) - RECIPE_KEYS)
    if unknown:
        errors.append(f"{where} has unknown key(s) {unknown}")
    rid = str(raw.get("id", "") or "")
    if not rid:
        errors.append(f"{where} missing required field: id")
    elif not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", rid):
        errors.append(f"{where} id must be kebab-case, got {rid!r}")
    if not str(raw.get("displayName", "") or ""):
        errors.append(f"{where} missing required field: displayName")
    provider = str(raw.get("provider", "") or "")
    if provider not in RECIPE_PROVIDERS:
        errors.append(
            f"{where} provider must be one of {sorted(RECIPE_PROVIDERS)}, got {provider!r}"
        )
    spec = raw.get("spec")
    if not isinstance(spec, dict) or not spec:
        errors.append(f"{where} missing required field: spec")
    patterns = raw.get("matchPatterns") or []
    if not isinstance(patterns, list) or not patterns:
        errors.append(f"{where} missing required field: matchPatterns")
        patterns = []
    declared: set[str] = set()
    for pattern in patterns:
        try:
            declared |= set(re.compile(str(pattern)).groupindex)
        except re.error as exc:
            errors.append(f"{where} matchPatterns entry is not a valid regex: {exc}")
    if isinstance(spec, dict):
        needed = _placeholders_in(spec)
        missing = sorted(needed - declared)
        if missing:
            errors.append(
                f"{where} spec references {missing} but no matchPatterns capture group "
                f"provides them (declared: {sorted(declared) or 'none'})"
            )
    enrichment = str(raw.get("enrichment", "") or "")
    if enrichment and enrichment not in ("full", "raw"):
        errors.append(f"{where} enrichment must be 'full' or 'raw', got {enrichment!r}")
    return errors


def _placeholders_in(value: Any) -> set[str]:
    """Every ``{{group}}`` name anywhere inside a spec (strings, lists, nested objects)."""
    if isinstance(value, str):
        return set(_PLACEHOLDER_RE.findall(value))
    if isinstance(value, dict):
        out: set[str] = set()
        for item in value.values():
            out |= _placeholders_in(item)
        return out
    if isinstance(value, list):
        out = set()
        for item in value:
            out |= _placeholders_in(item)
        return out
    return set()


def list_recipes() -> list[SourceRecipe]:
    """Every bundled recipe, id-sorted. An invalid file is SKIPPED with a warning.

    Skipping rather than raising is the right failure here and it is not the usual fail-open:
    a recipe is a *suggestion*, so one bad file must not take the create flow's whole
    directory down with it — and the repo's own test refuses to ship an invalid one, so a
    skip in production means a hand-edited install, not a shipped defect.
    """
    out: list[SourceRecipe] = []
    directory = recipes_dir()
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("source recipe %s is unreadable; skipping", path.name)
            continue
        errors = validate_recipe(raw, where=path.name)
        if errors:
            logger.warning("source recipe %s is invalid (%s); skipping", path.name, errors[0])
            continue
        out.append(SourceRecipe.from_dict(raw))
    return sorted(out, key=lambda r: r.id)


def get_recipe(recipe_id: str) -> SourceRecipe | None:
    """The bundled recipe with this id, or None."""
    for recipe in list_recipes():
        if recipe.id == recipe_id:
            return recipe
    return None


def resolve_spec(recipe: SourceRecipe, groups: dict[str, str]) -> dict[str, Any]:
    """The recipe's spec with ``{{group}}`` substituted. Raises ``KeyError`` on a hole.

    Refusing an unfilled placeholder is the point: a half-substituted URL is a request to a
    URL nobody meant, and a recipe is supposed to make the create flow MORE certain.
    """

    def _sub(value: Any) -> Any:
        if isinstance(value, str):

            def _one(match: re.Match[str]) -> str:
                name = match.group(1)
                if name not in groups or not groups[name]:
                    raise KeyError(name)
                return groups[name]

            return _PLACEHOLDER_RE.sub(_one, value)
        if isinstance(value, dict):
            return {k: _sub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_sub(v) for v in value]
        return value

    return dict(_sub(dict(recipe.spec)))


def recipes_for_url(url: str) -> list[RecipeMatch]:
    """Every recipe whose pattern matches ``url``, spec already resolved (§7.2).

    This is the "check if your site is already covered" answer. Order is recipe-id sorted
    (stable and explainable) rather than a relevance score — with a handful of bundled
    recipes a ranking would be a guess dressed as a judgement.
    """
    url = (url or "").strip()
    if not url:
        return []
    out: list[RecipeMatch] = []
    for recipe in list_recipes():
        for pattern in recipe.match_patterns:
            try:
                match = re.match(pattern, url, re.IGNORECASE)
            except re.error:
                continue
            if match is None:
                continue
            groups = {k: str(v) for k, v in (match.groupdict() or {}).items() if v}
            try:
                spec = resolve_spec(recipe, groups)
            except KeyError as exc:
                logger.debug("recipe %s matched %s but %s was empty", recipe.id, url, exc)
                continue
            out.append(RecipeMatch(recipe=recipe, spec=spec, groups=groups))
            break
    return out
