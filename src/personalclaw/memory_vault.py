"""Memory → markdown vault mirror (mem-fs-mirror, ROADMAP Phase C).

A **read-only projection** of the memory store into an Obsidian-compatible
markdown vault under ``~/.personalclaw/memory-vault/`` so a human can browse
memory as a linked knowledge graph (Obsidian graph view + backlinks) instead of
squinting at ``memory.db``.

Design — one path, no dual write surface:
  * The vault is a *pure projection* of ``MemoryService.get_records()``. It is
    reconciled at natural memory-write boundaries (post-consolidation seal, the
    scheduled maintenance cadence, and an explicit "sync now"), NOT by
    instrumenting every write method. This keeps the vault a derived artifact —
    idempotent, rebuildable from scratch, and impossible to drift into a second
    source of truth.
  * A content-hash manifest (``.vault-manifest.json``) makes each sync O(changed):
    only records whose rendered markdown changed are rewritten, and files for
    records that no longer exist are pruned. A full rebuild == delete the manifest.
  * ``[[wikilinks]]`` are derived from **real record fields** — supersession
    chains, shared tags (via tag-hub pages), and session grouping — never from
    fictional entity extraction. The graph is exactly as rich as the data.

Safety: memory text is often *untrusted* (episodic fragments, tool outputs). YAML
frontmatter values are JSON-encoded (JSON is a strict subset of YAML), so a value
containing ``---`` / newlines / quotes can never break out of the frontmatter
fence or forge extra keys. The vault is written only by this module; the user
reads it (read-only from the FS side).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from personalclaw.atomic_write import atomic_write

if TYPE_CHECKING:
    from personalclaw.memory_record import MemoryRecord
    from personalclaw.memory_service import MemoryService

logger = logging.getLogger(__name__)

_MANIFEST_NAME = ".vault-manifest.json"
_INDEX_NAME = "MEMORY.md"

# Which MemoryKind → which vault subdirectory. Grouping keeps the vault
# navigable; Obsidian resolves ``[[wikilinks]]`` by basename regardless of dir.
_KIND_DIR: dict[str, str] = {
    "semantic": "facts",
    "preference": "facts",
    "note": "facts",
    "lesson": "lessons",
    "episodic": "episodic",
    "procedural": "procedural",
    "commitment": "commitments",
    "self_persona": "persona",
}
_TAGS_DIR = "tags"

# Frontmatter fields emitted in this deterministic order (only when non-empty),
# so a re-render of an unchanged record produces byte-identical output.
_FM_ORDER = (
    "id",
    "kind",
    "tier",
    "scope",
    "scope_ref",
    "category",
    "confidence",
    "importance",
    "recall_count",
    "visit_count",
    "source",
    "conversation_id",
    "tags",
    "created_at",
    "updated_at",
    "superseded_by",
    "invalidated_at",
    "due_window",
    "channel",
    "dismissed_at",
)

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_BODY = 20_000  # cap a single note body so a runaway record can't bloat the vault


def _slug(raw: str, *, fallback: str = "record") -> str:
    """Sanitize an id/tag into a stable, filesystem- and wikilink-safe basename.

    Collisions are theoretically possible (``a/b`` and ``a-b`` both → ``a-b``), so
    when sanitization actually changes the string we append a short content hash
    of the *original* — stable across runs, unique in practice."""
    s = _UNSAFE_CHARS.sub("-", raw).strip("-._")
    if not s:
        s = fallback
    if s != raw:
        h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:6]
        s = f"{s}-{h}"
    return s[:120]


def _yaml_scalar(value: object) -> str:
    """Emit a YAML scalar that is ALWAYS safe — JSON encoding (JSON ⊂ YAML).

    A string with ``---``, newlines, colons, or quotes is JSON-escaped, so it
    cannot break the frontmatter fence or forge keys. Bools/ints/floats round-trip
    as themselves."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return json.dumps(str(value), ensure_ascii=False)


def _frontmatter(fields: list[tuple[str, object]]) -> str:
    lines = ["---"]
    for key, value in fields:
        if isinstance(value, list):
            if not value:
                continue
            items = ", ".join(_yaml_scalar(v) for v in value)
            lines.append(f"{key}: [{items}]")
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


class RenderedNote:
    """A rendered vault file: its relative path + content + wikilink targets."""

    __slots__ = ("relpath", "content", "links", "tags", "title")

    def __init__(
        self, relpath: str, content: str, links: set[str], tags: list[str], title: str
    ) -> None:
        self.relpath = relpath
        self.content = content
        self.links = links
        self.tags = tags
        self.title = title


def _record_basename(rec: "MemoryRecord") -> str:
    return _slug(rec.id, fallback=rec.kind.value)


