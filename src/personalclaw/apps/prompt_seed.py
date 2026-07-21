"""App-owned prompt seeding — an app ships and OWNS its prompts/snippets.

An app declares the prompt/snippet definition files it ships in its manifest's
``prompts`` list (paths relative to the app dir). Each file is a YAML with the
same on-disk shape as a bundled prompt/snippet, PLUS a top-level ``_entity``
discriminator (``prompt`` | ``snippet``) and — for a prompt — a ``use_case``.

On ``enable`` (and on the always-on bundled-provider discovery path at startup),
:func:`seed_app_prompts` writes each declared definition into the native prompt
store, idempotent + non-clobbering (an existing file — possibly user-edited — is
left untouched), exactly like core's ``seed_bundled_system_prompts``. It also
registers each prompt's use-case into :mod:`personalclaw.apps.prompt_registry`
so the use-case becomes bindable + resolvable (the core catalog UNIONs it).

On ``disable``/uninstall, :func:`remove_app_prompts` deletes only the prompt
files this app shipped (never a user's own prompt) and unregisters its
use-cases. Removal is keyed by the app's own definition files, so it never
touches a name the app didn't ship.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ENTITY_PROMPT = "prompt"
_ENTITY_SNIPPET = "snippet"


def _read_definition(app_dir: Path, rel_path: str) -> dict[str, Any] | None:
    """Read + parse one app prompt/snippet definition YAML. None on any error.

    Guards path traversal (the manifest validator also rejects ``..``, but a
    direct caller might not have validated) — the resolved path must stay inside
    the app dir."""
    from personalclaw.prompt_providers.native_provider import _yaml_loads

    try:
        target = (app_dir / rel_path).resolve()
        if not str(target).startswith(str(app_dir.resolve())):
            logger.warning("app prompt path escapes app dir: %r", rel_path)
            return None
        raw = _yaml_loads(target.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("could not read app prompt definition %r", rel_path, exc_info=True)
        return None
    if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
        return None
    return raw


def _entity_of(raw: dict[str, Any]) -> str:
    """The declared entity kind, defaulting to ``prompt`` (the common case)."""
    return str(raw.get("_entity") or _ENTITY_PROMPT).strip().lower()


def seed_app_prompts(manifest, app_dir: str | Path) -> None:
    """Seed an app's declared prompts/snippets into the native store + register
    their use-cases. Idempotent + non-clobbering (existing files untouched).

    ``manifest`` is an :class:`~personalclaw.apps.manifest.AppManifest`."""
    prompts = list(getattr(manifest, "prompts", None) or [])
    if not prompts:
        return
    # Honour the same opt-out core seeding uses, so tests that assert on a clean
    # user-only store aren't polluted by an app's prompts either.
    if os.environ.get("PERSONALCLAW_SKIP_PROMPT_SEED"):
        return

    from personalclaw.prompt_providers.base import PromptSnippet, PromptTemplate
    from personalclaw.prompt_providers.native_provider import (
        _prompt_path,
        _prompt_payload,
        _snippet_path,
        _snippet_payload,
        _yaml_dumps,
    )

    base = Path(app_dir)
    app_name = getattr(manifest, "name", "") or ""
    for rel in prompts:
        raw = _read_definition(base, str(rel))
        if raw is None:
            continue
        name = str(raw["name"]).strip()
        entity = _entity_of(raw)
        try:
            if entity == _ENTITY_SNIPPET:
                path = _snippet_path(name)
                if not path.exists():
                    snip = PromptSnippet.from_dict(raw)
                    path.write_text(_yaml_dumps(_snippet_payload(snip)), encoding="utf-8")
                    logger.info("Seeded app %r snippet %r", app_name, name)
                continue
            # entity == prompt
            path = _prompt_path(name)
            if not path.exists():
                tpl = PromptTemplate.from_dict(raw)
                path.write_text(_yaml_dumps(_prompt_payload(tpl)), encoding="utf-8")
                logger.info("Seeded app %r prompt %r", app_name, name)
            # Register the use-case regardless of whether we wrote the file this
            # run — a prior run (or a user edit) may already own the file, but the
            # in-process use-case registry is rebuilt every startup and must list
            # this app-owned use-case so it stays bindable + resolvable.
            use_case = str(raw.get("use_case") or "").strip()
            if use_case:
                from personalclaw.apps import prompt_registry

                prompt_registry.register_use_case(
                    use_case,
                    provider="native",
                    prompt_name=name,
                    category=str(raw.get("category") or "internal"),
                    app=app_name,
                )
        except (ValueError, OSError):
            logger.debug("failed to seed app %r %s %r", app_name, entity, name, exc_info=True)


def remove_app_prompts(manifest, app_dir: str | Path) -> None:
    """Remove an app's shipped prompt/snippet files + unregister its use-cases.

    Only the files the app declares are removed — never a user's own prompt. A
    file whose stored ``name`` no longer matches the app's definition is left
    alone (the app didn't ship it)."""
    app_name = getattr(manifest, "name", "") or ""
    # Always drop the app's use-case registrations (cheap; even if no files).
    from personalclaw.apps import prompt_registry

    prompt_registry.unregister_app(app_name)

    prompts = list(getattr(manifest, "prompts", None) or [])
    if not prompts:
        return

    from personalclaw.prompt_providers.native_provider import _prompt_path, _snippet_path

    base = Path(app_dir)
    for rel in prompts:
        raw = _read_definition(base, str(rel))
        if raw is None:
            continue
        name = str(raw["name"]).strip()
        entity = _entity_of(raw)
        try:
            path = _snippet_path(name) if entity == _ENTITY_SNIPPET else _prompt_path(name)
            if path.exists():
                path.unlink()
                logger.info("Removed app %r %s %r", app_name, entity, name)
        except (ValueError, OSError):
            logger.debug("failed to remove app %r %s %r", app_name, entity, name, exc_info=True)
