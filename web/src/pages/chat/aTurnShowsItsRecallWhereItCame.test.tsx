import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, fireEvent, cleanup } from '@testing-library/react'
import { runState, runningState } from '../../test/runState'

// ── A turn shows its recalled context where it was recalled (#5876) ───────────────────────────
//
// What a turn recalled (its memory, lessons, earlier conversation and episodic history, put before
// the message) was said only in the turn's footer, under the answer, whenever the recall happened,
// so a recall that fed the turn before it said a word read as if it came after. It is now a line of
// the turn, where its event came: above the text it fed, or between the steps it came between. It
// is not a step of the work. It stays there after a reload: the gateway says the recall as it puts
// the prompt together, before the turn's first text or step, so the reply's record (`context_fed`)
// puts it at the head of the turn. Its details are one tap away: how much was recalled, what it is,
// and where to review it. The footer keeps what the turn learned, the model that answered and the
// telemetry, and no longer says the recall.
//
// The frames and rows are what the gateway sends and persists (`chat_runner.run_chat`,
// `memory_reads.fed`, `chat_session_map.stamp_context_fed`).

const SESSION = 'chat-1-recall'
const FED = 'Injected 1,204 chars of context (memory, lessons, history, episodic)'
const NO_MEMORY = 'Injected 312 chars of context, none of it from your memory: this is a Temporary chat'
const STATS = 'Turn complete · 2 tool calls · 9 events'
const ASKED = { role: 'user', content: 'Check the notes folder and run the tests.', ts: '2026-10-02T09:00:00Z' }
const ANSWER = 'The notes folder is tidy and the tests pass.'

const h = vi.hoisted(() => ({ detail: null as unknown, chatSessionDetail: vi.fn() }))

vi.mock('../../lib/api', () => {
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
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

function pushFrame(type: string, data: Record<string, unknown>) {
  act(() => { FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type, data: { session: SESSION, ...data } }) }) })
}

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']

