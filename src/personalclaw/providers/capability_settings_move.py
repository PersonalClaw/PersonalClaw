"""Each capability's own settings move out of the app's shared file, once (#3842).

An app's settings file, ``data/config.json``, held everything its providers read. Since #3842 it
holds the account's part, the settings every capability shares, and a capability's own live in
``data/capabilities/<id>.json``. For an app whose manifest declares no account ``settings`` that
changes nothing: all of its settings were shared, so all of them are the account's, where they
already are. An app that declares ``settings`` names, by its capabilities' schemas, what belongs to
one capability alone (``apps.account_settings.capability_fields``), and
:func:`move_capability_settings` carries each such setting out of the shared file into its
capability's, with its secret re-stored under the capability's own owner.

Run once at gateway start, beside the move of plaintext secrets. It is idempotent and fail-safe
per app, and ordered so a stop at any point is finished by the next run: the capability's file is
written first (its secret stored, then the reference written), and only then the shared file
without the setting, which deletes the account's copy of the secret. A value the capability already
holds is kept. A second run finds nothing to move and writes nothing.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.config import secret_refs

logger = logging.getLogger(__name__)


def move_app(app: str, manifest: Any) -> list[str]:
    """Move *app*'s capability-only settings out of its shared file; the settings moved."""
    from personalclaw.apps.account_settings import capability_fields
    from personalclaw.providers.settings import (
        ProviderSettings,
        load_stored,
        load_stored_capability,
    )

    homes = capability_fields(manifest)
    account = load_stored(app)
    moving = sorted(k for k in account if k in homes)
    if not moving:
        return []
    values = secret_refs.resolve(
        {k: account[k] for k in moving}, owner=secret_refs.account_owner(app)
    )
    for cid in sorted({homes[k] for k in moving}):
        current = load_stored_capability(app, cid)
        carried = {k: values[k] for k in moving if homes[k] == cid and k not in current}
        if carried:
            ProviderSettings._save_capability(app, cid, {**current, **carried})
    ProviderSettings._save(app, {k: v for k, v in account.items() if k not in homes})
    return moving


def move_capability_settings() -> dict[str, list[str]]:
    """Move every installed app's capability-only settings into its capabilities' own files
    (:func:`move_app`). Returns what moved, by app; an app whose move fails keeps its file as it
    was, and the next start tries again."""
    from personalclaw.apps.app_manager import _manifest_of
    from personalclaw.apps.manager import list_apps

    moved: dict[str, list[str]] = {}
    for info in list_apps():
        app = str(info.get("name") or "")
        manifest = _manifest_of(app) if app else None
        if manifest is None or not manifest.settings:
            continue
        try:
            fields = move_app(app, manifest)
        except Exception:  # noqa: BLE001 — one app's settings must not keep the rest from moving
            logger.warning("app %s: its capability settings were not moved", app, exc_info=True)
            continue
        if fields:
            moved[app] = fields
            logger.info("app %s: moved %s into its capabilities' settings", app, ", ".join(fields))
    return moved


# ── a capability renamed or retired by an update (#3841) ────────────────────────────────────────


def move_on_rename(app: str, old: str, new: str) -> None:
    """Carry *old*'s own settings and secrets to *new* in every account of *app*, when an update
    renames the capability (``renamedFrom``). A ``capability_lifecycle`` mover: idempotent, and a
    stop at any point is finished by the next run.

    In each account, four steps, in this order: store the secrets under *new*'s owner, write
    *new*'s settings, delete *old*'s, then purge *old*'s keys. Settings *new* already holds are
    kept (the move then only deletes *old*'s and purges its keys), and with nothing under *old*
    the run only purges what a stop left behind.

    Each account's secrets are read and stored as that account's own, as every read of them is:
    a record that references another account's key fails its account's move, and copies
    nothing. An account whose move fails is logged and the others still move; the first failure
    is raised once every account has run, so the move stays pending and the next run tries the
    accounts again."""
    from personalclaw.providers.accounts import FIRST, further_accounts

    failed: list[Exception] = []
    for account in [None, *further_accounts(app)]:
        try:
            _move_one(app, old, new, account)
        except Exception as exc:  # noqa: BLE001 — one account's failure must not hold the rest
            logger.warning(
                "app %s: moving %s to %s in account %s failed",
                app,
                old,
                new,
                account or FIRST,
                exc_info=True,
            )
            failed.append(exc)
    if failed:
        raise failed[0]


def _move_one(app: str, old: str, new: str, account: str | None) -> None:
    from personalclaw.providers.settings import (
        ProviderSettings,
        _record,
        _write,
        _write_record,
        capability_secret_owner,
        load_stored_capability,
    )

    was = capability_secret_owner(app, old, account)
    to = capability_secret_owner(app, new, account)

    def stored_for_new() -> dict[str, Any]:
        values = secret_refs.resolve(load_stored_capability(app, old, account), owner=was)
        return secret_refs.store(
            values, owner=to, declared=secret_refs.declared_app_fields(app), previous=None
        )

    if account is None:
        old_path = ProviderSettings.capability_path(app, old)
        new_path = ProviderSettings.capability_path(app, new)
        if new_path.is_symlink():
            raise OSError(f"{new_path} is a link: settings are never written through one")
        if old_path.is_file() and not old_path.is_symlink():
            if not new_path.exists():
                _write(new_path, stored_for_new())
            old_path.unlink()
    else:
        record = _record(app, account)
        held = record["capabilities"]
        if old in held:
            if new not in held:
                held[new] = stored_for_new()
            del held[old]
            _write_record(app, account, record)
    secret_refs.purge([was.prefix])


def remove_on_retire(app: str, capability: str) -> None:
    """Delete a retired capability's own settings and secrets in every account of *app*
    (``ProviderSettings.remove_capability``). A ``capability_lifecycle`` remover: idempotent."""
    from personalclaw.providers.settings import ProviderSettings

    ProviderSettings.remove_capability(app, capability)


def _register() -> None:
    from personalclaw.apps import capability_lifecycle

    capability_lifecycle.register_mover(move_on_rename)
    capability_lifecycle.register_remover(remove_on_retire)


_register()
