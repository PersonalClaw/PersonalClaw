import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import { CompanionPanel } from './CompanionPanel'

// ── Phone push shows what is stored, or says it could not read it ─────────────────────────────
//
// The Phone push section reads `mobile` on its own, and the panel's failure branch covers a
// DIFFERENT read — the panel's main query can paint from its persisted cache while this one
// refetches and fails. When only this read failed, the pills rendered "Web push" as selected
// whatever was stored, the ntfy topic field (and the URL in it) vanished, and each click wrote.
// `mobilePushSettings.test.tsx` owns the read-succeeded behaviour.
//
// Same family as the onboarding defect (`app/identityReadFailure.test.tsx`): a failed read became
// the default, and the default licensed a write.

const patchConfig = vi.fn()
const config = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    personalclawConfig: () => config(),
    patchConfig: (path: string, value: unknown) => patchConfig(path, value),
    companionDiscovery: () => Promise.resolve({ advertising: false, reason: 'off', detail: 'Off.', service_type: '', instance_name: '', port: 0, addresses: [], txt: {} }),
    pushStatus: () => Promise.resolve({ backend: 'ntfy', vapid_public_key: '', vapid_ready: false, devices: [] }),
  },
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))

const stored = {
  companion: { discovery_enabled: false, instance_name: '' },
  browse: { user_browser_enabled: false },
  mobile: { push_backend: 'ntfy', ntfy_topic_url: 'https://ntfy.example/personalclaw' },
}

beforeEach(() => {
  patchConfig.mockReset().mockResolvedValue({})
  config.mockReset()
  sessionStorage.clear()
})
afterEach(cleanup)

/** The panel's own read answers; the Phone push section's read (the second one) fails first. */
function failThePushRead() {
  let calls = 0
  config.mockImplementation(() => (++calls === 2 ? Promise.reject(new Error('config unreadable')) : Promise.resolve(stored)))
}

describe('the Phone push section', () => {
  it('a failed read of it shows the failure, and offers no backend to pick', async () => {
    failThePushRead()
    render(<CompanionPanel />)
    expect(await screen.findByText(/Couldn't read your phone push settings: config unreadable/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Push backend: Web push' }),
      'the backend pills stood in for an unread setting').toBeNull()
    expect(screen.queryByRole('button', { name: 'Push backend: Off' })).toBeNull()
    expect(patchConfig).not.toHaveBeenCalled()
  })

  it('a retry that reads it shows what is stored — ntfy, with its topic', async () => {
    failThePushRead()
    render(<CompanionPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /Retry/ }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Push backend: ntfy' }).getAttribute('aria-pressed')).toBe('true'))
    expect((screen.getByPlaceholderText('https://ntfy.example/personalclaw') as HTMLInputElement).value)
      .toBe('https://ntfy.example/personalclaw')
  })
})
