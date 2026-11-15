/** The Power user view opens on the graph.
 *
 *  With it on, the bare `#/library` opens Graph; the tabs run Graph, Items, Tags, Conflicts,
 *  Decisions, with Library settings pinned last under a hairline; and any library filter key, or a
 *  search, opens Items, since it acts on Items' rows. Turning it on opens Graph; turning it off
 *  returns to the list with what was selected. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { answer, answers, resetGateway, route, row } from '../../test/libraryHarness'

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../test/libraryHarness')
  return { ...(await orig<typeof import('../../lib/api')>()), api: gatewayApi() }
})
const { LibraryPage } = await import('./LibraryPage')
const { powerUserPlace } = await import('./powerUserView')
const { resetDataStore } = await import('../../lib/data/store')

beforeEach(() => {
  resetDataStore(); resetGateway(); localStorage.clear()
  answers.library = answer([row('knowledge:k1', 'Router manual')])
  answers.knowledgeIntents = { intents: [] }
  answers.researchReports = { reports: [] }
  answers.knowledgeStats = { items: 1, entities: 0, relations: 0, embeddings: { enabled: false } }
  answers.knowledgeConflicts = { conflicts: [] }
  answers.librarySettings = { power_user_view: true }
  answers.saveLibrarySettings = { power_user_view: true }
})
afterEach(cleanup)

const mount = (query: Record<string, string> = {}) => {
  const r = route(query)
  render(<LibraryPage sub="" navEpoch={0} query={r.state.query} setQuery={r.setQuery} navigate={r.navigate} />)
  return r
}
const tabNames = () => screen.getAllByRole('tab').map((t) => t.querySelector('[data-type="label-l"]')?.textContent)

describe('the Power user view opens on the graph', () => {
  it('🔑 on, the bare address opens Graph, the first tab', async () => {
    mount()
    expect(await screen.findByRole('heading', { level: 2, name: 'Graph' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: /^Graph/ }).getAttribute('aria-selected')).toBe('true')
  })

  it('🔑 the tabs run in their order, with Library settings last', async () => {
    mount()
    await screen.findByRole('tablist', { name: 'Library views' })
    expect(tabNames()).toEqual(['Graph', 'Items', 'Tags', 'Conflicts', 'Decisions', 'Library settings'])
  })

  it.each([['tag', 'homelab'], ['kind', 'notes'], ['state', 'being_read'], ['q', 'router'], ['collection', 'c1']])(
    '🔑 a %s in the address opens Items', async (key, value) => {
      mount({ [key]: value })
      await screen.findByRole('button', { name: /^Router manual/ })
      expect(screen.getByRole('tab', { name: /^Items/ }).getAttribute('aria-selected')).toBe('true')
    })

  it('🔑 turning it on opens Graph, and turning it off returns to the list with what was selected', async () => {
    answers.librarySettings = { power_user_view: false }
    const r = mount({ item: 'knowledge:k1' })
    await screen.findByRole('button', { name: /^Router manual/ })
    await userEvent.click(screen.getByRole('button', { name: 'Power user view' }))
    await waitFor(() => expect(r.state.query.view).toBe('graph'))
    expect(r.state.query.item, 'the selection is kept').toBe('knowledge:k1')
  })

  it('🔑 typing a search on another tab opens Items with it', async () => {
    const r = mount({ view: 'graph' })
    await screen.findByRole('heading', { level: 2, name: 'Graph' })
    await userEvent.type(screen.getByRole('searchbox'), 'router')
    await waitFor(() => expect(r.state.query).toMatchObject({ view: 'items', q: 'router' }))
  })

  it('the place is read from the address and the stored setting alone', () => {
    expect(powerUserPlace({}, true, false)).toEqual({ tab: 'graph', visit: false })
    expect(powerUserPlace({}, false, false)).toEqual({ tab: null, visit: false })
    expect(powerUserPlace({ tag: 'x' }, true, true)).toEqual({ tab: 'items', visit: false })
    expect(powerUserPlace({ view: 'tags' }, false, false)).toEqual({ tab: 'tags', visit: true })
  })
})
