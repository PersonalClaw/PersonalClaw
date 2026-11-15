/** The old Knowledge views still draw, in the library: `#/knowledge?view=` graph, tags, conflicts
 *  and decisions land on `#/library?view=` the same and open that Power user view tab for the visit,
 *  whatever the stored setting; home, library and intents land on their `library?view=` address and
 *  draw the list (with the set-up panel for intents) there. Each address is left as it is. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { redirectFor } from '../../app/routeRedirects'
import { answer, answers, calls, resetGateway, route, row } from '../../test/libraryHarness'

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../test/libraryHarness')
  return { ...(await orig<typeof import('../../lib/api')>()), api: gatewayApi() }
})
const { LibraryPage } = await import('./LibraryPage')
const { resetDataStore } = await import('../../lib/data/store')

beforeEach(() => {
  resetDataStore(); resetGateway(); localStorage.clear()
  answers.library = answer([row('knowledge:k1', 'Router manual')])
  answers.knowledgeIntents = { intents: [] }
  answers.researchReports = { reports: [] }
  answers.knowledgeSources = { sources: [], kinds: [] }
  answers.librarySettings = { power_user_view: false }
  answers.knowledgeStats = { items: 1, entities: 0, relations: 0, embeddings: { enabled: false } }
  answers.knowledgeConflicts = { conflicts: [] }
})
afterEach(cleanup)

/** Open the library at what `#/knowledge?view=<view>` lands on, and keep what the page writes. */
function landAt(view: string) {
  const to = redirectFor(`knowledge?view=${view}`)!
  expect(to, `#/knowledge?view=${view}`).toBe(`library?view=${view}`)
  const r = route(Object.fromEntries(new URLSearchParams(to.split('?')[1])))
  render(<LibraryPage sub="" navEpoch={0} query={r.state.query} setQuery={r.setQuery} navigate={r.navigate} />)
  return r
}

describe('the old views still draw in the library', () => {
  it.each([['graph', 'Graph'], ['tags', 'Tags'], ['conflicts', 'Conflicts'], ['decisions', 'Decisions']])(
    '🔑 view=%s opens its %s tab for the visit, with Items beside it', async (view, title) => {
      const r = landAt(view)
      expect(await screen.findByRole('heading', { level: 2, name: title })).toBeTruthy()
      const views = within(screen.getByRole('tablist', { name: 'Library views' }))
      expect(views.getByRole('tab', { name: new RegExp(`^${title}`), selected: true })).toBeTruthy()
      expect(views.getByRole('tab', { name: /^Items/ }), 'the way back to the list').toBeTruthy()
      expect(screen.getByText('On for this visit. Your setting is off.')).toBeTruthy()
      expect(screen.getByRole('button', { name: 'Power user view' }).getAttribute('aria-pressed')).toBe('true')
      expect(r.setQuery, 'the address is left as it is').not.toHaveBeenCalled()
      expect(calls.saveLibrarySettings, 'and a link writes no setting').toBeUndefined()
    })

  it.each([['home'], ['library']])('🔑 view=%s draws the list, the address unchanged', async (view) => {
    const r = landAt(view)
    expect(await screen.findByRole('button', { name: /^Router manual/ })).toBeTruthy()
    expect(r.setQuery).not.toHaveBeenCalled()
  })

  it('🔑 view=intents draws the list with the set-up panel in the pane, the address unchanged', async () => {
    const r = landAt('intents')
    expect(await screen.findByRole('button', { name: /^Router manual/ })).toBeTruthy()
    expect(await screen.findByRole('heading', { name: 'Feeds, intents and reports' })).toBeTruthy()
    expect(r.setQuery).not.toHaveBeenCalled()
  })
})
