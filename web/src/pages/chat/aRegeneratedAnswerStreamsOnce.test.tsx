import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react'
import { runState, runningState } from '../../test/runState'

// ── A regenerated answer that uses tools streams once ────────────────────────────────────────────
//
// Seen once in a validation run: the regenerated answer of a chat whose answer used tools showed
// its text twice while it streamed, and once after a reload. Regenerate's request starts the new
// turn before it answers, so the new answer's frames can reach the page before the response does,
// and the page then reads the chat again and replays the frames held behind that read on top of
// the snapshot. The frames are the gateway's for a regenerated answer that calls a tool: its first
// words, the variant switch the first segment's settle sends (the earlier answers kept as the
// answer's versions), the call and its result, the segment boundary, and the words after it.
// Driven through the real ChatPage, on each side of the response and of the read.
//
// What it was: the frames held behind the read. The variant switch, replayed onto a snapshot that
// already held the call and the words after it, collapsed the answer to its first words; the
// replayed tool call then ended the text run the snapshot had resumed, whose words landed over
// them. On the code before 4dbde10b6 (a card the answer already shows ends no run, and a segment
// boundary the snapshot holds is skipped) the second case below fails with the first words gone.
// The first case passes on both: what the frames paint before the response is replaced by the
// snapshot the response's read adopts.

const SESSION = 'chat-8-regen'

const h = vi.hoisted(() => ({
  detailCalls: [] as { resolve: (d: unknown) => void }[],
  chatSessionDetail: vi.fn(),
  regenerate: vi.fn(),
}))

vi.mock('../../lib/api', () => {
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    regenerate: h.regenerate,
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  const api = new Proxy(base, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = (...__a: unknown[]) => ok())
    },
  })
  return { api, errText: (e: unknown) => String((e as Error)?.message ?? e) }
})

class FakeSocket {
  static last: FakeSocket | null = null
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  constructor(public url: string) { FakeSocket.last = this; setTimeout(() => this.onopen?.(), 0) }
  send(): void {}
  close(): void { this.readyState = 3 }
}

type Frame = [type: string, data: Record<string, unknown>]
const deliver = (frames: Frame[]) => {
  for (const [type, data] of frames) {
    act(() => { FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type, data: { session: SESSION, ...data } }) }) })
  }
}

let rafQueue: FrameRequestCallback[] = []
let rafClock = 0
function paintFrames(n = 30) {
  for (let i = 0; i < n && rafQueue.length; i++) {
    const due = rafQueue; rafQueue = []
    rafClock += 20
    act(() => { due.forEach((cb) => cb(rafClock)) })
  }
}

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']
let answerRegenerate: (r: unknown) => void

beforeEach(async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  rafQueue = []; rafClock = 0
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { rafQueue.push(cb); return rafQueue.length })
  vi.stubGlobal('cancelAnimationFrame', () => {})
  Element.prototype.scrollIntoView ??= () => {}
  if (typeof globalThis.IntersectionObserver === 'undefined') {
    vi.stubGlobal('IntersectionObserver', class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
      takeRecords(): [] { return [] }
    })
  }
  sessionStorage.clear()
  localStorage.clear()
  FakeSocket.last = null
  h.detailCalls.length = 0
  h.chatSessionDetail.mockReset().mockImplementation(() => new Promise((resolve) => { h.detailCalls.push({ resolve }) }))
  h.regenerate.mockReset().mockImplementation(() => new Promise((resolve) => { answerRegenerate = resolve }))
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

const QUESTION = { role: 'user', content: 'Read my notes, please.', ts: 't-user' }
const OLD = [
  { role: 'assistant', content: 'Let me look.', ts: 't-a1' },
  { role: 'tool', content: 'read_file', cls: 'msg msg-tool', meta: { tool_call_id: 't1', purpose: '', input: '{}', kind: 'read', detail: 'notes.md', done: true, output: 'ok' } },
  { role: 'assistant', content: 'They read fine.', ts: 't-a2' },
]
const FIRST = 'Looking again now.'
const SECOND = 'Your notes are in order.'
const TOOL: Record<string, unknown> = { tool_call_id: 't2', tool: 'read_file', kind: 'read', purpose: '', input_preview: '{}', detail: 'notes.md' }

