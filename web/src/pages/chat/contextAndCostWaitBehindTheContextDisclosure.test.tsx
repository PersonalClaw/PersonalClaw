import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, within, fireEvent, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { runState } from '../../test/runState'

// ── Context and cost wait behind the composer's Context disclosure (#3808, #3823) ─────────────
//
// The approved update keeps cost and context collapsed unless the owner pins them: the thin status
// line carries only what is pinned, and the composer warns near the limit. The model pill used to
// wear a context ring and the chat header a cost chip ("$0.0123 · 46k tokens"), both always on, and
// the chip alone gave a started chat a second header row (88 px against 56). Now neither shows: a
// collapsed Context control beside the composer's other turn controls opens the reading (how full
// the window is, or why it is not measured) and what the chat has cost, with a switch that pins the
// reading to the status line. The pin is the browser's, kept across reloads; what the line shows is
// the open chat's live reading, gone when no chat is open, never an old one. From 80 % of the
// window the composer says so in a line of its own, pinned or not.

const SESSION = 'chat-21-context'

const h = vi.hoisted(() => ({
  detail: {} as Record<string, unknown>,
  totals: { input_tokens: 0, output_tokens: 0, cost_usd: 0, turns: 0, priced: true } as Record<string, unknown>,
}))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const answers: Record<string, (...args: unknown[]) => unknown> = {
    chatSessionDetail: () => Promise.resolve({ ...h.detail }),
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
let PinnedContextReading: typeof import('../../app/pinnedContext')['PinnedContextReading']

/** A settled chat whose gateway last read its context as `usage`, and which has cost `cost`. */
function chat(usage: { pct: number | null; window?: number | null } | undefined, cost = { input_tokens: 41_000, output_tokens: 5_000, cost_usd: 0.0123, turns: 3, priced: true }) {
  h.detail = {
    key: SESSION, title: 'Planning the reading digest', run_state: runState({ session: SESSION }), queue: [],
    task_mode: 'agent', approval: 'normal', memory_mode: 'persistent', workspace_dir: '',
    ...(usage ? { context_usage: usage } : {}),
    messages: [
      { role: 'user', content: 'Plan the digest.', ts: '2026-10-06T09:00:00+00:00' },
      { role: 'assistant', content: 'Here is the digest, by day.', ts: '2026-10-06T09:00:05+00:00' },
    ],
  }
  h.totals = cost
}

beforeEach(async () => {
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
  ;({ PinnedContextReading } = await import('../../app/pinnedContext'))
})

afterEach(() => { vi.unstubAllGlobals() })

const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 60)) })

/** The open chat, with the status line's pinned slot beside it as the shell mounts it. */
function page(sub = SESSION) {
  return render(
    <AppearanceProvider>
      <ChatPage sub={sub} navigate={() => {}} query={{}} setQuery={() => {}} />
      <footer aria-label="Status"><PinnedContextReading /></footer>
    </AppearanceProvider>,
  )
}
async function openChat() {
  const view = page()
  await screen.findByText('Here is the digest, by day.')
  await settle()
  return view
}
const contextButton = () => screen.queryByRole('button', { name: 'Context' })
async function openContext() {
  fireEvent.click(contextButton()!)
  return within(await screen.findByRole('dialog', { name: 'Context' }))
}
const statusLine = () => screen.getByRole('contentinfo', { name: 'Status' })

