import { useState } from 'react'
import { api, type CodeProject, type TaskItem } from '../../../../lib/api'
import { isTerminalTask } from './taskState'
import { TERMINAL_STATUSES } from '../cockpitShared'

// ── The cockpit task list's controller ────────────────────────────────────────────────────────
//
// What the Tasks rail's list knows about the run's tasks: which are done, queued, queueable or
// running, what a finished or stopped project leaves undone, and the two things it can do, queue
// tasks and switch between autopilot and one-by-one, each reported through the cockpit toast when
// it fails. `StageTasks.tsx` draws it.

export function useStageTasks({ project, onTasksChanged, tasksByList }: {
  project: CodeProject; onTasksChanged: () => void; tasksByList: Record<string, TaskItem[]>
}) {
  const links = project.task_list_ids ?? {}
  const stages = project.stage_plan ?? []
  const [busy, setBusy] = useState(false)
  const noLists = !Object.keys(links).length
  const isPreLaunch = project.status === 'ready' || project.status === 'review'
  const queuedSet = new Set(project.queued_task_ids ?? [])
  const allTasks: TaskItem[] = Object.values(tasksByList).flat()
  const doneIds = new Set(allTasks.filter(isTerminalTask).map((t) => t.id))
  // Every known task id — so execState can tell a real (still-pending) blocker from a
  // dependency edge to a pruned/cross-stage id that no longer exists (the latter isn't
  // a blocker we can show or act on; counting it left a task 'blocked' with no listed
  // blocker). Matches the TaskDetailView blocker-list's byId filter.
  const knownIds = new Set(allTasks.map((t) => t.id))
  // A project that finished or was stopped runs nothing again: none of its tasks is queued,
  // queueable or running, and its drive mode is not something left to switch.
  const ended = TERMINAL_STATUSES.has(project.status)
  const queueable = ended ? [] : allTasks.filter((t) => !doneIds.has(t.id) && !queuedSet.has(t.id))
  const runningCount = ended ? 0 : allTasks.filter((t) => t.status === 'in_progress').length
  const undoneCount = ended ? allTasks.filter((t) => !doneIds.has(t.id)).length : 0

  async function queue(ids: string[]) {
    if (!ids.length || busy) return
    setBusy(true)
    // Surface a failed queue (worker gone, backend error) via the cockpit toast —
    // a silent finally{} left the button un-busying with no sign nothing queued.
    try { await api.uLoopQueue(project.id, ids, 'queue'); onTasksChanged() }
    catch (e) { window.dispatchEvent(new CustomEvent('ne:code-toast', { detail: { kind: 'error', text: `Couldn't queue ${ids.length > 1 ? 'those tasks' : 'that task'}: ${(e as Error).message || 'unknown error'}` } })) }
    finally { setBusy(false) }
  }
  const autopilot = project.autopilot !== false
  async function toggleAutopilot() {
    if (busy) return
    setBusy(true)
    try { await api.uLoopAutopilot(project.id, !autopilot); onTasksChanged() }
    catch (e) { window.dispatchEvent(new CustomEvent('ne:code-toast', { detail: { kind: 'error', text: `Couldn't switch drive mode: ${(e as Error).message || 'unknown error'}` } })) }
    finally { setBusy(false) }
  }
  return {
    links, stages, busy, noLists, isPreLaunch, queuedSet, doneIds, knownIds, ended, queueable, runningCount,
    undoneCount, queue, autopilot, toggleAutopilot,
  }
}
