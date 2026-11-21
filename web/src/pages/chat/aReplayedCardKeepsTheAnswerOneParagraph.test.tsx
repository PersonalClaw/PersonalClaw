import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import { runningState } from '../../test/runState'

// ── A frame replayed onto a re-read the chat already shows leaves its answer one paragraph ───────
//
// Measured before this was written: while an answer streams, a chat that reads its transcript again
// (a reload or deep link, a reconnect, a rewind) holds the frames that arrive meanwhile and replays
// them on top of the snapshot it adopts. Only a text chunk carried the gateway's stamp, so only a
// chunk the snapshot held was dropped. A tool call, an approval or an agent's question the snapshot
// already showed was matched by id and not drawn twice, but its frame still ended the answer's text
// run; so did a segment or thinking frame. The chunks after it then opened a second text segment,
// and one paragraph of the answer read as two until the next reload.
//
// Now a card frame ends the run only when its card is new to the transcript, and a segment or
// thinking frame carries the gateway's stamp, so one the snapshot already reflects is skipped like
// a chunk. A card or a segment that is new still ends the run. Driven through the real ChatPage.

const SESSION = 'chat-7-x'

const h = vi.hoisted(() => ({
  detailCalls: [] as { resolve: (d: unknown) => void }[],
  chatSessionDetail: vi.fn(),
}))

