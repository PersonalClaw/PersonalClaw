import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type CodeProject, type CodeFinding, type TaskItem } from '../../../lib/api'
import { isTerminalTask } from './tasks/taskState'
import { stageKey } from './cockpitShared'
import type { RouteProps } from '../../../app/useQueryState'

// ── The cockpit Tasks panel's controller ──────────────────────────────────────────────────────
//
// What the Tasks panel keeps: which task is open, closed again for the toast's Respond or when the
// task is gone; every stage's tasks, re-read each time the cockpit asks, with only the newest read
// applied and a failed list kept as it was; and each finding attributed to its task, or to its
// stage's running or last task when it names only a stage. `RightPanel.tsx` draws it.

/** Strip a model-authored stage decoration — '1 — ', 'Stage 2/2 — ', '3: ' — down to
 *  the bare title, case-folded (issue 642). Mirrors the ingest-side canonicalizer in
 *  loop/files.py (_STAGE_DECOR_RE): the ingest stamps NEW findings with the plan's
 *  canonical stage; this normalize keeps already-ledgered legacy labels attributable. */
export function normalizeStageLabel(s: string): string {
  return s.replace(/^\s*(stage\s*)?\d+(\s*\/\s*\d+)?\s*[—:\-–]\s*/i, '').trim().toLowerCase()
}

/** A task id the address may name: an id's characters, as the link parser holds a `task` to. */
const TASK_ID = /^[A-Za-z0-9._:-]{1,128}$/

