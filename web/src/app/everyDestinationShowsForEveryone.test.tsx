import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── Every destination shows for everyone (#3812) ──────────────────────────────────────────────────
//
// The sidebar was two rails: a starter set of five for a fresh install, which grew a row for each
// surface visited and hid the rest behind an "Everything" row, and every row for an install from
// before it. The sidebar is a hierarchy now, short enough to hold whole, so it is one rail: every
// destination, under its sections, for a fresh install and an old one alike. The record the
// starter rail kept (`nav-disclosure`) is no longer read or written, so a browser that still holds
// one, of any shape, sees the same sidebar as one that never had it.
//
// The phone's drawer carries the companion as well, and a desktop rail does not.

vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

// An onboarded operator: `onboarded` gates the whole shell (an un-onboarded one gets the
// full-screen first-run flow and no rail at all).
vi.mock('./identity', async (orig) => {
  const real = await orig<typeof import('./identity')>()
  return {
    ...real,
    useIdentity: () => ({ status: 'ready', name: 'Ada', username: 'ada', onboarded: true, setName: async () => {} }),
  }
})

// Every gateway read resolves empty. A Proxy rather than a hand-listed stub: the shell and its
// pages call a dozen endpoints between them and a missing one would throw inside a render, which
// reads as "the surface did not render" — the exact failure this file is trying to detect.
//
// 🪤 A LIST IS NOT THE ONLY EMPTY. `[]` for every endpoint left three shell-level reads throwing
// `Cannot read properties of undefined (reading 'filter')` — from promises that settled AFTER the
// test unmounted, so they surfaced as unhandled errors at the end of the WHOLE suite rather than
// as a failing test here. The envelope-shaped reads are therefore named; anything else is `[]`.
const ENVELOPES: Record<string, unknown> = {
  dashboardConfig: { user_name: 'Ada' },
  agents: { agents: [] },       // useComposerData reads `.agents.filter(…)`
  skillProposals: { proposals: [], lastReview: null }, // ActionCenter maps `.proposals`
}
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  const stub = new Proxy({}, {
    get: (_t, prop: string) => () => Promise.resolve(prop in ENVELOPES ? ENVELOPES[prop] : []),
  })
  return { ...real, api: stub }
})

// Imported AFTER the mocks so the shell picks them up.
const { App } = await import('./App')
const { ThemeProvider } = await import('./theme')
const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider } = await import('./personality')

// Every route in `App.tsx` is `lazy(() => import(…))`, so a deep link's first paint waits on a
// module transform, not on React. In isolation that lands in milliseconds; under the full suite
// (700 jsdom instances, #2951) it lands well past any `findBy` window, and the failure reads as
// "the surface never rendered" when the surface was only late. Resolving the routes these tests
// deep-link into HERE puts them in the module cache, so `lazy` settles in a microtask and each
// find below waits on the render it is actually asserting about. This changes no assertion.
await Promise.all([
  import('../pages/settings/SettingsPage'),
  import('../pages/triggers/TriggersSection'),
  import('../pages/dashboard/DashboardPage'),
])

/** The shell in the provider stack `main.tsx` gives it (identity is mocked above). Anything
 *  less and `useAppearance()` hands back null and every page crashes on mount — which would
 *  look exactly like "the surface did not render". */
const renderApp = () => render(
  <ThemeProvider><AppearanceProvider><PersonalityProvider><App /></PersonalityProvider></AppearanceProvider></ThemeProvider>,
)

/** The rail is the app's one `navigation` landmark. */
const rail = () => screen.getByRole('navigation')
const railLinks = () => within(rail()).getAllByRole('button').map((b) => b.getAttribute('aria-label'))
/** The rail's section headers: NAV's section names, as text outside every control. */
const sectionHeaders = () => [...rail().querySelectorAll('div')]
  .filter((d) => d.children.length === 0 && !d.closest('button'))
  .map((d) => d.textContent ?? '')
  .filter((t) => ['Platform', 'Capabilities', 'Apps'].includes(t))

/** Point `matchMedia('(max-width: 768px)')` at a fixed answer — jsdom has no layout, so the
 *  media query IS the viewport (the shape `ui/DegradedChip.test.tsx` established). */
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
})
afterEach(cleanup)

/** What a fresh install's first-run flow wrote, and what a browser that had visited a few pages kept. */
const RECORDS = {
  'a fresh install': JSON.stringify({ mode: 'starter', pinned: [] }),
  'a starter rail with pins': JSON.stringify({ mode: 'starter', pinned: ['tasks', 'knowledge', 'triggers', 'terminal'] }),
  'an expert rail': JSON.stringify({ mode: 'expert', pinned: [] }),
  'a corrupt record': '{not json',
}

/** Every destination the sidebar holds, in its order. */
const EVERY_ROW = ['Home', 'Chat', 'Projects', 'Library', 'Inbox', 'Automations', 'Capabilities', 'Settings']

async function rows(): Promise<string[]> {
  return waitFor(() => {
    const n = railLinks().filter((l): l is string => !!l && EVERY_ROW.includes(l.split(', ')[0])).map((l) => l.split(', ')[0])
    expect(n).toEqual(EVERY_ROW)
    return n
  })
}

describe('every destination shows for everyone', () => {
  it('with no record, every destination under its sections, and no control to show fewer', async () => {
    renderApp()
    expect(await rows()).toEqual(EVERY_ROW)
    expect(sectionHeaders()).toEqual(['Platform'])
    expect(within(rail()).queryByRole('button', { name: /^(Everything|Show fewer)/ })).toBeNull()
  })

  it.each(Object.entries(RECORDS))('%s left behind changes nothing', async (_name, record) => {
    localStorage.setItem('nav-disclosure', record)
    renderApp()
    expect(await rows()).toEqual(EVERY_ROW)
    expect(sectionHeaders()).toEqual(['Platform'])
  })

  it('a visit writes no record', async () => {
    const writes = vi.spyOn(Storage.prototype, 'setItem')
    location.hash = '#/triggers'
    renderApp()
    expect(await screen.findByRole('heading', { name: 'Automations', level: 1 })).toBeTruthy()
    expect(writes.mock.calls.filter(([key]) => key === 'nav-disclosure')).toEqual([])
    writes.mockRestore()
  })
})

describe('at a mobile viewport', () => {
  it('the drawer holds every destination and the phone companion, so a phone never has to type its address', async () => {
    // A paired phone that opened the full dashboard from its companion had no way back but
    // typing `#/companion`: the route is a full-screen shell route, outside NAV by design.
    setViewport(true)
    const user = userEvent.setup()
    renderApp()
    await user.click(await screen.findByRole('button', { name: 'Expand sidebar' }))
    for (const name of EVERY_ROW) expect(await within(rail()).findByRole('button', { name: new RegExp(`^${name}(,|$)`) })).toBeTruthy()
    await user.click(await within(rail()).findByRole('button', { name: 'Companion' }))
    await waitFor(() => expect(location.hash).toBe('#/companion'))
    expect(await screen.findByRole('heading', { name: 'Companion' })).toBeTruthy()
  })

  it('a desktop rail has no companion row: it is not a desktop destination', async () => {
    renderApp()
    await rows()
    expect(railLinks()).not.toContain('Companion')
  })
})
