import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'

// ── A web watch says what its checks found ──────────────────────────────────────────────────────
//
// Measured on a running gateway: a watch whose checks were refused by the network settings, and
// then — the host allowed — whose first check found nothing on the page to track, read "Firing on
// its own · No runs recorded yet" through all of it, and the list showed it like any healthy row.
// The server now sends the watch's last check (`last_check`), and while the watch cannot fire its
// sentence rides in `warnings`. This drives the real Triggers list and the store inspector with the
// rows a gateway sends.

type Row = Record<string, unknown>

let storeRows: Row[] = []

const REFUSED =
  "PersonalClaw's network settings refused http://localhost:18999/releases, which is on this " +
  'computer (localhost). If this endpoint is yours, add localhost to Allowed hosts in Settings → ' +
  'Security → Network egress, and its next check reaches it. It cannot fire while its checks are refused.'
const UNCHANGED = 'Nothing new: the 14 items on the page were all there before.'

function mockApi() {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      schedules: () => Promise.resolve({ jobs: [] }),
      hooks: () => Promise.resolve([]),
      storeTriggers: () => Promise.resolve(storeRows),
      callbacks: () => Promise.resolve([]),
      triggerReview: () => Promise.resolve([]),
      actionProviders: () => Promise.resolve([]),
      autonomyLadder: () => Promise.reject(new Error('no ladder in this test')),
      triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: [], event: [], app_sources: [] }),
      triggerHistory: () => Promise.resolve({ runs: [], total: 0 }),
    },
  }))
  vi.doMock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
}

function watchRow(overrides: Row = {}): Row {
  return {
    kind: 'store', store_kind: 'web_watch', id: 'store:web_watch:release-watch', raw_id: 'web_watch:release-watch',
    name: 'Release watch', enabled: true, spec: { url: 'http://localhost:18999/releases' },
    action: { provider: 'notify', config: {} }, health: 'ok', state: 'active', run_count: 0,
    last_error: '', broken: [], warnings: [], last_check: null, needs_grant: [],
    ...overrides,
  }
}

const refusedCheck = {
  outcome: 'refused', said: REFUSED, items: 0, at: '2026-10-02T19:41:36+00:00',
  since: '2026-10-02T19:35:30+00:00', checks: 3, can_fire: false,
}
const unchangedCheck = {
  outcome: 'unchanged', said: UNCHANGED, items: 14, at: '2026-10-02T19:51:36+00:00',
  since: '2026-10-02T19:51:36+00:00', checks: 1, can_fire: true,
}

async function mount(query: Record<string, string> = {}) {
  const { TriggersSection } = await import('./TriggersSection')
  render(<TriggersSection sub="" navigate={vi.fn()} navEpoch={0} query={query} setQuery={() => {}} />)
}

/** The opened inspector, scoped: the list row behind it prints the same reason. */
async function openWatch(row: Row) {
  storeRows = [row]
  await mount({ open: 'store:web_watch:release-watch' })
  const when = await waitFor(() => screen.getByText('When it runs').parentElement as HTMLElement)
  return within(when.parentElement as HTMLElement)
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  storeRows = []
  mockApi()
})

describe('the inspector of a web watch that cannot fire', () => {
  it('does not say it is firing, and says why once', async () => {
    const panel = await openWatch(watchRow({ last_check: refusedCheck, warnings: [REFUSED] }))
    await panel.findByText('Not firing — its checks of the page are refused')
    expect(panel.queryByText('Firing on its own')).toBeNull()
    // The sentence names the setting that lifts it, and appears once: the warning says it, so the
    // last check does not repeat it.
    expect(panel.getAllByText(REFUSED)).toHaveLength(1)
    expect(panel.getByText('Refused by the network settings')).toBeInTheDocument()
    expect(panel.getByText(/3 checks in a row since/)).toBeInTheDocument()
  })

  it('names the page it watches', async () => {
    const panel = await openWatch(watchRow({ last_check: refusedCheck, warnings: [REFUSED] }))
    expect(await panel.findByText('http://localhost:18999/releases')).toBeInTheDocument()
  })
})

describe('the inspector of a web watch that can fire', () => {
  it('says what its last check found, and keeps "Firing on its own"', async () => {
    const panel = await openWatch(watchRow({ last_check: unchangedCheck }))
    await panel.findByText('Firing on its own')
    expect(panel.getByText('Nothing new')).toBeInTheDocument()
    expect(panel.getByText(UNCHANGED)).toBeInTheDocument()
  })

  it('says a watch not checked yet has not been, and that its first check fires nothing', async () => {
    const panel = await openWatch(watchRow())
    expect(
      await panel.findByText('Not checked yet. Its first check records what is on the page and fires nothing.'),
    ).toBeInTheDocument()
  })
})

describe('the list row of a web watch that cannot fire', () => {
  it('badges it "can\'t fire", not "check schedule", with the reason on the row', async () => {
    storeRows = [watchRow({ last_check: refusedCheck, warnings: [REFUSED] })]
    await mount()
    await waitFor(() => expect(screen.getByText('Release watch')).toBeInTheDocument())
    expect(screen.getByText(/can't fire/)).toBeInTheDocument()
    expect(screen.queryByText(/check schedule/)).toBeNull()
    expect(screen.getByText(REFUSED)).toBeInTheDocument()
  })

  it('stays quiet for a watch that can fire (vacuity leg)', async () => {
    storeRows = [watchRow({ last_check: unchangedCheck })]
    await mount()
    await waitFor(() => expect(screen.getByText('Release watch')).toBeInTheDocument())
    expect(screen.queryByText(/can't fire/)).toBeNull()
  })
})
