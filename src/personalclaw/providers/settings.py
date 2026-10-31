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


class NoSuchAccount(ValueError):
    """The app has no further account by that id (#3842). A :class:`ValueError`, so every caller
    that refuses a bad id refuses this one too."""


def _account(account: str | None) -> str | None:
    """*account* as stored: ``None`` for the first account (``None`` or ``"default"``), else the
    further account's checked id (:func:`~personalclaw.providers.accounts.checked_account`)."""
    from personalclaw.providers.accounts import FIRST, checked_account

    return None if account is None or account == FIRST else checked_account(account)


def _record(extension_name: str, account: str) -> dict[str, Any]:
    from personalclaw.providers.accounts import read_record

    record = read_record(extension_name, account)
    if record is None:
        raise NoSuchAccount(f"{extension_name!r} has no account {account!r}")
    return record


def load_stored_account(extension_name: str, account: str | None = None) -> dict[str, Any]:
    """One account's settings as stored (secret fields are references): the first account's file,
    :func:`load_stored`, or a further account's record. Raises :class:`NoSuchAccount` for an
    account the app does not have, and :class:`ValueError` for a string that is no account id."""
    acct = _account(account)
    return load_stored(extension_name) if acct is None else _record(extension_name, acct)["account"]


def load_stored_capability(
    extension_name: str, capability: str, account: str | None = None
) -> dict[str, Any]:
    """One capability's own settings for one account as stored (secret fields are references),
    never read through a link, as :func:`load_stored`. Raises :class:`ValueError` for a string that
    is not a capability id (:func:`_checked_capability`) or not an account id, and reads nothing."""
    name = f"{_checked_capability(capability)}.json"
    acct = _account(account)
    if acct is not None:
        own = _record(extension_name, acct)["capabilities"].get(capability)
        return own if isinstance(own, dict) else {}
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


def _capability_ids(extension_name: str, account: str | None = None) -> list[str]:
    """The ids of the capabilities one account holds settings for (a file or key not named for an
    id is no capability's)."""
    if account is not None:
        held = _record(extension_name, account)["capabilities"]
        return sorted(c for c in held if isinstance(c, str) and KEBAB_RE.match(c))
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


def _has_accounts(extension_name: str) -> bool:
    manifest = secret_refs._app_manifest(extension_name)
    return bool(manifest is not None and manifest.settings)


def account_secret_owner(
    extension_name: str, account: str | None = None
) -> secret_refs.SecretOwner:
    """Who one account's secrets belong to (``secret_refs.account_owner``). In an app with
    accounts it holds that account's keys alone, so a reference in one account never resolves
    another's (``secret_refs.within_account``); an app without keeps holding every key it owns."""
    owner = secret_refs.account_owner(extension_name, account)
    if account is None and not _has_accounts(extension_name):
        return owner
    return secret_refs.within_account(owner, owner)


def capability_secret_owner(
    extension_name: str, capability: str, account: str | None = None
) -> secret_refs.SecretOwner:
    """Who one capability's own secrets belong to in one account, held to that account's keys as
    :func:`account_secret_owner` is."""
    owner = secret_refs.capability_owner(extension_name, capability, account)
    if account is None and not _has_accounts(extension_name):
        return owner
    return secret_refs.within_account(owner, secret_refs.account_owner(extension_name, account))


def _write_record(extension_name: str, account: str, record: dict[str, Any]) -> None:
    from personalclaw.providers.accounts import write_record

    write_record(extension_name, account, record)


