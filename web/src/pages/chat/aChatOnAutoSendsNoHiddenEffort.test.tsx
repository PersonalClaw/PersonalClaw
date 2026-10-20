import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import { runState } from '../../test/runState'

// ── A chat on Auto sends no hidden effort level ─────────────────────────────────────────────────
//
// The composer offers a reasoning effort only for a model that declares its levels, and Auto names
// no model, so it declares none. A chat that stored a level before that (every native model offered
// Low to Max then) kept it: its effort row was gone, so nothing showed the level or could change it,
// and every turn still ran at it. A chat opened on Auto with a stored level now runs at Default: the
// composer writes Default to the chat, as a switch to Auto does, and the pill says so.

const SESSION = 'chat-11-x'

const h = vi.hoisted(() => ({
  detail: {} as Record<string, unknown>,
  models: [] as unknown[],
  chatSessionDetail: vi.fn(),
  setReasoningEffort: vi.fn(),
}))

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api')
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    setReasoningEffort: h.setReasoningEffort,
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [{ name: 'researcher' }] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve(h.models),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  // The page reads ~40 endpoints; anything not named above resolves to a shape-agnostic value.
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

/** A chat with one answered turn, on the agent and model `over` names. */
function chat(over: Record<string, unknown>) {
  return {
    key: SESSION, title: 'Trip notes', run_state: runState({ session: SESSION }), queue: [] as unknown[],
    task_mode: 'agent', approval: 'normal', memory_mode: 'persistent', agent: 'researcher',
    messages: [
      { role: 'user', content: 'Where did we land on the ferry times?', ts: '2026-10-06T09:00:00+00:00' },
      { role: 'assistant', content: 'The 7:40 sailing, back on the 18:10.', ts: '2026-10-06T09:00:05+00:00' },
    ],
    ...over,
  }
}

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
  h.models = []
  h.chatSessionDetail.mockReset().mockImplementation(() => Promise.resolve({ ...h.detail }))
  h.setReasoningEffort.mockReset().mockResolvedValue({ ok: true, reasoning_effort: '' })
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

async function open(detail: Record<string, unknown>) {
  h.detail = chat(detail)
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
  await screen.findByText(/The 7:40 sailing/)
}

describe('a chat on Auto sends no hidden effort level', () => {
  it('a chat stored on Auto with High is set back to Default, and the pill says so', async () => {
    await open({ model: '', reasoning_effort: 'high' })
    await waitFor(() => expect(h.setReasoningEffort).toHaveBeenCalledWith(SESSION, ''))
    expect(h.setReasoningEffort).toHaveBeenCalledTimes(1)
    expect(screen.getByText('Effort set to Default: Auto has no High')).toBeTruthy()
  })

  it('a chat on a model that declares its level keeps it, and nothing is written', async () => {
    h.models = [{ name: 'Acme:deep', model_name: 'Deep', provider: 'Acme', description: '', efforts: ['low', 'high'] }]
    await open({ model: 'Acme:deep', reasoning_effort: 'high' })
    // The chat's own binding and the model list are both in: the pill names the model.
    await screen.findByRole('button', { name: 'Agent and model: researcher · Deep' })
    await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
    expect(h.setReasoningEffort).not.toHaveBeenCalled()
    expect(screen.queryByText(/^Effort set to Default/)).toBeNull()
  })
})
