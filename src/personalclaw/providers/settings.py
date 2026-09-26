"""Per-extension configuration storage.

Each extension owns its config at ``~/.personalclaw/apps/{name}/data/config.json``
— inside ``data/`` so it survives app updates (A2 preserves ``data/``). This is the
SAME path the Apps config UI writes through :mod:`apps.app_config`; a provider built
at boot reads its user settings from here. (Historically this read the app-dir root
``config.json`` while the UI wrote to ``data/config.json`` — so a key set in the UI
never reached the provider. Unified onto ``data/`` — bug #31.)

This module provides read/write with JSON Schema validation against the
extension's declared ``settingsSchema``.

A secret setting never reaches the file: :meth:`ProviderSettings.save` moves each
declared-sensitive (or credential-named) value into the credential store under a key this
app owns and writes a ``{{secret:…}}`` reference in its place, and :meth:`ProviderSettings.load`
resolves it back — so an app reads and writes real values exactly as before
(:mod:`personalclaw.config.secret_refs`). The Bot and App tokens slack-channel is configured
with used to sit in this file in plaintext, at mode 0644.

A reference resolves only against the app's own credentials: one in this file naming another
owner's key is refused by :meth:`ProviderSettings.load` and by :meth:`ProviderSettings.save`
(:class:`~personalclaw.config.secret_refs.ForeignSecretReference`). The settings routes read
:func:`load_stored` — the references, masked — never the values. It is a module function and
not a method on purpose: ``ProviderSettings`` is published to apps through ``personalclaw.sdk``,
and an app has no use for the stored form.
"""

import json
import logging
from pathlib import Path
from typing import Any

from personalclaw.apps.manager import app_dir, read_app_owned_text
from personalclaw.apps.schema_validate import validate_properties
from personalclaw.atomic_write import atomic_write
from personalclaw.config import secret_refs

logger = logging.getLogger(__name__)


def load_stored(extension_name: str) -> dict[str, Any]:
    """The app's settings file as it is on disk — secret fields are references. For the
    writers, and for the routes that show settings (masked), which have no business holding a
    value.

    Never read through a link: the app writes ``data/``, so a ``config.json`` it made a link
    reads as no settings rather than as whatever file the link names."""
    root = app_dir(extension_name)
    try:
        data = json.loads(read_app_owned_text(root, "data", "config.json"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("Failed to read extension config for %s: %s", extension_name, exc)
        return {}
    return data if isinstance(data, dict) else {}


class ProviderSettings:
    """Read/write per-extension config with schema validation."""

    @staticmethod
    def config_path(extension_name: str) -> Path:
        # Inside data/ so it survives updates (A2 preserves data/) — the SAME file
        # apps.app_config writes through the Apps config UI (bug #31: these once
        # diverged, so UI-set provider keys never reached the provider at build).
        return app_dir(extension_name) / "data" / "config.json"

    @staticmethod
    def load(extension_name: str) -> dict[str, Any]:
        """The extension's settings with every secret field holding its VALUE.

        Raises :class:`~personalclaw.config.secret_refs.ForeignSecretReference` when the file
        references a credential another owner holds."""
        return secret_refs.resolve(
            load_stored(extension_name), owner=secret_refs.app_owner(extension_name)
        )

    @staticmethod
    def save(extension_name: str, config: dict[str, Any]) -> None:
        """Persist ``config`` with each secret value moved into the credential store.

        Raises :class:`ValueError` for a value no credential can hold (a NUL character) or a
        reference to another owner's credential, and nothing is written.
        """
        ProviderSettings._save(extension_name, config)

    @staticmethod
    def _save(extension_name: str, config: dict[str, Any]) -> dict[str, Any]:
        stored = secret_refs.store(
            config,
            owner=secret_refs.app_owner(extension_name),
            declared=secret_refs.declared_app_fields(extension_name),
            previous=load_stored(extension_name),
        )
        atomic_write(
            ProviderSettings.config_path(extension_name), json.dumps(stored, indent=2) + "\n"
        )
        return stored

    @staticmethod
    def update(extension_name: str, partial: dict[str, Any]) -> dict[str, Any]:
        """Merge ``partial`` into the saved settings; returns them with each secret's VALUE.

        Merged over the STORED form, so a secret ``partial`` does not mention stays exactly the
        reference it is, and one it carries back as a reference (a masked form's untouched
        field) is kept — never read out and written again."""
        current = load_stored(extension_name)
        current.update(partial)
        stored = ProviderSettings._save(extension_name, current)
        return secret_refs.resolve(stored, owner=secret_refs.app_owner(extension_name))

    @staticmethod
    def validate(config: dict[str, Any], schema: dict[str, Any]) -> list[str]:
        """Validate config against a declared ``settingsSchema``. Returns list of errors.

        The per-property rules live in :mod:`personalclaw.apps.schema_validate`, shared with
        the app ``configSchema`` path. This copy used to implement them itself and had drifted:
        it enforced no bound at all (so ``confidence_threshold`` declaring ``[0.0, 1.0]``
        accepted ``5``) and accepted ``True`` for an ``integer``, since ``bool`` is an ``int``
        subclass (#616).

        What stays HERE is this path's own object-level policy, which differs from the app
        path's on purpose: a provider with no schema is unvalidated, and an unknown key is
        IGNORED rather than refused — a stored config may carry a key from an older manifest,
        and refusing it would make the whole config unsavable.
        """
        if not schema:
            return []
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return []
        # An undeclared key is simply not checked (the shared module skips whatever the schema
        # does not describe). The app path layers its own refusal on top of that; this one does
        # not, deliberately.
        return validate_properties(config, properties, schema.get("required", []))
