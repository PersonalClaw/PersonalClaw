import { api } from '../../lib/api'
import { LOOP_ACTION_SOURCE_STATUSES } from '../../lib/loopStatus'
import { reportingWrite } from '../../app/reportingWrite'
import { confirm } from '../../ui/dialog'

/** Whether a loop in *status* can be stopped (the backend's `STOPPABLE_STATUSES`, mirrored). */
export function canStopLoop(status: string): boolean {
  return LOOP_ACTION_SOURCE_STATUSES.stop.has(status)
}

/** The one confirm every Stop control on a loop asks — the cockpit's, the Code list's, a project's
 *  Work board's and the planning walkthrough's — worded for where the loop is, and naming it: from a
 *  list of loops "this run" says nothing about which one.
 *
 *  🔴 Stop is terminal, and the half a user loses work to is the one to say. Its teardown runs
 *  `worktree.cleanup_all`, which for every task worktree does `git worktree remove --force` AND
 *  `git branch -D`, so uncommitted work in a running task's worktree is discarded and its
 *  committed-but-unmerged work goes with the branch. A FINISHED task is merged back
 *  (`worktree.merge_worktree`), so merged work is in the workspace and untouched. A loop still
 *  planning has no workers and no worktrees: what stops is its planner. */
export function confirmStopLoop(status: string, name: string): Promise<boolean> {
  const subject = name.trim() ? `\u201c${name.trim()}\u201d` : 'this loop'
  if (status === 'planning' || status === 'intake') {
    return confirm({
      title: `Stop planning ${subject}?`,
      body: "Stopping ends this loop for good: its planner stops, and the plan so far stays on its page. It can't be resumed afterward (you'd start a new one). To change the task and plan again, use Cancel and edit the task instead.",
      danger: true,
      confirmLabel: 'Stop',
    })
  }
  return confirm({
    title: `Stop ${subject}?`,
    body: "Stopping ends the project for good — it can't be resumed afterward (you'd start a new one). Work already merged into your workspace is kept, but a task still running loses its own worktree and branch. Pause instead if you just want to step in.",
    danger: true,
    confirmLabel: 'Stop',
  })
}

/** Stop loop *id* from a surface that shows it working: read where it is (so the confirm says the
 *  right thing, and a loop that ended meanwhile is not offered a stop), confirm, then stop.
 *  Resolves whether it stopped; a failure is reported by `reportingWrite`. */
export async function stopLoop(id: string): Promise<boolean> {
  let status = ''
  let name = ''
  try { ({ status, name } = await api.uLoop(id)) } catch { status = '' }
  if (status && !canStopLoop(status)) return false
  if (!(await confirmStopLoop(status, name || ''))) return false
  return reportingWrite('stop this loop', () => api.uLoopAction(id, 'stop'))
}
