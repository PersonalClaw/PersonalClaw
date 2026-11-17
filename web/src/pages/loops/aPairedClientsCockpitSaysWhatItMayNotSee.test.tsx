import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import { ApiError, type Loop } from '../../lib/api'

// ── An automation client's cockpit says once what its pairing may not read ──────────────────────
//
// A scoped pairing reaches a loop's own rows (the loop, its report, its stream) and none of the
// cockpit's other reads: its artifacts, its tasks, its worker's chat. The gateway refuses each
// with one uniform 403 (`device_scope_refused`). The cockpit used to read that as a failed read:
// an error panel with a retry over the outputs, and every 30-second poll asking again. Now it is
// one plain line, no load error, no toast, and the refused reads are never asked again.

const { STORE, calls } = vi.hoisted(() => ({
  STORE: { loop: null as Loop | null, report: '' },
  calls: { artifacts: 0, task: 0, chat: 0 },
}))
const refused = () => new ApiError('This automation client can use only what its scope allows: loops.', 403, 'device_scope_refused')

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    uLoops: () => Promise.resolve(STORE.loop ? [STORE.loop] : []),
    uLoop: () => (STORE.loop ? Promise.resolve(STORE.loop) : Promise.reject(new Error('no such loop'))),
    uLoopAction: vi.fn(() => Promise.resolve(null)),
    uLoopReport: () => Promise.resolve({ report: STORE.report, log: '' }),
    artifacts: () => { calls.artifacts += 1; return Promise.reject(refused()) },
    task: () => { calls.task += 1; return Promise.reject(refused()) },
    chatSessionDetail: () => { calls.chat += 1; return Promise.reject(refused()) },
    projects: () => Promise.reject(refused()),
    approvals: () => Promise.reject(refused()),
    deleteULoop: () => Promise.resolve(),
    updateULoop: () => Promise.resolve(null),
    uLoopNudge: () => Promise.resolve(),
  },
}))
vi.mock('./useRunStream', () => ({ useRunStream: () => ({ connected: false }) }))
const { notified } = vi.hoisted(() => ({ notified: [] as unknown[] }))
vi.mock('../../app/appSdk', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  notify: (...a: unknown[]) => { notified.push(a) },
}))

const { LoopCockpitPage } = await import('./LoopCockpitPage')

function goal(over: Partial<Loop> = {}): Loop {
  return {
    id: 'g1', kind: 'goal', name: 'Nightly docs', task: 'tidy the docs', execution: 'solo',
    agent: 'claude-code', model: 'sonnet', attended: false, max_cycles: 30, idle_secs: 60,
    success_criteria: null, status: 'running', total_cycles: 2, error_message: null,
    created_at: 1_780_000_000, started_at: 1_780_000_005, completed_at: null,
    kind_config: { goal_type: 'open_ended' }, linked_task_ids: ['t1', 't2'], session_key: 'loop-g1',
    ...over,
  } as Loop
}

const LINE = "This loop's artifacts and tasks are not available to this pairing."

async function mount(): Promise<void> {
  render(<LoopCockpitPage id="g1" onBack={() => {}} query={{}} setQuery={() => {}} />)
  await waitFor(() => screen.getByRole('button', { name: 'Details' }))
}

beforeEach(() => {
  invalidateKeys('loops')
  invalidateKeys('loop:', true)
  STORE.loop = goal()
  STORE.report = ''
  calls.artifacts = 0; calls.task = 0; calls.chat = 0
  notified.length = 0
})
afterEach(() => { vi.useRealTimers() })

describe('a paired client’s cockpit', () => {
  it('says in one plain line what its pairing may not read, with no load error and no claim of none', async () => {
    await mount()
    expect(await screen.findByText(LINE)).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByText(/can use only what its scope allows/)).toBeNull()
    expect(screen.queryByRole('button', { name: /try again|retry/i })).toBeNull()
    expect(screen.queryByText(/No outputs/)).toBeNull()
    expect(notified).toEqual([])
  })

  it('still shows the deliverable it may read, with the line beside it', async () => {
    STORE.loop = goal({ status: 'complete', completed_at: 1_780_000_900 })
    STORE.report = '# Tidied\n\nEvery page has a heading.'
    await mount()
    expect(await screen.findByRole('tab', { name: /Deliverable/ })).toBeTruthy()
    expect(await screen.findByText(LINE)).toBeTruthy()
  })

  it('never asks a refused read again on its poll', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    await mount()
    await screen.findByText(LINE)
    const once = { ...calls }
    expect(once.artifacts).toBe(1)
    await vi.advanceTimersByTimeAsync(95_000)
    expect(calls.artifacts).toBe(once.artifacts)
    expect(calls.task).toBe(once.task)
    expect(calls.chat).toBeLessThanOrEqual(1)
    expect(screen.getAllByText(LINE)).toHaveLength(1)
  })
})
