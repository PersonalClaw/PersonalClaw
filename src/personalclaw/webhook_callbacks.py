"""A callback the agent registers so an outside system can hand it results later.

The chat's ``hook_register`` records a *callback*: a key an outside system names when it posts to
``POST /api/hooks/agent`` (``sessionKey: "hook:<id>"``), and the context the agent saved for the
turn that post starts. That turn runs unattended with the agent's tools, and it starts from text
the agent wrote. So a callback follows the trigger rule (`triggers.grants`): it is registered not
allowed to run, and the owner allows it on the Triggers page, which asks first. The yes is sealed
to the context as it stood (`owner_grants`), so a callback re-registered with other context waits
for the owner again. Switching it off takes the yes back, and nothing about that needs asking.

🔴 ONE OWNER, ONE PATH. Registrations live in ``<home>/webhook_callbacks.json``, and this module
is the only thing that reads or writes it. They used to be extra top-level keys in ``hooks.json``,
the lifecycle trigger store's file (`hooks.ScriptHookStore`). Measured on `main`: registering
``hook_id="hooks"`` replaced the owner's lifecycle triggers, after which the store failed to load
at all, and any other registration vanished at the store's next save.
"""

from __future__ import annotations

import fcntl
import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from personalclaw.constants import HOOK_SESSION_PREFIX
from personalclaw.owner_grants import GrantBook

logger = logging.getLogger(__name__)

#: The file, under the home.
CALLBACKS_FILE = "webhook_callbacks.json"

#: Where the owner's yes to each callback is kept (`owner_grants`), keyed by the callback's id.
BOOK = GrantBook("callbacks")

#: What a callback runs, as the Triggers page names it in "Not allowed to use …".
RUNS_LABEL = "Agent turn"


@dataclass
class Callback:
    """One registration: its id, the context its turn starts from, and when it was saved."""

    id: str
    context_summary: str = ""
    registered_at: float = 0.0

    @property
    def session_key(self) -> str:
        return f"{HOOK_SESSION_PREFIX}{self.id}"

    @classmethod
    def from_dict(cls, data: dict) -> Callback | None:
        cid = data.get("id")
        if not isinstance(cid, str) or not cid:
            return None
        context = data.get("context_summary")
        registered = data.get("registered_at")
        return cls(
            id=cid,
            context_summary=context if isinstance(context, str) else "",
            registered_at=float(registered) if isinstance(registered, (int, float)) else 0.0,
        )


def _path() -> Path:
    from personalclaw.config.loader import config_dir

    return Path(config_dir()) / CALLBACKS_FILE


@contextmanager
def _locked() -> Iterator[None]:
    """One writer at a time across processes: ``hook_register`` runs in the MCP server's process,
    and the owner's delete in the gateway's."""
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / f"{CALLBACKS_FILE}.lock", "a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read() -> dict[str, Callback]:
    """Every registration, by id. Never raises: a file that cannot be read, or an entry that is
    not a registration, is left out and logged, so one bad write cannot stop the rest loading."""
    path = _path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.warning("callbacks: %s is unreadable; no callback in it is listed", path)
        return {}
    entries = data.get("callbacks") if isinstance(data, dict) else None
    out: dict[str, Callback] = {}
    for entry in entries if isinstance(entries, list) else []:
        callback = Callback.from_dict(entry) if isinstance(entry, dict) else None
        if callback is None:
            logger.warning("callbacks: skipping an entry that is not a callback: %r", entry)
            continue
        out[callback.id] = callback
    return out


def _write(callbacks: dict[str, Callback]) -> None:
    from personalclaw.atomic_write import atomic_json_write

    atomic_json_write(_path(), {"version": 1, "callbacks": [asdict(c) for c in callbacks.values()]})


def list_all() -> list[Callback]:
    return list(_read().values())


def get(callback_id: str) -> Callback | None:
    return _read().get(callback_id)


def register(callback_id: str, context_summary: str) -> Callback:
    """Save *callback_id* with *context_summary*, replacing an earlier registration of it.

    What the chat's ``hook_register`` does. It gives nothing: a new callback is not allowed to run,
    and one re-registered with other context no longer matches the yes it had (`allowed`)."""
    with _locked():
        callbacks = _read()
        callback = Callback(
            id=callback_id, context_summary=context_summary, registered_at=time.time()
        )
        callbacks[callback_id] = callback
        _write(callbacks)
    return callback


def remove(callback_id: str) -> bool:
    """Delete a registration and the yes it had. The owner's delete on the Triggers page."""
    with _locked():
        callbacks = _read()
        if callbacks.pop(callback_id, None) is None:
            return False
        _write(callbacks)
    BOOK.revoke(callback_id)
    return True


def allowed(callback: Callback) -> bool:
    """Whether the owner allowed *callback* to run with the context it has now."""
    return BOOK.holds(callback.id, callback.context_summary)


def allow(callback: Callback) -> None:
    """The owner's yes, given after the Triggers page's question."""
    BOOK.give(callback.id, callback.context_summary)


def disallow(callback: Callback) -> None:
    """Take the owner's yes back: the Triggers page's switch turned off."""
    BOOK.revoke(callback.id)


def consent(callback: Callback) -> str:
    """The sentence the owner agrees to when they allow *callback* — product copy."""
    return (
        f"Allowing “{callback.id}” lets an outside system that holds your webhook token start an "
        "agent turn with its tools, from the context the agent saved for this callback, as it is "
        "now."
    )


#: The consent dialog's heading for :func:`consent` (`http_errors.consent_required`).
CONSENT_TITLE = "Allow this callback to run?"


def context_for_turn(callback: Callback, *, now: float | None = None) -> str:
    """The saved context a callback's turn starts from, by how old it is.

    Under an hour it is used as written; up to a day it is marked as possibly outdated; after that
    it is too stale to steer a turn and is left out.
    """
    if not callback.context_summary or not callback.registered_at:
        return ""
    age_hours = ((now if now is not None else time.time()) - callback.registered_at) / 3600
    if age_hours > 24:
        return ""
    if age_hours > 1:
        return f"[Context from {age_hours:.0f}h ago — may be outdated]\n{callback.context_summary}"
    return callback.context_summary
