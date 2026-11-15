/** ⌘K turns the Power user view on and off, and opens each of its tabs.
 *
 *  "Turn on Power user view" while it is off, "Turn off Power user view" while it is on: each writes
 *  the setting as the header's switch does (on opens the graph, off the list). And a Go to row for
 *  each tab, which opens it at its own address. */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { answers, calls, resetGateway } from '../../test/libraryHarness'

vi.mock('../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../test/libraryHarness')
  return { ...(await orig<typeof import('../../lib/api')>()), api: gatewayApi() }
})
const { powerUserCommands } = await import('./powerUserCommands')
const { LIBRARY_SETTINGS_KEY } = await import('./powerUserView')
const { writeQuery } = await import('../../lib/data')
const { resetDataStore } = await import('../../lib/data/store')

beforeEach(() => { resetDataStore(); resetGateway(); answers.saveLibrarySettings = { power_user_view: true } })

describe('the palette and the Power user view', () => {
  it('🔑 off, it offers Turn on, which writes the setting and opens the graph', async () => {
    const navigate = vi.fn()
    const row = powerUserCommands(navigate).find((c) => c.id === 'act:power-user-view')!
    expect(row.label).toBe('Turn on Power user view')
    row.run()
    expect(navigate).toHaveBeenCalledWith('library?view=graph')
    await vi.waitFor(() => expect(calls.saveLibrarySettings).toEqual([[{ power_user_view: true }]]))
  })

  it('🔑 on, it offers Turn off, which writes the setting and opens the list', async () => {
    writeQuery(LIBRARY_SETTINGS_KEY, { power_user_view: true })
    const navigate = vi.fn()
    const row = powerUserCommands(navigate).find((c) => c.id === 'act:power-user-view')!
    expect(row.label).toBe('Turn off Power user view')
    row.run()
    expect(navigate).toHaveBeenCalledWith('library')
    await vi.waitFor(() => expect(calls.saveLibrarySettings).toEqual([[{ power_user_view: false }]]))
  })

  it('🔑 a Go to row for each tab opens it at its own address', () => {
    const navigate = vi.fn()
    const rows = powerUserCommands(navigate).filter((c) => c.hint === 'Go to')
    expect(rows.map((r) => r.label)).toEqual(['Library › Graph', 'Library › Tags', 'Library › Conflicts', 'Library › Decisions', 'Library settings'])
    for (const r of rows) r.run()
    expect(navigate.mock.calls.map(([to]) => to)).toEqual([
      'library?view=graph', 'library?view=tags', 'library?view=conflicts', 'library?view=decisions', 'library?view=settings',
    ])
  })
})
