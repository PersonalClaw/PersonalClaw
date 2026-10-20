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
 *  🔴 Stop is terminal, so it says what becomes of the work. It ends the run the way every ending
 *  does (`manager.end_run`): a task's work already merged is in the workspace (an Unattended loop
 *  merges each task as it finishes, an Attended one once you approve its merge). Work not merged
 *  yet, a task still at work or finished work waiting for your merge, keeps its worktree and branch
 *  (`worktree.sweep_finished` removes only those holding nothing unmerged), and the project's page
 *  lists it to review and merge or discard (`code/KeptWork`). A loop still planning has no workers
 *  and no worktrees: what stops is its planner. */
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
    body: "Stopping ends the project for good — it can't be resumed afterward (you'd start a new one). Work already merged into your workspace stays there, and a task's work that isn't merged yet is kept on its own branch, a task still running and finished work waiting for you to merge it alike. The project's page then lists it for you to review and merge or discard. Pause instead if you just want to step in.",
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
