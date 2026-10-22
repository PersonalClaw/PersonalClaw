import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { runState, runningState } from '../test/runState'

// ── The sidebar moves focus as it opens and closes, and never touches the work (#3812) ─────────────
//
// Driven through the real shell. Opening the rail puts focus on the row that last had it, else the
// row the address lights; closing it puts focus back on the sidebar's toggle when focus was in the
// rail, since a collapsed rail drops its thread rows and a shut drawer is inert. Neither move takes
// focus from a field the owner is typing in. The phone's drawer does the same on its toggle and on
// Escape.
//
// Hiding the rail is chrome only: hidden and shown again while a turn and a loop run, the address,
// the lit thread and each thread's state are as they were, and nothing asked the gateway to stop.
//
// jsdom notes (the shape `threadRailInTheShell.test.tsx` established): no gateway socket, every read
// resolves empty unless named below, and the viewport is the `(max-width: 768px)` media query. jsdom
// reports no Mac, so ⌘B reads as Ctrl+B.

vi.mock('../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: () => {},
}))

vi.mock('./identity', async (orig) => {
  const real = await orig<typeof import('./identity')>()
  return {
    ...real,
    useIdentity: () => ({ status: 'ready', name: 'Ada', username: 'ada', onboarded: true, setName: async () => {} }),
  }
})

const SESSIONS = [
  { key: 'chat-1', title: 'Plan the garden', messages: 2, run_state: runningState({ session: 'chat-1' }), origin: 'manual', project_id: 'p-garden' },
  { key: 'chat-2', title: 'Order the soil', messages: 2, run_state: runState({ session: 'chat-2' }), origin: 'manual', project_id: 'p-garden' },
  { key: 'chat-3', title: 'A question about tax', messages: 2, run_state: runState({ session: 'chat-3' }), origin: 'manual', project_id: '' },
]
const LOOPS = [{
  id: 'e5f6a7b8', kind: 'code', name: 'Fence gate', task: 'fix the gate', status: 'running', total_cycles: 2,
  max_cycles: 10, started_at: 1_000, project_id: 'p-garden',
}]
const PROJECTS = [
  { id: 'p-personal', name: 'Personal', is_builtin: true, status: 'active' },
  { id: 'p-garden', name: 'Garden', status: 'active' },
]

function detail(key: string) {
  const s = SESSIONS.find((x) => x.key === key)
  return {
    key, title: s?.title ?? key, queue: [], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
    run_state: runState({ session: key }),
    messages: [
      { role: 'user', content: 'Where do we start?', ts: '2026-10-20T10:01:00+00:00' },
      { role: 'assistant', content: `ANSWER FOR ${key}.`, ts: '2026-10-20T10:01:30+00:00' },
    ],
  }
}

/** Every api method the shell called, in order. */
const calls: string[] = []

const READS: Record<string, unknown> = {
  dashboardConfig: { user_name: 'Ada' },
  agents: { agents: [] },
  skillProposals: { proposals: [], lastReview: null },
  sessionCost: { turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 },
  chatSessions: SESSIONS,
  uLoops: LOOPS,
  projects: PROJECTS,
  chatSessionDetail: detail,
}
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  const stub = new Proxy({}, {
    get: (_t, prop: string) => (...args: unknown[]) => {
      calls.push(prop)
      const read = READS[prop]
      return Promise.resolve(typeof read === 'function' ? read(...args) : prop in READS ? read : [])
    },
  })
  return { ...real, api: stub }
})

const { App } = await import('./App')
const { ThemeProvider } = await import('./theme')
const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider } = await import('./personality')
await Promise.all([import('../pages/dashboard/Home'), import('../pages/ChatPage')])

const renderApp = () => render(
  <ThemeProvider><AppearanceProvider><PersonalityProvider><App /></PersonalityProvider></AppearanceProvider></ThemeProvider>,
)

const rail = () => screen.getByRole('navigation')
const row = (name: RegExp) => within(rail()).findByRole('button', { name })
const lit = () => [...rail().querySelectorAll('[aria-current="page"]')].map((e) => e.getAttribute('aria-label'))

function setViewport(isMobile: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: /max-width:\s*768px/.test(q) ? isMobile : false,
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  }))
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  location.hash = '#/dashboard'
  setViewport(false)
  Element.prototype.scrollIntoView ??= () => {}
  if (typeof globalThis.IntersectionObserver === 'undefined') {
    vi.stubGlobal('IntersectionObserver', class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
      takeRecords(): [] { return [] }
    })
  }
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const CTRL_B = { key: 'b', code: 'KeyB', ctrlKey: true }
const toggle = () => document.querySelector<HTMLElement>('[data-rail-toggle]')!
const focused = () => document.activeElement?.getAttribute('aria-label') ?? document.activeElement?.tagName

