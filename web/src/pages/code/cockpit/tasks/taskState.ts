import { Loader2, CheckCircle2, Circle, Clock, XCircle, CirclePlay } from 'lucide-react'
import { type TaskItem } from '../../../../lib/api'

// ── A cockpit task's state ─────────────────────────────────────────────────────────────────────
//
// Which of its states a task is in, from its status, its blockers, the queue and its stage, and
// the icon, colour and label each state shows.

// A task's execution state, derived from its status + dependencies + the project
// queue. Drives the row's icon, label, and which action (Queue / queued / blocked)
// it offers. This is the user-facing model of the task-driven scheduler.
// A task that's reached a TERMINAL status — done/completed OR cancelled. Used to
// build the "resolved" set that ungates dependents + the done-count, matching the
// backend (tasks/reconcile: a cancelled blocker is resolved). A cancelled task must
// NOT keep its dependents blocked forever.
export const isTerminalTask = (t: TaskItem): boolean =>
  t.status === 'done' || t.status === 'completed' || t.status === 'cancelled'

export type ExecState = 'done' | 'cancelled' | 'running' | 'queued' | 'blocked' | 'ready' | 'waiting' | 'undone'

// `stageActive` defaults true so callers that don't pass it (e.g. the task detail
// view) keep the prior behavior. A queued/ready task in a NOT-yet-active stage is
// 'waiting' — the phase barrier holds it until its stage opens, even if its own
// deps are satisfied; surfacing that avoids the user thinking it'll run now.
// `ended`: the project finished or was stopped (TERMINAL_STATUSES), so nothing of it runs again.
// A task it left unfinished is 'undone' — not queued, ready to queue or waiting for a stage that
// will never open, which is what a stopped project's rail used to say.
export function execState(t: TaskItem, doneIds: Set<string>, queued: Set<string>, stageActive = true, knownIds?: Set<string>, ended = false): ExecState {
  if (t.status === 'done' || t.status === 'completed') return 'done'
  // Cancelled is terminal (TERMINAL_STATUSES) — must NOT fall through to ready/queued,
  // which would wrongly invite the user to queue work that's been cancelled.
  if (t.status === 'cancelled') return 'cancelled'
  if (ended) return 'undone'
  if (t.status === 'in_progress') return 'running'
  const deps = (t.dependencies?.map((d) => d.depends_on_task_id) ?? t.depends_on ?? [])
  // A dep blocks only if it's a KNOWN task that isn't terminal. An edge to an unknown id
  // (pruned in a re-plan, or a cross-stage target not in this set) is NOT an actionable
  // blocker — and the TaskDetailView's blocker LIST already drops it (byId.has filter).
  // Counting it here too made a task read 'blocked' with an EMPTY blocker list — a
  // dead-end the user can't act on + that never resolves. When knownIds is given, treat
  // an unknown dep as resolved so the row-state matches the detail view exactly.
  const blocked = deps.some((id) => !!id && !doneIds.has(id) && (!knownIds || knownIds.has(id)))
  if (blocked) return 'blocked'
  if (!stageActive) return 'waiting'  // phase barrier: its stage hasn't opened yet
  return queued.has(t.id) ? 'queued' : 'ready'
}

export const STATE_ICON: Record<ExecState, typeof Circle> = {
  // `ready` (the actionable state — queueable right now in one-by-one mode) gets a
  // distinct play-ready glyph so it stands out from the inert/blocked tasks, which
  // shared the bare Circle + the same muted color — making the one row inviting action
  // the LEAST distinguishable. blocked/waiting stay bare circles + muted.
  done: CheckCircle2, cancelled: XCircle, running: Loader2, queued: Clock, blocked: Circle, ready: CirclePlay, waiting: Clock, undone: Circle,
}

export const STATE_COLOR: Record<ExecState, string> = {
  done: 'var(--color-ok)', cancelled: 'var(--color-on-surface-low)', running: 'var(--color-primary)',
  // ready leans on the on-surface-var (clearer than the muted -low) so it reads as
  // available-to-act, not dormant like blocked/waiting.
  queued: 'var(--color-primary)', blocked: 'var(--color-on-surface-low)', ready: 'var(--color-on-surface-var)',
  waiting: 'var(--color-on-surface-low)', undone: 'var(--color-on-surface-low)',
}

// Human state word for the accessible name. done/ready carry no text badge (icon /
// strikethrough only), so without this a screen reader announces a completed and a
// ready task identically — just the title. The aria-label appends this so each row's
// state is spoken.
export const STATE_LABEL: Record<ExecState, string> = {
  done: 'done', cancelled: 'cancelled', running: 'running', queued: 'queued', blocked: 'blocked', ready: 'ready to queue', waiting: 'waiting for its stage',
  undone: 'not done',
}
