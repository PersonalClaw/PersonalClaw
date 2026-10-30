"""An app's accounts: each a full copy of its account settings and of each capability's own (#3842).

An app that declares account ``settings`` (``apps/account_settings.py``) has a first account, kept
where its settings always were: ``data/config.json`` and ``data/capabilities/<id>.json``
(``providers/settings.py``). Each further account is a record in the instance store,
``extensions/<app>/instances/<id>.json``, holding the same two parts::

    {"id", "extension_name", "display_name", "kind": "account",
     "account": {...}, "capabilities": {"<capability>": {...}}, "enabled"}

``kind`` keeps it apart from a multi-instance provider's record, and ``validate`` refuses account
``settings`` beside a ``multiInstance`` provider, so an app has accounts or instances, never both.

Nothing is inherited across accounts: a capability reads the account it runs under and nothing of
another's. Every secret is its account's (``secret_refs.account_owner``), and removing an account
deletes exactly its keys, its capabilities' with them. The first account is never removed, only
cleared. An older core refuses an app that declares account ``settings``, so it never reads these
records.

This module reads and writes the records as stored (secret fields are references);
``ProviderSettings`` is what resolves them and moves values into the credential store.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from personalclaw.apps.manifest import KEBAB_RE
from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

#: The id that names an app's first account, the one in ``data/config.json``. Never a record's.
FIRST = "default"

KIND = "account"


def checked_account(account: str) -> str:
    """*account*, refused with :class:`ValueError` unless it is a further account's id: kebab-case,
    and not :data:`FIRST`, which names the first account and has no record."""
    if not isinstance(account, str) or not KEBAB_RE.match(account):
        raise ValueError(f"{account!r} is not an account id")
    if account == FIRST:
        raise ValueError(f"{FIRST!r} is the app's first account, which has no record")
    return account


def _folder(app: str) -> Path:
    from personalclaw.providers.instances import _instances_dir

    return _instances_dir(app)


def record_path(app: str, account: str) -> Path:
    return _folder(app) / f"{checked_account(account)}.json"


def read_record(app: str, account: str) -> dict[str, Any] | None:
    """*account*'s record as stored, or ``None`` when the app has no such account. Never read
    through a link, its folder's included, and a multi-instance provider's record is no account."""
    path = record_path(app, account)
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Failed to read account %s of %s: %s", account, app, exc)
        return None
    if not isinstance(data, dict) or data.get("kind") != KIND:
        return None
    data["id"], data["extension_name"] = account, app
    for part in ("account", "capabilities"):
        if not isinstance(data.get(part), dict):
            data[part] = {}
    return data


def write_record(app: str, account: str, record: dict[str, Any]) -> None:
    """Write *account*'s record, refusing a link in its place or its folder's."""
    path = record_path(app, account)
    if path.is_symlink() or path.parent.is_symlink():
        raise OSError(f"{path} is a link: settings are never written through one")
    atomic_write(path, json.dumps(record, indent=2) + "\n")


def new_record(app: str, account: str, label: str) -> dict[str, Any]:
    return {
        "id": account,
        "extension_name": app,
        "display_name": label,
        "kind": KIND,
        "account": {},
        "capabilities": {},
        "enabled": True,
    }


def further_accounts(app: str) -> list[str]:
    """The ids of the app's further accounts, in order. A file that is no account's is skipped."""
    folder = _folder(app)
    if folder.is_symlink() or not folder.is_dir():
        return []
    ids = []
    for path in sorted(folder.glob("*.json")):
        if KEBAB_RE.match(path.stem) and path.stem != FIRST and read_record(app, path.stem):
            ids.append(path.stem)
    return ids


def new_account_id() -> str:
    return uuid.uuid4().hex[:12]
