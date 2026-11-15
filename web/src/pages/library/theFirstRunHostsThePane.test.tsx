/** The first run hosts the pane: on an empty library, `?setup=all` and
 *  `?setup=intent:new` open beside the empty state, and `?q=` shows the no-match state; the "…" has
 *  no Select items, and Read missed items and Process everything again… are off with "There is
 *  nothing to read yet.", and on while the read loads or after it failed. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { answer, answers, resetGateway, route } from '../../test/libraryHarness'

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
  render(<LibraryPage sub="" navEpoch={0} query={r.state.query} setQuery={r.setQuery} navigate={r.navigate} />)
}

/** The "…"'s rows, opened. */
async function overflow() {
  await userEvent.click(await screen.findByRole('button', { name: 'More actions' }))
  return (label: string) => screen.queryAllByText(label).map((el) => el.closest('button, [role="menuitem"]')).find(Boolean) as HTMLElement | undefined
}
const off = (el?: HTMLElement) => !!el && (el.getAttribute('aria-disabled') === 'true' || (el as HTMLButtonElement).disabled)

describe('the first run hosts the pane', () => {
  it('🔑 ?setup=all opens the panel beside the empty state', async () => {
    answers.library = answer([])
    mount({ setup: 'all' })
    expect(await screen.findByText('Your library is empty')).toBeTruthy()
    expect(await screen.findByRole('heading', { name: 'Feeds, intents and reports' })).toBeTruthy()
    expect(screen.getByText('Nothing adds on its own yet.')).toBeTruthy()
  })

  it('🔑 ?setup=intent:new opens New intent beside the empty state', async () => {
    answers.library = answer([])
    mount({ setup: 'intent:new' })
    expect(await screen.findByText('Your library is empty')).toBeTruthy()
    expect(await screen.findByRole('heading', { name: 'New intent' })).toBeTruthy()
  })

  it('🔑 ?q= on an empty library is the no-match state, not the first run', async () => {
    answers.library = answer([])
    mount({ q: 'plan' })
    expect(await screen.findByText('Nothing matches “plan”.')).toBeTruthy()
    expect(screen.queryByText('Your library is empty')).toBeNull()
  })

  it('🔑 the "…" has no Select items, and its reading rows are off with their reason', async () => {
    answers.library = answer([])
    mount()
    await screen.findByText('Your library is empty')
    const find = await overflow()
    expect(find('Select items')).toBeUndefined()
    expect(off(find('Read missed items'))).toBe(true)
    expect(off(find('Process everything again…'))).toBe(true)
    expect(screen.getAllByText('There is nothing to read yet.').length).toBeGreaterThan(0)
  })

  it('🔑 the reading rows are on while the read loads, and after it failed', async () => {
    mount()
    let find = await overflow()
    expect(off(find('Read missed items')), 'loading').toBe(false)
    cleanup(); resetDataStore()
    answers.library = new Error('no answer')
    mount()
    await screen.findByText(/Couldn.t load your library/)
    find = await overflow()
    expect(off(find('Process everything again…')), 'failed').toBe(false)
  })
})
