import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

// ── A failed week projection reads as failure, not as a week still projecting ────────────────
//
// `WeekGridView` bound `{ data: week }` and nothing else, so `week === undefined` meant BOTH
// "in flight" and "failed". The consequence was not a missing error message — it was a surface
// that actively asserted the wrong thing (#498):
//
//   • the header stayed on "Projecting…" FOREVER, and
//   • the `grid.totalFires === 0` empty state is gated on the read having LANDED, so a failure
//     fell through to the else and drew the full 7×24 table from `buildWeekGrid([], start)` —
//     168 empty cells under a legend advertising four cell states that can never appear.
//
// Measured against the running gateway with `/api/triggers/week` forced to reject: 1 table,
// 168 `<td>`s, zero `role="alert"`, no retry control. That is a calendar telling a user their
// week is empty when the truth is that nobody asked the server successfully.
//
// The fetcher never swallowed the rejection (there is no `.catch`), so `error` was always
// available — this is the same fix `TriggersListPage` already carries in this directory, and
// `triggersLoadError.test.tsx` is this test's sibling. Both states are driven through the REAL
// component, because the whole defect is WHICH ONE renders.

const WEEK_OK = {
  start: '2026-09-18T00:00:00', end: '2026-09-25T00:00:00', server_tz: 'UTC',
  truncated: [] as string[],
  occurrences: [
    { trigger_id: 'schedule:a', trigger_name: 'digest', at: 1789743600, suppressed_by: '', reason: '' },
  ],
}

function mockApi(triggersWeek: () => Promise<unknown>) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: { triggersWeek },
  }))
}

async function mount() {
  const { WeekGridView } = await import('./WeekGridView')
  render(<WeekGridView />)
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('the week grid distinguishes a failed projection from an empty week', () => {
  it('renders a retryable LoadError — and NOT the grid — when the read rejects', async () => {
    mockApi(() => Promise.reject(new Error('gateway down')))
    await mount()

    // `role="alert"` is LoadError's signature: an unrequested failure changes what the screen
    // means. `EmptyState` deliberately has no live region.
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent, 'names what failed to load').toMatch(/week/i)
    expect(screen.getByRole('button', { name: /Retry/ }), 'offers a way back').toBeInTheDocument()

    // 🔑 The half that made this a lie rather than an omission: the grid must be GONE. A drawn
    // 7×24 table of empty cells beside an error still reads as "your week is empty".
    expect(document.querySelectorAll('td').length, 'no phantom grid under the error').toBe(0)
    expect(document.body.textContent, 'and it is no longer "projecting"').not.toMatch(/Projecting/)
  })

  it('still says "Projecting…" while the read is genuinely in flight', async () => {
    // A promise that never settles is the honest model of "in flight" — the state the old code
    // was permanently stuck in must remain reachable for the case that really is loading.
    mockApi(() => new Promise(() => {}))
    await mount()
    await waitFor(() => expect(screen.getByText(/Projecting/)).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'a pending read is not an error').toBeNull()
  })

  it('shows the empty state — not an error — when the week really has no fires', async () => {
    mockApi(() => Promise.resolve({ ...WEEK_OK, occurrences: [] }))
    await mount()
    await waitFor(() => expect(screen.getByRole('heading', { name: 'No fires this week' })).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'a genuine empty is not an error').toBeNull()
  })

  it('draws the grid when the read succeeds with fires', async () => {
    mockApi(() => Promise.resolve(WEEK_OK))
    await mount()
    await waitFor(() => expect(document.querySelectorAll('td').length).toBeGreaterThan(0))
    expect(screen.queryByRole('alert'), 'a healthy week is not an error').toBeNull()
    expect(document.body.textContent, 'the summary replaced the projecting label').not.toMatch(/Projecting/)
  })
})

describe('the call site reads the error the hook exposes', () => {
  it('binds error and refresh, and gates the LoadError on them', () => {
    const src = require('node:fs').readFileSync(
      require('node:path').join(process.cwd(), 'src/pages/triggers/WeekGridView.tsx'), 'utf8',
    )
    // 🪤 Scoped to the useQuery REGISTRATION, not a character window: the file's prose mentions
    // `error` several times while explaining the defect, so a bare substring search for "error"
    // would pass on a component that had been reverted to `{ data: week }` with the comment left
    // behind. Take the destructuring pattern itself.
    const m = /const \{([^}]*)\} = useQuery<WeekProjection>/.exec(src)
    expect(m, 'the week useQuery must be found').not.toBeNull()
    expect(m![1], 'error must be bound at the call site').toMatch(/\berror\b/)
    expect(m![1], 'refresh must be bound so the retry can re-run it').toMatch(/\brefresh\b/)
    // The flag has to require BOTH: an error alone, with data already in hand from a previous
    // week, must not blank a grid the user can still read.
    expect(src, 'the failure flag requires no data AND an error')
      .toMatch(/const loadFailed = week === undefined && Boolean\(weekErr\)/)
    // And it must be ordered ahead of the empty state, or a failure falls through to the grid.
    expect(src.indexOf('loadFailed ? ('), 'LoadError is chosen before the empty state')
      .toBeLessThan(src.indexOf("title=\"No fires this week\""))
  })
})