beforeEach(async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
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
  h.chatSessionDetail.mockReset().mockImplementation(() => Promise.resolve(h.detail))
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

// ── the turn ────────────────────────────────────────────────────────────────────────────────

const CALLS = [
  { id: 'tc1', tool: 'Read File', kind: 'read', detail: 'notes/index.md' },
  { id: 'tc2', tool: 'Terminal', kind: 'execute', detail: 'make test' },
]

type Call = { id: string; tool: string; kind: string; detail: string }

function stepLive(c: Call) {
  pushFrame('tool_call', { tool_call_id: c.id, tool: c.tool, kind: c.kind, purpose: '', input_preview: '{}', detail: c.detail })
  pushFrame('tool_result', { tool_call_id: c.id, output: 'ok' })
}

const stepRow = (c: Call) => ({
  role: 'tool', content: c.tool, cls: 'msg msg-tool',
  meta: { tool_call_id: c.id, purpose: '', input: '{}', kind: c.kind, detail: c.detail, done: true, output: 'ok' },
})

/** The reply's record: its steps' rows, then its answer carrying what fed it (`context_fed`). */
function persisted(steps: Call[], fed: { kind: string; text: string }, stats = false): unknown[] {
  const meta: Record<string, unknown> = { context_fed: fed }
  if (stats) meta.turn_telemetry = { line: STATS }
  return [ASKED, ...steps.map(stepRow), { role: 'assistant', content: ANSWER, ts: '2026-10-02T09:01:00Z', meta }]
}

const detail = (messages: unknown[], running: boolean) => ({
  key: SESSION, title: SESSION, messages, run_state: running ? runningState({ session: SESSION }) : runState({ session: SESSION }), queue: [], task_mode: 'agent',
  approval: 'normal', memory_mode: 'persistent',
})

function openChat() {
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
}

async function aTurnRuns() {
  h.detail = detail([ASKED], true)
  openChat()
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop this turn' })).not.toBeNull())
}

/** The turn's recall line: its one control, named by what it says. */
const recallLine = () => screen.getByRole('button', { name: /^Recalled context · 1,204 chars/ })
/** Whether `a` comes before `b` in the page, as she reads it. */
const above = (a: Element, b: Element) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0
/** A step's card, by its accessible name. */
const card = (c: typeof CALLS[number]) => screen.getByRole('button', { name: new RegExp(`^Tool ${c.tool === 'Read File' ? 'Read file' : c.tool} ${c.detail}`) })

async function openTheWork(steps: number) {
  const fold = await waitFor(() => screen.getByRole('button', { name: new RegExp(`Worked through ${steps} steps?`) }))
  fireEvent.click(fold)
  await waitFor(() => expect(screen.getAllByRole('button', { name: /^Tool / })).toHaveLength(steps))
  return fold
}

describe('live, a turn shows its recall where its event came', () => {
  it('a recall said before the first text sits above it, as a line of the turn, not a step', async () => {
    await aTurnRuns()
    pushFrame('activity_event', { kind: 'context', text: FED })
    pushFrame('chat_chunk', { content: ANSWER, seq: 1 })
    h.detail = detail(persisted([], { kind: 'context', text: FED }), false)
    pushFrame('chat_done', { outcome: 'complete' })
    const answer = await screen.findByText(ANSWER)
    expect(above(recallLine(), answer), 'the recall is above the text it fed').toBe(true)
    expect(screen.queryByRole('button', { name: /Worked through/ }), 'a recall is no step: an answer with no steps has no fold').toBeNull()
  })

  it('a recall said between two steps sits between them, and is not counted as one', async () => {
    await aTurnRuns()
    stepLive(CALLS[0])
    pushFrame('activity_event', { kind: 'context', text: FED })
    stepLive(CALLS[1])
    pushFrame('chat_chunk', { content: ANSWER, seq: 1 })
    pushFrame('chat_done', { outcome: 'complete' })
    await screen.findByText(ANSWER)
    await openTheWork(2)
    expect(above(card(CALLS[0]), recallLine()), 'the recall is below the step before it').toBe(true)
    expect(above(recallLine(), card(CALLS[1])), 'the recall is above the step after it').toBe(true)
  })
})

describe('after a reload, the recall is where the gateway said it: at the head of the turn', () => {
  it('above the text it fed', async () => {
    h.detail = detail(persisted([], { kind: 'context', text: FED }), false)
    openChat()
    const answer = await screen.findByText(ANSWER)
    expect(above(recallLine(), answer)).toBe(true)
    expect(screen.queryByRole('button', { name: /Worked through/ })).toBeNull()
  })

  it('above the first step of a turn with steps, the steps still counted as two', async () => {
    h.detail = detail(persisted(CALLS, { kind: 'context', text: FED }), false)
    openChat()
    const answer = await screen.findByText(ANSWER)
    const fold = await openTheWork(2)
    expect(above(recallLine(), fold), 'the recall is above the work').toBe(true)
    expect(above(recallLine(), card(CALLS[0]))).toBe(true)
    expect(above(recallLine(), answer)).toBe(true)
  })

  it('live and after a reload, a recall said as the turn starts is in the same place', async () => {
    await aTurnRuns()
    pushFrame('activity_event', { kind: 'context', text: FED })
    for (const c of CALLS) stepLive(c)
    pushFrame('chat_chunk', { content: ANSWER, seq: 1 })
    h.detail = detail(persisted(CALLS, { kind: 'context', text: FED }), false)
    pushFrame('chat_done', { outcome: 'complete' })
    await screen.findByText(ANSWER)
    const live = await openTheWork(2)
    expect(above(recallLine(), live)).toBe(true)
    cleanup()

    openChat()
    await screen.findByText(ANSWER)
    const reloaded = await openTheWork(2)
    expect(above(recallLine(), reloaded)).toBe(true)
  })
})

describe('a memory_recall call the agent makes mid-turn is a step of its work, where it ran', () => {
  // The other recall the owner sees in the middle of a turn: the agent looking something up in your
  // memory itself (`mcp_memory`'s `memory_recall`). It says no activity line of its own, so it is a
  // call like any other: a card in the work, in the order it ran, live and after a reload, and
  // nothing about it in the turn's footer. The recall line stays the one the prompt was put
  // together with, at the head of the turn.
  const LOOKUP = { id: 'tc3', tool: 'memory_recall', kind: 'other', detail: 'what to keep in the notes folder' }
  const RAN = [CALLS[0], LOOKUP, CALLS[1]]
  const lookupCard = () => screen.getByRole('button', { name: /^Tool Recall what to keep in the notes folder/ })

  async function expectTheWorkInOrder() {
    await openTheWork(3)
    expect(above(card(CALLS[0]), lookupCard()), 'the lookup is below the step before it').toBe(true)
    expect(above(lookupCard(), card(CALLS[1])), 'the lookup is above the step after it').toBe(true)
    expect(screen.getAllByRole('button', { name: /^Recalled context/ }), 'one recall line: the prompt\'s').toHaveLength(1)
    expect(above(recallLine(), lookupCard())).toBe(true)
    const footer = screen.getByRole('button', { name: /^telemetry$|Turn details/ })
    expect(footer.textContent, 'the footer says nothing about the lookup').toBe('telemetry')
    fireEvent.click(footer)
    expect(screen.queryByText('Fed this turn:')).toBeNull()
  }

  it('live: its card sits between the steps it ran between, in the work fold', async () => {
    await aTurnRuns()
    pushFrame('activity_event', { kind: 'context', text: FED })
    for (const c of RAN) stepLive(c)
    pushFrame('chat_chunk', { content: ANSWER, seq: 1 })
    pushFrame('activity_event', { kind: 'stats', text: STATS })
    pushFrame('chat_done', { outcome: 'complete' })
    await screen.findByText(ANSWER)
    await expectTheWorkInOrder()
  })

  it('after a reload: the same card in the same place', async () => {
    h.detail = detail(persisted(RAN, { kind: 'context', text: FED }, true), false)
    openChat()
    await screen.findByText(ANSWER)
    await expectTheWorkInOrder()
  })
})

describe('its details are one tap away', () => {
  it('how much was recalled, what it is, and where to review it', async () => {
    h.detail = detail(persisted([], { kind: 'context', text: FED }), false)
    openChat()
    await screen.findByText(ANSWER)
    const line = recallLine()
    expect(line.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(line)
    expect(line.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByText(/saved memories, learned lessons, earlier conversation, and episodic history/)).toBeTruthy()
    const review = screen.getByRole('link', { name: 'Review what’s recalled' })
    expect(review.getAttribute('href')).toBe('#/memory?tab=recall')
  })

  it('a turn that read none of your memory says so on its line, with the gateway\'s reason, and offers no review', async () => {
    h.detail = detail(persisted([], { kind: 'context_without_memory', text: NO_MEMORY }), false)
    openChat()
    const answer = await screen.findByText(ANSWER)
    const line = screen.getByRole('button', { name: /^No memory recalled/ })
    expect(above(line, answer)).toBe(true)
    fireEvent.click(line)
    expect(screen.getByText(`${NO_MEMORY}.`)).toBeTruthy()
    expect(screen.queryByText(/saved memories/)).toBeNull()
    expect(screen.queryByRole('link', { name: 'Review what’s recalled' })).toBeNull()
  })
})

describe('the footer no longer says the recall', () => {
  it('it keeps the telemetry, and has no recall row', async () => {
    h.detail = detail(persisted(CALLS, { kind: 'context', text: FED }, true), false)
    openChat()
    await screen.findByText(ANSWER)
    const footer = screen.getByRole('button', { name: /^telemetry$|Turn details/ })
    expect(footer.textContent).toBe('telemetry')
    fireEvent.click(footer)
    expect(screen.queryByText('Fed this turn:')).toBeNull()
    expect(screen.getByText('Telemetry:')).toBeTruthy()
  })
})
