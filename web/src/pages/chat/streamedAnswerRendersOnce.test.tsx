import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── A streamed answer renders ONCE and WHOLE, whatever React batches and whenever it reloads ──
//
// Two measured defects on a fresh install (`.lanes/day56b-evidence`), both driven here through
// the REAL ChatPage wiring:
//
// 1. DOUBLED (s14). A new chat's first answer rendered twice — two full copies, or a truncated
//    copy above the whole one — in 2 of 6 runs, while the server held ONE assistant message and
//    the chat's socket received exactly one `chat_done`. The frame timelines separate the two
//    outcomes by one number: a turn ends with `activity_event {kind:"stats"}` then `chat_done`,
//    and in both doubled runs they arrived 1 ms apart; in the four clean runs, 8–19 ms apart.
//    That is a React BATCH, not a transport race. The activity handler asked the text-run owner
//    "is the trailing text still live?" INSIDE its deferred updater; when `chat_done` shared the
//    batch it had already released the run, so the stats line went BELOW the answer and the
//    terminal flush — whose own decision was taken synchronously (#3513) — found an activity
//    line at the tail and pushed the answer again. `act()` is the React batch here: frames
//    delivered in one `act()` share one render; frames in separate `act()`s do not.
//
// 2. CUT OFF (s22). Reloading mid-answer lost its start: 6,404 of 6,580 chars, then 3,635 of
//    3,857. The server sent the partial as a `streaming` message and hydration skipped the role
//    (170 and 209 chars); the remaining 6 and 13 were chunks broadcast between the mount's read
//    and its socket registering, on neither transport. The fix is one rule — a transcript is a
//    snapshot read while the socket listens, plus the frames after it — with chunks stamped by
//    the gateway so the overlap between the two is dropped, not painted twice.

const h = vi.hoisted(() => {
  const detail = {
    key: 'chat-6-x', title: 'chat-6-x', messages: [] as unknown[], running: false,
    queue: [] as unknown[], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  }
  return {
    detail,
    detailCalls: [] as { resolve: (d: unknown) => void }[],
    chatSessionDetail: vi.fn(),
    sendChat: vi.fn(),
  }
})

vi.mock('../../lib/api', () => {
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    sendChat: h.sendChat,
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    createChatSession: () => Promise.resolve({ key: 'chat-6-x' }),
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

/** A WebSocket jsdom can't open. Opens on the next tick unless a test holds it closed. */
class FakeSocket {
  static last: FakeSocket | null = null
  static holdClosed = false
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  constructor(public url: string) {
    FakeSocket.last = this
    if (!FakeSocket.holdClosed) setTimeout(() => this.onopen?.(), 0)
  }
  send(): void {}
  close(): void { this.readyState = 3 }
}

const SESSION = 'chat-6-x'
type Frame = [type: string, data: Record<string, unknown>]
const deliver = ([type, data]: Frame) =>
  FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type, data: { session: SESSION, ...data } }) })
/** Frames delivered inside ONE act() share one React render. */
const inOneBatch = (...frames: Frame[]) => act(() => { frames.forEach(deliver) })
/** The same frames, each rendered before the next arrives. */
const inSeparateBatches = (...frames: Frame[]) => { for (const f of frames) act(() => { deliver(f) }) }
/** Chunks with consecutive gateway stamps from `firstSeq`, one word each. */
const chunks = (text: string, firstSeq: number): Frame[] =>
  text.split(/(?<= )/).map((word, i) => ['chat_chunk', { content: word, seq: firstSeq + i }])

