/** Turning the Power user view off hides no control.
 *
 *  The view adds detail; it holds no essential control. With it off the header and its "…" offer
 *  every action they offer with it on, and each of Library settings' actions has its place in the
 *  regular view (Re-index and Process everything again… in the "…", Show them as the Filter's State),
 *  less the detail views themselves (the graph, the conflicts list) and Settings' own pages. And the
 *  preview a project's page hosts draws the same detail rows in About while the view is on, and none
 *  while it is off, as the library's own preview does: one component, one behaviour. */
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
const { LibraryPreview } = await import('../../ui/LibraryPreview')
const { resetDataStore } = await import('../../lib/data/store')

const STATS = {
  items: 5, entities: 9, relations: 4,
  embeddings: { enabled: true, embedded_items: 3, stale_items: 2 },
  enrichment: { entities: { ran: 3, failed: 1, running: 0, skipped: 0, not_run: 1 } },
}
beforeEach(() => {
  resetDataStore(); resetGateway(); localStorage.clear()
  // One item waits to be read and one couldn't be, as the stats below say: the Filter's State counts them.
  answers.library = answer([row('knowledge:k1', 'Router manual')], { facets: { kind: { notes: 5 }, state: { being_read: 1, unreadable: 1 } } })
  answers.knowledgeIntents = { intents: [] }
  answers.researchReports = { reports: [] }
  answers.knowledgeStats = STATS
  answers.knowledgeConflicts = { conflicts: [] }
  answers.embeddingReindexJobs = { jobs: [], active: null }
})
afterEach(cleanup)

function mount(on: boolean, query: Record<string, string> = {}) {
  answers.librarySettings = { power_user_view: on }
  const r = route(query)
  render(<LibraryPage sub="" navEpoch={0} query={r.state.query} setQuery={r.setQuery} navigate={r.navigate} />)
}

/** Every control the header and its "…" offer, by name. */
async function controls(): Promise<string[]> {
  await screen.findByRole('button', { name: /^Router manual/ })
  const header = screen.getByRole('button', { name: 'More actions' }).closest('header') ?? document.body
  const top = [...header.querySelectorAll('button')].map((b) => b.getAttribute('aria-label') || b.textContent?.trim() || '')
  await userEvent.click(screen.getByRole('button', { name: 'More actions' }))
  await screen.findByText('Read missed items')
  const menu = [...document.querySelectorAll('[role="menu"] button, [role="dialog"] button, [data-overflow-menu] button')]
    .map((b) => b.querySelector('[data-type="body-m"], span')?.textContent?.trim() || b.textContent?.trim() || '')
  cleanup()
  return [...new Set([...top, ...menu].filter(Boolean))].sort()
}

describe('turning the Power user view off hides no control', () => {
  it('🔑 the header and its "…" offer every action with it off that they offer with it on', async () => {
    mount(true, { view: 'items' })
    const on = await controls()
    resetDataStore()
    mount(false)
    const off = await controls()
    expect(on.filter((c) => !off.includes(c)), 'controls only the view has').toEqual([])
  })

  it("🔑 each of Library settings' actions has its place in the regular view", async () => {
    mount(true, { view: 'settings' })
    expect(await screen.findByRole('heading', { name: 'Library settings' })).toBeTruthy()
    const actions = screen.getAllByRole('button').map((b) => b.textContent?.trim() ?? '')
      .filter((t) => ['Re-index', 'Set up', 'Open graph', 'Show them', 'Review', 'Change', 'Process everything again…'].includes(t))
    expect(actions.length, 'the panel offers its actions').toBeGreaterThan(3)
    cleanup(); resetDataStore()
    mount(false)
    await screen.findByRole('button', { name: /^Router manual/ })
    await userEvent.click(screen.getByRole('button', { name: 'More actions' }))
    // Re-index (while items wait) and Process everything again… are the "…"'s.
    expect(await screen.findByText('Re-index search by meaning')).toBeTruthy()
    expect(screen.getByText('Process everything again…')).toBeTruthy()
    // Show them is the Filter's State: Still being read, Couldn't be read.
    await userEvent.keyboard('{Escape}')
    await userEvent.click(screen.getByRole('button', { name: /^Filter/ }))
    expect(await screen.findByText('Still being read')).toBeTruthy()
    expect(screen.getByText("Couldn't be read")).toBeTruthy()
  })
})

describe("a project's page hosts the same preview, detail and all", () => {
  const actions = {
    navigate: vi.fn(), writeUps: new Map<string, string>(), projects: [{ id: 'p-home', name: 'Homelab' }],
    chatTitle: () => '', pickChat: vi.fn(), pickProject: vi.fn(), project: 'p-home',
  }
  const item = row('knowledge:k1', 'Router manual', { project: { id: 'p-home', name: 'Homelab' } })
  beforeEach(() => {
    answers.knowledgeItem = { id: 'k1', title: 'Router manual', entities: [{ name: 'NAS' }], has_embedding: true }
    answers.knowledgeDuplicates = { duplicates: [], not_compared: 0, not_compared_note: '' }
  })

  it('🔑 with the view on, About draws the detail rows', async () => {
    answers.librarySettings = { power_user_view: true }
    render(<LibraryPreview item={item} actions={actions} />)
    expect(await screen.findByText('What was read from it')).toBeTruthy()
    expect(screen.getByText('NAS')).toBeTruthy()
    expect(screen.getByText('Searchable by meaning')).toBeTruthy()
  })

  it('🔴 with it off, none', async () => {
    answers.librarySettings = { power_user_view: false }
    render(<LibraryPreview item={item} actions={actions} />)
    await screen.findByRole('heading', { name: 'Router manual' })
    await waitFor(() => expect(screen.queryByText('Project')).toBeTruthy())
    expect(screen.queryByText('What was read from it')).toBeNull()
  })
})
