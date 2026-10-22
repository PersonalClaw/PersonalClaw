/** Triggers and Workflows are one destination, Automations, and every old address still opens (#3816).
 *
 *  Two sidebar rows become one. Its first tab is the one list of everything that runs without you,
 *  at `#/automations`; its second is Workflows, at the address its page always had. The list's own
 *  address from before, `#/triggers`, and every sub-route of it and of `#/workflows` open the page
 *  they did, now under the Automations strip, so a bookmark, a notification's link, an old
 *  transcript's and a copied one land where they did.
 *
 *  The selected automation and the run its history has open are in the address (`?open=`,
 *  `?run=`), so a reload, Back and Forward and a copied link restore both.
 *
 *  The real shell is booted as `main.tsx` boots it, on a set-up home whose reads are still on their
 *  way unless a case answers them, at each address, and asserted on what a person sees: the tab the
 *  strip has checked, the page's own heading, the address the bar holds, the sidebar row that is lit,
 *  and the run that is open.
 */
import { StrictMode } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { SAMPLE, SHIPPED_ROUTES } from './shippedRoutes'
import { redirectFor } from './routeRedirects'
import type { Command } from './CommandPalette'

/** The rows the shell last handed the palette. */
let rows: readonly Command[] = []
/** The reads a case answers, by method. Every other read is still on its way. */
let answers: Record<string, unknown> = {}

vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {}, refreshKinds: () => [] }))
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
const { invalidateKeys } = await import('../lib/data')

installAppSdk()
registerBuiltinContentTypes()

beforeAll(async () => {
  await Promise.all([
    import('../pages/triggers/TriggersSection'),
    import('../pages/workflows/WorkflowsSection'),
    import('../pages/dashboard/Home'),
  ])
}, 120_000)

beforeEach(() => {
  vi.stubGlobal('fetch', () => new Promise(() => {}))
  localStorage.clear()
  sessionStorage.clear()
  answers = {}
  invalidateKeys('automations:list')
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

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
    const now = `${location.hash}|${checkedTab()}|${heading()}|${openRun()}`
    still = now === last && document.querySelector('main') ? still + 1 : 0
    last = now
  }
}

/** The Automations strip, or null where it is not drawn. */
const strip = () => screen.queryByRole('radiogroup', { name: 'Automations' })
function checkedTab() {
  const s = strip()
  if (!s) return 'no Automations strip'
  return within(s).getAllByRole('radio').filter((r) => r.getAttribute('aria-checked') === 'true').map((r) => r.textContent).join(', ')
}
const heading = () => document.querySelector('main h1')?.textContent ?? ''
const rail = () => document.querySelector('nav[data-tour="rail"]') as HTMLElement
const railRows = () => within(rail()).getAllByRole('button').map((b) => b.getAttribute('aria-label') ?? '')
const railLit = () => within(rail()).queryAllByRole('button').filter((b) => b.getAttribute('aria-current') === 'page')
  .map((b) => b.getAttribute('aria-label'))
/** The run the open automation's history has expanded, by its summary, or ''. */
const openRun = () => [...document.querySelectorAll('button[aria-expanded="true"]')].map((b) => b.textContent ?? '').find((t) => t.includes('digest')) ?? ''

/** The deeper addresses each old route had, each with the tab it opens on and the heading its page
 *  draws there. Each opens as itself. */
const OLD = [
  ['triggers', 'Automations', 'Automations'],
  ['triggers?view=week', 'Automations', 'Automations'],
  ['triggers/new', 'Automations', 'New trigger'],
  ['triggers/new?kind=lifecycle', 'Automations', 'New trigger'],
  [`triggers/new?kind=schedule&preset=${SAMPLE.preset}`, 'Automations', 'New trigger'],
  [`triggers?open=${SAMPLE.trigger}`, 'Automations', 'Automations'],
  ['workflows', 'Workflows', 'Workflows'],
  ['workflows?tab=defs', 'Workflows', 'Workflows'],
  [`workflows/runs/${SAMPLE.run}`, 'Workflows', null],
  [`workflows/runs/${SAMPLE.run}?node=${SAMPLE.node}`, 'Workflows', null],
  [`workflows/defs/${SAMPLE.workflow}`, 'Workflows', null],
  [`workflows/defs/${SAMPLE.workflow}/edit`, 'Workflows', null],
  [`workflows/defs/${SAMPLE.workflow}/edit?from=2`, 'Workflows', null],
] as const

