import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import type { Loop } from '../../lib/api'
import type { WsMessage } from '../../lib/useChatSocket'
import { effectiveLoopStatus, loopStatusLabel } from '../../lib/loopStatus'

// ── A loop incident mode is holding says so, and says why ───────────────────────────────────────────
//
// With incident mode on, a running Unattended loop's page read "Working · Streaming · cycle 2/30" and
// offered nothing about the incident. Incident mode now holds the loop — no new cycle, no model call
// — while its status stays `running`, because a hold is not a pause: it carries on by itself once the
// switch is off. The loop's view carries the sentence that says so (`held`), and the page reads it,
// re-reading the loop when the gateway says the switch moved.

const HELD =
  'Held: incident mode is on, so this loop starts no new cycle and makes no model calls. ' +
  'It carries on by itself once incident mode is turned off.'

const { STORE, SOCKET } = vi.hoisted(() => ({
  STORE: { loop: null as Loop | null, reads: 0 },
  // Every component on the page subscribes; the latest handler each one passed, by call order.
  SOCKET: { handlers: [] as ((m: WsMessage) => void)[] },
}))
const frame = (m: WsMessage) => { for (const h of [...SOCKET.handlers]) h(m) }

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    uLoops: () => Promise.resolve(STORE.loop ? [STORE.loop] : []),
    uLoop: () => { STORE.reads += 1; return STORE.loop ? Promise.resolve({ ...STORE.loop }) : Promise.reject(new Error('gone')) },
    uLoopAction: vi.fn(() => Promise.resolve(null)),
    uLoopReport: () => Promise.resolve({ report: '', log: '' }),
    artifacts: () => Promise.resolve([]),
    task: () => Promise.resolve(null),
    project: () => Promise.resolve({ name: 'Test project' }),
    deleteULoop: () => Promise.resolve(),
    updateULoop: () => Promise.resolve(null),
    uLoopNudge: () => Promise.resolve(),
    chatSessionDetail: () => Promise.resolve({ messages: [] }),
  },
}))
vi.mock('./useRunStream', () => ({ useRunStream: () => ({ connected: true }) }))
vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: (onMessage: (m: WsMessage) => void) => { SOCKET.handlers.push(onMessage) },
}))

const { LoopCockpitPage } = await import('./LoopCockpitPage')

function goal(over: Partial<Loop> = {}): Loop {
  return {
    id: 'g1', kind: 'goal', name: 'Rain jackets', task: 'compare three rain jackets',
    execution: 'solo', agent: 'PersonalClaw', model: '', attended: false, max_cycles: 30,
    idle_secs: 60, success_criteria: null, status: 'running', total_cycles: 1, error_message: null,
    created_at: 1_780_000_000, started_at: 1_780_000_005, completed_at: null,
    kind_config: { goal_type: 'open_ended' }, held: '',
    ...over,
  } as Loop
}

async function mount(): Promise<void> {
  render(<LoopCockpitPage id="g1" onBack={() => {}} query={{}} setQuery={() => {}} />)
  await waitFor(() => screen.getByRole('button', { name: 'Details' }))
}

beforeEach(() => {
  invalidateKeys('loops')
  invalidateKeys('loop:', true)
  STORE.loop = null
  STORE.reads = 0
  SOCKET.handlers = []
})

describe('the display status of a held loop', () => {
  it('is Held for a running loop the switch holds, and only for one', () => {
    expect(effectiveLoopStatus('running', '', HELD)).toBe('held')
    expect(loopStatusLabel(effectiveLoopStatus('running', '', HELD))).toBe('Held')
    expect(effectiveLoopStatus('running', '', '')).toBe('running')
    // A paused or finished loop is not working anyway; its own word stays.
    expect(effectiveLoopStatus('paused', '', HELD)).toBe('paused')
    expect(effectiveLoopStatus('complete', 'cycle_budget', HELD)).toBe('ended_early')
  })
})

describe('the loop page', () => {
  it('says Held and why, not Working', async () => {
    STORE.loop = goal({ held: HELD, total_cycles: 0 })
    await mount()
    expect((await screen.findByRole('status')).textContent).toContain(HELD)
    expect(screen.getAllByText('Held').length).toBeGreaterThan(0)
    expect(screen.queryByText('Working')).toBeNull()
    // …and nothing else on the page says it is working either.
    expect(screen.queryByText(/Working on the first cycle/)).toBeNull()
  })

  it('a running loop nothing holds still reads Working', async () => {
    STORE.loop = goal()
    await mount()
    expect(await screen.findByText('Working')).toBeTruthy()
    expect(screen.queryByText(HELD)).toBeNull()
  })

  it('re-reads the loop when the gateway says the switch moved', async () => {
    STORE.loop = goal()
    await mount()
    await screen.findByText('Working')
    STORE.loop = goal({ held: HELD })
    await act(async () => { frame({ type: 'refresh', data: { kinds: ['incident', 'loops'] } }) })
    expect((await screen.findByRole('status')).textContent).toContain(HELD)
  })
})
