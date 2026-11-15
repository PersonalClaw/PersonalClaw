/** The Power user view's views are real tabs.
 *
 *  A vertical `role="tablist"`: the arrows move along it (Home and End to its ends) without opening,
 *  Enter or Space opens the one with focus, only the current tab is in the Tab order, each panel is
 *  named by its tab, and the current tab is drawn by the current outline, which forced colours keep. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { answer, answers, resetGateway, route, row } from '../../test/libraryHarness'
import { CURRENT_OUTLINE } from '../../ui/RowHitTarget'

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
  answers.knowledgeStats = { items: 1, entities: 0, relations: 0, embeddings: { enabled: false } }
  answers.knowledgeConflicts = { conflicts: [] }
  answers.librarySettings = { power_user_view: true }
})
afterEach(cleanup)

const mount = () => {
  const r = route({})
  render(<LibraryPage sub="" navEpoch={0} query={r.state.query} setQuery={r.setQuery} navigate={r.navigate} />)
  return r
}
const tab = (name: string) => screen.getByRole('tab', { name: new RegExp(`^${name}`) })

describe('the library views are real tabs', () => {
  it('🔑 a vertical tablist, only the current tab in the Tab order', async () => {
    mount()
    const list = await screen.findByRole('tablist', { name: 'Library views' })
    expect(list.getAttribute('aria-orientation')).toBe('vertical')
    expect(tab('Graph').tabIndex).toBe(0)
    for (const other of ['Items', 'Tags', 'Conflicts', 'Decisions', 'Library settings']) expect(tab(other).tabIndex).toBe(-1)
  })

  it('🔑 the arrows move without opening, and Enter opens', async () => {
    const r = mount()
    await screen.findByRole('tablist', { name: 'Library views' })
    tab('Graph').focus()
    await userEvent.keyboard('{ArrowDown}')
    expect(document.activeElement).toBe(tab('Items'))
    expect(r.setQuery, 'moving is not opening').not.toHaveBeenCalled()
    await userEvent.keyboard('{End}')
    expect(document.activeElement).toBe(tab('Library settings'))
    await userEvent.keyboard('{Home}')
    expect(document.activeElement).toBe(tab('Graph'))
    await userEvent.keyboard('{ArrowUp}')
    expect(document.activeElement, 'it wraps').toBe(tab('Library settings'))
    await userEvent.keyboard('{Enter}')
    expect(r.state.query.view).toBe('settings')
  })

  it('🔑 each panel is named by its tab', async () => {
    mount()
    const panel = await screen.findByRole('tabpanel')
    expect(panel.getAttribute('aria-labelledby')).toBe(tab('Graph').id)
    expect(tab('Graph').getAttribute('aria-controls')).toBe(panel.id)
  })

  it('🔑 the current tab is drawn by the current outline', async () => {
    mount()
    await screen.findByRole('tablist', { name: 'Library views' })
    for (const c of CURRENT_OUTLINE.split(/\s+/).filter(Boolean)) expect(tab('Graph').className).toContain(c)
    expect(tab('Items').className).not.toContain(CURRENT_OUTLINE.split(/\s+/)[0])
  })
})