describe('Automations is one destination', () => {
  it('#/automations, the sidebar row\'s own address, opens the one list', async () => {
    await open('automations')
    expect(checkedTab()).toBe('Automations')
    expect(heading()).toBe('Automations')
    expect(location.hash).toBe('#/automations')
    expect(railLit()).toEqual(['Automations'])
  })

  it.each(OLD)('the old #/%s still opens, inside Automations on the %s tab', async (address, tab, title) => {
    await open(address)
    expect(location.hash, 'it opens as itself: no address is retired').toBe(`#/${address}`)
    expect(checkedTab()).toBe(tab)
    expect(railLit()).toEqual(['Automations'])
    if (title) expect(heading()).toBe(title)
    expect(redirectFor(address), 'no row of the redirect table names it').toBeNull()
  })

  it('#/automations/new is the create page, and the list\'s New opens it', async () => {
    await open('automations/new')
    expect(heading()).toBe('New trigger')
    expect(checkedTab()).toBe('Automations')
  })

  it('choosing a tab writes its address in place of the one before, and keeps the strip', async () => {
    await open('automations')
    const before = strip()
    const entries = history.length
    fireEvent.click(within(before!).getByRole('radio', { name: 'Workflows' }))
    await settle()
    expect(location.hash).toBe('#/workflows')
    expect(heading()).toBe('Workflows')
    expect(history.length, 'a tab replaces the entry: Back leaves Automations').toBe(entries)
    expect(strip()).toBe(before)
    fireEvent.click(within(strip()!).getByRole('radio', { name: 'Automations' }))
    await settle()
    expect(location.hash).toBe('#/automations')
  })

  it('the sidebar holds one Automations row, in place of Triggers and Workflows', async () => {
    await open('dashboard')
    const names = railRows()
    expect(names).toContain('Automations')
    for (const gone of ['Triggers', 'Workflows']) expect(names).not.toContain(gone)
  })
})

describe('the selected automation and its open run are in the address', () => {
  const DIGEST = {
    kind: 'schedule', id: 'schedule:nightly', raw_id: 'nightly', name: 'Nightly digest', enabled: true,
    schedule: 'every day at 9:00', action: { provider: 'notify', config: { message: 'hi' } }, state: 'active',
    next_run_ts: Math.floor(Date.now() / 1000) + 3600, group: 'next_to_run', rank: 0, reports_to: { kind: 'inbox' },
  }
  const RUNS = [
    { run_id: 'r-2', job_id: 'nightly', status: 'success', summary: 'Sent the digest', started_at: Math.floor(Date.now() / 1000) - 60 },
    { run_id: 'r-1', job_id: 'nightly', status: 'failure', summary: 'The digest failed', error: 'timed out', started_at: Math.floor(Date.now() / 1000) - 86400 },
  ]
  beforeEach(() => {
    answers = {
      automations: { jobs: [{ ...DIGEST, id: 'nightly' }], hooks: [], stores: [], callbacks: [], server_tz: 'UTC', unreadable: [] },
      triggerReview: [],
      actionProviders: [],
      triggerHistory: { runs: RUNS, total: 2 },
      triggerRunDetail: { ...RUNS[1], trace: 'the trace' },
      channelRuntimes: [],
    }
  })

  it('a copied link, or a reload, opens the automation and the run it names', async () => {
    await open('automations?open=schedule%3Anightly&run=r-1')
    expect(screen.getByRole('region', { name: 'Nightly digest' })).toBeTruthy()
    expect(openRun()).toContain('The digest failed')
    // A reload is the shell booted again on the address the bar holds.
    await open(location.hash.slice(2))
    expect(openRun()).toContain('The digest failed')
  })

  it('opening a run is an entry of history: Back closes it, Forward opens it again', async () => {
    await open('automations?open=schedule%3Anightly')
    expect(openRun()).toBe('')
    fireEvent.click(screen.getByRole('button', { name: /Sent the digest/ }))
    await settle()
    expect(location.hash).toBe('#/automations?open=schedule%3Anightly&run=r-2')
    expect(openRun()).toContain('Sent the digest')
    await act(async () => { history.back() })
    await settle()
    expect(location.hash).toBe('#/automations?open=schedule%3Anightly')
    expect(openRun()).toBe('')
    await act(async () => { history.forward() })
    await settle()
    expect(openRun()).toContain('Sent the digest')
  })
})

describe('the palette and the catalogue', () => {
  const ROWS = [
    ['go:automations', 'Automations', 'automations'],
    ['go:workflows', 'Workflows', 'workflows'],
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

  it('has no Triggers row: the list is Automations', async () => {
    await open('dashboard')
    expect(rows.find((r) => r.id === 'go:triggers')).toBeUndefined()
  })

  it('lists Automations, its selection and its run, and keeps every old address opening as itself', () => {
    const entry = (route: string) => SHIPPED_ROUTES.find((e) => e.route === route)
    expect(entry('automations')?.from).toEqual(expect.arrayContaining(['router', 'palette']))
    expect(entry(`automations?open=${SAMPLE.trigger}&run=${SAMPLE.run}`)).toBeTruthy()
    for (const [address] of OLD) {
      expect(entry(address), `#/${address} is catalogued`).toBeTruthy()
      expect(entry(address)?.lands, `#/${address} opens as itself`).toBeUndefined()
    }
  })
})
