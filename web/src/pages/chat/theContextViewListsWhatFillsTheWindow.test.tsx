import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, within, fireEvent, waitFor } from '@testing-library/react'
import { runState } from '../../test/runState'

// ── The Context view lists what fills the window, and /context prints the same (#3823) ─────────
//
// The approved addition: the context reading opens a list of each measured part with its share
// and its action: instructions and tools (review the tools), memory and profile (review what is
// recalled), attached files, the conversation and tool results (compact). The parts come from the
// gateway with the `context_usage` reading, counted where the request is assembled, never in the
// browser. A part the gateway did not measure reads "not measured". `/context` in the composer
// prints the same list, in the same order, from the same reading, so the two always agree.

const SESSION = 'chat-24-parts'

const h = vi.hoisted(() => ({
  detail: {} as Record<string, unknown>,
  totals: { input_tokens: 0, output_tokens: 0, cost_usd: 0, turns: 0, priced: true } as Record<string, unknown>,
  sent: [] as string[],
}))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const answers: Record<string, (...args: unknown[]) => unknown> = {
    chatSessionDetail: () => Promise.resolve({ ...h.detail }),
    sendChat: (...args: unknown[]) => { h.sent.push(String(args[0])); return Promise.resolve({ ok: true, session: SESSION }) },
    usageTotals: () => Promise.resolve({ since: '', until: '', session: SESSION, totals: { cache_read_tokens: 0, cache_creation_tokens: 0, ...h.totals } }),
    chatBoundary: () => Promise.resolve({
      sandbox: { confined: true, label: 'Sandboxed', said: 'The commands it runs are sandboxed on this computer.' },
      reach: { tier: 'all', label: 'Network on', said: 'Its web tools reach public hosts.' },
    }),
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  const api = new Proxy({} as Record<string, unknown>, {
    get(_t, p) {
      if (typeof p !== 'string') return undefined
      return (...args: unknown[]) => (p in answers ? answers[p](...args) : Promise.resolve([]))
    },
  })
  return { ...real, api }
})

/** The tab's one socket, which a test can drop; `useChatSocket` opens the next one on its backoff. */
class FakeSocket {
  static all: FakeSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 0
  constructor(public url: string) {
    FakeSocket.all.push(this)
    setTimeout(() => { this.readyState = 1; this.onopen?.() }, 0)
  }
  send(): void {}
  close(): void { this.readyState = 3 }
  drop(): void { this.readyState = 3; this.onclose?.() }
  static get live(): FakeSocket { return FakeSocket.all[FakeSocket.all.length - 1] }
}

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']

/** One recorded `context_usage` reading, with the parts the gateway measured. */
const RECORDED = {
  pct: 31, window: 200_000,
  parts: [
    { kind: 'instructions', tokens: 12_000, action: 'tools' },
    { kind: 'memory', tokens: 2_000, action: 'memory' },
    { kind: 'conversation', tokens: 40_000, action: 'compact' },
    { kind: 'tool_results', tokens: 8_000, action: 'compact' },
  ],
}

function chat(usage: Record<string, unknown>) {
  h.detail = {
    key: SESSION, title: 'Planning the reading digest', queue: [], run_state: runState({ session: SESSION }),
    task_mode: 'agent', approval: 'normal', memory_mode: 'persistent', workspace_dir: '', context_usage: usage,
    messages: [
      { role: 'user', content: 'Plan the digest.', ts: '2026-10-06T09:00:00+00:00' },
      { role: 'assistant', content: 'Here is the digest, by day.', ts: '2026-10-06T09:00:05+00:00' },
    ],
  }
  h.totals = { input_tokens: 41_000, output_tokens: 5_000, cost_usd: 0.0123, turns: 3, priced: true }
}

const navigated: string[] = []

beforeEach(async () => {
  FakeSocket.all = []
  h.sent = []
  navigated.length = 0
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }))))
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

