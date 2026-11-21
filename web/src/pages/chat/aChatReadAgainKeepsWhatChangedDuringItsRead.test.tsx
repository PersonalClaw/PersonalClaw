import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, act, cleanup } from '@testing-library/react'
import { runState } from '../../test/runState'

// ── A chat read again keeps what changed about it while the read was out ─────────────────────────
//
// Measured before this was written: a chat opened while something about it changed (a queued
// message started or was cancelled, its title landed, a side-chat answer streamed) held the frames
// that arrived during its read and replayed them on top of the snapshot. Then the mount set the
// queue, the title and the side chat from that same, older snapshot, over what the replay had
// applied: a queued message that had already run showed again above the composer, a new title
// went back to the old one, and the side answer lost what streamed during the read. A reconnect's
// read restored none of them, so a change missed while the link was down stayed until the next
// frame.
//
// Now every read restores the queue, the title and the bookmarks as part of adopting its snapshot,
// and the frames held behind it land on top. The read a chat opens with restores its side chat too,
// with the answer still being written: the gateway keeps what it has streamed of it in the snapshot
// and stamps each delta, so a delta the snapshot holds is not added twice. A read too slow to hold
// the stream for applies its frames live, and again on top of the snapshot when it lands.

const SESSION = 'chat-4-mount'

const h = vi.hoisted(() => ({
  reads: [] as { resolve: (d: unknown) => void }[],
  chatSessionDetail: vi.fn(),
}))

vi.mock('../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../lib/api')>()
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    usageTotals: () => Promise.resolve({ totals: { turns: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0, priced: true } }),
    dashboardConfig: () => Promise.resolve({}),
  }
  const api = new Proxy(base, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = () => Promise.resolve({}))
    },
  })
  return { ...real, api }
})

class FakeSocket {
  static all: FakeSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  constructor(public url: string) { FakeSocket.all.push(this); setTimeout(() => this.onopen?.(), 0) }
  send(): void {}
  close(): void { this.readyState = 3 }
}
const latest = () => FakeSocket.all[FakeSocket.all.length - 1]

function frame(type: string, data: Record<string, unknown>) {
  act(() => { latest()?.onmessage?.({ data: JSON.stringify({ type, data: { session: SESSION, ...data } }) }) })
}

const ASKED = 'Second, the cache.'
const SIDE_Q = 'Which file is that?'

/** The chat as the gateway serves it: a queued message, a title, and a side answer being written,
 *  of which the snapshot holds the deltas stamped up to 10. */
function detail(over: Record<string, unknown> = {}) {
  return {
    key: SESSION, title: 'Old title', memory_mode: 'persistent',
    messages: [{ role: 'user', content: 'Why does the feed refetch?', ts: '2026-11-21T09:00:00Z' }, { role: 'assistant', content: 'It ignores the validators.', ts: '2026-11-21T09:00:05Z' }],
    run_state: runState({ session: SESSION }),
    queue: [{ id: 'q1', content: ASKED }],
    stream_seq: 10,
    side: { open: true, messages: [{ role: 'user', content: SIDE_Q }], last_run_id: 'r1', is_complete: false, partial: 'It is ' },
    ...over,
  }
}

/** What changed while the read was out: the queued message ran, the title landed, and the side
 *  answer went on (the delta stamped 9 is already in the snapshot's partial). */
function changes() {
  frame('queue_pop', { queue_id: 'q1' })
  frame('session_title', { key: SESSION, title: 'New title' })
  frame('chat.side_result', { run_id: 'r1', delta: 'It is ', done: false, seq: 9 })
  frame('chat.side_result', { run_id: 'r1', delta: 'feed.py.', done: false, seq: 11 })
}

let useChatStream: typeof import('./useChatStream')['useChatStream']

function open() {
  const sinks = { setRouting: vi.fn(), onVoiceChunk: vi.fn(), onReplyFinished: vi.fn(), onOpened: vi.fn() }
  return renderHook(() => useChatStream({ sessionId: SESSION, liveRun: '', setLiveRun: () => {}, ...sinks }))
}

