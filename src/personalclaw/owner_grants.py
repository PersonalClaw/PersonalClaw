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
A book that cannot be read grants nothing.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

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

    def _read(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            # Fail closed: an unreadable book is read as holding nothing, so nothing it recorded
            # runs until the owner allows it again.
            logger.warning("grants: %s is unreadable; nothing in it is allowed", self.path)
            return {}
        grants = data.get("grants") if isinstance(data, dict) else None
        if not isinstance(grants, dict):
            return {}
        return {k: v for k, v in grants.items() if isinstance(k, str) and isinstance(v, dict)}

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
            grants = self._read()
            grants[key] = {"seal": seal(content), "at": datetime.now(timezone.utc).isoformat()}
            self._write(grants)

    def revoke(self, key: str) -> None:
        """Forget the yes recorded under *key* (the thing it was for is gone)."""
        with self._locked():
            grants = self._read()
            if grants.pop(key, None) is not None:
                self._write(grants)

    def keys(self) -> set[str]:
        return set(self._read())
