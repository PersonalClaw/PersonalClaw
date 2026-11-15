/** Old Knowledge and Artifacts addresses open the library.
 *
 *  Every `shippedRoutes.ts` entry of the Knowledge list, the Artifacts grid and the Sources and Reports
 *  pages lands on its library address, through one redirect row each; and the old Sources and Reports pages
 *  open the set-up panel with their own group at the top of the pane, however many intents a home has.
 *  (The four `view=` values the library draws in place are `theOldViewsStillDrawInTheLibrary`'s.) */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { SAMPLE, SHIPPED_ROUTES } from '../../app/shippedRoutes'
import { redirectFor } from '../../app/routeRedirects'
import { answers, resetGateway } from '../../test/libraryHarness'

vi.mock('../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../test/libraryHarness')
  return { ...(await orig<typeof import('../../lib/api')>()), api: gatewayApi() }
})
const { SetupPanel } = await import('./setup/SetupPanel')
const { setupViewOf } = await import('./setup/SetupPane')
const { resetDataStore } = await import('../../lib/data/store')

/** An address as the router reads it: its query in one order, each value decoded. */
const normal = (a: string) => {
  const [path, q = ''] = a.split('?')
  const query = [...new URLSearchParams(q)].sort(([x], [y]) => x.localeCompare(y))
  return query.length ? `${path}?${new URLSearchParams(query).toString()}` : path
}

const LANDS: [string, string][] = [
  ['knowledge', 'library'],
  ['artifacts', 'library'],
  ['knowledge/sources', 'library?setup=feeds'],
  ['knowledge/sources/new', 'library?setup=source:new'],
  ['knowledge/reports', 'library?setup=reports'],
  ['knowledge?q=router&type=pdf&tag=perf', 'library?q=router&type=pdf&tag=perf'],
  [`knowledge?item=${SAMPLE.knowledge}`, `library?item=${SAMPLE.knowledge}`],
  ['knowledge?collection=c1&project=p1', 'library?collection=c1&project=p1'],
  ['artifacts?q=plan&kind=html&src=chat&col=c1&sort=updated', 'library?q=plan&kind=html&made=chat&collection=c1&sort=updated'],
]

describe('old Knowledge and Artifacts addresses open the library', () => {
  it.each(LANDS)('🔑 #/%s opens #/%s', (from, to) => {
    expect(normal(redirectFor(from) ?? ''), `#/${from}`).toBe(normal(to))
  })

  it('🔑 every catalogued Knowledge and Artifacts list address lands on a library address the catalogue holds', () => {
    const old = SHIPPED_ROUTES.filter((e) => /^(knowledge|artifacts)(\?|$|\/sources|\/reports)/.test(e.route))
    expect(old.length, 'vacuity floor').toBeGreaterThanOrEqual(12)
    for (const e of old) {
      const target = redirectFor(e.route)
      expect(target, `#/${e.route} is retired`).not.toBeNull()
      expect(normal(e.lands ?? ''), `#/${e.route}'s lands`).toBe(normal(target!))
      expect(target!.startsWith('library'), `#/${e.route} opens the library`).toBe(true)
      expect(SHIPPED_ROUTES.some((x) => normal(x.route) === normal(target!) && !x.lands), `#/${target} is an entry of its own`).toBe(true)
    }
  })

  it('the item pages keep their addresses', () => {
    for (const a of ['knowledge/new', `knowledge/item/${SAMPLE.knowledge}`, `knowledge/item/${SAMPLE.knowledge}?read=1`, `artifacts/${SAMPLE.artifact}`]) {
      expect(redirectFor(a), `#/${a}`).toBeNull()
    }
  })
})

describe('the old Sources and Reports pages land on their own group of the panel', () => {
  const scrolled: string[] = []
  beforeEach(() => {
    resetDataStore(); resetGateway(); scrolled.length = 0
    Element.prototype.scrollIntoView = vi.fn(function (this: Element) { scrolled.push(this.getAttribute('aria-label') ?? '') })
    // A home with many intents above the reports and feeds.
    answers.knowledgeIntents = { intents: Array.from({ length: 12 }, (_, i) => ({ id: `i${i}`, goal: `Intent ${i}`, enabled: true, outcome_count: i })) }
    answers.researchReports = { reports: [] }
    answers.knowledgeSources = { sources: [], kinds: [] }
    answers.intentRun = new Error('no run')
  })
  afterEach(() => { cleanup(); delete (Element.prototype as Partial<Element>).scrollIntoView })

  it.each([['knowledge/sources', 'Feeds, folders and apps'], ['knowledge/reports', 'Reports']])(
    '🔑 #/%s scrolls %s to the top of the pane', async (from, group) => {
      const setup = new URLSearchParams(redirectFor(from)!.split('?')[1]).get('setup')!
      const v = setupViewOf(setup)
      expect(v?.view).toBe('panel')
      render(<SetupPanel focus={v?.view === 'panel' ? v.focus: undefined} nav={{ form: vi.fn(), item: vi.fn() }} onClose={vi.fn()} />)
      await screen.findByText('Intent 11')
      expect(scrolled).toEqual([group])
    })

  it('#/library?setup=all opens the panel at its top, scrolling nothing', async () => {
    const v = setupViewOf('all')
    render(<SetupPanel focus={v?.view === 'panel' ? v.focus: undefined} nav={{ form: vi.fn(), item: vi.fn() }} onClose={vi.fn()} />)
    await screen.findByText('Intent 11')
    expect(scrolled).toEqual([])
  })
})
