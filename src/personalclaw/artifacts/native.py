"""Bundled on-disk artifact provider.

Persists each artifact under ``<root>/<slug>/`` (default root
``config_dir()/"artifacts"``):

    <slug>/meta.json        canonical metadata (Artifact.to_dict(persist=True))
    <slug>/current.html     latest live content (authoritative for chat-backed)
    <slug>/versions/vN.html immutable numbered snapshots (written on snapshot)

For file-backed artifacts (``source_path`` set) the live view reads/writes that
Workspace file directly — the artifact is a *naming + versioning + lifecycle*
layer over a single on-disk file, not a copy. The pointer is refused when it is set
unless ``source_files`` admits it, and every read/write of it re-checks it there.
The artifact's own files are gated by ``is_sensitive_path`` and re-checked to stay
under the provider root.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from personalclaw.artifacts import changes, source_files
from personalclaw.artifacts.bases import BodyChange, newest_body_change
from personalclaw.artifacts.deploy import ArtifactDeployStore
from personalclaw.artifacts.models import (
    ALLOWED_EVENT_TYPES,
    BINARY_KINDS,
    MAX_BINARY_CONTENT_BYTES,
    MAX_CONTENT_BYTES,
    MAX_DESCRIPTION_LEN,
    MAX_EVENTS_PER_ARTIFACT,
    MAX_NAME_LEN,
    MAX_VERSIONS,
    Artifact,
    ArtifactEvent,
    ArtifactKindMismatch,
    ArtifactStaleWrite,
    ArtifactVersionConflict,
    clean_event_metadata,
    clean_tags,
    ext_for_mime,
    is_binary_kind,
    is_valid_slug,
    kind_for_mime,
    mime_for_ext,
    normalize_kind,
    normalize_source,
    redacted,
    slugify,
)
from personalclaw.artifacts.provider import ArtifactProvider
from personalclaw.atomic_write import atomic_write, atomic_write_bytes
from personalclaw.config import loader as config_loader
from personalclaw.security import (
    MaskConflict,
    is_sensitive_path,
    keep_masked_spans,
    stored_name,
)
from personalclaw.stale_write import revision_of


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)

#: The only companion-file names ``store_version_file`` accepts: ``<stem>@<hex>[.ext]``. Anchored to
#: the ``@`` so a companion can never collide with a ``vN.<ext>`` snapshot — the two live in one
#: directory, and a name in both namespaces would let a media copy be pruned as a version (or
#: overwrite one).
_MEDIA_NAME_RE = re.compile(r"[\w.\-]+@[0-9a-f]{6,64}(\.[A-Za-z0-9]{1,12})?")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _refuse_if_readonly(art: Artifact) -> None:
    """Guard every content-mutating store method against a frozen artifact (SM-9).

    Enforced in the STORE, not the route, because the route is not the only caller: the
    MCP artifact tools and the workflow action providers reach the provider directly, so a
    check in ``handlers.py`` would leave the model an unguarded door to the same edit. The
    three mutating methods are enumerated exhaustively — ``update``, ``update_binary``,
    ``revert`` — rather than inferred, so adding a fourth means adding a guard here.

    ``PermissionError`` specifically: ``handlers.py`` already maps it (and ``ValueError``)
    to a 400 with the message, so a refusal surfaces as "this artifact is read-only"
    instead of the misleading 404 a ``None`` return would produce.
    """
    if art.readonly:
        raise PermissionError(
            f"artifact {art.slug!r} is read-only — it is a frozen record, not a document"
        )


def _kept_text(kind: str, content: str) -> str:
    """The text the store keeps as the body of an artifact of *kind*: a ``csv`` artifact's with
    each cell written by the CSV rule (``documents.writers.csv_writer.render_csv_text``), so no
    cell opens as a formula in a spreadsheet; any other kind's as it was given.

    A CSV artifact is a file a spreadsheet opens, and its text comes from whoever saved it (the
    agent, an app, a workflow or the owner), often copied from a page or a message. Kept in the
    STORE for the reason :func:`_refuse_if_readonly` is: the route is not the only caller, and the
    agent's tools and a workflow's steps reach the provider directly. Text the ``csv`` module
    cannot read is refused with ``ValueError``, which every caller answers with its message,
    before anything is written.
    """
    if kind != "csv":
        return content
    import csv

    from personalclaw.documents.writers.csv_writer import render_csv_text

    try:
        return render_csv_text(content)
    except csv.Error as exc:
        raise ValueError(f"could not read the csv text: {exc}") from exc


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

    @property
    def root(self) -> Path:
        """The artifacts tree this provider owns. Public so the folder store can be
        pointed at the SAME tree (a test provider on a tmp root must not have its
        folders land in the real home)."""
        return self._root

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
    def _raw_ref(slug: str, version: int) -> str:
        """The reference stored in a binary artifact's ``content`` — the raw URL of one version.

        The bytes never live in ``content`` (no base64-in-context); the renderer fetches them from
        this endpoint. 🔴 The reference names its version, the current one's too. A page keeps the
        image it loaded from a URL for as long as the page lives, whatever the response's cache
        headers say, and the current read used to hand out the one unversioned ``/raw`` for every
        version: an open artifact re-read its new version and went on showing the old picture.
        Each version's URL is its own and immutable; the unversioned ``/raw`` still serves the
        current body to a download.
        """
        return f"/api/artifacts/{slug}/raw?version={version}"

    # ── live-pointer (file-backed source_path) ──

    def _try_read_source_path(
        self, source_path: str, places: Sequence[str] | None = None
    ) -> str | None:
        """Read live content from a file-backed artifact's source path.

        Re-checks the pointer BEFORE reading (``source_files.admitted``: symlinks and
        ``..`` resolved, inside the places an artifact may point, no credential file);
        requires an existing regular file; bounds the read at the file level so a huge
        source can't exhaust memory. A refused pointer reads as absent, so the caller
        falls back to the artifact's own saved copy. *places* are
        ``source_files.places()`` when the caller already resolved them.
        """
        admitted = source_files.admitted(source_path, places)
        if admitted is None:
            return None
        resolved = Path(admitted)
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
        targets an existing file) and re-checks the pointer first, as the read does.
        Returns False (degrade to snapshot-only) on any failure.
        """
        admitted = source_files.admitted(source_path)
        if admitted is None:
            return False
        resolved = Path(admitted)
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

    def _live_body(self, art: Artifact, places: Sequence[str] | None = None) -> str | None:
        """The body a live read of *art* shows — what ``get()`` hands out as ``content``.

        The raw-URL ref for a binary kind; for a file-backed artifact the file it points at, or
        ``current.html`` when that file cannot be read; else ``current.html``. One definition,
        because a body save's precondition (``update(expect_revision=…)``) must be compared
        against exactly what the read it was built from showed. *places* as
        :meth:`_try_read_source_path` takes them.
        """
        if is_binary_kind(art.kind):
            return self._raw_ref(art.slug, art.version)
        live = self._try_read_source_path(art.source_path, places) if art.source_path else None
        return live if live is not None else self._current_content(art.slug)

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

    def store_version_file(self, slug: str, filename: str, data: bytes) -> bool:
        """Land one content-addressed companion file in ``<slug>/versions/``.

        The name MUST carry the ``<stem>@<hash><ext>`` shape the publish path mints. That is a
        structural guard, not cosmetics: a companion accepted as ``v3.html`` would overwrite a
        version snapshot, and one accepted as ``v3.png`` would be counted as a snapshot by
        ``_list_version_numbers`` and pruned as one. The ``@`` cannot appear in a snapshot name, so
        requiring it makes the two namespaces disjoint by construction.

        Idempotent by content address: a re-publish referencing the same unchanged file writes the
        same bytes to the same name, so the copy is made once however many versions cite it.
        """
        if not _MEDIA_NAME_RE.fullmatch(filename):
            return False
        vdir = self._artifact_dir(slug) / "versions"
        if not vdir.parent.is_dir():
            return False
        vdir.mkdir(parents=True, exist_ok=True)
        dest = vdir / filename
        if dest.exists():
            return True
        return self._write_bytes(dest, data)

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

    def _start_unpublished(self, slug: str) -> None:
        """A new artifact never inherits a deployment.

        Its slug's folder did not exist, so a row the deploy registry still holds for the
        slug was left by an artifact that is gone — one removed around the store (by hand,
        by another program) — and serving it would publish this artifact at that row's URL
        without anyone deploying it. Raises when that cannot be recorded, so nothing is made.
        """
        ArtifactDeployStore(self._root).teardown(slug)

    @staticmethod
    def _live_dirty(live: str | None, latest_snapshot: str | None) -> bool:
        if live is None:
            return False
        return live != (latest_snapshot or "")

    def _body_author(self, art: Artifact, live: str | None) -> BodyChange | None:
        """Who made *art*'s live body *live* (``bases.newest_body_change``).

        A file-backed text artifact's body is its file, which any program can write: when the file
        no longer matches what this store last wrote through to it, the body changed outside the
        store and no event names who changed it.
        """
        if art.source_path and live is not None and not is_binary_kind(art.kind):
            if live != (self._current_content(art.slug) or ""):
                return BodyChange(kind="edited", version=art.version, outside=True)
        return newest_body_change(art)

    def newest_change(self, slug: str) -> BodyChange | None:
        """Who made *slug*'s live body, or ``None`` when no such artifact (or no change) exists.

        What a refusal of a stale write names as the writer of the version it would have replaced.
        """
        with self._lock:
            art = self._read_meta(slug)
            if art is None:
                return None
            return self._body_author(art, self._live_body(art))

    def _keep_unversioned(
        self, art: Artifact, live: str | None, content: str, actor: str | None
    ) -> bool:
        """Before a write replaces *live*, keep it as a version when another writer left it and
        no version holds it. Returns whether a version was cut.

        An edit the owner saved without a new version (a plain Save), an app's plain save or a
        change to an artifact's file lives only in the live body, and whatever writes next erases
        it. A write built from a copy of the body names that copy's base and is refused when it
        is stale, but a current base proves only that the writer read the edit, not that what it
        writes kept it, and a chat's Trust can approve the write with nobody looking. A
        workflow's write is built from no copy at all: its step writes what its run made, over
        whatever the artifact holds. So the edit is cut as its own version first, credited to
        whoever made it, and the write lands on top: nothing anyone wrote is lost, Revert brings
        it back, and the timeline says whose each version is. A body this writer left itself (a
        dashboard's last refresh) is replaced as it is, so a refresh every few minutes does not
        bury the history in versions. The owner's own save from her editor is the caller's to
        leave out: it names the revision the editor showed her, and a plain Save keeps the
        version number.
        """
        if live is None or content == live:
            return False  # nothing is replaced
        nums = self._list_version_numbers(art.slug)
        latest = self._version_content(art.slug, nums[-1]) if nums else None
        if latest is not None and live == latest:
            return False  # a version holds it already
        author = self._body_author(art, live)
        if author is not None and not author.outside and author.by == (actor or ""):
            return False  # this writer's own text
        art.version += 1
        self._snapshot_version(art.slug, art.version, live)
        # Whose text it keeps, and the run that wrote it when a workflow's step did.
        kept = {"kept_before": actor or ""}
        if author is not None and author.run_id:
            kept["run_id"] = author.run_id
        self._append_event(
            art,
            ArtifactEvent(
                ts=_now(),
                type="edited",
                by=author.by if author is not None else "",
                session_id=author.session_id if author is not None else "",
                version=art.version,
                metadata=clean_event_metadata(kept),
            ),
        )
        return True

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
        collection: str | None = None,
        folder: str | None = None,
    ) -> list[Artifact]:
        # Unfiltered listings stay content-free (#630): rows come off _read_meta,
        # which never persists `live_dirty`, so the cheap library read does no
        # per-artifact body I/O. A text query is the deliberate exception because
        # the artifact card shows body content; searching less than the card shows
        # makes visible text unfindable. Even then, metadata/collection matches
        # avoid the body read, and list-shaped responses still omit content.
        root = self._ensure_root()
        with self._lock:
            slugs = [p.name for p in root.iterdir() if p.is_dir()] if root.exists() else []
        needle = q.casefold() if q else ""
        # Resolved at most once per search: each body read re-checks its pointer against
        # these, and resolving them per artifact made a search cost a root walk per row.
        places: Sequence[str] | None = None
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
            if collection and art.collection != collection:
                continue
            # Present-vs-absent, unlike every filter above: None = all folders,
            # "" = only unfiled, id = that folder. `if folder and ...` would make
            # the unfiled bucket unaskable.
            if folder is not None and art.folder_id != folder:
                continue
            if needle:
                # `slug` is in here because it is the artifact's IDENTITY — it is what the
                # URL shows and therefore what a user pastes back into the search box, so a
                # slug-shaped query returning nothing reads as a broken search rather than
                # a scoping choice (#421).
                metadata = (
                    f"{art.name}\n{art.slug}\n{art.description}\n"
                    f"{' '.join(art.tags)}\n{art.collection or ''}"
                ).casefold()
                if needle not in metadata:
                    if places is None and art.source_path:
                        places = source_files.places()
                    detail = self._get(art.slug, places=places)
                    if detail is None or needle not in (detail.content or "").casefold():
                        continue
            art.content = None  # list omits content
            out.append(art)
        out.sort(key=lambda a: a.updated_at or a.created_at, reverse=True)
        return out

    def find_similar(
        self, name: str, *, kind: str | None = None, project_id: str | None = None
    ) -> Artifact | None:
        """The most-recent existing artifact whose name matches *name* by slug — the
        list-before-save dedup hint (ARTIFACTS S1). Same slug derivation as save, so a
        re-save of "Sales Dashboard" finds the prior one instead of minting a ``-2``.
        Returns None when nothing matches. A read-only scan; never raises into save.

        Present-vs-absent, like ``list``'s ``folder`` above: None = every Project (the
        default, so an unscoped caller keeps today's behaviour), "" = only *unscoped*
        artifacts, an id = that Project.

        Filtered HERE rather than forwarded into ``list``, and that is the whole fix:
        ``list``'s ``project_id`` is TRUTHY, so ``list(project_id="")`` means "no filter"
        and an unscoped save would still dedup across every Project — the half-fix that
        leaves #3309 alive. ``list``'s spelling cannot simply be flipped either: it is
        reached straight off the REST query string (``handlers.py``), where a present-but-
        empty ``?project_id=`` means "all" to today's clients, and adding a second
        present-vs-absent parameter beside it would mint two spellings of one filter.
        """
        target = slugify(name or "")
        if not target:
            return None
        try:
            for art in self.list(kind=kind):  # newest-first
                if project_id is not None and art.project_id != project_id:
                    continue
                if slugify(art.name) == target or art.slug == target:
                    return art
        except Exception:
            return None
        return None

    def find_by_source_path(self, source_path: str) -> Artifact | None:
        if not source_path:
            return None
        for art in self.list(source_path=source_path):
            return art
        return None

    def get(self, slug: str, *, version: int | None = None) -> Artifact | None:
        return self._get(slug, version=version)

    def _get(
        self, slug: str, *, version: int | None = None, places: Sequence[str] | None = None
    ) -> Artifact | None:
        """:meth:`get`, with the places a pointer may name already resolved by the caller."""
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
                    art.version = version
                else:
                    art.content = self._raw_ref(slug, art.version)
                art.live_dirty = False
                return art
            if version is not None:
                content = self._version_content(slug, version)
                if content is None:
                    return None
                art.content = content
                art.version = version
                art.live_dirty = False
                return art
            # Live view: disk for file-backed, else current.html.
            live = self._live_body(art, places)
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
        tags: list[str] | None = None,  # type: ignore[valid-type]  # CI-1
        actor: str | None = None,
        session_id: str | None = None,
        project_id: str = "",
        event_metadata: dict | None = None,
        source_path: str = "",
    ) -> Artifact:
        """Create a BINARY artifact (kind:image): bytes stored on disk, content=raw ref.

        Mirrors :meth:`create` but the body is bytes — never text. The returned
        artifact's ``content`` is the raw-URL ref (what the API surfaces), so a
        caller embeds ``/api/artifacts/<slug>/raw?version=1`` rather than the bytes.

        ``source_path`` is the file a copy was saved from (Files' "Save as artifact"), admitted
        by ``source_files`` like a text artifact's pointer. For a binary artifact it is where the
        bytes came from, never a live pointer: every read serves the artifact's own versions, and
        nothing writes the file.
        """
        name = (name or "").strip()[:MAX_NAME_LEN] or "Untitled"
        # A non-binary kind reaching here is a PROGRAMMING ERROR, so it raises. This
        # used to coerce silently to "image" (`normalize_kind(kind) if is_binary_kind(kind)
        # else "image"`), which is how every generated video ended up stored as an image
        # (issue #94): `kind="video"` is in neither ALLOWED_KINDS nor BINARY_KINDS, so the
        # else-branch swallowed it. Failing loudly is what stops that class recurring as
        # new binary kinds are added.
        if not is_binary_kind(kind):
            raise ValueError(
                f"create_binary got non-binary kind {kind!r}; "
                f"expected one of {sorted(BINARY_KINDS)} — register the kind in both "
                "ALLOWED_KINDS and BINARY_KINDS first"
            )
        offered = kind_for_mime(mime)
        if offered and offered != normalize_kind(kind):
            raise ArtifactKindMismatch(slug or name, kind, offered)
        # Admitted before any side effect, exactly as `create` admits a text artifact's pointer.
        origin = source_files.admit(source_path.strip()) if source_path.strip() else ""
        with self._lock:
            base = slug.strip() if slug and is_valid_slug(slug.strip()) else slugify(name)
            final_slug = (
                base
                if (slug and is_valid_slug(base) and not (self._ensure_root() / base).exists())
                else self._unique_slug(base)
            )
            self._start_unpublished(final_slug)
            ts = _now()
            event = ArtifactEvent(
                ts=ts,
                type="created",
                by=actor or "",
                session_id=session_id or "",
                version=1,
                metadata=clean_event_metadata(event_metadata or {}),
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
                project_id=project_id or "",
                mime=mime or "image/png",
                source_path=origin,
                events=[event],
            )
            d = self._artifact_dir(final_slug)
            d.mkdir(parents=True, exist_ok=True)
            self._write_bytes(d / self._body_filename(art), data)
            self._snapshot_binary(art, 1, data)
            self._write_meta(art)
            art.content = self._raw_ref(final_slug, art.version)
        changes.emit(changes.UPSERT, art.slug)
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
        expect_version: int | None = None,
    ) -> Artifact | None:
        """Append a new binary version (an edit result). Bumps version + snapshots.

        ``expect_version`` (see the protocol docstring) is compared INSIDE the lock and
        before a single byte is written, so a conflicting write cannot slip between the
        check and the store.

        The body must be of the artifact's own kind (:class:`ArtifactKindMismatch`): a text
        artifact takes no bytes, and a *mime* naming another binary kind (a PDF for an image) is
        not this artifact's next version. Another format of the same kind is (a PNG edited into a
        JPEG).
        """
        if event_type == "reverted":
            raise ValueError("use revert() to restore a version, not update_binary()")
        if event_type is not None and event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError(f"invalid event_type: {event_type!r}")
        with self._lock:
            art = self._read_meta(slug)
            if art is None:
                return None
            offered = kind_for_mime(mime)
            if not is_binary_kind(art.kind) or (offered and offered != art.kind):
                raise ArtifactKindMismatch(slug, art.kind, offered or "binary")
            _refuse_if_readonly(art)
            if expect_version is not None and art.version != expect_version:
                raise ArtifactVersionConflict(slug, art.version, expect_version)
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
            art.content = self._raw_ref(slug, art.version)
        changes.emit(changes.UPSERT, slug)
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
            _refuse_if_readonly(art)
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
                # A version saved before CSV text was kept by the rule is restored by it now.
                content = _kept_text(art.kind, content)
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
            reverted = self.get(slug)
        changes.emit(changes.UPSERT, slug)
        return reverted

    def create(
        self,
        *,
        name: str,
        content: str | None = None,
        kind: str = "widget",
        source: str = "chat",
        slug: str | None = None,
        source_path: str = "",
        description: str = "",
        tags: list[str] | None = None,  # type: ignore[valid-type]  # CI-1
        actor: str | None = None,
        session_id: str | None = None,
        project_id: str = "",
        collection: str = "",
        event_metadata: dict | None = None,
        readonly: bool = False,
    ) -> Artifact:
        name = (name or "").strip()[:MAX_NAME_LEN] or "Untitled"
        # Files → Save as artifact sends the editor's draft, which is the masked read of the very
        # file this artifact points at, and the content is written into that file below. Each
        # marker is put back from the file first, so saving a file never masks its own keys.
        if source_path and content is not None:
            on_disk = self._try_read_source_path(source_path)
            if on_disk is not None:
                content = keep_masked_spans(content, on_disk)
        # Binary kinds (image) must go through create_binary — their body is bytes,
        # not text. Refuse here so a text body can't masquerade as an image.
        if is_binary_kind(kind):
            raise ValueError(f"kind {kind!r} is binary — use create_binary()")
        if content is not None:
            content = _kept_text(normalize_kind(kind), content)
        # Refused here, before anything is written, rather than trusted from the route: the loop
        # watchdog creates file-backed artifacts through this method too. Stored resolved.
        pointer = (source_path or "").strip()
        if pointer:
            pointer = source_files.admit(pointer)
        with self._lock:
            base = slug.strip() if slug and is_valid_slug(slug.strip()) else slugify(name)
            final_slug = (
                base
                if (slug and is_valid_slug(base) and not (self._ensure_root() / base).exists())
                else self._unique_slug(base)
            )
            self._start_unpublished(final_slug)
            ts = _now()
            event = ArtifactEvent(
                ts=ts,
                type="created",
                by=actor or "",
                session_id=session_id or "",
                version=1,
                metadata=clean_event_metadata(event_metadata or {}),
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
                source_path=pointer,
                project_id=project_id or "",
                collection=(collection or "").strip()[:MAX_NAME_LEN],
                readonly=bool(readonly),
                events=[event],
            )
            # 🔴 NO TEXT IS NOT AN EMPTY BODY. A file-backed artifact made without text of its own
            # starts as its file, read here under the lock, and the file is never written: reading
            # the missing text as "" wrote "" through and wiped the file it points at. Only a body
            # the caller sent is written through.
            if content is not None:
                body = content
            else:
                body = (self._try_read_source_path(pointer) if pointer else None) or ""
            d = self._artifact_dir(final_slug)
            d.mkdir(parents=True, exist_ok=True)
            self._write_text(d / "current.html", body)
            self._snapshot_version(final_slug, 1, body)
            if pointer and content is not None:
                self._try_write_source_path(pointer, content)
            self._write_meta(art)
            # Echo the body as a read of it shows it, not the raw input: what reached disk is
            # sliced to MAX_CONTENT_BYTES, so an over-cap body would otherwise report success at
            # full size while only the first MiB was kept, and a read takes its line breaks as
            # written (a CSV's CRLF reads as LF), so the base this answer hands the agent
            # (`artifacts.bases`) would name a body no read of it shows.
            shown = self._live_body(art)
            art.content = shown if shown is not None else body[:MAX_CONTENT_BYTES]
        # Mirroring observes the write from OUTSIDE the
        # lock: a listener reads the artifact back, and holding the store lock across an
        # index would serialize every concurrent save behind someone else's indexing.
        changes.emit(changes.UPSERT, art.slug)
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
        tags: list[str] | None = None,  # type: ignore[valid-type]  # CI-1
        collection: str | None = None,
        event_metadata: dict | None = None,
        source_path: str | None = None,
        expect_revision: str | None = None,
        add_tags: list[str] | None = None,  # type: ignore[valid-type]  # CI-1
        remove_tags: list[str] | None = None,  # type: ignore[valid-type]  # CI-1
    ) -> Artifact | None:
        # Validate event type BEFORE any side effect so an invalid type can't
        # orphan a versions/vN.html. 'reverted' is NOT an update event — it has its
        # own method (revert()) that restores a body server-side; routing it here
        # would (and did, for binary) write the wrong body.
        if event_type == "reverted":
            raise ValueError("use revert() to restore a version, not update()")
        if event_type is not None and event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError(f"invalid event_type: {event_type!r}")
        # A new pointer is admitted before any side effect, exactly as in create(); "" detaches.
        pointer: str | None = None
        if source_path is not None:
            pointer = source_path.strip()
            if pointer:
                pointer = source_files.admit(pointer)
        with self._lock:
            art = self._read_meta(slug)
            if art is None:
                return None
            _refuse_if_readonly(art)
            # 🔴 A BINARY ARTIFACT TAKES NO TEXT BODY. Its versions are bytes of its kind, written by
            # `update_binary` and `revert`. A text body here was filed as `current.html` and
            # `versions/vN.html` beside them: the metadata counted a version whose bytes the raw
            # read never served. Refused before anything is written, and first, because no
            # revision could make it a body this artifact can hold.
            binary = is_binary_kind(art.kind)
            if content is not None and binary:
                raise ArtifactKindMismatch(slug, art.kind, "text")
            # Read once: the precondition and the restore below are both taken of this one copy.
            live: str | None = None
            if content is not None or expect_revision is not None:
                live = self._live_body(art)
            # 🔴 A BODY BUILT FROM A STALE COPY IS REFUSED BEFORE ANYTHING IS WRITTEN — under the
            # lock the agent's `artifact_update` (an executor thread) and every other writer take,
            # so nothing can land between this comparison and the write below.
            if expect_revision is not None:
                current = redacted(live)
                if revision_of(current) != expect_revision:
                    raise ArtifactStaleWrite(slug, current)
            # Every read shows these fields through `redacted`, so a save built from one carries a
            # marker for each hidden value. Each marker is put back from what is stored before
            # anything is written, so no save writes one over the value it hides.
            if content is not None:
                content = _kept_text(art.kind, keep_masked_spans(content, live or ""))
            if name is not None:
                name = keep_masked_spans(name, art.name)
            if description is not None:
                description = keep_masked_spans(description, art.description)
            if collection is not None:
                collection = keep_masked_spans(collection, art.collection or "")
            # Every write keeps what it would erase (see the helper) but the owner's own save,
            # which names the revision her editor showed her: a plain Save keeps the version number.
            kept_unversioned = (
                content is not None
                and not binary
                and not (actor == "user" and expect_revision is not None)
                and self._keep_unversioned(art, live, content, actor)
            )

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
            if add_tags or remove_tags:
                # Every read shows a tag through `redacted`, so a chip the page removes, or sends
                # back, is the name it was shown: it names the stored tag that shows as it
                # (`security.stored_name`). A marker that names no stored tag is not a tag to add.
                dropped = {
                    name
                    for name in (stored_name(t, art.tags) for t in clean_tags(remove_tags))
                    if name is not None
                }
                added = [stored_name(t, art.tags) for t in clean_tags(add_tags)]
                if None in added:
                    raise MaskConflict()
                kept = [t for t in art.tags if t not in dropped]
                edited = clean_tags(kept + added)
                if edited != art.tags:
                    art.tags = edited
                    meta_changed = True
            if collection is not None:
                art.collection = collection.strip()[:MAX_NAME_LEN]
                meta_changed = True
            # ADOPTION (#290) — set BEFORE the content write below, so the same call that
            # attaches the pointer also pushes the body through it. Attaching afterwards
            # would leave the file and the artifact one version apart on the first write.
            if pointer is not None:
                art.source_path = pointer
                meta_changed = True

            # Track REAL change so the event / version / recency triad follows what
            # actually happened, not merely which flags the caller passed. The write
            # stays unconditional (idempotent), but content_changed gates the edit event
            # and the updated_at bump so an identical save/snapshot is inert.
            content_changed = False
            if content is not None:
                d = self._artifact_dir(slug)
                content_changed = content != (self._current_content(slug) or "")
                self._write_text(d / "current.html", content)
                if art.source_path:
                    self._try_write_source_path(art.source_path, content)

            cut_version = False
            # A binary body changes only through its own writers, each of which cuts the version it
            # writes, so its live body is always its latest version and a snapshot has nothing to
            # record. The agent's metadata-only update asks for one every time; read as text it cut
            # an empty `vN.html`.
            if snapshot and not binary:
                # Capture live state if no explicit content was passed.
                snap_content = content
                if snap_content is None:
                    snap_content = (
                        self._try_read_source_path(art.source_path) if art.source_path else None
                    )
                    if snap_content is None:
                        snap_content = self._current_content(slug) or ""
                # A snapshot whose bytes match the latest version records a change that
                # did not happen — skip the version cut (#692). Save is disabled while the
                # draft is clean; Snapshot is now equally inert on that same clean state.
                nums = self._list_version_numbers(slug)
                latest_snap = self._version_content(slug, nums[-1]) if nums else None
                if latest_snap is None or snap_content != latest_snap:
                    art.version += 1
                    self._snapshot_version(slug, art.version, snap_content)
                    cut_version = True

            # ONE event per real change. A version cut carries the caller's type (or
            # 'iterated' for the agent, else 'edited'); a content edit that cut no version
            # is an edit — which previously logged NOTHING because the append was nested in
            # the snapshot branch, dropping the event_type the UI sends on a plain save (#291).
            if cut_version or content_changed:
                resolved_type = event_type or ("iterated" if actor == "agent" else "edited")
                ev = ArtifactEvent(
                    ts=_now(),
                    type=resolved_type,
                    by=actor or "",
                    session_id=session_id or "",
                    version=art.version,
                    metadata=clean_event_metadata(event_metadata or {}),
                )
                self._append_event(art, ev)

            changed = kept_unversioned or cut_version or content_changed or meta_changed
            if changed:
                art.updated_at = _now()
                self._write_meta(art)

            # Return the live view (content + live_dirty) like get().
            updated = self.get(slug)
        # Emitted only when something actually changed — a PATCH that set nothing must not
        # re-index. The name is part of the mirror's title, so a metadata-only rename IS a
        # change worth mirroring; the mirror's own content-hash gate decides whether that
        # costs a re-embed.
        if updated is not None and changed:
            changes.emit(changes.UPSERT, slug)
        return updated

    def set_folder(self, slug: str, folder_id: str) -> Artifact | None:
        """File an artifact into a library folder — metadata only, no ``updated_at`` bump.

        The no-bump is the contract, not an optimization: ``updated_at`` means "the
        content changed", and the library (plus ``list``'s sort) is ordered by it.
        If tidying bumped it, dragging ten artifacts into a folder would rewrite the
        whole recency order and bury whatever the user was actually working on.

        No ``_refuse_if_readonly`` guard, deliberately: that guard covers the three
        CONTENT-mutating methods, and a frozen record (a shared transcript) is still
        the owner's to organize. Filing changes no body and no version.

        Existence of the folder is the caller's check (the route validates against
        ``ArtifactFolderStore``) — this method is the storage primitive, and
        ``delete_folder`` needs to write ``""`` regardless of tree state.
        """
        with self._lock:
            art = self._read_meta(slug)
            if art is None:
                return None
            art.folder_id = (folder_id or "").strip()
            self._write_meta(art)
            return self.get(slug)

    def delete(self, slug: str) -> bool:
        """Delete *slug* and everything under it, after taking its deployed page down.

        The page comes down HERE, in the store, because the store is what every delete
        reaches: the REST route, the agent's ``artifact_delete``, and an agent CLI's
        ``mcp-core`` process, which writes this tree directly and which no listener in the
        gateway hears. Slugs are reused, so a deployment left behind would serve whatever is
        saved under the slug next. Refused, nothing deleted, if the teardown cannot be
        recorded: an artifact still there and still published is a state the owner can see
        and retry; one that is gone and still published is not.
        """
        with self._lock:
            try:
                d = self._artifact_dir(slug)
            except ValueError:
                return False
            if not d.is_dir():
                return False
            import shutil

            try:
                ArtifactDeployStore(self._root).teardown(slug)
            except OSError:  # an unwritable registry, or a root refused as sensitive
                logger.warning("artifact delete refused, its page could not be taken down: %s", d)
                return False
            try:
                shutil.rmtree(d)
            except OSError:
                logger.warning("artifact delete failed: %s", d, exc_info=True)
                return False
        # Only a real removal notifies: emitting on a failed rmtree would drop a mirror for
        # content that is still there, which is the one direction of this pair that loses
        # something the user can still see.
        changes.emit(changes.DELETE, slug)
        return True

    def list_versions(self, slug: str) -> list[int]:  # type: ignore[valid-type]  # CI-1
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
