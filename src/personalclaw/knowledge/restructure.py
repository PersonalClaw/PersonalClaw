"""KL-19 -- the structural editing verbs, so the knowledge base stops being read-mostly.

A reader who notices that one note is really three, or that two are really one, could
previously do nothing about it from the surface where they noticed. The library offered
create, edit-body and delete; every STRUCTURAL change -- split, merge, extract, retitle,
move, re-kind -- was either impossible or a hand-rolled copy-paste that silently dropped
everything the store knew about the item.

**Why these are store verbs and not UI sugar.** Each one has to preserve three things a
copy-paste cannot:

1. **Provenance** -- the child of a split inherits its parent's `url`, `provider` and
   `kind`, and is LINKED to it by a real `part_of` edge, so "where did this come from" still
   has an answer. It deliberately does NOT inherit `source_id`/`guid`: those are a watched
   source's exactly-once persist key under a partial UNIQUE index, and copying them would
   either collide or make the child look like a second sighting of the parent's feed entry.
2. **Chunk lineage** -- the chunk layer and both vector arms are derived from body text. The
   moment a verb rewrites a body they are wrong, so every verb invalidates them for the items
   it touched and KL-14's maintenance host rebuilds them (`maintenance_passes.derived_refresh`).
   A split whose halves keep the parent's vectors is silently wrong: it searches, it returns
   results, and the results are about text that is no longer there.
3. **Inbound relations** -- citations naming the item as a source, typed item relations on
   either leg, collection memberships, reading highlights, and `[[Title]]` wikilinks in other
   items' prose. `store.inbound_references` enumerates them; each verb decides which of them
   its particular change would BREAK.

**Two-phase by construction.** `plan()` touches nothing and returns a token; `apply()` refuses
any token that is not the one `plan()` would issue right now. The token is a digest of the
verb, its parameters, the affected items' current state AND the break set the user was shown --
copying `api_durability_history_operate`, which binds a confirm to both the state it previewed
(`expected_head`) and the selection it described (`expected_paths`). Nothing is minted and
nothing expires: a preview is valid exactly as long as what it describes has not moved. This is
what makes "states what it would break BEFORE applying" true by construction rather than by
the frontend remembering to ask first.

**Idempotence under a doubled submit** falls out of the same digest. The token is the undo
journal's primary key, so a second confirm carrying the same token finds the applied record and
REPLAYS its result instead of restructuring again. That ordering is load-bearing: the journal is
consulted BEFORE the plan is re-derived, because after a successful split the parent's body no
longer contains the offsets the plan validated and re-deriving first would raise a confusing
refusal for what is really a duplicate request.

**Undo** restores through `store.restore_items_snapshot`, which puts the item rows, their tags,
memberships, mentions, highlights, relations and citations back inside one transaction -- and
then invalidates the derived layer again, because the reverse direction has exactly the same
staleness problem as the forward one.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: The verbs, in the order the reading surface offers them.
VERBS: tuple[str, ...] = ("split", "extract", "merge", "retitle", "move", "change_kind")

#: How a split-off or extracted child is linked back to the item it came from. `part_of` is
#: the existing `semantics.RELATION_TYPES` member that says exactly this; a new relation type
#: would need a rendering in every graph reader before it meant anything to a user.
LINEAGE_RELATION = "part_of"


class RestructureError(Exception):
    """A verb refused. `code` is the stable wire code the HTTP layer returns."""

    def __init__(self, code: str, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


class PreviewStale(RestructureError):
    """The confirm cites a preview that no longer describes the store."""

    def __init__(self, message: str, *, plan: "Plan") -> None:
        super().__init__("preview_stale", message, detail={"plan": plan.to_dict()})
        self.plan = plan


@dataclass(frozen=True)
class Break:
    """One inbound reference a verb would break, and whether it can be repaired.

    `relinkable` is the whole point of reporting these: a break the store can repair becomes
    an offer, and one it cannot becomes a warning the user weighs. Conflating the two would
    make the preview a wall of text with no decision in it.
    """

    kind: str
    message: str
    relinkable: bool
    refs: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "message": self.message,
            "relinkable": self.relinkable,
            "refs": list(self.refs),
        }


@dataclass(frozen=True)
class Plan:
    """What a verb WOULD do, plus the token a confirm must echo."""

    verb: str
    item_id: str
    summary: str
    token: str
    affected: tuple[str, ...]
    breaks: tuple[Break, ...]
    detail: dict

    @property
    def relinkable(self) -> bool:
        return any(b.relinkable for b in self.breaks)

    def to_dict(self) -> dict:
        return {
            "verb": self.verb,
            "item_id": self.item_id,
            "summary": self.summary,
            "token": self.token,
            "affected": list(self.affected),
            "breaks": [b.to_dict() for b in self.breaks],
            "relink_offered": self.relinkable,
            "detail": self.detail,
        }


# ── Section boundaries (the split/extract vocabulary) ────────────────────────────


def sections(content: str) -> list[dict]:
    """The section boundaries a split may cut on, as plain dicts for the wire.

    Delegates to `chunking.section_boundaries` rather than re-deriving headings, so the
    boundaries offered to a reader are the same ones the chunker will re-section the halves
    along. `chars` is how long the section is, which is what lets the UI say "142 words move"
    instead of showing a bare offset.
    """
    from personalclaw.knowledge import chunking

    bounds = chunking.section_boundaries(content or "")
    out: list[dict] = []
    for index, bound in enumerate(bounds):
        end = bounds[index + 1].offset if index + 1 < len(bounds) else len(content or "")
        out.append(
            {
                "offset": bound.offset,
                "line": bound.line,
                "title": bound.title,
                "level": bound.level,
                "chars": end - bound.offset,
            }
        )
    return out


# ── Planning ─────────────────────────────────────────────────────────────────────


def _row(store: Any, item_id: str) -> dict:
    item = store.get_item(item_id)
    if not item:
        raise RestructureError("item_not_found", f"no knowledge item {item_id!r}")
    return dict(item)


def _digest(verb: str, item_id: str, params: dict, states: list[dict], breaks: list[Break]) -> str:
    """The preview token: a digest of everything the preview asserted.

    Every input matters and each for its own reason. The PARAMS are in it because a preview of
    "split at offset 900" must not be confirmable as "split at offset 40" -- the durability
    precedent learned that from `expected_paths`, where a matching HEAD would otherwise accept
    a confirm for a path set the user never saw. The affected items' `updated_at` and a hash of
    their bodies are in it because a body edited between the two phases invalidates the offsets
    and the break analysis alike. The BREAK SET is in it because the user's decision was made
    against that list: if a third item starts citing this one between preview and confirm, the
    honest answer is to show the new preview, not to apply the old verdict.
    """
    payload = {
        "verb": verb,
        "item_id": item_id,
        "params": params,
        "states": states,
        "breaks": [b.to_dict() for b in breaks],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _states(store: Any, item_ids: list[str]) -> list[dict]:
    """A stable fingerprint per affected item: id, updated_at and a body hash."""
    out: list[dict] = []
    for iid in sorted(set(item_ids)):
        row = store.db.execute(
            "SELECT id, title, updated_at, content, kind FROM items WHERE id = ?", (iid,)
        ).fetchone()
        if row is None:
            continue
        body = str(row["content"] or "")
        out.append(
            {
                "id": row["id"],
                "updated_at": str(row["updated_at"] or ""),
                "title": str(row["title"] or ""),
                "kind": str(row["kind"] or ""),
                "content_sha": hashlib.sha256(body.encode("utf-8")).hexdigest()[:16],
            }
        )
    return out


def _wikilink_break(store: Any, title: str, *, becoming: str) -> Break | None:
    """The `[[Title]]` bodies that would stop resolving, if there are any."""
    refs = store.items_linking_to_title(title)
    if not refs:
        return None
    total = sum(int(r.get("links") or 0) for r in refs)
    plural = "" if total == 1 else "s"
    where = "item" if len(refs) == 1 else "items"
    return Break(
        kind="wikilink",
        message=(
            f"{total} [[{title}]] link{plural} in {len(refs)} other {where} would stop "
            f"resolving; relinking repoints them at “{becoming}”"
        ),
        relinkable=True,
        refs=tuple(str(r["id"]) for r in refs),
    )


def _chunk_citation_break(store: Any, item_id: str) -> Break | None:
    """Citations that name a CHUNK of this item, whose numbering a rewrite invalidates."""
    rows = store.db.execute(
        "SELECT item_id, marker FROM item_citations "
        "WHERE source_item_id = ? AND chunk_index >= 0 ORDER BY item_id, marker",
        (item_id,),
    ).fetchall()
    if not rows:
        return None
    plural = "" if len(rows) == 1 else "s"
    return Break(
        kind="citation_chunk",
        message=(
            f"{len(rows)} citation{plural} name a specific passage of this item by chunk "
            "number; rewriting its body renumbers the chunks, so relinking widens them to "
            "cite the item as a whole rather than the wrong passage"
        ),
        relinkable=True,
        refs=tuple(str(r["item_id"]) for r in rows),
    )


def _annotation_spans(store: Any, item_id: str, parts: list[str]) -> tuple[dict, list[str]]:
    """Which part each highlight lands in, and the quotes that land in none.

    A highlight anchors by TEXT plus occurrence, not by offset, so a body rewrite does not
    corrupt it -- it simply stops matching. Every quote that survives intact inside exactly one
    part can therefore FOLLOW the text, which is the difference between a split that preserves
    a reader's marks and one that silently strands them.
    """
    placement: dict[str, int] = {}
    stranded: list[str] = []
    for row in store.list_annotations(item_id):
        quote = str(row.get("quote") or "")
        if not quote:
            continue
        hits = [index for index, text in enumerate(parts) if quote in text]
        if len(hits) == 1:
            placement[str(row["id"])] = hits[0]
        elif not hits:
            stranded.append(str(row["id"]))
        else:
            # Present in more than one part: the anchor is ambiguous after the cut, so it stays
            # on the original rather than being moved to an arbitrary one of them.
            placement[str(row["id"])] = 0
    return placement, stranded


def _stranded_break(stranded: list[str]) -> Break | None:
    if not stranded:
        return None
    plural = "" if len(stranded) == 1 else "s"
    return Break(
        kind="annotation",
        message=(
            f"{len(stranded)} highlight{plural} sit across the cut, so no single piece "
            "contains the whole passage; they stay listed on the original but stop marking it"
        ),
        relinkable=False,
        refs=tuple(stranded),
    )


def _plan_pieces(content: str, offsets: list[int]) -> list[str]:
    cuts = [0, *offsets, len(content)]
    return [content[cuts[i] : cuts[i + 1]] for i in range(len(cuts) - 1)]


def _plan_split(store: Any, row: dict, params: dict) -> tuple[str, list[str], list[Break], dict]:
    content = str(row.get("content") or "")
    available = {int(s["offset"]): s for s in sections(content)}
    raw = params.get("offsets")
    if not isinstance(raw, list) or not raw:
        raise RestructureError(
            "split_needs_boundary", "supply offsets: a list of section-boundary offsets"
        )
    try:
        offsets = sorted({int(value) for value in raw})
    except (TypeError, ValueError) as exc:
        raise RestructureError("split_needs_boundary", "offsets must be integers") from exc
    for offset in offsets:
        if offset not in available:
            raise RestructureError(
                "not_a_section_boundary",
                f"offset {offset} is not a section boundary of this item",
                detail={"sections": list(available.values())},
            )
        if offset <= 0:
            # The document opens with this heading, so cutting here leaves the original with
            # nothing. Refused rather than silently shifted: the user picked a boundary, and
            # quietly cutting somewhere else is not what they asked for.
            raise RestructureError(
                "boundary_at_start",
                "that boundary is the start of the document, so splitting there would leave "
                "the original empty — pick a later section",
            )
    pieces = _plan_pieces(content, offsets)
    placement, stranded = _annotation_spans(store, str(row["id"]), pieces)
    breaks = [b for b in (_stranded_break(stranded), _chunk_citation_break(store, row["id"])) if b]
    titles = [str(available[o]["title"]) or "Untitled section" for o in offsets]
    moving = sum(len(p.split()) for p in pieces[1:])
    summary = (
        f"Split into {len(pieces)} items: “{row.get('title') or 'Untitled'}” keeps "
        f"{len(pieces[0].split())} words, and {moving} words move to "
        + ", ".join(f"“{t}”" for t in titles)
    )
    detail = {
        "pieces": [
            {"title": row.get("title") if i == 0 else titles[i - 1], "words": len(p.split())}
            for i, p in enumerate(pieces)
        ],
        "annotations_following": len([v for v in placement.values() if v > 0]),
        "sections": list(available.values()),
    }
    return summary, [str(row["id"])], breaks, {**detail, "offsets": offsets}


def _plan_extract(store: Any, row: dict, params: dict) -> tuple[str, list[str], list[Break], dict]:
    content = str(row.get("content") or "")
    raw_start = params.get("start")
    raw_end = params.get("end")
    if raw_start is None or raw_end is None:
        raise RestructureError("extract_needs_span", "supply integer start and end")
    try:
        start = int(raw_start)
        end = int(raw_end)
    except (TypeError, ValueError) as exc:
        raise RestructureError("extract_needs_span", "supply integer start and end") from exc
    if not (0 <= start < end <= len(content)):
        raise RestructureError(
            "extract_needs_span",
            f"start/end must satisfy 0 <= start < end <= {len(content)} (the body length)",
        )
    passage = content[start:end]
    if not passage.strip():
        raise RestructureError("extract_needs_span", "that span is only whitespace")
    title = " ".join(str(params.get("title") or "").split())
    if not title:
        raise RestructureError("title_required", "the extracted item needs a title")
    keep = bool(params.get("keep_in_source"))
    remainder = content if keep else content[:start] + content[end:]
    if not keep and not remainder.strip():
        raise RestructureError(
            "extract_empties_source",
            "extracting that span would leave the original empty — split the item instead, "
            "or extract a copy with keep_in_source",
        )
    breaks: list[Break] = []
    if not keep:
        _, stranded = _annotation_spans(store, str(row["id"]), [remainder, passage])
        breaks = [
            b for b in (_stranded_break(stranded), _chunk_citation_break(store, row["id"])) if b
        ]
    verb_word = "Copy" if keep else "Move"
    summary = (
        f"{verb_word} {len(passage.split())} words into a new item “{title}”, linked to "
        f"“{row.get('title') or 'Untitled'}”"
        + ("" if keep else f", leaving {len(remainder.split())} words behind")
    )
    detail = {
        "title": title,
        "words": len(passage.split()),
        "keep_in_source": keep,
        "excerpt": passage[:280],
    }
    return summary, [str(row["id"])], breaks, {**detail, "start": start, "end": end}


def _plan_merge(store: Any, row: dict, params: dict) -> tuple[str, list[str], list[Break], dict]:
    merge_id = str(params.get("merge_id") or "").strip()
    if not merge_id:
        raise RestructureError("merge_id_required", "supply merge_id: the item to fold in")
    if merge_id == str(row["id"]):
        raise RestructureError(
            "merge_into_self",
            "an item cannot be merged into itself — that would delete the survivor",
        )
    loser = _row(store, merge_id)
    inbound = store.inbound_references(merge_id)
    breaks: list[Break] = []
    cites = [c for c in inbound["citations"]]
    if cites:
        plural = "" if len(cites) == 1 else "s"
        breaks.append(
            Break(
                kind="citation",
                message=(
                    f"{len(cites)} citation{plural} name the folded-in copy as their source; "
                    "merging deletes it, so relinking repoints them at the survivor whose "
                    "body now holds that text"
                ),
                relinkable=True,
                refs=tuple(str(c["item_id"]) for c in cites),
            )
        )
    link_break = _wikilink_break(store, str(loser.get("title") or ""), becoming=str(row["title"]))
    if link_break is not None:
        breaks.append(link_break)
    summary = (
        f"Fold “{loser.get('title') or 'Untitled'}” into “{row.get('title') or 'Untitled'}”. "
        f"The survivor inherits its tags, shelves, entity mentions, "
        f"{inbound['annotations']} highlight(s) and {len(inbound['relations'])} relation(s); "
        "the folded-in copy is deleted"
    )
    detail = {
        "merge_id": merge_id,
        "merge_title": loser.get("title") or "",
        "inherits": {
            "annotations": inbound["annotations"],
            "relations": len(inbound["relations"]),
            "collections": len(inbound["collections"]),
        },
    }
    return summary, [str(row["id"]), merge_id], breaks, detail


def _plan_retitle(store: Any, row: dict, params: dict) -> tuple[str, list[str], list[Break], dict]:
    title = " ".join(str(params.get("title") or "").split())
    if not title:
        raise RestructureError("title_required", "supply a non-empty title")
    old = str(row.get("title") or "")
    if title == old:
        raise RestructureError("title_unchanged", "that is already the title")
    breaks: list[Break] = []
    link_break = _wikilink_break(store, old, becoming=title)
    affected = [str(row["id"])]
    if link_break is not None:
        breaks.append(link_break)
        # The referring items' BODIES are rewritten by a relink, so they are affected items and
        # must be in the snapshot. An undo that restored only the retitled item would leave
        # every referrer pointing at a title that no longer exists — the mirror of the break
        # this verb exists to repair.
        affected.extend(link_break.refs)
    summary = f"Rename “{old}” to “{title}”"
    return summary, affected, breaks, {"title": title, "previous_title": old}


def _plan_move(store: Any, row: dict, params: dict) -> tuple[str, list[str], list[Break], dict]:
    raw_collections = params.get("collections")
    raw_tags = params.get("tags")
    if raw_collections is None and raw_tags is None:
        raise RestructureError("nothing_to_move", "supply collections and/or tags")
    collections: list[str] | None = None
    if raw_collections is not None:
        if not isinstance(raw_collections, list):
            raise RestructureError("bad_collections", "collections must be a list of ids")
        collections = [str(c) for c in raw_collections if str(c or "").strip()]
        for cid in collections:
            found = store.db.execute(
                "SELECT id, name, kind FROM collections WHERE id = ?", (cid,)
            ).fetchone()
            if found is None:
                raise RestructureError("unknown_collection", f"no collection {cid!r}")
            if str(found["kind"]) != "manual":
                # A smart collection is a saved QUERY re-run on read; it has no membership
                # rows to write. Refusing names the reason rather than accepting a write that
                # would appear to succeed and change nothing the user can see.
                raise RestructureError(
                    "smart_collection",
                    f"“{found['name']}” is a smart shelf — its contents come from its query, "
                    "so an item cannot be moved into it",
                )
    tags: list[str] | None = None
    if raw_tags is not None:
        if not isinstance(raw_tags, list):
            raise RestructureError("bad_tags", "tags must be a list of names")
        tags = [" ".join(str(t).split()) for t in raw_tags if str(t or "").strip()]
    current_collections = [str(c["id"]) for c in store.collections_for_item(str(row["id"]))]
    parts: list[str] = []
    if collections is not None:
        adding = sorted(set(collections) - set(current_collections))
        removing = sorted(set(current_collections) - set(collections))
        parts.append(f"{len(adding)} shelf addition(s) and {len(removing)} removal(s)")
    if tags is not None:
        parts.append(f"tags become {', '.join(tags) if tags else 'none'}")
    summary = f"Reshelve “{row.get('title') or 'Untitled'}”: " + "; ".join(parts)
    return summary, [str(row["id"])], [], {"collections": collections, "tags": tags}


def _plan_change_kind(
    store: Any, row: dict, params: dict
) -> tuple[str, list[str], list[Break], dict]:
    from personalclaw.knowledge import semantics

    kind = str(params.get("kind") or "").strip().lower()
    if not kind:
        raise RestructureError("kind_required", "supply kind")
    if kind not in semantics.KINDS:
        raise RestructureError(
            "unknown_kind",
            f"unknown kind {kind!r} — one of: {', '.join(semantics.KINDS)}",
        )
    old = str(row.get("kind") or "")
    if kind == old:
        raise RestructureError("kind_unchanged", "that is already the item's kind")
    breaks: list[Break] = []
    if kind in semantics.SYNTHESIZED_KINDS and not store.item_citations(str(row["id"])):
        # Not relinkable: no rewrite the store can perform produces attribution that does not
        # exist. Reported so the user chooses knowingly rather than discovering it the next
        # time a persist gate refuses the item.
        breaks.append(
            Break(
                kind="kind_contract",
                message=(
                    f"“{kind}” is a synthesized kind, which is expected to carry citations, "
                    "and this item has none — it will read as unsourced"
                ),
                relinkable=False,
            )
        )
    new_key = semantics.logical_key(kind, str(row.get("title") or ""))
    summary = (
        f"Change kind from “{old or 'unset'}” to “{kind}”, which re-derives the item's "
        f"logical identity as {new_key or 'nothing'}"
    )
    return summary, [str(row["id"])], breaks, {"kind": kind, "previous_kind": old}


_PLANNERS = {
    "split": _plan_split,
    "extract": _plan_extract,
    "merge": _plan_merge,
    "retitle": _plan_retitle,
    "move": _plan_move,
    "change_kind": _plan_change_kind,
}


def plan(store: Any, verb: str, item_id: str, params: dict | None = None) -> Plan:
    """What *verb* would do to *item_id*. Touches nothing.

    Raises :class:`RestructureError` for a verb that cannot proceed at all (a bad span, an
    unknown kind, a smart shelf). A change that CAN proceed but would break an inbound
    reference is not an error -- it comes back as a `Break`, because the decision belongs to
    the person reading the preview.
    """
    if verb not in _PLANNERS:
        raise RestructureError("unknown_verb", f"verb must be one of: {', '.join(VERBS)}")
    row = _row(store, item_id)
    normalized = dict(params or {})
    summary, affected, breaks, detail = _PLANNERS[verb](store, row, normalized)
    token = _digest(verb, item_id, detail, _states(store, affected), breaks)
    return Plan(
        verb=verb,
        item_id=item_id,
        summary=summary,
        token=token,
        affected=tuple(affected),
        breaks=tuple(breaks),
        detail=detail,
    )


# ── Applying ─────────────────────────────────────────────────────────────────────


def refresh_derived(store: Any, item_ids: list[str], *, reason: str) -> list[str]:
    """Invalidate the derived layer for *item_ids* and hand the rebuild to KL-14's host.

    Invalidate rather than recompute, and the split between the two is the whole design. What
    happens HERE is cheap, synchronous and must be atomic with the restructure: the chunk rows
    and their ANN entries go, the whole-item vector is NULLed, the similarity claims this item
    made are released, and both sweep markers are cleared so the entity linker and the edge
    pass will look again. What happens LATER, on the host, is the expensive part: re-chunking,
    re-embedding and re-deriving similarity edges against the whole library.

    Doing the expensive half inline is the mistake `maintenance_passes` was written to prevent
    -- a bulk restructure would then do it once per item, each pass superseded by the next,
    while holding the write lock. Doing NEITHER half is the mistake this atom names: halves
    that keep the parent's vectors search as if the text never moved.
    """
    from personalclaw.knowledge import maintenance

    touched: list[str] = []
    for item_id in item_ids:
        if store.db.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone() is None:
            continue
        # Drops the chunk rows AND their ANN index entries, and clears the similarity sweep
        # marker so the edge pass re-examines the item.
        store.clear_chunks(item_id)
        store.db.execute("UPDATE items SET embedding = NULL WHERE id = ?", (item_id,))
        store.db.execute("DELETE FROM mention_sweeps WHERE item_id = ?", (item_id,))
        # An empty `keep` set releases every edge this item's own pass claimed, leaving the
        # ones its neighbours claimed intact. A plain delete on either leg would destroy a
        # neighbour's finding — see the `item_similarity_edges` writer-claim comment.
        store.release_similarity_claims(item_id, set())
        touched.append(item_id)
    store.db.commit()
    maintenance.mark_dirty(reason=f"restructure {reason}")
    return touched


def _inherit(store: Any, parent: dict, *, title: str, content: str) -> str:
    """Create a child item that carries its parent's provenance and curation."""
    child_id = store.create_typed_item(
        item_type=str(parent.get("item_type") or parent.get("type") or "note"),
        title=title,
        content=content,
        tags=list(parent.get("tags") or []),
        url=str(parent.get("url") or ""),
        provider=str(parent.get("provider") or "native"),
    )
    if not child_id:
        raise RestructureError("create_failed", "the new item could not be created")
    for collection in store.collections_for_item(str(parent["id"])):
        if str(collection.get("kind")) == "manual":
            store.add_to_collection(str(collection["id"]), child_id)
    if parent.get("kind"):
        store.set_item_identity(child_id, kind=str(parent["kind"]))
    store.add_item_relation(child_id, str(parent["id"]), LINEAGE_RELATION, provenance="extracted")
    return child_id