beforeEach(async () => {
  vi.resetModules()
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  sessionStorage.clear()
  FakeSocket.all = []
  h.reads.length = 0
  h.chatSessionDetail.mockReset().mockImplementation(() => new Promise((resolve) => { h.reads.push({ resolve }) }))
  ;({ useChatStream } = await import('./useChatStream'))
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

async function answer(n: number, d: unknown) {
  await waitFor(() => expect(h.reads.length, `read #${n} was never issued`).toBeGreaterThan(n))
  await act(async () => { h.reads[n].resolve(d) })
}

type Stream = ReturnType<typeof open>['result']['current']
function expectTheChanges(s: Stream) {
  expect(s.queued.map((q) => q.id), 'the queued message that ran is back above the composer').toEqual([])
  expect(s.title, 'the title went back to the snapshot’s').toBe('New title')
  expect(s.sideMsgs).toEqual([{ q: SIDE_Q, a: 'It is feed.py.', runId: 'r1', done: false }])
  expect(s.sideBusy, 'the side answer still being written').toBe(true)
}

describe('a chat opened while it changes', () => {
  it('🔴 keeps the queue, title and side answer that changed during its read', async () => {
    const { result } = open()
    await waitFor(() => expect(h.reads).toHaveLength(1))
    changes()
    await answer(0, detail())
    expectTheChanges(result.current)

    // The side answer ends as it would have.
    frame('chat.side_result', { run_id: 'r1', delta: '', done: true, seq: 12 })
    expect(result.current.sideMsgs[0].done).toBe(true)
    expect(result.current.sideBusy).toBe(false)
  })

  it('🔴 keeps them when its read is too slow to hold the stream for', async () => {
    const { result } = open()
    await waitFor(() => expect(h.reads).toHaveLength(1))
    // Past the hold (HOLD_LIMIT_MS, 4 s): the frames flow live, and the late snapshot paints over
    // them, so they are applied again on top of it.
    await act(() => new Promise((r) => setTimeout(r, 4_300)))
    changes()
    await answer(0, detail())
    expectTheChanges(result.current)
  }, 15_000)

  it('restores what its read holds when nothing changed meanwhile', async () => {
    const { result } = open()
    await answer(0, detail({ side: { open: true, messages: [{ role: 'user', content: SIDE_Q }, { role: 'assistant', content: 'It is feed.py.' }], last_run_id: 'r1', is_complete: true, partial: '' } }))
    expect(result.current.queued.map((q) => q.id)).toEqual(['q1'])
    expect(result.current.title).toBe('Old title')
    expect(result.current.sideMsgs).toEqual([{ q: SIDE_Q, a: 'It is feed.py.', runId: '', done: true }])
    expect(result.current.sideBusy).toBe(false)
  })
})

describe('a chat read again after its link came back', () => {
  async function reconnected(d: unknown) {
    const before = FakeSocket.all.length
    act(() => { latest().onclose?.() })
    await waitFor(() => expect(FakeSocket.all.length, 'the tab never reconnected').toBe(before + 1), { timeout: 5_000 })
    await answer(1, d)
  }

  it('🔴 takes the queue and title it missed while the link was down', async () => {
    const { result } = open()
    await answer(0, detail({ side: null }))
    expect(result.current.queued.map((q) => q.id)).toEqual(['q1'])

    // While the link was down the queued message ran and the chat was titled.
    await reconnected(detail({ side: null, queue: [], title: 'New title' }))
    expect(result.current.queued.map((q) => q.id)).toEqual([])
    expect(result.current.title).toBe('New title')
  }, 15_000)

  it('keeps a side question it has just asked, which the read may predate', async () => {
    const { result } = open()
    await answer(0, detail({ side: null }))
    act(() => { result.current.setSideMsgs([{ q: SIDE_Q, a: '', runId: '', done: false }]) })

    await reconnected(detail({ side: null }))
    expect(result.current.sideMsgs).toEqual([{ q: SIDE_Q, a: '', runId: '', done: false }])
  }, 15_000)
})
