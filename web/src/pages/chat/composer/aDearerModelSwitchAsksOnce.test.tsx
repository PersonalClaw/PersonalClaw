import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, fireEvent, within } from '@testing-library/react'
import { runState } from '../../../test/runState'

// ── A mid-chat switch to a dearer model asks once, with both prices (#3808) ─────────────────────
//
// A chat's next turn re-reads the whole conversation, so switching its model is paid for at once,
// at the new model's input price. When the gateway says a switch may cost more (`GET
// /api/chat/switch-price`: a context of 50,000 tokens or more, and a model not known to cost no
// more), the agent-and-model pill asks before the switch, with both prices, Switch and Keep current.
// Keep current changes nothing. Once the owner answers Switch in a chat, later switches there go
// straight through. The gateway's answers are fixed per test; what is read is what a person sees and
// the switch the page sends.

const SESSION = 'chat-11-long'

/** What the gateway answers for a switch, by the model asked about. */
const PRICES = {
  120: {
    'large-1': { ask: true, tokens: 120_000, threshold: 50_000, current: { provider: 'work', model: 'small-1', dollars: 0.06 }, next: { provider: 'work', model: 'large-1', dollars: 0.3 } },
    'huge-1': { ask: true, tokens: 120_000, threshold: 50_000, current: { provider: 'work', model: 'large-1', dollars: 0.3 }, next: { provider: 'work', model: 'huge-1', dollars: null } },
  },
  10: {
    'large-1': { ask: false, tokens: 10_000, threshold: 50_000, current: { provider: 'work', model: 'small-1', dollars: 0.005 }, next: { provider: 'work', model: 'large-1', dollars: 0.025 } },
  },
} as const

const h = vi.hoisted(() => ({
  calls: [] as Array<[string, unknown[]]>,
  detail: {} as Record<string, unknown>,
  prices: {} as Record<string, unknown>,
  priceFails: false,
  /** Price reads held back until the test answers them. */
  held: [] as Array<() => void>,
  hold: false,
}))