def _widen_chunk_citations(store: Any, item_id: str) -> int:
    """Repoint chunk-specific citations of *item_id* at the item as a whole."""
    cur = store.db.execute(
        "UPDATE item_citations SET chunk_index = -1 "
        "WHERE source_item_id = ? AND chunk_index >= 0",
        (item_id,),
    )
    store.db.commit()
    return cur.rowcount or 0


def _apply_split(store: Any, current: Plan, *, relink: bool) -> dict:
    row = _row(store, current.item_id)
    content = str(row.get("content") or "")
    pieces = _plan_pieces(content, list(current.detail["offsets"]))
    placement, _ = _annotation_spans(store, current.item_id, pieces)
    titles = [str(p["title"] or "Untitled section") for p in current.detail["pieces"][1:]]
    created: list[str] = []
    for index, piece in enumerate(pieces[1:], start=1):
        created.append(_inherit(store, row, title=titles[index - 1], content=piece))
    store.update_item(current.item_id, content=pieces[0])
    # Highlights follow their text. Anchoring is by quote + occurrence, so a mark whose
    # passage moved into a child resolves against the child and nowhere else.
    moved = 0
    for annotation_id, part in placement.items():
        if part > 0:
            store.db.execute(
                "UPDATE annotations SET item_id = ? WHERE id = ?",
                (created[part - 1], annotation_id),
            )
            moved += 1
    store.db.commit()
    widened = _widen_chunk_citations(store, current.item_id) if relink else 0
    return {
        "created": created,
        "kept": current.item_id,
        "annotations_moved": moved,
        "citations_widened": widened,
    }


