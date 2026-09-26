import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { hydrateTurns, type HistMsg } from './chatTypes'

// ── A reply another model wrote says so, under the reply, after a reload too ──────────────────
//
// Measured on main: an agent pinned to a model that does not exist answered on the chat binding,
// and nothing on the chat said so — the reply read as the pinned model's. The backend now says it
// live (an activity line) and stamps the sentence on the reply's `meta.model_substitution`; these
// pin the half that survives a reload: the transcript renders the stamp under the reply it
// describes, word for word, and nothing for a reply the chosen model wrote.

const SENTENCE =
  "Ran on fake-oai:fake-model-1 instead of Researcher's model fake-oai:no-such-model: it is not " +
  'one of the chat models set up in Settings → Models. Pick another model for Researcher on the ' +
  'Agents page, or add it in Settings → Models.'

const msg = (role: string, content: string, meta?: HistMsg['meta']): HistMsg =>
  ({ role, content, ts: `2026-09-26T10:00:0${content.length % 10}+00:00`, ...(meta ? { meta } : {}) })

describe('hydrateTurns — the substitution stays on the reply it describes', () => {
  it('carries the sentence onto the assistant turn', () => {
    const turns = hydrateTurns([msg('user', 'Name a colour.'), msg('assistant', 'Blue.', { model_substitution: SENTENCE })])
    expect(turns.find((t) => t.role === 'assistant')?.modelSubstitution).toBe(SENTENCE)
  })

  it('leaves a reply the chosen model wrote unmarked', () => {
    const turns = hydrateTurns([msg('user', 'Name a colour.'), msg('assistant', 'Blue.')])
    expect(turns.find((t) => t.role === 'assistant')?.modelSubstitution).toBeUndefined()
  })
})

const h = vi.hoisted(() => ({
  chatSessionDetail: vi.fn(),
  navigate: vi.fn(),
}))

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api')
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
  return { ...actual, api }
})

class FakeSocket {
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  constructor(public url: string) { setTimeout(() => this.onopen?.(), 0) }
  send(): void {}
  close(): void { this.readyState = 3 }
}

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']

beforeEach(async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  Element.prototype.scrollIntoView = vi.fn() as unknown as Element['scrollIntoView']
  if (typeof globalThis.IntersectionObserver === 'undefined') {
    vi.stubGlobal('IntersectionObserver', class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
      takeRecords(): [] { return [] }
    })
  }
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

function openChat(messages: unknown[]) {
  h.chatSessionDetail.mockReset().mockResolvedValue({
    key: 'chat-7-x', title: 'Pinned agent', messages, running: false, queue: [],
    task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  })
  render(
    <AppearanceProvider>
      <ChatPage sub="chat-7-x" navigate={h.navigate} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
}

describe('the chat, reopened', () => {
  it('says under the reply which model wrote it instead of the chosen one', async () => {
    openChat([
      { role: 'user', content: 'Name a colour.', ts: '2026-09-26T10:00:00+00:00' },
      { role: 'assistant', content: 'Blue.', ts: '2026-09-26T10:00:05+00:00', meta: { model_substitution: SENTENCE } },
    ])
    const note = await screen.findByTestId('model-substitution-note')
    expect(note).toHaveTextContent(SENTENCE)
  })

  it('says nothing under a reply the chosen model wrote', async () => {
    openChat([
      { role: 'user', content: 'Name a colour.', ts: '2026-09-26T10:00:00+00:00' },
      { role: 'assistant', content: 'Blue.', ts: '2026-09-26T10:00:05+00:00' },
    ])
    await waitFor(() => expect(screen.getByText('Blue.')).toBeInTheDocument())
    expect(screen.queryByTestId('model-substitution-note')).not.toBeInTheDocument()
  })
})
