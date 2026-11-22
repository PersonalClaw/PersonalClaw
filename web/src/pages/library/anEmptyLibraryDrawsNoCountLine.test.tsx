/** An empty library draws no count line: on a library with nothing in it yet, "0 items" above "Your
 *  library is empty" said the same thing twice, and was the first thing a new owner read. The line
 *  comes back once there is an item, and stays while a search or a filter narrows the list to
 *  nothing, where it says what narrowed it. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
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
  answers.knowledgeSources = { sources: [], kinds: [] }
  answers.intentCandidates = { total: 0 }
})
afterEach(cleanup)

const mount = (query: Record<string, string> = {}) => {
  const r = route(query)
  const { container } = render(<LibraryPage sub="" navEpoch={0} query={r.state.query} setQuery={r.setQuery} navigate={r.navigate} />)
  return container
}
const countLine = (c: HTMLElement) => c.querySelector('[data-list-line]')

describe('the library count line', () => {
  it('🔴 is not drawn above the empty state of a library with nothing in it', async () => {
    answers.library = answer([])
    const c = mount()
    await screen.findByText('Your library is empty')
    expect(countLine(c)?.textContent ?? null).toBeNull()
    expect(c.querySelector('[role="status"]'), 'the list still has its live region').not.toBeNull()
  })

  it('is drawn once there is an item', async () => {
    answers.library = answer([row('knowledge:k1', 'Router manual')])
    const c = mount()
    await screen.findByText('Router manual')
    await waitFor(() => expect(countLine(c)?.textContent).toBe('1 item'))
  })

  it('is drawn while a search narrows an empty library to nothing', async () => {
    answers.library = answer([])
    const c = mount({ q: 'plan' })
    await screen.findByText('Nothing matches “plan”.')
    expect(countLine(c)?.textContent).toContain('0 matches')
  })

  it('is drawn while a filter narrows an empty library to nothing, with its Clear', async () => {
    answers.library = answer([])
    const c = mount({ kind: 'note' })
    await waitFor(() => expect(countLine(c)?.textContent).toContain('0 items'))
    expect(countLine(c)?.textContent).toContain('Clear')
  })
})
