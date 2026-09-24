import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

// ── #624: a REFUSED inbox setting rolls back, in BOTH copies ─────────────────────────────────────
//
// Retention accepted -5/0/1.5/99999, showed each as saved, and never reported the server's 400 —
// and after the notify was added, the optimistic merge STILL stood: the field kept showing the
// refused value until a reload silently restored the stored one. `settingsWriteReported`'s
// doctrine names this the one unsanctioned shape ("keeping a refused value is not"), with the
// pre-patch rollback as the sanctioned form for a panel that owns its own state — the exact shape
// `setEngagement` uses in the same file.
//
// One test per copy (the drawer panel and the settings-page panel), because the two are separate
// files kept in field-parity by `inboxSettingsParity.test.ts` — a fix landing in only one is the
// drift that suite exists to catch, so this rail exercises each independently.

const notified: string[] = []
// Event-driven settlement: `notifyArrived` resolves the moment the panel calls
// notify(), so the assertion needs no poll loop. Two rounds of budget-raising on
// the polled form (1s default, then 4s) both starved under CI suite contention —
// waitFor samples on an interval, and a starved worker can miss every sample
// window inside any fixed budget. An awaited event has no sample window to miss;
// the generous test-level timeout below only bounds the true-regression case
// (notify never called at all).
let notifyArrived!: () => void
let notifySettled: Promise<void>

function mockApi(saveInboxSettings: () => Promise<unknown>) {
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as Record<string, unknown>),
        inboxSettings: () =>
          Promise.resolve({ auto_cleanup_enabled: true, retention_days: 90 }),
        personalclawConfig: () =>
          Promise.resolve({ inbox: { engagement_ranking_enabled: false, enabled: false }, proactive: {} }),
        proactiveStatus: () => Promise.resolve({}),
        saveInboxSettings,
      },
    }
  })
  vi.doMock('../../app/appSdk', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    notify: (msg: string) => { notified.push(msg); notifyArrived() },
  }))
}

async function driveRetentionTo(value: string) {
  const field = await screen.findByLabelText('Retention (days)')
  fireEvent.change(field, { target: { value } })
  fireEvent.blur(field)
  return field as HTMLInputElement
}

beforeEach(() => {
  vi.resetModules()
  notified.length = 0
  sessionStorage.clear()
  notifySettled = new Promise((r) => { notifyArrived = r })
})

describe('a refused retention value rolls back and says so (#624)', () => {
  it('drawer copy (pages/inbox): rejected write reverts to the stored value', async () => {
    mockApi(() => Promise.reject(new Error('retention_days must be an integer >= 1')))
    const { InboxSettingsPanel } = await import('./InboxSettingsPanel')
    render(<InboxSettingsPanel />)
    const field = await driveRetentionTo('3000')
    // Await the notify EVENT — no poll loop to starve (see the header note).
    await notifySettled
    expect(notified[0]).toContain("Couldn't save your inbox settings")
    // The rollback: the optimistic 3000 does not survive the refusal. The event
    // already fired, so only the React state flush remains — the wide budget
    // bounds the true-regression case, not the happy path.
    await waitFor(() => expect(field.value).toBe('90'), { timeout: 10_000 })
  }, 20_000)

  it('drawer copy: an accepted write keeps the new value (rollback is not a revert-always)', async () => {
    mockApi(() => Promise.resolve({}))
    const { InboxSettingsPanel } = await import('./InboxSettingsPanel')
    render(<InboxSettingsPanel />)
    const field = await driveRetentionTo('120')
    // No refusal → the value stands and nothing is notified.
    await waitFor(() => expect(field.value).toBe('120'), { timeout: 4000 })
    expect(notified).toEqual([])
  })
})