def _apply_extract(store: Any, current: Plan, *, relink: bool) -> dict:
    row = _row(store, current.item_id)
    content = str(row.get("content") or "")
    start = int(current.detail["start"])
    end = int(current.detail["end"])
    passage = content[start:end]
    keep = bool(current.detail["keep_in_source"])
    child_id = _inherit(store, row, title=str(current.detail["title"]), content=passage)
    moved = 0
    if not keep:
        remainder = content[:start] + content[end:]
        placement, _ = _annotation_spans(store, current.item_id, [remainder, passage])
        store.update_item(current.item_id, content=remainder)
        for annotation_id, part in placement.items():
            if part == 1:
                store.db.execute(
                    "UPDATE annotations SET item_id = ? WHERE id = ?", (child_id, annotation_id)
                )
                moved += 1
        store.db.commit()
    widened = _widen_chunk_citations(store, current.item_id) if (relink and not keep) else 0
    return {
        "created": [child_id],
        "kept": current.item_id,
        "annotations_moved": moved,
        "citations_widened": widened,
    }


def _apply_merge(store: Any, current: Plan, *, relink: bool) -> dict:
    merge_id = str(current.detail["merge_id"])
    loser_title = str(current.detail.get("merge_title") or "")
    keeper_title = str(_row(store, current.item_id).get("title") or "")
    relinked_links = {"items": 0, "links": 0, "item_ids": []}
    if relink and loser_title and loser_title != keeper_title:
        # Before the merge, while the losing title is still the one other bodies name.
        relinked_links = store.rewrite_wikilinks(loser_title, keeper_title)
    moved = store.merge_items(current.item_id, merge_id, relink_citations=relink)
    return {
        "created": [],
        "kept": current.item_id,
        "merged": merge_id,
        "moved": moved,
        "wikilinks_relinked": relinked_links,
    }


