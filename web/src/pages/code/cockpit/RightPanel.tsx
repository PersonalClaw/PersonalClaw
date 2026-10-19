import { AlertTriangle, RotateCcw } from 'lucide-react'
import { Button } from '../../../ui/Button'
import { type CodeProject } from '../../../lib/api'
import { type StallInfo } from '../../loops/runFold'
import { isTerminalTask } from './tasks/taskState'
import { TaskDetailView } from './tasks/TaskDetailView'
import { StageTasks } from './tasks/StageTasks'
import { stageKey, type ActivityItem } from './cockpitShared'
import { ProjectFooter } from './footer/ProjectFooter'
import { useRightPanel } from './useRightPanel'

// ── The cockpit's Tasks panel ──────────────────────────────────────────────────────────────────
//
// The right of the code cockpit: the task list with the footer beneath it, or one task's detail
// with its findings, live activity and blockers, and a notice with Retry when some tasks could not
// be re-read. `useRightPanel` keeps what it knows.

export function RightPanel({ project, onTasksChanged, tasksNonce, activityBySession, gateFail, stalled, onNudged, onStartNew, onAnswerOnScreen, answerWanted, onAnswerShown }: {
  project: CodeProject; onTasksChanged: () => void; tasksNonce: number
  activityBySession: Record<string, ActivityItem[]>; gateFail: { label: string; command: string; output: string } | null
  stalled: StallInfo | null; onNudged: () => void; onStartNew?: () => void
  /** Whether the answer panel (the project footer) is on screen, for the cockpit's toast. */
  onAnswerOnScreen?: (onScreen: boolean) => void
  /** The toast's Respond asked for the answer panel; `onAnswerShown` once its steer box has the cursor. */
  answerWanted?: boolean; onAnswerShown?: () => void
}) {
  const {
    setSelectedTaskId, tasksByList, loading, loadFailed, refresh, allTasks, selected, findingsByTask, stages,
    tasksByStage,
  } = useRightPanel({ project, tasksNonce, answerWanted })
  if (selected) {
    const doneIds = new Set(allTasks.filter(isTerminalTask).map((t) => t.id))
    // This task's live events: its own task-worker session (parallel) if present;
    // else, only if THIS task is the one in-progress, the main worker's stream
    // (sequential mode runs the active task in loop-<id>). A non-running task gets
    // no borrowed activity.
    const taskSess = `loop-${project.id}-${selected.id}`
    // A TERMINAL task has no live activity. Its per-task bucket can LINGER though: it's
    // only cleared on a clean chat_done, so a worker torn down by an orphan-reap /
    // watchdog kill / conflict-pause leaves a populated bucket — which would render as
    // stale "live" events on the now-done task's detail view. Gate on terminal (same
    // reasoning as the list-row pulse's doneForPulse guard).
    const live = isTerminalTask(selected)
      ? []
      : activityBySession[taskSess]
        ?? (selected.status === 'in_progress' ? activityBySession[`loop-${project.id}`] : undefined)
        ?? []
    // Which stage owns this task, and is that stage open (active/done) for exec? A
    // task in a not-yet-reached stage is held by the phase barrier → 'waiting'.
    const ownerStage = stages.find((s) => (tasksByStage[stageKey(s)] || []).some((t) => t.id === selected.id))
    const ss = ownerStage ? (project.stage_status?.[stageKey(ownerStage)] ?? 'pending') : 'active'
    const stageOpen = ss === 'active' || ss === 'done'
    // The specific NOT-yet-resolved blockers for this task (its dependency edges whose
    // target isn't terminal), as {id,title} — so the detail view can NAME what it's
    // waiting on + let the user click through, instead of a generic "its dependency
    // tasks". Resolve titles against the full task set; an edge to an unknown id (cross-
    // stage / pruned) is dropped (it's not a blocker we can act on here).
    const byId = new Map(allTasks.map((t) => [t.id, t]))
    const blockers = (selected.dependencies ?? [])
      .map((d) => d.depends_on_task_id)
      .filter((bid): bid is string => !!bid && byId.has(bid) && !doneIds.has(bid))
      .map((bid) => ({ id: bid, title: byId.get(bid)!.title }))
    // key by task id so a task→task switch (e.g. clicking a blocker chip, which calls
    // onOpenTask WITHOUT unmounting) REMOUNTS with fresh local state — else the steer
    // draft + last-steer echo (component-local) bleed across tasks, and a half-typed
    // steer for task A could be sent scoped to task B. Mirrors CenterEditor's
    // `FileViewer key={active.path}` for the same reason.
    return <TaskDetailView key={selected.id} project={project} task={selected} doneIds={doneIds} stageOpen={stageOpen}
      knownIds={new Set(byId.keys())}
      findings={findingsByTask[selected.id] ?? []} liveActivity={live} blockers={blockers} onOpenTask={setSelectedTaskId}
      onBack={() => setSelectedTaskId(null)} onChanged={onTasksChanged} />
  }
  // Task ids whose own worker session has live events right now → a list-row pulse
  // so the user sees WHICH parallel tasks are actively emitting (not just statically
  // 'running'). Keyed off the per-session activity buckets.
  const activeTaskIds = new Set<string>()
  const pfx = `loop-${project.id}-`
  // A DONE task must never show the "active now" pulse: a task-worker reaped without
  // a clean chat_done (orphan reap, watchdog kill, conflict pause) leaves its activity
  // bucket populated, so a bare items.length check would pulse a finished/merged task
  // forever. Gate on the task not being done.
  const doneForPulse = new Set(
    allTasks.filter(isTerminalTask).map((t) => t.id),
  )
  for (const [sess, items] of Object.entries(activityBySession)) {
    if (!sess.startsWith(pfx) || !items.length) continue
    const tid = sess.slice(pfx.length)
    if (!doneForPulse.has(tid)) activeTaskIds.add(tid)
  }
  return (
    <div className="flex h-full flex-col">
      {/* A task-list fetch failed this refresh — say so (with a retry) instead of
          rendering the affected stage as a false-empty "no tasks". Any stages that DID
          load stay shown below; this is a non-blocking notice, not a wipe. */}
      {loadFailed && (
        <div role="alert" data-type="caption" className="mx-2 mt-2 flex items-center gap-2 rounded-md px-2.5 py-1.5"
          style={{ background: 'color-mix(in srgb, var(--color-warn) 12%, transparent)', color: 'var(--color-warn)' }}>
          <AlertTriangle size={12} className="shrink-0" />
          <span className="min-w-0 flex-1">Couldn't refresh some tasks — showing the last known list.</span>
          {/* The raw 0.75rem size stays: Button carries its own data-type role and takes no override — no call-site element here to carry caption (see typeScaleRatchet). */}
          <Button variant="ghost" size="xs" onClick={refresh} className="shrink-0 gap-1 px-1.5 text-[0.75rem] text-warn hover:bg-warn/15"><RotateCcw size={11} /> Retry</Button>
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <StageTasks project={project} onTasksChanged={onTasksChanged} loading={loading}
          tasksByList={tasksByList} onSelect={setSelectedTaskId} activeTaskIds={activeTaskIds}
          mainActivity={activityBySession[`loop-${project.id}`] ?? []} />
      </div>
      {/* Project-level signals + steer box (list view): not task-scoped. */}
      <ProjectFooter project={project} gateFail={gateFail} stalled={stalled} onNudged={onNudged} onStartNew={onStartNew}
        onAnswerOnScreen={onAnswerOnScreen} answerWanted={answerWanted} onAnswerShown={onAnswerShown} />
    </div>
  )
}
