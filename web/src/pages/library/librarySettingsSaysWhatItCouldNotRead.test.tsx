/** Library settings says what it couldn't read.
 *
 *  Its Conflicts row reads the library's conflicts: with some undecided it counts them with Review,
 *  with none it is not drawn, and when the read fails it says "Couldn't check for conflicts." with
 *  Review, which opens the Conflicts tab, where the read says why. It never reads a failed read as
 *  "no conflicts". */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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
  answers.library = answer([row('knowledge:k1', 'Router manual')], { facets: { kind: { notes: 3 } } })
  answers.knowledgeStats = { items: 3, entities: 0, relations: 0, embeddings: { enabled: false } }
  answers.librarySettings = { power_user_view: true }
  answers.embeddingReindexJobs = { jobs: [], active: null }
  answers.knowledgeIntents = { intents: [] }
  answers.researchReports = { reports: [] }
})
afterEach(cleanup)

function mount() {
  const r = route({ view: 'settings' })
  render(<LibraryPage sub="" navEpoch={0} query={r.state.query} setQuery={r.setQuery} navigate={r.navigate} />)
  return r
}

describe('Library settings says what it could not read', () => {
  it('🔑 undecided conflicts are counted, with Review', async () => {
    answers.knowledgeConflicts = { conflicts: [{ id: 'c1' }, { id: 'c2', choice: 'both' }] }
    mount()
    expect(await screen.findByText('1 place where items disagree')).toBeTruthy()
  })

  it('none undecided: no Conflicts row', async () => {
    answers.knowledgeConflicts = { conflicts: [{ id: 'c2', choice: 'both' }] }
    mount()
    await screen.findByRole('heading', { name: 'Library settings' })
    await screen.findByText('What search can see')
    const panel = within(screen.getByRole('region', { name: 'Library settings' }))
    expect(panel.queryByText('Conflicts'), 'no row for none').toBeNull()
  })

  it('🔴 Items counts what the library holds, the pages the agent made with the notes, each kind in its own words', async () => {
    // The knowledge store holds the 6 notes; the library lists them and the artifact the agent made.
    answers.library = answer([...Array.from({ length: 6 }, (_, i) => row(`knowledge:n${i}`, `Note ${i}`)), row('artifact:lisbon', 'Lisbon trip plan')],
      { facets: { kind: { note: 6, document: 1 } } })
    answers.knowledgeStats = { items: 6, entities: 2, relations: 1, embeddings: { enabled: false } }
    answers.knowledgeConflicts = { conflicts: [] }
    mount()
    expect(await screen.findByText('7 in your library: 6 notes, 1 document')).toBeTruthy()
    expect(screen.getByText('2 people, places and ideas, with 1 link between them')).toBeTruthy()
  })

  it('🔴 the breakdown sums to the total: a findings log is a report, as the library counts it, and every kind is named', async () => {
    // The library's own count holds the notes, the artifact, a report's write-up and an intent's findings
    // log (a `report` row): the breakdown names each, and its parts add up to the total.
    answers.library = answer([row('knowledge:n1', 'Router manual'), row('knowledge:n2', 'Seed list'), row('artifact:lisbon', 'Lisbon trip plan'),
      row('report:rpt-1', 'Weekly perf digest', { kind: 'report' }), row('findings:homelab', 'Anything that could improve my homelab', { kind: 'report' }),
      row('knowledge:w1', 'A widget', { kind: 'widget' })],
    { facets: { kind: { note: 2, document: 1, report: 2, widget: 1 } } })
    answers.knowledgeStats = { items: 4, entities: 0, relations: 0, embeddings: { enabled: false } }
    answers.knowledgeConflicts = { conflicts: [] }
    mount()
    const says = (await screen.findByText(/in your library:/)).textContent ?? ''
    expect(says).toBe('6 in your library: 2 notes, 2 reports, 1 document, 1 item')
    const [total, ...parts] = [...says.matchAll(/\d+/g)].map((m) => Number(m[0]))
    expect(parts.reduce((a, b) => a + b, 0), 'the parts add up to the total').toBe(total)
  })

  it('🔴 a kind with no words of its own is an item, never its key or a blank', async () => {
    const { kindCount } = await import('./LibrarySettingsPanel')
    expect([kindCount('widget', 1), kindCount('widget', 3), kindCount('document', 1)]).toEqual(['1 item', '3 items', '1 document'])
  })

  it('every kind the Filter names has its own words in a count', async () => {
    const { KINDS } = await import('./libraryMeta')
    const { KIND_WORDS } = await import('./LibrarySettingsPanel')
    expect(KINDS.filter((k) => !KIND_WORDS[k.key]).map((k) => k.key)).toEqual([])
  })

  it('🔴 a library of only the pages the agent made is not empty', async () => {
    answers.library = answer([row('artifact:lisbon', 'Lisbon trip plan')], { facets: { kind: { document: 1 } } })
    answers.knowledgeStats = { items: 0, entities: 0, relations: 0, embeddings: { enabled: false } }
    answers.knowledgeConflicts = { conflicts: [] }
    mount()
    expect(await screen.findByText('1 in your library: 1 document')).toBeTruthy()
    expect(screen.queryByText('Nothing in your library yet.')).toBeNull()
    const again = screen.getByRole('button', { name: 'Process everything again…' })
    expect(again.getAttribute('aria-disabled') === 'true' || (again as HTMLButtonElement).disabled, 'nothing to read again').toBe(true)
  })

  it('🔴 a failed read says so, never "none", and Review opens the Conflicts tab', async () => {
    answers.knowledgeConflicts = new Error('gateway down')
    const r = mount()
    expect(await screen.findByText("Couldn't check for conflicts.")).toBeTruthy()
    const review = screen.getAllByRole('button', { name: 'Review' })
    expect(review).toHaveLength(1)
    await userEvent.click(review[0])
    await waitFor(() => expect(r.state.query.view).toBe('conflicts'))
  })
})