describe('the desktop rail', () => {
  it('closing it from a row puts focus on the toggle, and opening it puts focus back on that row', async () => {
    renderApp()
    const soil = await row(/^Order the soil/)
    soil.focus()
    fireEvent.keyDown(window, CTRL_B)
    await waitFor(() => expect(toggle()).toHaveAttribute('aria-label', 'Expand sidebar'))
    expect(document.activeElement, 'the collapsed rail drops its thread rows').toBe(toggle())
    fireEvent.keyDown(window, CTRL_B)
    await waitFor(() => expect(toggle()).toHaveAttribute('aria-label', 'Collapse sidebar'))
    await waitFor(() => expect(focused()).toMatch(/^Order the soil/))
  })

  it('opened with no row remembered, it puts focus on the row the address lights', async () => {
    localStorage.setItem('nav-collapsed', '1')
    location.hash = '#/chat/chat-1'
    const user = userEvent.setup()
    renderApp()
    await user.click(await screen.findByRole('button', { name: 'Expand sidebar' }))
    await waitFor(() => expect(focused()).toMatch(/^Plan the garden/))
    expect(document.activeElement).toHaveAttribute('aria-current', 'page')
  })

  it('never takes focus from a field the owner is typing in', async () => {
    localStorage.setItem('nav-collapsed', '1')
    renderApp()
    await screen.findByRole('button', { name: 'Expand sidebar' })
    const field = document.createElement('input')
    field.setAttribute('aria-label', 'A field outside the rail')
    document.body.appendChild(field)
    field.focus()
    fireEvent.keyDown(field, CTRL_B)
    await waitFor(() => expect(toggle()).toHaveAttribute('aria-label', 'Collapse sidebar'))
    expect(document.activeElement, 'opening').toBe(field)
    fireEvent.keyDown(field, CTRL_B)
    await waitFor(() => expect(toggle()).toHaveAttribute('aria-label', 'Expand sidebar'))
    expect(document.activeElement, 'and closing').toBe(field)
    field.remove()
  })
})

describe("the phone's drawer", () => {
  it('opening it puts focus on a row in it, and Escape puts focus back on the toggle', async () => {
    setViewport(true)
    location.hash = '#/chat/chat-2'
    const user = userEvent.setup()
    renderApp()
    const drawer = () => document.querySelector<HTMLElement>('[role="dialog"][aria-label="Navigation"]')!
    await waitFor(() => expect(drawer()).toHaveAttribute('inert'))
    await user.click(await screen.findByRole('button', { name: 'Expand sidebar' }))
    await waitFor(() => expect(drawer().hasAttribute('inert')).toBe(false))
    await waitFor(() => expect(drawer().contains(document.activeElement)).toBe(true))
    expect(focused()).toMatch(/^Order the soil/)
    await user.keyboard('{Escape}')
    await waitFor(() => expect(drawer()).toHaveAttribute('inert'))
    expect(document.activeElement).toBe(toggle())
  })
})

describe('hiding the rail is chrome only', () => {
  it('hidden and shown again while a turn and a loop run, the work and the lit thread are as they were', async () => {
    location.hash = '#/chat/chat-1'
    renderApp()
    await waitFor(() => expect(lit()).toEqual(['Plan the garden, running']))
    expect(await row(/^Fence gate, running$/)).toBeInTheDocument()
    calls.length = 0
    fireEvent.keyDown(window, CTRL_B)
    await waitFor(() => expect(toggle()).toHaveAttribute('aria-label', 'Expand sidebar'))
    expect(location.hash).toBe('#/chat/chat-1')
    fireEvent.keyDown(window, CTRL_B)
    await waitFor(() => expect(toggle()).toHaveAttribute('aria-label', 'Collapse sidebar'))
    expect(location.hash).toBe('#/chat/chat-1')
    await waitFor(() => expect(lit()).toEqual(['Plan the garden, running']))
    expect(await row(/^Fence gate, running$/)).toBeInTheDocument()
    expect(calls.filter((c) => /stop|cancel|interrupt|abort|pause|kill/i.test(c)), 'nothing asked the gateway to stop').toEqual([])
  })
})
