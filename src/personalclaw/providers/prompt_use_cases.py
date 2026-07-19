"""Prompt use-case truth store — which system prompt serves each runtime context.

Mirror of :mod:`personalclaw.providers.use_cases` (the model store), for prompts.
There is ONE store: ``~/.personalclaw/active_prompts.json``. It maps a use case to
a single prompt reference ``"<provider_name>:<prompt_name>"`` where
``provider_name`` is a registered prompt provider (e.g. ``native``). Every runtime
context that assembles a default system prompt reads its binding from here, so the
Settings → Prompts picker and what the runtime resolves never disagree.

Use cases (the distinct system-prompt contexts):

* ``chat``        — interactive chat sessions (dashboard / channel / CLI).
* ``background``  — unattended runs (cron jobs, heartbeat, campaign workers).
* ``code``        — the Code feature's coder agent.
* ``goal_loop``   — Goal Loop / autonomous goal-engine workers.

A use case with no binding falls back to the bundled default prompt
(``DEFAULT_PROMPT_NAME``), so the system works out-of-box with no configuration.
"""

import json
import logging
from pathlib import Path

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)


# ── Canonical use-case vocabulary ────────────────────────────────────────────
# Derived from the one-source-of-truth bundled-prompt catalog so the vocabulary,
# the default binding per use-case, and what gets seeded never disagree. Every
# shipped prompt (agent system prompts AND internal task prompts) is bindable here.
#
# This is the CORE vocabulary. An app/extension may OWN prompts too (declared in
# its manifest, seeded on enable); those use-cases live in the in-process
# :mod:`personalclaw.apps.prompt_registry`, populated by the app-prompt seeder.
# The resolution helpers below UNION the core catalog with that registry, so an
# app-owned use-case is bindable + resolvable exactly like a core one — while a
# system with no apps installed sees only this catalog (the registry is empty).
from personalclaw.prompt_providers.catalog import BUNDLED_PROMPTS as _CATALOG

# Core (catalog-derived) vocabulary. Kept as a module constant for the common
# import; the UNION (core + app-contributed) is exposed by all_prompt_use_cases().
PROMPT_USE_CASES: tuple[str, ...] = tuple(p.use_case for p in _CATALOG)

DEFAULT_PROMPT_PROVIDER = "native"

# Each CORE use-case ships a tailored bundled prompt (seeded from
# config/prompts/<file>) and is bound to it by default.
BUNDLED_PROMPT_NAME: dict[str, str] = {p.use_case: p.name for p in _CATALOG}
# The ultimate fallback (the chat prompt) for any use-case whose own prompt is missing.
DEFAULT_PROMPT_NAME = BUNDLED_PROMPT_NAME["chat"]


def _app_prompt_use_cases() -> "object":
    """The app-contributed prompt-use-case registry (lazy import — avoids a cycle,
    since the apps layer imports the prompt system). Returns the module."""
    from personalclaw.apps import prompt_registry

    return prompt_registry


def all_prompt_use_cases() -> tuple[str, ...]:
    """Every bindable use-case: the core catalog UNION the app-contributed ones.

    Core order first (display order), then app-contributed in registration order,
    deduped (a core use-case an app also names stays in its core position)."""
    out: list[str] = list(PROMPT_USE_CASES)
    seen = set(out)
    try:
        for uc in _app_prompt_use_cases().use_cases():
            if uc not in seen:
                seen.add(uc)
                out.append(uc)
    except Exception:  # noqa: BLE001 — a registry hiccup must not break resolution
        pass
    return tuple(out)


def valid_prompt_use_cases() -> frozenset[str]:
    """The set of bindable use-cases (core + app-contributed) for validity checks."""
    return frozenset(all_prompt_use_cases())


def bundled_prompt_name_for(use_case: str) -> str | None:
    """The default prompt NAME for ``use_case`` — its core catalog row, else an
    app-contributed prompt's name, else None."""
    name = BUNDLED_PROMPT_NAME.get(use_case)
    if name:
        return name
    try:
        entry = _app_prompt_use_cases().get(use_case)
    except Exception:  # noqa: BLE001
        return None
    return entry.prompt_name if entry else None


def _active_prompts_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / "active_prompts.json"


def load_active_prompts() -> dict[str, str]:
    """The use-case → prompt-ref bindings. Empty dict when unset/unreadable."""
    path = _active_prompts_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Keep only known use-cases (core + app-contributed) mapping to string refs.
    valid = valid_prompt_use_cases()
    return {
        k: v
        for k, v in data.items()
        if k in valid and isinstance(v, str) and v
    }


def save_active_prompts(active: dict[str, str]) -> None:
    path = _active_prompts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    valid = valid_prompt_use_cases()
    cleaned = {
        k: v
        for k, v in active.items()
        if k in valid and isinstance(v, str) and v
    }
    atomic_write(path, json.dumps(cleaned, indent=2) + "\n")


def active_prompt_ref(use_case: str) -> str:
    """The bound prompt ref for ``use_case``, or its bundled default when unbound.

    Returns ``"<provider>:<prompt_name>"``. A known use-case (core OR app-owned)
    falls back to its own tailored bundled prompt; an unknown one falls back to
    the chat prompt.
    """
    if use_case in valid_prompt_use_cases():
        ref = load_active_prompts().get(use_case)
        if ref:
            return ref
        name = bundled_prompt_name_for(use_case) or DEFAULT_PROMPT_NAME
        return f"{DEFAULT_PROMPT_PROVIDER}:{name}"
    return f"{DEFAULT_PROMPT_PROVIDER}:{DEFAULT_PROMPT_NAME}"


def split_ref(ref: str) -> tuple[str, str] | None:
    """Parse a ``"<provider_name>:<prompt_name>"`` ref. None if unqualified."""
    if ":" not in ref:
        return None
    provider_name, prompt_name = ref.split(":", 1)
    return (provider_name, prompt_name)


def resolve_prompt_content(use_case: str) -> str | None:
    """Resolve the bound prompt for ``use_case`` to its rendered content.

    Reads the binding (or the bundled default), fetches the template from its
    provider, and returns its ``content``. Returns ``None`` when the prompt
    can't be resolved (no provider, missing template) so the caller can fall
    back to the shipped file.
    """
    try:
        from personalclaw.prompt_providers.registry import (
            _ensure_default_providers_registered,
            get_prompt_provider,
        )

        # Seed first (core + always-on bundled-app prompts) so an app-owned
        # use-case is registered before its binding is resolved.
        _ensure_default_providers_registered()
        ref = active_prompt_ref(use_case)
        parsed = split_ref(ref)
        if not parsed:
            return None
        provider_name, prompt_name = parsed
        provider = get_prompt_provider(provider_name)
        if provider is None:
            return None
        template = provider.get_prompt(prompt_name)
        if template is None:
            # The bound/own prompt is missing — fall back to the chat prompt so a
            # use-case is never left with no system prompt.
            fallback = get_prompt_provider(DEFAULT_PROMPT_PROVIDER)
            template = fallback.get_prompt(DEFAULT_PROMPT_NAME) if fallback else None
            if template is None:
                return None
        return template.content
    except Exception:
        logger.debug("resolve_prompt_content failed for %s", use_case, exc_info=True)
        return None