vi.mock('../../lib/api', () => {
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    // Thinking is shown inline, so a thinking frame is drawn rather than dropped on arrival.
    dashboardConfig: () => Promise.resolve({ show_thinking_inline: true }),
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
/** Chunks with consecutive gateway stamps from `firstSeq`, one word each. */
const chunks = (text: string, firstSeq: number): Frame[] =>
  text.split(/(?<= )/).map((word, i) => ['chat_chunk', { content: word, seq: firstSeq + i }])

// The coalescer reveals on animation frames, run here when the test says so.
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
  h.chatSessionDetail.mockReset().mockImplementation(
    () => new Promise((resolve) => { h.detailCalls.push({ resolve }) }),
  )
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

const QUESTION = { role: 'user', content: 'Read my notes, please.', ts: 't-user' }
const BEFORE = 'Let me look at them.'
// The answer after the call: what the snapshot holds of it (chunks stamped 7–10), and the rest.
const PARTIAL = 'one, two, three, four, '
const REST = 'five, six, seven, eight.'
const WHOLE = PARTIAL + REST

const TOOL_ROW = {
  role: 'tool', content: 'read_file', cls: 'msg msg-tool',
  meta: { tool_call_id: 't1', purpose: '', input: '{}', kind: 'read', detail: 'notes.md', done: true, output: 'ok' },
}
const TOOL_FRAME: Frame = ['tool_call', { tool_call_id: 't1', tool: 'read_file', kind: 'read', purpose: '', input_preview: '{}', detail: 'notes.md' }]
const APPROVAL_ROW = {
  role: 'permission', content: 'read_file', cls: 'msg msg-permission',
  meta: { approval_id: 'r1', tool_call_id: 't1', input: '{}', resolved: 'approved' },
}
const APPROVAL_FRAME: Frame = ['approval', { request_id: 'r1', tool: 'read_file', tool_input: '{}' }]

/** The chat opened mid-answer, its read held open so the frames the test delivers next are the
 *  ones the read replays. */
async function opened() {
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
  await waitFor(() => expect(h.detailCalls.length).toBeGreaterThanOrEqual(1))
}

/** Answer the read with the answer so far: its text, its card(s) and the partial after them. */
async function answerRead(rows: unknown[], streamSeq = 10, partial = PARTIAL) {
  await act(async () => {
    h.detailCalls[0].resolve({
      key: SESSION, title: 'Notes', queue: [], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
      messages: [QUESTION, ...rows, { role: 'streaming', content: partial }],
      run_state: runningState(), stream_seq: streamSeq,
    })
  })
}

const toolCards = () => screen.queryAllByRole('button', { name: /^Tool / })
const follows = (a: Element, b: Element) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)

describe('a frame replayed onto a re-read that already shows it', () => {
  it('🔴 a tool call the snapshot holds leaves the answer one paragraph, under one card', async () => {
    await opened()
    deliver([TOOL_FRAME, ...chunks(PARTIAL, 7)])
    await answerRead([{ role: 'assistant', content: BEFORE }, TOOL_ROW])
    deliver(chunks(REST, 11))
    paintFrames()

    expect(screen.getAllByText(WHOLE), 'the answer after the call split into two paragraphs').toHaveLength(1)
    expect(toolCards()).toHaveLength(1)
  })

  it('🔴 a segment boundary the snapshot reflects leaves the run open', async () => {
    await opened()
    deliver([['chat_segment', { seq: 6 }], ...chunks(PARTIAL, 7)])
    await answerRead([{ role: 'assistant', content: BEFORE }, TOOL_ROW])
    deliver(chunks(REST, 11))
    paintFrames()

    expect(screen.getAllByText(WHOLE)).toHaveLength(1)
  })

  it('🔴 an approval the snapshot holds leaves the answer one paragraph', async () => {
    await opened()
    deliver([APPROVAL_FRAME, TOOL_FRAME, ...chunks(PARTIAL, 7)])
    await answerRead([{ role: 'assistant', content: BEFORE }, APPROVAL_ROW, TOOL_ROW])
    deliver(chunks(REST, 11))
    paintFrames()

    expect(screen.getAllByText(WHOLE)).toHaveLength(1)
  })

  it('🔴 thinking from before the snapshot leaves the run open', async () => {
    await opened()
    deliver([['chat_thinking', { content: 'Which notes first?', seq: 6 }], ...chunks(PARTIAL, 7)])
    await answerRead([{ role: 'assistant', content: BEFORE }, TOOL_ROW])
    deliver(chunks(REST, 11))
    paintFrames()

    expect(screen.getAllByText(WHOLE)).toHaveLength(1)
  })
})

describe('a frame new to the snapshot', () => {
  it('a tool call it does not hold still ends the run: the card sits between the two paragraphs', async () => {
    await opened()
    await answerRead([], 4)
    deliver([TOOL_FRAME, ...chunks(REST, 5)])
    paintFrames()

    expect(screen.queryAllByText(WHOLE), 'the text after the new card joined the text before it').toHaveLength(0)
    const [card] = toolCards()
    expect(card).toBeTruthy()
    const before = screen.getByText(PARTIAL.trim())
    const after = screen.getByText(REST)
    expect(follows(before, card) && follows(card, after), 'the card is not between the paragraphs').toBe(true)
  })

  it('a call id an earlier answer used is a new card in this one', async () => {
    // Some providers number their calls per answer, so `t1` comes round again. The record is of
    // the answer the frames patch, not of the whole chat.
    await opened()
    await answerRead([
      { role: 'assistant', content: BEFORE }, TOOL_ROW, { role: 'assistant', content: 'Your notes are fine.' },
      { role: 'user', content: 'And the other folder?', ts: 't-user-2' },
    ], 4)
    deliver([TOOL_FRAME, ...chunks(REST, 5)])
    paintFrames()

    expect(screen.queryAllByText(WHOLE)).toHaveLength(0)
    // The earlier answer's work is folded, so the card drawn is this answer's.
    const card = toolCards().at(-1)
    expect(card, 'the call in this answer has no card of its own').toBeTruthy()
    expect(follows(screen.getByText(PARTIAL.trim()), card!) && follows(card!, screen.getByText(REST))).toBe(true)
  })

  it('a segment boundary stamped after the snapshot still ends the run', async () => {
    await opened()
    await answerRead([], 4)
    deliver([['chat_segment', { seq: 5 }], ...chunks(REST, 6)])
    paintFrames()

    expect(screen.queryAllByText(WHOLE)).toHaveLength(0)
    expect(screen.getByText(PARTIAL.trim())).toBeTruthy()
    expect(screen.getByText(REST)).toBeTruthy()
  })
})
