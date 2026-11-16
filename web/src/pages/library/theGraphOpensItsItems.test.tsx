/** The graph opens the items that mention a name.
 *
 *  A name picked in the Graph tab says how many items mention it, what it is linked to and four of
 *  them; Show all 9 in Items opens Items with `entity=NAS`, whose count line says "9 items that
 *  mention NAS · Clear". One of the four opens that item in Items, the same way. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { answer, answers, calls, resetGateway, route, row } from '../../test/libraryHarness'

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../test/libraryHarness')
  return { ...(await orig<typeof import('../../lib/api')>()), api: gatewayApi() }
})
const { LibraryPage } = await import('./LibraryPage')
const { resetDataStore } = await import('../../lib/data/store')
const { countLine } = await import('./libraryWords')

const MENTIONS = Array.from({ length: 9 }, (_, i) => ({ id: `k${i + 1}`, title: `Rack note ${i + 1}` }))

beforeEach(() => {
  resetDataStore(); resetGateway(); localStorage.clear()
  // The graph reads its own endpoint through `fetch`: an empty 200.
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ nodes: [], edges: [] }) })))
  answers.library = answer(MENTIONS.map((m) => row(`knowledge:${m.id}`, m.title)))
  answers.knowledgeIntents = { intents: [] }
  answers.researchReports = { reports: [] }
  answers.knowledgeStats = { items: 9, entities: 3, relations: 2, embeddings: { enabled: false } }
  answers.knowledgeConflicts = { conflicts: [] }
  answers.librarySettings = { power_user_view: true }
  answers.knowledgeEntityItems = MENTIONS
  answers.knowledgeEntityRelated = { related: [{ name: 'Backups', relation_type: 'uses', outgoing: true }] }
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function mount(query: Record<string, string>) {
  const r = route(query)
  render(<LibraryPage sub="" navEpoch={0} query={r.state.query} setQuery={r.setQuery} navigate={r.navigate} />)
  return r
}

describe('the graph opens its items', () => {
  it('🔑 a picked name says how many items mention it, what it links to, and four of them', async () => {
    mount({ view: 'graph', entity: 'NAS' })
    expect(await screen.findByRole('heading', { level: 3, name: 'NAS' })).toBeTruthy()
    expect(await screen.findByText('Mentioned in 9 items')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Backups' })).toBeTruthy()
    for (const m of MENTIONS.slice(0, 4)) expect(screen.getByRole('button', { name: m.title })).toBeTruthy()
    expect(screen.queryByRole('button', { name: MENTIONS[4].title })).toBeNull()
  })

  it('🔑 Show all 9 in Items opens Items with entity=NAS', async () => {
    const r = mount({ view: 'graph', entity: 'NAS' })
    await userEvent.click(await screen.findByRole('button', { name: 'Show all 9 in Items' }))
    await waitFor(() => expect(r.state.query).toMatchObject({ view: 'items', entity: 'NAS' }))
  })

  it('🔑 and Items reads them, its count line saying "9 items that mention NAS · Clear"', async () => {
    mount({ view: 'items', entity: 'NAS' })
    await screen.findByRole('button', { name: /^Rack note 1/ })
    expect(calls.library.at(-1)?.[0]).toMatchObject({ entity: 'NAS' })
    expect(screen.getByText('9 items that mention NAS', { selector: '[data-list-line] [data-line-part]' })).toBeTruthy()
    // And the narrowed list's announcement says the same words: the line's part and the live region.
    expect(screen.getAllByText('9 items that mention NAS')).toHaveLength(2)
    expect(screen.getByRole('button', { name: 'Clear' })).toBeTruthy()
    expect(countLine({ query: { entity: 'NAS' }, data: { total: 1 } as never, projectName: '', wordsOnly: false }).lead).toBe('1 item that mentions NAS')
  })

  it('🔴 a links read that failed says so, and the name is not drawn as linked to nothing', async () => {
    answers.knowledgeEntityRelated = new Error('gateway down')
    mount({ view: 'graph', entity: 'NAS' })
    expect(await screen.findByText("Couldn't read what it is linked to.")).toBeTruthy()
    expect(screen.getByRole('heading', { level: 4, name: 'Linked to' })).toBeTruthy()
  })

  it('one of the four opens that item in Items', async () => {
    const r = mount({ view: 'graph', entity: 'NAS' })
    await userEvent.click(await screen.findByRole('button', { name: 'Rack note 2' }))
    await waitFor(() => expect(r.state.query).toMatchObject({ view: 'items', entity: 'NAS', item: 'knowledge:k2' }))
  })
})
