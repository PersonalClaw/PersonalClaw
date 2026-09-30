import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'

// ── A line the gateway adds to a chat is painted when it is added ──────────────────────────────
//
// `/compact`'s outcome ("Conversation compacted: freed 10% of the conversation …"), the pass a turn
// runs on its own history, and the notice that a session was restarted at the context threshold
// ("Auto-compacted at 71% of the context window.") are written to the transcript by the gateway and
// sent to the open chat as a `chat_message` frame. The page dropped every such frame that was not an
// error, so each line appeared only after a reload. It paints them now, joined to the answer the way
// a reload joins consecutive assistant messages, and once however many transports carry it.

const h = vi.hoisted(() => {
  const detail = {
    key: 'chat-7-x', title: 'chat-7-x', running: false,
    messages: [
      { role: 'user', content: 'first question', ts: '2026-09-26T09:59:00Z' },
      { role: 'assistant', content: 'first answer', ts: '2026-09-26T09:59:05Z' },
      { role: 'user', content: '/compact', ts: '2026-09-26T10:00:00Z' },
    ] as unknown[],
    queue: [] as unknown[], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  }
  return { detail, detailCalls: [] as { resolve: (d: unknown) => void }[], chatSessionDetail: vi.fn() }
})

vi.mock('../../lib/api', () => {
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    sendChat: () => Promise.resolve({ ok: true, session: 'chat-7-x' }),
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
  act(() => { FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type, data: { session: 'chat-7-x', ...data } }) }) })
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
  h.chatSessionDetail.mockReset().mockImplementation(
    () => new Promise((resolve) => { h.detailCalls.push({ resolve }) }),
  )
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

async function openRunning() {
  render(
    <AppearanceProvider>
      <ChatPage sub="chat-7-x" navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
  await waitFor(() => expect(h.detailCalls.length).toBeGreaterThanOrEqual(1))
  await act(async () => { h.detailCalls[0].resolve({ ...h.detail, running: true }) })
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeNull())
}

const COMPACTED = 'Conversation compacted: freed 10% of the conversation (74,611 → 67,481 characters)'
const occurrences = (text: string) => screen.queryAllByText(text, { exact: false }).length

describe('a notice the gateway adds to the chat', () => {
  it("paints /compact's outcome as it happens, once for both transports", async () => {
    await openRunning()
    // The same line reaches the page twice: the transcript's own announcement and the notice's.
    pushFrame('chat_message', { role: 'assistant', content: COMPACTED, ts: '2026-09-26T10:00:02Z' })
    pushFrame('chat_message', { role: 'assistant', content: COMPACTED })
    pushFrame('chat_done', { outcome: 'complete' })
    await waitFor(() => expect(occurrences(COMPACTED)).toBe(1))
  })

  it('paints a pass the turn ran on its own history, above the answer that follows it', async () => {
    await openRunning()
    pushFrame('chat_message', { role: 'assistant', content: COMPACTED })
    pushFrame('chat_chunk', { content: 'The failure pattern is a read timeout.', seq: 1 })
    pushFrame('chat_done', { outcome: 'complete' })
    await waitFor(() => expect(occurrences('The failure pattern is a read timeout.')).toBe(1))
    const notice = screen.getByText(COMPACTED, { exact: false })
    const answer = screen.getByText('The failure pattern is a read timeout.', { exact: false })
    expect(notice.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // Two segments, not one: the answer did not stream into the notice's text.
    expect(notice.textContent).not.toContain('read timeout')
  })

  it('empties the transcript when the agent clears the conversation, as a reload shows it', async () => {
    await openRunning()
    // The order the gateway sends them in: the line's announcement, the clear, the notice.
    pushFrame('chat_message', { role: 'assistant', content: 'Conversation cleared.', ts: '2026-09-26T10:00:03Z' })
    pushFrame('session_clear', {})
    pushFrame('chat_message', { role: 'assistant', content: 'Conversation cleared.' })
    pushFrame('chat_done', { outcome: 'complete' })
    await waitFor(() => expect(occurrences('Conversation cleared.')).toBe(1))
    expect(occurrences('first answer')).toBe(0)
  })

  it('paints the notice that the session was restarted at the context threshold, after the turn', async () => {
    await openRunning()
    pushFrame('chat_chunk', { content: 'An answer.', seq: 1 })
    pushFrame('chat_done', { outcome: 'complete' })
    pushFrame('chat_message', { role: 'assistant', content: 'Auto-compacted at 71% of the context window.' })
    await waitFor(() => expect(occurrences('Auto-compacted at 71% of the context window.')).toBe(1))
  })
})

// ── …and the context ring says what the compaction read ────────────────────────────────────────
//
// The ring is the gateway's own reading, the number its compaction acts on. When there is none,
// the dot used to say "no context window is declared for this model" whatever the reason — beside
// "Auto-compacted at 71% of the context window". The gateway names the window with each reading, a
// restart clears only the reading, and a chat opened later is drawn from what the ring was last told.

const ringTitle = () => document.querySelector('[title^="Context"]')?.getAttribute('title') ?? ''

describe('the context ring', () => {
  it('after a restart at the threshold, says it is not measured yet, not that no window exists', async () => {
    await openRunning()
    pushFrame('chat_chunk', { content: 'An answer.', seq: 1 })
    pushFrame('context_usage', { pct: 71, window: 32768 })
    pushFrame('chat_done', { outcome: 'complete' })
    await waitFor(() => expect(ringTitle()).toBe('Context: 71% used'))
    pushFrame('chat_message', { role: 'assistant', content: 'Auto-compacted at 71% of the context window.' })
    pushFrame('context_usage', { pct: null })
    await waitFor(() => expect(ringTitle()).toContain('not measured yet'))
    expect(ringTitle()).toContain('32,768-token window')
    expect(ringTitle()).not.toContain('declared')
  })

  it('is drawn on opening from what it was last told', async () => {
    render(
      <AppearanceProvider>
        <ChatPage sub="chat-7-x" navigate={() => {}} query={{}} setQuery={() => {}} />
      </AppearanceProvider>,
    )
    await waitFor(() => expect(h.detailCalls.length).toBeGreaterThanOrEqual(1))
    await act(async () => { h.detailCalls[0].resolve({ ...h.detail, context_usage: { pct: 40, window: 32768 } }) })
    await waitFor(() => expect(ringTitle()).toBe('Context: 40% used'))
  })

  it('names the fix only when the gateway says no window is declared or served', async () => {
    await openRunning()
    pushFrame('context_usage', { pct: null, window: null })
    await waitFor(() => expect(ringTitle()).toContain('Served context window'))
  })
})