def _record_title(rec: "MemoryRecord") -> str:
    """A human H1 title. Semantic ids ARE readable (``pref.editor``); episodic
    fragments get their leading text, cut on a WORD boundary (never mid-word) with
    an ellipsis so the title reads cleanly in Obsidian's file list + graph."""
    from personalclaw.memory_record import MemoryKind

    if rec.kind == MemoryKind.EPISODIC:
        flat = " ".join((rec.text or "").split())
        if not flat:
            return f"episodic {rec.id[:8]}"
        if len(flat) <= 72:
            return flat
        cut = flat[:72]
        # Back up to the last space so we don't slice a word in half; only keep
        # the trim if it leaves a reasonable amount of text.
        sp = cut.rfind(" ")
        if sp >= 40:
            cut = cut[:sp]
        return cut.rstrip(" ,.;:") + "…"
    return rec.id


def render_record(rec: "MemoryRecord") -> RenderedNote:
    """Render one record to a markdown note. Pure — no I/O, deterministic."""
    from personalclaw.memory_record import MemoryKind

    subdir = _KIND_DIR.get(rec.kind.value, "other")
    base = _record_basename(rec)
    relpath = f"{subdir}/{base}.md"
    title = _record_title(rec)

    fm: list[tuple[str, object]] = []
    for key in _FM_ORDER:
        if key == "kind":
            fm.append((key, rec.kind.value))
            continue
        if key == "tier":
            if rec.tier is not None:
                fm.append((key, rec.tier.value))
            continue
        if key == "scope":
            fm.append((key, rec.scope.value))
            continue
        if key == "tags":
            if rec.tags:
                fm.append((key, list(rec.tags)))
            continue
        val = getattr(rec, key, None)
        # Drop empties + heat-counter zeros so frontmatter stays lean + stable.
        if val in (None, "", 0, 0.0):
            continue
        fm.append((key, val))

    links: set[str] = set()

    # Body: the record's text projection; a dict value pretty-prints as JSON.
    body_text = rec.text or ""
    if not body_text and rec.value is not None:
        body_text = (
            json.dumps(rec.value, indent=2, ensure_ascii=False)
            if isinstance(rec.value, (dict, list))
            else str(rec.value)
        )
    body_text = body_text[:_MAX_BODY]

    parts = [_frontmatter(fm), "", f"# {title}", ""]
    if body_text.strip():
        parts.append(body_text.rstrip())
        parts.append("")

    # Supersession chain — a real, first-class relation.
    if rec.superseded_by:
        target = _slug(rec.superseded_by)
        links.add(target)
        parts.append(f"**Superseded by:** [[{target}]]")
        parts.append("")

    # Session grouping for episodic fragments.
    if rec.kind == MemoryKind.EPISODIC and rec.conversation_id:
        sess = _slug(f"session-{rec.conversation_id}")
        links.add(sess)
        parts.append(f"**Session:** [[{sess}]]")
        parts.append("")

    # Tag hubs — the primary graph-clustering signal.
    tag_links = [_slug(f"tag-{t}") for t in rec.tags]
    if tag_links:
        links.update(tag_links)
        parts.append("**Tags:** " + " ".join(f"[[{t}]]" for t in tag_links))
        parts.append("")

    return RenderedNote(relpath, "\n".join(parts).rstrip() + "\n", links, list(rec.tags), title)


def render_tag_hub(tag: str, members: list[tuple[str, str]]) -> RenderedNote:
    """A tag-hub note that forward-links every record carrying the tag, so the
    graph clusters by tag even in non-Obsidian viewers (Obsidian also shows the
    reverse backlinks automatically). ``members`` = [(basename, title), ...]."""
    slug = _slug(f"tag-{tag}")
    relpath = f"{_TAGS_DIR}/{slug}.md"
    fm = _frontmatter([("kind", "tag"), ("tag", tag), ("count", len(members))])
    lines = [
        fm,
        "",
        f"# #{tag}",
        "",
        f"{len(members)} memor" + ("y" if len(members) == 1 else "ies") + " with this tag:",
        "",
    ]
    for base, title in sorted(members):
        safe_title = title.replace("]", " ").replace("[", " ").strip() or base
        lines.append(f"- [[{base}]] — {safe_title}")
    return RenderedNote(relpath, "\n".join(lines).rstrip() + "\n", set(), [tag], f"#{tag}")


