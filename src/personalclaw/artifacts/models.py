"""Canonical artifact entity — the model shared across all artifact providers.

An Artifact is a named, versioned piece of LLM-generated content (a widget,
HTML tool, markdown doc, etc.) that outlives chat scrollback. The dataclass and
its caps/validators live here (split out of the provider, like ``tasks/models``)
so every provider and the REST layer share one definition.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

# ── Caps (bound LLM-authored content + on-disk growth) ──
MAX_VERSIONS = 50  # FIFO prune older numbered snapshots beyond this
MAX_CONTENT_BYTES = 1 * 1024 * 1024  # 1 MiB per TEXT artifact body
# Binary artifacts (kind:image) store bytes on disk, not text-in-content. A
# generated image is comfortably under this; far above the text cap so a 1.x MiB
# PNG isn't truncated. Still bounded so a runaway upload can't fill the disk.
MAX_BINARY_CONTENT_BYTES = 16 * 1024 * 1024  # 16 MiB per binary artifact body
MAX_NAME_LEN = 200
MAX_DESCRIPTION_LEN = 2000
MAX_TAGS = 16
MAX_TAG_LEN = 64
MAX_EVENTS_PER_ARTIFACT = 500
MAX_EVENT_METADATA_KEYS = 8
MAX_EVENT_METADATA_VALUE_LEN = 256

ALLOWED_KINDS = {
    "widget",
    "html",
    "react",
    "markdown",
    "svg",
    "json",
    "text",
    "infographic",
    "document",
    "image",
}
ALLOWED_SOURCES = {"chat", "cron", "subagent", "manual", "import"}
ALLOWED_EVENT_TYPES = {"created", "edited", "iterated", "referenced", "reverted"}

# Kinds whose body is BINARY (stored as raw bytes on disk, served via the raw
# endpoint) rather than text. For these the ``content`` field carries a reference
# (the raw URL) — never the bytes themselves (no base64-in-content: it inflates
# context + payload). Today only images; video/audio-gen would join here.
BINARY_KINDS = {"image"}

# Mapping from an image MIME type to the on-disk file extension. The default
# (png) covers gpt-image / FAL output; svg is already a TEXT kind, so it's absent.
_MIME_TO_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


def is_binary_kind(kind: str) -> bool:
    return (kind or "").strip().lower() in BINARY_KINDS


def ext_for_mime(mime: str, default: str = "png") -> str:
    """On-disk extension for a binary MIME type (png fallback)."""
    return _MIME_TO_EXT.get((mime or "").strip().lower(), default)


# Inverse of _MIME_TO_EXT: recover a version's MIME from its on-disk extension.
# Lets a historical body (e.g. versions/v1.jpg) report its true Content-Type even
# after a later edit changed the artifact's current mime — the extension on disk,
# not the (mutable) art.mime, is the per-version source of truth.
_EXT_TO_MIME = {ext: mime for mime, ext in _MIME_TO_EXT.items()}


def mime_for_ext(ext: str, default: str = "image/png") -> str:
    """MIME type for an on-disk binary extension (png fallback)."""
    return _EXT_TO_MIME.get((ext or "").strip().lower().lstrip("."), default)


# Slug: URL-safe handle, 1-80 chars, no leading/trailing hyphen. Blocks path
# traversal (no dots, slashes, or separators survive slugify) — the security
# spine for the on-disk dir name.
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$")
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Derive a URL-safe slug from a display name (NFKD, lowercase, hyphenate)."""
    normalized = unicodedata.normalize("NFKD", name or "")
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    hyphenated = _SLUG_STRIP_RE.sub("-", ascii_only.lower()).strip("-")
    slug = hyphenated[:80].strip("-")
    return slug or "artifact"


def is_valid_slug(slug: str) -> bool:
    return bool(_SLUG_RE.match(slug))


