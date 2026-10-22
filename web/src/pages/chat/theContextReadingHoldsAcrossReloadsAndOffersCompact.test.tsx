import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, within, fireEvent, waitFor } from '@testing-library/react'
import { runState, runningState } from '../../test/runState'

// ── The context reading holds across reloads and reconnects, and near the limit offers Compact ─
//
// #3823's acceptance: pin, unpin, compact, reconnect and an unknown limit each come back right
// after a reload and never show stale data as current; the expanded Context view is fully
// labelled, carries the same totals as the compact reading, and reads without the bar's colours.
// The status line's right side is ambient: the tab's live-update link when it is down, and the
// pinned reading otherwise. While the link is down a live reading may have been missed, so the line
// says the link is reconnecting rather than show the last reading as current; once it is back the
// chat reads itself again and the snapshot, newer than any reading said before the drop, wins.
// From 80% the composer's warning line offers the fix: Compact now, the `/compact` the conversation
// part of the context view offers too, and only while no turn is running.

const SESSION = 'chat-23-context'

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
let PinnedContextReading: typeof import('../../app/pinnedContext')['PinnedContextReading']

function chat(usage: { pct: number | null; window?: number | null } | undefined, opts: { running?: boolean } = {}) {
  h.detail = {
    key: SESSION, title: 'Planning the reading digest', queue: [],
    run_state: opts.running ? runningState({ session: SESSION }) : runState({ session: SESSION }),
    task_mode: 'agent', approval: 'normal', memory_mode: 'persistent', workspace_dir: '',
    ...(usage ? { context_usage: usage } : {}),
    messages: [
      { role: 'user', content: 'Plan the digest.', ts: '2026-10-06T09:00:00+00:00' },
      { role: 'assistant', content: 'Here is the digest, by day.', ts: '2026-10-06T09:00:05+00:00' },
    ],
  }
  h.totals = { input_tokens: 41_000, output_tokens: 5_000, cost_usd: 0.0123, turns: 3, priced: true }
}

function pushFrame(type: string, data: Record<string, unknown>) {
  act(() => { FakeSocket.live.onmessage?.({ data: JSON.stringify({ type, data: { session: SESSION, ...data } }) }) })
}

beforeEach(async () => {
  FakeSocket.all = []
  h.sent = []
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

const settle = (ms = 60) => act(async () => { await new Promise((r) => setTimeout(r, ms)) })
function page() {
  return render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} />
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
async function reload(view: ReturnType<typeof render>) {
  view.unmount()
  return openChat()
}
const statusLine = () => within(screen.getByRole('contentinfo', { name: 'Status' }))
/** The Context view, opened, and its text. */
async function contextView() {
  if (!screen.queryByRole('dialog', { name: 'Context' })) fireEvent.click(screen.getByRole('button', { name: 'Context' }))
  return within(await screen.findByRole('dialog', { name: 'Context' }))
}
const pin = () => localStorage.setItem('pc:status:context-pinned', '1')

describe('the expanded Context view', () => {
  it('labels each reading, in words that need no colour, with the totals the pinned line shows', async () => {
    pin()
    chat({ pct: 84, window: 200_000 })
    await openChat()
    const view = await contextView()
    // Each reading under its own label: a term and its definition, so it reads as one in a list.
    for (const label of ['Context window', 'Cost so far']) expect(view.getByText(label).tagName, label).toBe('DT')
    expect(view.getByText(/84% of the 200,000-token context window used, near the limit/)).toBeTruthy()
    expect(view.getByText('$0.0123 so far, over 46k tokens')).toBeTruthy()
    // The compact reading names the same totals.
    expect(statusLine().getByText('Context 84% · $0.0123 · 46k tokens')).toBeTruthy()
  })
})

describe('near the limit', () => {
  it('offers Compact now beside the warning, which sends /compact', async () => {
    chat({ pct: 84, window: 200_000 })
    await openChat()
    const line = screen.getByText("This chat's context is 84% full.").closest('[role="status"]') as HTMLElement
    fireEvent.click(within(line.parentElement!).getByRole('button', { name: 'Compact now' }))
    await waitFor(() => expect(h.sent).toContain('/compact'))
  })

  it('offers no Compact while a turn is running', async () => {
    chat({ pct: 84, window: 200_000 }, { running: true })
    await openChat()
    expect(screen.getByText("This chat's context is 84% full.")).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Compact now' })).toBeNull()
  })
})

describe('after a reload', () => {
  it('shows the reading the compaction left, not the one before it', async () => {
    pin()
    chat({ pct: 84, window: 200_000 })
    const first = await openChat()
    expect(statusLine().getByText(/^Context 84%/)).toBeTruthy()
    chat({ pct: 21, window: 200_000 })
    await reload(first)
    await waitFor(() => expect(statusLine().getByText(/^Context 21%/)).toBeTruthy())
    expect(screen.queryByText(/context is \d+% full/)).toBeNull()
    expect((await contextView()).getByText(/21% of the 200,000-token context window used/)).toBeTruthy()
  })

  it('says an unknown limit is unknown, on the line and in the view', async () => {
    pin()
    chat({ pct: null, window: null })
    const first = await openChat()
    await reload(first)
    await waitFor(() => expect(statusLine().getByText(/^Context window unknown/)).toBeTruthy())
    expect((await contextView()).getByText(/no context window is declared/)).toBeTruthy()
  })

  it('keeps an unpin', async () => {
    pin()
    chat({ pct: 34, window: 200_000 })
    const first = await openChat()
    fireEvent.click((await contextView()).getByRole('switch', { name: 'Pin to the status line' }))
    await waitFor(() => expect(statusLine().queryByText(/Context/)).toBeNull())
    await reload(first)
    await settle()
    expect(statusLine().queryByText(/Context/)).toBeNull()
  })
})

describe('across a reconnect', () => {
  it('says the link is reconnecting instead of a reading that may be stale, then reads the chat again', async () => {
    pin()
    chat({ pct: 34, window: 200_000 })
    await openChat()
    pushFrame('context_usage', { pct: 50, window: 200_000 })
    await waitFor(() => expect(statusLine().getByText(/^Context 50%/)).toBeTruthy())
    act(() => FakeSocket.live.drop())
    await waitFor(() => expect(statusLine().getByText('Reconnecting live updates…')).toBeTruthy())
    expect(statusLine().queryByText(/^Context 50%/), 'no reading said as current while frames may be lost').toBeNull()
    // While the link was down the gateway compacted the chat: the snapshot after the reconnect
    // holds the newer reading, and it wins over the live one said before the drop.
    chat({ pct: 12, window: 200_000 })
    await waitFor(() => expect(statusLine().getByText(/^Context 12%/)).toBeTruthy(), { timeout: 3000 })
    expect(statusLine().queryByText('Reconnecting live updates…')).toBeNull()
  })
})
