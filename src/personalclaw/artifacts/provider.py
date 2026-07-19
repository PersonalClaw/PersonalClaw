"""Abstract base for artifact providers.

Mirrors the Task/Prompt entity shape: a thin ABC over a backend (filesystem,
object store, remote service). The bundled :class:`NativeArtifactProvider` is the
on-disk implementation; the REST/MCP/CLI layers all dispatch through the
registry so a second backend drops in with no caller change.

The interface is synchronous: the native provider is filesystem I/O under a
coarse lock, and the in-process MCP tools call it directly. Async backends can
wrap their I/O; nothing in the call sites awaits a provider method.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from personalclaw.artifacts.models import Artifact


class ArtifactProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Unique provider identifier (e.g. 'native')."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable provider label."""
        ...

    @abstractmethod
    def list(
        self,
        *,
        tag: str | None = None,
        kind: str | None = None,
        q: str | None = None,
        source: str | None = None,
        source_path: str | None = None,
        project_id: str | None = None,
    ) -> list[Artifact]:
        """Return matching artifacts (without ``content``)."""
        ...

    @abstractmethod
    def get(self, slug: str, *, version: int | None = None) -> Artifact | None:
        """Return one artifact with ``content`` populated, or None.

        ``version=None`` returns the live view (disk for file-backed, else the
        latest ``current`` content); ``version=N`` returns the immutable snapshot.
        """
        ...

    @abstractmethod
    def create(
        self,
        *,
        name: str,
        content: str,
        kind: str = "widget",
        source: str = "chat",
        slug: str | None = None,
        source_path: str = "",
        description: str = "",
        tags: list[str] | None = None,
        actor: str | None = None,
        session_id: str | None = None,
        project_id: str = "",
    ) -> Artifact:
        ...

    @abstractmethod
    def update(
        self,
        slug: str,
        *,
        content: str | None = None,
        snapshot: bool = False,
        event_type: str | None = None,
        actor: str | None = None,
        session_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> Artifact | None:
        ...

    def revert(
        self,
        slug: str,
        from_version: int,
        *,
        actor: str | None = None,
        session_id: str | None = None,
    ) -> Artifact | None:
        """Restore a historical version's body as a new current version.

        Sourced server-side from the on-disk snapshot (kind-agnostic: text or
        binary), so the caller never round-trips content — the one correct path
        for binary artifacts. Default raises so a backend declares support
        explicitly; the native provider implements it."""
        raise NotImplementedError(f"{self.name} does not support revert")

    # ── binary bodies (kind:image et al — bytes on disk, not text) ──
    # Default raises so a text-only backend declares its limitation explicitly
    # rather than silently mishandling bytes; the native provider implements them.
    def create_binary(
        self,
        *,
        name: str,
        data: bytes,
        mime: str,
        kind: str = "image",
        source: str = "chat",
        slug: str | None = None,
        description: str = "",
        tags: list[str] | None = None,
        actor: str | None = None,
        session_id: str | None = None,
        project_id: str = "",
    ) -> Artifact:
        """Create a binary artifact (image). ``data`` is the raw bytes."""
        raise NotImplementedError(f"{self.name} does not support binary artifacts")

    def update_binary(
        self,
        slug: str,
        *,
        data: bytes,
        mime: str = "",
        actor: str | None = None,
        session_id: str | None = None,
        event_type: str | None = None,
    ) -> Artifact | None:
        """Append a new binary version (an edit result)."""
        raise NotImplementedError(f"{self.name} does not support binary artifacts")

    def raw_bytes(self, slug: str, *, version: int | None = None) -> tuple[bytes, str] | None:
        """Return ``(data, mime)`` for a binary artifact body, or None."""
        return None

    @abstractmethod
    def delete(self, slug: str) -> bool:
        ...

    @abstractmethod
    def list_versions(self, slug: str) -> list[int]:
        ...

    @abstractmethod
    def find_by_source_path(self, source_path: str) -> Artifact | None:
        """Return the artifact whose live-pointer matches ``source_path`` (dedup)."""
        ...

    @abstractmethod
    def record_impression(
        self,
        slug: str,
        *,
        by: str | None = None,
        session_id: str | None = None,
        message_ts: str | None = None,
        widget_index: int | None = None,
    ) -> tuple[Artifact | None, bool]:
        """Append a per-session ``referenced`` breadcrumb. Returns (artifact, appended)."""
        ...

    @property
    def readonly(self) -> bool:
        return False

    # Optional hook for metadata used by the UI; default no-op.
    def stats(self) -> dict[str, Any]:
        return {}
