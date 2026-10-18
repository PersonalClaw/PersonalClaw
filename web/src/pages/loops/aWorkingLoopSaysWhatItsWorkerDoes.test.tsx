import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import type { Loop, RunState } from '../../lib/api'
import type { WsMessage } from '../../lib/useChatSocket'
import { runningState } from '../../test/runState'

// ── A working loop says what its worker is doing, as the gateway measured it ─────────────────────
//
// The loop's page took its live line from the one status its worker's turn sent ("Thinking…", once
// a turn). The worker's turn now says each phase it is in (`work_phase`): the page's status line is
// that phase's line while the turn runs, and "working" between turns; its steps list gains the
// phase once per change, however often the gateway says it again while the turn runs, and a step's
// own phase is that step's tool line, not a second one, and the thinking said after its result adds
// no line.

const { STORE, SOCKET } = vi.hoisted(() => ({
  STORE: { loop: null as Loop | null },
  // Each subscriber's handler, as often as it rendered: the socket delivers a frame once to each.
  SOCKET: { handlers: [] as ((m: WsMessage) => void)[] },
}))
const frame = (m: WsMessage) => { for (const h of new Set(SOCKET.handlers)) h(m) }

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    uLoops: () => Promise.resolve(STORE.loop ? [STORE.loop] : []),
    uLoop: () => (STORE.loop ? Promise.resolve({ ...STORE.loop }) : Promise.reject(new Error('gone'))),
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

const LOOP: Loop = {
  id: 'g1', kind: 'goal', name: 'Rain jackets', task: 'compare three rain jackets',
  execution: 'solo', agent: 'PersonalClaw', model: '', attended: false, max_cycles: 30,
  idle_secs: 60, success_criteria: null, status: 'running', total_cycles: 1, error_message: null,
  created_at: 1_780_000_000, started_at: 1_780_000_005, completed_at: null,
  kind_config: { goal_type: 'open_ended' }, held: '',
} as Loop

/** The worker's turn says a phase: its frame, as the gateway sends it for the loop's session. */
const said = (over: Partial<RunState>) => act(async () => {
  frame({ type: 'work_phase', data: { ...runningState({ session: 'loop-g1', ...over }) } })
})

/** The loop's page, with its Details rail open on the cycle being worked. */
async function mount(): Promise<void> {
  render(<LoopCockpitPage id="g1" onBack={() => {}} query={{ details: '1' }} setQuery={() => {}} />)
  await screen.findByText('Cycle 2 · working')
}

beforeEach(() => {
  invalidateKeys('loops')
  invalidateKeys('loop:', true)
  STORE.loop = { ...LOOP }
  SOCKET.handlers = []
})

describe("a working loop's status line", () => {
  it("says its worker's phase while the turn runs, and working between turns", async () => {
    await mount()
    await said({ phase: 'searching', line: 'Searching the web', seq: 2 })
    // The header's line and the cycle being worked both say it.
    expect(screen.getByText('Searching the web')).toBeTruthy()
    expect(screen.getByText('Cycle 2 · Searching the web')).toBeTruthy()
    // The turn's last frame says how it ended and names no turn: the cycle is still being worked.
    await said({ phase: 'done', line: 'Done in 4 seconds', turn: null, seq: 3 })
    expect(screen.getByText('Cycle 2 · working')).toBeTruthy()
    expect(screen.getByText('Working')).toBeTruthy()
    expect(screen.queryByText(/Done in 4 seconds/)).toBeNull()
  })

  it('hears no other work', async () => {
    await mount()
    await act(async () => {
      frame({ type: 'work_phase', data: { ...runningState({ session: 'chat-9-x', line: 'Writing', phase: 'streaming' }) } })
    })
    expect(screen.getByText('Cycle 2 · working')).toBeTruthy()
  })
})

describe("a working loop's steps", () => {
  it('gain a phase once per change, however often the gateway says it again', async () => {
    await mount()
    for (const seq of [2, 3, 4]) await said({ phase: 'thinking', line: 'Thinking', seq })
    expect(screen.getByRole('button', { name: /^1 steps/ })).toBeTruthy()
    // A step's own phase is its tool line, not a second one, and the thinking the gateway says
    // after its result adds none either: a row after every call would halve the steps it holds.
    await act(async () => { frame({ type: 'tool_call', data: { session: 'loop-g1', tool: 'web_search', purpose: 'rain jackets' } }) })
    await said({ phase: 'searching', line: 'Searching the web', seq: 5 })
    await said({ phase: 'thinking', line: 'Thinking', seq: 6 })
    expect(screen.getByRole('button', { name: /^2 steps/ })).toBeTruthy()
  })
})
