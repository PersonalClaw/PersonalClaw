import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

// 🔴 The 900s-floor warning had NO SURFACE (issue 531). `models.validate_spec` raises it for any
// interval under `MIN_CLOCK_INTERVAL_SECS` and its own comment promises the row is "visibly
// flagged"; measured on a live gateway, the wire carried `broken: []` and no `warnings` key at all,
// so the one thing standing between a typo and a per-minute LLM invocation was a warning the
// backend computed and every surface then dropped.
//
// This drives the real `TriggersSection` with a row whose `warnings` is populated. The vacuity leg
// asserts a clean row stays quiet — a badge on every row is a badge users learn to ignore, which is
// the same failure as no badge. The precedence leg asserts an ERROR row shows only "needs
// attention": two verdicts side by side on one row make the user decide which to believe.

const SCHED = (over: Record<string, unknown> = {}) => ({
  kind: 'schedule',
  id: 'schedule:clock:fast-poll',
  raw_id: 'clock:fast-poll',
  name: 'Fast poll',
  enabled: true,
  schedule: 'every 60s',
  action: { provider: 'run-prompt', config: {} },
  last_run_ts: null,
  last_run_status: '',
  last_status: 'ok',
  run_count: 0,
  next_run_ts: null,
  author: '',
  read_only: false,
  broken: [],
  warnings: [],
  ...over,
})

const FLOOR_WARNING =
  '60s is below the 900s floor for an LLM-invoking trigger; it will still run, but confirm this is intended'

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

const mount = () =>
  render(<TriggersSection sub="" navigate={vi.fn()} navEpoch={0} query={{}} setQuery={() => {}} />)

beforeEach(() => { sessionStorage.clear() })

describe('a sub-floor cadence is visible on the list', () => {
  it('flags a row whose cadence is below the LLM-invoking floor', async () => {
    STATE.jobs = [SCHED({ warnings: [FLOOR_WARNING] })]
    mount()
    await waitFor(() => expect(screen.getByText('Fast poll')).toBeInTheDocument())
    expect(screen.getByText(/check schedule/)).toBeInTheDocument()
  })

  it('carries the WHY on the row, readable without hovering', async () => {
    // A bare badge sends the user hunting — and a reason that lived only in the badge's `title`
    // (B10, 2026-09-25) was one hover away on a desktop and unreachable on touch. The diagnosis is
    // printed on the row itself now, in full.
    STATE.jobs = [SCHED({ warnings: [FLOOR_WARNING] })]
    mount()
    await waitFor(() => expect(screen.getByText('Fast poll')).toBeInTheDocument())
    // `getByText` matches rendered TEXT, never an attribute — so this finds the reason only if it
    // is on the row as words, which is the whole change.
    expect(screen.getByText(FLOOR_WARNING)).toBeInTheDocument()
    expect(screen.getByText(/check schedule/)).not.toHaveAttribute('title')
  })

  it('stays quiet for a row with no warnings (vacuity leg)', async () => {
    STATE.jobs = [SCHED({ warnings: [] })]
    mount()
    await waitFor(() => expect(screen.getByText('Fast poll')).toBeInTheDocument())
    expect(screen.queryByText(/check schedule/)).toBeNull()
  })

  it('yields to "needs attention" when the row also has an ERROR', async () => {
    STATE.jobs = [SCHED({ broken: ['spec.expr: a cron clock needs an expression'], warnings: [FLOOR_WARNING] })]
    mount()
    await waitFor(() => expect(screen.getByText('Fast poll')).toBeInTheDocument())
    expect(screen.getByText(/needs attention/)).toBeInTheDocument()
    expect(screen.queryByText(/check schedule/)).toBeNull()
    expect(screen.queryByText(FLOOR_WARNING)).toBeNull()
  })
})
