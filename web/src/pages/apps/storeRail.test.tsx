import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { artGradient, artStops, artHash } from './appArt'

// ── PEP-3: the Store's persistent category/source rail, and the two things it is easy
//    to ship broken ────────────────────────────────────────────────────────────────────
//
// 1. A COMPONENT THAT NOTHING RENDERS. A rail with its own green isolation suite, mounted
//    only by that suite, is the inert-control shape this repo keeps finding. So nothing here
//    mounts `StoreSideRail` directly: every assertion below drives `AppsSection` — the
//    component `app/App.tsx` routes `case 'apps'` to — and the first test pins that route so
//    the surface under test is the one a user reaches. Delete the `<StoreSideRail …>` line in
//    `AppsSection.tsx` and this file goes red.
//
// 2. A SELECTION THAT LIVES IN COMPONENT STATE. "Survives reload via the URL" cannot be
//    proven by reading a state variable, and it cannot be proven by asserting `setQuery` was
//    called either — a page that writes the URL and then reads its own `useState` passes both.
//    So the URL test READS THE URL BACK out of a fake router and RE-MOUNTS a fresh component
//    from it (`unmount()` → `render()` with the parsed params), then asserts the grid is
//    filtered and the rail button is pressed. That is the reload, modelled.
//
// 🪤 `useIsMobile` is a `matchMedia('(max-width: 768px)')` query, and jsdom has no layout — so
// the media query IS the observable input for "wide" vs "narrow", exactly as `DegradedChip`
// and `escapeDismissContract` already record. The suite default (`src/test/setup.ts` stubs
// `matches: false`) is the WIDE branch; `setNarrow()` below flips it. Both directions are
// asserted, because a rail that renders at every width and a rail that renders at none both
// pass a single-width test.

const SRC = join(process.cwd(), 'src')

/** A source file with its COMMENTS stripped, so a text guard measures code and not the
 *  prose describing it (`listResultAnnounce.test.tsx` keeps the same helper for the same
 *  reason). Block comments first, then line comments. */
function code(abs: string): string {
  return readFileSync(abs, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|\s)\/\/.*$/gm, '$1')
}

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
  // The catalog read is async; the rail only exists once it resolves.
  await waitFor(() => expect(screen.getByRole('navigation', { name: 'Categories and sources' })).toBeTruthy())
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

// ── The call site ────────────────────────────────────────────────────────────────────────
describe('the rail is rendered by the page a user reaches', () => {
  it('#/apps is routed to AppsSection, the component every test below mounts', () => {
    const app = readFileSync(join(SRC, 'app/App.tsx'), 'utf8')
    expect(app, "the shell's own route table").toMatch(/case 'apps': return <AppsSection/)
  })

  it('AppsSection renders the rail in the Store view — and NOT in the Library view', async () => {
    const r = await mount(makeRouter('view=store'))
    expect(screen.getByRole('navigation', { name: 'Categories and sources' })).toBeTruthy()
    r.unmount()
    // 🔑 The vacuity half. If the rail were rendered unconditionally by the page shell, the
    // Store assertion above would pass with the `isStore` branch deleted. The Library must
    // NOT have it — the rail filters the Store's universe, which the Library does not show.
    const { AppsSection } = await import('./AppsSection')
    render(<AppsSection query={{ view: 'library' }} setQuery={() => {}} navigate={() => {}} />)
    await waitFor(() => expect(screen.getByRole('heading', { level: 1, name: 'Apps' })).toBeTruthy())
    expect(screen.queryByRole('navigation', { name: 'Categories and sources' })).toBeNull()
  })
})

