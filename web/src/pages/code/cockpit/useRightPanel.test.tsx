import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup, waitFor } from '@testing-library/react'
import type { CodeProject, TaskItem } from '../../../lib/api'

// ── The cockpit Tasks panel's controller ──────────────────────────────────────────────────────
//
// `useRightPanel` keeps which task is open, every stage's tasks (only the newest read applied, a
// failed list kept as it was), and each finding under the task it belongs to.

const h = vi.hoisted(() => ({ allTasks: vi.fn() }))
vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  return { ...real, api: { ...real.api, allTasks: h.allTasks } }
})

let useRightPanel: typeof import('./useRightPanel')['useRightPanel']
let normalizeStageLabel: typeof import('./useRightPanel')['normalizeStageLabel']
const task = (id: string, status = 'open'): TaskItem => ({ id, title: `Task ${id}`, status } as TaskItem)
const LISTS: Record<string, TaskItem[]> = { 'l-1': [task('t-1', 'done'), task('t-2', 'in_progress')], 'l-2': [task('t-3'), task('t-4')] }
const project = (over: Record<string, unknown> = {}) => ({
  id: 'loop-1', status: 'running', task_list_ids: { ideation: 'l-1', review: 'l-2' },
  stage_plan: [{ stage: 'ideation', title: 'Ideation' }, { stage: 'review', title: 'Review' }], findings: [], ...over,
} as unknown as CodeProject)
type Props = Parameters<typeof useRightPanel>[0]
const mount = (props: Partial<Props> = {}) => renderHook((p: Props) => useRightPanel(p), {
  initialProps: { project: project(), tasksNonce: 0, ...props } as Props,
})

beforeEach(async () => {
  h.allTasks.mockReset()
  h.allTasks.mockImplementation((q: { task_list: string }) => Promise.resolve({ tasks: LISTS[q.task_list] }))
  ;({ useRightPanel, normalizeStageLabel } = await import('./useRightPanel'))
})
afterEach(() => cleanup())

describe('useRightPanel', () => {
  it("reads every stage's tasks, and again when the cockpit asks", async () => {
    const { result, rerender } = mount()
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.allTasks.map((t) => t.id)).toEqual(['t-1', 't-2', 't-3', 't-4'])
    expect(result.current.tasksByStage.review.map((t) => t.id)).toEqual(['t-3', 't-4'])
    rerender({ project: project(), tasksNonce: 1 })
    await waitFor(() => expect(h.allTasks).toHaveBeenCalledTimes(4))
  })

  it('keeps a list that failed to read as it was, and says so', async () => {
    const { result, rerender } = mount()
    await waitFor(() => expect(result.current.allTasks).toHaveLength(4))
    h.allTasks.mockImplementation((q: { task_list: string }) => q.task_list === 'l-2' ? Promise.reject(new Error('down')) : Promise.resolve({ tasks: LISTS[q.task_list] }))
    rerender({ project: project(), tasksNonce: 1 })
    await waitFor(() => expect(result.current.loadFailed).toBe(true))
    expect(result.current.tasksByList['l-2'].map((t) => t.id)).toEqual(['t-3', 't-4'])
  })

  it('applies only the newest read', async () => {
    let late: (v: { tasks: TaskItem[] }) => void = () => {}
    h.allTasks.mockImplementationOnce(() => new Promise((r) => { late = r }))
    const { result, rerender } = mount({ project: project({ task_list_ids: { ideation: 'l-1' } }) })
    rerender({ project: project({ task_list_ids: { ideation: 'l-1' } }), tasksNonce: 1 })
    await waitFor(() => expect(result.current.allTasks.map((t) => t.id)).toEqual(['t-1', 't-2']))
    await act(async () => { late({ tasks: [task('stale')] }) })
    expect(result.current.allTasks.map((t) => t.id)).toEqual(['t-1', 't-2'])
  })

  it("closes an open task for the toast's Respond, and when the task is gone", async () => {
    const { result, rerender } = mount()
    await waitFor(() => expect(result.current.loading).toBe(false))
    act(() => result.current.setSelectedTaskId('t-3'))
    expect(result.current.selected?.id).toBe('t-3')
    rerender({ project: project(), tasksNonce: 0, answerWanted: true })
    expect(result.current.selected).toBeNull()
    rerender({ project: project(), tasksNonce: 0, answerWanted: false })
    act(() => result.current.setSelectedTaskId('t-gone'))
    await waitFor(() => expect(result.current.selected).toBeNull())
  })

  it("puts each finding under its task, or under its stage's running or last task", async () => {
    const findings = [
      { task_id: 't-3', summary: 'own task' },
      { stage: 'ideation', summary: 'stage id' },
      { stage: '2 — Review', summary: 'a decorated stage label' },
    ]
    const { result } = mount({ project: project({ findings }) })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.findingsByTask['t-2'].map((f) => f.summary)).toEqual(['stage id'])
    expect(result.current.findingsByTask['t-3'].map((f) => f.summary)).toEqual(['own task', 'a decorated stage label'])
    expect(normalizeStageLabel('Stage 2/2 — Verify & QA')).toBe('verify & qa')
  })
})
