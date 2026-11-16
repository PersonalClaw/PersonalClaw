import type { Loop, RunningRow, TaskItem, TaskListItem, WaitingItem, WaitingKind, WorkBoard, WorkRow } from '../../lib/api'
import { epochSeconds } from '../../lib/epoch'
import { projectPulse } from '../../lib/projectPulse'
import type { Thread } from '../../lib/useThreads'
import { TERMINAL } from '../tasks/taskMeta'

/** ── A project's page: which group each piece of its work is in ─────────────────────────────────
 *
 *  Each piece of work sits in one group on the page, the first that fits:
 *
 *  1. **Needs you**: a waiting item of `GET /api/waiting` with this project's `project_id`, in the
 *     order served (by consequence, then age): Home's Needs you, filtered.
 *  2. **Running now**: a running row of `GET /api/running` with this `project_id`, whose `waiting` is
 *     empty and whose work has no item in this project's Needs you, in the list's order (oldest first,
 *     so a phase change never moves one). 1 and 2 are `projectPulse`'s, the rule the Projects list and
 *     the palette count by, so the page and the list say the same.
 *  3. **Up next**: from the project's board (`GET /api/projects/{id}/work`), a loop made but not
 *     started (`ready`, the board's `queued`), a paused loop (`suspended`), a queued run and a
 *     suspended one. A task is never here: an open task is the Tasks group's.
 *  4. **Tasks**: the project's open tasks, from its own lists, less the steps of the loops this page
 *     lists, by the rule `/work` follows: a task in a list whose `loop_id` names one of those loops,
 *     and a task one of those loops links (`linked_task_ids`). The loop's own row stands for its
 *     steps. While the thread read has failed, the page lists no loop, so no step is left out.
 *  5. **Results**: the project's artifacts, from its one library read (`useProjectLibrary`), as
 *     the library's cards; its knowledge items are About › Knowledge's.
 *  6. **Recent**: everything else of the project that is not live: its chats, goal loops and code
 *     sessions from the thread read, and its finished runs from the board. Pinned first, then the
 *     latest activity, ties by key.
 *
 *  Each function is given the lists it has; a read that failed is the page's to say, never an empty
 *  list in its place. */

export interface ProjectLive {
  needsYou: WaitingItem[]
  unreadable: WaitingKind[]
  running: RunningRow[]
}

/** Needs you and Running now, by the pulse's rule. */
export function projectLive(
  projectId: string,
  waiting: { waiting: WaitingItem[]; unreadable: WaitingKind[] } | undefined,
  running: { running: RunningRow[] } | undefined,
): ProjectLive {
  const pulse = projectPulse(projectId, waiting, running, [], null)
  return { needsYou: pulse.needsYou, unreadable: pulse.unreadable, running: pulse.running }
}

/** The work refs Needs you and Running now already show, so no other group draws them again. */
export function liveWork(live: Pick<ProjectLive, 'needsYou' | 'running'>): Set<string> {
  const refs = new Set<string>()
  for (const w of live.needsYou) if (w.work) refs.add(w.work)
  for (const r of live.running) refs.add(r.work)
  return refs
}

/** A board row's work ref, as `GET /api/waiting` and `GET /api/running` name the same work. */
export function boardWorkRef(row: Pick<WorkRow, 'source' | 'run_id'>): string {
  return `${row.source === 'run' ? 'workflow' : row.source}:${row.run_id}`
}

/** Up next: a loop or run made and waiting to start or go on, in the board's order, unless a live
 *  group already shows it. */
export function upNextRows(board: Pick<WorkBoard, 'board'> | undefined, live: ReadonlySet<string>): WorkRow[] {
  const rows: WorkRow[] = []
  for (const group of board?.board ?? []) {
    if (group.state !== 'queued' && group.state !== 'suspended') continue
    for (const row of group.rows) {
      if (row.source !== 'loop' && row.source !== 'run') continue
      if (live.has(boardWorkRef(row))) continue
      rows.push(row)
    }
  }
  return rows
}

/** What an Up next row says it is, and the action it offers: "Not started" with Start for a loop
 *  made but not started, "Paused" with Resume for a paused loop or a suspended run, and "Queued"
 *  with no action for a queued run. */
