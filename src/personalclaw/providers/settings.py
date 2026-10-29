"""Per-extension configuration storage.

Each extension owns its config at ``~/.personalclaw/apps/{name}/data/config.json``
— inside ``data/`` so it survives app updates (an update preserves ``data/``). This is the
SAME path the Apps config UI writes through :mod:`apps.app_config`; a provider built
at boot reads its user settings from here. (Historically this read the app-dir root
``config.json`` while the UI wrote to ``data/config.json`` — so a key set in the UI
never reached the provider. Unified onto ``data/``.)

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

**Two parts (#3842).** ``data/config.json`` holds the app's account settings, the ones every
capability shares, and ``data/capabilities/<id>.json`` a capability's own
(:mod:`personalclaw.apps.account_settings` says which is which). :meth:`ProviderSettings.load` with
a capability reads that capability's: the account's merged with its own, which cannot collide, since
``validate`` refuses a capability setting named like an account one. :meth:`load_account` reads the
account's. A capability's secrets are stored under its own owner, nested under the app's
(``secret_refs.capability_owner``), so :meth:`remove_capability` deletes exactly its keys. An app
that declares no account ``settings`` stores everything in ``config.json``, as every app did
before, and an older core still reads that file exactly as it always did.
"""

import json
import logging
from pathlib import Path
from typing import Any

from personalclaw.apps.manager import app_dir, read_app_owned_text
from personalclaw.apps.manifest import KEBAB_RE
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


def _checked_capability(capability: str) -> str:
    """*capability*, refused with :class:`ValueError` unless it is a capability id (kebab-case),
    so no id reads or writes a file outside ``data/capabilities/``: these calls are the SDK's, and
    only the routes check an id against the manifest."""
    if not isinstance(capability, str) or not KEBAB_RE.match(capability):
        raise ValueError(f"{capability!r} is not a capability id")
    return capability


def load_stored_capability(extension_name: str, capability: str) -> dict[str, Any]:
    """One capability's own settings file as it is on disk (secret fields are references), never
    read through a link, as :func:`load_stored`. Raises :class:`ValueError` for a string that is
    not a capability id (:func:`_checked_capability`), and reads nothing."""
    name = f"{_checked_capability(capability)}.json"
    root = app_dir(extension_name)
    try:
        data = json.loads(read_app_owned_text(root, "data", "capabilities", name))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning(
            "Failed to read capability settings %s/%s: %s", extension_name, capability, exc
        )
        return {}
    return data if isinstance(data, dict) else {}


def _capability_ids(extension_name: str) -> list[str]:
    """The ids of the capabilities whose settings files exist (a file not named for an id is no
    capability's)."""
    folder = app_dir(extension_name) / "data" / "capabilities"
    if folder.is_symlink() or not folder.is_dir():
        return []
    return sorted(
        p.stem
        for p in folder.glob("*.json")
        if KEBAB_RE.match(p.stem) and p.is_file() and not p.is_symlink()
    )


def _write(path: Path, stored: dict[str, Any]) -> None:
    """Write a settings file the gateway owns inside the app's ``data/``, refusing a link the app
    planted in its place (it would send the write wherever the link points)."""
    if path.parent.is_symlink() or path.is_symlink():
        raise OSError(f"{path} is a link: settings are never written through one")
    atomic_write(path, json.dumps(stored, indent=2) + "\n")


def _homes(extension_name: str) -> dict[str, str]:
    """Each setting that belongs to one capability alone, and that capability's id, by what the
    manifest declares (``account_settings.capability_fields``)."""
    from personalclaw.apps.account_settings import capability_fields

    manifest = secret_refs._app_manifest(extension_name)
    return capability_fields(manifest) if manifest is not None else {}


def _split(config: dict[str, Any], homes: dict[str, str]) -> tuple[dict[str, Any], dict[str, dict]]:
    """``config`` as the account's part and each capability's, by :func:`_homes`."""
    account: dict[str, Any] = {}
    capabilities: dict[str, dict[str, Any]] = {}
    for key, value in config.items():
        if key in homes:
            capabilities.setdefault(homes[key], {})[key] = value
        else:
            account[key] = value
    return account, capabilities


