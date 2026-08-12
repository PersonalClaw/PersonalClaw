"""Project scoping for knowledge items (WORK-CONTAINERS §1.6).

Knowledge is ONE global library by design — a project is a **tag plus item metadata**, never
a second database (`session_brief.project_tag` states the same boundary). What §1.6 adds is
provenance and a container filter for the items a RUN writes:

* ``project_id`` — the container the item was produced in. An ordering/scoping key: it is
  what makes a project's brief and its Knowledge view able to ask "what did work on THIS
  project leave behind".
* ``run_id`` — the producing run, so an item is traceable back to the work that wrote it.
* ``sharing_policy`` — ``private`` | ``shared``, the **cross-container surfacing filter**:
  a project's view shows its own items whatever their policy, and another project's items
  only when they are ``shared``.

The default is ``private`` (:data:`DEFAULT_SHARING_POLICY`) deliberately: a run that forgets
to declare a policy must not leak what it wrote into every other project's view. The safe
direction for an un-declared value is "stays home", and widening later is a one-field edit
while un-leaking is not.

The enum is CLOSED. Every consumer enumerates both members explicitly and raises on an
unhandled one — a silent default branch here would turn a future third policy into
"whatever the last reader assumed", which is how a privacy filter becomes decorative.
"""

from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Any

from personalclaw.knowledge.session_brief import project_tag

logger = logging.getLogger(__name__)

#: Metadata keys this module owns inside an item's ``file_metadata`` blob.
PROJECT_ID_KEY = "project_id"
RUN_ID_KEY = "run_id"
SHARING_POLICY_KEY = "sharing_policy"


class SharingPolicy(str, Enum):
    """How far a run-written knowledge item travels. Closed — exactly two values."""

    #: Visible only inside the project that produced it.
    PRIVATE = "private"
    #: Also surfaced in other projects' Knowledge views, labeled with its source project.
    SHARED = "shared"


#: What an item gets when a run does not declare a policy. See the module docstring for why
#: this is the private end: an un-declared item stays in its own container.
DEFAULT_SHARING_POLICY = SharingPolicy.PRIVATE


def normalize_policy(value: Any) -> SharingPolicy:
    """Coerce a declared policy to a member. Anything unrecognised → the private default.

    Deliberate fail-closed coercion, not a swallowed default branch: a garbage value means
    the caller's intent is unknown, and the only safe reading of unknown intent for a
    visibility control is "do not surface it elsewhere". Logged so a template typo is
    findable instead of merely quiet.
    """
    raw = str(value or "").strip().lower()
    if not raw:
        return DEFAULT_SHARING_POLICY
    try:
        return SharingPolicy(raw)
    except ValueError:
        logger.info(
            "unknown sharing_policy %r on a knowledge write — treating it as %s",
            raw,
            DEFAULT_SHARING_POLICY.value,
        )
        return DEFAULT_SHARING_POLICY


def write_scope(
    *, project_id: str = "", run_id: str = "", requested_policy: Any = None
) -> dict[str, str]:
    """The scope metadata a run-written item carries. ``{}`` when the write has no run scope.

    An item written outside a run (a manual capture, a trigger with no run) gets NOTHING —
    no ``sharing_policy`` on a row that belongs to no container, because a visibility field
    nobody can act on is a field that reads as meaningful and is not.
    """
    pid = str(project_id or "").strip()
    rid = str(run_id or "").strip()
    if not pid and not rid:
        return {}
    out: dict[str, str] = {SHARING_POLICY_KEY: normalize_policy(requested_policy).value}
    if pid:
        out[PROJECT_ID_KEY] = pid
    if rid:
        out[RUN_ID_KEY] = rid
    return out


def scope_tags(project_id: str) -> list[str]:
    """Tags that file an item under its project. Empty for a project-less write.

    This is what makes the project half of §1.6 real rather than declared: the project brief
    (`session_brief.load_items`) reads items by exactly this tag, so before anything wrote it
    the brief was a live reader of a key no writer produced — it returned nothing for every
    project, forever.
    """
    tag = project_tag(project_id)
    return [tag] if tag else []


def item_scope(metadata: Any) -> tuple[str, str, SharingPolicy]:
    """``(project_id, run_id, policy)`` read off an item's metadata blob (dict or JSON str)."""
    meta = _as_dict(metadata)
    return (
        str(meta.get(PROJECT_ID_KEY, "") or ""),
        str(meta.get(RUN_ID_KEY, "") or ""),
        normalize_policy(meta.get(SHARING_POLICY_KEY)),
    )


