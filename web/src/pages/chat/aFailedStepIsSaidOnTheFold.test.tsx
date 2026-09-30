import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

// ── A failed step says so on the folded work, before anyone opens it ────────────────────────
//
// A finished turn folds its steps into "Worked through N steps · <tools>" so the answer leads.
// Measured before this: a turn whose one real step failed (`ok` false, the error in its result)
// folded to "Worked through 2 steps · automation_create" above a reply saying the watch was set
// up. The red mark was only on the card inside the fold, so the fold agreed with the reply.

const h = vi.hoisted(() => ({ chatSessionDetail: vi.fn() }))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    chatSessions: () => Promise.resolve([]),
    useCaseSettings: () => Promise.resolve({}),
    personalclawConfig: () => Promise.resolve({ voice: {} }),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    chatFolders: () => Promise.resolve([]),
    chatTags: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  const api = new Proxy(base, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = () => Promise.resolve([]))
    },
  })
  return { ...real, api }
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

const SESSION = 'chat-11-x'
const ASKED = { role: 'user', content: 'Set up a watch on the releases page that tells me when 0.29 ships.', ts: '2026-09-26T10:00:00Z' }
const ANSWER = { role: 'assistant', content: 'I have set up an automation to watch the releases page.', ts: '2026-09-26T10:00:09Z' }
const ALLOWED = {
  role: 'permission', content: 'automation_create', ts: '2026-09-26T10:00:03Z',
  meta: { tool_call_id: 'tc-2', resolved: 'approved' },
}
const step = (id: string, tool: string, output: string, failed: boolean) => ({
  role: 'tool', content: tool, ts: '2026-09-26T10:00:05Z',
  meta: { tool_call_id: id, done: true, output, ...(failed ? { ok: false } : {}) },
})

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']

beforeEach(async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => setTimeout(() => cb(0), 0))
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
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

async function openTurn(messages: unknown[]) {
  h.chatSessionDetail.mockReset().mockResolvedValue({
    key: SESSION, title: SESSION, messages, running: false, queue: [], task_mode: 'agent',
    approval: 'normal', memory_mode: 'persistent',
  })
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
  expect(await screen.findByText(ANSWER.content)).toBeTruthy()
  const fold = await waitFor(() => screen.getByRole('button', { name: /Worked through 2 steps/ }))
  expect(fold.getAttribute('aria-expanded')).toBe('false')
  return fold
}

describe('the folded work of a finished turn', () => {
  it('says a step failed without being opened', async () => {
    const fold = await openTurn([
      ASKED,
      ALLOWED,
      step('tc-2', 'automation_create', "Error: query: unknown field for tool 'automation_create'", true),
      ANSWER,
    ])
    expect(fold.textContent).toContain('1 failed')
    expect(fold.textContent).toContain('automation_create')
  })

  it('counts every failed step, and only the failed ones', async () => {
    const fold = await openTurn([
      ASKED,
      step('tc-1', 'web_fetch', 'Error: the page did not answer', true),
      step('tc-2', 'automation_create', "Error: query: unknown field for tool 'automation_create'", true),
      ANSWER,
    ])
    expect(fold.textContent).toContain('2 failed')
  })

  it('says nothing of failure when every step worked', async () => {
    const fold = await openTurn([
      ASKED,
      ALLOWED,
      step('tc-2', 'automation_create', 'Created automation watch-httpx-releases.', false),
      ANSWER,
    ])
    expect(fold.textContent).not.toMatch(/failed/)
  })
})
