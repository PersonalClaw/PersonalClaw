import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── One sentence must not answer three different questions ────────────────────────────────────────
//
// The dashboard's Discover slot branched `if (!discover || !discover.enabled)` onto a single line:
// **"Discover tips are off."** `discover` is `null` for THREE unrelated reasons — the read failed,
// the read has not landed yet, or the user really did turn tips off — so two thirds of the time that
// line was a confident claim about a SETTING THE USER NEVER TOUCHED, on the app's first screen.
//
// Measured on a genuinely empty home (`PERSONALCLAW_HOME=/tmp/wave2-firstrun-empty`, no `--seed`),
// dashboard at 1440×1000, with `/api/legibility/discover` intercepted:
//
//   aborted read  → dashboard slot: "Discover tips are off."
//                   `#/discover`  : "Couldn't load your tips" · "Failed to fetch" · Retry  ← correct
//   read delayed  → dashboard slot at 1.6 s: "Discover tips are off."
//   genuinely off → dashboard slot: "Discover tips are off." and NO way to turn them on;
//                   `#/discover`  : "Discover is off" + what tips are + "Open Settings"    ← correct
//
// Same endpoint, same condition, two surfaces, and the page had already been fixed for exactly this
// reason — its loader carries the comment *"Swallowing the rejection made `data` falsy, which this
// render reads as 'Discover is off' — so a failed request did not merely say nothing, it made a
// FALSE CLAIM ABOUT A SETTING."* This file holds the widget to the same standard, and the fix
// borrows three shipped forms rather than inventing any: `doctorErr`'s split in `DashboardLive`,
// `OnThisMachine`/`PinnedArtifacts`'s honest read-error slot, and `#/discover`'s own on-ramp.
//
// 🪤 The off-branch keeps the LABEL the page uses ("Open Settings"), not "Turn them on": the control
// navigates, it does not toggle. Naming it for the toggle would be the copy defect one layer down.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

const TIP = {
  id: 'chat', area: 'Talk to it', title: 'Start a conversation',
  lesson: 'Chat is the front door.', try_it: { route: 'chat/new', query: {}, label: 'Open Chat' },
}
const FEED = { enabled: true, areas: [{ area: 'Talk to it', tips: [TIP] }] }

/** Every slice `DashboardLiveProvider` polls on mount — an unmocked one throws before the widget
 *  renders, which would make every absence assertion below vacuously true. */
function mockApi(over: Record<string, unknown>) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      status: () => Promise.resolve({ update_available: false }),
      system: () => Promise.resolve({ platform: 'darwin' }),
      doctor: () => Promise.resolve({ ok: true, core_ok: true, worst: '', capabilities: {} }),
      notifications: () => Promise.resolve({ notifications: [] }),
      discover: () => Promise.resolve(FEED),
      approvals: () => Promise.resolve([]),
      inboxPending: () => Promise.resolve([]),
      skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
      uLoops: () => Promise.resolve([]),
      readyTasks: () => Promise.resolve([]),
      triggersHistory: () => Promise.resolve({ runs: [], did_ids: [], suppressed: 0 }),
      ...over,
    },
  }))
}

/** Mount the REAL provider around the real widget, plus a probe that consumes the same context.
 *
 *  🪤 The probe is the mountedness floor and it is load-bearing for the in-flight case, whose whole
 *  assertion is an ABSENCE. Without it, a provider that threw on mount — or an api mock missing one
 *  slice — would render nothing at all and read as "the slot correctly stayed quiet". */
async function mount(navigate = vi.fn()) {
  const { DashboardLiveProvider, useDashboardLive } = await import('./DashboardLive')
  const { Discover } = await import('./widgets/Discover')
  function Probe() {
    const live = useDashboardLive()
    return <span data-testid="probe">{live.discover === null ? 'unread' : 'read'}</span>
  }
  const route = { navigate, sub: '', navEpoch: 0, query: {}, setQuery: () => {} }
  render(
    <DashboardLiveProvider>
      <Discover {...route} />
      <Probe />
    </DashboardLiveProvider>,
  )
  await waitFor(() => expect(screen.getByTestId('probe')).toBeInTheDocument())
  return { navigate }
}

