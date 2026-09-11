import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { partitionRunsByFold } from './scheduleMeta'

// ── WF2AUT-10: the runs-inbox folds inert runs out of sight, and reveals them on demand ──────────
//
// A `skipped_*` fire (quiet hours, dedupe, a gate) is a real recorded row but NOT a fire that did
// anything. Rendering it inline with real fires makes a quiet history read as busy and a quiet-hours
// skip read like activity. The fold hides those by default behind a control that names how many are
// hidden, so the owner can reveal them when they actually want to see why nothing fired.

const RUNS = [
  { run_id: 'r1', status: 'success', outcome: 'ran', summary: 'Fired the digest', started_at: '2026-09-10T10:00:00Z' },
  { run_id: 'r2', status: 'skipped', outcome: 'skipped_quiet_hours', summary: 'Quiet hours skip', started_at: '2026-09-10T09:00:00Z' },
  { run_id: 'r3', status: 'skipped', outcome: 'skipped_dedupe', summary: 'Deduped', started_at: '2026-09-10T08:00:00Z' },
]

function mockApi() {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      triggerHistory: () => Promise.resolve({ runs: RUNS, total: RUNS.length, supported: true }),
      triggerRunDetail: () => Promise.resolve(RUNS[0]),
    },
  }))
}

async function mount() {
  mockApi()
  const { RunHistory } = await import('./ScheduleDetail')
  render(<RunHistory triggerId="schedule:abc" />)
  await screen.findByText('Fired the digest')
}

beforeEach(() => { vi.resetModules() })

describe('partitionRunsByFold', () => {
  it('splits inert skipped_* rows from real fires; an undefined/plain outcome stays a did', () => {
    const { did, suppressed } = partitionRunsByFold(RUNS)
    expect(did.map((r) => r.run_id)).toEqual(['r1'])
    expect(suppressed.map((r) => r.run_id)).toEqual(['r2', 'r3'])
    // Neither an inert outcome nor a skipped_ status → a did, never accidentally hidden.
    expect(partitionRunsByFold([{ status: 'success' }]).did).toHaveLength(1)
    expect(partitionRunsByFold([{ status: null, outcome: null }]).suppressed).toHaveLength(0)
  })
})

describe('the runs-inbox did/suppressed fold (WF2AUT-10)', () => {
  it('hides suppressed rows by default and reveals them on demand', async () => {
    await mount()
    // The real fire shows; the two suppressed rows are folded away.
    expect(screen.getByText('Fired the digest')).toBeTruthy()
    expect(screen.queryByText('Quiet hours skip')).toBeNull()
    expect(screen.queryByText('Deduped')).toBeNull()

    // The control names the hidden count and reveals on click.
    fireEvent.click(screen.getByText('Show 2 suppressed'))
    await waitFor(() => expect(screen.getByText('Quiet hours skip')).toBeTruthy())
    expect(screen.getByText('Deduped')).toBeTruthy()

    // Toggling again folds them back.
    fireEvent.click(screen.getByText('Hide 2 suppressed'))
    await waitFor(() => expect(screen.queryByText('Quiet hours skip')).toBeNull())
  })
})
