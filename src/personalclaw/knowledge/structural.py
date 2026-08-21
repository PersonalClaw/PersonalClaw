"""Structural retrieval — answer a question about STRUCTURE by traversal (KL-18).

``HybridRetriever`` answers "what is this query ABOUT". It cannot answer "what links to
this item": an inbound link is a fact the store already holds in a row, and an embedding
neighbourhood reproduces it only by coincidence — a document that cites a target while
sharing none of its vocabulary is invisible to every similarity arm, and a document that
merely rhymes with the target ranks first. So the two questions get two mechanisms:

* the similarity arm ranks by *proximity in meaning* (``retrieval.HybridRetriever``);
* this module answers by *traversal over stored relations* — ``item_relations``,
  ``item_citations``, the ``tags`` adjacency hierarchy, and ``items.updated_at``.

Three properties make the difference legible rather than nominal:

1. **Every hit carries its own path.** A structural answer is not a list of ids, it is a
   list of ``(item, path)`` pairs where the path is the exact chain of stored edges that
   reached it. A caller can render *why* an item was returned without a second query, and
   a wrong answer is falsifiable by inspection instead of a matter of taste.
2. **An empty result is a legible "no such relation", never a similarity fall-back.**
   ``StructuralAnswer.empty_reason`` names which structural fact was absent. A silent
   degrade to a semantic top-K is the specific defect this module exists to remove: it
   makes a structural answer indistinguishable from a guess.
3. **Structural and similarity retrieval COMPOSE, in one declared order.** Passing
   ``rank_query`` restricts FIRST (the traversal decides *which* items are eligible) and
   ranks SECOND (the semantic score decides their *order*) — :data:`RESTRICT_THEN_RANK`.
   The reverse order is not offered, because ranking first and filtering after silently
   drops eligible items that fell outside the semantic top-K, which makes the same query
   return different sets as the corpus grows. The order and the rank mode are both
   recorded on the answer, so a result is reproducible from what it reports.

Node references are namespaced (``item:``/``tag:``/``date:``) because a path legitimately
runs through more than one kind of node — a tag-subtree path descends tags and then steps
into an item — and an un-prefixed id could not say which.

**Known dependency, deliberately not fixed here:** ``item_relations`` carries no
``ON DELETE CASCADE`` on this branch, so a relation row can outlive the item it points at.
Traversal therefore treats an unresolvable neighbour as a dead end (dropped, never a hit)
rather than assuming the row set is clean. Once the cascade lands the drop becomes
unreachable; it is not load-bearing for correctness of a live store either way.
"""

import logging
import math
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field

from .embedder import bytes_to_floats
from .store import KnowledgeStore

logger = logging.getLogger(__name__)

# ── the declared verb vocabulary ────────────────────────────────────────────────
# Closed on purpose: each verb names a question the store can answer from rows it
# already holds. A verb whose relation the store does not record would be an invented
# edge type, which is worse than a stated gap.
LINKS_TO = "links_to"
DEPENDS_ON = "depends_on"
TAG_SUBTREE = "tag_subtree"
CHANGED_SINCE = "changed_since"
CONTRADICTIONS = "contradictions"

STRUCTURAL_VERBS: tuple[str, ...] = (
    LINKS_TO,
    DEPENDS_ON,
    TAG_SUBTREE,
    CHANGED_SINCE,
    CONTRADICTIONS,
)

#: The typed item-relation vocabulary this module traverses. Mirrors
#: ``semantics.RELATION_TYPES`` — imported by value rather than by reference so a new verb
#: there cannot silently widen a traversal that has no test for it.
TRAVERSABLE_RELATIONS: tuple[str, ...] = (
    "supersedes",
    "contradicts",
    "derived_from",
    "depends_on",
    "part_of",
)

#: ``depends_on``'s default edge set. Just the literal verb: "what does this depend on" is
#: a question about declared dependency, and folding ``part_of``/``derived_from`` in by
#: default would answer a broader question than the caller asked. A caller who wants the
#: wider closure passes ``relations=`` explicitly.
DEPENDENCY_RELATIONS: tuple[str, ...] = ("depends_on",)