def _apply_retitle(store: Any, current: Plan, *, relink: bool) -> dict:
    old = str(current.detail["previous_title"])
    new = str(current.detail["title"])
    store.update_item(current.item_id, title=new)
    # The derived logical identity is recomputed from the NEW title. `update_item` cannot
    # write `logical_key`, so without this the store would keep an identity keyed on a title
    # that no longer exists and the next persist of the same record would be admitted as a
    # second item.
    identity = store.set_item_identity(current.item_id)
    relinked = store.rewrite_wikilinks(old, new) if relink else {"items": 0, "links": 0}
    return {
        "created": [],
        "kept": current.item_id,
        "title": new,
        "logical_key": identity["logical_key"],
        "wikilinks_relinked": relinked,
    }


def _apply_move(store: Any, current: Plan, *, relink: bool) -> dict:
    collections = current.detail.get("collections")
    tags = current.detail.get("tags")
    added = removed = 0
    if collections is not None:
        want = set(collections)
        have = {str(c["id"]) for c in store.collections_for_item(current.item_id)}
        for cid in sorted(want - have):
            if store.add_to_collection(cid, current.item_id):
                added += 1
        for cid in sorted(have - want):
            if store.remove_from_collection(cid, current.item_id):
                removed += 1
    if tags is not None:
        store.update_item(current.item_id, tags=tags, tag_source="user")
    return {
        "created": [],
        "kept": current.item_id,
        "collections_added": added,
        "collections_removed": removed,
        "tags": tags,
    }


