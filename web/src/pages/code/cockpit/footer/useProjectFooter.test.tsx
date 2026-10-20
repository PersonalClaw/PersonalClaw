import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup } from '@testing-library/react'
import type { CodeProject } from '../../../../lib/api'

// ── The cockpit footer's controller ───────────────────────────────────────────────────────────
//
// `useProjectFooter` keeps the project-level steer box (one send at a time, the latest steer shown
// until the run confirms it, a failed one said and its text kept), takes the cursor when the
// toast's Respond asks, knows whether the worker itself is asking, and resumes a paused loop.

const h = vi.hoisted(() => ({ uLoopNudge: vi.fn(), uLoopAction: vi.fn() }))
vi.mock('../../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../../lib/api')>()
  return { ...real, api: { ...real.api, uLoopNudge: h.uLoopNudge, uLoopAction: h.uLoopAction } }
})

let useProjectFooter: typeof import('./useProjectFooter')['useProjectFooter']
const project = (over: Record<string, unknown> = {}) => ({ id: 'loop-1', status: 'running', nudges: [], ...over } as unknown as CodeProject)
type Props = Parameters<typeof useProjectFooter>[0]
const mount = (props: Partial<Props> = {}) => renderHook((p: Props) => useProjectFooter(p), {
  initialProps: { project: project(), onNudged: () => {}, ...props } as Props,
})
const toasts: string[] = []
const onToast = (e: Event) => toasts.push((e as CustomEvent).detail.text)

beforeEach(async () => {
  for (const f of Object.values(h)) f.mockReset()
  h.uLoopNudge.mockResolvedValue({})
  h.uLoopAction.mockResolvedValue({})
  toasts.length = 0
  window.addEventListener('ne:code-toast', onToast)
  ;({ useProjectFooter } = await import('./useProjectFooter'))
})
afterEach(() => { cleanup(); window.removeEventListener('ne:code-toast', onToast) })

describe('useProjectFooter', () => {
  it('sends a steer, clears the box, and shows it until the run confirms it', async () => {
    const onNudged = vi.fn()
    const { result, rerender } = mount({ onNudged })
    act(() => result.current.setText(' Use the cached ETag '))
    await act(() => result.current.steer())
    expect(h.uLoopNudge).toHaveBeenCalledWith('loop-1', 'Use the cached ETag')
    expect(result.current.text).toBe('')
    expect(result.current.lastSteer).toEqual({ text: 'Use the cached ETag' })
    expect(onNudged).toHaveBeenCalledTimes(1)
    rerender({ project: project({ nudges: [{ text: 'Use the cached ETag', applied_cycle: 4 }] }), onNudged })
    expect(result.current.lastSteer).toEqual({ text: 'Use the cached ETag', applied_cycle: 4 })
  })

  it('says a steer failed and gives its text back', async () => {
    h.uLoopNudge.mockRejectedValueOnce(new Error('unreachable'))
    const { result } = mount()
    act(() => result.current.setText('Retry the fetch'))
    await act(() => result.current.steer())
    expect(result.current.lastSteer).toEqual({ text: 'Retry the fetch', failed: true })
    expect(result.current.text).toBe('Retry the fetch')
    expect(result.current.sending).toBe(false)
  })

  it('sends a one-click answer once on a double send, and leaves the box alone', async () => {
    const { result } = mount()
    act(() => result.current.setText('draft'))
    await act(async () => { await Promise.all([result.current.steer('Use your best judgment'), result.current.steer('Use your best judgment')]) })
    expect(h.uLoopNudge).toHaveBeenCalledTimes(1)
    expect(result.current.text).toBe('draft')
  })

  it("takes the cursor when the toast's Respond asks for it", () => {
    const onAnswerShown = vi.fn()
    const { result, rerender } = mount({ onAnswerShown })
    const box = document.createElement('textarea')
    box.scrollIntoView = () => {}
    document.body.appendChild(box)
    result.current.steerRef.current = box
    rerender({ project: project(), onNudged: () => {}, onAnswerShown, answerWanted: true })
    expect(document.activeElement).toBe(box)
    expect(onAnswerShown).toHaveBeenCalledTimes(1)
    box.remove()
  })

  it('knows the worker is asking only when the pause is its own question', () => {
    expect(mount({ project: project({ status: 'needs_input', pending_question: { question: 'Which branch?' } }) }).result.current.workerAsks).toBe(true)
    expect(mount({ project: project({ status: 'needs_input', pending_question: { question: 'Merge it?', merge: { into: 'main' } } }) }).result.current.workerAsks).toBe(false)
    expect(mount({ project: project({ status: 'needs_input', pending_question: { question: 'Over the cap', spend_cap: true } }) }).result.current.workerAsks).toBe(false)
    expect(mount({ project: project({ status: 'running' }) }).result.current.workerAsks).toBe(false)
  })

  it('resumes a paused loop, or says it could not', async () => {
    const onNudged = vi.fn()
    const { result } = mount({ onNudged })
    await act(() => result.current.resume())
    expect(h.uLoopAction).toHaveBeenCalledWith('loop-1', 'resume')
    expect(onNudged).toHaveBeenCalledTimes(1)
    h.uLoopAction.mockRejectedValueOnce(new Error('offline'))
    await act(() => result.current.resume())
    expect(toasts).toEqual(["Couldn't resume this project: offline"])
    expect(result.current.resuming).toBe(false)
  })
})
