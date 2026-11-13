"""Which project a thing in the library is in: one rule, read by ``GET /api/projects/{id}/linked``
and by the library's read, ``GET /api/library``.

- A loop is in the one project :func:`personalclaw.loop.store.loop_project_id` names.
- An artifact is in its own ``project_id`` when that names a project. Otherwise it is in the
  project of the loop its ``loop:<id>`` tag names: a loop's deliverables carry the tag and an
  empty ``project_id``. The explicit id wins over the tag, and an artifact the owner took out of
  its project (``project_cleared``) is in none, whatever its tag.
- A knowledge item is in the project :mod:`personalclaw.knowledge.project_scope` files it under,
  and shows in another project only when it is shared.

An id that names no project reads as none: the thing keeps the id, and sits under No project.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: The tag a loop's deliverable carries, ``loop:<loop id>``.
LOOP_TAG_PREFIX = "loop:"


@dataclass(frozen=True)
class Projects:
    """The projects that exist now, read once for a whole listing: their names by id, and each
    loop's one project by loop id."""

    names: dict[str, str] = field(default_factory=dict)
    loops: dict[str, str] = field(default_factory=dict)

    def exists(self, project_id: str) -> bool:
        return bool(project_id) and project_id in self.names

    def name(self, project_id: str) -> str:
        return self.names.get(project_id, "")


def read_projects() -> Projects:
    """The projects and loops as they stand. A store that can't be read gives none of either:
    every thing then reads as in no project, rather than the read failing."""
    names: dict[str, str] = {}
    try:
        from personalclaw.tasks.hierarchy import HierarchyStore

        names = {p.id: str(p.name or "") for p in HierarchyStore().list_projects()}
    except Exception:
        logger.debug("projects unreadable for the project rule", exc_info=True)
    loops: dict[str, str] = {}
    try:
        from personalclaw.loop import store as loop_store

        exists = names.__contains__
        for lp in loop_store.list_all():
            pid = loop_store.loop_project_id(lp, exists)
            if pid:
                loops[lp.id] = pid
    except Exception:
        logger.debug("loops unreadable for the project rule", exc_info=True)
    return Projects(names=names, loops=loops)


def loop_ids_in_tags(tags: Iterable[str]) -> list[str]:
    """The loop ids an artifact's ``loop:<id>`` tags name, in tag order."""
    return [t[len(LOOP_TAG_PREFIX) :] for t in tags if str(t).startswith(LOOP_TAG_PREFIX)]


def artifact_project_id(art: Any, projects: Projects) -> str:
    """The one project *art* is in, or "" (see the module docstring)."""
    own = str(getattr(art, "project_id", "") or "")
    if projects.exists(own):
        return own
    if getattr(art, "project_cleared", False):
        return ""  # the owner took it out of its project: its loop's tag no longer files it
    for loop_id in loop_ids_in_tags(getattr(art, "tags", ()) or ()):
        pid = projects.loops.get(loop_id, "")
        if pid:
            return pid
    return ""


def knowledge_project_id(metadata: Any, projects: Projects) -> str:
    """The project a knowledge item is filed under, when that project exists, else ""."""
    from personalclaw.knowledge.project_scope import item_scope

    owner, _run_id, _policy = item_scope(metadata)
    return owner if projects.exists(owner) else ""


def project_artifacts(prov: Any, project_id: str, projects: Projects) -> list[Any]:
    """The artifacts in *project_id*, by the rule, in the provider's own order."""
    seen: set[str] = set()
    out: list[Any] = []
    for art in prov.list():
        if art.slug in seen or artifact_project_id(art, projects) != project_id:
            continue
        seen.add(art.slug)
        out.append(art)
    return out


def requested_project(value: Any) -> tuple[str, str]:
    """A request's ``project_id``, checked: ``(id, "")`` for "" (no project) or a project that
    exists; ``("", why)`` for anything else, which the route refuses with nothing written."""
    if not isinstance(value, str):
        return "", "project_id must be a string"
    pid = value.strip()
    if pid and not read_projects().exists(pid):
        return "", f"no project has the id {pid!r}"
    return pid, ""