// ── Clause: selecting a category/source filters the grid AND survives reload via the URL ──
describe('a rail selection filters the grid and survives a reload', () => {
  it('a category writes ?stag=, and a fresh mount from that URL is still filtered', async () => {
    const router = makeRouter('view=store')
    const r = await mount(router)

    // Before: unfiltered, and "All apps" is the pressed entry. Both halves matter — without
    // the "before" the "after" cannot distinguish a filter from a fixture that never had Timer.
    expect(cards()).toEqual(['Ledger', 'Notes', 'Timer'])
    expect(router.search(), 'no category in the URL yet').not.toMatch(/stag/)
    const rail = screen.getByRole('navigation', { name: 'Categories and sources' })
    expect(within(rail).getByRole('button', { name: /^All apps/ }).getAttribute('aria-pressed')).toBe('true')

    await userEvent.click(within(rail).getByRole('button', { name: /^Productivity/ }))

    // 1. Read the URL BACK. This is the artifact a reload restores from.
    const recorded = router.search()
    expect(recorded, 'the selection is IN the URL, not only in component state').toMatch(/stag=productivity/)

    // 2. Re-mount from it, from scratch — a new component instance, a new router built only
    //    from the recorded string. Nothing survives except the URL.
    r.unmount()
    const reloaded = makeRouter(recorded)
    await mount(reloaded)

    expect(cards(), 'the grid comes back filtered').toEqual(['Ledger', 'Notes'])
    const rail2 = screen.getByRole('navigation', { name: 'Categories and sources' })
    expect(within(rail2).getByRole('button', { name: /^Productivity/ }).getAttribute('aria-pressed')).toBe('true')
    expect(within(rail2).getByRole('button', { name: /^All apps/ }).getAttribute('aria-pressed')).toBe('false')
  })

  it('a source writes ?ssrc=, and a fresh mount from that URL is still filtered', async () => {
    const router = makeRouter('view=store')
    const r = await mount(router)
    expect(cards()).toEqual(['Ledger', 'Notes', 'Timer'])

    const rail = screen.getByRole('navigation', { name: 'Categories and sources' })
    // The Built-in group holds the two bundled apps; `/srv/apps` holds the local one.
    await userEvent.click(within(rail).getByRole('button', { name: /^Built-in/ }))

    const recorded = router.search()
    expect(recorded, 'the source key is the divider key, in the URL').toMatch(/ssrc=builtin/)

    r.unmount()
    await mount(makeRouter(recorded))
    expect(cards(), 'only the Built-in group survives').toEqual(['Notes', 'Timer'])
    const rail2 = screen.getByRole('navigation', { name: 'Categories and sources' })
    expect(within(rail2).getByRole('button', { name: /^Built-in/ }).getAttribute('aria-pressed')).toBe('true')
  })

  it('an unknown ?stag= yields the no-match empty state, not a silent full grid', async () => {
    // A filter the code reads but never applies renders as "no filter" — indistinguishable
    // from a working default. This pins that the URL value really reaches the grid.
    await mount(makeRouter('view=store&stag=nope'))
    expect(screen.queryAllByRole('button', { name: /— details$/ })).toHaveLength(0)
    expect(screen.getByRole('heading', { name: 'No matching apps' })).toBeTruthy()
  })
})

