/** Capabilities is one destination, and each of its tabs is its own address (#3817).
 *
 *  Agents, Skills, Learning, Tools, Prompts and Apps were six sidebar rows, and the Store a view of
 *  the last one. They are the seven tabs of Capabilities, Learning beside the Skills whose proposals
 *  it links to, and each tab keeps the address its page always had, so a bookmark, a reload, Back
 *  and a copied link open the tab they opened before: no address is retired, and none goes through
 *  the redirect table.
 *
 *  The real shell is booted as `main.tsx` boots it, on a set-up home whose reads are still on their
 *  way (what a page shows while it waits is that page), at each address, and asserted on what a
 *  person sees: which tab the Capabilities strip has checked, the page's own heading, the address
 *  the bar holds, and the sidebar row that is lit. The palette's rows are the ones the shell hands
 *  it, run as a person runs them, and the catalogue is `shippedRoutes.ts`.
 */
import { StrictMode } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { SHIPPED_ROUTES } from './shippedRoutes'
import type { Command } from './CommandPalette'

/** What the gateway answers; every other read stays on its way. */
const answers: Record<string, unknown> = {}

/** The rows the shell last handed the palette. */
let rows: readonly Command[] = []

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
    import('../pages/agents/AgentsSection'),
    import('../pages/skills/SkillsPage'),
    import('../pages/tools/ToolsPage'),
    import('../pages/prompts/PromptsSection'),
    import('../pages/apps/AppsSection'),
    import('../pages/apps/AppHostPage'),
    import('../pages/dashboard/DashboardPage'),
    import('../pages/learning/LearningPage'),
    import('../pages/workflows/WorkflowsSection'),
  ])
}, 120_000)

beforeEach(() => {
  vi.stubGlobal('fetch', () => new Promise(() => {}))
  localStorage.clear()
  sessionStorage.clear()
  for (const key of Object.keys(answers)) delete answers[key]
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

/** Each tab: its label, the address it has always had, and the heading its page shows. */
const TABS = [
  ['Agents', 'agents', 'Agents'],
  ['Skills', 'skills', 'Skills'],
  ['Learning', 'learning', 'Learning'],
  ['Tools', 'tools', 'Tools'],
  ['Prompts', 'prompts', 'Prompts'],
  ['Apps', 'apps', 'Apps'],
  ['Store', 'apps?view=store', 'Store'],
] as const

/** The deeper addresses the issue names, each with the tab it belongs to and the heading of what
 *  it opened before. */
const DEEPER = [
  ['agents/new', 'Agents', 'New agent'],
  ['skills?mode=proposals', 'Skills', 'Skill proposals'],
  ['tools?add=1', 'Tools', 'Tools'],
  ['prompts/view?kind=user&name=greeting', 'Prompts', 'greeting'],
  ['apps?view=store&stag=search', 'Store', 'Store'],
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

/** Let the shell run until the address and the page have held still for a few ticks. */
async function settle() {
  let last = ''
  let still = 0
  for (let tick = 0; tick < 600 && still < 3; tick++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 20)) })
    const now = `${location.hash}|${document.querySelector('main h1')?.textContent ?? ''}`
    still = now === last && now.split('|')[1] ? still + 1 : 0
    last = now
  }
}

/** The Capabilities strip, or null where it is not drawn. */
const strip = () => screen.queryByRole('radiogroup', { name: 'Capabilities' })

/** The tab the strip has checked. */
const checkedTab = () => {
  const s = strip()
  if (!s) return 'no Capabilities strip'
  return within(s).getAllByRole('radio').filter((r) => r.getAttribute('aria-checked') === 'true').map((r) => r.textContent).join(', ')
}

/** The page's own heading. */
const heading = () => document.querySelector('main h1')?.textContent ?? ''

/** The sidebar, its rows, and the one lit as where you are. */
const rail = () => document.querySelector('nav[data-tour="rail"]') as HTMLElement
const railRows = () => within(rail()).getAllByRole('button').map((b) => b.getAttribute('aria-label') ?? '')
const railLit = () => within(rail()).queryAllByRole('button').filter((b) => b.getAttribute('aria-current') === 'page')
  .map((b) => b.getAttribute('aria-label'))