def _apply_change_kind(store: Any, current: Plan, *, relink: bool) -> dict:
    identity = store.set_item_identity(current.item_id, kind=str(current.detail["kind"]))
    return {"created": [], "kept": current.item_id, **identity}


_APPLIERS = {
    "split": _apply_split,
    "extract": _apply_extract,
    "merge": _apply_merge,
    "retitle": _apply_retitle,
    "move": _apply_move,
    "change_kind": _apply_change_kind,
}


def apply(
    store: Any,
    verb: str,
    item_id: str,
    params: dict | None = None,
    *,
    token: str,
    relink: bool = True,
) -> dict:
    """Apply *verb*, but only against the exact preview *token* names.

    The journal lookup comes FIRST, before the plan is re-derived, and that order is what makes
    a doubled submit idempotent rather than confusing. After a successful split the parent's
    body no longer contains the offsets the plan validated, so re-deriving first would raise
    `not_a_section_boundary` for what is really a duplicate of a request that already
    succeeded. Consulting the journal first turns the second submit into a replay of the first
    one's result -- one effect, one response, whatever the client's retry logic does.
    """
    if verb not in _APPLIERS:
        raise RestructureError("unknown_verb", f"verb must be one of: {', '.join(VERBS)}")
    if not token:
        raise RestructureError(
            "token_required",
            "confirming a restructure requires the token from its preview",
        )
    prior = store.load_undo(token)
    if prior is not None and str(prior.get("verb")) == verb:
        recorded = dict(prior.get("snapshot") or {}).get("result") or {}
        return {**recorded, "undo_token": token, "idempotent": True}

    current = plan(store, verb, item_id, params)
    if token != current.token:
        raise PreviewStale(
            "this item changed since that preview was taken; review the new preview "
            "before confirming",
            plan=current,
        )

    snapshot = store.snapshot_items(list(current.affected))
    result = _APPLIERS[verb](store, current, relink=relink)
    created = [str(c) for c in result.get("created") or []]
    # The snapshot was taken before the children existed, so widen its id list to cover them.
    # `existing` is untouched, which is exactly how `restore_items_snapshot` tells "put this
    # row back" from "this row is something the verb minted and must go".
    snapshot["ids"] = list(snapshot["ids"]) + created
    store.save_undo(
        token,
        verb=verb,
        item_id=item_id,
        summary=current.summary,
        snapshot={"state": snapshot, "result": result},
    )
    refresh_derived(store, [*current.affected, *created], reason=verb)
    logger.info("knowledge restructure %s on %s: %s", verb, item_id, result)
    return {**result, "undo_token": token, "summary": current.summary, "idempotent": False}


def undo(store: Any, token: str) -> dict:
    """Reverse one applied restructure, relations included.

    The derived layer is invalidated again afterwards for the same reason the forward verb
    invalidates it: the restored bodies are different text from what the vectors were computed
    against. A restore that put the rows back and left the post-restructure vectors in place
    would leave the library searching for content it no longer has.
    """
    record = store.load_undo(token)
    if record is None:
        raise RestructureError(
            "unknown_undo_token",
            "that restructure is no longer undoable — it was already undone, or the journal "
            "has since rolled past it",
        )
    snapshot = dict(record.get("snapshot") or {}).get("state") or {}
    restored = store.restore_items_snapshot(snapshot)
    store.delete_undo(token)
    refresh_derived(store, list(snapshot.get("existing") or []), reason=f"undo {record['verb']}")
    logger.info("undid knowledge restructure %s (%s)", record["verb"], token)
    return {
        "ok": True,
        "verb": str(record["verb"]),
        "item_id": str(record["item_id"]),
        "summary": str(record.get("summary") or ""),
        "restored": restored,
    }
