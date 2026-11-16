/** A Power user view tab's fact never says 0.
 *
 *  Each tab says one fact under its name: "18,204 people, places and ideas", "5,012", "212", "3 to
 *  decide", "12 waiting to be read". A tab with nothing to say says nothing, so a 0 never reads as a
 *  fact, and an unread count reads as nothing rather than as a 0. */
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
  answers.library = answer([row('knowledge:k1', 'Router manual')])
  answers.knowledgeIntents = { intents: [] }
  answers.researchReports = { reports: [] }
  answers.librarySettings = { power_user_view: true }
})
afterEach(cleanup)

function mount() {
  const r = route({ view: 'items' })
  render(<LibraryPage sub="" navEpoch={0} query={r.state.query} setQuery={r.setQuery} navigate={r.navigate} />)
}
const fact = (name: string) => screen.getByRole('tab', { name: new RegExp(`^${name}`) }).querySelector('[data-type="caption"]')?.textContent ?? ''

describe("a tab's fact never says zero", () => {
  it('🔑 a populated library: each tab says its one fact', async () => {
    answers.knowledgeStats = {
      items: 5012, entities: 18204, relations: 41880, embeddings: { enabled: true },
      enrichment: { entities: { ran: 5000, failed: 0, running: 2, skipped: 0, not_run: 10 } },
    }
    answers.knowledgeTagTree = Array.from({ length: 212 }, (_, i) => ({ name: `t${i}` }))
    answers.knowledgeConflicts = { conflicts: [{ id: 'c1' }, { id: 'c1' }, { id: 'c2' }, { id: 'c3' }, { id: 'c4', choice: 'both' }] }
    mount()
    await screen.findByRole('tablist', { name: 'Library views' })
    await screen.findByText('18,204 people, places and ideas')
    expect(fact('Items')).toBe('5,012')
    expect(await screen.findByText('212')).toBeTruthy()
    expect(fact('Conflicts')).toBe('3 to decide')
    expect(fact('Library settings')).toBe('12 waiting to be read')
  })

  it('🔴 an empty library: no tab says 0, and none says anything', async () => {
    answers.knowledgeStats = { items: 0, entities: 0, relations: 0, embeddings: { enabled: false },
      enrichment: { entities: { ran: 0, failed: 0, running: 0, skipped: 0, not_run: 0 } } }
    answers.knowledgeTagTree = []
    answers.knowledgeConflicts = { conflicts: [{ id: 'c4', choice: 'both' }] }
    mount()
    await screen.findByRole('tablist', { name: 'Library views' })
    for (const t of ['Graph', 'Items', 'Tags', 'Conflicts', 'Decisions', 'Library settings']) {
      expect(fact(t), `${t} says nothing`).toBe('')
    }
    expect(screen.getByRole('tablist').textContent).not.toMatch(/\b0\b/)
  })

  it('a count it could not read reads as nothing, not as 0', async () => {
    answers.knowledgeStats = new Error('gateway down')
    answers.knowledgeTagTree = new Error('gateway down')
    answers.knowledgeConflicts = new Error('gateway down')
    mount()
    await screen.findByRole('tablist', { name: 'Library views' })
    expect(screen.getByRole('tablist').textContent).not.toMatch(/\b0\b/)
  })
})
