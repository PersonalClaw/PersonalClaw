/** Library is one destination, and each of its tabs is its own address (#3815).
 *
 *  Knowledge, Files and Artifacts were three sidebar rows, and the memory studio a page of
 *  Settings. They are the four tabs of Library. Knowledge, Files and Artifacts keep the addresses
 *  their pages always had, so a bookmark, a reload, Back and a copied link open the tab they opened
 *  before. Memory leaves Settings for `#/memory`: its old address is retired by one row of the
 *  redirect table, which carries the view and the record the old link named.
 *
 *  The real shell is booted as `main.tsx` boots it, on a set-up home whose reads are still on their
 *  way (what a page shows while it waits is that page), at each address, and asserted on what a
 *  person sees: which tab the Library strip has checked, the page's own heading, the address the
 *  bar holds, and the sidebar row that is lit. The palette's rows are the ones the shell hands it,
 *  run as a person runs them, and the catalogue is `shippedRoutes.ts`.
 */
import { StrictMode } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { SAMPLE, SHIPPED_ROUTES } from './shippedRoutes'
import { redirectFor } from './routeRedirects'
import type { Command } from './CommandPalette'

/** The rows the shell last handed the palette. */
let rows: readonly Command[] = []
/** The reads a case answers, by method, so a page can draw the record its address names. Every
 *  other read is still on its way. */
let answers: Record<string, unknown> = {}

vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../ui/DotGlow', () => ({ DotGlow: () => null }))
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  const gateway = new Proxy({}, {
    get: (_t, method: string) => () => method === 'dashboardConfig'
      ? Promise.resolve({ user_name: 'Ada', username: 'ada' })
      : method in answers ? Promise.resolve(answers[method]) : new Promise(() => {}),
  })
  return { ...real, api: gateway }
})
vi.mock('./CommandPalette', async (orig) => {
  const real = await orig<typeof import('./CommandPalette')>()
  return { ...real, CommandPalette: ({ commands }: { commands: Command[] }) => { rows = commands; return null } }
})

const { App } = await import('./App')
const { ErrorBoundary } = await import('./ErrorBoundary')
const { ThemeProvider } = await import('./theme')
const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider } = await import('./personality')
const { IdentityProvider } = await import('./identity')
const { installAppSdk } = await import('./appSdk')
const { registerBuiltinContentTypes } = await import('../ui/content/registerBuiltins')

installAppSdk()
registerBuiltinContentTypes()

// The pages the tabs draw, loaded once up front: a page's first load is a module transform, and on
// a busy machine that can outlast the waits below.
beforeAll(async () => {
  await Promise.all([
    import('../pages/knowledge/KnowledgeSection'),
    import('../pages/settings/MemoryPanel'),
    import('../pages/files/FilesSection'),
    import('../pages/artifacts/ArtifactsSection'),
    import('../pages/settings/SettingsPage'),
    import('../pages/dashboard/DashboardPage'),
  ])
}, 120_000)

