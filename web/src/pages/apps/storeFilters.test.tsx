import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { artGradient, artStops, artHash } from './appArt'
import { findHeaderFilter, headerFilter } from '../../test/headerFilter'

// ── The Store's categories and sources, and the two things it is easy to ship broken ──────────
//
// The Store's categories and sources filter its grid in place, so they are sections of its header
// filter, at every width. They were a rail beside the grid on a wide viewport, and the filter
// carried them only on a narrow one: one filter at two widths, which the header now makes one.
//
// 1. A FILTER THAT NOTHING RENDERS. Every assertion below drives `AppsSection`, the component
//    `app/App.tsx` routes `case 'apps'` to, and the first test pins that route.
//
// 2. A SELECTION THAT LIVES IN COMPONENT STATE. "Survives reload via the URL" cannot be proven by
//    reading a state variable, so the URL test reads the URL back out of a fake router and mounts
//    a fresh component from it, then asserts the grid is filtered and the row is chosen.
//
// 🪤 `useIsMobile` is a `matchMedia('(max-width: 768px)')` query, and jsdom has no layout, so the
// media query is the observable input for "wide" vs "narrow". The suite default is the WIDE
// branch; `setNarrow()` flips it. Both are asserted.

const SRC = join(process.cwd(), 'src')

const NOTES = {
  name: 'notes', displayName: 'Notes', description: 'Take notes', version: '1.0.0',
  icon: 'ClipboardList', heroUrl: 'https://example.test/notes.png', author: 'core',
  source: '', sourceKind: 'bundled', isProvider: false, providerType: '', tags: ['productivity'],
}
/** No `heroUrl` and no `icon` — the degraded manifest the generated-art path exists for. */
const TIMER = {
  name: 'timer', displayName: 'Timer', description: 'Count down', version: '1.0.0',
  icon: '', author: 'core',
  source: '', sourceKind: 'bundled', isProvider: false, providerType: '', tags: ['utility'],
}
/** Lives under the registered local source `/srv/apps`, so it folds into its own rail entry. */
const LEDGER = {
  name: 'ledger', displayName: 'Ledger', description: 'Money in, money out', version: '0.2.0',
  icon: 'Database', author: 'me',
  source: '/srv/apps/ledger', sourceKind: 'local', isProvider: false, providerType: '', tags: ['productivity'],
}

const CATALOG = {
  bundled: [NOTES, TIMER],
  gitSources: [], localSources: ['/srv/apps'],
  localApps: [LEDGER], remoteApps: [], gitApps: [],
}

function mockApi() {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      discover: () => Promise.resolve({ enabled: false, areas: [] }), // Discover off: no tip under the empty state
      apps: () => Promise.resolve([]),
      appCatalog: () => Promise.resolve(CATALOG),
    },
  }))
}

/** A stand-in for the hash router. `setQuery` folds the patch into REAL
 *  `URLSearchParams`, and `search()` renders what a reload would restore from — so the
 *  test can read the URL back rather than trusting a spy's argument. */
function makeRouter(search = 'view=store') {
  const params = new URLSearchParams(search)
  return {
    get query(): Record<string, string> { return Object.fromEntries(params.entries()) },
    setQuery(patch: Record<string, string | null | undefined>) {
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === undefined || v === '') params.delete(k)
        else params.set(k, v)
      }
    },
    search() { return params.toString() },
  }
}

async function mount(router: ReturnType<typeof makeRouter>) {
  const { AppsSection } = await import('./AppsSection')
  const r = render(<AppsSection query={router.query} setQuery={router.setQuery} navigate={() => {}} />)
  // The catalog read is async; the grid has cards once it resolves.
  await waitFor(() => expect(screen.getAllByRole('button', { name: /— details$/ }).length).toBeGreaterThan(0))
  return r
}

/** Point `matchMedia('(max-width: 768px)')` at "narrow". Mirrors `DegradedChip.test.tsx`. */
function setNarrow() {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: /max-width:\s*768px/.test(q),
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  }))
}

/** The cards the grid is currently showing, by name. `RowHitTarget` gives every card an
 *  "<name> — details" button, so this is a census of the GRID, not of the rail. */
function cards(): string[] {
  return screen.getAllByRole('button', { name: /— details$/ })
    .map((b) => (b.getAttribute('aria-label') ?? '').replace(/ — details$/, ''))
    .sort()
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear(); mockApi() })
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

/** The header filter's section titled `title`, the filter opened if it is not: its rows. */
async function section(title: string): Promise<HTMLElement> {
  const trigger = await findHeaderFilter()
  if (trigger.getAttribute('aria-expanded') !== 'true') await userEvent.click(trigger)
  return screen.findByRole('group', { name: title })
}
/** A section's rows, by their words (the count stripped). */
const rows = (el: HTMLElement) => within(el).getAllByRole('button').map((b) => (b.textContent ?? '').replace(/\d+$/, '').trim())
/** The rows a section has chosen, as a screen reader hears them: pressed. */
const chosen = (el: HTMLElement) => within(el).queryAllByRole('button', { pressed: true })
  .map((b) => (b.textContent ?? '').replace(/\d+$/, '').trim())

