/** The library lists each thing once.
 *
 *  What the gateway answers is the set: an artifact, a note, a report's row and what an intent found,
 *  one row each (the gateway folds an artifact's search mirror into the artifact, and an intent that found
 *  nothing has no row). The page draws exactly those rows, each once, named by its title, and no row
 *  is an intent or its set-up. */
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

beforeEach(() => { resetDataStore(); resetGateway(); localStorage.clear() })
afterEach(cleanup)

function mount(query: Record<string, string> = {}) {
  const r = route(query)
  render(<LibraryPage sub="" navEpoch={0} query={r.state.query} setQuery={r.setQuery} navigate={r.navigate} />)
  return r
}

const ROWS = [
  row('artifact:lisbon', 'Lisbon trip plan'),
  row('knowledge:k1', 'Router manual'),
  row('report:rpt-1a2b3c4d', 'Weekly perf digest'),
  row('findings:power-bill', 'Cheap ways to cut the power bill'),
]

describe('the library lists each thing once', () => {
  it('🔑 draws one row per thing the gateway answers, each named by its title', async () => {
    answers.library = answer(ROWS)
    answers.knowledgeIntents = { intents: [
      { id: 'power-bill', goal: 'Cheap ways to cut the power bill', enabled: true, outcome_count: 22 },
      { id: 'freezer', goal: 'Recipes that use what is in the freezer', enabled: true, outcome_count: 0 },
    ] }
    answers.researchReports = { reports: [] }
    mount()
    const named = (title: string) => screen.getAllByRole('button').filter((b) => (b.getAttribute('aria-label') ?? '').startsWith(title))
    await screen.findAllByRole('button', { name: /^Router manual/ })
    expect(named('Lisbon trip plan')).toHaveLength(1)
    expect(named('Router manual')).toHaveLength(1)
    expect(named('Weekly perf digest')).toHaveLength(1)
    expect(named('Findings: Cheap ways to cut the power bill')).toHaveLength(1)
  })

  it('🔑 no row is an intent: one that found nothing has none, and its set-up is not in the list', async () => {
    answers.library = answer(ROWS)
    answers.knowledgeIntents = { intents: [{ id: 'freezer', goal: 'Recipes that use what is in the freezer', enabled: true, outcome_count: 0 }] }
    answers.researchReports = { reports: [] }
    mount()
    await screen.findAllByRole('button', { name: /^Router manual/ })
    const list = screen.getByRole('list', { name: 'Library items' })
    expect(list.textContent).not.toContain('Recipes that use what is in the freezer')
    expect(list.textContent).not.toContain('Checking new items')
  })
})
