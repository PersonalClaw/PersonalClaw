"""Memory ↔ markdown vault (mem-fs-mirror; MEMORY-GRAPH-AND-VAULT §5).

A projection of the memory store into an Obsidian-compatible markdown vault under
``~/.personalclaw/memory-vault/`` so a human can browse memory as a linked
knowledge graph (Obsidian graph view + backlinks) instead of squinting at
``memory.db`` — and, in ``two_way`` mode, **edit it back**.

``memory.vault_mode`` (§5.1) picks how far that goes:

``off``
    No vault.
``mirror``
    One direction. The vault is a pure projection of ``MemoryService.get_records()``,
    regenerated from the store and never read back. A hand edit is overwritten — that
    is what "mirror" means, and the config help says so.
``two_way``
    The sync pass reads edited pages back through the normal ``MemoryService`` write
    path before re-projecting (§5.2).

Design — one path, no dual write surface:
  * Reconciliation happens at natural memory-write boundaries (post-consolidation
    seal, the maintenance cadence, and an explicit ``POST /api/memory/vault/sync``),
    NOT by instrumenting every write method or watching the filesystem. The vault
    stays a derived artifact: idempotent, rebuildable from scratch.
  * A content-hash manifest (``.vault-manifest.json``) makes each sync O(changed):
    only pages whose rendered markdown changed are rewritten, and files for records
    that no longer exist are pruned. A full rebuild == delete the manifest.
  * ``[[wikilinks]]`` are derived from **real relations** — ``mem_links`` graph edges,
    supersession chains, shared tags (via tag-hub pages), and session grouping —
    never from scraping the page text. A link the graph does not have cannot appear.

**``source_hash`` is the safety mechanism, and it covers the BODY ONLY.** Every page
carries ``source_hash`` = a hash of everything below the frontmatter fence. The sync
pass rewrites the frontmatter on every pass (counters, ``last_updated``, the hash
itself), so a hash that covered the frontmatter would never match its own page again
and every page would read as hand-edited forever. Body-only also buys the conflict
flag for free: stamping ``sync_conflict`` into the frontmatter of a page we refuse to
touch does not change its body hash, so the page stays flagged until the human
resolves it.

Two-way is *lossy in one direction only*: a page the parser cannot read with
confidence is **left exactly as the human wrote it**, flagged in frontmatter, and
reported by the vault lint. It is never overwritten and never dropped.

Safety: memory text is often *untrusted* (episodic fragments, tool outputs), and a
vault page is worse — a human can paste anything into it. YAML frontmatter values are
JSON-encoded (JSON is a strict subset of YAML), so a value containing ``---`` /
newlines / quotes can never break out of the frontmatter fence or forge extra keys.
Edits read back are written with ``source="vault_edit"``, which is deliberately NOT in
``MemoryService._TRUSTED_WRITE_SOURCES``: the *human's intent* is authoritative, the
*bytes* are not, so they pass the S5 injection scan like any other untrusted write.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import MEMORY_VAULT_MODES

if TYPE_CHECKING:
    from personalclaw.memory_graph import Entity
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
_ENTITIES_DIR = "entities"
_RAW_DIR = "raw"
#: Where a swept ``raw/`` file is parked once its knowledge item exists. Inside
#: ``raw/`` (so it is obvious where the file went) and dot-prefixed (so the next
#: sweep skips it) — moving rather than deleting, because the sweep's whole job is
#: to hand the user's file to knowledge, not to be the thing that loses it.
_RAW_DONE_DIR = "raw/.ingested"

#: ``type:`` values this projection actually writes. §5.3's vocabulary also names
#: ``connection`` and ``qa`` pages; nothing generates those yet, so they are not
#: declared here — a type nobody writes reads as a decision and behaves as an
#: omission.
_PAGE_TYPE: dict[str, str] = {
    "semantic": "concept",
    "preference": "concept",
    "note": "concept",
    "lesson": "concept",
    "procedural": "concept",
    "commitment": "concept",
    "self_persona": "concept",
    "approval": "concept",
    "slot": "slot",
    "episodic": "synthesis",
}

#: Page ``type``s that stand for one memory RECORD, as opposed to a hub (``tag``,
#: ``session``) or the front door (``index``). Only a record page's outbound entity
#: link creates a backlink-symmetry obligation.
_RECORD_PAGE_TYPES = frozenset({"concept", "slot", "synthesis"})

#: Record kinds whose page a human may edit back into memory (§5.2). Deliberately
#: narrow: these are the kinds whose page body IS the stored value, so "the text the
#: human left behind" maps onto exactly one ``set_semantic`` call. Episodic pages are
#: read-only even in two_way — an episode is evidence, and evidence is immutable.
#: Lessons/commitments/approvals carry structure (rule + counter-example, due
#: windows, verdicts) that a single body of prose cannot round-trip, so editing one is
#: a conflict to surface rather than a merge to guess at.
_EDITABLE_KINDS = frozenset({"semantic", "preference", "note", "slot"})

#: Everything below this line on a page is machine-owned. It is the parse-back
#: boundary: the human's value is what sits between the H1 and this marker.
_GENERATED_MARKER = "<!-- personalclaw:generated (replaced on every sync) -->"

#: Frontmatter key carrying the body hash at write time — the whole edit-detection
#: mechanism. See the module docstring for why it excludes the frontmatter.
_HASH_KEY = "source_hash"
#: Frontmatter key set on a page the sync refused to touch, and the lint check name.
_CONFLICT_KEY = "sync_conflict"

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
    "contributor",
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
#: How many inbound links one entity page carries. Generous rather than the graph
#: API's default 100: a truncated backlink list would make the symmetry lint report a
#: gap that only the page limit created.
_MAX_BACKLINKS = 1_000
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|#]+)")


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


# ── the page shape: frontmatter fence ↔ body, and the body hash ──────────────


def split_page(text: str) -> tuple[str, str]:
    """Split a page into ``(frontmatter_block, body)``.

    ``frontmatter_block`` excludes the fences. A page with no leading fence yields
    ``("", text)`` — a hand-written page is still a page; it just has no metadata
    and therefore no ``source_hash`` to compare against.
    """
    if not text.startswith("---"):
        return ("", text)
    rest = text[3:]
    if rest.startswith("\n"):
        rest = rest[1:]
    end = rest.find("\n---")
    if end < 0:
        # An unterminated fence — treat the whole file as body rather than
        # inventing metadata out of it.
        return ("", text)
    block = rest[:end]
    # Drop the newline that ends the closing fence AND the blank separator line
    # `compose_page` writes after it, so a round trip through split/compose is stable.
    body = rest[end + 4 :].lstrip("\n")
    return (block, body)


def parse_frontmatter(block: str) -> dict[str, object]:
    """Parse the frontmatter this module writes back into a dict.

    Deliberately NOT a general YAML parser: every value we emit is JSON (a strict
    subset of YAML), so ``json.loads`` round-trips ours exactly and a hand-typed
    value that is not JSON falls back to its raw string rather than raising. A
    malformed line is skipped, not guessed at — the caller's decision about an
    unparseable page is what protects the human's text, so this must not paper over
    one.
    """
    out: dict[str, object] = {}
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, raw = line.partition(":")
        if not sep or not key.strip() or key.startswith((" ", "\t")):
            continue
        raw = raw.strip()
        try:
            out[key.strip()] = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            out[key.strip()] = raw.strip('"')
    return out


def body_hash(body: str) -> str:
    """The content hash of a page BODY — the edit detector.

    Newlines are normalized and the whole body stripped, so a trailing newline an
    editor added is not mistaken for a human edit; nothing else is normalized,
    because collapsing interior whitespace would hide a real one.
    """
    norm = body.replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]


def compose_page(fields: list[tuple[str, object]], body: str) -> str:
    """Frontmatter (with a freshly computed ``source_hash``) + body → page bytes.

    The single place a page is assembled, so the hash cannot be computed over the
    wrong bytes at one call site and the right ones at another.
    """
    body = body.rstrip() + "\n"
    fm = [*fields, (_HASH_KEY, body_hash(body))]
    return _frontmatter(fm) + "\n\n" + body


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


def render_record(rec: "MemoryRecord", *, entities: "list[Entity] | None" = None) -> RenderedNote:
    """Render one record to a markdown note. Pure — no I/O, deterministic.

    ``entities`` are the entities this record links to **according to ``mem_links``**
    (§5.1). They are passed in rather than looked up here so rendering stays pure and,
    more importantly, so the only way an ``[[entity]]`` link can appear on a page is
    for the graph to hold that edge. Scraping the body for capitalized words would
    invent links the graph does not have, and the backlink-symmetry lint would then be
    checking the scraper against itself.
    """
    from personalclaw.memory_record import MemoryKind

    subdir = _KIND_DIR.get(rec.kind.value, "other")
    base = _record_basename(rec)
    relpath = f"{subdir}/{base}.md"
    title = _record_title(rec)

    fm: list[tuple[str, object]] = [("type", _PAGE_TYPE.get(rec.kind.value, "concept"))]
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

    # The human-editable region: H1 + the record's own text, and nothing else. Its
    # end is the generated marker below, which is what makes the parse-back
    # unambiguous instead of a guess about which trailing lines were ours.
    parts = [f"# {title}", ""]
    if body_text.strip():
        parts.append(body_text.rstrip())
        parts.append("")

    generated: list[str] = []

    # Supersession chain — a real, first-class relation.
    if rec.superseded_by:
        target = _slug(rec.superseded_by)
        links.add(target)
        generated.append(f"**Superseded by:** [[{target}]]")
        generated.append("")

    # Session grouping for episodic fragments.
    if rec.kind == MemoryKind.EPISODIC and rec.conversation_id:
        sess = _slug(f"session-{rec.conversation_id}")
        links.add(sess)
        generated.append(f"**Session:** [[{sess}]]")
        generated.append("")

    # Entity edges straight out of `mem_links` (§5.1). Sorted by name so the page is
    # byte-stable across runs regardless of row order.
    ent_links = [_entity_basename(e) for e in sorted(entities or [], key=lambda e: e.name)]
    if ent_links:
        links.update(ent_links)
        generated.append("**Entities:** " + " ".join(f"[[{t}]]" for t in ent_links))
        generated.append("")

    # Tag hubs — the primary graph-clustering signal.
    tag_links = [_slug(f"tag-{t}") for t in rec.tags]
    if tag_links:
        links.update(tag_links)
        generated.append("**Tags:** " + " ".join(f"[[{t}]]" for t in tag_links))
        generated.append("")

    if generated:
        parts.append(_GENERATED_MARKER)
        parts.append("")
        parts.extend(generated)

    # NOTE: no separate `last_updated` key. §5.1 lists one, but `_FM_ORDER` already
    # emits the record's own `updated_at`, which IS the store version this page was
    # projected from — the value the two-way pass compares against to notice a
    # concurrent store write. Two frontmatter keys for one fact would drift.
    return RenderedNote(
        relpath,
        compose_page(fm, "\n".join(parts)),
        links,
        list(rec.tags),
        title,
    )


def _entity_basename(entity: "Entity") -> str:
    """An entity page's basename — its NAME, not its id.

    Obsidian resolves ``[[wikilinks]]`` by basename, so ``[[Ana]]`` is what a human
    types and what the graph view labels. The id lives in frontmatter for the sync
    pass to key on."""
    return _slug(entity.name, fallback=entity.id)


def render_tag_hub(tag: str, members: list[tuple[str, str]]) -> RenderedNote:
    """A tag-hub note that forward-links every record carrying the tag, so the
    graph clusters by tag even in non-Obsidian viewers (Obsidian also shows the
    reverse backlinks automatically). ``members`` = [(basename, title), ...]."""
    slug = _slug(f"tag-{tag}")
    relpath = f"{_TAGS_DIR}/{slug}.md"
    fm: list[tuple[str, object]] = [
        ("type", "tag"),
        ("kind", "tag"),
        ("tag", tag),
        ("count", len(members)),
    ]
    lines = [
        f"# #{tag}",
        "",
        f"{len(members)} memor" + ("y" if len(members) == 1 else "ies") + " with this tag:",
        "",
        _GENERATED_MARKER,
        "",
    ]
    links: set[str] = set()
    for base, title in sorted(members):
        safe_title = title.replace("]", " ").replace("[", " ").strip() or base
        links.add(base)
        lines.append(f"- [[{base}]] — {safe_title}")
    return RenderedNote(relpath, compose_page(fm, "\n".join(lines)), links, [tag], f"#{tag}")


def render_session_hub(conversation_id: str, members: list[tuple[str, str]]) -> RenderedNote:
    """A session-hub page for one conversation's episodic fragments.

    🔴 Every episodic page has emitted ``**Session:** [[session-<id>]]`` since the
    mirror shipped, and **no such page was ever written**. Obsidian tolerates that (an
    unresolved link is still a graph node), which is why it went unnoticed — but the
    §5.3 broken-link lint measures the vault against itself, and a projection that
    links pages it does not create makes that check fire on the vault's own output.
    Generating the hub is the honest fix: the link resolves, the lint means what it
    says, and the session actually becomes browsable.
    """
    slug = _slug(f"session-{conversation_id}")
    relpath = f"sessions/{slug}.md"
    fm: list[tuple[str, object]] = [
        # Its own `type`, NOT `synthesis`: an episodic RECORD page is `synthesis`, and
        # a record page's entity link is what creates a backlink-symmetry obligation.
        # Sharing the type would make the hub inherit an obligation it cannot satisfy.
        ("type", "session"),
        ("kind", "session"),
        ("conversation_id", conversation_id),
        ("count", len(members)),
    ]
    lines = [
        f"# Session {conversation_id}",
        "",
        f"{len(members)} episodic fragment" + ("" if len(members) == 1 else "s") + ":",
        "",
        _GENERATED_MARKER,
        "",
    ]
    links: set[str] = set()
    for base, title in sorted(members):
        safe_title = title.replace("]", " ").replace("[", " ").strip() or base
        links.add(base)
        lines.append(f"- [[{base}]] — {safe_title}")
    return RenderedNote(
        relpath, compose_page(fm, "\n".join(lines)), links, [], f"Session {conversation_id}"
    )


def render_index(
    records: list["MemoryRecord"],
    *,
    entities: "list[Entity] | None" = None,
    mode: str = "mirror",
) -> RenderedNote:
    """The root ``MEMORY.md`` — counts by kind, the entity roster, and the
    highest-heat global facts. The vault's front door, and the page that makes every
    entity page reachable (an entity nothing links to would otherwise read as an
    orphan to the vault lint)."""
    from personalclaw.memory_record import MemoryKind, MemoryScope

    by_kind: dict[str, int] = {}
    for r in records:
        by_kind[r.kind.value] = by_kind.get(r.kind.value, 0) + 1

    two_way = mode == "two_way"
    lines = [
        "# Memory Vault",
        "",
        (
            "A **two-way** projection of PersonalClaw's memory. Open this folder in "
            "Obsidian for the graph view. Edit a fact page above its "
            "`personalclaw:generated` marker and the next sync reads your change back "
            "into memory — your edit wins. Anything the sync cannot read confidently is "
            "left exactly as you wrote it and reported in Settings → Memory → Health."
            if two_way
            else "A read-only mirror of PersonalClaw's memory. Open this folder in "
            "Obsidian for the graph view. Do not edit — files are regenerated from the "
            "memory store. Switch `memory.vault_mode` to `two_way` to edit them back."
        ),
        "",
        "## Counts",
        "",
    ]
    for kind in sorted(by_kind):
        lines.append(f"- **{kind}**: {by_kind[kind]}")
    lines.append("")

    links: set[str] = set()
    lines.append(_GENERATED_MARKER)
    lines.append("")

    roster = sorted(entities or [], key=lambda e: e.name)
    if roster:
        lines.append("## Entities")
        lines.append("")
        for entity in roster:
            base = _entity_basename(entity)
            links.add(base)
            lines.append(f"- [[{base}]] — {entity.entity_type}")
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
            links.add(base)
            summary = " ".join((r.text or str(r.value or "")).split())[:100]
            lines.append(f"- [[{base}]] — {summary}")
        lines.append("")
    fm: list[tuple[str, object]] = [
        ("type", "index"),
        ("kind", "index"),
        ("total", len(records)),
        ("vault_mode", mode),
    ]
    return RenderedNote(_INDEX_NAME, compose_page(fm, "\n".join(lines)), links, [], "Memory Vault")


_TIMELINE_HEADING = "## Timeline"
_COMPILED_HEADING = "## Compiled"
_BACKLINKS_HEADING = "## Backlinks"


def timeline_lines(body: str) -> list[str]:
    """The evidence lines already on an entity page, in the order they appear.

    Pulled out verbatim so the next render can carry them through unchanged. This is
    the append-only half of §5.1's "compiled truth + append-only timeline": the
    compiled section is rewritten every sync, the history below it never is.
    """
    out: list[str] = []
    inside = False
    for line in body.splitlines():
        if line.strip() == _TIMELINE_HEADING:
            inside = True
            continue
        if inside:
            if line.startswith("## "):
                break
            if line.startswith("- "):
                out.append(line)
    return out


def render_entity_page(
    entity: "Entity",
    *,
    compiled: list[tuple[str, str]],
    backlinks: list[str],
    evidence: list[str],
    existing_body: str = "",
) -> RenderedNote:
    """One page per ``mem_entity``: compiled truth on top, append-only timeline below.

    ``compiled`` = ``[(basename, summary)]`` for the facts currently linked to this
    entity — regenerated every sync, so a superseded fact leaves it. ``evidence`` =
    the dated lines this sync would add; anything already in ``existing_body``'s
    timeline is carried through **verbatim and in its original position**. A sync
    never reorders or rewrites history, because the point of a timeline is that it is
    the one part of the page you can trust not to have been quietly revised.
    """
    base = _entity_basename(entity)
    relpath = f"{_ENTITIES_DIR}/{base}.md"
    fm: list[tuple[str, object]] = [
        ("type", "entity"),
        ("kind", "entity"),
        ("id", entity.id),
        ("title", entity.name),
        ("entity_type", entity.entity_type),
    ]
    if entity.aliases:
        fm.append(("aliases", list(entity.aliases)))
    fm.append(("source", entity.source))
    fm.append(("sources", ["memory.db:mem_entities", "memory.db:mem_links"]))

    lines = [f"# {entity.name}", "", _GENERATED_MARKER, "", _COMPILED_HEADING, ""]
    links: set[str] = set()
    if compiled:
        for target, summary in compiled:
            links.add(target)
            safe = summary.replace("]", " ").replace("[", " ").strip() or target
            lines.append(f"- [[{target}]] — {safe}")
    else:
        lines.append("_Nothing linked to this entity yet._")
    lines.append("")

    lines.append(_BACKLINKS_HEADING)
    lines.append("")
    for target in backlinks:
        links.add(target)
        lines.append(f"- [[{target}]]")
    if not backlinks:
        lines.append("_No records link here._")
    lines.append("")

    merged = list(timeline_lines(existing_body))
    seen = set(merged)
    for line in evidence:
        if line not in seen:
            seen.add(line)
            merged.append(line)
    lines.append(_TIMELINE_HEADING)
    lines.append("")
    lines.extend(merged)
    return RenderedNote(relpath, compose_page(fm, "\n".join(lines)), links, [], entity.name)


class MemoryVault:
    """Reconciles the on-disk markdown vault against the memory store.

    Stateless beyond the manifest it reads/writes; construct freely (or reuse via
    :func:`vault_for`). ``sync()`` is idempotent and cheap when nothing changed.

    ``mode`` is ``memory.vault_mode``. Only ``two_way`` reads pages back; every other
    behavior (projection, raw sweep, seeding, lint) is identical in ``mirror``."""

    def __init__(self, service: "MemoryService", vault_dir: Path, *, mode: str = "mirror") -> None:
        self._svc = service
        self._dir = vault_dir
        self._mode = mode if mode in MEMORY_VAULT_MODES else "mirror"

    @property
    def path(self) -> Path:
        return self._dir

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def two_way(self) -> bool:
        return self._mode == "two_way"

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
            "mode": self._mode,
        }

    # ── the graph projection every page shape needs ──────────────────────────

    def _graph_view(self) -> tuple[
        list["Entity"],
        dict[tuple[str, str], list["Entity"]],
        dict[str, list[dict]],
    ]:
        """``(entities, record → entities, entity_id → inbound links)`` from ``mem_links``.

        Built once per sync from ONE public API (``graph_backlinks``), so the record
        pages' ``**Entities:**`` lines and the entity pages' ``## Backlinks`` lines are
        two projections of the same rows. That is what makes backlink symmetry true by
        construction and leaves the lint to catch *disk* drift rather than a renderer
        disagreeing with itself. Empty when the graph is off or unavailable — the vault
        then renders exactly as it did before the graph existed.
        """
        svc = self._svc
        if not getattr(svc, "has_graph", False):
            return ([], {}, {})
        try:
            raw_entities = svc.graph_entities()
        except Exception:
            logger.debug("vault: entity roster unavailable", exc_info=True)
            return ([], {}, {})
        from personalclaw.memory_graph import Entity

        entities = [
            Entity(
                id=str(e.get("id") or ""),
                name=str(e.get("name") or ""),
                entity_type=str(e.get("entity_type") or "topic"),
                aliases=tuple(e.get("aliases") or ()),
                source=str(e.get("source") or "user"),
            )
            for e in raw_entities
            if e.get("id") and e.get("name")
        ]
        by_record: dict[tuple[str, str], list[Entity]] = {}
        inbound: dict[str, list[dict]] = {}
        for entity in entities:
            try:
                rows = svc.graph_backlinks(entity.id, limit=_MAX_BACKLINKS)
            except Exception:
                logger.debug("vault: backlinks failed for %s", entity.id, exc_info=True)
                rows = []
            # Oldest first: the timeline is append-only, so the order it is BUILT in is
            # the order it will keep forever.
            rows.sort(key=lambda r: (str(r.get("created_at") or ""), int(r.get("id") or 0)))
            inbound[entity.id] = rows
            for row in rows:
                key = (str(row.get("from_kind") or ""), str(row.get("from_ref") or ""))
                by_record.setdefault(key, []).append(entity)
        return (entities, by_record, inbound)

    @staticmethod
    def _link_key(rec: "MemoryRecord") -> tuple[str, str]:
        """The ``mem_links`` ``(from_kind, from_ref)`` for a record.

        The linker writes only two ``from_kind`` values — ``episodic`` for episodic
        rows and ``semantic`` for everything backed by ``semantic_memory`` (facts,
        preferences, lessons, slots). Keyed off that, not off ``rec.kind``, or a
        lesson's edges would be looked up under a ``from_kind`` nothing ever wrote.
        """
        return ("episodic" if rec.kind.value == "episodic" else "semantic", rec.id)

    def sync(self, *, knowledge: object = None, enqueue: object = None) -> dict:
        """Reconcile the vault against the store. Returns a change summary.

        Order matters and is the whole design:

        1. **absorb** hand edits (``two_way`` only) — before anything is re-rendered,
           or the projection would overwrite the human's text and the edit would be
           gone before it was read;
        2. **project** the store onto pages, skipping any page absorption refused
           (those are flagged in frontmatter instead, never overwritten);
        3. **sweep** ``raw/`` into the knowledge ingest queue;
        4. **seed** the starter files that are missing or still pristine.

        Never raises for a single bad record or page — it is skipped and logged, so
        one malformed row can't stall the vault.
        """
        absorbed: dict = {"absorbed": 0, "conflicts": {}, "rejected": 0}
        if self.two_way:
            absorbed = self.absorb_edits()
        conflicts: dict[str, str] = dict(absorbed["conflicts"])

        records = self._svc.get_records()
        entities, links_by_record, inbound = self._graph_view()

        rendered: dict[str, RenderedNote] = {}
        tag_members: dict[str, list[tuple[str, str]]] = {}
        session_members: dict[str, list[tuple[str, str]]] = {}
        summary_by_ref: dict[tuple[str, str], tuple[str, str]] = {}
        for rec in records:
            try:
                note = render_record(rec, entities=links_by_record.get(self._link_key(rec)))
            except Exception:
                logger.debug(
                    "vault: failed to render record %s", getattr(rec, "id", "?"), exc_info=True
                )
                continue
            # Last writer wins on a basename collision (ids are unique in practice).
            rendered[note.relpath] = note
            base = Path(note.relpath).stem
            summary_by_ref[self._link_key(rec)] = (
                base,
                " ".join((rec.text or str(rec.value or "")).split())[:100],
            )
            for tag in note.tags:
                tag_members.setdefault(tag, []).append((base, note.title))
            if rec.kind.value == "episodic" and rec.conversation_id:
                session_members.setdefault(rec.conversation_id, []).append((base, note.title))

        # Entity pages (§5.1) — compiled truth + append-only timeline.
        for entity in entities:
            try:
                rendered_entity = self._render_entity(entity, inbound, summary_by_ref)
            except Exception:
                logger.debug("vault: entity page failed for %s", entity.id, exc_info=True)
                continue
            rendered[rendered_entity.relpath] = rendered_entity

        # Tag hubs + session hubs — the two link targets record pages point at.
        for tag, members in tag_members.items():
            hub = render_tag_hub(tag, members)
            rendered[hub.relpath] = hub
        for conversation_id, members in session_members.items():
            hub = render_session_hub(conversation_id, members)
            rendered[hub.relpath] = hub

        # Root index.
        try:
            rendered[_INDEX_NAME] = render_index(records, entities=entities, mode=self._mode)
        except Exception:
            logger.debug("vault: index render failed", exc_info=True)

        # Reconcile against the manifest.
        old_manifest = self._load_manifest()
        new_manifest: dict[str, str] = {}
        written = 0
        for relpath, note in rendered.items():
            if relpath in conflicts:
                # The human's page stands. Keep its manifest entry so the prune pass
                # below does not delete the very file we refused to overwrite.
                if relpath in old_manifest:
                    new_manifest[relpath] = old_manifest[relpath]
                continue
            new_manifest[relpath] = hashlib.sha256(note.content.encode("utf-8")).hexdigest()
            # 🔴 Compare against the BYTES ON DISK, not against the manifest digest.
            # The old fast path skipped the write whenever the freshly rendered digest
            # equalled the manifest entry — which says "the projection has not changed",
            # NOT "the file still holds it". So a hand-edited page in `mirror` mode was
            # never restored, silently contradicting the one thing mirror mode promises.
            # A read costs less than the write it avoids, and the manifest keeps its real
            # job: knowing which files are ours to prune.
            try:
                if (self._dir / relpath).read_text(encoding="utf-8") == note.content:
                    continue  # byte-identical on disk — nothing to do
            except OSError:
                pass
            try:
                atomic_write(self._dir / relpath, note.content, fsync=False)
                written += 1
            except OSError:
                logger.debug("vault: write failed for %s", relpath, exc_info=True)
                new_manifest.pop(relpath, None)

        # Prune files that were ours but are no longer produced. Only manifest
        # entries: a page the human created is not ours to delete.
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

        # The drop box has to be visible to be usable, so make it exist. Created here
        # rather than in `seed()` because it is structure, not content — and `sweep_raw`
        # still short-circuits on an empty one before it opens a knowledge store.
        try:
            (self._dir / _RAW_DIR).mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.debug("vault: could not create raw/", exc_info=True)
        swept = self.sweep_raw(knowledge=knowledge, enqueue=enqueue)
        seeded = self.seed(starter_seeds(self._mode))

        summary = {
            "records": len(records),
            "files": len(new_manifest),
            "written": written,
            "pruned": pruned,
            "mode": self._mode,
            "absorbed": absorbed["absorbed"],
            "rejected": absorbed["rejected"],
            "conflicts": len(conflicts),
            "raw_ingested": swept["ingested"],
            "seeded": seeded["written"],
        }
        logger.info("memory vault synced: %s", summary)
        return summary

    def _render_entity(
        self,
        entity: "Entity",
        inbound: dict[str, list[dict]],
        summary_by_ref: dict[tuple[str, str], tuple[str, str]],
    ) -> RenderedNote:
        """Build one entity page, carrying its existing timeline through untouched."""
        rows = inbound.get(entity.id, [])
        compiled: list[tuple[str, str]] = []
        backlinks: list[str] = []
        evidence: list[str] = []
        seen_bases: set[str] = set()
        for row in rows:
            ref = (str(row.get("from_kind") or ""), str(row.get("from_ref") or ""))
            known = summary_by_ref.get(ref)
            if known is not None and known[0] not in seen_bases:
                seen_bases.add(known[0])
                backlinks.append(known[0])
                if ref[0] == "semantic":
                    compiled.append(known)
            # Evidence refs are backticked, NOT wikilinked: the timeline is
            # append-only, so a line outlives the record it cites, and a wikilink to a
            # pruned page would make the broken-link lint fire on the vault's own
            # honest history.
            stamp = str(row.get("created_at") or "")[:19]
            context = " ".join(str(row.get("context") or "").split())[:160]
            line = f"- {stamp} — `{ref[1]}`" + (f" — {context}" if context else "")
            evidence.append(line)
        existing_body = ""
        target = self._dir / f"{_ENTITIES_DIR}/{_entity_basename(entity)}.md"
        try:
            existing_body = split_page(target.read_text(encoding="utf-8"))[1]
        except OSError:
            pass
        return render_entity_page(
            entity,
            compiled=compiled,
            backlinks=backlinks,
            evidence=evidence,
            existing_body=existing_body,
        )

    # ── §5.2: edits flow back ────────────────────────────────────────────────

    def absorb_edits(self) -> dict:
        """Read hand-edited pages back into memory. ``two_way`` only.

        For every page the manifest says is ours, compare the on-disk body hash to the
        ``source_hash`` its own frontmatter carries:

        * **equal** → nobody touched it; skip (this is the cheap path, and the reason
          the hash must not cover the frontmatter the sync itself rewrites);
        * **different** → a human edited it. If the page parses confidently, the edit
          goes through ``MemoryService.apply_vault_edit`` — the normal write path, so
          the same key validation, the same S5 injection scan, the same reversible
          ``memory_events`` row with ``source: vault_edit``;
        * **different but not confidently parseable** → the page is left EXACTLY as the
          human wrote it, stamped ``sync_conflict: <reason>`` in frontmatter, and
          reported by the lint. Stamping the frontmatter does not change the body hash,
          so the page stays flagged until a human resolves it — no silent overwrite, no
          dropped edit, no guessed merge.

        Returns ``{"absorbed": n, "rejected": n, "conflicts": {relpath: reason}}``.
        """
        out: dict = {"absorbed": 0, "rejected": 0, "conflicts": {}}
        if not self.two_way:
            return out
        for relpath in sorted(self._load_manifest()):
            page = self._dir / relpath
            try:
                text = page.read_text(encoding="utf-8")
            except OSError:
                continue  # the projection below will re-create it
            block, body = split_page(text)
            fm = parse_frontmatter(block)
            claimed = str(fm.get(_HASH_KEY) or "")
            if not claimed:
                continue  # pre-hash page (or hand-made) — the projection rewrites it
            if claimed == body_hash(body):
                continue  # untouched
            ok, reason = self._apply_page_edit(relpath, fm, body)
            if ok:
                out["absorbed"] += 1
                continue
            out["rejected"] += 1
            out["conflicts"][relpath] = reason
            self._flag_conflict(page, block, body, reason)
        if out["absorbed"] or out["conflicts"]:
            logger.info(
                "memory vault absorbed %d edit(s), %d conflict(s): %s",
                out["absorbed"],
                len(out["conflicts"]),
                sorted(out["conflicts"]),
            )
        return out

    def _apply_page_edit(self, relpath: str, fm: dict, body: str) -> tuple[bool, str]:
        """Apply one edited page, or say why it cannot be applied.

        Every refusal path returns ``(False, reason)`` and writes NOTHING. The bar for
        "confidently parseable" is deliberately high — a wrong merge here is
        unrecoverable in a way a refused one never is.
        """
        key = str(fm.get("id") or "")
        kind = str(fm.get("kind") or "")
        if not key:
            return (False, "no id in frontmatter")
        if kind not in _EDITABLE_KINDS:
            return (False, f"{kind or 'unknown'} pages are read-only")
        value = extract_edited_value(body)
        if value is None:
            return (False, "cannot locate the edited value (H1 heading missing?)")
        if not value.strip():
            return (False, "edited value is empty — delete the page to propose removal")
        # A concurrent store write is not a refusal: §5.2 makes the human authoritative.
        # It IS worth telling them about, and the previous value stays recoverable
        # through the `memory_events` row this write logs (`undo_event` restores it).
        stale = False
        projected = str(fm.get("updated_at") or "")
        if projected:
            current = self._svc.get_semantic(key) or {}
            live = str(current.get("updated_at") or "")
            stale = bool(live) and live != projected
        ok, detail = self._svc.apply_vault_edit(key, value)
        if not ok:
            return (False, detail)
        if stale:
            logger.info(
                "vault edit to %s won over a concurrent store write (undo via the "
                "memory event log)",
                key,
            )
        return (True, detail)

    def _flag_conflict(self, page: Path, block: str, body: str, reason: str) -> None:
        """Stamp ``sync_conflict`` into a page's frontmatter, leaving the body alone.

        The body is written back byte-for-byte, so the human's text survives and its
        hash still disagrees with ``source_hash`` — the page keeps reporting itself as
        unresolved instead of quietly becoming "clean" because we touched it.
        """
        fm = parse_frontmatter(block)
        if str(fm.get(_CONFLICT_KEY) or "") == reason:
            return  # already flagged with this reason — don't churn the file
        fields = [(k, v) for k, v in fm.items() if k not in (_CONFLICT_KEY, _HASH_KEY)]
        fields.append((_CONFLICT_KEY, reason))
        fields.append((_HASH_KEY, str(fm.get(_HASH_KEY) or "")))
        try:
            atomic_write(page, _frontmatter(fields) + "\n\n" + body.rstrip() + "\n", fsync=False)
        except OSError:
            logger.debug("vault: could not flag conflict on %s", page, exc_info=True)

    # ── §5.5: raw/ capture, starter seeding ──────────────────────────────────

    def sweep_raw(self, *, knowledge: object = None, enqueue: object = None) -> dict:
        """Route files dropped in ``raw/`` into the KNOWLEDGE ingest queue.

        The boundary holds inside the vault dir: a file the user dropped is one of
        *their documents*, not a memory the assistant formed, so it becomes a knowledge
        item and never a memory row. Swept files move to ``raw/.ingested/`` rather than
        being deleted — the sweep's job is to hand the file over, not to be the thing
        that loses it.

        No new watcher: the sync pass is the sweep. ``enqueue`` (the gateway's ingest
        queue) is optional — without it the item is left ``processing_status='queued'``
        and the queue's own ``recover_pending()`` picks it up, so a sweep on the
        consolidation cadence still gets ingested.
        """
        out = {"ingested": 0, "failed": 0}
        raw = self._dir / _RAW_DIR
        if not raw.is_dir():
            return out
        candidates = [
            p
            for p in sorted(raw.iterdir())
            if p.is_file() and not p.is_symlink() and not p.name.startswith(".")
        ]
        if not candidates:
            return out
        if knowledge is None:
            from personalclaw.knowledge import get_knowledge_store

            knowledge = get_knowledge_store()
        done = self._dir / _RAW_DONE_DIR
        for src in candidates:
            try:
                content = ""
                if src.suffix.lower() in (".md", ".markdown", ".txt", ".text", ""):
                    content = src.read_text(encoding="utf-8", errors="replace")[:_MAX_BODY]
                done.mkdir(parents=True, exist_ok=True)
                dest = done / src.name
                src.replace(dest)
                item_id = knowledge.create_typed_item(  # type: ignore[attr-defined]
                    item_type="note",
                    title=src.stem or src.name,
                    content=content,
                    provider="native",
                    tags=["vault-raw"],
                    extra={"file_path": str(dest)},
                )
                if not item_id:
                    out["failed"] += 1
                    continue
                knowledge.update_item(  # type: ignore[attr-defined]
                    item_id, processing_status="queued", touch=False
                )
                if callable(enqueue):
                    enqueue(item_id)
                out["ingested"] += 1
            except Exception:
                logger.debug("vault: raw sweep failed for %s", src, exc_info=True)
                out["failed"] += 1
        if out["ingested"]:
            logger.info("memory vault raw sweep: %d file(s) → knowledge", out["ingested"])
        return out

    def seed(self, seeds: dict[str, str]) -> dict:
        """Write starter pages, but only where doing so cannot destroy anything.

        A file is written when it is **missing**, or when it is **pristine** — present
        and still carrying the ``source_hash`` its own body hashes to, i.e. nobody has
        edited it. A page the human touched is left alone. That is what makes shipped
        starter context safe: seeding can never be the mechanism that overwrites the
        thing the user wrote.

        Seeded files are deliberately NOT recorded in the manifest — the projection
        does not produce them, so a manifest entry would make the next sync's prune
        pass delete them.
        """
        out = {"written": 0, "kept": 0}
        for relpath, content in sorted(seeds.items()):
            target = self._dir / relpath
            try:
                existing = target.read_text(encoding="utf-8")
            except OSError:
                existing = ""
            if existing:
                block, body = split_page(existing)
                claimed = str(parse_frontmatter(block).get(_HASH_KEY) or "")
                if not claimed or claimed != body_hash(body):
                    out["kept"] += 1  # hand-edited (or unhashed) — never clobber it
                    continue
                if existing == content:
                    out["kept"] += 1  # already current
                    continue
            try:
                atomic_write(target, content, fsync=False)
                out["written"] += 1
            except OSError:
                logger.debug("vault: seed write failed for %s", relpath, exc_info=True)
        return out

    # ── §5.3: vault lints ───────────────────────────────────────────────────

    def lint_flags(self) -> list[tuple[str, str, str]]:
        """Deterministic vault checks as ``(check, key, detail)`` triples.

        Measured against what is ON DISK, not against the renderer's own intentions —
        the renderer derives entity links and backlinks from the same ``mem_links``
        rows, so a check that asked the renderer would be comparing it to itself and
        could never fail.
        """
        flags: list[tuple[str, str, str]] = []
        if not self._dir.is_dir():
            return flags
        pages: dict[str, tuple[dict, str]] = {}
        for path in sorted(self._dir.rglob("*.md")):
            rel = path.relative_to(self._dir).as_posix()
            if rel.startswith((_RAW_DIR + "/", ".")):
                continue
            try:
                block, body = split_page(path.read_text(encoding="utf-8"))
            except OSError:
                continue
            pages[rel] = (parse_frontmatter(block), body)

        basenames = {Path(rel).stem for rel in pages}
        manifest = set(self._load_manifest())
        seeded = set(starter_seeds(self._mode))
        inbound: dict[str, set[str]] = {}

        for rel, (fm, body) in pages.items():
            stem = Path(rel).stem
            page_type = str(fm.get("type") or "")
            conflict = str(fm.get(_CONFLICT_KEY) or "")
            if conflict:
                flags.append(("vault_conflict", rel, f"{conflict} — edit not applied"))
            claimed = str(fm.get(_HASH_KEY) or "")
            if claimed and claimed != body_hash(body) and not conflict:
                flags.append(
                    ("vault_stale_hash", rel, "edited since the last sync — not yet read back")
                )
            for target in _WIKILINK_RE.findall(body):
                target = target.strip()
                if not target:
                    continue
                if target not in basenames:
                    flags.append(("vault_broken_link", rel, f"[[{target}]] has no page"))
                    continue
                # Only a RECORD page's link creates a symmetry obligation. The index and
                # the hub pages link an entity as navigation, and demanding they be
                # listed back would make every clean vault report asymmetry — a rail
                # that fires on correct output stops meaning anything.
                if page_type in _RECORD_PAGE_TYPES:
                    inbound.setdefault(target, set()).add(stem)
            if rel not in manifest and rel not in seeded:
                flags.append(
                    ("vault_orphan_page", rel, "not produced by the projection — yours to keep")
                )

        # Backlink symmetry (§5.3): if a record page links an entity, that entity's
        # page must list the record back. Asymmetry means the two pages were written by
        # different syncs — a stale entity page next to a fresh record page.
        for rel, (fm, body) in pages.items():
            if str(fm.get("type") or "") != "entity":
                continue
            stem = Path(rel).stem
            listed = {t.strip() for t in _WIKILINK_RE.findall(body)}
            for source in sorted(inbound.get(stem, set())):
                if source not in listed:
                    flags.append(
                        (
                            "vault_backlink_asymmetry",
                            rel,
                            f"[[{source}]] links here but is not listed back",
                        )
                    )
        return flags


# ── §5.5: starter seeding ───────────────────────────────────────────────────


def starter_seeds(mode: str) -> dict[str, str]:
    """The starter pages a fresh vault gets: how to read it, and how ``raw/`` works.

    Hashed like every other page, so :meth:`MemoryVault.seed` can tell "still the
    shipped text" from "the user rewrote this" and only ever replaces the former.
    """
    two_way = mode == "two_way"
    readme = [
        "# Reading this vault",
        "",
        "Every page here is projected from PersonalClaw's memory store.",
        "",
        "- `facts/` — one page per remembered fact, preference or note.",
        "- `episodic/` — conversation fragments. Evidence: read-only, always.",
        "- `entities/` — one page per person/project/tool memory knows about:",
        "  compiled truth on top, an append-only timeline below.",
        "- `tags/` — hub pages that make the graph view cluster.",
        "- `raw/` — drop a file here and the next sync files it under **Knowledge**,",
        "  never into memory.",
        "",
        "## Frontmatter",
        "",
        "`source_hash` is a hash of everything below the frontmatter fence. It is how",
        "the sync knows whether you edited a page. `sync_conflict` means the sync read",
        "your edit, could not apply it safely, and left your text untouched.",
        "",
        "## Editing",
        "",
        (
            "This vault is in **two_way** mode. Edit a `facts/` page between its H1 and"
            " the `personalclaw:generated` marker and the next sync writes your version"
            " into memory — your edit wins over the stored value. Everything below the"
            " marker is regenerated, so changes there are lost."
            if two_way
            else "This vault is in **mirror** mode: pages are regenerated from the store"
            " and your edits WILL be overwritten. Set `memory.vault_mode` to `two_way`"
            " in Settings → Memory to edit memory from here."
        ),
    ]
    # NOTE: no seed inside `raw/`. A guide page there would be swept into Knowledge by
    # the very sweep it documents (a real bug this caught: the seeded `raw/README.md`
    # was ingested as a user document on the next sync). Rather than teach the sweep an
    # exception — which would also swallow a `README.md` the user genuinely dropped —
    # `raw/` stays a pure drop box and is documented here, once.
    return {
        "README.md": compose_page([("type", "guide"), ("kind", "guide")], "\n".join(readme)),
    }


def extract_edited_value(body: str) -> str | None:
    """The human-editable region of a record page, or ``None`` if it cannot be found.

    The region is everything between the ``# H1`` heading and the generated marker (or
    the end of the body when a page has no generated section). ``None`` — not ``""`` —
    when the H1 is gone, because a page whose structure the human dismantled is a page
    we must not guess about: returning empty text would read as "the user cleared this
    fact" and delete something they meant to keep.
    """
    lines = body.replace("\r\n", "\n").split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.startswith("# ")), None)
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].strip() == _GENERATED_MARKER or lines[i].startswith("## "):
            end = i
            break
    return "\n".join(lines[start + 1 : end]).strip()


# ── config + wiring ─────────────────────────────────────────────────────────


def vault_mode_from_config() -> str:
    """``memory.vault_mode`` — ``off`` when it cannot be read.

    Fails to ``off``, not to ``mirror``: an unreadable config must not start writing a
    projection of the user's memory to a path nobody confirmed.
    """
    from personalclaw.config.loader import AppConfig

    try:
        mode = str(getattr(AppConfig.load().memory, "vault_mode", "off") or "off")
    except Exception:
        logger.debug("vault: config unreadable", exc_info=True)
        return "off"
    return mode if mode in MEMORY_VAULT_MODES else "off"


def vault_path_from_config() -> Path:
    """Where the vault lives, regardless of mode.

    Separate from :func:`vault_dir_from_config` because "sync now" exports to the
    configured path even with the vault off — a user should be able to look at a vault
    before committing to keeping one in sync.
    """
    from personalclaw.config.loader import AppConfig, config_dir

    rel = (getattr(AppConfig.load().memory, "vault_path", "") or "memory-vault").strip()
    p = Path(rel).expanduser()
    return p if p.is_absolute() else (config_dir() / rel)


def vault_dir_from_config() -> Path | None:
    """The configured vault directory, or None when ``vault_mode`` is ``off``."""
    if vault_mode_from_config() == "off":
        return None
    return vault_path_from_config()


def vault_for(service: "MemoryService") -> MemoryVault | None:
    """Build a mode-aware vault for ``service`` from config, or None when off."""
    mode = vault_mode_from_config()
    if mode == "off":
        return None
    return MemoryVault(service, vault_path_from_config(), mode=mode)


def mirror_after_consolidation(service: "MemoryService") -> None:
    """Best-effort post-consolidation sync — the primary freshness trigger.

    Wired into ``ConversationManager.consolidate_session``; never raises so a
    mirror hiccup can't break session sealing. In ``two_way`` this is also the
    on-cadence half of §5.2: edits made in the vault between sessions are absorbed
    when the next session seals, with no watcher and no daemon."""
    try:
        vault = vault_for(service)
        if vault is not None:
            vault.sync()
    except Exception:
        logger.debug("memory vault: post-consolidation mirror failed", exc_info=True)