export function upNextWords(row: Pick<WorkRow, 'source' | 'state'>): { state: string; action: 'start' | 'resume' | null } {
  if (row.state === 'suspended') return { state: 'Paused', action: 'resume' }
  if (row.source === 'loop') return { state: 'Not started', action: 'start' }
  return { state: 'Queued', action: null }
}

/** The loops the page lists: the thread read's loops in this project, by the one rule. */
export function listedLoops(loops: readonly Loop[] | null, projectId: string, inProject: (loop: Loop) => string): Loop[] | null {
  if (loops === null) return null
  return loops.filter((l) => inProject(l) === projectId)
}

export interface ProjectTasks {
  /** The open tasks the group lists, in the Tasks page's default order (the latest change first). */
  open: TaskItem[]
  /** Whether the project has any task at all, open or done, after its loops' steps are left out:
   *  with none the group is not drawn, and with only done ones it says "No open tasks." */
  any: boolean
}

const updated = (t: TaskItem) => Date.parse(t.updated_at || t.created_at || '') || 0

/** The project's own tasks: those in its lists, less the steps of `loops` (`null` while the thread
 *  read has failed: then no step is left out, as `/work` keeps a step whose loop it could not list). */
export function projectTasks(tasks: readonly TaskItem[], lists: readonly TaskListItem[], loops: readonly Loop[] | null): ProjectTasks {
  const loopIds = new Set((loops ?? []).map((l) => l.id))
  const steps = new Set((loops ?? []).flatMap((l) => l.linked_task_ids ?? []))
  const own = new Set(lists.filter((l) => !(l.loop_id && loopIds.has(l.loop_id))).map((l) => l.id))
  const mine = tasks.filter((t) => t.task_list_id && own.has(t.task_list_id) && !steps.has(t.id))
  const open = mine.filter((t) => !TERMINAL.has(t.status)).sort((a, b) => updated(b) - updated(a))
  return { open, any: mine.length > 0 }
}

/** The task `?open=task:<id>` names, as this project's page may open it: one of the project's own
 *  (in one of its lists, a loop's step among them), one of another project's, which this page
 *  never opens (its Edit and Delete would act on that project's task under this one's header),
 *  or none at all. */
export function projectTaskOf(tasks: readonly TaskItem[], lists: readonly TaskListItem[], id: string):
  { task: TaskItem; where: 'here' } | { task: null; where: 'elsewhere' | 'gone' } {
  const t = tasks.find((x) => x.id === id)
  if (!t) return { task: null, where: 'gone' }
  const own = new Set(lists.map((l) => l.id))
  return t.task_list_id && own.has(t.task_list_id) ? { task: t, where: 'here' } : { task: null, where: 'elsewhere' }
}

export type RecentRow =
  | { kind: 'thread'; key: string; thread: Thread; at: number; pinned: boolean }
  | { kind: 'run'; key: string; row: WorkRow; at: number; pinned: false }

function ms(ts: string | null | undefined): number {
  const s = epochSeconds(ts)
  return s === undefined ? 0 : s * 1000
}

/** Recent: the project's chats, goal loops and code sessions that no other group shows, and its
 *  finished runs. Pinned first, then the latest activity, ties by key. */
export function recentRows(
  projectId: string,
  threads: readonly Thread[],
  board: Pick<WorkBoard, 'board'> | undefined,
  shown: ReadonlySet<string>,
): RecentRow[] {
  const rows: RecentRow[] = []
  for (const t of threads) {
    if (t.projectId !== projectId || t.archived || shown.has(t.key)) continue
    rows.push({ kind: 'thread', key: t.key, thread: t, at: t.activeAt, pinned: t.pinned })
  }
  for (const group of board?.board ?? []) {
    if (group.state !== 'done') continue
    for (const row of group.rows) {
      if (row.source !== 'run') continue
      const key = boardWorkRef(row)
      if (shown.has(key)) continue
      rows.push({ kind: 'run', key, row, at: ms(row.updated_at), pinned: false })
    }
  }
  return rows.sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.at - a.at || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0))
}

/** Up next's rows as the work refs Recent leaves out. */
export function upNextWork(rows: readonly WorkRow[]): Set<string> {
  return new Set(rows.map(boardWorkRef))
}
