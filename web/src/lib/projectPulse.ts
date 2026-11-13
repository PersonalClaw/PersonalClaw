import type { ProjectItem, RunningRow, WaitingItem, WaitingKind } from './api'
import { epochSeconds } from './epoch'
import type { Thread } from './useThreads'

/** ── A project's pulse, and the order every list of projects takes ─────────────────────────────
 *
 *  One pure function, read by the Projects list, the palette and a project's page, so the three say
 *  the same counts. It reads what Home reads (`GET /api/waiting`, `GET /api/running`, the thread
 *  read), filtered to one project by the `project_id` the gateway decided once for each piece of
 *  work, so a project's counts and Home's agree:
 *
 *  · **Needs you** is every waiting item with this project's `project_id`: a loop's question, a plan
 *    waiting for launch and a run at a gate each count once, as Home counts them.
 *  · **Running** is every running row with this `project_id` that waits on nothing (`waiting` empty)
 *    and whose work has no item in this project's Needs you: a piece of work is counted once, where
 *    it most needs the owner.
 *  · **Last active** is the newest of its threads' activity, its running rows' last change and the
 *    project's own `updated_at`.
 *  · **Unreadable** is the waiting read's sources that could not be read. No project can be cleared
 *    of those, so a caller that shows the counts says so beside them.
 *
 *  A read that failed is the caller's to say: this function is given the lists it has, never an
 *  empty one in place of a failed one. */

export type LiveRow =
  | { kind: 'waiting'; item: WaitingItem }
  | { kind: 'running'; row: RunningRow }

export interface ProjectPulse {
  needsYou: WaitingItem[]
  running: RunningRow[]
  /** Epoch ms, 0 when nothing says. */
  lastActiveAt: number
  /** The waiting items, then the running rows, each in its own list's order. */
  live: LiveRow[]
  unreadable: WaitingKind[]
}

function ms(ts: string | null | undefined): number {
  const s = epochSeconds(ts)
  return s === undefined ? 0 : s * 1000
}

export function projectPulse(
  projectId: string,
  waiting: { waiting: WaitingItem[]; unreadable: WaitingKind[] } | undefined,
  running: { running: RunningRow[] } | undefined,
  threads: readonly Thread[],
  project: Pick<ProjectItem, 'updated_at'> | null | undefined,
): ProjectPulse {
  const needsYou = (waiting?.waiting ?? []).filter((w) => w.project_id === projectId)
  const asked = new Set(needsYou.map((w) => w.work).filter(Boolean))
  const live = (running?.running ?? []).filter((r) => r.project_id === projectId && r.waiting.length === 0 && !asked.has(r.work))
  let lastActiveAt = ms(project?.updated_at)
  for (const t of threads) if (t.projectId === projectId && t.activeAt > lastActiveAt) lastActiveAt = t.activeAt
  for (const r of live) lastActiveAt = Math.max(lastActiveAt, ms(r.since))
  return {
    needsYou,
    running: live,
    lastActiveAt,
    live: [...needsYou.map((item) => ({ kind: 'waiting' as const, item })), ...live.map((row) => ({ kind: 'running' as const, row }))],
    unreadable: waiting?.unreadable ?? [],
  }
}

/** The pulse in words, each count at 0 left out: "2 need you", "1 needs you", "2 running". */
export function pulseWords(pulse: Pick<ProjectPulse, 'needsYou' | 'running'>): string[] {
  const words: string[] = []
  const n = pulse.needsYou.length
  if (n) words.push(`${n} ${n === 1 ? 'needs' : 'need'} you`)
  if (pulse.running.length) words.push(`${pulse.running.length} running`)
  return words
}

/** Projects in the order every list of them takes: the pinned ones first, in the pins' own order,
 *  then the newest activity first, ties by name. *lastActiveAt* reads each project's pulse; a
 *  project it does not know reads 0. */
export function projectOrder<P extends Pick<ProjectItem, 'id' | 'name'>>(
  projects: readonly P[],
  pins: readonly string[],
  lastActiveAt: (id: string) => number,
): P[] {
  const pinAt = new Map(pins.map((id, i) => [id, i]))
  return [...projects].sort((a, b) => {
    const pa = pinAt.get(a.id), pb = pinAt.get(b.id)
    if (pa !== undefined || pb !== undefined) return (pa ?? Number.MAX_SAFE_INTEGER) - (pb ?? Number.MAX_SAFE_INTEGER)
    return lastActiveAt(b.id) - lastActiveAt(a.id) || a.name.localeCompare(b.name)
  })
}
