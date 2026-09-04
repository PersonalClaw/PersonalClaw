import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

// ── #624, settings-page copy: a REFUSED inbox setting rolls back ─────────────────────────────────
// The twin of `pages/inbox/inboxRefusedValueRollsBack.test.tsx` — the two panels are separate
// files kept in field-parity by `inboxSettingsParity.test.ts`, so each copy's rollback is
// exercised in its own file. See that rail for the full doctrine trail.

const notified: string[] = []
// Event-driven settlement, mirroring the drawer rail: notify() resolves the
// deferred, so no polled waitFor can starve under CI contention.
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

beforeEach(() => {
  vi.resetModules()
  notified.length = 0
  sessionStorage.clear()
  notifySettled = new Promise((r) => { notifyArrived = r })
})

describe('settings-page copy: a refused retention value rolls back (#624)', () => {
  it('rejected write reverts to the stored value and notifies', async () => {
    mockApi(() => Promise.reject(new Error('retention_days must be an integer >= 1')))
    const { InboxSettingsPanel } = await import('./InboxSettingsPanel')
    render(<InboxSettingsPanel />)
    const field = (await screen.findByLabelText('Retention (days)')) as HTMLInputElement
    fireEvent.change(field, { target: { value: '3000' } })
    fireEvent.blur(field)
    await notifySettled
    expect(notified[0]).toContain("Couldn't save your inbox settings")
    await waitFor(() => expect(field.value).toBe('90'), { timeout: 10_000 })
  }, 20_000)
})
