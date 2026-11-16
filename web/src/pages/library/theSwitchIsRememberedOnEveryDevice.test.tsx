/** The Power user view's setting is remembered on every device.
 *
 *  It is the gateway's (`GET` and `PUT /api/library/settings`), so a second browser reads what the
 *  first wrote. Pressing the switch writes it. A link to a tab shows the tab, the switch on and "On
 *  for this visit. Your setting is off.", and writes nothing, since a link restores state and never
 *  acts; Keep it on writes `true`. */
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

/** The gateway's one stored setting, which every browser reads. */
let stored = false
beforeEach(() => {
  resetDataStore(); resetGateway(); localStorage.clear(); sessionStorage.clear()
  stored = false
  answers.library = answer([row('knowledge:k1', 'Router manual')])
  answers.knowledgeIntents = { intents: [] }
  answers.researchReports = { reports: [] }
  answers.knowledgeStats = { items: 1, entities: 0, relations: 0, embeddings: { enabled: false } }
  answers.knowledgeConflicts = { conflicts: [] }
  answers.librarySettings = () => ({ power_user_view: stored })
  answers.saveLibrarySettings = (body: { power_user_view: boolean }) => { stored = body.power_user_view; return { power_user_view: stored } }
})
afterEach(cleanup)

const mount = (query: Record<string, string> = {}) => {
  const r = route(query)
  render(<LibraryPage sub="" navEpoch={0} query={r.state.query} setQuery={r.setQuery} navigate={r.navigate} />)
  return r
}

describe('the switch is remembered on every device', () => {
  it('🔑 pressing it writes the setting, and a second browser reads it', async () => {
    mount()
    await screen.findByRole('button', { name: /^Router manual/ })
    await userEvent.click(screen.getByRole('button', { name: 'Power user view' }))
    await waitFor(() => expect(calls.saveLibrarySettings).toEqual([[{ power_user_view: true }]]))
    // A second browser: nothing kept here, the same gateway.
    cleanup(); resetDataStore(); localStorage.clear(); sessionStorage.clear()
    mount()
    expect(await screen.findByRole('button', { name: 'Power user view', pressed: true })).toBeTruthy()
    expect(screen.getByRole('tablist', { name: 'Library views' })).toBeTruthy()
  })

  it('🔑 a link to a tab shows it for the visit, with the switch on, and writes nothing', async () => {
    const r = mount({ view: 'tags' })
    expect(await screen.findByRole('heading', { level: 2, name: 'Tags' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Power user view' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByText('On for this visit. Your setting is off.')).toBeTruthy()
    expect(calls.saveLibrarySettings).toBeUndefined()
    expect(r.setQuery).not.toHaveBeenCalled()
  })

  it('🔑 Keep it on writes true', async () => {
    mount({ view: 'graph' })
    await userEvent.click(await screen.findByRole('button', { name: 'Keep it on' }))
    await waitFor(() => expect(calls.saveLibrarySettings).toEqual([[{ power_user_view: true }]]))
    await waitFor(() => expect(screen.queryByText('On for this visit. Your setting is off.')).toBeNull())
  })

  it('🔑 pressing the switch in a visit turns the view off and returns to the list', async () => {
    const r = mount({ view: 'conflicts' })
    await screen.findByRole('heading', { level: 2, name: 'Conflicts' })
    await userEvent.click(screen.getByRole('button', { name: 'Power user view' }))
    await waitFor(() => expect(r.state.query.view).toBeUndefined())
    expect(calls.saveLibrarySettings).toEqual([[{ power_user_view: false }]])
  })
})
