import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

// ── The shell carries one status line, on every page, and the connection lives in it ───────────
//
// #3823: the gateway connection was a dot in the shell's top-right corner cluster, inside the main
// area beside every page header. It moves to one status line at the bottom of the window, mounted
// once by the shell and outside the routed page, so no page header and no Home band carries it.
// Focus mode hides the sidebar and the side pane (`web/REDESIGN.md` §4) and nothing else: the line
// stays, and a lost connection still escalates to its banner. On a phone the line stays too, with
// the connection only (the line's own rule, `theStatusLineSaysWhenTheGatewayIsLost.test.tsx`).
//
// jsdom notes (the shape `navDisclosure.test.tsx` established): no gateway socket, every read
// resolves empty unless named below, and the Tools page is a stand-in, since what is under test is
// the shell around a page, not the page.

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

const gateway = vi.hoisted(() => ({ up: true }))
const ENVELOPES: Record<string, unknown> = {
  dashboardConfig: { user_name: 'Ada' },
  agents: { agents: [] },
  skillProposals: { proposals: [], lastReview: null },
  sessionCost: { turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 },
}
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  const stub = new Proxy({}, {
    get: (_t, prop: string) => () => {
      if (prop === 'system') return gateway.up ? Promise.resolve({ hostname: 'studio.example.test', os: 'Darwin', arch: 'arm64' }) : Promise.reject(new TypeError('Failed to fetch'))
      return Promise.resolve(prop in ENVELOPES ? ENVELOPES[prop] : [])
    },
  })
  return { ...real, api: stub }
})

vi.mock('../pages/tools/ToolsPage', async () => {
  const { createElement: h } = await import('react')
  return { ToolsPage: () => h('h1', null, 'Probe page') }
})

const { App } = await import('./App')
const { ThemeProvider } = await import('./theme')
const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider } = await import('./personality')
await Promise.all([import('../pages/tools/ToolsPage'), import('../pages/dashboard/DashboardPage'), import('../pages/ChatPage')])

const renderApp = () => render(
  <ThemeProvider><AppearanceProvider><PersonalityProvider><App /></PersonalityProvider></AppearanceProvider></ThemeProvider>,
)

const FOCUS_KEY = { key: '\\', code: 'Backslash', ctrlKey: true }
const rail = () => document.querySelector('nav[data-tour="rail"]')
const statusLines = () => screen.queryAllByRole('contentinfo', { name: 'Status' })
const connection = /^System status — /

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
  gateway.up = true
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

/** The one status line, once the shell has read the gateway. */
async function theLine(): Promise<HTMLElement> {
  await waitFor(() => expect(statusLines()).toHaveLength(1))
  const line = statusLines()[0]
  await within(line).findByRole('button', { name: gateway.up ? 'System status — Gateway connected' : /^System status — Gateway disconnected/ })
  return line
}

describe('the status line in the shell', () => {
  it.each([
    ['Home', '#/dashboard'],
    ['a new chat', '#/chat/new'],
    ['Tools', '#/tools'],
  ])('🔴 one line on %s, outside the page, and the connection is in it and nowhere else', async (_name, hash) => {
    location.hash = hash
    renderApp()
    const line = await theLine()

    const main = document.querySelector('main')!
    expect(main.contains(line), 'the line is the shell’s, not the page’s').toBe(false)
    expect(within(main).queryByRole('button', { name: connection }), 'no page header, Home band or corner carries the connection').toBeNull()
    expect(screen.getAllByRole('button', { name: connection })).toHaveLength(1)
    // The corner cluster keeps its other controls, and the connection left it.
    const corner = screen.getByRole('button', { name: /^Open terminal/ }).parentElement!
    expect(within(corner).getByRole('button', { name: /^Theme: / })).toBeTruthy()
    expect(within(corner).queryByRole('button', { name: connection })).toBeNull()
  })

  it('stays in focus mode, and a lost connection still escalates to its banner there', async () => {
    location.hash = '#/tools'
    gateway.up = false
    renderApp()
    await screen.findByRole('heading', { name: 'Probe page' })
    await waitFor(() => expect(rail()).not.toBeNull())

    fireEvent.keyDown(window, FOCUS_KEY)
    await waitFor(() => expect(rail()).toBeNull())
    const line = await theLine()
    expect(within(line).getByText(/^Gateway disconnected · /)).toBeTruthy()

    const words = await screen.findByText('The gateway isn’t answering.', {}, { timeout: 4_000 })
    expect(words.closest('[role="alert"]'), 'the banner is an alert').toBeTruthy()
    expect(screen.queryByText(/Incident mode is active/), 'the incident banner keeps its own meaning').toBeNull()
  })

  it('stays on a phone, where the rail is a drawer', async () => {
    setViewport(true)
    location.hash = '#/tools'
    renderApp()
    await screen.findByRole('heading', { name: 'Probe page' })
    const line = await theLine()
    expect(within(line).getByRole('button', { name: 'System status — Gateway connected' })).toBeTruthy()
  })
})
