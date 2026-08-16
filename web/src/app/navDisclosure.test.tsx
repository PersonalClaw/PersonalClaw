import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  STARTER_NAV_IDS, isDisclosed, readNavDisclosure, pinNavSurface, setNavMode, undisclosedCount,
} from './navDisclosure'

// ── Progressive disclosure over the rail, and the one thing that makes it safe ────────────────
//
// The rail hides 13 of its 18 destinations on a fresh install. **Hiding a row must never make a
// surface unreachable**, so the whole atom rests on one property, asserted here by DRIVING the
// real shell rather than by reading source:
//
//     a deep link to a hidden surface RENDERS it, and visiting it PINS it.
//
// Both halves matter and each fails differently. If the shell ever routed through the disclosure
// filter, `#/tools` under starter mode would blank or fall back to the dashboard — and a
// route-string assertion would still pass, because a blank page and a rendered page both
// "navigate". So the assertion is the page's own `h1`. If auto-pin regressed, the rail would
// simply never grow and the product would quietly become a five-page app with thirteen secrets.
//
// Falsified (both mutations, measured):
//   • `rendered` routed through `isDisclosed` (fall back to dashboard when hidden) →
//     "deep-linking a hidden surface renders it" RED: `Unable to find an accessible element with
//     the role "heading" and name "Tools"`.
//   • auto-pin made a no-op (`return` before `pinNav`) → 3 RED, incl. "…and pins it into the
//     rail": `expect(pinned).toContain('tools')` / `Unable to find … link "Tools"`.
//
// jsdom notes: the WS liveness hook has no gateway to connect to and its reconnect backoff would
// outlive the test, and every `api.*` read is stubbed to resolve empty — the pages under test
// here render their own chrome regardless, which is exactly what "did the surface render" needs.

vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

