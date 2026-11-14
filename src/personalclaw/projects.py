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

import json
import logging
import re
from pathlib import Path

from personalclaw.atomic_write import atomic_json_write
from personalclaw.tasks.hierarchy import HierarchyStore, projects_root
from personalclaw.tasks.models import MADE_FOR_LOOP, Project

logger = logging.getLogger(__name__)

# The persistent catch-all project for work created without a chosen project.
DEFAULT_PROJECT_NAME = "Personal"


def _store() -> HierarchyStore:
    return HierarchyStore()


def made_for_loop(loop_id: str) -> str:
    """The ``made_for`` a project a loop made for itself carries."""
    return f"{MADE_FOR_LOOP}{loop_id}"


def resolve_project_id(
    chosen_id: str = "", *, auto_name: str = "", made_for: str | None = None
) -> str:
    """Return the project id a work unit should bind to.

    - A valid ``chosen_id`` that still resolves is used as-is.
    - Otherwise a fresh project is auto-created (the "initiate a project for the
      session" behavior): named ``auto_name`` if given (deduped against existing
      names), else routed to the persistent ``Personal`` catch-all. The auto-named
      project is created UNLOCKED so the LLM can rename it later, and carries
      *made_for*, the loop it was made for, when the caller names one.

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
    return _create_unique(store, name, made_for=made_for).id


def project_exists(project_id: str) -> bool:
    """Whether *project_id* names a project: its record is there and reads. ``False`` for ``""``,
    for an id no project has (a deleted one), and on any error, so a reader that asks never fails
    on the answer."""
    if not project_id:
        return False
    try:
        return _store().get_project(project_id) is not None
    except Exception:  # noqa: BLE001 - an id that cannot be read names no project
        logger.debug("project_exists: %r could not be read", project_id, exc_info=True)
        return False


def _create_unique(store: HierarchyStore, name: str, *, made_for: str | None = None):
    """Create a project, suffixing the name on collision so a generated name never
    fails. Returns the created (or existing-on-exact-name) Project."""
    base = name[:80].strip() or DEFAULT_PROJECT_NAME
    candidate = base
    n = 2
    while store.get_project_by_name(candidate) is not None:
        suffix = f" ({n})"
        candidate = base[: 80 - len(suffix)] + suffix
        n += 1
    return store.create_project(candidate, made_for=made_for)


def clear_made_for(project_id: str, *, unless: str = "") -> None:
    """The owner made *project_id* theirs (a chat or another loop started in it): take off the
    loop's mark, unless it is *unless*, the loop's own. Never raises: a mark left on is a project
    folded under "Made for goal loops", which the owner can still open."""
    if not project_id:
        return
    try:
        _store().clear_made_for(project_id, unless=unless)
    except Exception:  # noqa: BLE001 - the work that clears it must never fail on it
        logger.warning("could not clear a project's loop mark", exc_info=True)


def backfill_made_for() -> int:
    """Give each project file written before ``made_for`` existed its answer, once. The gateway's
    boot calls it (``cli_server._boot_config``); a read never writes it.

    The stamp's rule, read off the loops: a project is made for the loop (the oldest, when more
    than one) whose ``tasks_project_id`` names it while its ``project_id`` is empty, when the
    project's name is not locked, it is not built in, its brief is empty, and no loop's
    ``project_id`` names it (the owner chose it for one). Else ``null``. It writes only files
    without the key, so it runs once per file. When the loops cannot be read it writes nothing,
    so a later boot decides. It reads no chats.

    The same pass names each of those files' projects whose name is not the owner's (not locked,
    not built in) as a loop's project is named now, from the masked text (:func:`masked_name`),
    since a loop named them from its raw name or task before: a name that is the one an older
    release gave it from such a loop (:func:`_named_as_shown`), and else a name the mask itself
    changes. The rest of each file stays as written. A file it cannot write is passed over, so the
    next boot tries it again.

    Returns how many files it wrote."""
    from personalclaw.loop import store as loop_store
    from personalclaw.security import redact_for_display

    root = projects_root()
    if not root.is_dir():
        return 0
    pending: list[tuple[Path, dict]] = []
    names: set[str] = set()
    for folder in sorted(root.iterdir()):
        path = folder / "project.json"
        if not path.is_file():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        names.add(str(record.get("name") or ""))
        if "made_for" not in record:
            pending.append((path, record))
    if not pending:
        return 0
    try:
        loops = loop_store.list_all()
    except Exception:  # noqa: BLE001 - nothing is known, so nothing is written
        logger.warning("loops unreadable; projects keep no loop mark until a later boot")
        return 0
    chosen = {lp.project_id for lp in loops if lp.project_id}
    made: dict[str, str] = {}
    # Every loop that could have named a project: its tasks went there with no project chosen.
    named_by: dict[str, list] = {}
    for lp in sorted(loops, key=lambda lp: lp.created_at or 0.0):
        if lp.tasks_project_id and not lp.project_id:
            made.setdefault(lp.tasks_project_id, lp.id)
            named_by.setdefault(lp.tasks_project_id, []).append(lp)
    written = 0
    for path, record in pending:
        project = Project.from_dict(record)
        loop_id = made.get(project.id, "")
        its_loops = bool(
            loop_id
            and not project.name_locked
            and not project.is_builtin_project()
            and not project.brief.strip()
            and project.id not in chosen
        )
        record["made_for"] = made_for_loop(loop_id) if its_loops else None
        if not project.name_locked and not project.is_builtin_project():
            shown = _named_as_shown(project.name, named_by.get(project.id, ()))
            if shown is None and redact_for_display(project.name) != project.name:
                shown = masked_name(project.name)
            if shown is not None:
                names.discard(project.name)
                record["name"] = name = _unused(shown, names)
                names.add(name)
        try:
            atomic_json_write(path, record)
        except OSError:
            logger.warning("project file %s unwritable; a later boot gives it its mark", path)
            continue
        written += 1
    return written


def _named_as_shown(stored: str, loops) -> str | None:
    """The name a project takes now when *stored* is the one an older release gave it from one of
    *loops*: the loop's raw name or task cut at 80 characters, or that cut shortened for a ` (n)`
    suffix on a clash, as releases before this one named a loop's project. A loop with a name is
    tried by its task too: it may have been renamed since its first provision named the project
    from that task, and a rename never renamed the project. None when no loop named it so, or
    when the loop's text reads the same masked."""
    for lp in loops:
        texts = [lp.name or lp.task or ""]
        if lp.name and lp.task:
            texts.append(lp.task)
        for text in texts:
            old = (text or "Loop")[:80].strip()
            now = masked_name(text) or "Loop"
            if now == old:
                continue
            if stored == old:
                return now
            clash = re.fullmatch(r"(.*)( \((\d+)\))", stored, flags=re.S)
            if clash and clash.group(1) == old[: 80 - len(clash.group(2))]:
                return now
    return None


def masked_name(text: str) -> str:
    """A project name made from a loop's name or task, as the loop's reads show that text: the
    display mask first, then the 80-character cut."""
    from personalclaw.security import redact_for_display

    return redact_for_display(text or "")[:80].strip()


def _unused(name: str, taken: set[str]) -> str:
    """*name*, or *name* with the first `` (n)`` no other project has."""
    if name not in taken:
        return name
    n = 2
    while True:
        suffix = f" ({n})"
        candidate = name[: 80 - len(suffix)] + suffix
        if candidate not in taken:
            return candidate
        n += 1


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
    if project is None or project.is_builtin_project() or project.name_locked:
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
