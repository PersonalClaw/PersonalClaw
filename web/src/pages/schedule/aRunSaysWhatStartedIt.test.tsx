import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import type { ScheduleJob, Trigger as WireTrigger } from '../../lib/api'
import { runSourceMeta } from './scheduleMeta'
import { scheduleToTrigger, storeToTrigger } from '../triggers/triggerMeta'

// ── A run says what started it ───────────────────────────────────────────────────────────────────
//
// Every row of an automation's run history keeps what started its run (`ScheduleRun.source`): you,
// what fires it on its own, or who else asked for it by name. Only a run of yours passes over the
// hourly cap and the failure streak, so a run an agent asked for must never read as yours. It did:
// the history tagged every run anyone asked for `manual`, the tag your own Run now has, and the
// Triggers page said nothing of who started the last run.

const RUNS = [
  { run_id: 'r-agent', status: 'success', summary: 'Wrote the standup notes.', started_at: '2026-10-04T09:03:00Z', source: 'agent' },
  { run_id: 'r-you', status: 'success', summary: 'Wrote the standup notes.', started_at: '2026-10-04T09:02:00Z', source: 'you' },
  { run_id: 'r-hook', status: 'failure', summary: '', error: 'the notes server said no', started_at: '2026-10-04T09:01:00Z', source: 'webhook' },
  // Recorded before rows said what started them: nothing is guessed for it.
  { run_id: 'r-old', status: 'success', summary: 'Wrote the old notes.', started_at: '2026-10-03T09:00:00Z', trigger: 'manual' },
]

beforeEach(() => {
  cleanup()
  vi.resetModules()
})

async function mountHistory() {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      triggerHistory: () => Promise.resolve({ runs: RUNS, total: RUNS.length, supported: true }),
      triggerRunDetail: () => Promise.resolve({}),
    },
  }))
  const { RunHistory } = await import('./ScheduleDetail')
  render(<RunHistory triggerId="schedule:clock:standup-notes" />)
}

describe('what started a run, in words', () => {
  it('names each source, and says nothing for a row that does not say', () => {
    expect(runSourceMeta('agent')).toEqual({ label: 'agent', title: 'An agent asked for this run' })
    expect(runSourceMeta('you')?.label).toBe('you')
    expect(runSourceMeta('webhook')?.title).toBe('A program posted to its webhook')
    expect(runSourceMeta('')).toBeNull()
    expect(runSourceMeta(undefined)).toBeNull()
    // A word this page does not know is not guessed at.
    expect(runSourceMeta('manual')).toBeNull()
  })
})

describe('the run history', () => {
  it('🔴 shows what started each run, an agent’s apart from yours', async () => {
    await mountHistory()
    const agent = (await screen.findByText('agent')).closest('button')
    expect(agent).not.toBeNull()
    expect(within(agent as HTMLElement).getByText('agent')).toHaveAttribute('title', 'An agent asked for this run')
    expect(screen.getByText('you')).toHaveAttribute('title', expect.stringMatching(/^You ran it/))
    expect(screen.getByText('webhook')).toHaveAttribute('title', 'A program posted to its webhook')
  })

  it('shows no tag on a row recorded before rows said, and never the old one', async () => {
    await mountHistory()
    const old = (await screen.findByText('Wrote the old notes.')).closest('button') as HTMLElement
    expect(old).not.toBeNull()
    for (const word of ['you', 'agent', 'manual']) expect(within(old).queryByText(word)).toBeNull()
  })
})

describe('the Triggers page row', () => {
  it('carries what started the last run, for a schedule and for a stored automation', () => {
    const schedule = scheduleToTrigger({
      id: 'clock:standup-notes', name: 'Standup notes', enabled: true, schedule: 'every 3600s', message: '',
      last_run_source: 'program',
    } as ScheduleJob)
    const stored = storeToTrigger({
      kind: 'store', id: 'store:webhook:deploy', raw_id: 'webhook:deploy', name: 'Deploy', enabled: true,
      action: { provider: 'notify', config: {} }, last_run_source: 'agent',
    } as WireTrigger)
    const never = storeToTrigger({
      kind: 'store', id: 'store:file:notes', raw_id: 'file:notes', name: 'Notes', enabled: true,
      action: { provider: 'notify', config: {} },
    } as WireTrigger)

    expect(schedule.lastRunSource).toBe('program')
    expect(stored.lastRunSource).toBe('agent')
    expect(never.lastRunSource).toBeNull()
  })
})