const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 60)) })
async function openChat(query: Record<string, string> = {}) {
  render(<AppearanceProvider><ChatPage sub={SESSION} navigate={(p: string) => { navigated.push(p) }} query={query} setQuery={() => {}} /></AppearanceProvider>)
  await screen.findByText('Here is the digest, by day.')
  await settle()
}
async function viewRows(): Promise<string[]> {
  fireEvent.click(screen.getByRole('button', { name: 'Context' }))
  const view = await screen.findByRole('dialog', { name: 'Context' })
  return [...view.querySelectorAll('li[data-part]')].map((li) => li.querySelector('span')!.textContent!.trim())
}
/** What `/context` printed in the chat: its list, line by line. */
async function printedRows(): Promise<string[]> {
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
  const heading = await screen.findByRole('heading', { name: 'Context' })
  const answer = heading.parentElement!
  return [...answer.querySelectorAll('li')].map((li) => li.textContent!.trim())
}

describe('one recorded reading', () => {
  it('lists every part with its share, in order, and /context prints the same rows', async () => {
    chat(RECORDED)
    await openChat({ seed: '/context' })
    const listed = await viewRows()
    expect(listed).toEqual([
      'Instructions and tools: 12,000 tokens, 6% of the window',
      'Memory and profile: 2,000 tokens, 1% of the window',
      'Attached files: not measured',
      'Conversation: 40,000 tokens, 20% of the window',
      'Tool results: 8,000 tokens, 4% of the window',
    ])
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    expect(await printedRows()).toEqual(listed)
    expect(h.sent, '/context is answered here, never sent to the model').not.toContain('/context')
  })

  it('offers each measured part its action, and none for a part not measured', async () => {
    chat(RECORDED)
    await openChat()
    fireEvent.click(screen.getByRole('button', { name: 'Context' }))
    const view = within(await screen.findByRole('dialog', { name: 'Context' }))
    const row = (kind: string) => within(document.querySelector(`li[data-part="${kind}"]`) as HTMLElement)
    expect(row('files').queryByRole('button')).toBeNull()
    fireEvent.click(row('instructions').getByRole('button', { name: 'Review tools' }))
    expect(navigated).toContain('tools')
    fireEvent.click(screen.getByRole('button', { name: 'Context' }))
    await screen.findByRole('dialog', { name: 'Context' })
    fireEvent.click(row('memory').getByRole('button', { name: 'Review what’s recalled' }))
    expect(navigated).toContain('memory?tab=recall')
    fireEvent.click(screen.getByRole('button', { name: 'Context' }))
    await screen.findByRole('dialog', { name: 'Context' })
    fireEvent.click(row('conversation').getByRole('button', { name: 'Compact now' }))
    await waitFor(() => expect(h.sent).toContain('/compact'))
    void view
  })
})

describe('a reading without parts', () => {
  it('says every part is not measured, in the view and in /context', async () => {
    chat({ pct: 31, window: 200_000 })
    await openChat({ seed: '/context' })
    const listed = await viewRows()
    expect(listed).toHaveLength(5)
    for (const line of listed) expect(line).toMatch(/: not measured$/)
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    expect(await printedRows()).toEqual(listed)
  })

  it('takes a live reading\'s parts and drops them with a cleared reading', async () => {
    chat({ pct: 31, window: 200_000 })
    await openChat()
    act(() => { FakeSocket.live.onmessage?.({ data: JSON.stringify({ type: 'context_usage', data: { session: SESSION, ...RECORDED, pct: 33 } }) }) })
    expect((await viewRows())[0]).toBe('Instructions and tools: 12,000 tokens, 6% of the window')
    act(() => { FakeSocket.live.onmessage?.({ data: JSON.stringify({ type: 'context_usage', data: { session: SESSION, pct: null } }) }) })
    await waitFor(() => expect(document.querySelector('li[data-part="instructions"] span')!.textContent).toMatch(/not measured$/))
  })
})
