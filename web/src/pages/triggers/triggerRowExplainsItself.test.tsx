import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

/**
 * A trigger row says WHICH state it is in, and WHY (issue 496).
 *
 * 🔴 THE DEFECT, measured against a live gateway with one trigger seeded per state the backend can
 * report. The row's right-hand cell drew a coloured glyph and a timestamp, and nothing else:
 *
 *   Nightly backup sync   autopaused, health failing   → red X, no word, no reason
 *   Feed summariser       QUARANTINED, health failing  → THE SAME red X, no word, no reason
 *   Lap2 regression probe never fired, health ok       → ok-GREEN tick beside the word "never"
 *   app-note-mirror       PARKED, app uninstalled      → neutral "no data" circle
 *
 * Eight of the seventeen rows carried a `last_error` on the wire — including the reap reason the
 * issue was filed over, *"Reaped after 1811s (exceeded 1800s deadline)"* — and the list rendered
 * none of them. This drives the REAL `TriggersSection` against those exact payloads.
 *
 * Every positive has a vacuity leg: a page that labelled and explained every row would satisfy the
 * positives, so the healthy rows must stay quiet.
 */

const SCHED = (over: Record<string, unknown> = {}) => ({
  kind: 'schedule',
  id: 'schedule:clock:probe',
  raw_id: 'clock:probe',
  name: 'Nightly backup sync',
  enabled: true,
  schedule: 'every day at 02:00',
  action: { provider: 'notify', config: {} },
  last_run_ts: 1_786_000_000,
  last_run_status: '',
  last_status: 'ok',
  state: 'active',
  last_error: null,
  run_count: 5,
  next_run_ts: null,
  broken: [],
  ...over,
})
const STORE = (over: Record<string, unknown> = {}) => ({
  kind: 'store',
  id: 'store:file:docs-mirror',
  raw_id: 'file:docs-mirror',
  store_kind: 'file',
  name: 'Docs mirror',
  enabled: true,
  action: { provider: 'notify', config: {} },
  spec: {},
  health: 'ok',
  state: 'active',
  last_error: '',
  run_count: 9,
  broken: [],
  ...over,
})
const EVENT = (over: Record<string, unknown> = {}) => ({
  kind: 'event',
  id: 'event:app-note-mirror',
  raw_id: 'app-note-mirror',
  name: 'app-note-mirror',
  enabled: true,
  pattern: 'AppEvent',
  action: { provider: 'notify', config: {} },
  health: 'ok',
  state: 'active',
  last_error: '',
  fire_count: 3,
  ...over,
})

const REAP = 'Reaped after 1811s (exceeded 1800s deadline)'

const { STATE } = vi.hoisted(() => ({
  STATE: { jobs: [] as unknown[], stores: [] as unknown[], events: [] as unknown[] },
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    schedules: () => Promise.resolve({ jobs: STATE.jobs }),
    hooks: () => Promise.resolve([]),
    storeTriggers: () => Promise.resolve(STATE.stores),
    eventTriggers: () => Promise.resolve(STATE.events),
    actionProviders: () => Promise.resolve([]),
    autonomyLadder: () => Promise.reject(new Error('no ladder in this test')),
    triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: [], event: [] }),
  },
}))

const { TriggersSection } = await import('./TriggersSection')

const mount = () =>
  render(<TriggersSection sub="" navigate={vi.fn()} navEpoch={0} query={{}} setQuery={() => {}} />)

beforeEach(() => {
  sessionStorage.clear()
  STATE.jobs = []
  STATE.stores = []
  STATE.events = []
})