vi.mock('../../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../../lib/api')>()
  const answers: Record<string, (...args: unknown[]) => unknown> = {
    chatSessionDetail: () => Promise.resolve({ ...h.detail }),
    chatBoundary: () => Promise.resolve({
      sandbox: { confined: true, label: 'Sandboxed', said: 'The commands it runs are sandboxed on this computer.' },
      reach: { tier: 'all', label: 'Network on', said: 'Its web tools reach public hosts.' },
    }),
    chatSwitchPrice: (_s: unknown, _p: unknown, model: unknown) => h.priceFails
      ? Promise.reject(new Error('the gateway is restarting'))
      : h.hold
        ? new Promise((resolve) => { h.held.push(() => resolve(h.prices[String(model)])) })
        : Promise.resolve(h.prices[String(model)]),
    setSessionModel: () => Promise.resolve({ ok: true }),
    models: () => Promise.resolve([
      { name: 'work:small-1', model_name: 'small-1', description: 'small-1', provider: 'work' },
      { name: 'work:large-1', model_name: 'large-1', description: 'large-1', provider: 'work' },
      { name: 'work:huge-1', model_name: 'huge-1', description: 'huge-1', provider: 'work' },
    ]),
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  const api = new Proxy({} as Record<string, unknown>, {
    get(_t, p) {
      if (typeof p !== 'string') return undefined
      return (...args: unknown[]) => {
        h.calls.push([p, args])
        return p in answers ? answers[p](...args) : Promise.resolve([])
      }
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

let ChatPage: typeof import('../../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../../app/appearance')['AppearanceProvider']

const chat = (over: Record<string, unknown> = {}) => ({
  key: SESSION, title: 'Port the parser', run_state: runState({ session: SESSION }), queue: [],
  task_mode: 'agent', approval: 'normal', memory_mode: 'persistent', model: 'work:small-1', workspace_dir: '/home/user/src/parser',
  messages: [
    { role: 'user', content: 'Port the parser to the new grammar.', ts: '2026-10-06T09:00:00+00:00' },
    { role: 'assistant', content: 'The grammar has three new rules to port.', ts: '2026-10-06T09:00:05+00:00' },
  ],
  ...over,
})

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
  h.calls.length = 0
  h.detail = chat()
  h.prices = PRICES[120]
  h.priceFails = false
  h.hold = false
  h.held.length = 0
  ;({ ChatPage } = await import('../../ChatPage'))
  ;({ AppearanceProvider } = await import('../../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 60)) })

async function open() {
  render(<AppearanceProvider><ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} /></AppearanceProvider>)
  await screen.findByText(/three new rules to port/)
  await settle()
}

/** The switches the page sent, as the model each one names. */
const switches = () => h.calls.filter(([n]) => n === 'setSessionModel').map(([, a]) => a[1])
const pill = () => screen.getByRole('button', { name: /^Agent and model:/ })

/** Open the pill and pick the model row `label`, as a pointer does. */
async function pickModel(label: string) {
  fireEvent.click(pill())
  await settle()
  const row = screen.getAllByRole('button').find((b) => (b.textContent ?? '').startsWith(label))
  if (!row) throw new Error(`no ${label} row`)
  fireEvent.click(row)
  await settle()
}
const question = () => screen.queryByRole('alertdialog')
/** The question gone, once the menu's closing has run its course. */
const asked = async () => { await waitFor(() => expect(question()).toBeNull()) }

describe('a switch to a dearer model on a long chat', () => {
  it('asks first, with both prices, and switches only on Switch', async () => {
    await open()
    await pickModel('large-1')
    const q = question()
    expect(q, 'the pill asks before switching').not.toBeNull()
    expect(within(q!).getByText(/^Switch to large-1\?$/)).toBeTruthy()
    expect(q!.textContent).toContain('The next reply re-reads about 120,000 tokens on large-1: about $0.30, against about $0.06 on small-1.')
    expect(switches(), 'nothing is switched while it asks').toEqual([])
    expect(document.activeElement?.textContent, 'Keep current has the focus').toBe('Keep current')
    fireEvent.click(within(q!).getByRole('button', { name: 'Switch' }))
    await settle()
    expect(switches()).toEqual(['work:large-1'])
    await asked()
  })

  it('Keep current switches nothing', async () => {
    await open()
    await pickModel('large-1')
    fireEvent.click(within(question()!).getByRole('button', { name: 'Keep current' }))
    await settle()
    expect(switches()).toEqual([])
    await asked()
  })

  it('once answered Switch, a later switch in the same chat does not ask again', async () => {
    await open()
    await pickModel('large-1')
    fireEvent.click(within(question()!).getByRole('button', { name: 'Switch' }))
    await settle()
    const reads = h.calls.filter(([n]) => n === 'chatSwitchPrice').length
    await asked()
    await pickModel('huge-1')
    await asked()
    expect(switches()).toEqual(['work:large-1', 'work:huge-1'])
    expect(h.calls.filter(([n]) => n === 'chatSwitchPrice').length, 'nor reads the prices').toBe(reads)
  })

  it('a model nothing prices says "price not set"', async () => {
    await open()
    await pickModel('huge-1')
    expect(question()!.textContent).toContain('on huge-1: price not set, against about $0.30 on large-1.')
  })

  it('a price that could not be read is asked about with the reason', async () => {
    h.priceFails = true
    await open()
    await pickModel('large-1')
    expect(question()!.textContent).toMatch(/read what the next reply costs on large-1.*the gateway is restarting/)
    expect(switches()).toEqual([])
  })
})

describe('a switch whose prices are still being read', () => {
  it('waits on them, and a menu closed meanwhile switches nothing when they come', async () => {
    h.prices = PRICES[10]
    h.hold = true
    await open()
    await pickModel('large-1')
    expect(question()!.textContent).toContain('Reading what the next reply costs')
    fireEvent.click(pill())
    await settle()
    await act(async () => { h.held.forEach((answer) => answer()) })
    await settle()
    expect(switches(), 'the owner closed the menu: no switch').toEqual([])
  })
})

describe('a menu closed with Escape while the prices are read', () => {
  it('switches nothing when they come', async () => {
    h.prices = PRICES[10]
    h.hold = true
    await open()
    await pickModel('large-1')
    expect(question()!.textContent).toContain('Reading what the next reply costs')
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    await asked()
    await act(async () => { h.held.forEach((answer) => answer()) })
    await settle()
    expect(switches()).toEqual([])
  })
})

describe('a switch the gateway does not ask about', () => {
  it('on a short chat, switches at once', async () => {
    h.prices = PRICES[10]
    await open()
    await pickModel('large-1')
    await asked()
    expect(switches()).toEqual(['work:large-1'])
  })

  it('back to the model the chat is on, or to Auto, asks nothing and reads nothing', async () => {
    await open()
    await pickModel('small-1')
    await pickModel('Auto')
    await asked()
    expect(h.calls.filter(([n]) => n === 'chatSwitchPrice')).toEqual([])
  })
})
