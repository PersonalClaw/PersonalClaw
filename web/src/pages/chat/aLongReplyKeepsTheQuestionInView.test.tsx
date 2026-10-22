import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, fireEvent, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { runState, runningState } from '../../test/runState'

// ── A long reply keeps the question in view, when the owner asks for it (#3820) ────────────────
//
// An opt-in setting, "Keep my question in view" (Settings › Chat, off by default): while a reply
// taller than the window streams, the reading column pins a one-line banner with the owner's
// latest question. Clicking it scrolls to the question; it leaves when the reply settles or the
// question is back in view. Static: no animation of its own. A reply taller than the window is one
// that has scrolled its question out above the view, which the column's IntersectionObserver says.

const SESSION = 'chat-25-question'
const QUESTION = 'Compare the three storage engines for a write-heavy log.\nInclude their compaction costs.'

const h = vi.hoisted(() => ({
  detail: {} as Record<string, unknown>,
  totals: { input_tokens: 0, output_tokens: 0, cost_usd: 0, turns: 0, priced: true } as Record<string, unknown>,
  sent: [] as string[],
  keep: false,
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
    dashboardConfig: () => Promise.resolve({ send_on_enter: true, keep_question_in_view: h.keep }),
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

/** The column's IntersectionObserver, which the test tells whether the question is in view. */
const io = vi.hoisted(() => ({ observers: [] as { cb: IntersectionObserverCallback; els: Element[] }[] }))
class FakeIO {
  private entry: { cb: IntersectionObserverCallback; els: Element[] }
  constructor(cb: IntersectionObserverCallback) { this.entry = { cb, els: [] }; io.observers.push(this.entry) }
  observe(el: Element) { this.entry.els.push(el) }
  unobserve() {}
  disconnect() { this.entry.els = [] }
  takeRecords() { return [] }
}
/** Say the question's node is out of view above (`true`) or back in view. */
function questionScrolledOut(out: boolean) {
  act(() => {
    for (const o of io.observers) {
      const els = o.els.filter((el) => el.getAttribute('data-transcript-turn') === 'user')
      if (!els.length) continue
      o.cb(els.map((target) => ({
        target, isIntersecting: !out, intersectionRatio: out ? 0 : 1,
        boundingClientRect: { top: out ? -400 : 40 } as DOMRectReadOnly,
        rootBounds: { top: 0 } as DOMRectReadOnly,
      }) as unknown as IntersectionObserverEntry), {} as IntersectionObserver)
    }
  })
}

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']

function streamingChat() {
  h.detail = {
    key: SESSION, title: 'Storage engines', queue: [], run_state: runningState({ session: SESSION }),
    task_mode: 'agent', approval: 'normal', memory_mode: 'persistent', workspace_dir: '',
    messages: [
      { role: 'user', content: QUESTION, ts: '2026-10-06T09:00:00+00:00' },
      { role: 'assistant', content: 'LSM trees first. ' , ts: '2026-10-06T09:00:05+00:00' },
    ],
  }
}

beforeEach(async () => {
  FakeSocket.all = []
  io.observers = []
  h.keep = false
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  vi.stubGlobal('IntersectionObserver', FakeIO as unknown as typeof IntersectionObserver)
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }))))
  Element.prototype.scrollIntoView ??= () => {}
  sessionStorage.clear()
  localStorage.clear()
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 60)) })
async function openChat() {
  render(<AppearanceProvider><ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} /></AppearanceProvider>)
  await screen.findByText(/LSM trees first/)
  await settle()
}
const banner = () => screen.queryByRole('button', { name: /^Your question: / })

describe('with the setting on', () => {
  beforeEach(() => { h.keep = true })

  it('pins the question\'s first line while a reply that scrolled it out streams', async () => {
    streamingChat()
    await openChat()
    expect(banner(), 'not while the question is in view').toBeNull()
    questionScrolledOut(true)
    await waitFor(() => expect(banner()).not.toBeNull())
    expect(banner()!.textContent).toContain('Compare the three storage engines for a write-heavy log.')
    expect(banner()!.textContent).not.toContain('Include their compaction costs')
  })

  it('scrolls to the question on a click', async () => {
    streamingChat()
    await openChat()
    questionScrolledOut(true)
    const scrolled = vi.fn()
    const node = document.querySelector('[data-transcript-turn="user"]') as HTMLElement
    node.scrollIntoView = scrolled
    fireEvent.click(await waitFor(() => banner()!))
    expect(scrolled).toHaveBeenCalled()
  })

  it('leaves once the question is back in view', async () => {
    streamingChat()
    await openChat()
    questionScrolledOut(true)
    await waitFor(() => expect(banner()).not.toBeNull())
    questionScrolledOut(false)
    await waitFor(() => expect(banner()).toBeNull())
  })

  it('leaves once the reply settles', async () => {
    streamingChat()
    await openChat()
    questionScrolledOut(true)
    await waitFor(() => expect(banner()).not.toBeNull())
    act(() => { FakeSocket.live.onmessage?.({ data: JSON.stringify({ type: 'chat_done', data: { session: SESSION, outcome: 'complete' } }) }) })
    await waitFor(() => expect(banner()).toBeNull())
  })
})

describe('with the setting off', () => {
  it('pins nothing, however long the reply', async () => {
    streamingChat()
    await openChat()
    questionScrolledOut(true)
    await settle()
    expect(banner()).toBeNull()
  })
})

describe('a settled chat', () => {
  it('pins nothing even with the setting on', async () => {
    h.keep = true
    streamingChat()
    h.detail = { ...h.detail, run_state: runState({ session: SESSION }) }
    await openChat()
    questionScrolledOut(true)
    await settle()
    expect(banner()).toBeNull()
  })
})

describe('the Settings toggle', () => {
  it('busts the chat\'s read of it, so turning it on reaches an open chat at once', () => {
    const panel = readFileSync(join(process.cwd(), 'src', 'pages', 'settings', 'ChatPanel.tsx'), 'utf8')
    expect(panel).toMatch(/invalidateKeys\('chat:keep-question-in-view'\)/)
    const reader = readFileSync(join(process.cwd(), 'src', 'pages', 'chat', 'transcript', 'QuestionInView.tsx'), 'utf8')
    expect(reader).toMatch(/useQuery\('chat:keep-question-in-view'/)
  })
})