# ── composition ─────────────────────────────────────────────────────────────────
#: Structure only: the traversal both selects and orders.
STRUCTURE_ONLY = "structure_only"
#: The one composed order. Restrict by traversal, then rank the survivors semantically.
RESTRICT_THEN_RANK = "restrict_then_rank"

#: Rank modes, recorded so a composed order is reproducible from the answer alone.
RANK_VECTOR = "vector"
RANK_LEXICAL = "lexical"

# ── empty reasons ───────────────────────────────────────────────────────────────
NO_SUCH_ITEM = "no_such_item"
NO_SUCH_TAG = "no_such_tag"
NO_SUCH_RELATION = "no_such_relation"
NO_CHANGE_SINCE = "no_change_since"
NO_CONTRADICTION = "no_contradiction"
BAD_REQUEST = "bad_request"

_EMPTY_MESSAGES = {
    NO_SUCH_ITEM: "no such item in the library",
    NO_SUCH_TAG: "no such tag in the taxonomy",
    NO_SUCH_RELATION: "no such relation — nothing is linked this way",
    NO_CHANGE_SINCE: "no item changed after that point",
    NO_CONTRADICTION: "no contradiction recorded",
    BAD_REQUEST: "the query is not answerable as asked",
}


def empty_message(reason: str) -> str:
    """A human sentence for an ``empty_reason``. Unknown reasons render as themselves so a
    new reason can never surface as an empty string."""
    return _EMPTY_MESSAGES.get(reason, reason)


# ── refs ────────────────────────────────────────────────────────────────────────


def item_ref(item_id: str) -> str:
    return f"item:{item_id}"


def tag_ref(name: str) -> str:
    return f"tag:{name}"


def date_ref(iso: str) -> str:
    return f"date:{iso}"


@dataclass(frozen=True)
class PathStep:
    """One stored edge, traversed. ``edge`` names the row that justified the step."""

    from_ref: str
    edge: str
    to_ref: str
    #: ``outbound`` when the step followed the row's source→target direction, ``inbound``
    #: when it followed it backwards, ``""`` for a step out of a non-item anchor.
    direction: str = ""
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "from": self.from_ref,
            "edge": self.edge,
            "to": self.to_ref,
            "direction": self.direction,
            "detail": dict(self.detail),
        }

    def describe(self) -> str:
        arrow = "<-" if self.direction == "inbound" else "->"
        return f"{self.from_ref} {arrow}[{self.edge}]{arrow} {self.to_ref}"


def describe_path(path: Iterable[PathStep]) -> str:
    """The path as one rendered sentence — the justification a caller shows the user."""
    steps = list(path)
    if not steps:
        return ""
    out = [steps[0].from_ref]
    for step in steps:
        arrow = "<--" if step.direction == "inbound" else "--"
        out.append(f"{arrow}{step.edge}-->")
        out.append(step.to_ref)
    return " ".join(out)


@dataclass(frozen=True)
class StructuralHit:
    """One item the traversal reached, with the chain that reached it."""

    item_id: str
    title: str
    depth: int
    path: tuple[PathStep, ...]
    #: Set only when a semantic rank composed on top; ``None`` under STRUCTURE_ONLY, so a
    #: reader can tell a structural position from a scored one.
    score: float | None = None

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "depth": self.depth,
            "path": [s.to_dict() for s in self.path],
            "why": describe_path(self.path),
            "score": self.score,
        }


@dataclass(frozen=True)
class StructuralAnswer:
    """A structural result. Empty is a *statement*, not a fall-back."""

    verb: str
    origin: str
    composition: str
    hits: tuple[StructuralHit, ...] = ()
    rank_mode: str = ""
    empty_reason: str = ""
    truncated: bool = False

    def __bool__(self) -> bool:
        return bool(self.hits)

    def to_dict(self) -> dict:
        return {
            "verb": self.verb,
            "origin": self.origin,
            "composition": self.composition,
            "rank_mode": self.rank_mode,
            "hits": [h.to_dict() for h in self.hits],
            "count": len(self.hits),
            "truncated": self.truncated,
            "empty_reason": self.empty_reason,
            "empty_message": empty_message(self.empty_reason) if self.empty_reason else "",
        }


