import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'

// ── A file the Triggers page lists from that cannot be read ─────────────────────────────────────
//
// `triggers.json` read as empty when it did not parse: the page listed nothing from it and offered
// the newcomer's "No triggers" over automations that were there, and the next write replaced them.
// Now nothing is written to such a file until it can be read, and every `GET /api/triggers` names
// it (`unreadable`, in `triggers.store.unreadable_said`'s words): the page says which file, what
// that stops, where its copy is kept and what to do, above the list and in place of the empty
// state. The last test is the vacuity leg: with every file readable there is no notice, and an
// empty home still gets its "No triggers".

const SAID = '~/.personalclaw/triggers.json could not be read (it is not valid JSON: line 1, column 74). '
  + 'Until it can be, none of the automations in it is listed or runs, and none can be made, changed or '
  + 'deleted. A copy of it as it was is kept beside it as triggers.json.broken-20261004T090000Z.'
const REMEDY = 'Repair the file, restore it from a snapshot under Settings → Durability, or remove it to start '
  + 'over; the copy keeps what it held. Every automation in it is listed and runs again as soon as it can be read.'

const { API } = vi.hoisted(() => ({
  API: {
    schedules: vi.fn(() => Promise.resolve({ jobs: [] as unknown[], server_tz: 'UTC', unreadable: [] as unknown[] })),
    hooks: vi.fn(() => Promise.resolve([])),
    storeTriggers: vi.fn(() => Promise.resolve([])),
    callbacks: vi.fn(() => Promise.resolve([])),
    triggerReview: vi.fn(() => Promise.resolve([])),
    actionProviders: vi.fn(() => Promise.resolve([])),
    autonomyLadder: vi.fn(() => Promise.reject(new Error('no ladder in this test'))),
    triggerVariables: vi.fn(() => Promise.resolve({ lifecycle: [], schedule: [], event: [], app_sources: [] })),
    triggerHistory: vi.fn(() => Promise.resolve({ runs: [], total: 0 })),
    channels: vi.fn(() => Promise.resolve([])),
  },
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: API,
}))
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

async function mountTriggers() {
  const { TriggersSection } = await import('./TriggersSection')
  render(<TriggersSection sub="" navigate={vi.fn()} navEpoch={0} query={{}} setQuery={() => {}} />)
}

// `useQuery` keeps a module-global cache: each test reads its own lists only with it emptied.
afterEach(() => { resetDataStore() })

beforeEach(() => {
  sessionStorage.clear()
  API.schedules.mockImplementation(() => Promise.resolve({
    jobs: [], server_tz: 'UTC',
    unreadable: [{ file: '~/.personalclaw/triggers.json', said: SAID, remedy: REMEDY }],
  }))
})

describe('the Triggers page over a file it cannot read', () => {
  it('says which file, what that stops, where its copy is and what to do', async () => {
    await mountTriggers()
    const notice = await screen.findByText('Your automations could not be read')
    const band = notice.closest('[role="alert"]')
    expect(band).not.toBeNull()
    expect(band).toHaveTextContent(SAID)
    expect(band).toHaveTextContent(REMEDY)
  })

  it('offers no "No triggers" over automations it could not read', async () => {
    await mountTriggers()
    await screen.findByText('Your automations could not be read')
    expect(screen.queryByRole('heading', { name: 'No triggers' })).toBeNull()
  })

  it('every file readable: no notice, and an empty home is still told it has none', async () => {
    API.schedules.mockImplementation(() => Promise.resolve({ jobs: [], server_tz: 'UTC', unreadable: [] }))
    await mountTriggers()
    await waitFor(() => expect(screen.getByRole('heading', { name: 'No triggers' })).toBeInTheDocument())
    expect(screen.queryByText('Your automations could not be read')).toBeNull()
  })
})
