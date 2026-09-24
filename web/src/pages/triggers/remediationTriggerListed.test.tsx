import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

// ── PR2-8: "runs as ONE adaptive-clock trigger (created_by: system) on the Automations page" ──
//
// The FE half of that clause, and the reason it exists as its own file: a trigger row that
// exists in storage but never RENDERS is this repo's recurring defect, and the backend test
// (`tests/test_resilience_remediation_trigger.py`) can only prove the projection carries it.
// So this drives the real `TriggersSection` at `#/triggers` with the row the backend actually
// serves, and asserts the user can see it. Delete the list render, drop the row from the
// composed `triggers` memo, or filter system-created rows out, and this fails.
//
// Two things are deliberately NOT asserted through a snapshot: the row's NAME and its CADENCE
// sentence. "adaptive" alone would tell a user nothing about when maintenance next runs, so the
// column has to carry both cadences — and a test that only checked for the name would pass on a
// row rendering an empty schedule cell.
//
// The vacuity leg is the second `describe`: with the same page and an EMPTY schedule list the
// name must be absent, so the positive assertion cannot be satisfied by a page that renders the
// word "Self-remediation" from anywhere else (a preset, a heading, an empty state).

const REMEDIATION_ROW = {
  kind: 'schedule',
  id: 'schedule:system:self-remediation',
  raw_id: 'system:self-remediation',
  name: 'Self-remediation',
  enabled: true,
  // What `describe_cadence` renders for an `adaptive` clock — pinned here in the shape the wire
  // carries it, not re-derived, so a change to that sentence surfaces as a diff in both halves.
  schedule: 'adaptive — every 60m healthy, 5m degraded (now: healthy)',
  action: { provider: 'self-remediation', config: {} },
  last_run_ts: null,
  last_run_status: '',
  last_status: 'ok',
  run_count: 0,
  next_run_ts: null,
  is_running: false,
  created_by: 'system',
  author: '',
  read_only: false,
  broken: [],
}

const { STATE } = vi.hoisted(() => ({ STATE: { jobs: [] as unknown[] } }))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    schedules: () => Promise.resolve({ jobs: STATE.jobs }),
    hooks: () => Promise.resolve([]),
    storeTriggers: () => Promise.resolve([]),
    eventTriggers: () => Promise.resolve([]),
    actionProviders: () => Promise.resolve([]),
    autonomyLadder: () => Promise.reject(new Error('no ladder in this test')),
    triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: [], event: [] }),
  },
}))

const { TriggersSection } = await import('./TriggersSection')

const mount = (query: Record<string, string> = {}) =>
  render(
    <TriggersSection sub="" navigate={vi.fn()} navEpoch={0} query={query} setQuery={() => {}} />,
  )

beforeEach(() => { sessionStorage.clear() })

describe('the self-remediation trigger on the Triggers page', () => {
  beforeEach(() => { STATE.jobs = [REMEDIATION_ROW] })

  it('renders the engine as a row a user can see and open', async () => {
    mount()
    await waitFor(() => expect(screen.getByText('Self-remediation')).toBeInTheDocument())
    // The ROW itself, not merely the text: `ListRow` is a button labelled with the trigger name,
    // so this is what a user clicks to inspect the engine.
    expect(screen.getByRole('button', { name: 'Self-remediation' })).toBeInTheDocument()
  })

  it('tells the user BOTH cadences and which one is live', async () => {
    mount()
    await waitFor(() => expect(screen.getByText(REMEDIATION_ROW.schedule)).toBeInTheDocument())
  })

  it('names the action it runs, from the declared label rather than the id-prettifier', async () => {
    mount()
    // `actionLabel('self-remediation')`, drawn beside the cadence. The unmapped fallback would
    // render `Self remediation` — a second spelling of the backend `display_name` — and the unmapped
    // ICON is the same `Zap` bolt every other unlabelled provider draws, so a maintenance pass and a
    // shell command would be pixel-identical on this row.
    await waitFor(() => expect(screen.getByText('Self-Remediation')).toBeInTheDocument())
  })

  it('survives the type filter it belongs to, and is excluded by one it does not', async () => {
    // The page's filter taxonomy, driven through the URL the way a user's click sets it. An
    // adaptive clock is a SCHEDULE row (`api_triggers` projects every `kind: clock` through
    // `_schedule_rows`), so a filter that dropped it would hide the engine from the one view a user
    // narrows to when looking for a recurring job.
    mount({ filter: 'schedule' })
    await waitFor(() => expect(screen.getByText('Self-remediation')).toBeInTheDocument())

    // The paired negative: filtering to a kind it is NOT must exclude it. Without this leg the
    // assertion above would pass on a page that ignored the filter entirely.
    mount({ filter: 'lifecycle' })
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'No matching triggers' })).toBeInTheDocument(),
    )
  })
})

describe('the vacuity control', () => {
  beforeEach(() => { STATE.jobs = [] })

  it('does not render the name when the backend serves no such row', async () => {
    mount()
    await waitFor(() => expect(screen.getByRole('heading', { name: 'No triggers' })).toBeInTheDocument())
    expect(screen.queryByText('Self-remediation')).not.toBeInTheDocument()
    expect(screen.queryByText(REMEDIATION_ROW.schedule)).not.toBeInTheDocument()
  })
})