def _write_part(
    extension_name: str, account: str, stored: dict[str, Any], capability: str | None = None
) -> None:
    """Write one part of a further account's record: its account settings, or *capability*'s."""
    record = _record(extension_name, account)
    if capability is None:
        record["account"] = stored
    else:
        record["capabilities"][capability] = stored
    _write_record(extension_name, account, record)


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
    """Read/write per-extension config with schema validation.

    Every call takes an optional ``account`` (#3842): ``None`` (or ``"default"``) is the app's
    first account, the one every app had before accounts; a further account is one
    :meth:`add_account` made (``providers/accounts.py``). An account the app does not have raises
    :class:`NoSuchAccount`."""

    @staticmethod
    def config_path(extension_name: str) -> Path:
        # Inside data/ so it survives updates (an update preserves data/) — the SAME file
        # apps.app_config writes through the Apps config UI (these once
        # diverged, so UI-set provider keys never reached the provider at build).
        return app_dir(extension_name) / "data" / "config.json"

    @staticmethod
    def capability_path(extension_name: str, capability: str) -> Path:
        """Where one capability's own settings live for the first account (#3842), beside the
        account's file. Raises :class:`ValueError` for a string that is not a capability id."""
        name = f"{_checked_capability(capability)}.json"
        return app_dir(extension_name) / "data" / "capabilities" / name

    @staticmethod
    def load(
        extension_name: str, capability: str | None = None, account: str | None = None
    ) -> dict[str, Any]:
        """The settings with every secret field holding its VALUE: *capability*'s (its account's
        merged with its own), or, with none, the account's merged with every capability's, the one
        dict every app read before its settings had two parts (kept until the v0.4.0 cut). One
        account's alone: nothing is inherited from another.

        Raises :class:`~personalclaw.config.secret_refs.ForeignSecretReference` when the file
        references a credential another owner holds, and :class:`ValueError` (reading nothing) for
        a *capability* that is not a capability id or an *account* the app does not have."""
        if capability is not None:
            _checked_capability(capability)
        acct = _account(account)
        merged = ProviderSettings.load_account(extension_name, acct)
        for cid in [capability] if capability else _capability_ids(extension_name, acct):
            merged.update(ProviderSettings._load_capability(extension_name, cid, acct))
        return merged

    @staticmethod
    def load_account(extension_name: str, account: str | None = None) -> dict[str, Any]:
        """One account's settings, the ones every capability of the app shares, secrets as their
        VALUES."""
        acct = _account(account)
        return secret_refs.resolve(
            load_stored_account(extension_name, acct),
            owner=account_secret_owner(extension_name, acct),
        )

    @staticmethod
    def _load_capability(
        extension_name: str, capability: str, account: str | None = None
    ) -> dict[str, Any]:
        return secret_refs.resolve(
            load_stored_capability(extension_name, capability, account),
            owner=capability_secret_owner(extension_name, capability, account),
        )

    @staticmethod
    def save(extension_name: str, config: dict[str, Any], account: str | None = None) -> None:
        """Persist ``config`` with each secret value moved into the credential store: each
        setting into the account's part or its capability's, by what the manifest declares.

        A whole write: every capability that owns a setting has its own replaced too, so one whose
        settings ``config`` leaves out is cleared, its secrets with them.

        Raises :class:`ValueError` for a value no credential can hold (a NUL character) or a
        reference to another owner's credential, and nothing is written.
        """
        acct = _account(account)
        homes = _homes(extension_name)
        own, capabilities = _split(config, homes)
        ProviderSettings._save(extension_name, own, acct)
        for cid in sorted(set(homes.values())):
            ProviderSettings._save_capability(extension_name, cid, capabilities.get(cid, {}), acct)

    @staticmethod
    def _save(
        extension_name: str, config: dict[str, Any], account: str | None = None
    ) -> dict[str, Any]:
        stored = secret_refs.store(
            config,
            owner=account_secret_owner(extension_name, account),
            declared=secret_refs.declared_app_fields(extension_name),
            previous=load_stored_account(extension_name, account),
        )
        if account is None:
            _write(ProviderSettings.config_path(extension_name), stored)
        else:
            _write_part(extension_name, account, stored)
        return stored

    @staticmethod
    def save_account(
        extension_name: str, config: dict[str, Any], account: str | None = None
    ) -> None:
        """Replace one account's own settings with ``config``, each secret value moved into the
        credential store under the account's owner; its capabilities' stay as they are. Raises as
        :meth:`save` does."""
        ProviderSettings._save(extension_name, config, _account(account))

    @staticmethod
    def save_capability(
        extension_name: str, capability: str, config: dict[str, Any], account: str | None = None
    ) -> None:
        """Replace *capability*'s own settings for one account with ``config``, each secret value
        moved into the credential store under the capability's owner. Raises as :meth:`save` does,
        and :class:`ValueError` for a *capability* that is not a capability id."""
        ProviderSettings._save_capability(extension_name, capability, config, _account(account))

    @staticmethod
    def _save_capability(
        extension_name: str, capability: str, config: dict[str, Any], account: str | None = None
    ) -> dict[str, Any]:
        _checked_capability(capability)
        stored = secret_refs.store(
            config,
            owner=capability_secret_owner(extension_name, capability, account),
            declared=secret_refs.declared_app_fields(extension_name),
            previous=load_stored_capability(extension_name, capability, account),
        )
        if account is None:
            _write(ProviderSettings.capability_path(extension_name, capability), stored)
        else:
            _write_part(extension_name, account, stored, capability)
        return stored

    @staticmethod
    def update(
        extension_name: str, partial: dict[str, Any], account: str | None = None
    ) -> dict[str, Any]:
        """Merge ``partial`` into one account's saved settings, each setting into the account's
        part or its capability's; returns them all with each secret's VALUE, as :meth:`load` with
        no capability does.

        Merged over the STORED form, so a secret ``partial`` does not mention stays exactly the
        reference it is, and one it carries back as a reference (a masked form's untouched
        field) is kept — never read out and written again."""
        acct = _account(account)
        own, capabilities = _split(partial, _homes(extension_name))
        if own or not capabilities:
            ProviderSettings.update_account(extension_name, own, acct)
        for cid, part in capabilities.items():
            ProviderSettings.update_capability(extension_name, cid, part, acct)
        return ProviderSettings.load(extension_name, account=acct)

    @staticmethod
    def update_account(
        extension_name: str, partial: dict[str, Any], account: str | None = None
    ) -> dict[str, Any]:
        """Merge ``partial`` into one account's settings; returns them with each secret's VALUE."""
        acct = _account(account)
        current = load_stored_account(extension_name, acct)
        current.update(partial)
        stored = ProviderSettings._save(extension_name, current, acct)
        return secret_refs.resolve(stored, owner=account_secret_owner(extension_name, acct))

    @staticmethod
    def update_capability(
        extension_name: str, capability: str, partial: dict[str, Any], account: str | None = None
    ) -> dict[str, Any]:
        """Merge ``partial`` into *capability*'s own settings for one account; returns them with
        each secret's VALUE (its own only: :meth:`load` with the capability adds the account's).
        Raises :class:`ValueError`, writing nothing, for a *capability* that is not a capability
        id or an *account* the app does not have."""
        acct = _account(account)
        current = load_stored_capability(extension_name, capability, acct)
        current.update(partial)
        stored = ProviderSettings._save_capability(extension_name, capability, current, acct)
        return secret_refs.resolve(
            stored, owner=capability_secret_owner(extension_name, capability, acct)
        )

    @staticmethod
    def remove_capability(extension_name: str, capability: str) -> int:
        """Delete *capability*'s own settings and exactly its secrets, in every account; each
        account's own and every other capability's stay. Returns how many secrets were deleted.
        Raises :class:`ValueError`, deleting nothing, for a *capability* that is not a capability
        id."""
        from personalclaw.providers.accounts import further_accounts

        _checked_capability(capability)
        removed = 0
        for acct in [None, *further_accounts(extension_name)]:
            removed += secret_refs.purge(
                [capability_secret_owner(extension_name, capability, acct).prefix]
            )
            if acct is not None:
                record = _record(extension_name, acct)
                if record["capabilities"].pop(capability, None) is not None:
                    _write_record(extension_name, acct, record)
                continue
            path = ProviderSettings.capability_path(extension_name, capability)
            if path.is_file() and not path.is_symlink():
                path.unlink()
        return removed

    # ── accounts ────────────────────────────────────────────────────────────────────────────

    @staticmethod
    def list_accounts(extension_name: str) -> list[dict[str, str]]:
        """The app's accounts, the first (``"default"``) then each further one, by id and label."""
        from personalclaw.providers.accounts import FIRST, further_accounts

        rows = [{"id": FIRST, "label": ""}]
        for acct in further_accounts(extension_name):
            label = _record(extension_name, acct).get("display_name", "")
            rows.append({"id": acct, "label": str(label or "")})
        return rows

    @staticmethod
    def add_account(extension_name: str, label: str = "", account: str | None = None) -> str:
        """Add a further account, with no settings yet; returns its id (*account*, else a new
        one). Raises :class:`ValueError` for an id that is not a further account's, one the app
        already has, or one whose file holds anything else (a multi-instance record an earlier
        version left shares the folder, and its key prefix): nothing is written over."""
        from personalclaw.providers.accounts import (
            new_account_id,
            new_record,
            read_record,
            record_path,
            write_record,
        )

        if not _has_accounts(extension_name):
            raise ValueError(
                f"{extension_name!r} declares no account settings, so it keeps one account"
            )
        acct = _account(account) if account is not None else new_account_id()
        if acct is None:
            raise ValueError("the first account is always there, and is never added")
        if read_record(extension_name, acct) is not None:
            raise ValueError(f"{extension_name!r} already has an account {acct!r}")
        path = record_path(extension_name, acct)
        if path.exists() or path.is_symlink():
            raise ValueError(
                f"{extension_name!r} already keeps a record under {acct!r} that is no account: "
                "name the account another id"
            )
        write_record(extension_name, acct, new_record(extension_name, acct, label))
        return acct

    @staticmethod
    def remove_account(extension_name: str, account: str) -> int:
        """Remove a further account: exactly its secrets, its capabilities' with them, then its
        record. Every other account's keys stay, the first account's included. The first account
        is never removed (:meth:`clear_account` empties it). Returns how many secrets were deleted.

        The secrets go first, so a stop half way leaves a record whose secrets read as absent,
        and running it again finishes it."""
        from personalclaw.providers.accounts import checked_account, record_path

        acct = checked_account(account)
        _record(extension_name, acct)
        removed = secret_refs.purge([account_secret_owner(extension_name, acct).prefix])
        record_path(extension_name, acct).unlink(missing_ok=True)
        return removed

    @staticmethod
    def clear_account(extension_name: str, account: str | None = None) -> int:
        """Empty one account's own settings, and delete exactly its own secrets: never a key of a
        capability nested under its prefix, which is not the account's to clear. Returns how many
        secrets were deleted."""
        acct = _account(account)
        owner = account_secret_owner(extension_name, acct)
        before = secret_refs.count_direct(owner)
        ProviderSettings._save(extension_name, {}, acct)
        secret_refs.purge_direct(owner)  # one no reference named any more
        return before

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
