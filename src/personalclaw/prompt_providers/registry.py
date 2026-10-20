"""In-process registry of prompt providers."""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.prompt_providers.base import PromptProvider

logger = logging.getLogger(__name__)

_providers: "dict[str, PromptProvider]" = {}


@dataclass(frozen=True)
class _Seed:
    """What the last seed of the bundled prompts left, which the next lookup compares against."""

    #: The prompt store's folders, and the folder of the bundled apps whose prompts it seeded.
    where: tuple[Path, Path, Path]
    #: The files those store folders held after it.
    files: frozenset[str]
    #: The app use-cases registered after it.
    use_cases: frozenset[str]


_last_seed: _Seed | None = None


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
    """Idempotent registration of the bundled native filesystem provider, and seeding of the
    bundled prompts so each is bindable in Settings.

    The store is seeded once, and again only when something the last seed left is gone: a file
    of the store deleted, the app use-cases it registered forgotten, another home or another
    folder of bundled apps. Every prompt lookup asks for this, ten of them to a turn, and a seed
    reads and parses every bundled snippet's stored copy, every bundled app's manifest and every
    prompt those apps ship: run each time, it was most of the time a turn took to reach its
    model, on the event loop that answers every request.
    """
    global _last_seed
    if "native" not in _providers:
        from personalclaw.prompt_providers.native_provider import NativePromptProvider

        register_prompt_provider(NativePromptProvider())
    # Opt-out for tests that assert on a clean, user-only prompt store (the seeders read it too).
    if os.environ.get("PERSONALCLAW_SKIP_PROMPT_SEED"):
        return
    try:
        from personalclaw.apps import prompt_registry
        from personalclaw.prompt_providers.native_provider import (
            seed_bundled_app_prompts,
            seed_bundled_system_prompts,
            store_folders,
        )
        from personalclaw.providers.loader import BUNDLED_DIR

        where = (*store_folders(), BUNDLED_DIR)
        last = _last_seed
        if (
            last is not None
            and last.where == where
            and last.files <= _files_in(where[:2])
            and last.use_cases <= set(prompt_registry.use_cases())
        ):
            return
        seed_bundled_system_prompts()
        # Also seed prompts OWNED by always-on bundled provider apps (knowledge,
        # web-tools, …) so their use-cases resolve even without a full app-provider
        # discovery pass — they're part of the shipped baseline.
        seed_bundled_app_prompts()
        _last_seed = _Seed(
            where=where,
            files=_files_in(where[:2]),
            use_cases=frozenset(prompt_registry.use_cases()),
        )
    except Exception:
        logger.debug("bundled system-prompt seed failed", exc_info=True)


def _files_in(folders: tuple[Path, ...]) -> frozenset[str]:
    """Every file *folders* hold, as ``<folder>/<name>``; a folder that is missing holds none."""
    names: set[str] = set()
    for folder in folders:
        try:
            names.update(f"{folder.name}/{name}" for name in os.listdir(folder))
        except OSError:
            continue
    return frozenset(names)
