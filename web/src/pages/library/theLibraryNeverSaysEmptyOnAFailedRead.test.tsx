/** The library never says it is empty because a read failed.
 *
 *  A failed read is `LoadError`'s own words, never "Your library is empty"; a store that could not be
 *  read is the notice above the rows that were; and a home that holds only what the agent made is not
 *  an empty library. */
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

beforeEach(() => {
  resetDataStore(); resetGateway(); localStorage.clear()
  answers.knowledgeIntents = { intents: [] }
  answers.researchReports = { reports: [] }
})
afterEach(cleanup)

function mount() {
  const r = route()
  render(<LibraryPage sub="" navEpoch={0} query={r.state.query} setQuery={r.setQuery} navigate={r.navigate} />)
}

describe('the library never says empty on a failed read', () => {
  it('🔑 a failed read says it could not load, and offers Retry', async () => {
    answers.library = new Error('the gateway did not answer')
    mount()
    expect(await screen.findByText(/Couldn.t load your library/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeTruthy()
    expect(screen.queryByText('Your library is empty')).toBeNull()
    // Nor does the header take it for a first run: Search and the Filter stay.
    expect(screen.getByRole('searchbox'), 'the search stays').toBeTruthy()
    expect(screen.getByRole('button', { name: /^Filter/ }), 'and the Filter').toBeTruthy()
  })

  it('🔑 a store that could not be read is the notice over the rows that were', async () => {
    answers.library = answer([row('knowledge:k1', 'Router manual')], { failed: ['artifacts'], complete: false })
    mount()
    expect(await screen.findByText(/pages and documents the agent made couldn.t be loaded/i)).toBeTruthy()
    expect(screen.getByRole('button', { name: /^Router manual/ })).toBeTruthy()
    expect(screen.queryByText('Your library is empty')).toBeNull()
  })

  it('an artifact-only home is not empty', async () => {
    answers.library = answer([row('artifact:lisbon', 'Lisbon trip plan')])
    mount()
    expect(await screen.findByRole('button', { name: /^Lisbon trip plan/ })).toBeTruthy()
    expect(screen.queryByText('Your library is empty')).toBeNull()
  })

  it('🔴 control: an empty home says so', async () => {
    answers.library = answer([])
    mount()
    expect(await screen.findByText('Your library is empty')).toBeTruthy()
  })
})
