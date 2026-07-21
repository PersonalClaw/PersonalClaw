"""Runtime registry of app-contributed prompt use-cases.

An app/extension can OWN the prompts it uses (declared in its manifest's
``prompts`` list and seeded into the native prompt store on enable). For such a
prompt to be *bindable* and *resolvable*, its use-case must join the core
use-case vocabulary that :mod:`personalclaw.providers.prompt_use_cases` exposes.

We can't union from the catalog there: an app's prompts only exist once its
manifest is read, and ``prompt_use_cases`` is on the prompt-resolution hot path
(importing the apps layer from it would be a cycle and a per-call filesystem
scan). So the app-prompt SEEDER registers each app prompt's use-case HERE, and
``prompt_use_cases`` reads this in-process registry and unions it with the core
catalog. The registry is repopulated every startup (the bundled-provider
discovery path + ``enable`` re-seed) and on each ``enable``; ``disable`` removes
the app's entries.

Entries are keyed by use-case. Re-registering the same use-case overwrites (an
app re-enabled, or a manifest changed). Robust when empty — with no apps
installed this contributes nothing and the core catalog stands alone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppPromptUseCase:
    """One app-owned prompt's bindable use-case → its store identity.

    ``provider`` is the prompt provider the prompt lives in (always ``native``
    today — apps seed into the native store), ``prompt_name`` the stored name,
    ``category`` the Settings-UI grouping (see :mod:`catalog`)."""

    use_case: str
    provider: str
    prompt_name: str
    category: str
    app: str  # owning app name (for removal + diagnostics)


# use_case → entry. In-process; rebuilt at startup + on enable/disable.
_REGISTRY: dict[str, AppPromptUseCase] = {}


def register_use_case(
    use_case: str,
    *,
    provider: str,
    prompt_name: str,
    category: str = "internal",
    app: str = "",
) -> None:
    """Record (or overwrite) one app-owned prompt use-case binding."""
    if not use_case or not prompt_name:
        return
    _REGISTRY[use_case] = AppPromptUseCase(
        use_case=use_case,
        provider=provider,
        prompt_name=prompt_name,
        category=category,
        app=app,
    )


def unregister_app(app: str) -> None:
    """Drop every use-case contributed by ``app`` (on disable/uninstall)."""
    if not app:
        return
    for uc in [uc for uc, e in _REGISTRY.items() if e.app == app]:
        _REGISTRY.pop(uc, None)


def clear() -> None:
    """Forget all app-contributed use-cases (test isolation / full re-scan)."""
    _REGISTRY.clear()


def use_cases() -> tuple[str, ...]:
    """Every app-contributed use-case, in registration order."""
    return tuple(_REGISTRY.keys())


def get(use_case: str) -> AppPromptUseCase | None:
    return _REGISTRY.get(use_case)


def default_prompt_names() -> dict[str, str]:
    """``use_case -> prompt_name`` for app-contributed prompts (the default binding)."""
    return {uc: e.prompt_name for uc, e in _REGISTRY.items()}


def category_for(use_case: str) -> str | None:
    e = _REGISTRY.get(use_case)
    return e.category if e else None
