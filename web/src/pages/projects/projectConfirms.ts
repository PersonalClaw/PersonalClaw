import { Archive } from 'lucide-react'
import { confirm } from '../../ui/dialog'

/** ── A project's confirms, word for word, in one place ───────────────────────────────────────
 *
 *  The delete, force-delete and archive confirms a project's "…" asks, from the Projects list's
 *  row and from the project's own page, so both ask the same words. Each resolves true when the
 *  owner confirms. `blastRadiusIsVerified` and `deleteNamesTheTasks` read their words here. */

export function confirmDeleteProject(name: string): Promise<boolean> {
  return confirm({
    title: `Delete project "${name}"?`,
    // 🔴 THIS DIALOG PROMISED "the tasks themselves stay" WHILE THE HANDLER DELETED EVERY ONE OF THEM.
    // The cascade runs in the calling handler (`tasks/hierarchy_handlers.py`: `collect_tasks(project=…)`
    // → `_cascade_delete_tasks`), above `hierarchy.delete_project`, whose own docstring is about the
    // function it documents. No count: this page cannot obtain an honest one (`api.tasks()` defaults
    // to `limit=50`, and the server's `total` saturates at 500), so the loss is stated categorically.
    // "Workspace files on disk are left untouched" stays LAST, the one reassuring clause.
    body: 'Every task in this project is permanently deleted, along with its notes, action plan and exit criteria — that cannot be undone. Its context directory and task lists go with it. Workspace files on disk are left untouched.',
    danger: true, confirmLabel: 'Delete',
  })
}

/** The re-confirm when the delete answered 409: the project still has bound work. */
export function confirmForceDeleteProject(name: string): Promise<boolean> {
  return confirm({
    title: 'Project still has active work',
    // Tasks named first: the larger and less recoverable loss of the two, and the same cascade
    // runs on the force path.
    body: `"${name}" still has work scoped under it. Every task in the project is permanently deleted with its notes and exit criteria. Deleting it now also STOPS and REMOVES any bound loops (Goal, Code, Design, General) — their workers are halted, their task worktrees and branches deleted with any work in them that isn't merged into your workspace, and the loops deleted (not just unlinked). Project-bound chats are kept but UNBOUND (detached from this project, not deleted). This can't be undone.`,
    danger: true, confirmLabel: 'Delete anyway',
  })
}

export function confirmArchiveProject(name: string): Promise<boolean> {
  return confirm({
    title: `Archive project "${name}"?`,
    body: 'This project stays in Projects, but is removed from project pickers until you restore it. Nothing is deleted: its work, tasks, context, and workspace files stay in place.',
    confirmLabel: 'Archive',
    icon: Archive,
  })
}
