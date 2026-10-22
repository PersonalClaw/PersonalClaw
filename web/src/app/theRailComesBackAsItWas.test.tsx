import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { runState } from '../test/runState'
import type { Command } from './CommandPalette'

// ── The sidebar comes back as it was, after a reload or a collapse (#3812) ────────────────────────
//
// The rail's open state, its width, each project's disclosure and how far it was scrolled are kept on
// this device, and the thread it lights is the address's. Driven through the real shell: the rail is
// toggled from the toolbar, the keyboard and the palette, resized by its keyboard handle, a group is
// closed and the rail scrolled; a reload then opens it exactly so. A collapse keeps the full rail's
// place: opening it again, before or after a reload, comes back to the same rows.
//
// jsdom lays nothing out, so the scroll area is given a size by hand (600 px showing 2,000 px of
// rows) and keeps the scroll position it is set to. jsdom reports no Mac, so ⌘B reads as Ctrl+B.

vi.mock('../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: () => {},
}))

/** The rows the shell hands ⌘K. */
let rows: readonly Command[] = []
vi.mock('./CommandPalette', async (orig) => {
  const real = await orig<typeof import('./CommandPalette')>()
  return { ...real, CommandPalette: ({ commands }: { commands: Command[] }) => { rows = commands; return null } }
})

vi.mock('./identity', async (orig) => {
  const real = await orig<typeof import('./identity')>()
  return {
    ...real,
    useIdentity: () => ({ status: 'ready', name: 'Ada', username: 'ada', onboarded: true, setName: async () => {} }),
  }
})

const SESSIONS = [
  { key: 'chat-1', title: 'Plan the garden', messages: 2, run_state: runState({ session: 'chat-1' }), origin: 'manual', project_id: 'p-garden' },
  { key: 'chat-2', title: 'Order the soil', messages: 2, run_state: runState({ session: 'chat-2' }), origin: 'manual', project_id: 'p-garden' },
  { key: 'chat-3', title: 'A question about tax', messages: 2, run_state: runState({ session: 'chat-3' }), origin: 'manual', project_id: '' },
]
const LOOPS = [{
  id: 'e5f6a7b8', kind: 'code', name: 'Fence gate', task: 'fix the gate', status: 'failed', total_cycles: 2,
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
await Promise.all([import('../pages/dashboard/DashboardPage'), import('../pages/ChatPage')])

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
const scroller = () => document.querySelector<HTMLElement>('[data-rail-scroll]')!
const railWidth = () => document.querySelector<HTMLElement>('nav[data-tour="rail"]')!.style.width
const expanded = () => toggle().getAttribute('aria-label') === 'Collapse sidebar'
const group = (name: RegExp) => within(rail()).getByRole('button', { name })

/** The scroll area's size and position, which jsdom does not lay out: 600 px showing 2,000. */
const scrolled = new WeakMap<Element, number>()
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockImplementation(function (this: HTMLElement) { return this.hasAttribute('data-rail-scroll') ? 2000 : 0 })
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function (this: HTMLElement) { return this.hasAttribute('data-rail-scroll') ? 600 : 0 })
  Object.defineProperty(HTMLElement.prototype, 'scrollTop', {
    configurable: true,
    get(this: HTMLElement) { return scrolled.get(this) ?? 0 },
    set(this: HTMLElement, v: number) { scrolled.set(this, v) },
  })
})
afterEach(() => { vi.restoreAllMocks(); delete (HTMLElement.prototype as { scrollTop?: number }).scrollTop })

/** Scrolls the rail as the owner's wheel does. */
function scrollRailTo(y: number) {
  scroller().scrollTop = y
  fireEvent.scroll(scroller())
}

/** The palette's own row for the sidebar, run as ⌘K runs it. */
const paletteToggle = () => act(() => rows.find((r) => r.label === 'Show or hide the sidebar')!.run())

