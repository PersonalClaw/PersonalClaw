/** A search says what it shows: a capped search says it shows the best
 *  matches, a search with no model to read meaning says it matches words only and offers to set one
 *  up, a passage found by meaning alone says so, and a failed search is a failure, not "no matches". */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { answer, answers, resetGateway, route, row } from '../../test/libraryHarness'
import { countLine } from './libraryWords'

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../test/libraryHarness')
  return { ...(await orig<typeof import('../../lib/api')>()), api: gatewayApi() }
})
const { LibraryPage } = await import('./LibraryPage')
const { resetDataStore } = await import('../../lib/data/store')

beforeEach(() => {
  resetDataStore(); resetGateway(); localStorage.clear()
  answers.knowledgeIntents = { intents: [] }
  answers.researchReports = { reports: [] }
})
afterEach(cleanup)

const mount = (query: Record<string, string>) => {
  const r = route(query)
  render(<LibraryPage sub="" navEpoch={0} query={r.state.query} setQuery={r.setQuery} navigate={r.navigate} />)
  return r
}

describe('a search says what it shows', () => {
  it('🔑 capped: it shows the best matches and says how to narrow it', () => {
    const data = answer(Array.from({ length: 100 }, (_, i) => row(`knowledge:k${i}`, `Note ${i}`)), { total: 340, capped: true })
    expect(countLine({ query: { q: 'plan' }, data, projectName: '', wordsOnly: false }))
      .toEqual({ lead: 'Showing the best 100 matches.', line: 'Add a word or a filter to narrow it.', actions: [] })
  })

  it('🔑 words only: it says so, and offers to set up search by meaning', () => {
    const data = answer([row('knowledge:k1', 'Router manual')], { total: 23 })
    expect(countLine({ query: { q: 'router' }, data, projectName: '', wordsOnly: true })).toEqual({
      lead: '23 matches', line: 'Matching words only.', actions: [{ key: 'set-up-meaning', label: 'Set up search by meaning' }],
    })
  })

  it('🔑 a passage found by meaning alone says so on its row', async () => {
    answers.knowledgeStats = { items: 1, entities: 0, relations: 0, embeddings: { enabled: true } }
    answers.library = answer([row('knowledge:k1', 'Router manual', { match: { passage: 'the uplink port', meaning_only: true } })])
    mount({ q: 'wan' })
    expect(await screen.findByText(/Similar meaning: the uplink port/)).toBeTruthy()
  })

  it('🔑 a failed search is a failure, never "nothing matches"', async () => {
    answers.library = new Error('the search failed')
    mount({ q: 'router' })
    expect(await screen.findByText(/Couldn.t load your library/)).toBeTruthy()
    expect(screen.queryByText(/Nothing matches/)).toBeNull()
  })
})
