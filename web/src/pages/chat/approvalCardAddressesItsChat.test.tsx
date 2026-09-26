import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── A chat's approval card answers by the CHAT's id, and ignores another chat's answer ──────────
//
// A pending approval is now ONE registry entry every surface reads (`GET /api/approvals`, Home's
// count and To triage, the phone, the Inbox), and the `approval` frame the card renders IS that
// entry. Its `id` is the REGISTRY id, `<session>:<request_id>`, because a chat's own id is unique
// only inside that chat: an ACP agent's permission request carries the agent's JSON-RPC message
// id, which every connection counts from the same small integers. So two chats can both be
// waiting on "1" at once.
//
// The card must therefore keep addressing its call by the chat's own `request_id` — the id the
// transcript rehydrates and `POST /api/chat/sessions/{session}/approve` takes. Posting the
// registry id there answers nothing (404), and matching `approval_resolved` by a bare id alone
// would let another chat's answer close this card.

const h = vi.hoisted(() => ({
  detail: {
    key: 'chat-a', title: 'chat-a', messages: [] as unknown[], running: true,
    queue: [] as unknown[], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  },
  approve: vi.fn(),
}))

vi.mock('../../lib/api', () => {
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: () => Promise.resolve(h.detail),
    approve: h.approve,
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  // The page reads ~40 endpoints; anything this test does not care about resolves benignly.
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
  h.approve.mockReset().mockResolvedValue({ ok: true })
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

/** The registry's frame for chat-a's approval — exactly what `_hold_approval` broadcasts. */
const APPROVAL = {
  id: 'chat-a:1', request_id: '1', session: 'chat-a', source: '', tool: 'bash',
  tool_input: '{"command": "rm -rf /tmp/scratch"}', tool_purpose: '', risk: 'destructive',
  is_read_only: false, grant_agent: '', agent: 'researcher', session_title: '', ts: 0,
}

async function openWithPendingApproval() {
  render(
    <AppearanceProvider>
      <ChatPage sub="chat-a" navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
  await waitFor(() => expect(FakeSocket.last).toBeTruthy())
  pushFrame('approval', APPROVAL)
  return screen.findByRole('button', { name: /^Deny bash/ })
}

describe('the approval card in the chat it belongs to', () => {
  it('answers with the chat’s own request id, not the registry id', async () => {
    const user = userEvent.setup()
    await user.click(await openWithPendingApproval())
    await waitFor(() => expect(h.approve).toHaveBeenCalledWith('chat-a', 'rejected', '1'))
  })

  it('closes when its approval is answered elsewhere — and only then', async () => {
    await openWithPendingApproval()
    // Another chat waiting on the same bare id "1" is answered: this card must not move.
    pushFrame('approval_resolved', { id: 'chat-b:1', request_id: '1', session: 'chat-b', approved: true, outcome: 'approved' })
    expect(screen.queryByRole('button', { name: /^Deny bash/ })).toBeTruthy()
    // This chat's approval is answered from Home or the phone: the card collapses to the outcome.
    pushFrame('approval_resolved', { id: 'chat-a:1', request_id: '1', session: 'chat-a', approved: false, outcome: 'rejected' })
    await waitFor(() => expect(screen.queryByRole('button', { name: /^Deny bash/ })).toBeNull())
  })

  it('says the turn was stopped, not "denied", when its approval ends with the turn', async () => {
    // The frame a stopped turn sends. The card used to read only `approved: false` and paint
    // "denied" — a decision the user never made.
    await openWithPendingApproval()
    pushFrame('approval_resolved', { id: 'chat-a:1', request_id: '1', session: 'chat-a', approved: false, outcome: 'cancelled' })
    await waitFor(() => expect(screen.queryByRole('button', { name: /^Deny bash/ })).toBeNull())
    expect(screen.getByText(/not run — the turn was stopped/)).toBeTruthy()
    expect(screen.queryByText(/denied/)).toBeNull()
  })
})
