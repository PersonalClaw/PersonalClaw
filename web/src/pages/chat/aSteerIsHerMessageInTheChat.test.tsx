import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { hydrateTurns, turnText } from './chatTypes'
import { MessageUser } from '../../ui/chat/MessageUser'

// ── A message she steers into a running answer is her message in the chat, where it went in ─
//
// Typed while an answer streams, a message is sent as a steer, and the running turn takes it at its
// next step. The page kept a line above the composer ("Steered into this answer: …") until the turn
// ended and the transcript never held the message, so afterwards, and after any reload, the answer
// read as if she had said nothing. Now the gateway writes her row where the turn took it and says so
// live (`chat_user_message` with `steer`): her bubble sits between what the answer said before it
// and after, and says it was steered in. The line above the composer stays only while the steer
// waits for the turn to take it.

const ASKED = 'Write the loader for the garden config.'
const STEER = 'Use tabs, not spaces.'
const FIRST = "I'll indent it with four spaces."
const SECOND = 'Switching to tabs, as you asked.'
const STEER_TS = '2026-10-04T09:30:05.000Z'
const STEERED = 'Steered into the answer'

const HISTORY = [
  { role: 'user', content: ASKED, ts: '2026-10-04T09:30:00.000Z' },
  { role: 'assistant', content: FIRST, ts: '2026-10-04T09:30:02.000Z' },
  { role: 'user', content: STEER, ts: STEER_TS, meta: { steered: true } },
  { role: 'assistant', content: SECOND, ts: '2026-10-04T09:30:09.000Z' },
]

describe('her steer, read back', () => {
  it('is her own turn between the two parts of the answer, marked as steered', () => {
    const turns = hydrateTurns(HISTORY)
    expect(turns.map((t) => [t.role, turnText(t)])).toEqual([
      ['user', ASKED], ['assistant', FIRST], ['user', STEER], ['assistant', SECOND],
    ])
    expect(turns.map((t) => t.steered ?? false)).toEqual([false, false, true, false])
  })
})

describe('her bubble', () => {
  it('says a steer went into the answer', () => {
    render(<MessageUser steered>{STEER}</MessageUser>)
    expect(screen.getByText(STEERED)).toBeTruthy()
    expect(screen.getByText(STEER)).toBeTruthy()
  })

  it('says nothing of the kind for a message sent between turns', () => {
    render(<MessageUser>{ASKED}</MessageUser>)
    expect(screen.queryByText(STEERED)).toBeNull()
  })
})

// ── The page: reopened, and live ──────────────────────────────────────────────────────────────

const h = vi.hoisted(() => ({
  detailCalls: [] as { resolve: (d: unknown) => void }[],
  chatSessionDetail: vi.fn(),
  sendChat: vi.fn(),
}))

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
  act(() => { FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type, data: { session: 'chat-8-x', ...data } }) }) })
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
  FakeSocket.last = null
  h.detailCalls.length = 0
  h.sendChat.mockReset().mockResolvedValue({ ok: true, steered: true })
  h.chatSessionDetail.mockReset().mockImplementation(
    () => new Promise((resolve) => { h.detailCalls.push({ resolve }) }),
  )
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

const page = (query: Record<string, string> = {}) =>
  render(
    <AppearanceProvider>
      <ChatPage sub="chat-8-x" navigate={() => {}} query={query} setQuery={() => {}} />
    </AppearanceProvider>,
  )

async function answerDetail(patch: Record<string, unknown>) {
  await waitFor(() => expect(h.detailCalls.length).toBeGreaterThanOrEqual(1))
  await act(async () => {
    h.detailCalls[0].resolve({
      key: 'chat-8-x', title: 'Garden', queue: [], task_mode: 'agent', approval: 'normal',
      memory_mode: 'persistent', ...patch,
    })
  })
}

/** Whether *a*'s node comes before *b*'s in the document. */
const before = (a: Element, b: Element) =>
  Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)

describe('a chat reopened after a steer', () => {
  it('shows her steer between the two parts of the answer, as steered', async () => {
    page()
    await answerDetail({ messages: HISTORY, running: false })

    const first = await screen.findByText(FIRST)
    const steer = screen.getByText(STEER)
    const second = screen.getByText(SECOND)
    expect(before(first, steer) && before(steer, second)).toBe(true)
    expect(screen.getAllByText(STEERED)).toHaveLength(1)
  })
})

describe('a steer she sends while the answer streams', () => {
  it('waits above the composer, then moves into the chat where the turn took it', async () => {
    const user = userEvent.setup()
    // `?seed=` pre-fills the composer, so this drives the real send path without typing into
    // CodeMirror (which jsdom cannot lay out).
    page({ seed: STEER })
    await answerDetail({ messages: [HISTORY[0]], running: true, steerable: true })
    pushFrame('chat_chunk', { content: FIRST, seq: 1 })
    await user.click(await screen.findByRole('button', { name: 'Steer — send into the running turn' }))

    await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
    const [text, , meta, mode] = h.sendChat.mock.calls[0]
    expect([text, mode]).toEqual([STEER, 'steer'])
    const ts = (meta as { client_ts: string }).client_ts
    // Accepted: until the turn takes it, the line above the composer says it is on its way.
    expect(await screen.findByText(`Steering into this answer: ${STEER}`)).toBeTruthy()
    expect(screen.queryByText(STEERED)).toBeNull()

    // The turn takes it: the answer so far is settled, then her row is written where it now is.
    pushFrame('chat_segment', {})
    pushFrame('chat_user_message', { content: STEER, ts, steer: true })
    pushFrame('chat_chunk', { content: SECOND, seq: 2 })

    await waitFor(() => expect(screen.queryByText(`Steering into this answer: ${STEER}`)).toBeNull())
    const steered = await screen.findByText(STEERED)
    const first = screen.getByText(FIRST)
    const second = await screen.findByText(SECOND)
    expect(before(first, steered) && before(steered, second)).toBe(true)
    expect(screen.getAllByText(STEER)).toHaveLength(1)
    // It is the same turn: the composer still offers Stop, not Send.
    expect(screen.queryByRole('button', { name: 'Send message' })).toBeNull()
  })

  it('leaves the line above the composer once a steer the turn did not take is queued', async () => {
    const user = userEvent.setup()
    page({ seed: STEER })
    await answerDetail({ messages: [HISTORY[0]], running: true, steerable: true })
    await user.click(await screen.findByRole('button', { name: 'Steer — send into the running turn' }))
    await waitFor(() => expect(h.sendChat).toHaveBeenCalled())
    const ts = (h.sendChat.mock.calls[0][2] as { client_ts: string }).client_ts
    expect(await screen.findByText(`Steering into this answer: ${STEER}`)).toBeTruthy()

    // The turn ended first: the gateway queues it for the next turn and says which steer it is.
    pushFrame('queue_push', { content: STEER, ts: '2026-10-04T09:30:20.000Z', queue_id: 'q-1', steer_ts: ts })

    await waitFor(() => expect(screen.queryByText(`Steering into this answer: ${STEER}`)).toBeNull())
    expect(screen.queryByText(STEERED)).toBeNull()
  })
})