describe('each tab of Capabilities is its own address', () => {
  it.each(TABS)('the %s tab opens at #/%s, with its own page under the strip', async (tab, address, title) => {
    await open(address)
    expect(checkedTab()).toBe(tab)
    expect(heading()).toBe(title)
    expect(location.hash, 'the address is the one the tab has always had').toBe(`#/${address}`)
    expect(railLit(), 'the sidebar says where you are').toEqual(['Capabilities'])
  })

  it.each(DEEPER)('#/%s lands where it did, inside Capabilities on the %s tab', async (address, tab, title) => {
    await open(address)
    expect(checkedTab()).toBe(tab)
    expect(heading()).toBe(title)
    expect(location.hash).toBe(`#/${address}`)
  })

  it('the strip shows the tabs in its order, Learning beside Skills', async () => {
    await open('learning')
    expect(strip(), 'no Capabilities strip').not.toBeNull()
    expect(within(strip()!).getAllByRole('radio').map((r) => r.textContent)).toEqual(TABS.map(([tab]) => tab))
  })

  it('#/capabilities, the sidebar row\'s own address, opens the first tab, Agents', async () => {
    await open('capabilities')
    expect(checkedTab()).toBe('Agents')
    expect(heading()).toBe('Agents')
    expect(location.hash).toBe('#/capabilities')
    expect(railLit()).toEqual(['Capabilities'])
  })

  it('the strip clears the shell corners, and the tab\'s own header under it pads to the page\'s edges', async () => {
    await open('tools')
    const [stripBar, pageBar] = [...document.querySelectorAll('main header')] as HTMLElement[]
    expect(stripBar.contains(strip()), 'the strip is the first bar').toBe(true)
    expect(stripBar.style.paddingLeft).toContain('--shell-corner-l')
    expect(stripBar.style.paddingRight).toContain('--shell-corner-r')
    expect(pageBar.textContent).toContain('Tools')
    expect([pageBar.style.paddingLeft, pageBar.style.paddingRight], 'no corner floats over it').toEqual(['var(--spacing-l, 16px)', 'var(--spacing-l, 16px)'])
  })

  it('an installed app\'s own page stays outside Capabilities, at #/app/<name>', async () => {
    await open('app/an-app')
    expect(strip()).toBeNull()
    expect(location.hash).toBe('#/app/an-app')
  })

  it('choosing a tab writes its address in place of the one before, keeps the strip, and a reload opens it', async () => {
    await open('agents')
    const before = strip()
    expect(before, 'no Capabilities strip').not.toBeNull()
    const entries = history.length
    fireEvent.click(within(before!).getByRole('radio', { name: 'Skills' }))
    await settle()
    expect(location.hash).toBe('#/skills')
    expect(heading()).toBe('Skills')
    expect(history.length, 'a tab is a view of one destination, so it replaces the entry: Back leaves Capabilities').toBe(entries)
    expect(strip(), 'the strip stays where it is, and the focus with it').toBe(before)

    fireEvent.click(within(strip()!).getByRole('radio', { name: 'Store' }))
    await settle()
    expect(location.hash).toBe('#/apps?view=store')
    expect(checkedTab()).toBe('Store')

    // A reload is the shell booted again on the address the bar holds.
    await open(location.hash.slice(2))
    expect(checkedTab()).toBe('Store')
    expect(heading()).toBe('Store')
  })
})

describe('Learning sits beside the skill proposals', () => {
  it('its link to the waiting skill proposals opens them on the Skills tab, under the same strip', async () => {
    answers.learningProposals = {
      rows: [], total: 0, by_kind: {}, by_tier: {}, flagged: 0, unrenderable: [], bulk_acceptable: 0, skill_proposals_pending: 2,
    }
    await open('learning')
    const before = strip()
    expect(checkedTab()).toBe('Learning')
    fireEvent.click(await screen.findByRole('button', { name: 'Review in Skill proposals' }))
    await settle()
    expect(location.hash).toBe('#/skills?mode=proposals')
    expect(checkedTab()).toBe('Skills')
    expect(heading()).toBe('Skill proposals')
    expect(strip(), 'the strip stays where it is').toBe(before)
  })
})

describe('the Store is a tab of its own', () => {
  it('the Apps tab toggles Native and Library only, and the Store tab draws no toggle of its own', async () => {
    await open('apps')
    const views = screen.getByRole('radiogroup', { name: 'Native or Library' })
    expect(within(views).getAllByRole('radio').map((r) => r.textContent)).toEqual(['Native', 'Library'])
    await open('apps?view=store')
    expect(screen.queryByRole('radiogroup', { name: 'Native or Library' })).toBeNull()
    expect(screen.getAllByRole('radio', { name: 'Store' }), 'only the strip names the Store').toHaveLength(1)
  })
})

