import { useEffect, useRef, useState } from 'react'
import { api, type CodeProject, type CodeFinding, type TaskItem } from '../../../../lib/api'
import { isTerminalTask, execState } from './taskState'
import { TERMINAL_STATUSES, autoGrowTextarea, type ActivityItem } from '../cockpitShared'

// ── The cockpit task detail's controller ──────────────────────────────────────────────────────
//
// What one task's detail view knows and does: the task's state in the run (running, queued, done,
// or left undone by a finished project), the latest findings shown first with the earlier ones
// behind a toggle, the activity column kept at its newest line while the reader is at the bottom,
// and queueing, un-queueing and steering this one task, each guarded against a double send and
// reported through the cockpit toast when it fails. `TaskDetailView.tsx` draws it.

export function useTaskDetail({ project, task, doneIds, stageOpen, knownIds, findings, liveActivity, onChanged }: {
  project: CodeProject; task: TaskItem; doneIds: Set<string>; stageOpen: boolean; knownIds: Set<string>; findings: CodeFinding[]; liveActivity: ActivityItem[]
  onChanged: () => void
}) {
  // The project finished or was stopped: nothing of it runs again, so its task has no queue to
  // join and no stage to wait for.
  const ended = TERMINAL_STATUSES.has(project.status)
  const state = execState(task, doneIds, new Set(project.queued_task_ids ?? []), stageOpen, knownIds, ended)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  // A long-running task accumulates many cycle findings; rendering every FindingCard
  // (summary + insight + files + collapsible evidence) balloons the activity column.
  // Show the most RECENT N by default with a "show earlier" toggle — the latest cycles
  // are what the user wants, and it bounds the DOM. Mirrors the FilesTouched cap pattern.
  const [showAllFindings, setShowAllFindings] = useState(false)
  // Synchronous guard for queue/steer: disabled={busy} + the `busy` state check are
  // async, so a rapid double Enter/click both pass before the re-render → a double
  // nudge/queue (double guidance write + SSE). The ref short-circuits the 2nd call
  // immediately (the acting-guard pattern).
  const actingRef = useRef(false)
  // Auto-grow the task-scoped steer box to fit multi-line input (mirrors the
  // project-level steer box).
  const steerRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => { autoGrowTextarea(steerRef.current) }, [text])
  // Optimistic echo of the latest task-scoped steer so the user sees it landed —
  // mirrors the project-level steer box (which showed an echo while the task one
  // gave no confirmation at all). Local-only: task nudges aren't in project.nudges.
  const [lastSteer, setLastSteer] = useState<{ text: string; failed?: boolean } | null>(null)
  const queued = !ended && (project.queued_task_ids ?? []).includes(task.id)
  const autopilot = project.autopilot !== false
  const done = task.status === 'done' || task.status === 'completed'
  // Cancelled is ALSO terminal (isTerminalTask) — its worker is gone, so the
  // scheduling note + the task-scoped steer box must treat it like a finished task,
  // not fall through to "Queued by autopilot." / a no-op steer box (guidance nothing
  // reads). `done` alone excluded cancelled; gate the terminal surfaces on this.
  const terminal = isTerminalTask(task)
  const running = !ended && task.status === 'in_progress'
  const live = running ? liveActivity : []
  const plan = task.action_plan ?? []
  const crit = task.exit_criteria ?? []
  const ws = project.workspace_dir || project.files_dir || ''

  // Auto-scroll the activity column to the newest finding / live line as the worker
  // streams — but only when the user is already near the bottom, so scrolling up to
  // read earlier cycles isn't yanked back down.
  const scrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120
    if (nearBottom) el.scrollTop = el.scrollHeight
  }, [findings.length, live.length])

  async function queue(action: 'queue' | 'unqueue') {
    if (busy || actingRef.current) return
    actingRef.current = true
    setBusy(true)
    try { await api.uLoopQueue(project.id, [task.id], action); onChanged() }
    catch (e) { window.dispatchEvent(new CustomEvent('ne:code-toast', { detail: { kind: 'error', text: `Couldn't ${action === 'queue' ? 'queue' : 'unqueue'} this task: ${(e as Error).message || 'unknown error'}` } })) }
    finally { setBusy(false); actingRef.current = false }
  }
  // Steering scoped to THIS task — prefix the nudge so the worker knows which task
  // the direction applies to (queued → applied when it runs; running → next cycle).
  // `explicit` lets a one-click action (e.g. the needs-input "Use your best judgment"
  // button) steer without using the textarea; otherwise the typed `text` is sent.
  async function steer(explicit?: string) {
    const t = (explicit ?? text).trim()
    if (!t || busy || actingRef.current) return
    actingRef.current = true
    setBusy(true)
    // Scope the steer to THIS task (task_id → guidance_<task_id>.txt for its
    // worker); keep the title prefix so the text is self-describing in the trail.
    // Clear the box only on SUCCESS — a pre-await clear lost the user's text with no
    // error if the send failed (project went terminal mid-send / network / 5xx).
    try {
      await api.uLoopNudge(project.id, `[For task "${task.title}"] ${t}`, task.id)
      if (explicit === undefined) setText('')
      setLastSteer({ text: t }); onChanged()
    } catch (e) {
      setLastSteer({ text: t, failed: true })
      window.dispatchEvent(new CustomEvent('ne:code-toast', { detail: { kind: 'error', text: `Couldn't send that steer: ${(e as Error).message || 'unknown error'}` } }))
    } finally { setBusy(false); actingRef.current = false }
  }
  return {
    ended, state, text, setText, busy, showAllFindings, setShowAllFindings, steerRef, lastSteer, queued,
    autopilot, done, terminal, running, live, plan, crit, ws, scrollRef, queue, steer,
  }
}
