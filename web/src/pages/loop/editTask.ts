import { api, type Loop } from '../../lib/api'
import { reportingWrite } from '../../app/reportingWrite'

/** The composer address that brings back what was typed for *loop*: its kind, task, project,
 *  Mode, and (for a Code loop on an existing codebase) the codebase — as the project's codebase
 *  (`ws`) when it has a project, else as the path typed for it (`codebase`). `LoopSection` reads
 *  each param back into `LoopComposer`. A fresh-start Code loop's workspace is one the loop made
 *  for itself, so it is not offered back as an existing codebase. */
export function composerRouteFor(loop: Loop): string {
  const q = new URLSearchParams({ kind: loop.kind, task: loop.task })
  if (loop.project_id) q.set('project', loop.project_id)
  if (loop.attended) q.set('mode', 'attended')
  const brownfield = (loop.kind_config || {}).project_kind === 'brownfield'
  if (loop.kind === 'code' && brownfield && loop.workspace_dir) {
    q.set(loop.project_id ? 'ws' : 'codebase', loop.workspace_dir)
  }
  return `loop?${q.toString()}`
}

/** "Cancel and edit the task" on a planning walkthrough: the draft goes — which stops its
 *  planner — and the composer opens with what was typed for it. Resolves to the address to go
 *  to, or null when the cancel did not go through (the failure is reported; the walkthrough
 *  stays, since leaving would hide a planner that is still drafting). */
export async function cancelPlanningToEdit(id: string): Promise<string | null> {
  let loop: Loop | null = null
  try { loop = await api.uLoop(id) } catch (e) {
    // Already gone: nothing is planning, and nothing is left to bring back.
    if ((e as { status?: number })?.status === 404) return 'loop'
  }
  if (!(await reportingWrite('cancel this plan', () => api.deleteULoop(id)))) return null
  return loop ? composerRouteFor(loop) : 'loop'
}
