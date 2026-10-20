import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import type { ChatSessionSummary, RunState } from '../../../lib/api'
import type { WsMessage } from '../../../lib/useChatSocket'
import { resetDataStore } from '../../../lib/data/store'
import { runState, runningState } from '../../../test/runState'

// ── A row on `#/chat/history` follows its chat ──────────────────────────────────────────────────
//
// The list is what `GET /api/chat/sessions` answered when the page read it, and a chat moves on
// behind it: its turn ends, its count grows, a chat started in another tab appears. Measured on a
// gateway: a chat whose turn had ended still read "3 messages · running" while the route already
// served it idle with 7 messages, because the page heard none of the frames that say so. These
// tests paint the list, move the server, deliver the frames the gateway sends, and read the row.

const SESSION = 'chat-live'
const server = { rows: [] as ChatSessionSummary[] }
const chatSessions = vi.fn(async () => server.rows)

vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  const api = new Proxy({
    chatSessions,
    chatFolders: () => Promise.resolve([]), chatTags: () => Promise.resolve([]),
    rooms: () => Promise.resolve({ enabled: false }),
  } as Record<string, unknown>, {
    get(t, p: string) { return p in t ? t[p] : (t[p] = () => Promise.resolve({})) },
  })
  return { ...real, api }
})

// Every subscriber's handler, so a frame reaches the page the way the one socket delivers it.
const handlers = new Set<{ current: (m: WsMessage) => void }>()
vi.mock('../../../lib/useChatSocket', async (orig) => {
  const real = await orig<typeof import('../../../lib/useChatSocket')>()
  const React = await import('react')
  return {
    ...real,
    useChatSocket: (cb: (m: WsMessage) => void) => {
      const ref = React.useRef(cb)
      ref.current = cb
      React.useEffect(() => {
        handlers.add(ref)
        return () => { handlers.delete(ref) }
      }, [])
    },
  }
})
const deliver = (m: WsMessage) => { for (const h of [...handlers]) h.current(m) }
const phaseFrame = (rs: RunState): WsMessage => ({ type: 'work_phase', data: rs as unknown as Record<string, unknown> })

const row = (key: string, messages: number, rs: RunState, title = 'Why the feed refetches') =>
  ({ key, title, origin: 'manual', messages, run_state: rs, last_activity_ts: '2026-10-01T09:00:00Z' }) as ChatSessionSummary
const RUNNING = runningState({ session: SESSION, seq: 4 })
const ENDED = runState({ session: SESSION, phase: 'done', line: 'Done', last_outcome: 'complete', seq: 9 })

/** Long past any coalescing window, and short of the request deadline. */
const LATER_MS = 2000
const settle = (ms = 0) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })

async function paint() {
  const { ChatHistoryPage } = await import('./ChatHistoryPage')
  render(<ChatHistoryPage navigate={() => {}} query={{}} setQuery={() => {}} />)
  await settle()
  expect(screen.getByText('3 messages · running'), 'the list paints the running chat first').toBeTruthy()
  chatSessions.mockClear()
}

beforeEach(() => {
  vi.useFakeTimers()
  resetDataStore()
  handlers.clear()
  chatSessions.mockClear()
  server.rows = [row(SESSION, 3, RUNNING)]
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('a chat-list row follows its chat', () => {
  it('stops saying "running" and counts the new messages once the turn ends', async () => {
    await paint()
    server.rows = [row(SESSION, 7, ENDED)]
    // What the gateway sends as a turn ends: its last phase, the list push, the end, the hint.
    deliver(phaseFrame(ENDED))
    deliver({ type: 'sessions', data: {} })
    deliver({ type: 'chat_done', data: { session: SESSION, outcome: 'complete' } })
    deliver({ type: 'refresh', data: { kinds: ['history'] } })
    await settle(LATER_MS)
    expect(screen.queryByText(/running/), 'the row still says running after its turn ended').toBeNull()
    expect(screen.getByText('7 messages')).toBeTruthy()
    expect(chatSessions, 'one turn ending is one read of the list, not one per frame').toHaveBeenCalledTimes(1)
  })

  it.each([
    ['its last phase frame', phaseFrame(ENDED)],
    ['the list push', { type: 'sessions', data: {} }],
    ['the history hint', { type: 'refresh', data: { kinds: ['history'] } }],
  ])('reads the list again on %s alone', async (_what, frame) => {
    await paint()
    server.rows = [row(SESSION, 7, ENDED)]
    deliver(frame as WsMessage)
    await settle(LATER_MS)
    expect(screen.getByText('7 messages')).toBeTruthy()
  })

  it('shows a chat started elsewhere, on the list push', async () => {
    await paint()
    server.rows = [row(SESSION, 3, RUNNING), row('chat-new', 1, runningState({ session: 'chat-new' }), 'Started in another tab')]
    deliver({ type: 'sessions', data: {} })
    await settle(LATER_MS)
    expect(screen.getByText('Started in another tab')).toBeTruthy()
  })

  it('does not read the list for a running turn saying its phase again, or for another hint', async () => {
    await paint()
    // The gateway says a running turn's phase again every fifteen seconds, and on each change of
    // step: the row already says running, so there is nothing to read.
    for (let i = 0; i < 20; i += 1) deliver(phaseFrame({ ...RUNNING, phase: i % 2 ? 'tool' : 'thinking', seq: 10 + i }))
    deliver({ type: 'refresh', data: { kinds: ['loops'] } })
    deliver({ type: 'chat_chunk', data: { session: SESSION, content: 'more' } })
    await settle(LATER_MS)
    expect(chatSessions).not.toHaveBeenCalled()
  })

  it('reads the list once for a storm of pushes', async () => {
    await paint()
    server.rows = [row(SESSION, 7, ENDED)]
    for (let i = 0; i < 50; i += 1) deliver({ type: 'sessions', data: {} })
    await settle(LATER_MS)
    expect(chatSessions).toHaveBeenCalledTimes(1)
    expect(screen.getByText('7 messages')).toBeTruthy()
  })
})