def render_index(records: list["MemoryRecord"]) -> str:
    """The root ``MEMORY.md`` — counts by kind + the highest-heat global facts.
    This is the plan's named MVP surface, kept as the vault's front door."""
    from personalclaw.memory_record import MemoryKind, MemoryScope

    by_kind: dict[str, int] = {}
    for r in records:
        by_kind[r.kind.value] = by_kind.get(r.kind.value, 0) + 1

    lines = [
        _frontmatter([("kind", "index"), ("total", len(records))]),
        "",
        "# Memory Vault",
        "",
        "A read-only mirror of PersonalClaw's memory. Open this folder in Obsidian "
        "for the graph view. Do not edit — files are regenerated from the memory store.",
        "",
        "## Counts",
        "",
    ]
    for kind in sorted(by_kind):
        lines.append(f"- **{kind}**: {by_kind[kind]}")
    lines.append("")

    # Top global facts by heat — the "what does it actually know" front page.
    facts = [
        r
        for r in records
        if r.scope == MemoryScope.GLOBAL
        and r.kind
        in (MemoryKind.SEMANTIC, MemoryKind.PREFERENCE, MemoryKind.LESSON, MemoryKind.PROCEDURAL)
    ]
    facts.sort(key=lambda r: r.heat(), reverse=True)
    if facts:
        lines.append("## Most-recalled facts")
        lines.append("")
        for r in facts[:25]:
            base = _record_basename(r)
            summary = " ".join((r.text or str(r.value or "")).split())[:100]
            lines.append(f"- [[{base}]] — {summary}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


class MemoryVault:
    """Reconciles the on-disk markdown vault against the memory store.

    Stateless beyond the manifest it reads/writes; construct freely (or reuse via
    :func:`vault_for`). ``sync()`` is idempotent and cheap when nothing changed."""

    def __init__(self, service: "MemoryService", vault_dir: Path) -> None:
        self._svc = service
        self._dir = vault_dir

    @property
    def path(self) -> Path:
        return self._dir

    def _manifest_path(self) -> Path:
        return self._dir / _MANIFEST_NAME

    def _load_manifest(self) -> dict[str, str]:
        try:
            return json.loads(self._manifest_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def status(self) -> dict:
        """Lightweight status for the UI — no full render."""
        manifest = self._load_manifest()
        # note files = manifest minus the index (index is tracked too)
        return {
            "path": str(self._dir),
            "files": len(manifest),
            "exists": self._dir.exists(),
        }

    def sync(self) -> dict:
        """Reconcile the vault to the current record set. Returns a change summary.

        Writes only files whose content changed, prunes files for records that no
        longer exist, and rewrites the manifest. Never raises for a single bad
        record — it is skipped and logged, so one malformed row can't stall the
        mirror."""
        records = self._svc.get_records()

        rendered: dict[str, RenderedNote] = {}
        # Records → notes.
        tag_members: dict[str, list[tuple[str, str]]] = {}
        for rec in records:
            try:
                note = render_record(rec)
            except Exception:
                logger.debug(
                    "vault: failed to render record %s", getattr(rec, "id", "?"), exc_info=True
                )
                continue
            # Last writer wins on a basename collision (ids are unique in practice).
            rendered[note.relpath] = note
            base = Path(note.relpath).stem
            for tag in note.tags:
                tag_members.setdefault(tag, []).append((base, note.title))

        # Tag hubs.
        for tag, members in tag_members.items():
            hub = render_tag_hub(tag, members)
            rendered[hub.relpath] = hub

        # Root index.
        try:
            index_content = render_index(records)
        except Exception:
            logger.debug("vault: index render failed", exc_info=True)
            index_content = "# Memory Vault\n"
        rendered[_INDEX_NAME] = RenderedNote(_INDEX_NAME, index_content, set(), [], "Memory Vault")

        # Reconcile against the manifest.
        old_manifest = self._load_manifest()
        new_manifest: dict[str, str] = {}
        written = 0
        for relpath, note in rendered.items():
            digest = hashlib.sha256(note.content.encode("utf-8")).hexdigest()
            new_manifest[relpath] = digest
            if old_manifest.get(relpath) == digest and (self._dir / relpath).exists():
                continue  # unchanged — skip the write
            try:
                atomic_write(self._dir / relpath, note.content, fsync=False)
                written += 1
            except OSError:
                logger.debug("vault: write failed for %s", relpath, exc_info=True)
                new_manifest.pop(relpath, None)

        # Prune files that were ours but are no longer produced.
        pruned = 0
        for relpath in old_manifest:
            if relpath in new_manifest:
                continue
            try:
                (self._dir / relpath).unlink()
                pruned += 1
            except OSError:
                pass

        try:
            atomic_write(
                self._manifest_path(),
                json.dumps(new_manifest, indent=0, sort_keys=True),
                fsync=False,
            )
        except OSError:
            logger.debug("vault: manifest write failed", exc_info=True)

        summary = {
            "records": len(records),
            "files": len(new_manifest),
            "written": written,
            "pruned": pruned,
        }
        logger.info("memory vault synced: %s", summary)
        return summary


# ── config + wiring ─────────────────────────────────────────────────────────


def vault_dir_from_config() -> Path | None:
    """Resolve the configured vault directory, or None when the mirror is off."""
    from personalclaw.config.loader import AppConfig, config_dir

    cfg = AppConfig.load().memory
    if not getattr(cfg, "vault_enabled", False):
        return None
    rel = (getattr(cfg, "vault_path", "") or "memory-vault").strip()
    p = Path(rel).expanduser()
    return p if p.is_absolute() else (config_dir() / rel)


def vault_for(service: "MemoryService") -> MemoryVault | None:
    """Build a vault for ``service`` from config, or None when disabled."""
    vdir = vault_dir_from_config()
    if vdir is None:
        return None
    return MemoryVault(service, vdir)


def mirror_after_consolidation(service: "MemoryService") -> None:
    """Best-effort post-consolidation sync — the primary freshness trigger.

    Wired into ``ConversationManager.consolidate_session``; never raises so a
    mirror hiccup can't break session sealing."""
    try:
        vault = vault_for(service)
        if vault is not None:
            vault.sync()
    except Exception:
        logger.debug("memory vault: post-consolidation mirror failed", exc_info=True)
