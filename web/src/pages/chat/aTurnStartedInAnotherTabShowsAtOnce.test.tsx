import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'

// ── A chat open in a second tab shows a turn the moment it starts ──────────────────────────────
//
// Two tabs on one chat: the tab that sends paints the question and its Stop control itself, and
// the other one joins the turn by reading the chat once. It joined on the turn's first ANSWER
// frame — a word of the reply, a thought, a tool card — so while the model had not started
// answering it showed nothing of the turn at all: not the question, not "Thinking…", not Stop.
// A local model busy with other work can take minutes to say its first word, or time out first.
// The turn's opening status line ("Thinking…") is sent as the turn starts, so the other tab joins
// on it.

const h = vi.hoisted(() => {
  const idle = {
    key: 'chat-9-x', title: 'chat-9-x', running: false,
    messages: [
      { role: 'user', content: 'earlier question', ts: '2026-09-30T09:00:00Z' },
      { role: 'assistant', content: 'earlier answer', ts: '2026-09-30T09:00:05Z' },
    ] as unknown[],
    queue: [] as unknown[], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  }
  return { idle, detailCalls: [] as { resolve: (d: unknown) => void }[], chatSessionDetail: vi.fn() }
})

vi.mock('../../lib/api', () => {
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    sendChat: () => Promise.resolve({ ok: true, session: 'chat-9-x' }),
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
  act(() => { FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type, data }) }) })
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

/** The chat, open and idle, as a second tab has it before another tab sends. */
async function openIdle() {
  render(
    <AppearanceProvider>
      <ChatPage sub="chat-9-x" navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
  await waitFor(() => expect(h.detailCalls.length).toBe(1))
  await act(async () => { h.detailCalls[0].resolve(h.idle) })
  await waitFor(() => expect(screen.queryByText('earlier answer')).not.toBeNull())
  expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull()
}

describe('a turn another tab started in this chat', () => {
  it('shows its question and Stop as soon as it starts, before the model says a word', async () => {
    await openIdle()
    // All the gateway has sent of the turn so far: its opening status. No answer frame yet.
    pushFrame('chat_status', { session: 'chat-9-x', status: 'Thinking…' })
    await waitFor(() => expect(h.detailCalls.length).toBe(2))
    await act(async () => {
      h.detailCalls[1].resolve({
        ...h.idle,
        running: true,
        messages: [...h.idle.messages, { role: 'user', content: 'which stage is the run on?', ts: '2026-09-30T09:05:00Z' }],
      })
    })
    await waitFor(() => expect(screen.queryByText('which stage is the run on?')).not.toBeNull())
    expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeNull()
  })

  it('reads nothing for a turn in another chat', async () => {
    await openIdle()
    pushFrame('chat_status', { session: 'chat-3-other', status: 'Thinking…' })
    await act(async () => { await new Promise((r) => setTimeout(r, 20)) })
    expect(h.detailCalls.length).toBe(1)
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull()
  })
})