@dataclass
class ArtifactEvent:
    """A lifecycle entry in an artifact's activity timeline."""

    ts: str
    type: str  # one of ALLOWED_EVENT_TYPES
    by: str = ""  # 'user' | 'agent' | actor label
    session_id: str = ""  # originating chat session (for the deep-link); '' = none
    version: int = 0  # post-event version
    from_version: int = 0  # set on 'reverted'
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ArtifactEvent":
        return cls(
            ts=str(d.get("ts", "")),
            type=str(d.get("type", "")),
            by=str(d.get("by", "")),
            session_id=str(d.get("session_id", "")),
            version=int(d.get("version", 0) or 0),
            from_version=int(d.get("from_version", 0) or 0),
            metadata=d.get("metadata") or {},
        )


@dataclass
class Artifact:
    """A named, versioned artifact.

    ``content`` is populated only on detail reads (``list`` omits it). For
    file-backed artifacts ``source_path`` points at a Workspace file that is the
    live source of truth; ``live_dirty`` is computed at read time (live content
    differs from the latest numbered snapshot) and never persisted.
    """

    slug: str
    name: str
    kind: str = "widget"
    source: str = "chat"
    description: str = ""
    tags: list[str] = field(default_factory=list)
    version: int = 1
    created_at: str = ""
    updated_at: str = ""
    content: str | None = None
    events: list[ArtifactEvent] = field(default_factory=list)
    source_path: str = ""
    live_dirty: bool = False
    # For BINARY_KINDS (image): the body's MIME type, which fixes the on-disk
    # extension + tells the renderer how to display it. "" for text kinds.
    mime: str = ""
    # Optional containing Project (Projects native entity) this artifact belongs to.
    # "" = unscoped. Lets a project's outputs surface alongside its loops/code/tasks.
    project_id: str = ""

    def to_dict(self, *, persist: bool = False) -> dict[str, Any]:
        """Serialize. ``persist=True`` (meta.json) drops the transient/derived
        fields: ``content`` (lives in current.html / source_path) and
        ``live_dirty`` (computed per read)."""
        d: dict[str, Any] = {
            "slug": self.slug,
            "name": self.name,
            "kind": self.kind,
            "source": self.source,
            "description": self.description,
            "tags": list(self.tags),
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_path": self.source_path,
            "project_id": self.project_id,
            "mime": self.mime,
            "events": [e.to_dict() for e in self.events],
        }
        if not persist:
            d["content"] = self.content
            d["live_dirty"] = self.live_dirty
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Artifact":
        return cls(
            slug=str(d.get("slug", "")),
            name=str(d.get("name", "")),
            kind=str(d.get("kind", "widget")),
            source=str(d.get("source", "chat")),
            description=str(d.get("description", "")),
            tags=list(d.get("tags", []) or []),
            version=int(d.get("version", 1) or 1),
            created_at=str(d.get("created_at", "")),
            updated_at=str(d.get("updated_at", "")),
            content=d.get("content"),
            events=[ArtifactEvent.from_dict(e) for e in (d.get("events") or [])],
            source_path=str(d.get("source_path", "")),
            live_dirty=bool(d.get("live_dirty", False)),
            project_id=str(d.get("project_id", "")),
            mime=str(d.get("mime", "")),
        )


def normalize_kind(kind: str) -> str:
    k = (kind or "").strip().lower()
    return k if k in ALLOWED_KINDS else "widget"


def normalize_source(source: str) -> str:
    s = (source or "").strip().lower()
    return s if s in ALLOWED_SOURCES else "chat"


def clean_tags(tags: Any) -> list[str]:
    """Coerce arbitrary tag input into a deduped, capped, trimmed list."""
    if not isinstance(tags, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for t in tags:
        tag = str(t).strip()[:MAX_TAG_LEN]
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
        if len(out) >= MAX_TAGS:
            break
    return out


def clean_event_metadata(metadata: Any) -> dict[str, Any]:
    """Bound event metadata to ≤8 string-keyed scalar values ≤256 chars."""
    if not isinstance(metadata, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in metadata.items():
        if len(out) >= MAX_EVENT_METADATA_KEYS:
            break
        if not isinstance(k, str):
            continue
        if isinstance(v, bool) or isinstance(v, (int, float)):
            out[k] = v
        else:
            out[k] = str(v)[:MAX_EVENT_METADATA_VALUE_LEN]
    return out
