import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render } from '@testing-library/react'
import type { WsMessage } from '../../../lib/useChatSocket'

// ── How often the band reads ─────────────────────────────────────────────────────────────────────
//
// Its minute step only re-draws. It reads again when the gateway rings that work changed, at most
// once every 30 seconds, and a burst of rings makes one request.

let reads = 0
let needsLoading = false
let week: { start: string; end: string; occurrences: { trigger_id: string; trigger_name: string; at: number }[]; truncated?: string[] } =
  { start: '', end: '', occurrences: [] }
const socket: { message?: (m: WsMessage) => void; reconnect?: (() => void) | null } = {}

vi.mock('../../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      agentsDay: (since: string, until: string) => { reads += 1; return Promise.resolve({ since, until, agents: [], cut: 0 }) },
      spawnedAgents: () => Promise.resolve([]),
      uLoops: () => Promise.resolve([]),
      triggersWeek: () => Promise.resolve(week),
      agents: () => Promise.resolve({ agents: [], default_agent: 'PersonalClaw' }),
      usageBudget: () => Promise.resolve(null),
    },
  }
})
vi.mock('../../../lib/useChatSocket', () => ({
  useChatSocket: (message: (m: WsMessage) => void, reconnect: (() => void) | null) => {
    socket.message = message
    socket.reconnect = reconnect
  },
}))
vi.mock('../../../lib/useAgentActivity', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useAgentActivity: () => ({ entities: [], error: null, loading: false }),
}))
vi.mock('../../../lib/useNeedsYou', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useNeedsYou: () => ({ rows: [], error: null, loading: needsLoading }),
}))

import { AgentDay, READ_GAP_MS } from './AgentDay'
import { clock } from './agentDayModel'

const pass = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
const ring = (type: string) => act(() => { socket.message?.({ type } as WsMessage) })

beforeEach(() => { vi.useFakeTimers(); reads = 0; needsLoading = false; week = { start: '', end: '', occurrences: [] } })
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('the band says nothing', () => {
  it('until Needs You has answered, so it never says nothing waits before it knows', async () => {
    needsLoading = true
    const { container, rerender } = render(<AgentDay />)
    await pass(0)
    expect(container.textContent).not.toMatch(/Nothing ran/)

    needsLoading = false
    rerender(<AgentDay />)
    await pass(0)
    expect(container.textContent).toMatch(/Nothing ran today\./)
  })
})

describe('the band reads', () => {
  it('once when it is drawn, and its minute step only re-draws', async () => {
    render(<AgentDay />)
    await pass(0)
    expect(reads).toBe(1)

    await pass(5 * 60_000)
    expect(reads).toBe(1)
  })

  it('once for a burst of rings, and never twice in 30 seconds', async () => {
    expect(READ_GAP_MS).toBe(30_000)
    render(<AgentDay />)
    await pass(0)

    for (let i = 0; i < 20; i++) await ring(i % 2 ? 'running_changed' : 'subagent_done')
    await pass(READ_GAP_MS)
    expect(reads).toBe(2)

    await ring('running_changed')
    await pass(READ_GAP_MS - 1)
    expect(reads).toBe(2)
    await pass(1)
    expect(reads).toBe(3)
  })

  it('reads again after the socket comes back, inside the same 30 seconds', async () => {
    render(<AgentDay />)
    await pass(0)

    await act(() => { socket.reconnect?.() })
    await ring('running_changed')
    await pass(READ_GAP_MS)
    expect(reads).toBe(2)
  })

  it('reads nothing for a ring that is not about work', async () => {
    render(<AgentDay />)
    await pass(0)

    await ring('chat_message')
    await pass(2 * READ_GAP_MS)
    expect(reads).toBe(1)
  })
})

describe('the band reads the week', () => {
  it('with the schedules it cut short, so a cut one runs on to the window’s edge', async () => {
    // At 18:20, so the first run is never past midnight, which says 'tomorrow'.
    vi.setSystemTime(new Date(2026, 10, 3, 18, 20))
    // The week read sends 200 runs of a minute's schedule, 3 h 20 min of the 4 h ahead, and names it cut.
    const first = Math.ceil(Date.now() / 60_000) * 60_000 + 60_000
    week = {
      start: '', end: '', truncated: ['schedule:hb'],
      occurrences: [
        ...Array.from({ length: 200 }, (_, i) => ({ trigger_id: 'schedule:hb', trigger_name: 'Heartbeat tasks', at: (first + i * 60_000) / 1000 })),
        { trigger_id: 'schedule:triage', trigger_name: 'Morning triage', at: (first + 24 * 3600_000) / 1000 },
      ],
    }
    const { container } = render(<AgentDay />)
    await pass(0)

    expect(container.querySelector('li[aria-label^="Scheduled"]')?.getAttribute('aria-label'))
      .toBe(`Scheduled, agent not named: ${clock(first)} Heartbeat tasks, every 1 min.`)
  })
})
