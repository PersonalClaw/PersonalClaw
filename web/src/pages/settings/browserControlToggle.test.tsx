import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── A rendered control bound to nothing (BROWSE-AUTOMATION BA-7) ───────────────────────────────
//
// `browse.user_browser_enabled` gates the SECOND browse execution target: the operator's own
// browser, with every site they are already signed in to. The backend refuses that target when the
// switch is off — a task asking for it is SKIPPED and never re-pointed at the gateway's own profile.
//
// 🪤 THE FAILURE THIS RAIL EXISTS FOR: the switch renders, the user flips it, and nothing is
// written — because the panel PATCHes the wrong section prefix, or no path at all. The config
// round-trip's Python half cannot see this: `_EDITABLE_CONFIG` can hold the key while the only
// control for it posts to `companion.user_browser_enabled`, and every backend test stays green.
// So the assertion is on the PATH STRING the click actually sends.
//
// 🪤 AND THE LIMITS ARE ON SCREEN: "skipped, not switched to the other profile" and "never for a
// scheduled run" are the two things a user cannot infer from a switch labelled with a verb.

const patchConfig = vi.fn((_path: string, _value: unknown) => Promise.resolve({}))
vi.mock('../../lib/api', () => ({
  api: {
    pushStatus: () => Promise.resolve({ backend: 'webpush', vapid_public_key: 'k', vapid_ready: true, devices: [] }),
    personalclawConfig: () =>
      Promise.resolve({
        companion: { discovery_enabled: false, instance_name: '' },
        browse: { user_browser_enabled: false },
      }),
    patchConfig: (path: string, value: unknown) => patchConfig(path, value),
    companionDiscovery: () => Promise.resolve(null),
  },
}))

describe('the browser-control toggle writes the path it claims', () => {
  it('PATCHes browse.user_browser_enabled when flipped on', async () => {
    patchConfig.mockClear()
    const { CompanionPanel } = await import('./CompanionPanel')
    render(<CompanionPanel />)
    const toggle = await screen.findByRole('switch', { name: /let tasks drive my browser/i })
    expect(toggle).toHaveAttribute('aria-checked', 'false')
    await userEvent.click(toggle)
    // The whole rail: the SECTION PREFIX is what a copied panel gets wrong, and a wrong prefix
    // is a 400 the optimistic UI rolls back after the user has already walked away.
    expect(patchConfig).toHaveBeenCalledWith('browse.user_browser_enabled', true)
  })

  it('states both limits beside the switch, not only in the docs', async () => {
    const { CompanionPanel } = await import('./CompanionPanel')
    render(<CompanionPanel />)
    await screen.findByRole('switch', { name: /let tasks drive my browser/i })
    // No silent fallback, said in the words a user would use.
    expect(screen.getByText(/never switched to this machine's own browser profile/i)).toBeTruthy()
    // The never-unattended floor.
    expect(screen.getByText(/scheduled and unattended runs can never use your browser/i)).toBeTruthy()
  })
})
