import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ScheduleWidget } from './ScheduleWidget'
import type { ScheduleRun } from '../../../lib/api'

// ── A Recent-activity row must SAY WHICH AUTOMATION IT IS (issue 466) ─────────
//
// 🔴 THE DEFECT, measured on a live gateway with five real fires. The widget read
// `r.job_name || r.job_id || 'Schedule'`, and `/api/triggers/history`'s default (unified) shape
// sends neither name — it sends `trigger_id` and `trigger_name`. So the fallback fired on every
// row and the first screen of the product rendered:
//
//     Schedule   ran      4m ago
//     Schedule   failed   4m ago
//     Schedule   failed   5m ago
//     Schedule   failed   5m ago
//     Schedule   failed   5m ago
//
// Five different automations, one label. Worse in the accessibility tree, where four rows shared
// ONE name ("Schedule — failed") — the exact too-little-information shape `rowSubject`'s own doc
// warns about. And each failure said "failed" and nothing else, because `reason` (the row's
// MANDATORY one-line why) had no reader on this surface at all.
//
// These are RENDER assertions on purpose. The Python rail
// (`tests/test_dashboard_widget_payload_reads.py`) proves the widget's reads exist in the
// endpoint's real response; only rendering proves a user can tell two rows apart. The 8-of-8
// mismatch this issue reports compiled cleanly and passed every existing test, because every
// existing test for this widget checks `statusMeta` in isolation and never mounts the row.

vi.mock('../DashboardLive', () => ({
  useDashboardLive: () => ({
    schedule: RUNS,
    scheduleDidIds: RUNS.map((r) => r.id as string),
    scheduleSuppressed: 0,
  }),
}))

/** Rows in the shape `/api/triggers/history` really sends — copied from a live response, not
 *  invented: `trigger_id`/`trigger_name`/`outcome`/`reason`, ISO timestamps, no `job_name`. */
const RUNS: ScheduleRun[] = [
  {
    id: 'manual-1', trigger_id: 'schedule:clock:morning-ping', trigger_name: 'Morning ping',
    outcome: 'ran', reason: 'notified: Morning', weight: 'full',
    started_at: '2026-09-06T09:24:32.510006+00:00', finished_at: '2026-09-06T09:24:32.517305+00:00',
  },
  {
    id: 'manual-2', trigger_id: 'schedule:clock:nightly-digest', trigger_name: 'Nightly digest',
    outcome: 'failed', reason: "invoke-agent hook is missing 'task_template'", weight: 'full',
    started_at: '2026-09-06T09:23:56.971775+00:00', finished_at: '2026-09-06T09:23:56.971811+00:00',
  },
  {
    // A kind with no name of its own: an event trigger. The row must still not read "Schedule".
    id: 'event:memory-watcher:summary', trigger_id: 'event:memory-watcher',
    trigger_name: 'memory-watcher', outcome: 'ran', reason: '5 fire(s) recorded', weight: 'ledger',
    started_at: '2026-09-06T09:20:00.000000+00:00', finished_at: '2026-09-06T09:20:00.000000+00:00',
  },
]

const navigate = vi.fn()
/** The full `RouteProps` the widget's signature requires — spread at each mount site so the props
 *  stay honest against the real interface rather than cast away with `as`. */
const route = { sub: '', navigate, navEpoch: 0, query: {}, setQuery: vi.fn() }

describe('a Recent-activity row identifies its automation', () => {
  it('renders each automation BY NAME, not the literal word "Schedule"', () => {
    render(<ScheduleWidget {...route} />)
    expect(screen.getByText('Morning ping')).toBeTruthy()
    expect(screen.getByText('Nightly digest')).toBeTruthy()
    expect(screen.getByText('memory-watcher')).toBeTruthy()
    // The fallback must not be reachable while every row carries a name.
    expect(screen.queryByText('Schedule')).toBeNull()
  })

  it('names each row IN THE ACCESSIBILITY TREE for the automation it reports', () => {
    // Distinctness alone is too weak a bar, and a mutation run proved it: reverting the label to the
    // dead `r.job_name || r.job_id` read left all three names distinct anyway, because `reason`
    // differs per row. So this asserts CONTAINMENT — each row's accessible name carries its own
    // automation's name — which is the property a screen-reader user actually needs. Distinctness
    // is then asserted on top, since it is what the original defect broke (four rows, one name).
    render(<ScheduleWidget {...route} />)
    const names = screen.getAllByRole('button')
      .map((b) => b.getAttribute('aria-label') || '')
      .filter((n) => /ran|failed/.test(n))
    expect(names.length).toBe(3)
    expect(new Set(names).size).toBe(3)
    for (const run of RUNS) {
      expect(names.some((n) => n.startsWith(run.trigger_name as string))).toBe(true)
    }
  })

  it('shows WHY a failed run failed', () => {
    // `reason` is documented as mandatory for any non-clean outcome precisely because "failed"
    // alone tells the user their automation did not happen and nothing else.
    render(<ScheduleWidget {...route} />)
    expect(screen.getByText(/invoke-agent hook is missing 'task_template'/)).toBeTruthy()
  })

  it('renders a real relative time from an ISO timestamp, never NaN', () => {
    // The endpoint sends ISO-8601 while the schedule routes send epoch seconds; the widget's `rel`
    // composes `epochSeconds`, so both coerce. Six rows of "in NaNd" was this issue's headline
    // symptom and its regression guard belongs on a row that actually renders.
    render(<ScheduleWidget {...route} />)
    expect(screen.queryByText(/NaN/)).toBeNull()
  })

  it('falls back to the trigger id — not "Schedule" — when a row carries no name', () => {
    // The honest fallback: an id a user can match against the Triggers page beats a word that is
    // the same on every row.
    const one: ScheduleRun = { ...RUNS[0], trigger_name: '' }
    expect(one.trigger_name || one.trigger_id || 'Schedule').toBe('schedule:clock:morning-ping')
  })
})
