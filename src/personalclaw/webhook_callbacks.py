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
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from personalclaw import lasting_work, record_files
from personalclaw.constants import HOOK_SESSION_PREFIX
from personalclaw.owner_grants import GrantBook

if TYPE_CHECKING:
    from personalclaw.outside_text import Admitted

logger = logging.getLogger(__name__)

#: The file, under the home.
CALLBACKS_FILE = "webhook_callbacks.json"

#: Where the owner's yes to each callback is kept (`owner_grants`), keyed by the callback's id.
BOOK = GrantBook("callbacks")

#: What a callback runs, as the Triggers page names it in "Not allowed to use …".
RUNS_LABEL = "Agent turn"


@dataclass
class Callback:
    """One registration: its id, the context its turn starts from, when it was saved, and who
    asked for it when the owner did not (``lasting_work.ASKED_BY``: the source of the message that
    started the turn that registered it, ``{}`` for the owner's own)."""

    id: str
    context_summary: str = ""
    registered_at: float = 0.0
    asked_by: dict[str, str] = field(default_factory=dict)

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
            # Read fail-closed: a record that cannot be read names someone no record names.
            asked_by=lasting_work.recorded(data.get(lasting_work.ASKED_BY)),
        )


def _path() -> Path:
    from personalclaw.config.loader import config_dir

    return Path(config_dir()) / CALLBACKS_FILE


@contextmanager
def _locked() -> Iterator[None]:
    """One writer at a time across processes: ``hook_register`` runs in the MCP server's process,
    and the owner's delete in the gateway's."""
    from personalclaw.durability.home_paths import open_lock

    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open_lock(path.parent / f"{CALLBACKS_FILE}.lock") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


#: Where the file keeps its registrations.
_CALLBACKS = record_files.Shape(key="callbacks")


def _read(*, strict: bool = False) -> dict[str, Callback]:
    """Every registration, by id. Never raises for a list: a file that cannot be read lists none
    (the read keeps a copy of it and says so once, and :func:`unreadable` says why), and an entry
    that is not a registration is left out and logged, so one bad write cannot stop the rest
    loading. A write reads it *strict*, and is refused with ``record_files.Unreadable`` rather
    than replace every registration it could not read with its one."""
    read = record_files.records if strict else record_files.records_or_empty
    entries = read(_path(), _CALLBACKS)
    out: dict[str, Callback] = {}
    for entry in entries:
        callback = Callback.from_dict(entry) if isinstance(entry, dict) else None
        if callback is None:
            logger.warning("callbacks: skipping an entry that is not a callback: %r", entry)
            continue
        out[callback.id] = callback
    return out


def _write(callbacks: dict[str, Callback]) -> None:
    from personalclaw.atomic_write import atomic_json_write

    def _entry(callback: Callback) -> dict:
        entry = asdict(callback)
        # The owner's own registration records nobody (`lasting_work.ASKED_BY`).
        if not entry.get(lasting_work.ASKED_BY):
            entry.pop(lasting_work.ASKED_BY, None)
        return entry

    atomic_json_write(_path(), {"version": 1, "callbacks": [_entry(c) for c in callbacks.values()]})


def list_all() -> list[Callback]:
    return list(_read().values())


def unreadable() -> record_files.Unreadable | None:
    """Why the registry cannot be read now, or None when it can (or is not there): what the
    Triggers page says in place of a list that would read as "no callbacks"."""
    try:
        _read(strict=True)
    except record_files.Unreadable as found:
        return found
    return None


def get(callback_id: str) -> Callback | None:
    return _read().get(callback_id)


def register(callback_id: str, context_summary: str) -> Callback:
    """Save *callback_id* with *context_summary*, replacing an earlier registration of it.

    What the chat's ``hook_register`` does. It gives nothing: a new callback is not allowed to run,
    and one re-registered with other context no longer matches the yes it had (`allowed`).

    Refused, before anything is written, for the work of an Incognito or Temporary chat: the
    context would be kept after the chat and start a turn of its own (`lasting_work`). One
    registered for a turn someone other than the owner asked for records who did
    (``memory_writes.asker``), so its turn is held to them, and its Allow says so (:func:`consent`).
    """
    lasting_work.refuse(lasting_work.CALLBACK, lasting_work.CREATE)
    from personalclaw import memory_writes

    asked = memory_writes.asker()
    with _locked():
        callbacks = _read(strict=True)
        callback = Callback(
            id=callback_id,
            context_summary=context_summary,
            registered_at=time.time(),
            asked_by=asked,
        )
        callbacks[callback_id] = callback
        _write(callbacks)
    return callback


def remove(callback_id: str) -> bool:
    """Delete a registration and the yes it had. The owner's delete on the Triggers page."""
    with _locked():
        callbacks = _read(strict=True)
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
    """The sentence the owner agrees to when they allow *callback* — product copy. One registered
    for a turn someone else asked for says who: its turn is held to them."""
    allowing = (
        f"Allowing “{callback.id}” lets an outside system that holds your webhook token start an "
        "agent turn with its tools, from the context the agent saved for this callback, as it is "
        "now."
    )
    if not callback.asked_by:
        return allowing
    from personalclaw.turn_source import named

    return (
        f"{allowing} It was registered in a turn {named(callback.asked_by)} asked for, so its turn "
        "changes none of your memory without your own word."
    )


#: The consent dialog's heading for :func:`consent` (`http_errors.consent_required`).
CONSENT_TITLE = "Allow this callback to run?"


def restored_context(callback: Callback, *, now: float | None = None) -> Admitted:
    """The saved context a callback's turn starts from, as its model is handed it, by how old it is.

    Under an hour it is used as written; up to a day it is marked as possibly outdated; after that
    it is too stale to steer a turn and is left out. The agent wrote it in an earlier turn from
    what that turn read, so it is text from outside by the time it comes back: masked of any
    secret it holds, then through the door every such text takes into a prompt
    (``outside_text.admit``), read by the injection screen and fenced as data with the callback
    as its source. The note on its age is PersonalClaw's own and stays outside the fence. One the
    screen refuses comes back as nothing, with the groups it was refused for, and its turn does
    not start (``dashboard.handlers.hooks.api_hooks_agent``).
    """
    from personalclaw.outside_text import Admitted, admit
    from personalclaw.security import redact_for_model

    if not callback.context_summary or not callback.registered_at:
        return Admitted()
    age_hours = ((now if now is not None else time.time()) - callback.registered_at) / 3600
    if age_hours > 24:
        return Admitted()
    admitted = admit(
        redact_for_model(callback.context_summary),
        source=f"callback:{callback.id}",
        source_type="callback_context",
        source_id=callback.id,
        transformation_path="restore",
    )
    if admitted.refused or age_hours <= 1:
        return admitted
    return Admitted(
        text=f"[Context from {age_hours:.0f}h ago — may be outdated]\n{admitted.text}",
        flagged=admitted.flagged,
    )