beforeEach(() => {
  vi.stubGlobal('fetch', () => new Promise(() => {}))
  localStorage.clear()
  sessionStorage.clear()
  answers = {}
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

/** Each tab: its label, its address, and the heading its page shows. */
const TABS = [
  ['Knowledge', 'knowledge', 'Knowledge'],
  ['Memory', 'memory', 'Memory'],
  ['Files', 'files', 'Files'],
  ['Artifacts', 'artifacts', 'Artifacts'],
] as const

/** The knowledge item and the artifact the deeper addresses name, as the gateway answers them. */
const ITEM = {
  id: SAMPLE.knowledge, title: 'Widgets, considered', item_type: 'note', word_count: 12,
  content: '# Widgets, considered\n\nThe quick brown fox jumps over the lazy dog, at length.',
}
const ARTIFACT = {
  slug: SAMPLE.artifact, name: 'Quarterly report', kind: 'markdown', source: 'chat', description: '', tags: [],
  version: 1, content: 'The quarter, in short.', created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
}

/** The Knowledge page's view switcher, and the view it has checked. */
const knowledgeViews = () => screen.getByRole('radiogroup', { name: 'Knowledge view' })
const checkedView = () => within(knowledgeViews()).getAllByRole('radio').filter((r) => r.getAttribute('aria-checked') === 'true')
  .map((r) => r.textContent)
/** The reader's article, or null where the item is not open in it. */
const reader = () => screen.queryByRole('group', { name: 'Article body' })
/** The Memory view the page has selected. */
const memoryView = () => within(screen.getByRole('tablist', { name: 'Memory view' })).getByRole('tab', { selected: true }).textContent

/** The deeper addresses the issue names, each with the tab it belongs to and what it draws there,
 *  the sub-view the address names. Each opens as itself. */
const DEEPER = [
  { address: `knowledge/item/${SAMPLE.knowledge}`, tab: 'Knowledge', draws: () => {
    expect(heading(), 'the item\'s own page').toBe(ITEM.title)
    expect(reader(), 'not yet in the reader').toBeNull()
  } },
  { address: `knowledge/item/${SAMPLE.knowledge}?read=1`, tab: 'Knowledge', draws: () => {
    expect(heading()).toBe(ITEM.title)
    expect(reader(), 'the item, open in the reader').not.toBeNull()
  } },
  { address: 'knowledge?view=graph', tab: 'Knowledge', draws: () => expect(checkedView()).toEqual(['Graph']) },
  { address: 'knowledge/sources', tab: 'Knowledge', draws: () => expect(heading()).toBe('Watched sources') },
  { address: 'knowledge/new', tab: 'Knowledge', draws: () => expect(heading()).toBe('Add knowledge') },
  { address: 'files?dir=docs', tab: 'Files', draws: () => {
    expect((screen.getByRole('textbox', { name: 'Go to path' }) as HTMLInputElement).value, 'the folder the address names').toBe('docs')
  } },
  { address: `artifacts/${SAMPLE.artifact}`, tab: 'Artifacts', draws: () => {
    expect(heading(), 'the artifact\'s own page, not the grid').toBe('')
    expect(screen.getByTitle('Back to the library')).toBeTruthy()
    expect(within(document.querySelector('main')!).getByText(ARTIFACT.name)).toBeTruthy()
  } },
  { address: 'memory?tab=audit', tab: 'Memory', draws: () => expect(memoryView()).toContain('Audit') },
] as const

/** The addresses Library retires or takes over, each with where it lands and the tab it opens. */
const RETIRED = [
  ['settings/memory', 'memory', 'Memory'],
  ['settings/memory?tab=audit', 'memory?tab=audit', 'Memory'],
  ['settings/memory?tab=settings&pref=pref.tone', 'memory?tab=settings&pref=pref.tone', 'Memory'],
  ['settings/memory?tab=studio&sel=fact%3Aa', 'memory?tab=studio&sel=fact%3Aa', 'Memory'],
  ['settings/memory?tab=audit&partition=p-0a1b2c3d', 'memory?tab=audit&partition=p-0a1b2c3d', 'Memory'],
  [`files/${SAMPLE.artifact}`, `artifacts/${SAMPLE.artifact}`, 'Artifacts'],
] as const

/** Boot the shell fresh at `address`, as a copied link or a reload opens it, and wait until it has
 *  stopped moving. */
async function open(address: string) {
  cleanup()
  history.replaceState(null, '', `#/${address}`)
  render(
    <StrictMode>
      <ThemeProvider><AppearanceProvider><PersonalityProvider><IdentityProvider>
        <ErrorBoundary><App /></ErrorBoundary>
      </IdentityProvider></PersonalityProvider></AppearanceProvider></ThemeProvider>
    </StrictMode>,
  )
  await settle()
}

/** Let the shell run until the address and what it draws have held still for a few ticks. */
async function settle() {
  let last = ''
  let still = 0
  for (let tick = 0; tick < 600 && still < 3; tick++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 20)) })
    const now = `${location.hash}|${checkedTab()}|${heading()}`
    still = now === last && document.querySelector('main') ? still + 1 : 0
    last = now
  }
}

/** The Library strip, or null where it is not drawn. */
const strip = () => screen.queryByRole('radiogroup', { name: 'Library' })

/** The tab the strip has checked. */
function checkedTab() {
  const s = strip()
  if (!s) return 'no Library strip'
  return within(s).getAllByRole('radio').filter((r) => r.getAttribute('aria-checked') === 'true').map((r) => r.textContent).join(', ')
}

/** The page's own heading. */
const heading = () => document.querySelector('main h1')?.textContent ?? ''

/** The sidebar, its rows, and the one lit as where you are. */
const rail = () => document.querySelector('nav[data-tour="rail"]') as HTMLElement
const railRows = () => within(rail()).getAllByRole('button').map((b) => b.getAttribute('aria-label') ?? '')
const railLit = () => within(rail()).queryAllByRole('button').filter((b) => b.getAttribute('aria-current') === 'page')
  .map((b) => b.getAttribute('aria-label'))