// The coalescer reveals on animation frames. jsdom's rAF is wall-clock driven, so it is replaced
// by a queue the test runs explicitly: a frame is painted exactly when the test says so.
let rafQueue: FrameRequestCallback[] = []
let rafClock = 0
function paintFrames(n = 30) {
  for (let i = 0; i < n && rafQueue.length; i++) {
    const due = rafQueue; rafQueue = []
    rafClock += 20  // > FRAME_MS, so every frame advances the reveal
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
  FakeSocket.last = null
  FakeSocket.holdClosed = false
  h.detailCalls.length = 0
  h.sendChat.mockReset().mockResolvedValue({ ok: true, session: SESSION })
  h.chatSessionDetail.mockReset().mockImplementation(
    () => new Promise((resolve) => { h.detailCalls.push({ resolve }) }),
  )
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

const page = (sub: string = SESSION, query: Record<string, string> = {}) => (
  <AppearanceProvider>
    <ChatPage sub={sub} navigate={() => {}} query={query} setQuery={() => {}} />
  </AppearanceProvider>
)

/** Answer the Nth outstanding session read (0-based) with a patch over the base payload. */
async function answerRead(n: number, patch: Record<string, unknown>) {
  await waitFor(() => expect(h.detailCalls.length, `session read #${n} was never issued`).toBeGreaterThan(n))
  await act(async () => { h.detailCalls[n].resolve({ ...h.detail, ...patch }) })
}

/** Mount a session and wait until its first read is in flight (the socket is listening). */
async function open() {
  render(page())
  await waitFor(() => expect(h.detailCalls.length).toBeGreaterThanOrEqual(1))
}

const primaryAction = () =>
  ['Stop', 'Steer — send into the running turn', 'Send message']
    .find((name) => screen.queryByRole('button', { name })) ?? '(no primary action)'

const QUESTION = { role: 'user', content: 'Count, please.', ts: 't-user' }

// ── 1. the closing frames of a turn ─────────────────────────────────────────────────────────

const ANSWER = 'The streamed answer lands exactly once.'
const STATS: Frame = ['activity_event', { kind: 'stats', text: 'Turn complete: 9 events, 0 tool calls' }]
const DONE: Frame = ['chat_done', {}]

/** Open a live turn and stream the answer until a frame has PAINTED all of it. The painted run
 *  is the precondition: the defect needs a flush to have claimed the trailing text first. */
async function streamAnswerAndPaint() {
  await open()
  await answerRead(0, { running: true, messages: [QUESTION], stream_seq: 0 })
  for (const f of chunks(ANSWER, 1)) act(() => { deliver(f) })
  paintFrames()
  expect(screen.getAllByText(ANSWER), 'precondition: the streamed answer is painted').toHaveLength(1)
}

describe('the closing frames of a streamed turn', () => {
  it('render the answer once when the stats line and chat_done share a React batch', async () => {
    await streamAnswerAndPaint()
    inOneBatch(STATS, DONE)
    paintFrames()
    expect(screen.getAllByText(ANSWER), 'chat_done appended the answer a second time').toHaveLength(1)
  })

  it('render the answer once when they arrive in separate batches (the ordering control)', async () => {
    // Falsifiability: the only difference from the test above is the batch boundary. If this one
    // also doubled, the file would be measuring something other than the batch.
    await streamAnswerAndPaint()
    inSeparateBatches(STATS, DONE)
    paintFrames()
    expect(screen.getAllByText(ANSWER)).toHaveLength(1)
  })
})

// ── 2. a reload mid-answer ──────────────────────────────────────────────────────────────────

// The partial the snapshot holds (chunks stamped 1–4) and the rest of the answer (5–8).
const PARTIAL = 'one, two, three, four, '
const REST = 'five, six, seven, eight.'
const WHOLE = PARTIAL + REST
const MID_ANSWER = { running: true, messages: [QUESTION, { role: 'streaming', content: PARTIAL }], stream_seq: 4 }

describe('a reload in the middle of an answer', () => {
  it('shows the whole partial and continues it in place', async () => {
    await open()
    await answerRead(0, MID_ANSWER)
    expect(screen.getByText(PARTIAL.trim()), 'the in-flight partial was not hydrated').toBeTruthy()
    for (const f of chunks(REST, 5)) act(() => { deliver(f) })
    paintFrames()
    expect(screen.getAllByText(WHOLE)).toHaveLength(1)
  })

  it('drops the chunks the snapshot already holds and keeps the ones after it', async () => {
    // Both sides of the read's round trip: chunks 3–4 were broadcast before the gateway took the
    // snapshot (so it holds them) but reach the tab after the read was issued; 5–6 came after it.
    await open()
    for (const f of [...chunks('three, four, ', 3), ...chunks('five, six, ', 5)]) act(() => { deliver(f) })
    await answerRead(0, MID_ANSWER)
    for (const f of chunks('seven, eight.', 7)) act(() => { deliver(f) })
    paintFrames()
    expect(screen.getAllByText(WHOLE), 'the answer across the reload is not whole-and-once').toHaveLength(1)
  })

  it('a turn that ends during the read still shows its whole answer, and settles', async () => {
    await open()
    for (const f of [...chunks(REST, 5), DONE]) act(() => { deliver(f) })
    await answerRead(0, MID_ANSWER)   // read while running: its answer ends in the replay
    paintFrames()
    expect(screen.getAllByText(WHOLE)).toHaveLength(1)
    await waitFor(() => expect(primaryAction()).toBe('Send message'))
  })

  it('does not paint a finished answer twice when its chunks reach the tab after the read (#3513)', async () => {
    // The refresh-first ordering on ANY mount, not only the one seeded mount the old fence knew.
    await open()
    await answerRead(0, { running: false, messages: [QUESTION, { role: 'assistant', content: WHOLE }], stream_seq: 8 })
    for (const f of [...chunks(WHOLE, 1), DONE]) act(() => { deliver(f) })
    paintFrames()
    expect(screen.getAllByText(WHOLE)).toHaveLength(1)
  })

  it('reads only once the chat is listening', async () => {
    // A read that reaches the gateway before the tab's socket registers leaves the chunks
    // broadcast in between on neither transport — the 6 and 13 chars missing from the middle of
    // s22's answers.
    FakeSocket.holdClosed = true
    render(page())
    await waitFor(() => expect(FakeSocket.last).toBeTruthy())
    await new Promise((r) => setTimeout(r, 50))
    expect(h.detailCalls, 'the session was read before its socket was listening').toHaveLength(0)
    act(() => { FakeSocket.last?.onopen?.() })
    await waitFor(() => expect(h.detailCalls).toHaveLength(1))
  })

  it('reads again when a socket that was too slow to wait for finally opens', async () => {
    FakeSocket.holdClosed = true
    render(page())
    // The bound is a cost limit, not a correctness one: the transcript still loads without the
    // socket, and the late open re-reads so nothing broadcast before it is lost.
    await waitFor(() => expect(h.detailCalls).toHaveLength(1), { timeout: 4_000 })
    await answerRead(0, { running: true, messages: [QUESTION], stream_seq: 0 })
    act(() => { FakeSocket.last?.onopen?.() })
    await answerRead(1, MID_ANSWER)
    expect(screen.getByText(PARTIAL.trim())).toBeTruthy()
  })
})

// ── 3. the session-create remount ───────────────────────────────────────────────────────────

/** Send the first message from a new chat, then remount on the created key — what the router
 *  does when `ensureSession` navigates. Returns the send's client stamp. */
async function sendFromNewChat() {
  const user = userEvent.setup()
  const view = render(page('new', { seed: 'Count, please.' }))
  await waitFor(() => expect(primaryAction()).toBe('Send message'))
  await user.click(screen.getByRole('button', { name: 'Send message' }))
  await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
  const ts = String(h.sendChat.mock.calls[0][2]?.client_ts)
  view.rerender(page(SESSION))
  return ts
}

describe("a new chat's first turn across the session-create remount", () => {
  it('settles a turn that ended while the remount replaced the chat', async () => {
    // The whole turn — its chunks and its `chat_done` — reached the instance the remount replaced
    // (#3575: a lost terminal frame on ~23% of first turns before the stall heal existed).
    const ts = await sendFromNewChat()
    await answerRead(0, {
      running: false, stream_seq: 8,
      messages: [{ role: 'user', content: 'Count, please.', ts }, { role: 'assistant', content: WHOLE }],
    })
    await waitFor(() => expect(primaryAction()).toBe('Send message'))
    expect(screen.getAllByText(WHOLE)).toHaveLength(1)
  })

  it('keeps the just-sent message when the replacement read beats the send', async () => {
    await sendFromNewChat()
    await answerRead(0, { running: false, messages: [], stream_seq: 0 })  // read before the POST landed
    expect(screen.getByText('Count, please.'), 'the older snapshot erased the user’s own message').toBeTruthy()
    expect(primaryAction(), 'an older snapshot must not end a turn it has not seen start').toBe('Stop')
    for (const f of [...chunks(WHOLE, 1), DONE]) act(() => { deliver(f) })
    paintFrames()
    expect(screen.getAllByText(WHOLE)).toHaveLength(1)
    await waitFor(() => expect(primaryAction()).toBe('Send message'))
  })
})

// ── 4. a gateway too slow to hold the stream for ────────────────────────────────────────────
//
// Measured in the browser on this branch before the hold was bounded: under load a session read
// took 25-45 s, and the stall reconciler issued one every two seconds while it waited. Each read
// held the chat's frames until it landed, so three of twelve first turns sat on "Stop", their
// answer and `chat_done` held, until the stall heal settled them 60-78 s later.

/** The hold bound in ChatPage (HOLD_LIMIT_MS), plus a margin. */
const PAST_THE_HOLD_MS = 4_300
const wait = (ms: number) => act(() => new Promise((r) => setTimeout(r, ms)))

describe('a session read slower than the hold', () => {
  it('lets the answer flow live, and repaints it once when the late snapshot lands', async () => {
    // The first turn of a new chat, which paints from its seed while the remount's read is out.
    const ts = await sendFromNewChat()
    await waitFor(() => expect(h.detailCalls).toHaveLength(1))
    for (const f of chunks(WHOLE, 1)) act(() => { deliver(f) })
    await wait(PAST_THE_HOLD_MS)
    paintFrames()
    expect(screen.getAllByText(WHOLE), 'the frames stayed held behind the slow read').toHaveLength(1)
    act(() => { deliver(DONE) })
    expect(primaryAction()).toBe('Send message')
    // The late snapshot was taken mid-answer, so it describes the turn as running and holds only
    // chunks 1-4. It paints over what the tab shows; the frames it does not hold, `chat_done`
    // included, are applied again on top of it.
    await answerRead(0, {
      running: true, stream_seq: 4,
      messages: [{ role: 'user', content: 'Count, please.', ts }, { role: 'streaming', content: PARTIAL }],
    })
    expect(screen.getAllByText(WHOLE), 'the late repaint lost or doubled the answer').toHaveLength(1)
    expect(primaryAction(), 'the late snapshot revived a turn that had ended').toBe('Send message')
  })

  it('the stall reconciler never reads while a session read is out, and its own read holds nothing', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const ts = await sendFromNewChat()
    await waitFor(() => expect(h.detailCalls).toHaveLength(1))
    // Streaming, silent, and the mount read unanswered: two reconciler ticks pass.
    await wait(PAST_THE_HOLD_MS)
    expect(h.detailCalls, 'the reconciler read while a session read was in flight').toHaveLength(1)
    await answerRead(0, { running: true, stream_seq: 0, messages: [{ role: 'user', content: 'Count, please.', ts }] })
    // Still silent: the reconciler reads now — once.
    await waitFor(() => expect(h.detailCalls).toHaveLength(2), { timeout: 5_000 })
    for (const f of chunks(PARTIAL, 1)) act(() => { deliver(f) })
    paintFrames()
    expect(screen.getByText(PARTIAL.trim()), 'the reconciler’s read held the stream').toBeTruthy()
    // It answers with a snapshot older than the frames that arrived meanwhile: not a stall.
    await answerRead(1, { running: false, stream_seq: 0, messages: [{ role: 'user', content: 'Count, please.', ts }] })
    expect(screen.getByText(PARTIAL.trim())).toBeTruthy()
    expect(primaryAction()).toBe('Stop')
    expect(warn.mock.calls.flat().join(' ')).not.toContain('terminal frame never reached')
    warn.mockRestore()
  })
})

