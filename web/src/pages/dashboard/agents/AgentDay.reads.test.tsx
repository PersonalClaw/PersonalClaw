import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render } from '@testing-library/react'
import type { WsMessage } from '../../../lib/useChatSocket'

// ── How often the band reads ─────────────────────────────────────────────────────────────────────
//
// Its minute step only re-draws. It reads again when the gateway rings that work changed, at most
// once every 30 seconds, and a burst of rings makes one request.

let reads = 0
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
      triggersWeek: () => Promise.resolve({ start: '', end: '', occurrences: [] }),
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
  useNeedsYou: () => ({ rows: [], error: null, loading: false }),
}))

import { AgentDay, READ_GAP_MS } from './AgentDay'

const pass = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
const ring = (type: string) => act(() => { socket.message?.({ type } as WsMessage) })

beforeEach(() => { vi.useFakeTimers(); reads = 0 })
afterEach(() => { cleanup(); vi.useRealTimers() })

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
