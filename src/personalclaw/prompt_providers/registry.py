"""In-process registry of prompt providers."""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.prompt_providers.base import PromptProvider

logger = logging.getLogger(__name__)

_providers: "dict[str, PromptProvider]" = {}


def register_prompt_provider(provider: "PromptProvider") -> None:
    _providers[provider.name] = provider


def get_prompt_provider(name: str) -> "PromptProvider | None":
    return _providers.get(name)


def list_prompt_providers() -> list[str]:
    return list(_providers.keys())


def get_default_provider() -> "PromptProvider | None":
    """Return the native provider when registered, otherwise the first
    provider that registered. Used by /api/prompts and the @prompt expander
    when no provider qualifier is given.
    """
    if "native" in _providers:
        return _providers["native"]
    return next(iter(_providers.values()), None)


def _ensure_default_providers_registered() -> None:
    """Idempotent registration of the bundled native filesystem provider +
    seeding of the default system prompt so it is bindable in Settings."""
    if "native" not in _providers:
        from personalclaw.prompt_providers.native_provider import NativePromptProvider

        register_prompt_provider(NativePromptProvider())
    try:
        from personalclaw.prompt_providers.native_provider import (
            seed_bundled_app_prompts,
            seed_bundled_system_prompts,
        )

        seed_bundled_system_prompts()
        # Also seed prompts OWNED by always-on bundled provider apps (knowledge,
        # web-tools, …) so their use-cases resolve even without a full app-provider
        # discovery pass — they're part of the shipped baseline.
        seed_bundled_app_prompts()
    except Exception:
        logger.debug("bundled system-prompt seed failed", exc_info=True)