describe('the row names its state', () => {
  it('labels an autopaused schedule "autopaused"', async () => {
    STATE.jobs = [SCHED({ state: 'autopaused', last_status: 'failing', last_error: 'paused after 5 consecutive failures' })]
    mount()
    await waitFor(() => expect(screen.getByText('Nightly backup sync')).toBeInTheDocument())
    expect(screen.getByText('autopaused')).toBeInTheDocument()
  })

  it('labels a quarantined schedule "quarantined", not "failing"', async () => {
    // Both are `health: failing`. Quarantine cannot be undone with the row's toggle
    // (`resume_state` refuses it), so calling it "failing" points the user at a dead button.
    STATE.jobs = [SCHED({ name: 'Feed summariser', state: 'quarantined', last_status: 'failing', last_error: 'a payload matched an injection pattern' })]
    mount()
    await waitFor(() => expect(screen.getByText('Feed summariser')).toBeInTheDocument())
    expect(screen.getByText('quarantined')).toBeInTheDocument()
    expect(screen.queryByText('failing')).toBeNull()
  })

  it('labels a parked event trigger "parked" instead of the never-run dot', async () => {
    STATE.events = [EVENT({ state: 'parked', health: 'parked', last_error: 'the app that owns this event is gone' })]
    mount()
    await waitFor(() => expect(screen.getByText('app-note-mirror')).toBeInTheDocument())
    expect(screen.getByText('parked')).toBeInTheDocument()
  })

  it('says "never" for a trigger that has never fired — and does NOT tick it green', async () => {
    // The measured overstatement: `last_status: 'ok'` is the dataclass DEFAULT on a never-fired row.
    STATE.jobs = [SCHED({ name: 'Lap2 regression probe', last_run_ts: null, run_count: 0, last_status: 'ok' })]
    mount()
    await waitFor(() => expect(screen.getByText('Lap2 regression probe')).toBeInTheDocument())
    expect(screen.getByText('never')).toBeInTheDocument()
    expect(screen.queryByText('ok'), 'a never-fired trigger must not claim a successful run').toBeNull()
  })

  it('does not print "never" for a store row that HAS fired but carries no timestamp', async () => {
    // A store row sends no `last_run_ts`, so this cell used to print "never" beside `run_count: 9`.
    STATE.stores = [STORE({ run_count: 9 })]
    mount()
    await waitFor(() => expect(screen.getByText('Docs mirror')).toBeInTheDocument())
    expect(screen.queryByText('never'), '9 recorded fires is not "never"').toBeNull()
  })

  it('vacuity leg: a healthy schedule that HAS run reads ok and nothing else', async () => {
    STATE.jobs = [SCHED({ last_run_status: 'ran', run_count: 3 })]
    mount()
    await waitFor(() => expect(screen.getByText('Nightly backup sync')).toBeInTheDocument())
    expect(screen.getByText('ran')).toBeInTheDocument()
    expect(screen.queryByText('autopaused')).toBeNull()
    expect(screen.queryByText('quarantined')).toBeNull()
  })
})

describe('the row explains itself', () => {
  it('renders the reason beside a non-ok row', async () => {
    // The issue's own example, on the surface the issue named.
    STATE.jobs = [SCHED({ name: 'system:notification-digest', last_status: 'degraded', last_error: REAP })]
    mount()
    await waitFor(() => expect(screen.getByText('system:notification-digest')).toBeInTheDocument())
    expect(screen.getByText(REAP)).toBeInTheDocument()
  })

  it('renders the reason for a degraded STORE automation too', async () => {
    STATE.stores = [STORE({ health: 'degraded', last_error: REAP })]
    mount()
    await waitFor(() => expect(screen.getByText('Docs mirror')).toBeInTheDocument())
    expect(screen.getByText(REAP)).toBeInTheDocument()
  })

  it('renders the reason for a parked EVENT trigger too', async () => {
    STATE.events = [EVENT({ state: 'parked', health: 'parked', last_error: 'the app that owns this event is gone' })]
    mount()
    await waitFor(() => expect(screen.getByText('app-note-mirror')).toBeInTheDocument())
    expect(screen.getByText('the app that owns this event is gone')).toBeInTheDocument()
  })

  it('vacuity leg: WITHHOLDS a stale reason from a row that has recovered', async () => {
    // `last_error_summary` is never cleared on success — `unpark_due` resets `health_status` to `ok`
    // and leaves the text — so an ungated reason reports a fault that is already fixed.
    STATE.jobs = [SCHED({ last_status: 'ok', last_run_status: 'ran', run_count: 4, last_error: REAP })]
    mount()
    await waitFor(() => expect(screen.getByText('Nightly backup sync')).toBeInTheDocument())
    expect(screen.queryByText(REAP), 'a healthy row must not report a fixed fault').toBeNull()
  })

  it('vacuity leg: withholds it from a trigger that has not run yet', async () => {
    STATE.jobs = [SCHED({ last_run_ts: null, run_count: 0, last_status: 'ok', last_error: REAP })]
    mount()
    await waitFor(() => expect(screen.getByText('Nightly backup sync')).toBeInTheDocument())
    expect(screen.queryByText(REAP)).toBeNull()
  })
})