// ── Clause: wide shows the rail persistently, narrow falls back to the dropdown ──────────
describe('the rail and the dropdown are one filter at two widths', () => {
  it('wide: the rail is present and the dropdown does NOT repeat its two dimensions', async () => {
    await mount(makeRouter('view=store'))
    expect(screen.getByRole('navigation', { name: 'Categories and sources' })).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Filter & sort' }))
    // 🪤 Scoped OUTSIDE the rail. `queryByText('Categories')` over the whole document finds
    // the RAIL's own heading and reads as "the dropdown has it too" — the absence has to be
    // measured somewhere the rail is not.
    const rail = screen.getByRole('navigation', { name: 'Categories and sources' })
    const outside = (t: string) => screen.queryAllByText(t).filter((el) => !rail.contains(el))
    // Sort/Type still live in the dropdown — proof the menu opened and is populated, so the
    // two absences below are absences and not an unopened popover.
    expect(outside('Sort by'), 'the menu is open and populated').toHaveLength(1)
    expect(outside('Categories'), 'the rail owns it at this width').toHaveLength(0)
    expect(outside('Sources'), 'the rail owns it at this width').toHaveLength(0)
  })

  it('narrow: the rail is gone and the dropdown carries Categories + Sources', async () => {
    setNarrow()
    const { AppsSection } = await import('./AppsSection')
    render(<AppsSection query={{ view: 'store' }} setQuery={() => {}} navigate={() => {}} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Filter & sort' })).toBeTruthy())
    expect(screen.queryByRole('navigation', { name: 'Categories and sources' }), 'no rail below the threshold').toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Filter & sort' }))
    expect(screen.getByText('Categories'), 'the fallback carries the category dimension').toBeTruthy()
    expect(screen.getByText('Sources'), 'and the source dimension').toBeTruthy()
    // The SAME vocabulary, humanised the same way — one derivation feeding both.
    expect(screen.getByRole('button', { name: /^Productivity/ })).toBeTruthy()
  })

  it('narrow keeps the dimensions REACHABLE — the fallback filters too', async () => {
    setNarrow()
    const router = makeRouter('view=store')
    const { AppsSection } = await import('./AppsSection')
    render(<AppsSection query={router.query} setQuery={router.setQuery} navigate={() => {}} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Filter & sort' })).toBeTruthy())
    await userEvent.click(screen.getByRole('button', { name: 'Filter & sort' }))
    await userEvent.click(screen.getByRole('button', { name: /^Utility/ }))
    expect(router.search(), 'the dropdown writes the same URL key the rail does').toMatch(/stag=utility/)
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

// ── Clause: keyboard-navigable, with aria-pressed category buttons ───────────────────────
describe('the rail is operable from the accessibility tree', () => {
  it('exactly one entry per block is pressed, and it is the selected one', async () => {
    await mount(makeRouter('view=store&stag=utility&ssrc=builtin'))
    const rail = screen.getByRole('navigation', { name: 'Categories and sources' })
    const pressed = within(rail).getAllByRole('button', { pressed: true })
      .map((b) => b.textContent)
    expect(pressed, 'the two selected entries, and nothing else').toEqual(['Utility1', 'Built-in2'])
    // Vacuity: the same query must find the DEFAULTS pressed when nothing is filtered.
    cleanup()
    await mount(makeRouter('view=store'))
    const rail2 = screen.getByRole('navigation', { name: 'Categories and sources' })
    expect(within(rail2).getAllByRole('button', { pressed: true }).map((b) => b.textContent))
      .toEqual(['All apps3', 'All sources3'])
  })

  it('every entry is a real button — reachable by Tab, operable by Enter', async () => {
    const router = makeRouter('view=store')
    await mount(router)
    const rail = screen.getByRole('navigation', { name: 'Categories and sources' })
    const productivity = within(rail).getByRole('button', { name: /^Productivity/ })
    // No roving `tabIndex={-1}`: arrow support was added ON TOP of the tab stops, so a
    // keyboard user who never presses an arrow still reaches every entry.
    // (Framer Motion sets an explicit `tabindex="0"` on any element with whileTap/whileHover —
    // the same thing the AppCard comment records — so the property to pin is "not removed from
    // the tab order", i.e. never `-1`, not "attribute absent".)
    for (const b of within(rail).getAllByRole('button')) {
      expect(b.getAttribute('tabindex'), `${b.textContent} must stay a tab stop`).not.toBe('-1')
    }
    productivity.focus()
    await userEvent.keyboard('{Enter}')
    expect(router.search(), 'Enter activates it — no click needed').toMatch(/stag=productivity/)
  })

  it('Tab walks every entry of both blocks, in reading order', async () => {
    // The keyboard contract, driven rather than asserted from attributes. The first version
    // of this rail ALSO implemented an arrow-key cursor; `ui/popupItemRoles.test.tsx` failed
    // it — a cursor over a mapped list with no container role — and the fix was to drop the
    // cursor, not to declare a role that would have forced `aria-selected` over the
    // `aria-pressed` this atom requires. So Tab is the whole navigation model, and this walks it.
    await mount(makeRouter('view=store'))
    const rail = screen.getByRole('navigation', { name: 'Categories and sources' })
    const entries = within(rail).getAllByRole('button', { name: /\d$|^Add source$|apps$|sources$/ })
    expect(entries.length, 'All apps + 2 categories + All sources + 2 sources + Add source').toBe(7)

    within(rail).getByRole('button', { name: /^All apps/ }).focus()
    const walked: string[] = [(document.activeElement?.textContent ?? '').trim()]
    for (let i = 1; i < entries.length; i++) {
      await userEvent.tab()
      walked.push((document.activeElement?.textContent ?? '').trim())
    }
    expect(walked, 'every entry is reached, in DOM order, with no gap').toEqual(
      ['All apps3', 'Productivity2', 'Utility1', 'All sources3', 'Built-in2', '/srv/apps1', 'Add source'])
  })

  it('the pressed state is not carried by a class alone', async () => {
    // The failure this guards: styling selection with a tint and nothing else. A tint is
    // invisible to every non-visual reader, and it is what a "make it look selected" fix
    // reaches for first.
    const src = code(join(SRC, 'pages/apps/StoreSideRail.tsx'))
    expect(src, 'aria-pressed is bound to the live selection, not hardcoded')
      .toMatch(/pressed=\{value === o\.key\}/)
    // The shared row is the one that emits it, so the primitive must forward it.
    expect(code(join(SRC, 'ui/FilterRow.tsx')), 'FilterRow publishes the prop').toMatch(/aria-pressed=\{pressed\}/)
    // 🪤 Read through `code()`, which strips comments. The first version of this assertion
    // scanned the raw file and went red on the rail's OWN doc comment saying it must not use
    // `sr-only` — a text scanner reads prose as code, and the prose was the compliance note.
    expect(src, 'no visually-hidden text inside the button to pollute its accessible name').not.toMatch(/sr-only/)
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
