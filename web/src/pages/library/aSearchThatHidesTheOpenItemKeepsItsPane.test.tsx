/** A search or filter that hides the open item does not take it out of its pane.
 *
 *  Driven (dogfood, 11-21): with a note open (`?item=knowledge:<id>`), a search for a word it does
 *  not hold listed "0 matches", and the pane, on the same address, read "Nothing in your library has
 *  this address." The note was there; the search only left it out of the list. The pane read the
 *  item from the list alone. Now it keeps the item it showed while a search or filter hides it, and
 *  says so; an item a search hides that it never showed is said to be hidden, not missing. With
 *  nothing narrowing the list, an address no item has still says so. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { answer, answers, resetGateway, route, row } from '../../test/libraryHarness'

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../test/libraryHarness')
  return { ...(await orig<typeof import('../../lib/api')>()), api: gatewayApi() }
})
const { LibraryPage } = await import('./LibraryPage')
const { resetDataStore } = await import('../../lib/data/store')

const NOTE = row('knowledge:k1', 'Router manual')
const opened = () => screen.queryByRole('heading', { level: 2, name: 'Router manual' })
const HIDES = 'The search or filter leaves this item out of the list.'
const GONE = 'Nothing in your library has this address.'

beforeEach(() => {
  resetDataStore(); resetGateway(); localStorage.clear()
  answers.knowledgeIntents = { intents: [] }
  answers.researchReports = { reports: [] }
  answers.knowledgeSources = { sources: [], kinds: [] }
  answers.intentCandidates = { total: 0 }
  // The read answers what its search leaves: the note holds no "zebra".
  answers.library = (p: { q?: string } | undefined) => (p?.q ? answer([]) : answer([NOTE]))
})
afterEach(cleanup)

const page = (query: Record<string, string>) => {
  const r = route(query)
  return <LibraryPage sub="" navEpoch={0} query={query} setQuery={r.setQuery} navigate={r.navigate} />
}

describe('the open item and a search that hides it', () => {
  it('🔴 keeps the item it showed in its pane, and says the search hides it', async () => {
    const view = render(page({ item: NOTE.ref }))
    await screen.findByRole('heading', { level: 2, name: 'Router manual' })

    view.rerender(page({ item: NOTE.ref, q: 'zebra' }))
    await screen.findByText(HIDES)

    expect(screen.queryByText(GONE)).toBeNull()
    expect(opened(), 'the open item left its pane').not.toBeNull()
  })

  it('🔴 says a search hides an item it never showed, not that nothing has its address', async () => {
    render(page({ item: NOTE.ref, q: 'zebra' }))

    expect(await screen.findByText(HIDES)).toBeTruthy()
    expect(screen.queryByText(GONE)).toBeNull()
  })

  it('still says nothing has an address no item has, with nothing narrowing the list', async () => {
    render(page({ item: 'knowledge:gone' }))

    expect(await screen.findByText(GONE)).toBeTruthy()
    expect(screen.queryByText(HIDES)).toBeNull()
  })
})
