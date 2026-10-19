import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup } from '@testing-library/react'
import type { CodeProject, TaskItem } from '../../../../lib/api'

// ── The cockpit task list's controller ────────────────────────────────────────────────────────
//
// `useStageTasks` keeps what the Tasks rail's list knows about the run's tasks (done, queued,
// queueable, running, left undone) and queues tasks or switches the drive mode, saying so when
// either fails.

const h = vi.hoisted(() => ({ uLoopQueue: vi.fn(), uLoopAutopilot: vi.fn() }))
vi.mock('../../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../../lib/api')>()
  return { ...real, api: { ...real.api, uLoopQueue: h.uLoopQueue, uLoopAutopilot: h.uLoopAutopilot } }
})

let useStageTasks: typeof import('./useStageTasks')['useStageTasks']
const task = (id: string, status: string): TaskItem => ({ id, title: `Task ${id}`, status } as TaskItem)
const TASKS = { 'l-1': [task('t-1', 'done'), task('t-2', 'in_progress')], 'l-2': [task('t-3', 'open'), task('t-4', 'open'), task('t-5', 'cancelled')] }
const project = (over: Partial<CodeProject> = {}) => ({
  id: 'loop-1', status: 'running', task_list_ids: { build: 'l-1', test: 'l-2' }, stage_plan: [], queued_task_ids: ['t-3'], ...over,
} as unknown as CodeProject)
const toasts: string[] = []
const onToast = (e: Event) => toasts.push((e as CustomEvent).detail.text)

beforeEach(async () => {
  for (const f of Object.values(h)) f.mockReset()
  h.uLoopQueue.mockResolvedValue({})
  h.uLoopAutopilot.mockResolvedValue({})
  toasts.length = 0
  window.addEventListener('ne:code-toast', onToast)
  ;({ useStageTasks } = await import('./useStageTasks'))
})
afterEach(() => { cleanup(); window.removeEventListener('ne:code-toast', onToast) })

describe('useStageTasks', () => {
  it('knows which tasks are done, queued, queueable and running', () => {
    const { result } = renderHook(() => useStageTasks({ project: project(), onTasksChanged: () => {}, tasksByList: TASKS }))
    expect([...result.current.doneIds].sort()).toEqual(['t-1', 't-5'])
    expect([...result.current.queuedSet]).toEqual(['t-3'])
    expect(result.current.queueable.map((t) => t.id)).toEqual(['t-2', 't-4'])
    expect(result.current.runningCount).toBe(1)
    expect(result.current.undoneCount).toBe(0)
    expect(result.current.ended).toBe(false)
    expect(result.current.autopilot).toBe(true)
  })

  it('a stopped project queues and runs nothing, and counts what it left undone', () => {
    const { result } = renderHook(() => useStageTasks({ project: project({ status: 'stopped' }), onTasksChanged: () => {}, tasksByList: TASKS }))
    expect(result.current.ended).toBe(true)
    expect(result.current.queueable).toEqual([])
    expect(result.current.runningCount).toBe(0)
    expect(result.current.undoneCount).toBe(3)
  })

  it('queues tasks and refreshes, or says it could not', async () => {
    const onTasksChanged = vi.fn()
    const { result } = renderHook(() => useStageTasks({ project: project(), onTasksChanged, tasksByList: TASKS }))
    await act(() => result.current.queue(['t-4']))
    expect(h.uLoopQueue).toHaveBeenCalledWith('loop-1', ['t-4'], 'queue')
    expect(onTasksChanged).toHaveBeenCalledTimes(1)
    h.uLoopQueue.mockRejectedValueOnce(new Error('the worker is gone'))
    await act(() => result.current.queue(['t-2', 't-4']))
    expect(toasts).toEqual(["Couldn't queue those tasks: the worker is gone"])
    expect(result.current.busy).toBe(false)
  })

  it('switches between autopilot and one-by-one, or says it could not', async () => {
    const { result } = renderHook(() => useStageTasks({ project: project({ autopilot: false }), onTasksChanged: () => {}, tasksByList: TASKS }))
    expect(result.current.autopilot).toBe(false)
    await act(() => result.current.toggleAutopilot())
    expect(h.uLoopAutopilot).toHaveBeenCalledWith('loop-1', true)
    h.uLoopAutopilot.mockRejectedValueOnce(new Error('offline'))
    await act(() => result.current.toggleAutopilot())
    expect(toasts).toEqual(["Couldn't switch drive mode: offline"])
  })
})