// An onboarded operator: `onboarded` gates the whole shell (an un-onboarded one gets the
// full-screen first-run flow and no rail at all).
vi.mock('./identity', async (orig) => {
  const real = await orig<typeof import('./identity')>()
  return {
    ...real,
    useIdentity: () => ({ name: 'Ada', onboarded: true, loaded: true, setName: async () => {}, clearName: async () => {} }),
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

/** The shell in the provider stack `main.tsx` gives it (identity is mocked above). Anything
 *  less and `useAppearance()` hands back null and every page crashes on mount — which would
 *  look exactly like "the surface did not render". */
const renderApp = () => render(
  <ThemeProvider><AppearanceProvider><PersonalityProvider><App /></PersonalityProvider></AppearanceProvider></ThemeProvider>,
)

/** The rail is the app's one `navigation` landmark. */
const rail = () => screen.getByRole('navigation')
const railLinks = () => within(rail()).getAllByRole('button').map((b) => b.getAttribute('aria-label'))

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

// ─────────────────────────────────────────────────────────────────────────────────────────────
describe('the disclosure store', () => {
  it('an install with NO record keeps its full rail (the upgrade marker)', () => {
    // C4 asks for an "onboarding-completed-before-this-version" marker. The record's ABSENCE is
    // it: only `Onboarding`'s finish step writes one, so no record means the install was
    // onboarded before this shipped and must not lose surfaces it has been using.
    expect(readNavDisclosure()).toEqual({ mode: 'expert', pinned: [] })
  })

  it('a record written by onboarding starts on the starter rail', () => {
    setNavMode('starter')
    expect(readNavDisclosure().mode).toBe('starter')
  })

  it('a corrupt or partial record falls back without throwing', () => {
    localStorage.setItem('nav-disclosure', '{not json')
    expect(readNavDisclosure()).toEqual({ mode: 'expert', pinned: [] })
    // A record that exists but names no mode was still written by this app, so it means
    // "onboarded under this version" — starter, with the garbage pins dropped.
    localStorage.setItem('nav-disclosure', '{"pinned":[1,"loops",null]}')
    expect(readNavDisclosure()).toEqual({ mode: 'starter', pinned: ['loops'] })
  })

  it('pinning is idempotent and survives a fresh read', () => {
    setNavMode('starter')
    pinNavSurface('tools')
    pinNavSurface('tools')
    expect(readNavDisclosure().pinned).toEqual(['tools'])
  })

  it('pinning does not silently flip an expert install back to starter', () => {
    // `pinNavSurface` writes the WHOLE record, so it has to carry the mode it read — otherwise
    // the first pin on an install with no record would mint `{pinned:[…]}`, which reads back as
    // starter, and an upgraded user's rail would collapse behind them.
    pinNavSurface('tools')
    expect(readNavDisclosure().mode).toBe('expert')
  })

  it('isDisclosed: starter shows the starter set, pins, and app tiles — nothing else', () => {
    for (const id of STARTER_NAV_IDS) expect(isDisclosed(id, 'starter', [])).toBe(true)
    expect(isDisclosed('tools', 'starter', [])).toBe(false)
    expect(isDisclosed('tools', 'starter', ['tools'])).toBe(true)
    // A contributed app's tile is already an explicit per-app pin (`nav-apps`); disclosure has
    // nothing to reveal for it and must not undo that choice.
    expect(isDisclosed('app/weather', 'starter', [])).toBe(true)
    // Expert shows everything, including a surface nobody pinned.
    expect(isDisclosed('tools', 'expert', [])).toBe(true)
  })

  it('undisclosedCount counts only what starter holds back', () => {
    const ids = [...STARTER_NAV_IDS, 'tools', 'learning', 'app/weather']
    expect(undisclosedCount(ids, [])).toBe(2)
    expect(undisclosedCount(ids, ['tools'])).toBe(1)
  })
})

// ─────────────────────────────────────────────────────────────────────────────────────────────
describe('the rail a fresh install sees', () => {
  beforeEach(() => setNavMode('starter'))

  it('shows the starter surfaces and holds the rest back', async () => {
    renderApp()
    const names = await waitFor(() => {
      const n = railLinks()
      expect(n.length).toBeGreaterThan(3)
      return n
    })
    for (const label of ['Home', 'Chat', 'Inbox', 'Store', 'Settings']) expect(names).toContain(label)
    // The vacuity guard for every assertion below: something really is hidden.
    for (const label of ['Learning', 'Tools', 'Terminal', 'Workflows']) expect(names).not.toContain(label)
  })

  it('drops the section headers, which have nothing left to group', async () => {
    // Measured on the live starter rail before this: five rows under three headings, with
    // "PLATFORM" sitting over Inbox alone and "APPS" over Store alone. A heading per item is
    // chrome, not structure — the starter rail is one curated group by construction.
    renderApp()
    await waitFor(() => expect(railLinks()).toContain('Store'))
    expect(rail().textContent).not.toMatch(/PLATFORM|CAPABILITIES|APPS/i)
  })

  it('says how many surfaces it is holding back, in the control\'s own name', async () => {
    renderApp()
    const more = await screen.findByRole('button', { name: /^Everything, show \d+ more surfaces$/ })
    expect(more).toHaveAttribute('aria-expanded', 'false')
    // The count must ride the accessible NAME: an aria-label OVERRIDES the element's text, so a
    // number living only in the `+N` span is announced nowhere (the defect the badge above it
    // already carries a comment about).
    expect(more.getAttribute('aria-label')).toMatch(/show 1[0-9] more surfaces/)
    // The global focus ring in this app is `:focus-visible { outline }`, which jsdom does not
    // compute — so what is assertable is that the control does not KILL it, and that it is
    // reachable and not nested inside another control.
    expect(more.className).not.toContain('outline-none')
    expect(more.closest('button')).toBe(more)
    more.focus()
    expect(more).toHaveFocus()
  })

  it('renders NO control once nothing is left to reveal', async () => {
    // The vacuity guard, asserted as BEHAVIOUR rather than as source — a control reading
    // "Everything +0" is a button that appears to do something and does not.
    //
    // Every non-starter rail id, pinned. That makes this a ratchet too: add a rail destination
    // without either putting it in STARTER_NAV_IDS or listing it here and this test reds,
    // which is the moment to classify it.
    localStorage.setItem('nav-disclosure', JSON.stringify({
      mode: 'starter',
      pinned: ['projects', 'knowledge', 'tasks', 'triggers', 'files', 'artifacts', 'terminal',
        'agents', 'tools', 'skills', 'learning', 'prompts', 'workflows'],
    }))
    renderApp()
    await waitFor(() => expect(railLinks()).toContain('Workflows'))
    expect(screen.queryByRole('button', { name: /^Everything, show/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /^Show fewer/ })).toBeNull()
  })
})

// ─────────────────────────────────────────────────────────────────────────────────────────────
describe('a hidden surface is hidden from the RAIL, never from the app', () => {
  beforeEach(() => setNavMode('starter'))

  it('deep-linking a hidden surface renders it — and pins it into the rail', async () => {
    // The clause the whole atom rests on. `#/tools` is a Capabilities surface, so under
    // starter mode it has no rail row at all.
    expect(isDisclosed('tools', 'starter', [])).toBe(false)
    location.hash = '#/tools'
    renderApp()

    // 1. It RENDERED. Asserted on the page's own h1, not on the route string — a blank page and
    //    a rendered page both "navigate", and a disclosure filter applied to the route would
    //    produce the blank one (or a silent fall back to the dashboard).
    expect(await screen.findByRole('heading', { name: 'Tools', level: 1 })).toBeInTheDocument()

    // 2. It PINNED. This is what makes hiding safe: the rail grows with use.
    await waitFor(() => expect(readNavDisclosure().pinned).toContain('tools'))

    // 3. And the pin reached the rail, not just the store.
    await waitFor(() => expect(railLinks()).toContain('Tools'))
  })

  it('the pin survives a reload', async () => {
    location.hash = '#/tools'
    renderApp()
    await waitFor(() => expect(readNavDisclosure().pinned).toContain('tools'))
    cleanup()

    // A fresh mount at a DIFFERENT route — the row is there because it was persisted, not
    // because the surface happens to be on screen.
    location.hash = '#/dashboard'
    renderApp()
    await waitFor(() => expect(railLinks()).toContain('Tools'))
  })

  it('the command palette offers every surface, including the hidden ones', async () => {
    const user = userEvent.setup()
    renderApp()
    await waitFor(() => expect(railLinks()).not.toContain('Tools'))

    await user.keyboard('{Meta>}k{/Meta}')
    const search = await screen.findByLabelText('Search pages and actions')
    await user.type(search, 'Tools')
    // Present in the palette even though the rail has no row for it — the always-open door.
    const hit = await screen.findByRole('option', { name: /^Tools/ })
    await user.click(hit)

    // Reached through the palette, the surface renders and pins exactly as a deep link does.
    expect(await screen.findByRole('heading', { name: 'Tools', level: 1 })).toBeInTheDocument()
    await waitFor(() => expect(railLinks()).toContain('Tools'))
  })
})

// ─────────────────────────────────────────────────────────────────────────────────────────────
describe('expert mode', () => {
  it('the rail\'s own control expands everything, permanently', async () => {
    setNavMode('starter')
    const user = userEvent.setup()
    renderApp()
    const more = await screen.findByRole('button', { name: /^Everything, show \d+ more surfaces$/ })
    await user.click(more)

    await waitFor(() => {
      const names = railLinks()
      for (const label of ['Learning', 'Tools', 'Terminal', 'Workflows', 'Prompts', 'Agents']) {
        expect(names).toContain(label)
      }
    })
    // Persisted, so "permanently" is not just this render.
    expect(readNavDisclosure().mode).toBe('expert')
    // And the section headers come back with the surfaces they group.
    expect(rail().textContent).toMatch(/Platform/i)
    expect(rail().textContent).toMatch(/Capabilities/i)
    // And the same control collapses again — a disclosure that only opens leaves a user who
    // expanded out of curiosity with no local undo.
    expect(await screen.findByRole('button', { name: /^Show fewer, hide \d+ surfaces$/ }))
      .toHaveAttribute('aria-expanded', 'true')
  })

  it('does not auto-pin, so "show fewer" still means something', async () => {
    // In expert mode nothing is hidden, so a visit reveals nothing — and pinning everything
    // browsed while expanded would silently empty the starter rail's difference and turn the
    // toggle into a one-way door.
    setNavMode('expert')
    location.hash = '#/tools'
    renderApp()
    expect(await screen.findByRole('heading', { name: 'Tools', level: 1 })).toBeInTheDocument()
    expect(readNavDisclosure().pinned).toEqual([])
  })

  it('an upgraded install shows every surface from the first paint', async () => {
    // No record at all — the pre-this-version install. Nothing may disappear on them.
    localStorage.clear()
    renderApp()
    await waitFor(() => {
      const names = railLinks()
      for (const label of ['Home', 'Chat', 'Learning', 'Tools', 'Terminal', 'Workflows', 'Settings']) {
        expect(names).toContain(label)
      }
    })
    expect(screen.queryByRole('button', { name: /^Everything, show/ })).toBeNull()
  })
})

// ─────────────────────────────────────────────────────────────────────────────────────────────
describe('at a mobile viewport', () => {
  it('the control is in the drawer, named, and operable from the keyboard', async () => {
    // The rail is an overlay DRAWER on a phone — `inert` until the shell toggle opens it — so
    // the control has to be reachable through that path too. And mobile-only is NOT
    // keyboard-exempt: `useIsMobile` is a `max-width: 768px` media query, not a touch test, so
    // a narrow desktop window with a real keyboard gets this exact drawer.
    setViewport(true)
    setNavMode('starter')
    const user = userEvent.setup()
    renderApp()
    await user.click(await screen.findByRole('button', { name: 'Expand sidebar' }))

    const more = await screen.findByRole('button', { name: /^Everything, show \d+ more surfaces$/ })
    expect(more).toHaveAttribute('aria-expanded', 'false')
    more.focus()
    expect(more).toHaveFocus()
    await user.keyboard('{Enter}')

    await waitFor(() => expect(readNavDisclosure().mode).toBe('expert'))
    await waitFor(() => expect(railLinks()).toContain('Tools'))
  })
})

// ─────────────────────────────────────────────────────────────────────────────────────────────
describe('the control converges on the rail\'s own motion, so reduced-motion is inherited', () => {
  it('adds no bespoke animation', () => {
    // Nothing here needs a reduced-motion special case, and that is the point: the app root
    // wraps everything in `MotionConfig reducedMotion="user"` (which swaps framer transforms for
    // a fade) and tokens.css neutralises CSS transitions under `prefers-reduced-motion`. Both
    // only reach a control that uses the house primitives — a hand-rolled keyframe or an inline
    // duration would sail past both, which is what this asserts against.
    const src = readFileSync(join(process.cwd(), 'src/ui/NavRail.tsx'), 'utf8')
    const at = src.indexOf('aria-expanded={disclosure.expanded}')
    expect(at, 'the disclosure control must still be in NavRail').toBeGreaterThan(-1)
    const block = src.slice(at - 400, at + 1600)
    expect(block, 'same tap spring as every nav item').toContain('transition={spring.spatialFast}')
    expect(block, 'the chevron rotates through the shared CSS transition').toContain("'transition-transform'")
    expect(block, 'no hand-rolled keyframe or duration').not.toMatch(/duration:|animate=\{\{|keyframes/)
  })
})

// ─────────────────────────────────────────────────────────────────────────────────────────────
describe('the Appearance toggle', () => {
  it('is reachable at #/settings/design and changes the real rail', async () => {
    // The done_when's "expert-mode toggle in Appearance". Driven through the shell rather than
    // by rendering the panel in isolation, because the thing worth asserting is that ONE
    // setting moves the ONE rail — a panel-local test would pass with the two wired to
    // different stores.
    setNavMode('starter')
    const user = userEvent.setup()
    location.hash = '#/settings/design'
    renderApp()

    // A generous window: the settings page is code-split and this find waits on that chunk.
    const sw = await screen.findByRole('switch', { name: 'Show every surface' }, { timeout: 5000 })
    expect(sw).toHaveAttribute('aria-checked', 'false')
    await user.click(sw)

    await waitFor(() => expect(railLinks()).toContain('Workflows'))
    expect(readNavDisclosure().mode).toBe('expert')
    // And back — the rail keeps the starter set, so turning it off is not a trap either.
    await user.click(await screen.findByRole('switch', { name: 'Show every surface' }))
    await waitFor(() => expect(railLinks()).not.toContain('Workflows'))
    expect(railLinks()).toContain('Settings')
  })
})