/** The new answer's frames up to the snapshot (stamped up to 103), and the words after it. */
const BEFORE_SNAPSHOT: Frame[] = [
  ['chat_chunk', { content: FIRST, seq: 101 }],
  ['chat_variant_switch', { index: 1, count: 2, content: FIRST }],
  ['tool_call', TOOL],
  ['tool_result', { tool_call_id: 't2', output: 'ok' }],
  ['chat_segment', { seq: 102 }],
  ['chat_chunk', { content: 'Your notes ', seq: 103 }],
]
const AFTER_SNAPSHOT: Frame[] = [['chat_chunk', { content: 'are in order.', seq: 104 }]]
/** The chat as the gateway holds it once those frames are sent. */
const MID_ANSWER = {
  key: SESSION, title: 'Notes', queue: [], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  messages: [
    QUESTION,
    { role: 'assistant', content: FIRST, ts: 't-n1', variants: [{ content: 'Let me look.They read fine.' }, { content: FIRST }], variant_idx: 1 },
    { role: 'tool', content: 'read_file', cls: 'msg msg-tool', meta: { tool_call_id: 't2', purpose: '', input: '{}', kind: 'read', detail: 'notes.md', done: true, output: 'ok' } },
    { role: 'streaming', content: 'Your notes ' },
  ],
  run_state: runningState(), stream_seq: 103,
}

/** The chat opened on its finished answer, Regenerate pressed and not yet answered. */
async function regenerating() {
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
  await waitFor(() => expect(h.detailCalls.length).toBe(1))
  await act(async () => {
    h.detailCalls[0].resolve({
      key: SESSION, title: 'Notes', queue: [], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
      messages: [QUESTION, ...OLD], run_state: runState({ session: SESSION }), stream_seq: 100,
    })
  })
  fireEvent.click(await screen.findByRole('button', { name: 'Regenerate' }))
  await waitFor(() => expect(h.regenerate).toHaveBeenCalled())
}

async function answered() {
  await act(async () => { answerRegenerate({ ok: true }) })
  await waitFor(() => expect(h.detailCalls.length, 'the chat was not read again').toBeGreaterThanOrEqual(2))
}

/** Every read since the chat opened answered with *d*: Regenerate's own, and the one a frame of
 *  a turn the page was not following starts when it reaches the page first. */
async function readsAnswered(d: unknown) {
  await act(async () => { for (const read of h.detailCalls.slice(1)) read.resolve(d) })
}

function expectEachSentenceOnce() {
  expect(screen.getAllByText(FIRST), 'the first words of the new answer').toHaveLength(1)
  expect(screen.getAllByText(SECOND), 'the words after its call').toHaveLength(1)
  expect(screen.queryByText('They read fine.'), 'the replaced answer is still on screen').toBeNull()
}

describe('a regenerated answer that calls a tool', () => {
  it('streams once when its frames reach the page before the regenerate answer does', async () => {
    await regenerating()
    deliver(BEFORE_SNAPSHOT)
    await answered()
    deliver(AFTER_SNAPSHOT)
    await readsAnswered(MID_ANSWER)
    paintFrames()
    expectEachSentenceOnce()
  })

  it('streams once when its frames arrive while the chat is read again', async () => {
    await regenerating()
    await answered()
    deliver([...BEFORE_SNAPSHOT, ...AFTER_SNAPSHOT])
    await readsAnswered(MID_ANSWER)
    paintFrames()
    expectEachSentenceOnce()
  })
})

describe('switching an answer to a version that called a tool', () => {
  // The gateway swaps the turn's rows for the version's (`chat_regenerate._show_version`) and says
  // so (`whole`): the page used to collapse the answer to the version's text, so a version's tool
  // call showed only after a reload, and read the chat again to show it as written.
  it('🔴 reads the chat again and shows the version whole, its tool call included', async () => {
    const versions = [{ content: 'Let me look.\n\nThey read fine.' }, { content: 'Probably fine.' }]
    const chat = (messages: unknown[]) => ({
      key: SESSION, title: 'Notes', queue: [], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
      messages, run_state: runState({ session: SESSION }), stream_seq: 100,
    })
    render(
      <AppearanceProvider>
        <ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} />
      </AppearanceProvider>,
    )
    await waitFor(() => expect(h.detailCalls.length).toBe(1))
    await act(async () => {
      h.detailCalls[0].resolve(chat([QUESTION, { role: 'assistant', content: 'Probably fine.', ts: 't-n', variants: versions, variant_idx: 1 }]))
    })
    await screen.findByText('Probably fine.')

    deliver([['chat_variant_switch', { index: 0, count: 2, content: versions[0].content, whole: true }]])

    await waitFor(() => expect(h.detailCalls.length, 'the chat was not read again').toBe(2))
    await act(async () => {
      h.detailCalls[1].resolve(chat([QUESTION, { ...OLD[0], variants: versions, variant_idx: 0 }, OLD[1], OLD[2]]))
    })
    paintFrames()
    // Its call, folded as a finished turn's steps are, then the words after it.
    expect(await screen.findByText(/Worked through 1 step/)).toBeTruthy()
    expect(screen.getByText('They read fine.')).toBeTruthy()
    expect(screen.queryByText('Probably fine.')).toBeNull()
  })
})
