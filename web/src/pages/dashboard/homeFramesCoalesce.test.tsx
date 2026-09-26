import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act } from '@testing-library/react'
import type { WsMessage } from '../../lib/useChatSocket'

// ── Home reads a slice once per burst of its frames, and only on the frames that move it ────────
//
// Home no longer polls the slices whose every change arrives as a frame (approvals, the Inbox,
// notifications), so frames are the whole signal, and they arrive in bursts: "Dismiss all" moves
// every open row and the gateway says so once per row. One read per frame was 37 reads of the
// Inbox for a 37-row sweep. And the run feed re-read on `refresh: history`, which is the CHAT
// history; the trigger runs it shows are `cron_history`.

const frames: Array<(m: WsMessage) => void> = []
const inboxOpen = vi.fn(() => Promise.resolve([]))
const triggersHistory = vi.fn(() => Promise.resolve({ runs: [], did_ids: [] }))

function mockApi() {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      approvals: () => Promise.resolve([]),
      inboxOpen: () => inboxOpen(),
      skillProposals: () => Promise.resolve({ proposals: [] }),
      uLoops: () => Promise.resolve([]),
      readyTasks: () => Promise.resolve([]),
      notifications: () => Promise.resolve({ notifications: [] }),
      triggersHistory: () => triggersHistory(),
      status: () => Promise.resolve({}),
      system: () => Promise.resolve({}),
      discover: () => Promise.resolve({ tips: [] }),
      doctor: () => Promise.resolve({ ok: true, core_ok: true, worst: '', capabilities: {} }),
    },
  }))
  vi.doMock('../../lib/useChatSocket', () => ({
    useChatSocket: (onMessage: (m: WsMessage) => void) => { frames.push(onMessage) },
  }))
}

async function mountHome() {
  const { DashboardLiveProvider } = await import('./DashboardLive')
  render(<DashboardLiveProvider><div /></DashboardLiveProvider>)
  await act(async () => { await Promise.resolve() })
}

function say(m: WsMessage) {
  act(() => { for (const onMessage of [...frames]) onMessage(m) })
}

beforeEach(() => {
  vi.resetModules()
  vi.useFakeTimers()
  frames.length = 0
  inboxOpen.mockClear()
  triggersHistory.mockClear()
  mockApi()
})
afterEach(() => { vi.useRealTimers() })

describe('Home and its frames', () => {
  it('reads the Inbox once for a burst of row moves', async () => {
    await mountHome()
    const onMount = inboxOpen.mock.calls.length
    for (let i = 0; i < 37; i++) say({ type: 'inbox_item_updated', data: { id: `row-${i}` } } as WsMessage)
    act(() => { vi.advanceTimersByTime(200) })
    expect(inboxOpen.mock.calls.length - onMount).toBe(1)
  })

  it('reads the run feed on a trigger run, not on a chat-history change', async () => {
    await mountHome()
    const onMount = triggersHistory.mock.calls.length
    say({ type: 'refresh', data: { kinds: ['history'] } } as WsMessage)
    act(() => { vi.advanceTimersByTime(200) })
    expect(triggersHistory.mock.calls.length - onMount).toBe(0)
    say({ type: 'refresh', data: { kinds: ['crons', 'cron_history'] } } as WsMessage)
    act(() => { vi.advanceTimersByTime(200) })
    expect(triggersHistory.mock.calls.length - onMount).toBe(1)
  })
})
