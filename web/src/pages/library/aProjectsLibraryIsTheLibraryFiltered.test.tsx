/** A project's library is the library filtered: `?project=` reads that project's set,
 *  with and without each `store`, and its count line says the link's number; Add and a drop file into
 *  it; "Nothing in Garden yet." when it holds nothing; no row carries that project's chip; and Open
 *  project opens that one project, whatever the address holds. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { answer, answers, calls, resetGateway, route, row } from '../../test/libraryHarness'

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../test/libraryHarness')
  return { ...(await orig<typeof import('../../lib/api')>()), api: gatewayApi() }
})
const { LibraryPage } = await import('./LibraryPage')
const { resetDataStore } = await import('../../lib/data/store')

const HOME = { id: 'p-home', name: 'Homelab' }
beforeEach(() => {
  resetDataStore(); resetGateway(); localStorage.clear()
  answers.knowledgeIntents = { intents: [] }
  answers.researchReports = { reports: [] }
  answers.projects = [{ ...HOME, status: 'active' }, { id: 'p-garden', name: 'Garden', status: 'active' }]
})
afterEach(cleanup)

const mount = (query: Record<string, string>) => {
  const r = route(query)
  render(<LibraryPage sub="" navEpoch={0} query={r.state.query} setQuery={r.setQuery} navigate={r.navigate} />)
  return r
}

describe('a project\'s library is the library filtered', () => {
  it.each([
    [{ project: 'p-home' }, '34 items in Homelab', {}],
    [{ project: 'p-home', store: 'knowledge' }, '34 notes and files in Homelab', { store: 'knowledge' }],
    [{ project: 'p-home', store: 'artifacts' }, '34 pages and documents in Homelab', { store: 'artifacts' }],
  ])('🔑 %o reads that set, and its count line says the link\'s number', async (query, line, store) => {
    answers.library = answer([row('knowledge:k1', 'Router manual', { project: HOME })], { total: 34 })
    mount(query as Record<string, string>)
    // The count line's own part, not the announcement that says the same words.
    expect(await screen.findByText(line, { selector: '[data-list-line] [data-line-part]' })).toBeTruthy()
    // And the narrowed list's announcement says it too: the line's part and the live region.
    expect(await screen.findAllByText(line)).toHaveLength(2)
    expect(calls.library.at(-1)?.[0]).toEqual({ project: 'p-home', ...store })
  })

  it('🔑 no row carries the chip of the project it is filtered to', async () => {
    answers.library = answer([row('knowledge:k1', 'Router manual', { project: HOME })])
    mount({ project: 'p-home' })
    await screen.findByRole('button', { name: /^Router manual/ })
    expect(screen.queryByRole('button', { name: 'Filter by project Homelab' })).toBeNull()
  })

  it.each([
    ['the count line', [row('knowledge:k1', 'Router manual')]],
    ['an empty project', []],
  ])('🔑 %s\'s Open project opens one project, whatever the address holds', async (_, rows) => {
    answers.library = answer(rows)
    const r = mount({ project: 'a/../../settings?section=x' })
    // An empty project offers it twice: on the count line and under "Nothing in this project yet.".
    for (const b of await screen.findAllByRole('button', { name: 'Open project' })) await userEvent.click(b)
    expect(r.navigate.mock.calls.length).toBeGreaterThan(0)
    for (const [to] of r.navigate.mock.calls) expect(to).toBe('projects/a%2F..%2F..%2Fsettings%3Fsection%3Dx')
  })

  it('🔑 an empty project says "Nothing in Garden yet."', async () => {
    answers.library = answer([])
    mount({ project: 'p-garden' })
    expect(await screen.findByText('Nothing in Garden yet.')).toBeTruthy()
  })

  it('🔑 a file dropped on the page goes into the project', async () => {
    answers.library = answer([row('knowledge:k1', 'Router manual', { project: HOME })])
    answers.ingestKnowledgeFile = { item_id: 'k2', status: 'ok' }
    mount({ project: 'p-home' })
    await screen.findByRole('button', { name: /^Router manual/ })
    const file = new File(['notes'], 'rack.md', { type: 'text/markdown' })
    const page = document.querySelector('[class*="h-full"][class*="flex-col"]') as HTMLElement
    fireEvent.dragEnter(page, { dataTransfer: { types: ['Files'], files: [file] } })
    expect(screen.getByText('Drop to add to Homelab')).toBeTruthy()
    fireEvent.drop(page, { dataTransfer: { types: ['Files'], files: [file] } })
    await waitFor(() => expect(calls.ingestKnowledgeFile?.[0]?.[3]).toBe('p-home'))
  })
})