const OFF = /Discover tips are off/i
const FAILED = /Couldn’t load your tips/i

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('the dashboard Discover slot distinguishes failed, unread and off', () => {
  it('a FAILED read says it could not read, not that a setting is off', async () => {
    mockApi({ discover: () => Promise.reject(new Error('Failed to fetch')) })
    await mount()
    await waitFor(() => expect(screen.getByText(FAILED)).toBeInTheDocument())
    expect(screen.queryByText(OFF), 'a dead endpoint is not the user’s choice').toBeNull()
  })

  it('an UNREAD slice says nothing at all — it does not pre-announce a setting', async () => {
    // Never resolves: the state the slot is in for every millisecond before the first read lands,
    // and forever on a hung request.
    mockApi({ discover: () => new Promise(() => {}) })
    await mount()
    // The probe proves the provider is live and the slice really is still `null` — so the two
    // absences below are measurements, not an unmounted tree.
    expect(screen.getByTestId('probe').textContent).toBe('unread')
    expect(screen.queryByText(OFF), 'not yet read is not "off"').toBeNull()
    expect(screen.queryByText(FAILED), 'and it is not a failure either').toBeNull()
  })

  it('a genuinely OFF feed states the fact AND offers the way to change it', async () => {
    mockApi({ discover: () => Promise.resolve({ enabled: false, areas: [] }) })
    const { navigate } = await mount()
    await waitFor(() => expect(screen.getByText(OFF)).toBeInTheDocument())
    // Keyboard-reachable, and it goes where the setting actually lives.
    const cta = screen.getByRole('button', { name: /Open Settings/i })
    await userEvent.click(cta)
    expect(navigate).toHaveBeenCalledWith('settings/legibility')
    expect(screen.queryByText(FAILED)).toBeNull()
  })

  it('a live feed still renders its deck, and none of the three sentences appear', async () => {
    // THE OTHER DIRECTION. A test that only checks the empty branches cannot tell a fixed gate from
    // one that now swallows the happy path.
    mockApi({})
    await mount()
    await waitFor(() => expect(screen.getByText('Start a conversation')).toBeInTheDocument())
    expect(screen.queryByText(OFF)).toBeNull()
    expect(screen.queryByText(FAILED)).toBeNull()
    expect(screen.queryByRole('button', { name: /Open Settings/i })).toBeNull()
  })
})

describe('the split lives in the shared feed, and matches its already-correct sibling', () => {
  it('the live context carries the tips read’s own failure', () => {
    // Without this the widget's failed branch is an INERT control: `discoverErr` would be
    // permanently falsy and the honest sentence unreachable. Asserted on the context VALUE, which
    // is what a consumer actually receives.
    const code = read('pages/dashboard/DashboardLive.tsx')
    expect(code, 'declared on the interface').toMatch(/discoverErr: unknown/)
    expect(code, 'and the context carries it').toMatch(/discover, discoverErr,/)
    // The swallow this fix removes must not come back.
    expect(code, 'no bare swallow may remain on the tips read').not.toMatch(
      /api\.discover\(\)[\s\S]{0,80}catch\(\(\) => \{\}\)/,
    )
  })

  it('the widget and the page reach the SAME setting by the same route', () => {
    // One vocabulary across the two surfaces that answer this condition. A route rename on either
    // side reds here instead of quietly leaving one on-ramp pointing at a dead hash.
    const widget = read('pages/dashboard/widgets/Discover.tsx')
    const page = read('pages/discover/DiscoverPage.tsx')
    for (const [what, src] of [['widget', widget], ['page', page]] as const) {
      expect(src, `${what} must route the off case to the legibility settings`).toMatch(
        /navigate\('settings\/legibility'\)/,
      )
    }
    // And the widget no longer conflates the three conditions in one condition.
    expect(widget, 'the old conflated gate must be gone').not.toMatch(/!discover \|\| !discover\.enabled/)
  })
})
