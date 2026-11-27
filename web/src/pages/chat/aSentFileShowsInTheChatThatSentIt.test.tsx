import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, within } from '@testing-library/react'
import { runState } from '../../test/runState'

// ── A file the agent sends shows in the chat that sent it ────────────────────────────────────────
//
// Measured from the code: the gateway kept a sent file (`notify_attachment`) as a `file` row and
// told the page with a `file_ready` frame, and the page read neither: no branch drew a `file` row,
// no case read the frame. So the agent said "File sent" and nothing showed, live or after a reload.
// The gateway now writes the row into the chat whose turn sent it, and sends the frame to that
// chat only; the page draws each as a card under the answer, with Download.
// Driven through the real ChatPage.

const SESSION = 'chat-4-export'

const h = vi.hoisted(() => ({ chatSessionDetail: vi.fn() }))

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

const frame = (type: string, data: Record<string, unknown>) => {
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
  sessionStorage.clear()
  localStorage.clear()
  FakeSocket.last = null
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

const QUESTION = { role: 'user', content: 'Export this as a CSV and send it to me.', ts: 't-user' }
const ANSWER = { role: 'assistant', content: 'Here is the export.', ts: 't-a1' }
const SENT = { filename: 'export.csv', path: '/outbox/export.csv', description: 'the totals by city', size: 2048, content_type: 'text/csv' }

const chat = (messages: Record<string, unknown>[]) => ({
  key: SESSION, title: 'Export', queue: [], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  messages, run_state: runState(), stream_seq: 0,
})

async function open(messages: Record<string, unknown>[]) {
  h.chatSessionDetail.mockReset().mockResolvedValue(chat(messages))
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
  await screen.findByText('Here is the export.')
  await waitFor(() => expect(FakeSocket.last).not.toBeNull())
}

const cards = () => screen.queryByRole('list', { name: 'Files sent in this turn' })

describe('a file the agent sends shows in the chat that sent it', () => {
  it('🔴 a sent file the chat keeps is drawn as its card, with Download, after a reload', async () => {
    await open([QUESTION, ANSWER, { role: 'file', content: JSON.stringify(SENT), ts: 't-f1' }])

    const list = await screen.findByRole('list', { name: 'Files sent in this turn' })
    expect(within(list).getByText('export.csv')).toBeTruthy()
    expect(within(list).getByText('the totals by city · 2 KB')).toBeTruthy()
    const download = within(list).getByRole('link', { name: 'Download export.csv' })
    expect(download.getAttribute('href')).toContain('/api/outbox/export.csv')
    expect(download.getAttribute('download')).toBe('export.csv')
  })

  it('🔴 a live file_ready for this chat adds its card once; one for another chat adds none', async () => {
    await open([QUESTION, ANSWER])
    expect(cards()).toBeNull()

    frame('file_ready', { ...SENT, session: 'chat-9-other', ts: 't-x' })
    expect(cards()).toBeNull()

    frame('file_ready', { ...SENT, session: SESSION, ts: 't-f1' })
    frame('file_ready', { ...SENT, session: SESSION, ts: 't-f1' })
    const list = await screen.findByRole('list', { name: 'Files sent in this turn' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(1)
    expect(within(list).getByText('export.csv')).toBeTruthy()
  })
})