// ── The call site ────────────────────────────────────────────────────────────────────────
describe('the filters are drawn by the page a user reaches', () => {
  it('#/apps is routed to AppsSection, the component every test below mounts', () => {
    const app = readFileSync(join(SRC, 'app/App.tsx'), 'utf8')
    expect(app, "the shell's own route table").toMatch(/case 'apps': return <AppsSection/)
  })

  it('no rail sits beside the grid, at either width', async () => {
    const r = await mount(makeRouter('view=store'))
    expect(screen.queryByRole('navigation', { name: 'Categories and sources' })).toBeNull()
    r.unmount()
    setNarrow()
    await mount(makeRouter('view=store'))
    expect(screen.queryByRole('navigation', { name: 'Categories and sources' })).toBeNull()
  })
})

// ── The header filter carries both, at every width ─────────────────────────────────────────
describe("categories and sources are sections of the Store's header filter", () => {
  for (const narrow of [false, true]) {
    it(`${narrow ? 'narrow' : 'wide'}: Categories and Sources, in the vocabulary the grid can show`, async () => {
      if (narrow) setNarrow()
      await mount(makeRouter('view=store'))
      expect(rows(await section('Categories'))).toEqual(['All apps', 'Productivity', 'Utility'])
      expect(rows(await section('Sources'))).toEqual(['All sources', 'Built-in', 'apps', 'Add source'])
    })
  }

  it("Add source is the Sources section's last row, and opens the sources panel", async () => {
    await mount(makeRouter('view=store'))
    await userEvent.click(within(await section('Sources')).getByRole('button', { name: 'Add source' }))
    await waitFor(() => expect(screen.getByPlaceholderText(/a directory of app subdirs/)).toBeTruthy())
  })
})

// ── The sections say what is chosen, to a screen reader as to the eye ──────────────────────
// The rail these sections replaced announced its state: one pressed entry per block, each a real
// button. The sections keep that: each is a group named by its title, and its chosen row is
// pressed, not only tinted and checked.
describe('the sections are operable from the accessibility tree', () => {
  it('exactly one row per section is pressed, and it is the chosen one', async () => {
    await mount(makeRouter('view=store&stag=utility&ssrc=builtin'))
    expect(chosen(await section('Categories'))).toEqual(['Utility'])
    expect(chosen(await section('Sources'))).toEqual(['Built-in'])
    // Vacuity: with nothing filtered, the defaults are the pressed rows.
    cleanup()
    await mount(makeRouter('view=store'))
    expect(chosen(await section('Categories'))).toEqual(['All apps'])
    expect(chosen(await section('Sources'))).toEqual(['All sources'])
  })

  it('every row is a real button that stays a tab stop, and the rest say they are not chosen', async () => {
    await mount(makeRouter('view=store'))
    for (const title of ['Categories', 'Sources']) {
      for (const b of within(await section(title)).getAllByRole('button')) {
        expect(b.getAttribute('tabindex'), `${b.textContent} must stay a tab stop`).not.toBe('-1')
        if (b.textContent !== 'Add source') expect(b.getAttribute('aria-pressed'), b.textContent ?? '').toMatch(/^(true|false)$/)
      }
    }
  })
})

describe('the sections are walked by Tab and chosen by Enter, as the rail was', () => {
  const said = () => (document.activeElement?.textContent ?? '').replace(/\d+$/, '').trim()

  it('Tab walks every row of both sections in reading order, the filter open throughout', async () => {
    // Main's rail walk, on the sections: the rows were the page's own Tab stops, and a flyout that
    // closed on any Tab left a keyboard user only its first row.
    await mount(makeRouter('view=store'))
    const trigger = await findHeaderFilter()
    within(await section('Categories')).getByRole('button', { name: /^All apps/ }).focus()
    const walked = [said()]
    const want = ['All apps', 'Productivity', 'Utility', 'All sources', 'Built-in', 'apps', 'Add source']
    for (let i = 1; i < want.length; i++) {
      await userEvent.tab()
      walked.push(said())
      expect(trigger.getAttribute('aria-expanded'), `still open after reaching ${said()}`).toBe('true')
    }
    expect(walked, 'every row is reached, in DOM order, with no gap').toEqual(want)
  })

  it('Enter chooses a focused row: no click needed', async () => {
    const router = makeRouter('view=store')
    await mount(router)
    within(await section('Categories')).getByRole('button', { name: /^Productivity/ }).focus()
    await userEvent.keyboard('{Enter}')
    expect(router.search()).toMatch(/stag=productivity/)
  })
})