describe('the sidebar after a collapse', () => {
  it('opened again, it keeps the search as typed and what it found, the address and the lit thread', async () => {
    location.hash = '#/chat/chat-2'
    const user = userEvent.setup()
    renderApp()
    await row(/^Order the soil/)
    await user.type(within(rail()).getByRole('searchbox', { name: 'Search threads' }), 'soil')
    await waitFor(() => expect(within(rail()).queryByRole('button', { name: /^Plan the garden/ })).toBeNull())

    await user.click(toggle())
    await waitFor(() => expect(expanded()).toBe(false))
    await user.click(toggle())
    await waitFor(() => expect(expanded()).toBe(true))
    expect(within(rail()).getByRole('searchbox', { name: 'Search threads' })).toHaveValue('soil')
    await row(/^Order the soil/)
    expect(within(rail()).queryByRole('button', { name: /^Plan the garden/ }), 'still what the search found').toBeNull()
    expect(location.hash).toBe('#/chat/chat-2')
    expect(lit()).toEqual(['Order the soil'])
  })

  it('a reload starts with no search', async () => {
    location.hash = '#/chat/chat-2'
    const user = userEvent.setup()
    const view = renderApp()
    await row(/^Order the soil/)
    await user.type(within(rail()).getByRole('searchbox', { name: 'Search threads' }), 'soil')
    view.unmount()
    renderApp()
    await row(/^Plan the garden/)
    expect(within(rail()).getByRole('searchbox', { name: 'Search threads' })).toHaveValue('')
  })
})

describe('the sidebar after a reload', () => {
  it('toggled from the toolbar, the keyboard and the palette, then reloaded: open, as wide, as scrolled, its groups as they were', async () => {
    location.hash = '#/chat/chat-2'
    const user = userEvent.setup()
    const view = renderApp()
    await row(/^Order the soil/)
    // Wider by two of its handle's steps, the "No project" group closed, scrolled 300 px down.
    const handle = screen.getByRole('separator', { name: /Resize navigation/ })
    handle.focus()
    await user.keyboard('{ArrowRight}{ArrowRight}')
    const wide = railWidth()
    expect(wide).not.toBe('')
    await user.click(group(/^No project, /))
    expect(group(/^No project, /)).toHaveAttribute('aria-expanded', 'false')
    scrollRailTo(300)

    await user.click(toggle())
    await waitFor(() => expect(expanded()).toBe(false))
    fireEvent.keyDown(window, CTRL_B)
    await waitFor(() => expect(expanded()).toBe(true))
    paletteToggle()
    await waitFor(() => expect(expanded()).toBe(false))
    paletteToggle()
    await waitFor(() => expect(expanded()).toBe(true))
    await waitFor(() => expect(scroller().scrollTop, 'opening again comes back to the same rows').toBe(300))

    view.unmount()
    renderApp()
    await row(/^Order the soil/)
    expect(expanded()).toBe(true)
    expect(railWidth()).toBe(wide)
    expect(lit()).toEqual(['Order the soil'])
    expect(group(/^No project, /)).toHaveAttribute('aria-expanded', 'false')
    expect(group(/^Garden, /)).toHaveAttribute('aria-expanded', 'true')
    await waitFor(() => expect(scroller().scrollTop).toBe(300))
  })

  it('reloaded while collapsed it stays collapsed, and opening it comes back to where the full rail was', async () => {
    location.hash = '#/chat/chat-2'
    const view = renderApp()
    await row(/^Order the soil/)
    scrollRailTo(420)
    fireEvent.keyDown(window, CTRL_B)
    await waitFor(() => expect(expanded()).toBe(false))
    // The collapsed rail's short scroll area moving does not forget the full rail's place.
    scrollRailTo(0)

    view.unmount()
    renderApp()
    await waitFor(() => expect(toggle()).toHaveAttribute('aria-label', 'Expand sidebar'))
    fireEvent.keyDown(window, CTRL_B)
    await waitFor(() => expect(expanded()).toBe(true))
    await row(/^Order the soil/)
    await waitFor(() => expect(scroller().scrollTop).toBe(420))
  })
})
