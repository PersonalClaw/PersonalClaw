import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup, waitFor } from '@testing-library/react'

// ── The code cockpit's controller ─────────────────────────────────────────────────────────────
//
// `useCodeCockpit` keeps the project for one code loop: read on mount, "missing" once the loop is
// gone, a failed read said and polled until it recovers, kept current from the run stream; and the
// terminal flag, the toast and the task refresh the regions share.

type StreamOpts = { onSnapshot: (p: unknown) => void; onLifecycle: (event: string, data?: unknown) => void }
const h = vi.hoisted(() => ({
  uLoop: vi.fn(), agentProviders: vi.fn(), useRunStream: vi.fn(), useChatSocket: vi.fn(), useVisiblePoll: vi.fn(),
  stream: null as unknown as { open: boolean; opts: StreamOpts },
}))
vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  return { ...real, api: { ...real.api, uLoop: h.uLoop, agentProviders: h.agentProviders } }
})
vi.mock('../../loops/useRunStream', () => ({ useRunStream: h.useRunStream }))
vi.mock('../../../lib/useChatSocket', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/useChatSocket')>()
  return { ...real, useChatSocket: h.useChatSocket }
})
vi.mock('../../../lib/useVisiblePoll', () => ({ useVisiblePoll: h.useVisiblePoll }))
vi.mock('../../../lib/useWorkspaceMissing', () => ({ useWorkspaceMissing: () => false }))

let useCodeCockpit: typeof import('./useCodeCockpit')['useCodeCockpit']
const LOOP = { id: 'loop-1', name: 'Feed refetch fix', kind: 'code', status: 'running', plan: [{ stage: 'build', title: 'Build' }], phase_status: { build: 'active' }, kind_config: { project_kind: 'brownfield', verify_command: 'npm run build' } }
const mount = (query: Record<string, string> = {}, setQuery = vi.fn()) => ({ setQuery, ...renderHook(() => useCodeCockpit({ id: 'loop-1', query, setQuery })) })

beforeEach(async () => {
  for (const f of [h.uLoop, h.agentProviders, h.useRunStream, h.useChatSocket, h.useVisiblePoll]) f.mockReset()
  h.uLoop.mockResolvedValue(LOOP)
  h.agentProviders.mockResolvedValue([])
  h.useRunStream.mockImplementation((_id: string, open: boolean, opts: StreamOpts) => { h.stream = { open, opts }; return { connected: true } })
  ;({ useCodeCockpit } = await import('./useCodeCockpit'))
})
afterEach(() => cleanup())

describe('useCodeCockpit', () => {
  it('reads the project on mount, in the shape the regions draw', async () => {
    const { result } = mount()
    await waitFor(() => expect(result.current.project).toMatchObject({ id: 'loop-1', project_kind: 'brownfield', verify_command: 'npm run build' }))
    expect(result.current.project).toMatchObject({ stage_plan: [{ stage: 'build', title: 'Build' }], stage_status: { build: 'active' } })
    expect(result.current.connected).toBe(true)
  })

  it('a loop that is gone is missing, and its stream closes', async () => {
    h.uLoop.mockRejectedValue(Object.assign(new Error('Not found'), { status: 404 }))
    const { result } = mount()
    await waitFor(() => expect(result.current.project).toBe('missing'))
    expect(h.stream.open).toBe(false)
  })

  it('says a read that failed, and polls until it recovers', async () => {
    h.uLoop.mockRejectedValue(Object.assign(new Error('Bad gateway'), { status: 502 }))
    const { result } = mount()
    await waitFor(() => expect(result.current.loadErr?.message).toBe('Bad gateway'))
    expect(result.current.project).toBeNull()
    expect(h.useVisiblePoll.mock.calls.some(([, ms]) => ms === 4000)).toBe(true)
  })

  it('follows the run stream: a snapshot replaces the project, and a delete makes it missing', async () => {
    const { result } = mount()
    await waitFor(() => expect(result.current.project).not.toBeNull())
    act(() => h.stream.opts.onSnapshot({ ...LOOP, name: 'Honour ETags' }))
    expect(result.current.project).toMatchObject({ name: 'Honour ETags' })
    act(() => h.stream.opts.onLifecycle('needs_input'))
    expect(result.current.toast).toEqual({ kind: 'question', text: '' })
    act(() => h.stream.opts.onLifecycle('deleted'))
    expect(result.current.project).toBe('missing')
  })

  it('keeps the terminal flag in the URL, and re-reads the tasks when asked', async () => {
    const { result, setQuery } = mount({ term: '1' })
    expect(result.current.showTerm).toBe(true)
    act(() => result.current.setShowTerm(false))
    expect(setQuery).toHaveBeenCalledWith({ term: null }, undefined)
    const before = result.current.tasksNonce
    act(() => result.current.refetchTasks())
    expect(result.current.tasksNonce).toBe(before + 1)
  })
})
