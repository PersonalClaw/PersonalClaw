"""Artifact folders — the library's organizational tree (PEP-6).

A folder is pure organization: it names a place in the library side rail and
nothing else. It owns no content, so no folder operation may ever destroy or
rewrite an artifact — deleting a folder falls its members back to *unfiled*
(``folder_id == ""``), and renaming one touches no artifact record at all.

Shape mirrors the chat-folder store (``dashboard/chat_folders.py``): a flat list
of records with an opaque 12-char-hex id, ``parent_id`` nesting, ``order`` and an
``icon``. Flat + opaque-id (rather than a path string) is what makes a rename a
one-record write instead of a tree rewrite.

Persistence lives at ``<home>/artifacts/folders.json`` — inside the artifacts
tree so the existing ``artifacts`` durability inventory entry already covers it,
and safe alongside the per-artifact directories because the provider's ``list``
enumerates directories only.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from personalclaw.artifacts.provider import ArtifactProvider
from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir
from personalclaw.security import is_sensitive_path

logger = logging.getLogger(__name__)

MAX_FOLDER_NAME_LEN = 100
MAX_FOLDER_ICON_LEN = 3  # a single emoji (plus a variation selector / ZWJ)
MAX_FOLDERS = 500  # bounds the side rail + the JSON file

#: Sentinel for "the library root" — an artifact or folder with no parent.
ROOT = ""


@dataclass
class ArtifactFolder:
    """One node in the artifacts library tree."""

    id: str
    name: str
    parent_id: str = ROOT
    order: int = 0
    icon: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "parent_id": self.parent_id,
            "order": self.order,
            "icon": self.icon,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ArtifactFolder":
        return cls(
            id=str(d.get("id", "")),
            name=str(d.get("name", "")),
            parent_id=str(d.get("parent_id", "") or ""),
            order=int(d.get("order", 0) or 0),
            icon=str(d.get("icon", "")),
        )


#: Annotations inside ``ArtifactFolderStore`` go through these aliases because the class
#: defines its own ``list()`` method, which shadows the builtin ``list`` when annotations
#: are resolved in class scope.
_FolderRecords = list[ArtifactFolder]
_FolderIds = list[str]


class ArtifactFolderStore:
    """Flat-JSON store for the artifacts library folder tree.

    Reads re-load from disk on every call rather than caching, so a store
    constructed fresh against the same path sees everything a previous instance
    wrote (the reload contract PEP-6 requires) without a cache-invalidation seam.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self._root = Path(root) if root else (config_dir() / "artifacts")
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._root / "folders.json"

    # ── persistence ──

    def _load(self) -> _FolderRecords:
        path = self.path
        if is_sensitive_path(str(path)) or not path.is_file():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            logger.warning("corrupt artifact folders file: %s", path)
            return []
        if not isinstance(raw, list):
            return []
        out: _FolderRecords = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            folder = ArtifactFolder.from_dict(entry)
            if folder.id:
                out.append(folder)
        return out

    def _save(self, folders: _FolderRecords) -> None:
        root = self._root
        if is_sensitive_path(str(root)):
            raise PermissionError("artifact root resolves to a sensitive path")
        root.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps([f.to_dict() for f in folders], indent=2))

    # ── reads ──

    def list(self) -> _FolderRecords:
        """Every folder, ordered by ``order`` then name (stable side-rail order)."""
        with self._lock:
            folders = self._load()
        folders.sort(key=lambda f: (f.order, f.name.lower()))
        return folders

    def get(self, folder_id: str) -> ArtifactFolder | None:
        if not folder_id:
            return None
        with self._lock:
            return next((f for f in self._load() if f.id == folder_id), None)

    def exists(self, folder_id: str) -> bool:
        return self.get(folder_id) is not None

    def children(self, parent_id: str = ROOT) -> _FolderRecords:
        return [f for f in self.list() if f.parent_id == parent_id]

    def descendants(self, folder_id: str) -> _FolderIds:
        """Ids strictly below *folder_id* (breadth-first, cycle-safe)."""
        folders = self.list()
        out: _FolderIds = []
        frontier = [folder_id]
        seen = {folder_id}
        while frontier:
            current = frontier.pop(0)
            for f in folders:
                if f.parent_id == current and f.id not in seen:
                    seen.add(f.id)
                    out.append(f.id)
                    frontier.append(f.id)
        return out

    # ── validation ──

    @staticmethod
    def _clean_name(name: str) -> str:
        cleaned = (name or "").strip()[:MAX_FOLDER_NAME_LEN]
        if not cleaned:
            raise ValueError("folder name required")
        return cleaned

    @staticmethod
    def _clean_icon(icon: str) -> str:
        return (icon or "").strip()[:MAX_FOLDER_ICON_LEN]

    def _validate_parent(self, folders: _FolderRecords, folder_id: str, parent_id: str) -> str:
        """Refuse a nesting that can't exist. Raises before anything is written.

        Two refusals, both required by PEP-6: a parent that does not exist, and a
        parent that is the folder itself or one of its own descendants (a cycle
        would strand every folder in the loop out of the tree walk forever).
        """
        parent = (parent_id or "").strip()
        if not parent:
            return ROOT
        if not any(f.id == parent for f in folders):
            raise ValueError(f"parent folder not found: {parent!r}")
        if folder_id:
            if parent == folder_id:
                raise ValueError("a folder cannot be its own parent")
            # Walk up from the proposed parent: meeting ourselves means the move
            # would close a cycle.
            by_id = {f.id: f for f in folders}
            cursor: str | None = parent
            seen: set[str] = set()
            while cursor and cursor not in seen:
                seen.add(cursor)
                if cursor == folder_id:
                    raise ValueError("a folder cannot be nested inside its own descendant")
                node = by_id.get(cursor)
                cursor = node.parent_id if node else None
        return parent

    # ── writes ──

    def create(self, name: str, *, parent_id: str = ROOT, icon: str = "") -> ArtifactFolder:
        clean_name = self._clean_name(name)
        with self._lock:
            folders = self._load()
            if len(folders) >= MAX_FOLDERS:
                raise ValueError(f"folder limit reached ({MAX_FOLDERS})")
            parent = self._validate_parent(folders, "", parent_id)
            folder = ArtifactFolder(
                id=uuid.uuid4().hex[:12],
                name=clean_name,
                parent_id=parent,
                order=len(folders),
                icon=self._clean_icon(icon),
            )
            folders.append(folder)
            self._save(folders)
        return folder

    def update(
        self,
        folder_id: str,
        *,
        name: str | None = None,
        parent_id: str | None = None,
        order: int | None = None,
        icon: str | None = None,
    ) -> ArtifactFolder | None:
        """Rename / re-nest / reorder one folder record. Touches no artifact.

        Returns ``None`` when the folder does not exist; raises ``ValueError``
        for an invalid name or nesting (nothing is persisted in that case).
        """
        with self._lock:
            folders = self._load()
            folder = next((f for f in folders if f.id == folder_id), None)
            if folder is None:
                return None
            new_name = self._clean_name(name) if name is not None else None
            new_parent = (
                self._validate_parent(folders, folder_id, parent_id)
                if parent_id is not None
                else None
            )
            if new_name is not None:
                folder.name = new_name
            if new_parent is not None:
                folder.parent_id = new_parent
            if order is not None:
                folder.order = int(order)
            if icon is not None:
                folder.icon = self._clean_icon(icon)
            self._save(folders)
        return folder

    def _remove_record(self, folder_id: str) -> bool:
        """Drop one folder record, reparenting its child folders to the root.

        Private on purpose: deleting a folder without also unfiling its member
        artifacts would leave records pointing at an id that no longer resolves,
        so ``delete_folder`` is the only door.
        """
        with self._lock:
            folders = self._load()
            if not any(f.id == folder_id for f in folders):
                return False
            for f in folders:
                if f.parent_id == folder_id:
                    f.parent_id = ROOT
            self._save([f for f in folders if f.id != folder_id])
        return True


def delete_folder(
    store: ArtifactFolderStore, provider: ArtifactProvider, folder_id: str
) -> tuple[bool, int]:
    """Delete *folder_id*: members fall back to unfiled, child folders to the root.

    The single deletion entry point, and deliberately NOT a cascade — a folder is
    organization, so no artifact may be destroyed by removing one. Returns
    ``(deleted, unfiled_count)``.

    Unfiling runs BEFORE the record is dropped: if the process dies between the
    two steps the folder still exists and its members are merely unfiled, which
    is a legible state. The reverse order would leave members pointing at a
    vanished id.
    """
    if not store.exists(folder_id):
        return False, 0
    unfiled = 0
    for art in provider.list(folder=folder_id):
        if provider.set_folder(art.slug, ROOT) is not None:
            unfiled += 1
    return store._remove_record(folder_id), unfiled