class ProviderSettings:
    """Read/write per-extension config with schema validation."""

    @staticmethod
    def config_path(extension_name: str) -> Path:
        # Inside data/ so it survives updates (an update preserves data/) — the SAME file
        # apps.app_config writes through the Apps config UI (these once
        # diverged, so UI-set provider keys never reached the provider at build).
        return app_dir(extension_name) / "data" / "config.json"

    @staticmethod
    def capability_path(extension_name: str, capability: str) -> Path:
        """Where one capability's own settings live (#3842), beside the account's file. Raises
        :class:`ValueError` for a string that is not a capability id."""
        name = f"{_checked_capability(capability)}.json"
        return app_dir(extension_name) / "data" / "capabilities" / name

    @staticmethod
    def load(extension_name: str, capability: str | None = None) -> dict[str, Any]:
        """The settings with every secret field holding its VALUE: *capability*'s (the account's
        merged with its own), or, with none, the account's merged with every capability's, the one
        dict every app read before its settings had two parts (kept until the v0.4.0 cut).

        Raises :class:`~personalclaw.config.secret_refs.ForeignSecretReference` when the file
        references a credential another owner holds, and :class:`ValueError` (reading nothing) for a
        *capability* that is not a capability id."""
        if capability is not None:
            _checked_capability(capability)
        merged = ProviderSettings.load_account(extension_name)
        for cid in [capability] if capability else _capability_ids(extension_name):
            merged.update(ProviderSettings._load_capability(extension_name, cid))
        return merged

    @staticmethod
    def load_account(extension_name: str) -> dict[str, Any]:
        """The account settings every capability of the app shares, secrets as their VALUES."""
        return secret_refs.resolve(
            load_stored(extension_name), owner=secret_refs.account_owner(extension_name)
        )

    @staticmethod
    def _load_capability(extension_name: str, capability: str) -> dict[str, Any]:
        return secret_refs.resolve(
            load_stored_capability(extension_name, capability),
            owner=secret_refs.capability_owner(extension_name, capability),
        )

    @staticmethod
    def save(extension_name: str, config: dict[str, Any]) -> None:
        """Persist ``config`` with each secret value moved into the credential store: each
        setting into the account's file or its capability's, by what the manifest declares.

        A whole write: every capability that owns a setting has its own replaced too, so one whose
        settings ``config`` leaves out is cleared, its secrets with them.

        Raises :class:`ValueError` for a value no credential can hold (a NUL character) or a
        reference to another owner's credential, and nothing is written.
        """
        homes = _homes(extension_name)
        account, capabilities = _split(config, homes)
        ProviderSettings._save(extension_name, account)
        for cid in sorted(set(homes.values())):
            ProviderSettings._save_capability(extension_name, cid, capabilities.get(cid, {}))

    @staticmethod
    def _save(extension_name: str, config: dict[str, Any]) -> dict[str, Any]:
        stored = secret_refs.store(
            config,
            owner=secret_refs.account_owner(extension_name),
            declared=secret_refs.declared_app_fields(extension_name),
            previous=load_stored(extension_name),
        )
        _write(ProviderSettings.config_path(extension_name), stored)
        return stored

    @staticmethod
    def save_capability(extension_name: str, capability: str, config: dict[str, Any]) -> None:
        """Replace *capability*'s own settings with ``config``, each secret value moved into the
        credential store under the capability's owner. Raises as :meth:`save` does, and
        :class:`ValueError` for a *capability* that is not a capability id."""
        ProviderSettings._save_capability(extension_name, capability, config)

    @staticmethod
    def _save_capability(
        extension_name: str, capability: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        _checked_capability(capability)
        stored = secret_refs.store(
            config,
            owner=secret_refs.capability_owner(extension_name, capability),
            declared=secret_refs.declared_app_fields(extension_name),
            previous=load_stored_capability(extension_name, capability),
        )
        _write(ProviderSettings.capability_path(extension_name, capability), stored)
        return stored

    @staticmethod
    def update(extension_name: str, partial: dict[str, Any]) -> dict[str, Any]:
        """Merge ``partial`` into the saved settings, each setting into the account's part or its
        capability's; returns them all with each secret's VALUE, as :meth:`load` with no
        capability does.

        Merged over the STORED form, so a secret ``partial`` does not mention stays exactly the
        reference it is, and one it carries back as a reference (a masked form's untouched
        field) is kept — never read out and written again."""
        account, capabilities = _split(partial, _homes(extension_name))
        if account or not capabilities:
            ProviderSettings.update_account(extension_name, account)
        for cid, own in capabilities.items():
            ProviderSettings.update_capability(extension_name, cid, own)
        return ProviderSettings.load(extension_name)

    @staticmethod
    def update_account(extension_name: str, partial: dict[str, Any]) -> dict[str, Any]:
        """Merge ``partial`` into the account settings; returns them with each secret's VALUE."""
        current = load_stored(extension_name)
        current.update(partial)
        stored = ProviderSettings._save(extension_name, current)
        return secret_refs.resolve(stored, owner=secret_refs.account_owner(extension_name))

    @staticmethod
    def update_capability(
        extension_name: str, capability: str, partial: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge ``partial`` into *capability*'s own settings; returns them with each secret's
        VALUE (its own only: :meth:`load` with the capability adds the account's). Raises
        :class:`ValueError`, writing nothing, for a *capability* that is not a capability id."""
        current = load_stored_capability(extension_name, capability)
        current.update(partial)
        stored = ProviderSettings._save_capability(extension_name, capability, current)
        return secret_refs.resolve(
            stored, owner=secret_refs.capability_owner(extension_name, capability)
        )

    @staticmethod
    def remove_capability(extension_name: str, capability: str) -> int:
        """Delete *capability*'s own settings and exactly its secrets; the account's and every
        other capability's stay. Returns how many secrets were deleted. Raises :class:`ValueError`,
        deleting nothing, for a *capability* that is not a capability id."""
        _checked_capability(capability)
        removed = secret_refs.purge(
            [secret_refs.capability_owner(extension_name, capability).prefix]
        )
        path = ProviderSettings.capability_path(extension_name, capability)
        if path.is_file() and not path.is_symlink():
            path.unlink()
        return removed

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
