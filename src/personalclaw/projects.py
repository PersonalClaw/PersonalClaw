"""Project service — the first-class work-unit container.

A **Project** (the Tasks ``hierarchy.Project``, see :mod:`personalclaw.tasks.models`)
ties together everything a user does on one logical effort: Goal Loops, Code
projects, and manually-created Tasks all scope under one project, sharing its
context directory for continuation.

This module is the small service layer the work-unit features call to RESOLVE a
project to bind to. It deliberately holds no state of its own — it composes the
``HierarchyStore`` — so both Goal Loop and Code share one binding policy:

- ``resolve_project_id`` — given an optional chosen id, return a usable project id,
  auto-creating a fresh one when nothing is chosen (the "initiate a project for the
  session" behavior).
- ``ensure_task_list`` — find-or-create the per-work-unit TaskList UNDER its bound
  project (replacing the old umbrella "Goal Loops"/"Code" projects), so a unit's
  decomposed tasks live in the project the user chose.
"""

from __future__ import annotations

import logging

from personalclaw.tasks.hierarchy import HierarchyStore

logger = logging.getLogger(__name__)

# The persistent catch-all project for work created without a chosen project.
DEFAULT_PROJECT_NAME = "Personal"


def _store() -> HierarchyStore:
    return HierarchyStore()


def resolve_project_id(chosen_id: str = "", *, auto_name: str = "") -> str:
    """Return the project id a work unit should bind to.

    - A valid ``chosen_id`` that still resolves is used as-is.
    - Otherwise a fresh project is auto-created (the "initiate a project for the
      session" behavior): named ``auto_name`` if given (deduped against existing
      names), else routed to the persistent ``Personal`` catch-all. The auto-named
      project is created UNLOCKED so the LLM can rename it later (S4).

    Always returns a real, existing project id.
    """
    store = _store()
    if chosen_id:
        existing = store.get_project(chosen_id)
        if existing is not None:
            return existing.id
    name = (auto_name or "").strip()
    if not name:
        return store.find_or_create_project(DEFAULT_PROJECT_NAME).id
    # Auto-named session project: dedupe the name so create() can't collide.
    return _create_unique(store, name).id


def _create_unique(store: HierarchyStore, name: str):
    """Create a project, suffixing the name on collision so a generated name never
    fails. Returns the created (or existing-on-exact-name) Project."""
    base = name[:80].strip() or DEFAULT_PROJECT_NAME
    candidate = base
    n = 2
    while store.get_project_by_name(candidate) is not None:
        suffix = f" ({n})"
        candidate = base[: 80 - len(suffix)] + suffix
        n += 1
    return store.create_project(candidate)


def ensure_task_list(project_id: str, list_name: str) -> str:
    """Find-or-create a TaskList named ``list_name`` under ``project_id``. Idempotent
    — a second call returns the same list. Returns the TaskList id."""
    store = _store()
    for tl in store.list_task_lists(project_id=project_id):
        if tl.name == list_name:
            return tl.id
    return store.create_task_list(name=list_name, project_id=project_id).id


def context_dir(project_id: str) -> str:
    """The absolute path to a project's context directory (created on demand), or ""
    if ``project_id`` is blank / the project doesn't exist. This is the shared space
    where everything operating on the project — Goal Loops, Code projects — should
    consolidate durable cross-feature context (notes, decisions, scratch) so work
    continues coherently across features and sessions."""
    if not project_id:
        return ""
    store = _store()
    if store.get_project(project_id) is None:
        return ""
    return str(store.context_dir(project_id))


def maybe_rename_from(project_id: str, title: str) -> None:
    """Update an AUTO-named project's name to a better LLM-generated ``title`` as the
    user's work clarifies it (the "LLM keeps the name updated" behavior).

    No-op when:
    - the project is missing, default (Personal/Repeatable), or ``name_locked``
      (the user renamed it manually → the LLM must not clobber that), or
    - ``title`` is blank or already the project's name.

    The new name is deduped against existing names so the rename can't collide. The
    project stays UNLOCKED so a later, better title can refine it again — until the
    user takes over with a manual rename (which sets ``name_locked``)."""
    title = (title or "").strip()[:80]
    if not title:
        return
    store = _store()
    project = store.get_project(project_id)
    if project is None or project.is_default_project() or project.name_locked:
        return
    if project.name == title:
        return
    # Dedupe: if the exact title is taken by ANOTHER project, suffix it.
    target = title
    other = store.get_project_by_name(target)
    if other is not None and other.id != project_id:
        n = 2
        while True:
            suffix = f" ({n})"
            target = title[: 80 - len(suffix)] + suffix
            existing = store.get_project_by_name(target)
            if existing is None or existing.id == project_id:
                break
            n += 1
    try:
        store.update_project(project_id, name=target)
    except ValueError:
        logger.debug("maybe_rename_from: rename to %r skipped (collision)", target)