describe('the sidebar holds one Capabilities row', () => {
  it('in place of Agents, Tools, Skills, Learning, Prompts and Apps', async () => {
    await open('dashboard')
    const names = railRows()
    expect(names).toContain('Capabilities')
    for (const gone of ['Agents', 'Tools', 'Skills', 'Learning', 'Prompts', 'Apps']) expect(names).not.toContain(gone)
    // Workflows moved with Triggers into the one Automations row (#3816).
    expect(names).toEqual(expect.arrayContaining(['Automations', 'Settings']))
  })

  it('and an app UI the owner pinned stays a row of its own, below the destinations', async () => {
    answers.apps = [{
      name: 'an-app', displayName: 'An app', icon: 'blocks', enabled: true, hasUI: true,
      uiPages: [{ label: 'An app board', icon: 'blocks' }],
    }]
    localStorage.setItem('nav-apps', JSON.stringify(['an-app']))
    await open('dashboard')
    const names = railRows()
    expect(names).toContain('An app board')
    expect(names.indexOf('An app board'), 'after the destinations, the last of them Capabilities').toBeGreaterThan(names.indexOf('Capabilities'))
    expect(names.indexOf('An app board'), 'above Settings').toBeLessThan(names.indexOf('Settings'))
  })
})

/** The sidebar top to bottom: each section header with the rows under it, the rows above the first
 *  header, and the rows pinned to the bottom, under ''. A destination is a row of the rail, its
 *  button, with its section's header line just above it where a section starts; the threads are a
 *  group of their own and no row here. The scroll area holds the sections, and the foot under it
 *  the pinned rows. */
function railGroups(): { header: string, rows: string[] }[] {
  const groups = [{ header: '', rows: [] as string[] }]
  const area = rail().querySelector('[data-rail-scroll]')!
  const foot = rail().querySelector('[data-rail-pinned]')
  for (const item of [...area.children, ...(foot ? [null, ...foot.children] : [])]) {
    // The foot's pinned rows end every section.
    if (item === null) { groups.push({ header: '', rows: [] }); continue }
    const button = item.lastElementChild
    if (item.tagName !== 'DIV' || item.getAttribute('role') === 'group' || !(button instanceof HTMLButtonElement)) continue
    const header = item.children.length === 2 ? item.firstElementChild : null
    if (header) groups.push({ header: header.textContent ?? '', rows: [] })
    groups[groups.length - 1].rows.push(button.getAttribute('aria-label') ?? '')
  }
  return groups
}

/** The destinations the full sidebar holds, top to bottom, each with the address its row opens. */
const DESTINATIONS = [
  ['Home', 'dashboard'],
  ['Chat', 'chat'],
  ['Projects', 'projects'],
  ['Library', 'library'],
  ['Inbox', 'inbox'],
  ['Automations', 'automations'],
  ['Capabilities', 'capabilities'],
  ['Settings', 'settings'],
] as const

describe('no sidebar header repeats a row under it', () => {
  it('no section header names a row under it', async () => {
    await open('dashboard')
    const groups = railGroups()
    expect(groups.filter((g) => g.header).length, 'the full sidebar draws its section headers').toBeGreaterThan(0)
    for (const { header, rows } of groups.filter((g) => g.header)) {
      expect(rows.map((r) => r.toLowerCase()), `the "${header}" header repeats a row under it`).not.toContain(header.toLowerCase())
    }
  })

  it('Automations and Capabilities keep their place and their order, with no header of their own', async () => {
    await open('dashboard')
    const labels = DESTINATIONS.map(([label]) => label) as string[]
    expect(railRows().filter((r) => labels.includes(r))).toEqual(labels)
    const headers = railGroups().map((g) => g.header)
    for (const name of ['Automations', 'Capabilities']) expect(headers).not.toContain(name)
  })

  it.each(DESTINATIONS.filter(([label]) => ['Automations', 'Capabilities'].includes(label)))(
    'the %s row still opens #/%s', async (label, address) => {
      await open('dashboard')
      fireEvent.click(within(rail()).getByRole('button', { name: label }))
      await settle()
      expect(location.hash).toBe(`#/${address}`)
      expect(railLit()).toEqual([label])
    })
})

describe('the palette and the catalogue cover every tab', () => {
  /** Each row: its id, its words, and the address it opens. */
  const ROWS = [
    ['go:capabilities', 'Capabilities', 'capabilities'],
    ['go:agents', 'Agents', 'agents'],
    ['go:skills', 'Skills', 'skills'],
    ['go:learning', 'Learning', 'learning'],
    ['go:tools', 'Tools', 'tools'],
    ['go:prompts', 'Prompts', 'prompts'],
    ['go:apps', 'Apps', 'apps'],
    ['go:store', 'Store', 'apps?view=store'],
    ['act:add-tool-server', 'Add a tool server', 'tools?add=1'],
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

  it('the catalogue lists Capabilities and every tab\'s address, each opened by the palette', () => {
    const entry = (route: string) => SHIPPED_ROUTES.find((e) => e.route === route)
    expect(entry('capabilities')?.from).toEqual(expect.arrayContaining(['router', 'palette']))
    for (const [, , address] of ROWS) {
      expect(entry(address)?.from, `#/${address}`).toContain('palette')
      expect(entry(address)?.lands, `#/${address} opens as itself`).toBeUndefined()
    }
  })
})