describe('each tab of Library is its own address', () => {
  it.each(TABS)('the %s tab opens at #/%s, with its own page under the strip', async (tab, address, title) => {
    await open(address)
    expect(checkedTab()).toBe(tab)
    expect(heading()).toBe(title)
    expect(location.hash, 'the address is the tab\'s own').toBe(`#/${address}`)
    expect(railLit(), 'the sidebar says where you are').toEqual(['Library'])
  })

  it.each(DEEPER)('#/$address lands where it did, inside Library on the $tab tab', async ({ address, tab, draws }) => {
    answers = {
      knowledgeItem: ITEM,
      artifact: ARTIFACT, artifactVersions: { slug: ARTIFACT.slug, versions: [] }, artifactEvents: { events: [] },
    }
    await open(address)
    expect(checkedTab()).toBe(tab)
    expect(location.hash).toBe(`#/${address}`)
    expect(railLit()).toEqual(['Library'])
    draws()
  })

  it.each(RETIRED)('the old #/%s opens #/%s, on the %s tab', async (address, lands, tab) => {
    await open(address)
    expect(location.hash).toBe(`#/${lands}`)
    expect(checkedTab()).toBe(tab)
    expect(railLit()).toEqual(['Library'])
  })

  it('#/library, the sidebar row\'s own address, opens the first tab, Knowledge', async () => {
    await open('library')
    expect(checkedTab()).toBe('Knowledge')
    expect(heading()).toBe('Knowledge')
    expect(location.hash).toBe('#/library')
    expect(railLit()).toEqual(['Library'])
  })

  it('the old Settings › Memory link opens the view it named', async () => {
    await open('settings/memory?tab=audit')
    expect(memoryView()).toContain('Audit')
  })

  it('choosing a tab writes its address in place of the one before, keeps the strip, and a reload opens it', async () => {
    await open('knowledge')
    const before = strip()
    expect(before, 'no Library strip').not.toBeNull()
    const entries = history.length
    fireEvent.click(within(before!).getByRole('radio', { name: 'Memory' }))
    await settle()
    expect(location.hash).toBe('#/memory')
    expect(heading()).toBe('Memory')
    expect(history.length, 'a tab is a view of one destination, so it replaces the entry: Back leaves Library').toBe(entries)
    expect(strip(), 'the strip stays where it is, and the focus with it').toBe(before)

    fireEvent.click(within(strip()!).getByRole('radio', { name: 'Artifacts' }))
    await settle()
    expect(location.hash).toBe('#/artifacts')
    expect(checkedTab()).toBe('Artifacts')

    // A reload is the shell booted again on the address the bar holds.
    await open(location.hash.slice(2))
    expect(checkedTab()).toBe('Artifacts')
    expect(heading()).toBe('Artifacts')
  })
})

describe('an old Settings › Memory link opens the record it named', () => {
  /** A fact, and a learned preference, each as the gateway holds it. */
  const FACT = 'user.favorite_language'
  const PREF = 'pref.facet.style.bbaad48828'
  const PREF_TEXT = 'keep your answers short'
  beforeEach(() => {
    // The linked row is scrolled into view, which jsdom does not lay out.
    Element.prototype.scrollIntoView = vi.fn()
    answers = {
      memoryStats: null,
      memorySemantic: [
        { key: FACT, value_json: '"Python"', source: 'user_explicit', confidence: 1 },
        { key: PREF, value_json: JSON.stringify({ cls: 'style', text: PREF_TEXT, stability: 1, cue: 'explicit' }), source: 'facet', confidence: 0.9 },
      ],
      memoryEpisodic: [],
      lessons: [],
      memorySettings: { history_idle_hours: 2, history_max_days: 90 },
      memoryFacets: [{
        key: PREF, cls: 'style', text: PREF_TEXT, cue: 'explicit', stability: 1, stored_stability: 1, state: 'Active',
        updated_at: '2026-09-29T16:22:58Z', pinned: false, forgotten: false,
      }],
    }
  })
  afterEach(() => { delete (Element.prototype as Partial<Element>).scrollIntoView })

  it('a citation\'s record (?sel=) opens selected in the Studio', async () => {
    const sel = encodeURIComponent(`fact:${FACT}`)
    await open(`settings/memory?tab=studio&sel=${sel}`)
    expect(location.hash).toBe(`#/memory?tab=studio&sel=${sel}`)
    expect(memoryView()).toContain('Studio')
    const marked = screen.getAllByRole('button', { current: true })
    expect(marked.map((b) => b.textContent), 'the one record the link named is the one selected').toEqual([expect.stringContaining(FACT)])
  })

  it('a learned preference\'s link (?pref=) opens Learned preferences at its row', async () => {
    const pref = encodeURIComponent(PREF)
    await open(`settings/memory?tab=settings&pref=${pref}`)
    expect(location.hash).toBe(`#/memory?tab=settings&pref=${pref}`)
    expect(memoryView()).toContain('Settings')
    const row = screen.getByText(PREF_TEXT).closest('[aria-current]')
    expect(row, 'the row the link named is marked').not.toBeNull()
    expect(document.activeElement, 'and has the focus, so its Pin and Forget are the next stops').toBe(row)
  })
})

