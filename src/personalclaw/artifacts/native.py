"""Bundled on-disk artifact provider.

Persists each artifact under ``<root>/<slug>/`` (default root
``config_dir()/"artifacts"``):

    <slug>/meta.json        canonical metadata (Artifact.to_dict(persist=True))
    <slug>/current.html     latest live content (authoritative for chat-backed)
    <slug>/versions/vN.html immutable numbered snapshots (written on snapshot)

For file-backed artifacts (``source_path`` set) the live view reads/writes that
Workspace file directly — the artifact is a *naming + versioning + lifecycle*
layer over a single on-disk file, not a copy. Every read/write is gated by
``is_sensitive_path`` and re-checked to stay under the provider root.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from personalclaw.artifacts.models import (
    ALLOWED_EVENT_TYPES,
    MAX_BINARY_CONTENT_BYTES,
    MAX_CONTENT_BYTES,
    MAX_DESCRIPTION_LEN,
    MAX_EVENTS_PER_ARTIFACT,
    MAX_NAME_LEN,
    MAX_VERSIONS,
    Artifact,
    ArtifactEvent,
    clean_event_metadata,
    clean_tags,
    ext_for_mime,
    is_binary_kind,
    is_valid_slug,
    mime_for_ext,
    normalize_kind,
    normalize_source,
    slugify,
)
from personalclaw.artifacts.provider import ArtifactProvider
from personalclaw.atomic_write import atomic_write, atomic_write_bytes
from personalclaw.config.loader import config_dir
from personalclaw.security import is_sensitive_path

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class NativeArtifactProvider(ArtifactProvider):
    """Filesystem-backed artifact provider (the bundled default)."""

    def __init__(self, root: Path | str | None = None) -> None:
        self._root = Path(root) if root else (config_dir() / "artifacts")
        # Reentrant: update()/record_impression() re-enter via self.get() while
        # already holding the lock (a coarse single-instance guard).
        self._lock = threading.RLock()

    @property
    def name(self) -> str:
        return "native"

    @property
    def display_name(self) -> str:
        return "Local filesystem"

    # ── path helpers (security spine) ──

    def _ensure_root(self) -> Path:
        root = self._root
        if is_sensitive_path(str(root)):
            raise PermissionError("artifact root resolves to a sensitive path")
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _artifact_dir(self, slug: str) -> Path:
        if not is_valid_slug(slug):
            raise ValueError(f"invalid slug: {slug!r}")
        root = self._ensure_root()
        # Re-check the resolved path stays under root (defense in depth).
        if not (root / slug).resolve().is_relative_to(root.resolve()):
            raise ValueError(f"slug escapes artifact root: {slug!r}")
        return root / slug

    def _read_text(self, path: Path) -> str | None:
        if is_sensitive_path(str(path)):
            return None
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read(MAX_CONTENT_BYTES + 1)[:MAX_CONTENT_BYTES]
        except (OSError, ValueError):
            return None

    def _write_text(self, path: Path, content: str) -> bool:
        if is_sensitive_path(str(path)):
            return False
        try:
            atomic_write(path, content[:MAX_CONTENT_BYTES])
            return True
        except (OSError, ValueError):
            logger.warning("artifact write failed: %s", path, exc_info=True)
            return False

    # ── binary body I/O (kind:image et al — stored as raw bytes, not text) ──

    def _read_bytes(self, path: Path) -> bytes | None:
        if is_sensitive_path(str(path)):
            return None
        try:
            with open(path, "rb") as f:
                return f.read(MAX_BINARY_CONTENT_BYTES + 1)[:MAX_BINARY_CONTENT_BYTES]
        except OSError:
            return None

    def _write_bytes(self, path: Path, data: bytes) -> bool:
        if is_sensitive_path(str(path)):
            return False
        try:
            atomic_write_bytes(path, data[:MAX_BINARY_CONTENT_BYTES])
            return True
        except (OSError, ValueError):
            logger.warning("artifact binary write failed: %s", path, exc_info=True)
            return False

    def _body_filename(self, art: Artifact, version: int | None = None) -> str:
        """The on-disk body filename for an artifact, kind-aware.

        Text kinds use ``current.html`` / ``vN.html`` (unchanged). Binary kinds
        use ``current.<ext>`` / ``vN.<ext>`` where ext comes from the mime, so a
        PNG lands as ``current.png`` — never forced into ``.html``.
        """
        if is_binary_kind(art.kind):
            ext = ext_for_mime(art.mime)
        else:
            ext = "html"
        stem = f"v{version}" if version is not None else "current"
        return f"{stem}.{ext}"

    @staticmethod
    def _raw_ref(slug: str, version: int | None = None) -> str:
        """The reference stored in a binary artifact's ``content`` — the raw URL.

        The bytes never live in ``content`` (no base64-in-context); the renderer
        fetches them from this endpoint. A versioned read appends ``?version=N``.
        """
        base = f"/api/artifacts/{slug}/raw"
        return f"{base}?version={version}" if version is not None else base

    # ── live-pointer (file-backed source_path) ──

    def _try_read_source_path(self, source_path: str) -> str | None:
        """Read live content from a file-backed artifact's source path.

        Resolves symlinks/.. and refuses sensitive paths BEFORE reading; requires
        an absolute, existing, regular file; bounds the read at the file level so
        a huge source can't exhaust memory.
        """
        if not source_path:
            return None
        try:
            p = Path(source_path)
            if not p.is_absolute():
                return None
            resolved = p.resolve()
        except (OSError, ValueError):
            return None
        if is_sensitive_path(str(resolved)):
            return None
        if not resolved.is_file():
            return None
        try:
            with open(resolved, "r", encoding="utf-8", errors="replace") as f:
                return f.read(MAX_CONTENT_BYTES + 1)[:MAX_CONTENT_BYTES]
        except (OSError, ValueError):
            return None

    def _try_write_source_path(self, source_path: str, content: str) -> bool:
        """Write back to a file-backed artifact's source path.

        Refuses to CREATE a non-existent file (the Save-as-artifact flow always
        targets an existing file) and refuses sensitive/symlink-to-sensitive
        paths. Returns False (degrade to snapshot-only) on any failure.
        """
        if not source_path:
            return False
        try:
            p = Path(source_path)
            if not p.is_absolute():
                return False
            resolved = p.resolve()
        except (OSError, ValueError):
            return False
        if is_sensitive_path(str(resolved)):
            return False
        if not resolved.is_file():
            return False  # never create
        try:
            atomic_write(resolved, content[:MAX_CONTENT_BYTES])
            return True
        except (OSError, ValueError):
            logger.warning("artifact source_path write failed: %s", resolved, exc_info=True)
            return False

    # ── meta / version I/O ──

    def _meta_path(self, slug: str) -> Path:
        return self._artifact_dir(slug) / "meta.json"

    def _read_meta(self, slug: str) -> Artifact | None:
        path = self._meta_path(slug)
        raw = self._read_text(path)
        if raw is None:
            return None
        try:
            return Artifact.from_dict(json.loads(raw))
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.warning("corrupt artifact meta: %s", path)
            return None

    def _write_meta(self, art: Artifact) -> None:
        d = self._artifact_dir(art.slug)
        d.mkdir(parents=True, exist_ok=True)
        atomic_write(d / "meta.json", json.dumps(art.to_dict(persist=True), indent=2))

    def _current_content(self, slug: str) -> str | None:
        return self._read_text(self._artifact_dir(slug) / "current.html")

    def _version_content(self, slug: str, version: int) -> str | None:
        return self._read_text(self._artifact_dir(slug) / "versions" / f"v{version}.html")

    def _binary_version_path(self, slug: str, version: int) -> Path | None:
        """Resolve a binary version's on-disk body by globbing ``versions/v{n}.*``
        (excluding the text ``.html``). The extension on disk — not the artifact's
        current ``mime`` — is the per-version source of truth, so the lookup
        survives a mime change between versions (e.g. a PNG v1 edited into a JPEG
        v2): ``v1.png`` is still found after ``art.mime`` flips to image/jpeg."""
        vdir = self._artifact_dir(slug) / "versions"
        if not vdir.is_dir():
            return None
        for f in vdir.glob(f"v{version}.*"):
            if f.suffix.lower() != ".html":
                return f
        return None

    def _list_version_numbers(self, slug: str) -> list[int]:
        """Numbered snapshots present. Globs ``v*.*`` so it's body-extension
        agnostic — a binary image's ``v1.png`` counts the same as a ``v1.html``."""
        vdir = self._artifact_dir(slug) / "versions"
        if not vdir.is_dir():
            return []
        nums: list[int] = []
        for f in vdir.glob("v*.*"):
            try:
                nums.append(int(f.stem[1:]))
            except ValueError:
                continue
        return sorted(set(nums))

    def _prune_versions(self, slug: str) -> None:
        nums = self._list_version_numbers(slug)
        excess = len(nums) - MAX_VERSIONS
        if excess <= 0:
            return
        vdir = self._artifact_dir(slug) / "versions"
        for n in nums[:excess]:
            for f in vdir.glob(f"v{n}.*"):
                try:
                    f.unlink()
                except OSError:
                    pass

    def _snapshot_version(self, slug: str, version: int, content: str) -> None:
        vdir = self._artifact_dir(slug) / "versions"
        vdir.mkdir(parents=True, exist_ok=True)
        atomic_write(vdir / f"v{version}.html", content[:MAX_CONTENT_BYTES])
        self._prune_versions(slug)

    def _snapshot_binary(self, art: Artifact, version: int, data: bytes) -> None:
        vdir = self._artifact_dir(art.slug) / "versions"
        vdir.mkdir(parents=True, exist_ok=True)
        self._write_bytes(vdir / self._body_filename(art, version), data)
        self._prune_versions(art.slug)

    def _append_event(self, art: Artifact, event: ArtifactEvent) -> None:
        art.events.append(event)
        if len(art.events) > MAX_EVENTS_PER_ARTIFACT:
            art.events = art.events[-MAX_EVENTS_PER_ARTIFACT:]

    def _unique_slug(self, base: str) -> str:
        """Disambiguate foo / foo-2 / foo-3 against existing dirs."""
        root = self._ensure_root()
        if not (root / base).exists():
            return base
        n = 2
        while (root / f"{base}-{n}").exists():
            n += 1
        return f"{base}-{n}"

    @staticmethod
    def _live_dirty(live: str | None, latest_snapshot: str | None) -> bool:
        if live is None:
            return False
        return live != (latest_snapshot or "")

    # ── ABC methods ──

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
        root = self._ensure_root()
        with self._lock:
            slugs = [p.name for p in root.iterdir() if p.is_dir()] if root.exists() else []
        out: list[Artifact] = []
        for slug in slugs:
            art = self._read_meta(slug)
            if art is None:
                continue
            if tag and tag not in art.tags:
                continue
            if kind and art.kind != kind:
                continue
            if source and art.source != source:
                continue
            if source_path and art.source_path != source_path:
                continue
            if project_id and art.project_id != project_id:
                continue
            if q:
                hay = f"{art.name}\n{art.description}\n{' '.join(art.tags)}".lower()
                if q.lower() not in hay:
                    continue
            art.content = None  # list omits content
            out.append(art)
        out.sort(key=lambda a: a.updated_at or a.created_at, reverse=True)
        return out

    def find_by_source_path(self, source_path: str) -> Artifact | None:
        if not source_path:
            return None
        for art in self.list(source_path=source_path):
            return art
        return None

    def get(self, slug: str, *, version: int | None = None) -> Artifact | None:
        with self._lock:
            art = self._read_meta(slug)
            if art is None:
                return None
            # Binary kinds (image): content is the raw-URL REF, never the bytes
            # (no base64-in-content). live_dirty is meaningless for an immutable
            # generated binary, so it stays False.
            if is_binary_kind(art.kind):
                if version is not None:
                    if self._binary_version_path(slug, version) is None:
                        return None
                    art.content = self._raw_ref(slug, version)
                else:
                    art.content = self._raw_ref(slug)
                art.live_dirty = False
                return art
            if version is not None:
                content = self._version_content(slug, version)
                if content is None:
                    return None
                art.content = content
                art.live_dirty = False
                return art
            # Live view: disk for file-backed, else current.html.
            live = self._try_read_source_path(art.source_path) if art.source_path else None
            if live is None:
                live = self._current_content(slug)
            art.content = live
            nums = self._list_version_numbers(slug)
            latest_snap = self._version_content(slug, nums[-1]) if nums else None
            art.live_dirty = self._live_dirty(live, latest_snap)
            return art

    def raw_bytes(self, slug: str, *, version: int | None = None) -> tuple[bytes, str] | None:
        """Return ``(data, mime)`` for a binary artifact's body, or None.

        Backs ``GET /api/artifacts/{slug}/raw`` — the renderer/<img> fetches the
        actual image bytes from here rather than carrying them in JSON content.
        """
        with self._lock:
            art = self._read_meta(slug)
            if art is None or not is_binary_kind(art.kind):
                return None
            if version is not None:
                path = self._binary_version_path(slug, version)
                if path is None:
                    return None
                # The per-version mime comes from the snapshot's extension, not the
                # (mutable) art.mime — so a historical version serves its own type.
                mime = mime_for_ext(path.suffix, art.mime or "application/octet-stream")
            else:
                path = self._artifact_dir(slug) / self._body_filename(art)
                mime = art.mime or "application/octet-stream"
            data = self._read_bytes(path)
            if data is None:
                return None
            return (data, mime)

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
        """Create a BINARY artifact (kind:image): bytes stored on disk, content=raw ref.

        Mirrors :meth:`create` but the body is bytes — never text. The returned
        artifact's ``content`` is the raw-URL ref (what the API surfaces), so a
        caller embeds ``/api/artifacts/<slug>/raw`` rather than the bytes.
        """
        name = (name or "").strip()[:MAX_NAME_LEN] or "Untitled"
        with self._lock:
            base = slug.strip() if slug and is_valid_slug(slug.strip()) else slugify(name)
            final_slug = (
                base
                if (slug and is_valid_slug(base) and not (self._ensure_root() / base).exists())
                else self._unique_slug(base)
            )
            ts = _now()
            event = ArtifactEvent(
                ts=ts, type="created", by=actor or "", session_id=session_id or "", version=1
            )
            art = Artifact(
                slug=final_slug,
                name=name,
                kind=normalize_kind(kind) if is_binary_kind(kind) else "image",
                source=normalize_source(source),
                description=(description or "").strip()[:MAX_DESCRIPTION_LEN],
                tags=clean_tags(tags),
                version=1,
                created_at=ts,
                updated_at=ts,
                project_id=project_id or "",
                mime=mime or "image/png",
                events=[event],
            )
            d = self._artifact_dir(final_slug)
            d.mkdir(parents=True, exist_ok=True)
            self._write_bytes(d / self._body_filename(art), data)
            self._snapshot_binary(art, 1, data)
            self._write_meta(art)
            art.content = self._raw_ref(final_slug)
            return art

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
        """Append a new binary version (an edit result). Bumps version + snapshots."""
        if event_type == "reverted":
            raise ValueError("use revert() to restore a version, not update_binary()")
        if event_type is not None and event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError(f"invalid event_type: {event_type!r}")
        with self._lock:
            art = self._read_meta(slug)
            if art is None or not is_binary_kind(art.kind):
                return None
            if mime:
                art.mime = mime
            art.version += 1
            d = self._artifact_dir(slug)
            self._write_bytes(d / self._body_filename(art), data)
            self._snapshot_binary(art, art.version, data)
            ev = ArtifactEvent(
                ts=_now(),
                type=event_type or ("iterated" if actor == "agent" else "edited"),
                by=actor or "",
                session_id=session_id or "",
                version=art.version,
            )
            self._append_event(art, ev)
            art.updated_at = _now()
            self._write_meta(art)
            art.content = self._raw_ref(slug)
            return art

    def revert(
        self,
        slug: str,
        from_version: int,
        *,
        actor: str | None = None,
        session_id: str | None = None,
    ) -> Artifact | None:
        """Restore a historical version's body as a NEW current version.

        Kind-agnostic: the body is sourced from the on-disk snapshot (text
        ``vN.html`` or a binary ``vN.<ext>``) — never round-tripped through the
        caller. This is the one correct revert path for binary artifacts (the FE
        only holds a raw-URL ref, not the bytes) and is also cleaner for text
        (no stale client content). Emits a ``reverted`` event tagging the source
        version. Returns the live artifact, or None if slug/version is missing.
        """
        with self._lock:
            art = self._read_meta(slug)
            if art is None:
                return None
            if is_binary_kind(art.kind):
                src = self._binary_version_path(slug, from_version)
                if src is None:
                    return None
                data = self._read_bytes(src)
                if data is None:
                    return None
                # The restored body keeps the source version's own mime (its disk
                # extension), so reverting a JPEG v1 onto a now-PNG artifact lands
                # the bytes under the right extension + Content-Type.
                art.mime = mime_for_ext(src.suffix, art.mime or "image/png")
                art.version += 1
                d = self._artifact_dir(slug)
                self._write_bytes(d / self._body_filename(art), data)
                self._snapshot_binary(art, art.version, data)
            else:
                content = self._version_content(slug, from_version)
                if content is None:
                    return None
                art.version += 1
                d = self._artifact_dir(slug)
                self._write_text(d / "current.html", content)
                if art.source_path:
                    self._try_write_source_path(art.source_path, content)
                self._snapshot_version(slug, art.version, content)
            ev = ArtifactEvent(
                ts=_now(),
                type="reverted",
                by=actor or "",
                session_id=session_id or "",
                version=art.version,
                from_version=from_version,
            )
            self._append_event(art, ev)
            art.updated_at = _now()
            self._write_meta(art)
            return self.get(slug)

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
        name = (name or "").strip()[:MAX_NAME_LEN] or "Untitled"
        # Binary kinds (image) must go through create_binary — their body is bytes,
        # not text. Refuse here so a text body can't masquerade as an image.
        if is_binary_kind(kind):
            raise ValueError(f"kind {kind!r} is binary — use create_binary()")
        with self._lock:
            base = slug.strip() if slug and is_valid_slug(slug.strip()) else slugify(name)
            final_slug = base if (slug and is_valid_slug(base) and not (self._ensure_root() / base).exists()) else self._unique_slug(base)
            ts = _now()
            event = ArtifactEvent(
                ts=ts, type="created", by=actor or "", session_id=session_id or "", version=1
            )
            art = Artifact(
                slug=final_slug,
                name=name,
                kind=normalize_kind(kind),
                source=normalize_source(source),
                description=(description or "").strip()[:MAX_DESCRIPTION_LEN],
                tags=clean_tags(tags),
                version=1,
                created_at=ts,
                updated_at=ts,
                source_path=source_path or "",
                project_id=project_id or "",
                events=[event],
            )
            d = self._artifact_dir(final_slug)
            d.mkdir(parents=True, exist_ok=True)
            self._write_text(d / "current.html", content or "")
            self._snapshot_version(final_slug, 1, content or "")
            if source_path:
                self._try_write_source_path(source_path, content or "")
            self._write_meta(art)
            art.content = content
            return art

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
        # Validate event type BEFORE any side effect so an invalid type can't
        # orphan a versions/vN.html. 'reverted' is NOT an update event — it has its
        # own method (revert()) that restores a body server-side; routing it here
        # would (and did, for binary) write the wrong body.
        if event_type == "reverted":
            raise ValueError("use revert() to restore a version, not update()")
        if event_type is not None and event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError(f"invalid event_type: {event_type!r}")
        with self._lock:
            art = self._read_meta(slug)
            if art is None:
                return None

            # Metadata-only updates never bump a version or snapshot.
            meta_changed = False
            if name is not None:
                art.name = name.strip()[:MAX_NAME_LEN] or art.name
                meta_changed = True
            if description is not None:
                art.description = description.strip()[:MAX_DESCRIPTION_LEN]
                meta_changed = True
            if tags is not None:
                art.tags = clean_tags(tags)
                meta_changed = True

            wrote_content = False
            if content is not None:
                d = self._artifact_dir(slug)
                self._write_text(d / "current.html", content)
                if art.source_path:
                    self._try_write_source_path(art.source_path, content)
                wrote_content = True

            if snapshot:
                # Capture live state if no explicit content was passed.
                snap_content = content
                if snap_content is None:
                    snap_content = (
                        self._try_read_source_path(art.source_path) if art.source_path else None
                    )
                    if snap_content is None:
                        snap_content = self._current_content(slug) or ""
                art.version += 1
                self._snapshot_version(slug, art.version, snap_content)
                resolved_type = event_type or ("iterated" if actor == "agent" else "edited")
                ev = ArtifactEvent(
                    ts=_now(),
                    type=resolved_type,
                    by=actor or "",
                    session_id=session_id or "",
                    version=art.version,
                )
                self._append_event(art, ev)

            if snapshot or wrote_content or meta_changed:
                art.updated_at = _now()
                self._write_meta(art)

            # Return the live view (content + live_dirty) like get().
            return self.get(slug)

    def delete(self, slug: str) -> bool:
        with self._lock:
            try:
                d = self._artifact_dir(slug)
            except ValueError:
                return False
            if not d.is_dir():
                return False
            import shutil

            try:
                shutil.rmtree(d)
                return True
            except OSError:
                logger.warning("artifact delete failed: %s", d, exc_info=True)
                return False

    def list_versions(self, slug: str) -> list[int]:
        with self._lock:
            if self._read_meta(slug) is None:
                return []
            return self._list_version_numbers(slug)

    def record_impression(
        self,
        slug: str,
        *,
        by: str | None = None,
        session_id: str | None = None,
        message_ts: str | None = None,
        widget_index: int | None = None,
    ) -> tuple[Artifact | None, bool]:
        with self._lock:
            art = self._read_meta(slug)
            if art is None:
                return None, False
            if session_id:
                # Idempotent per session: suppress if this session already has
                # ANY lifecycle event on the artifact.
                if any(e.session_id == session_id for e in art.events):
                    return art, False
            ev = ArtifactEvent(
                ts=_now(),
                type="referenced",
                by=by or "",
                session_id=session_id or "",
                version=art.version,
                metadata=clean_event_metadata(
                    {"message_ts": message_ts or "", "widget_index": widget_index}
                    if message_ts or widget_index is not None
                    else {}
                ),
            )
            self._append_event(art, ev)
            self._write_meta(art)
            return art, True
