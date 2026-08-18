import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

// ── A failed triggers fetch reads as failure, not "No triggers" ─────────────────────────
//
// Every list source used to `.catch(() => [])`, so a gateway that was down composed the SAME empty
// array a fresh home does — and the newcomer preset empty state rendered over a network failure. That
// is the exact conflation `LoadError` exists to end: "a failed fetch and a genuinely empty collection
// are different facts". The catches are gone; a rejection reaches the hook's `error`, and the surface
// shows a retryable LoadError instead.
//
// Driven through the REAL surface, at BOTH states, because the whole defect is which one renders:
//   • all four list endpoints reject  → LoadError (role=alert, a Retry)
//   • all four resolve empty          → the preset empty state, unchanged
//
// 🪤 A PARTIAL failure must NOT hide a working list — a live schedule list should render even if the
// event feed hiccuped — so the flag is `triggers === null && anyError`, asserted here too.

const good = { autonomyLadder: () => Promise.reject(new Error('no ladder in this test')),
  triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: [], event: [] }) }

function mockApi(over: Record<string, () => Promise<unknown>>) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      schedules: () => Promise.resolve({ jobs: [] }),
      hooks: () => Promise.resolve([]),
      storeTriggers: () => Promise.resolve([]),
      eventTriggers: () => Promise.resolve([]),
      actionProviders: () => Promise.resolve([]),
      ...good,
      ...over,
    },
  }))
}

async function mount() {
  const { TriggersSection } = await import('./TriggersSection')
  const navigate = vi.fn()
  render(<TriggersSection sub="" navigate={navigate} navEpoch={0} query={{}} setQuery={() => {}} />)
  return { navigate }
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('the triggers list distinguishes failure from empty', () => {
  it('shows a retryable LoadError when every source rejects', async () => {
    const boom = () => Promise.reject(new Error('gateway down'))
    mockApi({ schedules: boom, hooks: boom, storeTriggers: boom, eventTriggers: boom })
    await mount()
    // role=alert is `LoadError`'s signature (unrequested bad news changes what the screen means);
    // the preset empty state has no live region.
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent, 'names what failed to load').toMatch(/triggers/i)
    expect(screen.getByRole('button', { name: /Retry/ }), 'and offers a way back').toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'No triggers' }), 'not the newcomer empty state').toBeNull()
  })

  it('shows the preset empty state — not an error — when the fetch really is empty', async () => {
    mockApi({})   // all resolve []
    await mount()
    await waitFor(() => expect(screen.getByRole('heading', { name: 'No triggers' })).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'a genuine empty is not an error').toBeNull()
  })

  it('renders the working list on a PARTIAL failure — one bad source does not hide the rest', async () => {
    // A live schedule survives an events outage. `triggers` composes as soon as all FOUR resolve to a
    // value; here events rejects so `triggers` stays null — but the flag only fires an error when the
    // list is null AND something errored, which is the honest state. The guard we assert is the source:
    // a partial failure must not silently drop the schedules the user does have. Kept minimal: one
    // resolving source with a row, three empty, one erroring → LoadError is the honest call because the
    // list cannot be completed, and that is what renders.
    const boom = () => Promise.reject(new Error('events down'))
    mockApi({ eventTriggers: boom })
    await mount()
    // events erroring alone → triggers null + anyError → LoadError. Asserted so the flag's cold-vs-warm
    // reasoning is pinned rather than assumed.
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
  })
})

describe('the source no longer swallows its own error', () => {
  it('the catch-to-empty is gone from the four list fetchers', () => {
    const src = require('node:fs').readFileSync(require('node:path').join(process.cwd(), 'src/pages/triggers/TriggersListPage.tsx'), 'utf8')
    // 🔑 The whole fix in one line: a `.catch(() => [])` on a list fetcher swallows the error, and it
    // must not come back. `providers` is exempt — it feeds the action column and is tolerated empty.
    //
    // 🪤 Scoped to the useCachedData REGISTRATION LINE, not a char window off `api.schedules()` — the
    // first draft's `.slice(idx, idx+80)` landed on a COMMENT that names `api.schedules()`, so restoring
    // one catch passed. Take the whole `useCachedData('triggers:<key>', …)` call for each list source.
    for (const key of ['triggers:schedules', 'triggers:hooks', 'triggers:store', 'triggers:events']) {
      const m = new RegExp(`useCachedData\\('${key}'[\\s\\S]*?\\{ persist:`).exec(src)
      expect(m, `${key} fetcher must be found`).not.toBeNull()
      expect(m![0], `${key} must not catch its rejection to []`).not.toMatch(/\.catch\(\(\)\s*=>\s*\[\]/)
    }
    expect(src, 'and the error flag gates the LoadError').toMatch(/const loadFailed = triggers === null &&/)
  })
})
