/** Web push says its readiness OUT LOUD — the silent-non-delivery rail.
 *
 *  'Web push' is the DEFAULT backend, and it cannot deliver anything until the
 *  gateway holds a VAPID keypair from `personalclaw push init`. Before this
 *  rail's fix, the only mention of that prerequisite was a hint sentence; the
 *  actual state (`vapid_ready`, which the backend has always exposed on
 *  GET /api/push) rendered nowhere. A selected default that silently sends
 *  nothing is the same defect class the Install & offline row fixed — its
 *  comment argues "saying so out loud beats an install button that silently
 *  never appears" — so the readiness row follows that idiom exactly: words +
 *  tone, never color alone.
 *
 *  Pins: the row renders READY truthfully; renders NOT SET UP with the exact
 *  remedy command when the keypair is missing; and is ABSENT for backends that
 *  do not read it (the file's own only-shown-for-the-backend-that-reads-it
 *  rule, same as the ntfy topic field).
 */

import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import { CompanionPanel } from './CompanionPanel'
import { invalidateKeys } from '../../lib/data'

const cfgWith = (mobile: Record<string, unknown>) => ({
  companion: { discovery_enabled: false, instance_name: '' },
  mobile,
})

let configResult: () => Promise<unknown> = () => Promise.resolve(cfgWith({}))
let pushStatusResult = () =>
  Promise.resolve({ backend: 'webpush', vapid_public_key: '', vapid_ready: false, devices: [] })

vi.mock('../../lib/api', () => ({
  api: {
    personalclawConfig: () => configResult(),
    patchConfig: () => Promise.resolve({}),
    companionDiscovery: () =>
      Promise.resolve({ advertising: false, reason: 'off', detail: 'Off.', service_type: '', instance_name: '', port: 0, addresses: [], txt: {} }),
    pushStatus: () => pushStatusResult(),
  },
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))
vi.mock('../../app/registerServiceWorker', () => ({ serviceWorkerBlockedReason: () => null }))

afterEach(() => {
  cleanup()
})

beforeEach(() => {
  // useQuery caches across renders; each test re-seeds config + push status.
  invalidateKeys('settings:companion')
  invalidateKeys('companion:push')
})

describe('the web push keypair readiness row', () => {
  it('says READY when the gateway holds a keypair', async () => {
    configResult = () => Promise.resolve(cfgWith({ push_backend: 'webpush' }))
    pushStatusResult = () =>
      Promise.resolve({ backend: 'webpush', vapid_public_key: 'pk', vapid_ready: true, devices: [] })
    render(<CompanionPanel />)
    await waitFor(() => expect(screen.getByText('Ready')).toBeTruthy())
    expect(screen.getByText(/subscribed devices can receive pushes/i)).toBeTruthy()
  })

  it('says NOT SET UP with the exact remedy when the keypair is missing', async () => {
    configResult = () => Promise.resolve(cfgWith({ push_backend: 'webpush' }))
    pushStatusResult = () =>
      Promise.resolve({ backend: 'webpush', vapid_public_key: '', vapid_ready: false, devices: [] })
    render(<CompanionPanel />)
    await waitFor(() => expect(screen.getByText('Not set up')).toBeTruthy())
    // The remedy names the command and the consequence — not just a state word.
    // The Field hint mentions the command too; the ROW's remedy is the one with the reload step.
    expect(screen.getAllByText(/personalclaw push init/).length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText(/Web push sends nothing/i)).toBeTruthy()
  })

  it('renders for the DEFAULT (unset) backend too — the first-run state is webpush', async () => {
    configResult = () => Promise.resolve(cfgWith({}))
    pushStatusResult = () =>
      Promise.resolve({ backend: 'webpush', vapid_public_key: '', vapid_ready: false, devices: [] })
    render(<CompanionPanel />)
    await waitFor(() => expect(screen.getByText('Not set up')).toBeTruthy())
  })

  it('is absent for backends that do not read it', async () => {
    for (const backend of ['ntfy', 'none']) {
      invalidateKeys('settings:companion')
  invalidateKeys('companion:push')
      configResult = () => Promise.resolve(cfgWith({ push_backend: backend }))
      pushStatusResult = () =>
        Promise.resolve({ backend, vapid_public_key: '', vapid_ready: false, devices: [] })
      render(<CompanionPanel />)
      await waitFor(() => expect(screen.getByText('Phone push')).toBeTruthy())
      expect(screen.queryByText('Not set up')).toBeNull()
      expect(screen.queryByText(/Keypair/)).toBeNull()
      cleanup()
    }
  })
})
