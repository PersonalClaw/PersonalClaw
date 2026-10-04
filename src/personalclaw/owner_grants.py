"""The owner's yes to something an agent can write, kept where only the owner's surfaces write.

A trigger's grant lives on its row (`triggers.grants`). Three things run unattended from text or
a file an agent can write, and none of them is a trigger row: a callback's saved context
(`webhook_callbacks`, which the chat's ``hook_register`` writes), a HEARTBEAT.md task
(`heartbeat`), and a script the agent CLI's hooks run (`agent_hooks`). For each, the owner's yes
is recorded here, one book per kind at ``<home>/grants/<book>.json``, sealed to the SHA-256 of the
content as it stood when they said yes. A change to the content is a new question, the way an
edit to what a granted trigger runs is (`triggers.grants.narrow`).

🔴 ONLY THE OWNER'S SURFACES WRITE A BOOK, and each asks first (`http_errors.consent_required`).
`grants/` is an owner-only path (`owner_only`): the OS sandbox around the agent's shell refuses
the write, the tool-call screen refuses a call that names it, and no file root reaches into it.
A book that cannot be read grants nothing, and is never written over: a give is refused until it
can be read.
"""

from __future__ import annotations

import fcntl
import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from personalclaw import record_files

#: The directory under the home the books live in (an `owner_only.OWNER_ONLY_DIRS` entry).
GRANTS_DIR = "grants"


def seal(content: str | bytes) -> str:
    """The fingerprint a yes is recorded against: the SHA-256 of *content* (UTF-8 for text)."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(data).hexdigest()


def grants_dir() -> Path:
    from personalclaw.config.loader import config_dir

    return Path(config_dir()) / GRANTS_DIR


class GrantBook:
    """One kind's grants: ``key → the seal of what the owner allowed under it``."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def path(self) -> Path:
        from personalclaw.record_ids import record_path

        return record_path(grants_dir(), self._name, kind="grant book")

    def _read(self, *, strict: bool = False) -> dict[str, dict]:
        """The book's grants, by key.

        Fail closed: a book that cannot be read holds nothing for a question, so nothing it
        recorded runs until it can be read. And never written over: a write reads it *strict* and
        is refused with ``record_files.Unreadable``, because writing one yes over it would replace
        every other yes it holds. The failed read keeps a copy of it and says so, once.
        """
        try:
            data = record_files.read(self.path, dict)
            grants = data.get("grants") if data is not None else None
            if grants is not None and not isinstance(grants, dict):
                raise record_files.not_the_store(
                    self.path, 'it is JSON, but its "grants" is not an object'
                )
        except record_files.Unreadable:
            if strict:
                raise
            return {}
        return {
            k: v for k, v in (grants or {}).items() if isinstance(k, str) and isinstance(v, dict)
        }

    @contextmanager
    def _locked(self) -> Iterator[None]:
        from personalclaw.atomic_write import ensure_private_dir
        from personalclaw.durability.home_paths import open_lock

        ensure_private_dir(self.path.parent)
        with open_lock(self.path.parent / f".{self._name}.lock") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _write(self, grants: dict[str, dict]) -> None:
        from personalclaw.atomic_write import atomic_json_write

        atomic_json_write(self.path, {"version": 1, "grants": grants})

    def holds(self, key: str, content: str | bytes) -> bool:
        """Whether the owner allowed *key* with *content* exactly as it is now."""
        entry = self._read().get(key)
        return entry is not None and entry.get("seal") == seal(content)

    def give(self, key: str, content: str | bytes) -> None:
        """Record the owner's yes to *key* as *content* stands. Only an owner surface calls this,
        after its question."""
        with self._locked():
            grants = self._read(strict=True)
            grants[key] = {"seal": seal(content), "at": datetime.now(timezone.utc).isoformat()}
            self._write(grants)

    def revoke(self, key: str) -> None:
        """Forget the yes recorded under *key* (the thing it was for is gone). A book that cannot
        be read is left as it is: nothing in it is allowed meanwhile, and nothing is written."""
        with self._locked():
            grants = self._read()
            if grants.pop(key, None) is not None:
                self._write(grants)

    def keys(self) -> set[str]:
        return set(self._read())