export function useRightPanel({ project, tasksNonce, answerWanted, query, setQuery }: {
  project: CodeProject; tasksNonce: number; answerWanted?: boolean
} & Partial<Pick<RouteProps, 'query' | 'setQuery'>>) {
  // Navigable Tasks panel: a list view (all tasks, grouped by stage) and a per-task
  // DETAIL view (task plan + its agent loop events + a task-scoped steer box). The
  // selected task makes "what is the user steering" unambiguous for both sides.
  // The open task is in the cockpit's address (`task=<id>`): opening or closing one is a push, so
  // Back closes it, and a reload, a copied address or a link opens it again. Opening it only shows
  // it; its steer box starts empty. Without an address (a caller that passes none) it is kept here.
  const [localTaskId, setLocalTaskId] = useState<string | null>(null)
  const named = query?.task ?? ''
  const selectedTaskId = setQuery ? (TASK_ID.test(named) ? named : null) : localTaskId
  const setQueryRef = useRef(setQuery); setQueryRef.current = setQuery
  const selectTask = useCallback((id: string | null, opts?: { replace?: boolean }) => {
    const write = setQueryRef.current
    if (write) write({ task: id || null }, { replace: !!opts?.replace })
    else setLocalTaskId(id)
  }, [])
  const setSelectedTaskId = useCallback((id: string | null) => selectTask(id), [selectTask])
  /** Drops the task from the address as a correction, not a step Back would undo. */
  const dropTask = useCallback(() => selectTask(null, { replace: true }), [selectTask])
  // An id outside an id's shape opens nothing, and is dropped from the address.
  useEffect(() => {
    if (setQueryRef.current && named && !TASK_ID.test(named)) dropTask()
  }, [named, dropTask])
  // The toast's Respond is a project-level answer, and the project footer that takes it is
  // unmounted while a TASK is open (the detail view replaces it), so a Respond then closes the
  // task. The footer mounts with the request still standing and takes the cursor itself.
  useEffect(() => {
    if (answerWanted && selectedTaskId) dropTask()
  }, [answerWanted]) // eslint-disable-line react-hooks/exhaustive-deps
  const links = project.task_list_ids ?? {}
  const [tasksByList, setTasksByList] = useState<Record<string, TaskItem[]>>({})
  const [loading, setLoading] = useState(true)
  // A task-list fetch that FAILED this refresh — so a transient error doesn't render
  // the stage as a (false) empty "no tasks" list, hiding real work in the core
  // execute-one-by-one surface. Tracked separately so successful stages still show.
  const [loadFailed, setLoadFailed] = useState(false)
  // Sequence guard: refresh fires on EVERY tasksNonce bump (each SSE lifecycle event +
  // the 8s poll), so during a busy run multiple fetches overlap. Without ordering, a
  // slow earlier fetch resolving AFTER a faster later one would clobber the rail with
  // stale tasks. Stamp each run + only the latest one's result is allowed to apply.
  const refreshSeq = useRef(0)
  const refresh = useCallback(() => {
    const lists = Object.values(links)
    if (!lists.length) { setLoading(false); return }
    const seq = ++refreshSeq.current
    let anyFailed = false
    Promise.all(lists.map((lid) => api.allTasks({ task_list: lid }).then((r) => [lid, r.tasks] as const)
      .catch(() => { anyFailed = true; return [lid, null] as const })))
      // A failed list yields null (NOT []): keep its PRIOR tasks shown (merge over the
      // previous map) rather than blanking the stage to a misleading "no tasks". Only
      // overwrite a list whose fetch succeeded.
      .then((pairs) => {
        if (seq !== refreshSeq.current) return  // a newer refresh superseded this one
        setTasksByList((prev) => {
          const next = { ...prev }
          for (const [lid, tasks] of pairs) if (tasks !== null) next[lid] = tasks
          return next
        })
        setLoadFailed(anyFailed)
      }).finally(() => { if (seq === refreshSeq.current) setLoading(false) })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(links)])
  useEffect(() => { refresh() }, [refresh, tasksNonce])

  const allTasks: TaskItem[] = Object.values(tasksByList).flat()
  const selected = selectedTaskId ? allTasks.find((t) => t.id === selectedTaskId) : null
  // Self-heal a dangling selection: if a task was open in the detail view and it's
  // since gone (deleted, or its list dropped) AFTER tasks have loaded, clear the id
  // so a refetch race can't snap a stale task back into focus. Only once loaded +
  // non-empty, so the normal "still fetching" window doesn't wrongly clear it.
  const selectionStale = !!selectedTaskId && !loading && allTasks.length > 0 && !selected
  useEffect(() => { if (selectionStale) dropTask() }, [selectionStale, dropTask])
  // findings per task (task_id, or sequential findings attributed to their stage's
  // active task) — the agent loop events shown in TASK SCOPE.
  const findingsByTask: Record<string, CodeFinding[]> = {}
  const stages = project.stage_plan ?? []
  const tasksByStage: Record<string, TaskItem[]> = {}
  for (const s of stages) tasksByStage[stageKey(s)] = (links[stageKey(s)] && tasksByList[links[stageKey(s)]]) || []
  // A sequential-mode cycle finding has no task_id; its `stage` field may carry the
  // stage id ("implementation"), the stage title, OR a model-decorated label like
  // '1 — Write bell_times.py' / 'Stage 2/2 — Verify & QA' (issue 642: the field is
  // unconstrained model output, and exact equality attributed ZERO of a 12-cycle
  // loop's findings). The ingest now canonicalizes new findings server-side; the
  // normalized fallback here keeps every already-ledgered legacy label attributable.
  const stageTasksFor = (fstage: string): TaskItem[] => {
    const s = stages.find((sg) => sg.stage === fstage || sg.title === fstage)
      ?? stages.find((sg) => normalizeStageLabel(sg.title) === normalizeStageLabel(fstage)
        || normalizeStageLabel(sg.stage ?? '') === normalizeStageLabel(fstage))
    return s ? (tasksByStage[stageKey(s)] || []) : []
  }
  for (const f of (project.findings ?? [])) {
    let tid = f.task_id
    if (!tid && f.stage) {
      const st = stageTasksFor(f.stage)
      // The active task if one is running; else the LAST task of the stage, so a
      // COMPLETED stage's cycle findings still surface under a task (sequential mode
      // attributes nothing, and every task is done → no in-progress to borrow).
      tid = (st.find((t) => t.status === 'in_progress')
        || st.find((t) => !isTerminalTask(t))
        || st[st.length - 1])?.id
    }
    if (tid) (findingsByTask[tid] ??= []).push(f)
  }
  return {
    setSelectedTaskId, tasksByList, loading, loadFailed, refresh, allTasks, selected, findingsByTask, stages,
    tasksByStage,
  }
}
