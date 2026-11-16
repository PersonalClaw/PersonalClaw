/** Library settings' "Being read" row counts what its Show them lists.
 *
 *  Found walking the app with no model set up: the row said "3 waiting · 24 couldn't be read", and
 *  Show them opened Items on "0 items". It counted the graph's name taking (`stats.enrichment`),
 *  which fails while there is no model, and its filter lists the items still being read or that
 *  couldn't be read (`state`), which were none: those 24 had been read and could be searched. Now
 *  the row, the Library settings tab's fact and the Items count line count reading from the list's
 *  own `state` facet, which Show them filters by, and taking names has a row of its own, with the
 *  way to set up a model when there is none. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { LibraryItem } from '../../lib/api'
import { answer, answers, resetGateway, route, row } from '../../test/libraryHarness'

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../test/libraryHarness')
  return { ...(await orig<typeof import('../../lib/api')>()), api: gatewayApi() }
})
const { LibraryPage } = await import('./LibraryPage')
const { resetDataStore } = await import('../../lib/data/store')

/** 27 notes, all read but `reading` of them, whose names the graph could not take: no model. */
let rows: LibraryItem[] = []
const READ = Array.from({ length: 25 }, (_, i) => row(`knowledge:r${i}`, `Read note ${i}`))

/** `GET /api/library` as the gateway answers it: the rows the state filter keeps, and the state
 *  facet counted over every row (a facet's own filter is not applied to its counts). */
function library(params: { state?: string[] | string } = {}) {
  const want = params.state ? (Array.isArray(params.state) ? params.state : String(params.state).split(',')) : null
  const kept = want ? rows.filter((r) => want.includes(r.state)) : rows
  const state: Record<string, number> = {}
  for (const r of rows) if (r.state) state[r.state] = (state[r.state] ?? 0) + 1
  return answer(kept, { facets: { kind: { note: kept.length }, state } })
}

beforeEach(() => {
  resetDataStore(); resetGateway(); localStorage.clear()
  rows = [row('knowledge:b1', 'Still reading', { state: 'being_read' }), row('knowledge:u1', 'Could not read', { state: 'unreadable' }), ...READ]
  answers.library = library
  answers.knowledgeStats = {
    items: 27, entities: 0, relations: 0, embeddings: { enabled: false },
    enrichment: { model_available: false, entities: { ran: 0, failed: 24, running: 0, skipped: 0, not_run: 3 } },
  }
  answers.librarySettings = { power_user_view: true }
  answers.embeddingReindexJobs = { jobs: [], active: null }
  answers.knowledgeIntents = { intents: [] }
  answers.researchReports = { reports: [] }
  answers.knowledgeConflicts = { conflicts: [] }
})
afterEach(cleanup)

function mount(view: string) {
  const r = route({ view })
  render(<LibraryPage sub="" navEpoch={0} query={r.state.query} setQuery={r.setQuery} navigate={r.navigate} />)
  return r
}

const settingsRow = async (title: string) => {
  const panel = within(await screen.findByRole('region', { name: 'Library settings' }))
  // The row: its term and sentence, and its action beside them.
  return panel.queryByText(title, { selector: 'dt' })?.parentElement?.parentElement ?? null
}

describe("Library settings' Being read row counts what Show them lists", () => {
  it('🔴 its count is what Show them opens', async () => {
    const r = mount('settings')
    await screen.findByText('What search can see')
    const being = await settingsRow('Being read')
    expect(being, 'a Being read row while two items are not read').not.toBeNull()
    const says = within(being!).getByRole('definition').textContent ?? ''
    expect(says).toBe("1 still being read · 1 couldn't be read")
    await userEvent.click(within(being!).getByRole('button', { name: 'Show them' }))
    const state = r.state.pushed.at(-1)?.state
    expect(state, 'Show them opens Items by reading state').toBeTruthy()
    const listed = library({ state: String(state) }).total
    const counted = [...says.matchAll(/\d+/g)].reduce((a, m) => a + Number(m[0]), 0)
    expect(counted, 'the row counts what Show them lists').toBe(listed)
  })

  it('🔴 with every item read there is no Being read row, and the names not taken yet have their own, with the way to a model', async () => {
    rows = [...READ, row('knowledge:r25', 'Read note 25'), row('knowledge:r26', 'Read note 26')]
    const r = mount('settings')
    await screen.findByText('What search can see')
    expect(await settingsRow('Being read'), 'nothing is being read').toBeNull()
    const names = await settingsRow('Names for the graph')
    expect(names, 'the names the graph has not taken have a row').not.toBeNull()
    expect(within(names!).getByRole('definition').textContent).toBe('Not taken yet from 27 items. Taking them needs a model.')
    await userEvent.click(within(names!).getByRole('button', { name: 'Set up a model' }))
    expect(r.navigate).toHaveBeenCalledWith('settings/models')
  })

  it('🔴 the Library settings tab says what is still being read, never the names not taken', async () => {
    mount('settings')
    const tab = await within(await screen.findByRole('tablist', { name: 'Library views' })).findByRole('tab', { name: /^Library settings/ })
    await within(tab).findByText('1 still being read')
    expect(tab.textContent).not.toMatch(/waiting to be read/)
  })

  it("🔴 the Items count line says what is still being read, never the names not taken", async () => {
    mount('items')
    const region = await screen.findByRole('region', { name: 'Library items' })
    await waitFor(() => expect(region.textContent).toMatch(/1 still being read/))
    expect(region.textContent).not.toMatch(/waiting to be read/)
  })
})
