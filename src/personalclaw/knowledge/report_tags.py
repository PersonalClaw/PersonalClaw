"""Which reports read a tag, which of a report's tags are gone, and the sentence a report whose
tags are all gone says in place of running.

A report's scope holds its tags by name, and reads each named tag's subtree, matched without
regard to case (`knowledge_report_provider._tag_closure`). So a tag is read by every report whose
scope names it or one of its ancestors, and a name that matches no tag now reads nothing. A
rename or merge carries the scopes along (`research_reports.carry_scope_tag`), and a delete
leaves the name in place: a scope with no tags reads the whole library, so dropping
the last name would turn a report on one tag into a report on everything. The delete records
the name on each report that read it (``tags_deleted``), and a report's tags are gone when that
record names them and no tag matches: a tag nobody has made yet is never gone.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

#: The ``last_status`` a report records when every tag its source names is gone.
TAGS_MISSING = "tags_missing"


def fold(name: str) -> str:
    """A tag name as scopes match it: trimmed, and without regard to case."""
    return str(name or "").strip().casefold()


def scope_names(defn: Any) -> list[str]:
    """The tag names *defn*'s scopes read, its source's first, each once."""
    out: list[str] = []
    seen: set[str] = set()
    for scope in (getattr(defn, "source", None), getattr(defn, "context", None)):
        for name in getattr(scope, "tags", ()) or ():
            if fold(name) and fold(name) not in seen:
                seen.add(fold(name))
                out.append(str(name).strip())
    return out


def reports_by_tag(tag_rows: Sequence[dict], reports: Iterable[Any]) -> dict[int, list[dict]]:
    """Map of tag id → ``[{id, name}]``, the reports that read it: those whose scope names the tag
    or one of its ancestors."""
    parents: dict[int, int | None] = {}
    names: dict[int, str] = {}
    for row in tag_rows:
        tag_id = int(row["id"])
        parent = row.get("parent_id")
        parents[tag_id] = int(parent) if parent is not None else None
        names[tag_id] = fold(row.get("name") or "")
    readers = [(defn, {fold(n) for n in scope_names(defn)}) for defn in reports]
    out: dict[int, list[dict]] = {}
    for tag_id in names:
        line: set[str] = set()
        walked: set[int] = set()
        at: int | None = tag_id
        while at is not None and at in names and at not in walked:  # a cycle ends the walk
            walked.add(at)
            line.add(names[at])
            at = parents.get(at)
        out[tag_id] = [
            {"id": defn.id, "name": defn.name} for defn, wanted in readers if wanted & line
        ]
    return out


def missing_tags(defn: Any, tag_names: set[str]) -> list[str]:
    """The names in *defn*'s scopes that the tag delete route recorded for it (``tags_deleted``,
    `research_reports.record_deleted_tag`) and that match no tag now (*tag_names*, folded). A
    name no tag has had is not one, and a deleted tag made again drops out."""
    deleted = {fold(n) for n in getattr(defn, "tags_deleted", ()) or ()}
    return [
        name for name in scope_names(defn) if fold(name) in deleted and fold(name) not in tag_names
    ]


def _listed(names: Sequence[str]) -> str:
    quoted = [f"“{n}”" for n in names]
    return quoted[0] if len(quoted) == 1 else f"{', '.join(quoted[:-1])} and {quoted[-1]}"


def gone_sentence(defn: Any, tag_names: set[str]) -> str:
    """What a report says in place of running when its source names tags and none of them is there
    any more, "" when it can run: "Reads nothing: the tag “perf” was deleted."."""
    source = getattr(defn, "source", None)
    tags: tuple = tuple(getattr(source, "tags", None) or ())
    if source is None or getattr(source, "intent", None) or not tags:
        return ""
    named = [str(n).strip() for n in tags if fold(n)]
    missing = {fold(n) for n in missing_tags(defn, tag_names)}
    # Every source tag is among `missing_tags`: deleted, and matching no tag now. A tag nobody has
    # made yet is not, and its report reads what is new as it always has.
    if not named or any(fold(n) not in missing for n in named):
        return ""
    if len(named) == 1:
        return f"Reads nothing: the tag {_listed(named)} was deleted."
    return f"Reads nothing: the tags {_listed(named)} were deleted."


def tag_names(store: Any) -> set[str]:
    """Every tag name in the library now, folded: the names alone, with none of the usage counts
    the tag list works out for each."""
    return {fold(str(row[0] or "")) for row in store.db.execute("SELECT name FROM tags")}