describe('a started chat', () => {
  it('shows no context or cost reading until the Context control is opened', async () => {
    chat({ pct: 34, window: 200_000 })
    await openChat()
    expect(document.body.textContent, 'no cost on the page').not.toMatch(/\$0\.0123/)
    expect(document.body.textContent, 'no reading on the page').not.toMatch(/34\s?%/)
    expect(document.querySelector('[title^="Context:"]'), 'no ring on the model pill').toBeNull()
    expect(screen.queryByTitle(/What this conversation has cost/), 'no cost chip in the header').toBeNull()
    expect(contextButton()).not.toBeNull()
    expect(contextButton()!.getAttribute('aria-expanded')).toBe('false')
  })

  it('opens the reading and the cost behind the Context control', async () => {
    chat({ pct: 34, window: 200_000 })
    await openChat()
    const panel = await openContext()
    expect(contextButton()!.getAttribute('aria-expanded')).toBe('true')
    expect(panel.getByText('34% of the 200,000-token context window used')).toBeTruthy()
    expect(panel.getByRole('progressbar', { name: 'Context used' }).getAttribute('aria-valuenow')).toBe('34')
    expect(panel.getByText('$0.0123 so far, over 46k tokens')).toBeTruthy()
    expect(panel.getByRole('switch', { name: 'Pin to the status line' }).getAttribute('aria-checked')).toBe('false')
    expect(within(statusLine()).queryByText(/Context/), 'nothing pinned').toBeNull()
  })

  it('says why there is no reading, as the ring did', async () => {
    chat({ pct: null, window: 32_768 })
    await openChat()
    const panel = await openContext()
    expect(panel.getByText(/not measured yet/).textContent).toContain('32,768-token window')
    expect(panel.queryByRole('progressbar')).toBeNull()
  })

  it('says a cost that mixes an unpriced model is not totalled, never a confident $0', async () => {
    chat({ pct: 10, window: 200_000 }, { input_tokens: 9_000, output_tokens: 1_000, cost_usd: 0, turns: 1, priced: false })
    await openChat()
    const panel = await openContext()
    expect(panel.getByText(/10k tokens so far/).textContent).toMatch(/no price/)
    expect(panel.queryByText(/\$0\.0000/)).toBeNull()
  })
})

describe('the pin', () => {
  it('puts the open chat\'s reading on the status line, and takes it off again', async () => {
    chat({ pct: 34, window: 200_000 })
    await openChat()
    fireEvent.click((await openContext()).getByRole('switch', { name: 'Pin to the status line' }))
    await waitFor(() => expect(within(statusLine()).getByText('Context 34% · $0.0123 · 46k tokens')).toBeTruthy())
    fireEvent.click(screen.getByRole('switch', { name: 'Pin to the status line' }))
    await waitFor(() => expect(within(statusLine()).queryByText(/Context/)).toBeNull())
  })

  it('is kept across a reload, and shows only a live reading: none once the chat is closed', async () => {
    chat({ pct: 34, window: 200_000 })
    const first = await openChat()
    fireEvent.click((await openContext()).getByRole('switch', { name: 'Pin to the status line' }))
    await waitFor(() => expect(within(statusLine()).getByText(/^Context 34%/)).toBeTruthy())
    first.unmount()
    // A reload: the pin is the browser's; the reading is the chat's, read again.
    chat({ pct: 52, window: 200_000 })
    const second = await openChat()
    await waitFor(() => expect(within(statusLine()).getByText(/^Context 52%/)).toBeTruthy())
    second.rerender(
      <AppearanceProvider>
        <div />
        <footer aria-label="Status"><PinnedContextReading /></footer>
      </AppearanceProvider>,
    )
    await waitFor(() => expect(within(statusLine()).queryByText(/Context/), 'no chat open, no reading').toBeNull())
  })

  it('says a reading is not measured rather than show an old one', async () => {
    localStorage.setItem('pc:status:context-pinned', '1')
    chat({ pct: null, window: 32_768 })
    await openChat()
    await waitFor(() => expect(within(statusLine()).getByText(/^Context not measured yet/)).toBeTruthy())
  })

  it('is mounted by the shell in the status line', () => {
    const app = readFileSync(join(process.cwd(), 'src', 'app', 'App.tsx'), 'utf8')
    expect(app).toMatch(/<StatusLine[^>]*right=\{<PinnedContextReading \/>\}/)
  })
})

describe('near the limit', () => {
  it('says so in the composer from 80 %, pinned or not', async () => {
    chat({ pct: 84, window: 200_000 })
    await openChat()
    const line = screen.getByText("This chat's context is 84% full.")
    expect(line.closest('[role="status"]'), 'said to a screen reader as it appears').not.toBeNull()
  })

  it('says nothing below 80 %', async () => {
    chat({ pct: 79, window: 200_000 })
    await openChat()
    expect(screen.queryByText(/context is \d+% full/)).toBeNull()
  })
})

describe('a new chat', () => {
  it('has no Context control, since there is nothing to read yet', async () => {
    render(<AppearanceProvider><ChatPage sub="" navigate={() => {}} query={{}} setQuery={() => {}} /></AppearanceProvider>)
    await screen.findByRole('button', { name: /^Execution boundary:/ })
    await settle()
    expect(contextButton()).toBeNull()
  })
})
