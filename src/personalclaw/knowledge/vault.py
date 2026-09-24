"""Knowledge ↔ markdown projection: the library as plain files the owner owns (KL-20).

A knowledge item used to be reachable only through ``knowledge.db``. This module projects
every item to a human-readable markdown file under the owner's home, with YAML front-matter
carrying identity and relations, and reads an owner's edit **back** — which is the whole
difference between an export and ownership.

**It is the memory vault's projector, pointed at the knowledge store — not a second one.**
The atom says so explicitly, and the reason is the swallowed-write family's worst shape: two
projectors writing the owner's files, each treating the other's bytes as a hand edit,
overwriting each other forever with nothing in either's summary saying so. So every mechanic
MGAV-6 already ships is imported from :mod:`personalclaw.memory_vault` rather than
reimplemented:

* **mode config** — the same three-valued vocabulary (``MEMORY_VAULT_MODES``:
  ``off`` / ``mirror`` / ``two_way``), resolved the same way and failing to ``off`` for the
  same reason (:func:`vault_mode_from_config`);
* **the page format and its wikilink projection** — :func:`~personalclaw.memory_vault.slug`,
  :func:`~personalclaw.memory_vault.compose_page`,
  :func:`~personalclaw.memory_vault.split_page`,
  :func:`~personalclaw.memory_vault.parse_frontmatter`,
  :data:`~personalclaw.memory_vault.GENERATED_MARKER` and
  :func:`~personalclaw.memory_vault.extract_edited_value`, so an Obsidian vault of memory
  and one of knowledge are the same artifact in two directories;
* **the sync pass** — the same ``absorb → project → collect deletions`` order, for the
  reason MGAV-6 documents: re-render first and the owner's text is gone before it is read;
* **its verification** — the same ``(check, key, detail)`` flag shape, the same
  ``sync_conflict`` front-matter stamp and the same broken-link / orphan-page checks over
  :data:`~personalclaw.memory_vault.WIKILINK_RE`, so a refusal is visible in the file the
  owner was just editing as well as in a health surface.

**The content hash already excludes the field it writes.**
:func:`~personalclaw.memory_vault.body_hash` covers the BODY only and ``source_hash`` lives
in the front-matter, so writing the hash cannot change the value being hashed. That is not a
property this module adds — it is why MGAV-6's hash is body-only, and inheriting it is what
makes a projection unable to retrigger itself. ``test_projection_does_not_retrigger_itself``
is the proof rather than this paragraph.

What is genuinely NEW here, because memory did not need it:

**A ledger, not a manifest.** MGAV-6 keeps ``.vault-manifest.json`` and re-renders every
record on every sync — fine for a few hundred facts, wrong for a library. The projection
backlog is instead keyed in ``vault_projections`` (see the DDL): "which items disagree with
their ledger row". A keyed backlog returns rows only when there is real work and 0 exactly
when the vault is settled, which is the contract KL-14's sub-batch loop is written against.
A cursor-and-scan design would have examined one window per TICK, so an edit to the
ten-thousandth item would have waited hours behind items with nothing to say.

**Deletion is explicit in BOTH directions, and neither direction destroys.**
A file the owner removed is tombstoned (``owner_deleted``) and never re-created — "no row"
would mean "project it again", so the absence has to be recorded rather than inferred. It
does **not** delete the item: a missing file is an ambiguous signal (a moved directory, a
sync client, a bad backup restore) and deleting the owner's knowledge on it would be
unrecoverable. An item removed in the app has its file unlinked and its row forgotten, which
is why the ledger deliberately carries **no foreign key** — ``ON DELETE CASCADE`` would take
away the only record that a file existed.

**A two-sided change SURFACES.** The ledger stores both what the store said
(``projected_updated_at``) and what we wrote (``projected_body_hash``), so "the owner edited
the file", "the store moved" and "BOTH moved" are three distinguishable states rather than
one. The third writes nothing to the store, leaves the file byte-for-byte, stamps
``sync_conflict`` into its front-matter and stands the row up in
:meth:`KnowledgeVault.lint_flags` and the Doctor's ``knowledge-vault`` probe. With only one
of the two recorded, that state is indistinguishable from a plain edit and every projection
resolves silently toward the database.

**Gated off by default.** ``knowledge.vault_mode`` defaults to ``off`` and an unreadable
config resolves to ``off``: a control that writes the owner's files must be chosen. Its
proof is not that the flag exists but that flipping it makes files appear — see
``test_gate_off_writes_nothing`` next to ``test_gate_on_projects_files``.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import MEMORY_VAULT_MODES
from personalclaw.memory_vault import (
    CONFLICT_KEY,
    GENERATED_MARKER,
    HASH_KEY,
    WIKILINK_RE,
    body_hash,
    compose_page,
    extract_edited_value,
    frontmatter,
    parse_frontmatter,
    slug,
    split_page,
)

logger = logging.getLogger(__name__)

#: One vocabulary for both vaults. Imported rather than re-spelled: two tuples that happen to
#: agree today are how a mode becomes valid in one surface and silently `off` in the other.
KNOWLEDGE_VAULT_MODES = MEMORY_VAULT_MODES

#: Where item pages live inside the vault. Obsidian resolves ``[[wikilinks]]`` by basename
#: regardless of directory, so grouping is navigation only.
_ITEMS_DIR = "items"
_TAGS_DIR = "tags"
_README = "README.md"

#: The page ``type``, so a mixed vault (memory pages beside knowledge pages) is still
#: unambiguous to a reader and to the lint.
PAGE_TYPE = "knowledge_item"

#: Front-matter emitted in this order, so a re-render of an unchanged item is byte-identical
#: and the projection cannot report a change it did not make.
_FM_ORDER = (
    "type",
    "id",
    "title",
    "item_type",
    "kind",
    "url",
    "source",
    "tags",
    "collections",
    "relations",
    "citations",
    "created_at",
    "updated_at",
)

#: Largest item content this projection will write. An item above it is NOT truncated —
#: truncating and then reading the file back would write the truncation into the store, which
#: is data loss dressed as a bound. It is refused instead, recorded as a conflict so it
#: leaves the backlog, and reported. The bound exists so one pathological item cannot stall
#: the maintenance host, not to edit anybody's document.
MAX_ITEM_BYTES = 1_000_000

#: How many candidate files one sub-batch reads, and how many pages it writes. The stat-only
#: scan below is deliberately NOT bounded by this: `os.stat` over a ledger is microseconds
#: per row, and bounding the cheap half is what would make an edit wait for hours.
DEFAULT_BATCH = 50

#: Everything `slug` would rewrite, applied to a title first so the basename does not carry
#: `slug`'s collision suffix on every single page. Same character class as `slug`'s own.
_WORD_BREAK = re.compile(r"[^A-Za-z0-9._-]+")


# ── config ──────────────────────────────────────────────────────────────────


def vault_mode_from_config() -> str:
    """``knowledge.vault_mode`` — ``off`` when it cannot be read.

    Fails to ``off``, not to ``mirror``, for the reason its memory twin does: an unreadable
    config must not start writing a projection of the owner's library to a path nobody
    confirmed.
    """
    from personalclaw.config.loader import AppConfig

    try:
        mode = str(getattr(AppConfig.load().knowledge, "vault_mode", "off") or "off")
    except Exception:  # noqa: BLE001 — an unreadable config is "off", never a crash
        logger.debug("knowledge vault: config unreadable", exc_info=True)
        return "off"
    return mode if mode in KNOWLEDGE_VAULT_MODES else "off"


def vault_path_from_config() -> Path:
    """Where the projection lives, regardless of mode."""
    from personalclaw.config.loader import AppConfig, config_dir

    try:
        rel = (getattr(AppConfig.load().knowledge, "vault_path", "") or "").strip()
    except Exception:  # noqa: BLE001
        rel = ""
    rel = rel or "knowledge-vault"
    p = Path(rel).expanduser()
    return p if p.is_absolute() else (config_dir() / rel)


def vault_for(store: Any) -> "KnowledgeVault | None":
    """A mode-aware projector for *store*, or None when ``vault_mode`` is ``off``."""
    mode = vault_mode_from_config()
    if mode == "off":
        return None
    return KnowledgeVault(store, vault_path_from_config(), mode=mode)


# ── rendering ───────────────────────────────────────────────────────────────


def page_basename(item: dict) -> str:
    """The stable basename for an item's page: title-derived, id-suffixed.

    Title first because the point of the projection is a directory a human can read, and the
    id suffix because two items may legitimately share a title and a wikilink target must be
    unique. `slug` is MGAV-6's — one sanitizer, so a `[[link]]` written by either vault
    resolves the same way.
    """
    title = " ".join(str(item.get("title") or "").split())[:80]
    # Pre-normalised so `slug` is a no-op on the result rather than appending its
    # collision hash: `slug` suffixes a short digest whenever sanitising CHANGED the
    # string, and a title with a space in it always changes, so every basename would
    # carry a meaningless `-54d83f`. The id prefix is what actually disambiguates two
    # items with the same title, and it is already in the name.
    safe = _WORD_BREAK.sub("-", title).strip("-")
    return slug(f"{safe}-{str(item.get('id') or '')[:8]}", fallback="item")


def _relation_lines(relations: list[dict], names: dict[str, str], item_id: str) -> list[str]:
    """``[[wikilink]]`` lines for the typed edges on either leg of *item_id*.

    Derived from ``item_relations`` rows, never from scraping the body — the invariant MGAV-6
    states and the reason its broken-link lint means anything. A relation whose other end has
    no page is skipped rather than linked: a projection that emits links to pages it does not
    create makes its own verification fire on correct output.
    """
    out: list[str] = []
    for rel in relations:
        src = str(rel.get("source_item_id") or "")
        dst = str(rel.get("target_item_id") or "")
        other = dst if src == item_id else src
        base = names.get(other, "")
        rel_type = str(rel.get("relation_type") or "")
        # A typeless row is skipped rather than labelled: `add_item_relation` refuses anything
        # outside `semantics.RELATION_TYPES`, so inventing a name here would put a vocabulary
        # in the owner's files that no writer can produce and no reader recognises.
        if not base or not rel_type:
            continue
        arrow = "→" if src == item_id else "←"
        out.append(f"- {rel_type} {arrow} [[{base}]]")
    return sorted(set(out))


def render_item(
    item: dict,
    *,
    relations: list[dict],
    relation_names: dict[str, str],
    collections: list[str],
    citations: list[dict],
) -> tuple[str, str]:
    """One item → ``(relpath, page_bytes)``.

    The body is ``# title`` / the item's content / the generated marker / machine-owned
    sections. That shape is not decoration: it is what makes
    :func:`~personalclaw.memory_vault.extract_edited_value` able to hand the owner's version
    back, and it is identical to a memory record page so one parser reads both.
    """
    item_id = str(item.get("id") or "")
    base = page_basename(item)
    relpath = f"{_ITEMS_DIR}/{base}.md"
    tags = [str(t) for t in (item.get("tags") or []) if str(t).strip()]

    rel_lines = _relation_lines(relations, relation_names, item_id)
    cite_lines = sorted(
        {
            f"- [{c.get('marker') or '?'}] {' '.join(str(c.get('excerpt') or '').split())[:120]}"
            for c in citations
        }
    )
    rel_pairs = sorted(
        {
            "{}:{}".format(
                r["relation_type"],
                (
                    r.get("target_item_id")
                    if str(r.get("source_item_id") or "") == item_id
                    else r.get("source_item_id")
                ),
            )
            for r in relations
            if r.get("relation_type")
        }
    )

    shelves = sorted(collections)
    fields: dict[str, object] = {
        "type": PAGE_TYPE,
        "id": item_id,
        "title": str(item.get("title") or ""),
        "item_type": str(item.get("item_type") or item.get("type") or ""),
        "kind": str(item.get("kind") or ""),
        "url": str(item.get("url") or ""),
        "source": str(item.get("source") or ""),
        "tags": tags,
        "collections": shelves,
        # Identity-bearing rather than presentational: the front-matter carries the RELATION
        # as data (type + target id) while the body carries it as a link a human follows.
        # Dropping either would make the file a worse copy of one of the two.
        "relations": rel_pairs,
        "citations": sorted({str(c.get("source_item_id") or "") for c in citations if c}),
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
    }
    fm = [(k, fields[k]) for k in _FM_ORDER if fields.get(k) not in ("", [], None)]

    title = str(item.get("title") or item_id) or item_id
    body_parts = [f"# {title}", "", str(item.get("content") or "").rstrip(), "", GENERATED_MARKER]
    if rel_lines:
        body_parts += ["", "## Relations", *rel_lines]
    if tags:
        body_parts += ["", "## Tags", " ".join(f"[[tag-{slug(t)}]]" for t in tags)]
    if shelves:
        body_parts += ["", "## Collections", *[f"- {c}" for c in shelves]]
    if cite_lines:
        body_parts += ["", "## Citations", *cite_lines]
    return relpath, compose_page(fm, "\n".join(body_parts))


def render_tag_hub(tag: str, members: list[str]) -> tuple[str, str]:
    """A tag hub page, so a ``[[tag-x]]`` link in an item page resolves to something."""
    base = f"tag-{slug(tag)}"
    body = [f"# {tag}", "", GENERATED_MARKER, "", *[f"- [[{m}]]" for m in sorted(members)]]
    return f"{_TAGS_DIR}/{base}.md", compose_page([("type", "tag"), ("tag", tag)], "\n".join(body))


def readme(mode: str) -> str:
    """The one guide page, hashed like every other so a rewritten one is never replaced."""
    two_way = mode == "two_way"
    lines = [
        "# Reading this knowledge vault",
        "",
        "Every page under `items/` is one knowledge item, projected from PersonalClaw's",
        "library. Front-matter carries its identity (`id`, `kind`, `url`) and its relations",
        "(`relations`, `citations`, `collections`, `tags`); the body carries the same",
        "relations as wikilinks you can follow.",
        "",
        "## Frontmatter",
        "",
        "`source_hash` hashes everything below the frontmatter fence — it is how the sync",
        "knows whether you edited a page. `sync_conflict` means the sync read your edit,",
        "could not apply it safely, and left your text exactly as you wrote it.",
        "",
        "## Editing",
        "",
        (
            "This vault is in **two_way** mode. Edit the text between the `# ` heading and"
            " the `personalclaw:generated` marker and the next maintenance pass writes your"
            " version back into the library — your edit wins. Everything below the marker is"
            " regenerated. Delete a page and it stays deleted: it is not re-created, and the"
            " item is not deleted either — remove it in the app for that. If a page changed"
            " here AND in the app since the last sync, nothing is written on either side and"
            " the page is flagged."
            if two_way
            else "This vault is in **mirror** mode: pages are regenerated from the library and"
            " your edits WILL be overwritten. Set `knowledge.vault_mode` to `two_way` to edit"
            " the library from here."
        ),
    ]
    return compose_page([("type", "guide")], "\n".join(lines))


# ── the projector ───────────────────────────────────────────────────────────


class KnowledgeVault:
    """Reconciles the markdown projection against the knowledge store.

    Stateless beyond the ledger it reads and writes; construct freely (or via
    :func:`vault_for`). One :meth:`sync_batch` is one bounded unit of work and returns how
    much it did, which is exactly what KL-14's sub-batch loop consumes.
    """

    def __init__(self, store: Any, vault_dir: Path, *, mode: str = "mirror") -> None:
        self._store = store
        self._dir = vault_dir
        self._mode = mode if mode in KNOWLEDGE_VAULT_MODES else "mirror"

    @property
    def path(self) -> Path:
        return self._dir

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def two_way(self) -> bool:
        return self._mode == "two_way"

    # ── one bounded pass ────────────────────────────────────────────────────

    def sync_batch(self, *, max_items: int = DEFAULT_BATCH) -> dict:
        """One bounded sub-batch: absorb, then project, then collect deletions.

        The order is MGAV-6's and it is the whole design — project first and the owner's text
        is overwritten before anything read it. Returns a summary whose ``units`` is the
        number the maintenance pass reports: it drains to 0 when the vault is settled, which
        is what stops the host claiming another sub-batch.
        """
        bound = max(1, int(max_items))
        out: dict[str, Any] = {
            "mode": self._mode,
            "absorbed": 0,
            "conflicts": 0,
            "overwritten": 0,
            "tombstoned": 0,
            "written": 0,
            "deleted": 0,
        }
        absorbed = self._absorb_batch(bound)
        out.update(absorbed)
        out["written"] = self._project_batch(bound)
        out["deleted"] = self._collect_deletions(bound)
        out["units"] = (
            out["absorbed"]
            + out["conflicts"]
            + out["overwritten"]
            + out["tombstoned"]
            + out["written"]
            + out["deleted"]
        )
        # The guide page and the tag hubs are re-asserted only when the pass moved something,
        # or when the vault does not exist yet. A settled vault must cost one backlog query
        # and one stat per page — "bounded" has to hold on the tick where nothing happened,
        # which is almost every tick.
        if out["units"] or not (self._dir / _README).exists():
            self._ensure_static()
        if out["units"]:
            logger.info("knowledge vault synced: %s", out)
        return out

    # ── absorb: the file → store direction ──────────────────────────────────

    def _candidates(self) -> list[dict]:
        """Ledger rows whose file changed since we last LOOKED at it, plus vanished files.

        One ``stat`` per row and no reads: this is the half that has to be cheap, because it
        runs over the whole ledger on every sub-batch. Keyed on ``seen_mtime`` (the mtime at
        the last EXAMINATION) rather than on ``projected_at``, so a page we looked at and
        refused stops being a candidate — keyed the other way, one unresolved conflict would
        burn every sub-batch of every tick and the pass would never return 0.
        """
        rows: list[dict] = []
        for row in self._store.vault_projections():
            if int(row.get("owner_deleted") or 0):
                continue
            relpath = str(row.get("relpath") or "")
            if not relpath:
                continue
            try:
                mtime = (self._dir / relpath).stat().st_mtime
            except OSError:
                # Gone. Recorded as a candidate so the tombstone decision happens in one
                # place with the rest of the reconciliation.
                rows.append({**row, "mtime": None})
                continue
            if abs(float(mtime) - float(row.get("seen_mtime") or 0.0)) > 1e-6:
                rows.append({**row, "mtime": mtime})
        return rows

    def _absorb_batch(self, bound: int) -> dict[str, int]:
        """Read at most *bound* changed files back. Returns the per-outcome counts."""
        out = {"absorbed": 0, "conflicts": 0, "overwritten": 0, "tombstoned": 0}
        for row in self._candidates()[:bound]:
            item_id = str(row.get("item_id") or "")
            relpath = str(row.get("relpath") or "")
            if row.get("mtime") is None:
                # The owner deleted the page. Tombstoned so the projection never re-creates
                # it, and the ITEM is left alone: a missing file is ambiguous (a moved
                # directory, a half-restored backup, a sync client) and deleting somebody's
                # knowledge on an ambiguous signal is the one mistake here that cannot be
                # undone. Surfaced instead — see `lint_flags`.
                self._store.mark_vault_page_deleted(item_id)
                out["tombstoned"] += 1
                continue
            page = self._dir / relpath
            mtime = float(row["mtime"])
            try:
                text = page.read_text(encoding="utf-8")
            except OSError:
                logger.debug("knowledge vault: unreadable page %s", relpath, exc_info=True)
                continue
            block, body = split_page(text)
            fm = parse_frontmatter(block)
            if body_hash(body) == str(row.get("projected_body_hash") or ""):
                # The body is still ours — the owner touched the file (a save with no change,
                # a `touch`, a reverted edit) but not its content. Record the look so it stops
                # being a candidate; nothing to write in either direction.
                self._store.record_vault_examination(item_id, seen_mtime=mtime)
                continue
            if not self.two_way:
                # `mirror` means one direction and says so in the config help and in this
                # vault's own README. The edit is not applied and the page is re-projected
                # immediately — NOT left for the projection half, which is keyed on the
                # store's `updated_at` and would see no reason to run: mirror mode would then
                # detect the edit, count it, and leave the edited file in place forever, which
                # is neither direction. Counted rather than silent so the summary shows it.
                out["overwritten"] += 1
                self._project_one(item_id)
                continue
            ok, reason = self._apply_page_edit(item_id, row, fm, body)
            if ok:
                out["absorbed"] += 1
                # `seen_mtime` is NOT recorded here: applying the edit moves the item's
                # `updated_at`, so the projection half below re-renders this very page and
                # `record_vault_projection` records the mtime of the bytes IT wrote. Recording
                # the pre-render mtime would mean the next pass saw a "changed" file — the
                # self-retrigger this atom names, arriving through the back door.
                continue
            if not reason:
                # A refusal with no reason is a NO-OP, not a conflict: the owner's text is
                # already what the store holds (they reformatted the machine half, or saved a
                # revert). Recording the look retires the candidate; stamping `sync_conflict`
                # here would flag a page that has nothing wrong with it, and a flag that fires
                # on correct output stops meaning anything.
                self._store.record_vault_examination(item_id, seen_mtime=mtime)
                continue
            out["conflicts"] += 1
            # Stamp FIRST, then record the mtime of the stamped file. `_flag_conflict` rewrites
            # the page (front-matter only), so recording the pre-stamp mtime would leave the
            # page a candidate forever: the next pass would see "changed", re-refuse it, and
            # every sub-batch of every tick would be spent on one page only the owner can fix.
            self._flag_conflict(page, block, body, reason)
            try:
                mtime = page.stat().st_mtime
            except OSError:
                pass
            self._store.record_vault_examination(item_id, seen_mtime=mtime, conflict=reason)
        return out

    def _apply_page_edit(self, item_id: str, row: dict, fm: dict, body: str) -> tuple[bool, str]:
        """Apply one edited page, or say why it cannot be applied. Refusals write NOTHING.

        The bar for "confidently parseable" is deliberately high, and the two-sided check is
        the substantive one: if the store's ``updated_at`` no longer matches the value this
        page was rendered from, BOTH sides moved since the projection and there is no version
        of "apply" that is not a silent choice. MGAV-6 makes the human authoritative over a
        concurrent store write because a memory value is one sentence and its previous value
        stays recoverable through ``memory_events``; a knowledge item is a document whose
        overwritten version is gone, so this refuses and surfaces instead.
        """
        if str(fm.get("id") or "") != item_id:
            return (False, "frontmatter id does not match the page's item")
        live = self._store.get_item(item_id)
        if not live:
            return (False, "the item this page projects no longer exists")
        projected = str(row.get("projected_updated_at") or "")
        current = str(live.get("updated_at") or "")
        if projected and current and current != projected:
            return (
                False,
                "changed on BOTH sides since the last sync — nothing was written either way "
                f"(in the app: {current}; this page was rendered from: {projected})",
            )
        # `stop_at_headings=False`: a knowledge item is a document and its own `## ` sections
        # are the owner's, not ours. Left on, absorbing an edited article would truncate it at
        # its first subheading and write the truncation back.
        value = extract_edited_value(body, stop_at_headings=False)
        if value is None:
            return (False, "cannot locate the edited text (the '# ' heading is missing?)")
        if not value.strip():
            return (False, "the edited text is empty — delete the item in the app to remove it")
        if len(value.encode("utf-8")) > MAX_ITEM_BYTES:
            return (False, f"edited text exceeds {MAX_ITEM_BYTES} bytes")
        if value == str(live.get("content") or "").rstrip():
            # No-op, signalled by an EMPTY reason. See the caller: this is the one refusal
            # that must not be stamped as a conflict.
            return (False, "")
        self._store.update_item(item_id, content=value)
        # KL-19's contract: a body that moved invalidates every artifact derived from the old
        # text. Invalidate here (cheap, synchronous) and let KL-14's host rebuild — the same
        # split `restructure.refresh_derived` exists to enforce, reused rather than a second
        # opinion about what an edited body invalidates.
        try:
            from personalclaw.knowledge import restructure

            restructure.refresh_derived(self._store, [item_id], reason="vault edit")
        except Exception:  # noqa: BLE001 — a stale derived layer must not lose the edit
            logger.warning("knowledge vault: derived refresh failed for %s", item_id, exc_info=True)
        return (True, "applied")

    def _flag_conflict(self, page: Path, block: str, body: str, reason: str) -> None:
        """Stamp ``sync_conflict`` into the front-matter, leaving the body alone.

        MGAV-6's mechanic, unchanged: the body is written back byte-for-byte so the owner's
        text survives, and because ``source_hash`` covers the body only, stamping the
        front-matter does not make the page look clean again.
        """
        fm = parse_frontmatter(block)
        if str(fm.get(CONFLICT_KEY) or "") == reason:
            return
        fields = [(k, v) for k, v in fm.items() if k not in (CONFLICT_KEY, HASH_KEY)]
        fields.append((CONFLICT_KEY, reason))
        fields.append((HASH_KEY, str(fm.get(HASH_KEY) or "")))
        try:
            # `frontmatter`, NOT `compose_page`: composing would recompute `source_hash` over
            # the owner's body and the page would read as clean on the next pass — the flag
            # would erase the very disagreement it exists to record.
            atomic_write(page, frontmatter(fields) + "\n\n" + body.rstrip() + "\n", fsync=False)
        except OSError:
            logger.debug("knowledge vault: could not flag %s", page, exc_info=True)

    # ── project: the store → file direction ─────────────────────────────────

    def _project_batch(self, bound: int) -> int:
        """Write at most *bound* out-of-date pages. Returns pages written."""
        batch = self._store.items_needing_vault_projection(bound)
        cohort = frozenset(str(e.get("id") or "") for e in batch)
        written = 0
        for entry in batch:
            item_id = str(entry.get("id") or "")
            try:
                if self._project_one(item_id, cohort):
                    written += 1
            except Exception:  # noqa: BLE001 — one bad item must not stall the vault
                logger.warning("knowledge vault: projection failed for %s", item_id, exc_info=True)
        return written

    def _project_one(self, item_id: str, cohort: frozenset[str] = frozenset()) -> bool:
        item = self._store.get_item(item_id)
        if not item:
            return False
        content = str(item.get("content") or "")
        if len(content.encode("utf-8")) > MAX_ITEM_BYTES:
            # Refused, never truncated — see MAX_ITEM_BYTES. Recorded as a conflict so it
            # leaves the backlog (otherwise the host re-renders it every sub-batch forever)
            # and shows up in the verification surface.
            self._store.record_vault_examination(
                item_id,
                seen_mtime=0.0,
                conflict=f"item is larger than {MAX_ITEM_BYTES} bytes — not projected",
            )
            return False
        refs = self._store.inbound_references(item_id)
        relations = [dict(r) for r in refs.get("relations") or []]
        names = self._relation_names(relations, item_id, cohort)
        collections = [str(c.get("name") or "") for c in refs.get("collections") or []]
        citations = [dict(c) for c in self._store.item_citations(item_id)]
        relpath, content_bytes = render_item(
            item,
            relations=relations,
            relation_names=names,
            collections=[c for c in collections if c],
            citations=citations,
        )
        target = self._dir / relpath
        # Compare against the BYTES ON DISK, not against the ledger digest. MGAV-6 learned
        # this one the hard way: the ledger says "the projection has not changed", NOT "the
        # file still holds it", so a page deleted-and-restored or hand-mangled would never be
        # rewritten in mirror mode.
        if self._read(target) == content_bytes:
            self._record(item_id, relpath, item, content_bytes, target)
            return False
        try:
            atomic_write(target, content_bytes, fsync=False)
        except OSError:
            logger.debug("knowledge vault: write failed for %s", relpath, exc_info=True)
            return False
        # Stale row cleanup BEFORE recording: a retitle changes the basename, so the page the
        # last projection wrote is now an orphan no `orphan_vault_projections` query would
        # ever find (its item still exists). Deleted here, where both names are known.
        old = self._store.vault_projection(item_id)
        old_rel = str((old or {}).get("relpath") or "")
        if old_rel and old_rel != relpath:
            try:
                (self._dir / old_rel).unlink()
            except OSError:
                pass
        self._record(item_id, relpath, item, content_bytes, target)
        return True

    def _record(self, item_id: str, relpath: str, item: dict, page: str, target: Path) -> None:
        """Write the ledger row for a page that is now on disk with exactly these bytes."""
        _, body = split_page(page)
        try:
            mtime = target.stat().st_mtime
        except OSError:
            mtime = 0.0
        self._store.record_vault_projection(
            item_id,
            relpath=relpath,
            updated_at=str(item.get("updated_at") or ""),
            body_hash=body_hash(body),
            projected_at=datetime.now().isoformat(timespec="seconds"),
            seen_mtime=float(mtime),
        )

    def _relation_names(
        self, relations: list[dict], item_id: str, cohort: frozenset[str]
    ) -> dict[str, str]:
        """Basenames for the other end of each relation, for items that HAVE A PAGE.

        A page, not merely a row. Keyed on "the item exists" the projection emits
        ``[[Beta-abcd1234]]`` while Beta is still in the backlog, which is a link to a file
        that is not there — and :meth:`lint_flags`' broken-link check then fires on the
        vault's own output, which is how a rail stops meaning anything. The relation is still
        in the front-matter either way (it is a fact about the item), and the link appears on
        the pass after the other end is projected.

        ``cohort`` is the set of ids this sub-batch is about to write, so two items projected
        together link each other in that same pass — otherwise whichever `items.id` sorted
        first would be missing the link until something else touched it.
        """
        names: dict[str, str] = {}
        for rel in relations:
            src = str(rel.get("source_item_id") or "")
            dst = str(rel.get("target_item_id") or "")
            other = dst if src == item_id else src
            if not other or other in names:
                continue
            if other not in cohort:
                ledger = self._store.vault_projection(other)
                if not ledger or int(ledger.get("owner_deleted") or 0):
                    continue
            row = self._store.get_item(other)
            if row:
                names[other] = page_basename(row)
        return names

    def _ensure_static(self) -> None:
        """The README and the tag hubs every item page links to.

        Called only when the pass actually wrote or removed something (see
        :meth:`sync_batch`), because a settled vault must cost nothing. Membership comes from
        ONE query — :meth:`~personalclaw.knowledge.store.KnowledgeStore.vault_tag_membership`
        — rather than a `get_item` per ledger row, which would make a navigation aid the most
        expensive thing in the pass. The README is created only when ABSENT: a rewritten guide
        page is the owner's, and re-asserting ours over it every pass would be the mirror-mode
        overwrite applied to a file nothing projects.
        """
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.debug("knowledge vault: cannot create %s", self._dir, exc_info=True)
            return
        target = self._dir / _README
        if not target.exists():
            try:
                atomic_write(target, readme(self._mode), fsync=False)
            except OSError:
                logger.debug("knowledge vault: README not written", exc_info=True)
        members: dict[str, set[str]] = {}
        for row in self._store.vault_tag_membership():
            tag = str(row.get("tag") or "").strip()
            if tag:
                members.setdefault(tag, set()).add(page_basename(row))
        expected: set[str] = set()
        for tag, bases in members.items():
            relpath, page = render_tag_hub(tag, sorted(bases))
            expected.add(relpath)
            hub = self._dir / relpath
            if self._read(hub) != page:
                try:
                    atomic_write(hub, page, fsync=False)
                except OSError:
                    logger.debug("knowledge vault: tag hub %s not written", relpath, exc_info=True)
        # Prune hubs whose membership went to ZERO. Writing the ones that have members and
        # never removing the ones that do not is the one-sided-inventory shape: a hub for a tag
        # whose last item was deleted (or whose page the owner removed) keeps linking a file
        # that is gone, and the broken-link check then fires on a page the projection owns.
        # Found exactly that way, driving the real host.
        hubs = self._dir / _TAGS_DIR
        if hubs.is_dir():
            for path in hubs.glob("*.md"):
                if path.relative_to(self._dir).as_posix() in expected:
                    continue
                try:
                    path.unlink()
                except OSError:
                    logger.debug("knowledge vault: stale hub %s not removed", path, exc_info=True)

    # ── deletions: the store → file direction ───────────────────────────────

    def _collect_deletions(self, bound: int) -> int:
        """Unlink the files of items that no longer exist. Returns files collected.

        "A removed item leaves no orphan file", and the ledger's missing foreign key is what
        makes the query possible at all.
        """
        collected = 0
        for row in self._store.orphan_vault_projections(limit=bound):
            relpath = str(row.get("relpath") or "")
            if relpath:
                try:
                    (self._dir / relpath).unlink()
                except OSError:
                    pass  # already gone — the row still has to go
            self._store.forget_vault_projection(str(row.get("item_id") or ""))
            collected += 1
        if collected:
            self._ensure_static()
        return collected

    # ── verification ────────────────────────────────────────────────────────

    def lint_flags(self) -> list[tuple[str, str, str]]:
        """Deterministic checks as ``(check, key, detail)`` triples — MGAV-6's flag shape.

        Measured against the LEDGER and what is on disk, never against the renderer's own
        intentions: a check that asked the renderer whether it had rendered correctly would be
        comparing it with itself and could never fail.
        """
        flags: list[tuple[str, str, str]] = []
        for row in self._store.vault_projection_flags():
            key = str(row.get("relpath") or row.get("item_id") or "")
            if int(row.get("owner_deleted") or 0):
                flags.append(
                    (
                        "knowledge_vault_page_deleted",
                        key,
                        "you deleted this page; the item is still in the library. Delete the "
                        "item in the app to remove it, or re-enable the page from Knowledge.",
                    )
                )
            conflict = str(row.get("conflict") or "")
            if conflict:
                flags.append(("knowledge_vault_conflict", key, f"{conflict} — nothing written"))
        if not self._dir.is_dir():
            return flags
        projected = {str(r.get("relpath") or "") for r in self._store.vault_projections()}
        basenames: set[str] = set()
        linked: list[tuple[str, str]] = []
        for path in sorted(self._dir.rglob("*.md")):
            rel = path.relative_to(self._dir).as_posix()
            if rel.startswith("."):
                continue
            basenames.add(Path(rel).stem)
            # The link check scans the pages whose links the PROJECTION derives from rows —
            # item pages and tag hubs. The README is excluded from it (it is prose ABOUT
            # wikilinks, and reading its example as a link makes the rail fire on the guide
            # page) while still being excluded from the orphan check for the obvious reason.
            if rel in projected or rel.startswith(f"{_TAGS_DIR}/"):
                try:
                    linked.append((rel, path.read_text(encoding="utf-8")))
                except OSError:
                    continue
                continue
            if rel == _README:
                continue
            flags.append(
                ("knowledge_vault_orphan_page", rel, "not produced by the projection — yours")
            )
        # Broken links, measured against what is ON DISK. MGAV-6's check, and the rail that
        # keeps `_relation_names`' page-not-row rule honest: without it, "we never link a page
        # we did not create" is a comment rather than a constraint.
        for rel, text in linked:
            _, body = split_page(text)
            for target in WIKILINK_RE.findall(body):
                target = target.strip()
                if target and target not in basenames:
                    flags.append(("knowledge_vault_broken_link", rel, f"[[{target}]] has no page"))
        return flags

    def status(self) -> dict:
        """Lightweight status for a UI or a probe — no rendering, no file reads."""
        flags = self._store.vault_projection_flags()
        return {
            "mode": self._mode,
            "path": str(self._dir),
            "pending": self._store.count_items_needing_vault_projection(),
            "projected": len(self._store.vault_projections()),
            "conflicts": sum(1 for r in flags if str(r.get("conflict") or "")),
            "owner_deleted": sum(1 for r in flags if int(r.get("owner_deleted") or 0)),
        }

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""


# ── the maintenance pass (KL-14's host, no new cadence) ─────────────────────


def projection_pass(*, batch_size: int = 0) -> int:
    """One bounded sub-batch of the markdown projection. Returns units of work done.

    Registered ``batched=True`` on KL-14's host: the return value is PROGRESS, and 0 means
    "the vault agrees with the library", which is what stops the sub-batch loop. Every
    refusal records a durable ledger state precisely so it contributes 0 next time — a pass
    that kept re-reporting an unresolved conflict would busy-loop ``max_batches`` times per
    tick on a page only the owner can resolve.

    Mode ``off`` returns 0 and touches nothing. That is the gate, and it is the reason the
    projection cannot be the thing that surprises somebody by writing their home.
    """
    from personalclaw.knowledge import get_knowledge_store

    vault = vault_for(get_knowledge_store())
    if vault is None:
        return 0
    return int(vault.sync_batch(max_items=batch_size or DEFAULT_BATCH).get("units") or 0)
