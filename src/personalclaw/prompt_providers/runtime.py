"""Runtime rendering helpers — the one way call sites turn a bound prompt into
final text.

Every migrated call site that used to build a hardcoded f-string now calls
:func:`render_use_case_prompt` (for a standalone LLM task prompt bound to a
use-case) or :func:`render_snippet_block` (for an injected instruction fragment).
Both resolve through the registered prompt provider and render through the engine
— snippet ``{{> name}}`` includes and ``{{var}}`` substitutions are applied
exactly as in the Settings preview, so what the model receives can never drift
from what an author sees.

These wrap the lower-level ``engine.render`` so no consumer re-implements the
provider lookup + snippet-resolver + ``PromptTemplate`` assembly.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from personalclaw.prompt_providers.base import PromptSnippet

logger = logging.getLogger(__name__)


def snippet_resolver() -> Callable[[str], "PromptSnippet | None"]:
    """A ``name -> PromptSnippet | None`` resolver for the engine's ``{{> name}}``
    includes, backed by the default prompt provider. Yields None for every name
    when no provider is available (the engine then renders an explicit
    ``[missing snippet: name]`` marker rather than failing)."""
    try:
        from personalclaw.prompt_providers.registry import (
            _ensure_default_providers_registered,
            get_default_provider,
        )

        _ensure_default_providers_registered()
        provider = get_default_provider()
    except Exception:
        provider = None
    return lambda n: provider.get_snippet(n) if provider is not None else None


def render_use_case_prompt(use_case: str, values: dict[str, Any] | None = None) -> str | None:
    """Resolve the prompt bound to ``use_case`` and render it with ``values``.

    Reads the binding (Settings → Prompts) or the bundled default, fetches the
    template from its provider, and renders it through the engine with snippet
    includes resolved. Returns the rendered string, or ``None`` when the prompt
    can't be resolved (no provider / missing template) so the caller can fall
    back to a shipped default.
    """
    from personalclaw.prompt_providers.engine import render_template
    from personalclaw.providers.prompt_use_cases import (
        DEFAULT_PROMPT_NAME,
        DEFAULT_PROMPT_PROVIDER,
        active_prompt_ref,
        split_ref,
    )

    try:
        from personalclaw.prompt_providers.registry import (
            _ensure_default_providers_registered,
            get_prompt_provider,
        )

        # Register + seed FIRST (core + always-on bundled-app prompts) so an
        # app-owned use-case is in the vocabulary before we resolve its binding —
        # otherwise it'd look unknown and fall back to the chat prompt.
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
            # Bound/own prompt missing — fall back to the chat prompt so a
            # use-case is never left with nothing.
            fallback = get_prompt_provider(DEFAULT_PROMPT_PROVIDER)
            template = fallback.get_prompt(DEFAULT_PROMPT_NAME) if fallback else None
            if template is None:
                return None
        return render_template(template, values or {}, resolver=(lambda n: provider.get_snippet(n)))
    except Exception:
        logger.debug("render_use_case_prompt failed for %r", use_case, exc_info=True)
        return None


def render_snippet_block(name: str, values: dict[str, Any] | None = None) -> str:
    """Render a bundled instruction snippet by name, with ``values`` substituted.

    Used for injected instruction fragments (critical rules, workspace identity,
    widget block, …) that are composed into the session context rather than bound
    to a use-case. Returns ``""`` when the snippet can't be resolved so a missing
    fragment degrades to nothing instead of breaking context assembly.
    """
    from personalclaw.prompt_providers.engine import render_snippet as _render_snippet

    try:
        from personalclaw.prompt_providers.registry import (
            _ensure_default_providers_registered,
            get_default_provider,
        )

        _ensure_default_providers_registered()
        provider = get_default_provider()
        if provider is None:
            return ""
        snip = provider.get_snippet(name)
        if snip is None:
            return ""
        return _render_snippet(snip, values or {}, resolver=(lambda n: provider.get_snippet(n)))
    except Exception:
        logger.debug("render_snippet_block failed for %r", name, exc_info=True)
        return ""