// ── Clause: selecting a category/source filters the grid AND survives reload via the URL ──
describe('a category or source filters the grid and survives a reload', () => {
  it('a category writes ?stag=, and a fresh mount from that URL is still filtered', async () => {
    const router = makeRouter('view=store')
    const r = await mount(router)
    // Before: unfiltered, and All apps is chosen. Without the "before" the "after" cannot tell a
    // filter from a fixture that never had Timer.
    expect(cards()).toEqual(['Ledger', 'Notes', 'Timer'])
    expect(router.search(), 'no category in the URL yet').not.toMatch(/stag/)
    expect(chosen(await section('Categories'))).toEqual(['All apps'])

    await userEvent.click(within(await section('Categories')).getByRole('button', { name: /^Productivity/ }))
    const recorded = router.search()
    expect(recorded, 'the selection is IN the URL, not only in component state').toMatch(/stag=productivity/)

    r.unmount()
    await mount(makeRouter(recorded))
    expect(cards(), 'the grid comes back filtered').toEqual(['Ledger', 'Notes'])
    expect(chosen(await section('Categories'))).toEqual(['Productivity'])
    expect(headerFilter().getAttribute('aria-label'), 'and the filter says so').toContain('Productivity')
  })

  it('a source writes ?ssrc=, and a fresh mount from that URL is still filtered', async () => {
    const router = makeRouter('view=store')
    const r = await mount(router)
    expect(cards()).toEqual(['Ledger', 'Notes', 'Timer'])
    // The Built-in group holds the two bundled apps; `/srv/apps` holds the local one.
    await userEvent.click(within(await section('Sources')).getByRole('button', { name: /^Built-in/ }))
    const recorded = router.search()
    expect(recorded, 'the source key is the divider key, in the URL').toMatch(/ssrc=builtin/)

    r.unmount()
    await mount(makeRouter(recorded))
    expect(cards(), 'only the Built-in group survives').toEqual(['Notes', 'Timer'])
    expect(chosen(await section('Sources'))).toEqual(['Built-in'])
  })

  it('an unknown ?stag= yields the no-match empty state, not a silent full grid', async () => {
    // A filter the code reads but never applies renders as "no filter", indistinguishable from a
    // working default. This pins that the URL value really reaches the grid.
    const { AppsSection } = await import('./AppsSection')
    render(<AppsSection query={{ view: 'store', stag: 'nope' }} setQuery={() => {}} navigate={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'No matching apps' })).toBeTruthy()
    expect(screen.queryAllByRole('button', { name: /— details$/ })).toHaveLength(0)
  })
})

// ── Clause: cards render art-forward WITH and WITHOUT hero art ───────────────────────────
describe('every card is banner-topped, with or without hero art', () => {
  it('a hero app shows its image; a hero-less app shows generated token art', async () => {
    const { container } = await mount(makeRouter('view=store'))
    const banners = [...container.querySelectorAll<HTMLElement>('[data-art]')]
    expect(banners, 'one banner per card — no card is bannerless').toHaveLength(3)

    const kinds = banners.map((b) => b.dataset.art).sort()
    expect(kinds, 'both paths are exercised by this fixture').toEqual(['generated', 'generated', 'hero'])

    const hero = banners.find((b) => b.dataset.art === 'hero')!
    expect(hero.querySelector('img')?.getAttribute('src')).toBe(NOTES.heroUrl)

    const generated = banners.find((b) => b.dataset.art === 'generated')!
    expect(generated.querySelector('img'), 'no broken image element on the fallback path').toBeNull()
    expect(generated.style.background, 'a real gradient, not an empty slot').toMatch(/linear-gradient/)
    expect(generated.style.background, 'built from scheme tokens').toMatch(/var\(--color-/)
    expect(generated.style.background, 'and never a literal color').not.toMatch(/#[0-9a-f]{3,8}/i)
  })

  it('the icon avatar is rendered even when the manifest declares no icon', async () => {
    // TIMER declares `icon: ''`. The old card dropped the tile entirely, leaving the
    // hero-less card a different shape; `AppIcon` resolves an absent name to Blocks.
    const { container } = await mount(makeRouter('view=store&stag=utility'))
    expect(cards()).toEqual(['Timer'])
    expect(container.querySelectorAll('[data-art]')).toHaveLength(1)
    expect(container.querySelector('.ring-surface-container'), 'the avatar tile is present').toBeTruthy()
  })
})

// ── The generated art itself ─────────────────────────────────────────────────────────────
describe('generated card art is deterministic and token-only', () => {
  it('the same app always draws the same art, and different apps differ', () => {
    expect(artGradient('notes')).toBe(artGradient('notes'))
    expect(artHash('notes')).not.toBe(artHash('timer'))
    const names = ['notes', 'timer', 'ledger', 'inbox', 'weather', 'kanban']
    expect(new Set(names.map(artGradient)).size, 'not all one wash').toBeGreaterThan(1)
  })

  it('the two stops are always distinct tokens', () => {
    // A same-token gradient renders as a flat block, which reads as a bug rather than art.
    // Swept wide enough that a modulo collision would have to be systematic to hide.
    for (let i = 0; i < 500; i++) {
      const { from, to } = artStops(`app-${i}`)
      expect(to, `app-${i}`).not.toBe(from)
    }
  })

  it('carries no literal color, in any name', () => {
    for (const n of ['a', 'zz', 'com.example.app', '']) {
      expect(artGradient(n)).not.toMatch(/#[0-9a-f]{3,8}/i)
      expect(artGradient(n)).toMatch(/color-mix\(in srgb, var\(--color-/)
    }
  })
})