_WORD_RE = re.compile(r"[a-z0-9]+")


def _terms(text: str) -> set[str]:
    return {t for t in _WORD_RE.findall((text or "").lower()) if len(t) > 1}


def lexical_scores(query: str, texts: dict[str, str]) -> dict[str, float]:
    """Query-term coverage per candidate, in ``[0, 1]``.

    The embedder-free rank arm. Deliberately *coverage of the query* rather than a
    tf-idf-ish weight: it is the property the composition test needs to be able to state
    exactly — a candidate sharing no vocabulary with the query scores 0.0, which is the
    measurable sense in which similarity "gets a structural question wrong".
    """
    q = _terms(query)
    if not q:
        return dict.fromkeys(texts, 0.0)
    return {cid: len(q & _terms(text)) / len(q) for cid, text in texts.items()}


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


class StructuralRetriever:
    """Traversal-backed retrieval over the relations the knowledge store already holds.

    Mirrors :class:`~personalclaw.knowledge.retrieval.HybridRetriever`'s construction
    (store + optional embedder) so a caller holding one can build the other without
    learning a second convention. The embedder is used *only* for the composed rank arm;
    no verb consults it to decide membership.
    """

    def __init__(self, store: KnowledgeStore, embedder: Callable[[str], list[float]] | None = None):
        self.store = store
        self.embedder = embedder

    # ── public surface ──────────────────────────────────────────────────────────

    def query(
        self,
        verb: str,
        *,
        origin: str = "",
        since: str = "",
        depth: int = 1,
        limit: int = 25,
        relations: Iterable[str] | None = None,
        include_archived: bool = False,
        rank_query: str = "",
    ) -> StructuralAnswer:
        """Answer one structural question.

        ``verb`` is one of :data:`STRUCTURAL_VERBS`. ``origin`` is an item id for
        ``links_to``/``depends_on``, a tag name for ``tag_subtree``, and optional (a
        scoping item id) for ``contradictions``. ``since`` is an ISO timestamp for
        ``changed_since``.

        ``rank_query`` composes a semantic rank ON TOP of the traversal — restrict first,
        rank second (:data:`RESTRICT_THEN_RANK`). It can reorder hits and can never add or
        remove one, so a composed answer is a permutation of the structural answer.
        """
        verb = (verb or "").strip()
        if verb not in STRUCTURAL_VERBS:
            return StructuralAnswer(
                verb=verb,
                origin=origin,
                composition=STRUCTURE_ONLY,
                empty_reason=BAD_REQUEST,
            )
        depth = max(1, min(int(depth or 1), 6))
        limit = max(1, min(int(limit or 25), 500))

        if verb == LINKS_TO:
            answer = self._links_to(origin, depth, limit, include_archived)
        elif verb == DEPENDS_ON:
            answer = self._depends_on(origin, depth, limit, include_archived, relations)
        elif verb == TAG_SUBTREE:
            answer = self._tag_subtree(origin, depth, limit, include_archived)
        elif verb == CHANGED_SINCE:
            answer = self._changed_since(since, limit, include_archived)
        else:
            answer = self._contradictions(origin, limit, include_archived)

        # Composition. An EMPTY structural result stays empty: there is no similarity
        # arm to fall back to, because a fall-back is what makes a structural answer
        # indistinguishable from a guess.
        if not answer.hits or not (rank_query or "").strip():
            return answer
        return self._rank_within(answer, rank_query)

    # ── verbs ───────────────────────────────────────────────────────────────────

    def _links_to(
        self, item_id: str, depth: int, limit: int, include_archived: bool
    ) -> StructuralAnswer:
        """What links TO this item — inbound typed relations and inbound citations.

        The inbound direction is the half a similarity arm cannot reach even in principle:
        the linking document's vocabulary is its own, not the target's.
        """
        origin = item_ref(item_id)
        if not self._item_row(item_id):
            return StructuralAnswer(LINKS_TO, origin, STRUCTURE_ONLY, empty_reason=NO_SUCH_ITEM)
        hits, truncated = self._traverse(
            item_id,
            self._expand_inbound,
            depth=depth,
            limit=limit,
            include_archived=include_archived,
        )
        return StructuralAnswer(
            LINKS_TO,
            origin,
            STRUCTURE_ONLY,
            hits=hits,
            empty_reason="" if hits else NO_SUCH_RELATION,
            truncated=truncated,
        )

    def _depends_on(
        self,
        item_id: str,
        depth: int,
        limit: int,
        include_archived: bool,
        relations: Iterable[str] | None,
    ) -> StructuralAnswer:
        """What this item depends on — the outbound dependency chain, transitively.

        Transitivity is the whole reason this is traversal and not the single-hop relations
        read the detail page already does: "what does this depend on" includes what its
        dependencies depend on, and no single row states that.
        """
        origin = item_ref(item_id)
        wanted = self._relation_filter(relations, DEPENDENCY_RELATIONS)
        if not wanted:
            return StructuralAnswer(DEPENDS_ON, origin, STRUCTURE_ONLY, empty_reason=BAD_REQUEST)
        if not self._item_row(item_id):
            return StructuralAnswer(DEPENDS_ON, origin, STRUCTURE_ONLY, empty_reason=NO_SUCH_ITEM)

        def expand(current: str) -> Iterator[tuple[str, PathStep]]:
            yield from self._expand_outbound(current, wanted)

        hits, truncated = self._traverse(
            item_id, expand, depth=depth, limit=limit, include_archived=include_archived
        )
        return StructuralAnswer(
            DEPENDS_ON,
            origin,
            STRUCTURE_ONLY,
            hits=hits,
            empty_reason="" if hits else NO_SUCH_RELATION,
            truncated=truncated,
        )

    def _tag_subtree(
        self, tag_name: str, depth: int, limit: int, include_archived: bool
    ) -> StructuralAnswer:
        """Everything under a tag subtree — the tags hierarchy walked, then membership.

        The path descends the taxonomy and then steps into the item, so a hit two levels
        down says which intermediate tag put it there. A flat "items tagged X" read cannot,
        and a semantic query for the tag's *name* answers a different question entirely.
        """
        name = (tag_name or "").strip()
        origin = tag_ref(name)
        if not name:
            return StructuralAnswer(TAG_SUBTREE, origin, STRUCTURE_ONLY, empty_reason=BAD_REQUEST)
        row = self.store.db.execute("SELECT id, name FROM tags WHERE name = ?", (name,)).fetchone()
        if not row:
            return StructuralAnswer(TAG_SUBTREE, origin, STRUCTURE_ONLY, empty_reason=NO_SUCH_TAG)

        # BFS down parent_id, remembering the path of tag names that reached each tag. A
        # visited set is required, not defensive: `set_tag_parent` guards cycles today, but
        # a taxonomy read must not be the thing that hangs if one ever exists.
        tag_paths: dict[int, tuple[PathStep, ...]] = {int(row["id"]): ()}
        frontier = [(int(row["id"]), str(row["name"]), 0)]
        order: list[tuple[int, str, int]] = [(int(row["id"]), str(row["name"]), 0)]
        seen_tags = {int(row["id"])}
        while frontier:
            tag_id, tname, tdepth = frontier.pop(0)
            # ``depth`` counts hops in the RECORDED PATH, the same unit ``_traverse`` uses, and
            # the membership step into the item consumes one of them. So depth=1 is "items on
            # this tag", depth=2 adds its children's. Bounding the tag descent by ``depth``
            # instead would make one verb's depth mean one more hop than every other's.
            if tdepth >= depth - 1:
                continue
            children = self.store.db.execute(
                "SELECT id, name FROM tags WHERE parent_id = ? ORDER BY name", (tag_id,)
            ).fetchall()
            for child in children:
                cid = int(child["id"])
                if cid in seen_tags:
                    continue
                seen_tags.add(cid)
                step = PathStep(
                    from_ref=tag_ref(tname),
                    edge="tag:child_of",
                    to_ref=tag_ref(str(child["name"])),
                    direction="outbound",
                    detail={"parent": tname, "child": str(child["name"])},
                )
                tag_paths[cid] = (*tag_paths[tag_id], step)
                frontier.append((cid, str(child["name"]), tdepth + 1))
                order.append((cid, str(child["name"]), tdepth + 1))

        hits: list[StructuralHit] = []
        seen_items: set[str] = set()
        truncated = False
        for tag_id, tname, tdepth in order:
            rows = self.store.db.execute(
                "SELECT i.id, i.title FROM item_tags it JOIN items i ON i.id = it.item_id "
                "WHERE it.tag_id = ? " + self._visibility_sql("i", include_archived) + " "
                "ORDER BY i.id",
                (tag_id,),
            ).fetchall()
            for r in rows:
                iid = str(r["id"])
                if iid in seen_items:
                    continue
                if len(hits) >= limit:
                    truncated = True
                    break
                seen_items.add(iid)
                step = PathStep(
                    from_ref=tag_ref(tname),
                    edge="tag:tagged",
                    to_ref=item_ref(iid),
                    direction="outbound",
                    detail={"tag": tname},
                )
                hits.append(
                    StructuralHit(
                        item_id=iid,
                        title=str(r["title"] or ""),
                        depth=tdepth + 1,
                        path=(*tag_paths[tag_id], step),
                    )
                )
            if truncated:
                break
        hits.sort(key=lambda h: (h.depth, h.item_id))
        return StructuralAnswer(
            TAG_SUBTREE,
            origin,
            STRUCTURE_ONLY,
            hits=tuple(hits),
            empty_reason="" if hits else NO_SUCH_RELATION,
            truncated=truncated,
        )

    def _changed_since(self, since: str, limit: int, include_archived: bool) -> StructuralAnswer:
        """What changed after a point in time.

        Structural because the answer is a *stored fact* (``items.updated_at``) rather than
        a ranking: the justification is the timestamp that satisfied the predicate, carried
        on the path's single step so "why is this here" is answerable from the hit alone.
        """
        stamp = (since or "").strip()
        origin = date_ref(stamp)
        if not stamp:
            return StructuralAnswer(CHANGED_SINCE, origin, STRUCTURE_ONLY, empty_reason=BAD_REQUEST)
        rows = self.store.db.execute(
            "SELECT i.id, i.title, i.updated_at FROM items i "
            "WHERE i.updated_at > ? " + self._visibility_sql("i", include_archived) + " "
            "ORDER BY i.updated_at DESC, i.id ASC LIMIT ?",
            (stamp, limit + 1),
        ).fetchall()
        truncated = len(rows) > limit
        hits = tuple(
            StructuralHit(
                item_id=str(r["id"]),
                title=str(r["title"] or ""),
                depth=1,
                path=(
                    PathStep(
                        from_ref=origin,
                        edge="changed_after",
                        to_ref=item_ref(str(r["id"])),
                        detail={"updated_at": str(r["updated_at"] or ""), "since": stamp},
                    ),
                ),
            )
            for r in rows[:limit]
        )
        return StructuralAnswer(
            CHANGED_SINCE,
            origin,
            STRUCTURE_ONLY,
            hits=hits,
            empty_reason="" if hits else NO_CHANGE_SINCE,
            truncated=truncated,
        )

    def _contradictions(self, item_id: str, limit: int, include_archived: bool) -> StructuralAnswer:
        """Which claims contradict each other — the ``contradicts`` edges, as pairs.

        One hit per edge, the counterpart named on the path, so a caller reads a pair and
        not a bare list of "suspicious" items. Scoped to one item when ``origin`` is given
        (both legs, because a contradiction has no natural direction).

        Deliberately reads ``item_relations`` only. The per-item ``insights.conflicts``
        narrative that ``/api/knowledge/conflicts`` serves is prose about one item, not a
        relation between two — ``contradiction.edges_from_conflicts`` is what turns the
        subset carrying both item ids into rows here, and traversing the narrative would
        mean inventing the edge it declined to assert.
        """
        origin = item_ref(item_id) if item_id else "corpus"
        vis = self._visibility_sql("i", include_archived)
        vis_t = self._visibility_sql("t", include_archived)
        if item_id:
            if not self._item_row(item_id):
                return StructuralAnswer(
                    CONTRADICTIONS, origin, STRUCTURE_ONLY, empty_reason=NO_SUCH_ITEM
                )
            sql = (
                "SELECT r.source_item_id AS a, r.target_item_id AS b, r.confidence, "
                "       r.provenance, i.title AS a_title, t.title AS b_title "
                "FROM item_relations r "
                "JOIN items i ON i.id = r.source_item_id JOIN items t ON t.id = r.target_item_id "
                f"WHERE r.relation_type = 'contradicts' {vis} {vis_t} "
                "  AND (r.source_item_id = ? OR r.target_item_id = ?) "
                "ORDER BY r.source_item_id, r.target_item_id LIMIT ?"
            )
            params: tuple = (item_id, item_id, limit + 1)
        else:
            sql = (
                "SELECT r.source_item_id AS a, r.target_item_id AS b, r.confidence, "
                "       r.provenance, i.title AS a_title, t.title AS b_title "
                "FROM item_relations r "
                "JOIN items i ON i.id = r.source_item_id JOIN items t ON t.id = r.target_item_id "
                f"WHERE r.relation_type = 'contradicts' {vis} {vis_t} "
                "ORDER BY r.source_item_id, r.target_item_id LIMIT ?"
            )
            params = (limit + 1,)
        rows = self.store.db.execute(sql, params).fetchall()
        truncated = len(rows) > limit
        hits: list[StructuralHit] = []
        for r in rows[:limit]:
            a, b = str(r["a"]), str(r["b"])
            # Report the counterpart when the query is item-scoped, the target otherwise.
            if item_id and b == item_id:
                subject, subject_title, other, other_title = a, r["a_title"], b, r["b_title"]
                # The step runs b→a, against the stored source→target row.
                direction = "inbound"
            else:
                subject, subject_title, other, other_title = b, r["b_title"], a, r["a_title"]
                direction = "outbound"
            hits.append(
                StructuralHit(
                    item_id=subject,
                    title=str(subject_title or ""),
                    depth=1,
                    path=(
                        PathStep(
                            from_ref=item_ref(other),
                            edge="relation:contradicts",
                            to_ref=item_ref(subject),
                            direction=direction,
                            detail={
                                "confidence": r["confidence"],
                                "provenance": r["provenance"],
                                "counterpart_title": str(other_title or ""),
                            },
                        ),
                    ),
                )
            )
        return StructuralAnswer(
            CONTRADICTIONS,
            origin,
            STRUCTURE_ONLY,
            hits=tuple(hits),
            empty_reason="" if hits else NO_CONTRADICTION,
            truncated=truncated,
        )

    # ── traversal machinery ─────────────────────────────────────────────────────

    def _traverse(
        self,
        start: str,
        expand: Callable[[str], Iterator[tuple[str, PathStep]]],
        *,
        depth: int,
        limit: int,
        include_archived: bool,
    ) -> tuple[tuple[StructuralHit, ...], bool]:
        """Breadth-first over stored edges, keeping the FIRST path that reaches each item.

        Breadth-first (not depth-first) so the recorded path is a shortest one: the
        justification a reader sees is the most direct chain the store holds, and a longer
        detour past the same item never overwrites it. The start item is never a hit — "what
        links to X" does not include X — but it is marked visited so a cycle back to it ends.
        """
        visited: set[str] = {start}
        paths: dict[str, tuple[PathStep, ...]] = {}
        frontier: list[tuple[str, int]] = [(start, 0)]
        hits: list[StructuralHit] = []
        truncated = False
        while frontier and not truncated:
            current, current_depth = frontier.pop(0)
            if current_depth >= depth:
                continue
            for neighbour, step in expand(current):
                if neighbour in visited:
                    continue
                row = self._item_row(neighbour)
                # An unresolvable neighbour is a dead end: `item_relations` has no cascade
                # on this branch, so a row can point at a deleted item.
                if row is None:
                    continue
                if not include_archived and (
                    row["status"] != "active" or int(row["is_archived"] or 0)
                ):
                    continue
                visited.add(neighbour)
                paths[neighbour] = (*paths.get(current, ()), step)
                if len(hits) >= limit:
                    truncated = True
                    break
                hits.append(
                    StructuralHit(
                        item_id=neighbour,
                        title=str(row["title"] or ""),
                        depth=current_depth + 1,
                        path=paths[neighbour],
                    )
                )
                frontier.append((neighbour, current_depth + 1))
        hits.sort(key=lambda h: (h.depth, h.item_id))
        return tuple(hits), truncated

    def _expand_inbound(self, item_id: str) -> Iterator[tuple[str, PathStep]]:
        """Everything that points AT ``item_id``: typed relations plus citations."""
        rows = self.store.db.execute(
            "SELECT source_item_id, relation_type, confidence, provenance FROM item_relations "
            "WHERE target_item_id = ? ORDER BY relation_type, source_item_id",
            (item_id,),
        ).fetchall()
        for r in rows:
            if r["relation_type"] not in TRAVERSABLE_RELATIONS:
                continue
            yield (
                str(r["source_item_id"]),
                PathStep(
                    from_ref=item_ref(item_id),
                    edge=f"relation:{r['relation_type']}",
                    to_ref=item_ref(str(r["source_item_id"])),
                    direction="inbound",
                    detail={"confidence": r["confidence"], "provenance": r["provenance"]},
                ),
            )
        cites = self.store.db.execute(
            "SELECT item_id, MIN(marker) AS marker FROM item_citations WHERE source_item_id = ? "
            "GROUP BY item_id ORDER BY item_id",
            (item_id,),
        ).fetchall()
        for c in cites:
            yield (
                str(c["item_id"]),
                PathStep(
                    from_ref=item_ref(item_id),
                    edge="cites",
                    to_ref=item_ref(str(c["item_id"])),
                    direction="inbound",
                    detail={"marker": c["marker"]},
                ),
            )

    def _expand_outbound(
        self, item_id: str, wanted: tuple[str, ...]
    ) -> Iterator[tuple[str, PathStep]]:
        placeholders = ", ".join("?" for _ in wanted)
        rows = self.store.db.execute(
            "SELECT target_item_id, relation_type, confidence, provenance FROM item_relations "
            f"WHERE source_item_id = ? AND relation_type IN ({placeholders}) "  # noqa: S608
            "ORDER BY relation_type, target_item_id",
            (item_id, *wanted),
        ).fetchall()
        for r in rows:
            yield (
                str(r["target_item_id"]),
                PathStep(
                    from_ref=item_ref(item_id),
                    edge=f"relation:{r['relation_type']}",
                    to_ref=item_ref(str(r["target_item_id"])),
                    direction="outbound",
                    detail={"confidence": r["confidence"], "provenance": r["provenance"]},
                ),
            )

    def _item_row(self, item_id: str):
        if not item_id:
            return None
        return self.store.db.execute(
            "SELECT id, title, status, is_archived FROM items WHERE id = ?", (item_id,)
        ).fetchone()

    @staticmethod
    def _visibility_sql(alias: str, include_archived: bool) -> str:
        if include_archived:
            return ""
        return f"AND {alias}.status = 'active' AND COALESCE({alias}.is_archived, 0) = 0"

    @staticmethod
    def _relation_filter(
        relations: Iterable[str] | None, default: tuple[str, ...]
    ) -> tuple[str, ...]:
        if relations is None:
            return default
        wanted = tuple(
            r
            for r in dict.fromkeys(str(x).strip().lower() for x in relations)
            if r in TRAVERSABLE_RELATIONS
        )
        return wanted

    # ── composition: restrict (above) then rank (here) ──────────────────────────

    def _rank_within(self, answer: StructuralAnswer, rank_query: str) -> StructuralAnswer:
        """Order the traversal's survivors by semantic closeness to ``rank_query``.

        Membership is already decided; this is ordering only. Every hit keeps its path and
        its depth, an unembeddable item scores 0.0 rather than being dropped (dropping
        would let the rank arm overrule a structural fact), and the sort is total —
        ``(-score, depth, item_id)`` — so the same store and query always produce the same
        order.
        """
        ids = [h.item_id for h in answer.hits]
        scores, mode = self._score(rank_query, ids)
        scored = tuple(
            StructuralHit(
                item_id=h.item_id,
                title=h.title,
                depth=h.depth,
                path=h.path,
                score=round(float(scores.get(h.item_id, 0.0)), 6),
            )
            for h in answer.hits
        )
        ordered = tuple(sorted(scored, key=lambda h: (-(h.score or 0.0), h.depth, h.item_id)))
        return StructuralAnswer(
            verb=answer.verb,
            origin=answer.origin,
            composition=RESTRICT_THEN_RANK,
            hits=ordered,
            rank_mode=mode,
            empty_reason=answer.empty_reason,
            truncated=answer.truncated,
        )

    def _score(self, query: str, ids: list[str]) -> tuple[dict[str, float], str]:
        """Per-candidate semantic score plus the mode that produced it.

        Vector when an embedder is present and the query embeds, lexical otherwise. The
        mode is returned rather than inferred by the caller because the two are on
        different scales and a consumer comparing scores across answers must be able to
        see which it is looking at.
        """
        if self.embedder is not None:
            try:
                qvec = self.embedder(query)
            except Exception:
                logger.debug("structural rank: embedder failed, using lexical", exc_info=True)
                qvec = None
            if qvec:
                out: dict[str, float] = {}
                for cid in ids:
                    row = self.store.db.execute(
                        "SELECT embedding FROM items WHERE id = ?", (cid,)
                    ).fetchone()
                    vec = bytes_to_floats(row["embedding"]) if row and row["embedding"] else []
                    out[cid] = _cosine(qvec, vec)
                return out, RANK_VECTOR
        texts: dict[str, str] = {}
        for cid in ids:
            row = self.store.db.execute(
                "SELECT title, summary, content FROM items WHERE id = ?", (cid,)
            ).fetchone()
            if row is None:
                texts[cid] = ""
                continue
            texts[cid] = " ".join(str(row[k] or "") for k in ("title", "summary", "content"))
        return lexical_scores(query, texts), RANK_LEXICAL


def render_answer(answer: StructuralAnswer, *, limit: int = 25) -> str:
    """The answer as agent-facing text: one line per hit, each carrying its own path.

    An empty answer renders the reason, never an apology that could be mistaken for "the
    search found nothing relevant" — the distinction between "no such relation" and "no
    close match" is the whole point of the verb.
    """
    if not answer.hits:
        return f"({empty_message(answer.empty_reason)} — {answer.verb} {answer.origin})"
    head = f"{answer.verb} {answer.origin} — {len(answer.hits)} result(s)"
    if answer.composition == RESTRICT_THEN_RANK:
        head += f", ranked ({answer.rank_mode}) after the structural restriction"
    if answer.truncated:
        head += " [truncated]"
    lines = [head]
    for hit in answer.hits[:limit]:
        score = "" if hit.score is None else f" score={hit.score:.3f}"
        title = hit.title or "(untitled)"
        lines.append(f"- {title} (id={hit.item_id}, depth={hit.depth}{score})")
        lines.append(f"    why: {describe_path(hit.path)}")
    return "\n".join(lines)