def visible_in_project(metadata: Any, *, project_id: str) -> bool:
    """Whether an item may appear in *project_id*'s Knowledge view.

    * its OWN project's items — always (private and shared alike; the owner sees everything
      it produced);
    * another project's items — only when ``shared``;
    * an item with no project scope — no: it belongs to the global library, which has its
      own view, and mixing it in would make every project's view a copy of the library.
    """
    pid = str(project_id or "").strip()
    if not pid:
        return False
    owner, _run_id, policy = item_scope(metadata)
    if not owner:
        return False
    if owner == pid:
        return True
    # Cross-container: the closed enum, enumerated. A new member must be handled here
    # explicitly rather than inheriting whichever branch happens to fall through.
    if policy is SharingPolicy.SHARED:
        return True
    if policy is SharingPolicy.PRIVATE:
        return False
    raise ValueError(f"unhandled sharing policy {policy!r}")


def project_items(store: Any, *, project_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """The items a project's Knowledge view shows, newest first.

    Two passes rather than one clever query: the project's own items come off the tag join
    (the same index the brief uses), and cross-container candidates come off a cheap
    ``file_metadata`` prefilter and are then decided in Python by
    :func:`visible_in_project` — one filter implementation, so the view and any other reader
    cannot drift apart on what "shared" means.

    Never raises: a store that cannot answer yields an empty list, because a project page
    must render without its Knowledge section rather than 500.
    """
    pid = str(project_id or "").strip()
    if not pid:
        return []
    rows: dict[str, dict[str, Any]] = {}
    for row in _own_rows(store, pid, limit) + _shared_candidates(store, limit):
        meta = _as_dict(row.get("file_metadata"))
        if not visible_in_project(meta, project_id=pid):
            continue
        owner, run_id, policy = item_scope(meta)
        rows[str(row["id"])] = {
            "id": str(row["id"]),
            "title": str(row.get("title") or ""),
            "kind": str(row.get("kind") or ""),
            "summary": str(row.get("summary") or "")[:280],
            "updated_at": str(row.get("updated_at") or ""),
            "project_id": owner,
            "run_id": run_id,
            "sharing_policy": policy.value,
            # "" for the project's own items; the OWNING project's display name for a
            # cross-container hit, so the view can say where a shared item came from
            # instead of implying this project produced it.
            "source_project": "" if owner == pid else _project_name(owner),
        }
    ordered = sorted(rows.values(), key=lambda r: r["updated_at"], reverse=True)
    return ordered[: max(1, limit)]


# ── internals ───────────────────────────────────────────────────────────────


_SELECT = "SELECT i.id, i.kind, i.title, i.summary, i.updated_at, i.file_metadata FROM items i "


def _own_rows(store: Any, project_id: str, limit: int) -> list[dict[str, Any]]:
    tag = project_tag(project_id)
    if not tag:
        return []
    try:
        return [
            dict(r)
            for r in store.db.execute(
                _SELECT + "JOIN item_tags it ON it.item_id = i.id "
                "JOIN tags t ON t.id = it.tag_id "
                "WHERE t.name = ? AND i.is_archived = 0 "
                "ORDER BY i.updated_at DESC LIMIT ?",
                (tag, max(1, limit)),
            )
        ]
    except Exception:
        logger.debug("project knowledge query failed for %r", project_id, exc_info=True)
        return []


def _shared_candidates(store: Any, limit: int) -> list[dict[str, Any]]:
    """Rows that carry ANY sharing policy — the prefilter, not the decision.

    Matches on the KEY, never on the serialized value: `json.dumps` spacing is a formatting
    detail, and a `LIKE '%"sharing_policy": "shared"%'` prefilter would silently miss every
    row written by a caller that dumped compactly.
    """
    try:
        return [
            dict(r)
            for r in store.db.execute(
                _SELECT + "WHERE i.is_archived = 0 AND i.file_metadata LIKE ? "
                "ORDER BY i.updated_at DESC LIMIT ?",
                ('%"sharing_policy"%', max(1, limit) * 4),
            )
        ]
    except Exception:
        logger.debug("shared knowledge prefilter failed", exc_info=True)
        return []


def _project_name(project_id: str) -> str:
    try:
        from personalclaw.tasks.hierarchy import HierarchyStore

        project = HierarchyStore().get_project(project_id)
        return str(getattr(project, "name", "") or "") if project else ""
    except Exception:
        logger.debug("source project name lookup failed for %r", project_id, exc_info=True)
        return ""


def _as_dict(metadata: Any) -> dict[str, Any]:
    if isinstance(metadata, dict):
        return metadata
    if isinstance(metadata, str) and metadata.strip():
        try:
            parsed = json.loads(metadata)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
