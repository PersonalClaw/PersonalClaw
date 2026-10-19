import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup } from '@testing-library/react'
import type { CodeProject, TaskItem } from '../../../../lib/api'

// ── The cockpit task detail's controller ──────────────────────────────────────────────────────
//
// `useTaskDetail` keeps one task's state in the run and queues, un-queues or steers that task alone,
// guarded against a double send and saying so when a send fails.

const h = vi.hoisted(() => ({ uLoopQueue: vi.fn(), uLoopNudge: vi.fn() }))
vi.mock('../../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../../lib/api')>()
  return { ...real, api: { ...real.api, uLoopQueue: h.uLoopQueue, uLoopNudge: h.uLoopNudge } }
})

let useTaskDetail: typeof import('./useTaskDetail')['useTaskDetail']
const TASK = { id: 't-2', title: 'Send If-None-Match', status: 'in_progress', action_plan: ['Read the fetcher'], exit_criteria: ['Tests pass'] } as unknown as TaskItem
const project = (over: Partial<CodeProject> = {}) => ({ id: 'loop-1', status: 'running', workspace_dir: '/repo/feed', queued_task_ids: [], ...over } as unknown as CodeProject)
const LIVE = [{ kind: 'tool' as const, label: 'Edit', detail: 'src/fetch.py' }]
const mount = (over: { project?: CodeProject; task?: TaskItem; onChanged?: () => void } = {}) => renderHook(() => useTaskDetail({
  project: over.project ?? project(), task: over.task ?? TASK, doneIds: new Set(), stageOpen: true, knownIds: new Set(['t-2']),
  findings: [], liveActivity: LIVE, onChanged: over.onChanged ?? (() => {}),
}))
const toasts: string[] = []
const onToast = (e: Event) => toasts.push((e as CustomEvent).detail.text)

beforeEach(async () => {
  for (const f of Object.values(h)) f.mockReset()
  h.uLoopQueue.mockResolvedValue({})
  h.uLoopNudge.mockResolvedValue({})
  toasts.length = 0
  window.addEventListener('ne:code-toast', onToast)
  ;({ useTaskDetail } = await import('./useTaskDetail'))
})
afterEach(() => { cleanup(); window.removeEventListener('ne:code-toast', onToast) })

describe('useTaskDetail', () => {
  it('a running task shows the live activity, and its plan and criteria', () => {
    const { result } = mount()
    expect(result.current.state).toBe('running')
    expect(result.current.running).toBe(true)
    expect(result.current.live).toBe(LIVE)
    expect(result.current.plan).toEqual(['Read the fetcher'])
    expect(result.current.crit).toEqual(['Tests pass'])
    expect(result.current.ws).toBe('/repo/feed')
  })

  it('a task a stopped project left unfinished is undone, not queued or running', () => {
    const { result } = mount({ project: project({ status: 'stopped', queued_task_ids: ['t-2'] }) })
    expect(result.current.state).toBe('undone')
    expect(result.current.queued).toBe(false)
    expect(result.current.running).toBe(false)
    expect(result.current.live).toEqual([])
  })

  it('steers this task alone, and clears the box once it lands', async () => {
    const onChanged = vi.fn()
    const { result } = mount({ onChanged })
    act(() => result.current.setText('  Keep the old header too  '))
    await act(() => result.current.steer())
    expect(h.uLoopNudge).toHaveBeenCalledWith('loop-1', '[For task "Send If-None-Match"] Keep the old header too', 't-2')
    expect(result.current.text).toBe('')
    expect(result.current.lastSteer).toEqual({ text: 'Keep the old header too' })
    expect(onChanged).toHaveBeenCalledTimes(1)
  })

  it('keeps the text and says so when a steer fails', async () => {
    h.uLoopNudge.mockRejectedValueOnce(new Error('the run ended'))
    const { result } = mount()
    act(() => result.current.setText('Retry the fetch'))
    await act(() => result.current.steer())
    expect(result.current.text).toBe('Retry the fetch')
    expect(result.current.lastSteer).toEqual({ text: 'Retry the fetch', failed: true })
    expect(toasts).toEqual(["Couldn't send that steer: the run ended"])
  })

  it('sends a one-click steer without touching the box, and only once on a double send', async () => {
    const { result } = mount()
    act(() => result.current.setText('draft'))
    await act(async () => { await Promise.all([result.current.steer('Use your best judgment'), result.current.steer('Use your best judgment')]) })
    expect(h.uLoopNudge).toHaveBeenCalledTimes(1)
    expect(result.current.text).toBe('draft')
  })

  it('queues and un-queues this task, or says it could not', async () => {
    const { result } = mount({ task: { ...TASK, status: 'open' } as TaskItem })
    await act(() => result.current.queue('queue'))
    expect(h.uLoopQueue).toHaveBeenCalledWith('loop-1', ['t-2'], 'queue')
    h.uLoopQueue.mockRejectedValueOnce(new Error('offline'))
    await act(() => result.current.queue('unqueue'))
    expect(toasts).toEqual(["Couldn't unqueue this task: offline"])
  })
})