describe('Knowledge\'s own item list is no longer called Library', () => {
  it('it is All items, the view the Knowledge tab opens on', async () => {
    await open('knowledge')
    expect(within(knowledgeViews()).getAllByRole('radio').map((r) => r.textContent)).toEqual(['Home', 'All items', 'Graph', 'Intents', 'Tags', 'Conflicts', 'Decisions'])
    expect(checkedView()).toEqual(['All items'])
  })

  it('an old #/knowledge?view=library link still opens it, at the address it was', async () => {
    await open('knowledge?view=library')
    expect(location.hash).toBe('#/knowledge?view=library')
    expect(checkedTab()).toBe('Knowledge')
    expect(checkedView()).toEqual(['All items'])
  })
})

describe('the sidebar holds one Library row', () => {
  it('in place of Knowledge, Files and Artifacts', async () => {
    await open('dashboard')
    const names = railRows()
    expect(names).toContain('Library')
    for (const gone of ['Knowledge', 'Files', 'Artifacts']) expect(names).not.toContain(gone)
  })

  it('and Settings no longer lists Memory, which is a tab of Library', async () => {
    await open('settings')
    expect(screen.getByRole('button', { name: 'Open Models settings' }), 'the Settings home drew its cards').toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Open Memory settings' })).toBeNull()
  })
})

describe('the palette and the catalogue cover every tab', () => {
  /** Each row: its id, its words, and the address it opens. */
  const ROWS = [
    ['go:library', 'Library', 'library'],
    ['go:knowledge', 'Knowledge', 'knowledge'],
    ['go:memory', 'Memory', 'memory'],
    ['go:files', 'Files', 'files'],
    ['go:artifacts', 'Artifacts', 'artifacts'],
  ] as const

  it.each(ROWS)('the palette row %s, "%s", opens #/%s', async (id, label, address) => {
    await open('dashboard')
    const row = rows.find((r) => r.id === id)
    expect(row, `no palette row ${id}`).toBeTruthy()
    expect(row!.label).toBe(label)
    history.replaceState(null, '', '#/dashboard')
    act(() => row!.run())
    await settle()
    expect(location.hash).toBe(`#/${address}`)
  })

  it('the catalogue lists Library and every tab\'s address, each opened by the palette', () => {
    const entry = (route: string) => SHIPPED_ROUTES.find((e) => e.route === route)
    expect(entry('library')?.from).toEqual(expect.arrayContaining(['router', 'palette']))
    expect(entry('memory')?.from).toEqual(expect.arrayContaining(['router', 'palette']))
    for (const [, , address] of ROWS) {
      expect(entry(address)?.from, `#/${address}`).toContain('palette')
      expect(entry(address)?.lands, `#/${address} opens as itself`).toBeUndefined()
    }
    // The item list's key from when the view was called Library, so the shell opens it with the rest.
    expect(entry('knowledge?view=library'), '#/knowledge?view=library').toBeTruthy()
    expect(entry('knowledge?view=library')?.lands, '#/knowledge?view=library opens as itself').toBeUndefined()
  })

  it('the old Settings › Memory address is retired to the Memory tab, its view and record carried', () => {
    expect(SHIPPED_ROUTES.find((e) => e.route === 'settings/memory')?.lands).toBe('memory')
    for (const [address, lands] of RETIRED) expect(redirectFor(address), `#/${address}`).toBe(lands)
    // Each Memory view it had is still catalogued, at its new address.
    for (const tab of ['health', 'recall', 'inspect', 'audit', 'settings']) {
      expect(SHIPPED_ROUTES.find((e) => e.route === `settings/memory?tab=${tab}`)?.lands).toBe(`memory?tab=${tab}`)
      expect(SHIPPED_ROUTES.find((e) => e.route === `memory?tab=${tab}`), `memory?tab=${tab}`).toBeTruthy()
    }
  })
})
