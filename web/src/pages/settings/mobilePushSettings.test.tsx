import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { CompanionPanel } from './CompanionPanel'
import { invalidateKeys } from '../../lib/data'

// ── `mobile.push_backend` / `mobile.ntfy_topic_url` — the FRONTEND CONTROL (MC-5) ──────────
//
// The config round-trip contract has five points, and the fifth is "a frontend control if
// user-facing". A field with a dataclass, `_meta`, `load()`, `to_dict()` and a PATCH
// allowlist entry but no control is a config field only a text-editor user can reach — which
// is the "inert shipped control" family this repo keeps rediscovering.
//
// 🪤 THE DEFECT THIS RAIL IS FOR is the DOTTED PREFIX. This panel already owned
// `companion.*`, so its existing `patch()` helper hardcodes that prefix. A `mobile.*` field
// wired through it would PATCH `companion.push_backend` — a path the backend allowlist does
// not contain, so the write 400s and the control silently never persists. The prefix is
// therefore asserted, not the fact that "a PATCH happened".

const patchConfig = vi.fn()
const config = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    personalclawConfig: () => config(),
    patchConfig: (path: string, value: unknown) => patchConfig(path, value),
    companionDiscovery: () => Promise.resolve({ advertising: false, reason: 'off', detail: 'Off.', service_type: '', instance_name: '', port: 0, addresses: [], txt: {} }),
    pushStatus: () => pushStatusResult(),
  },
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))

// Reassignable per-test; defaults to a ready keypair so pre-existing tests keep
// their behavior (the readiness row simply reads "Ready" beneath the pills).
let pushStatusResult = () =>
  Promise.resolve({ backend: 'webpush', vapid_public_key: 'k', vapid_ready: true, devices: [] })

const cfgWith = (mobile: Record<string, unknown>) => ({
  companion: { discovery_enabled: false, instance_name: '' },
  mobile,
})

beforeEach(() => {
  patchConfig.mockReset().mockResolvedValue({})
  config.mockReset()
  invalidateKeys('settings:companion')
  invalidateKeys('settings:companion:mobile')
  sessionStorage.clear()
})
afterEach(cleanup)

describe('the phone-push settings control', () => {
  it('renders the three backends and PATCHes the `mobile.` path', async () => {
    config.mockResolvedValue(cfgWith({ push_backend: 'webpush', ntfy_topic_url: '' }))
    render(<CompanionPanel />)

    const ntfy = await screen.findByRole('button', { name: 'Push backend: ntfy' })
    expect(screen.getByRole('button', { name: 'Push backend: Web push' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Push backend: Off' })).toBeTruthy()
    // Which one is selected is in the AX tree, not only in the pill's colour.
    expect(screen.getByRole('button', { name: 'Push backend: Web push' }).getAttribute('aria-pressed')).toBe('true')

    await userEvent.click(ntfy)
    // 🔑 `mobile.push_backend`, NOT `companion.push_backend`.
    expect(patchConfig).toHaveBeenCalledWith('mobile.push_backend', 'ntfy')
  })

  it('only offers the topic URL for the backend that reads it', async () => {
    config.mockResolvedValue(cfgWith({ push_backend: 'webpush', ntfy_topic_url: '' }))
    render(<CompanionPanel />)
    await screen.findByRole('button', { name: 'Push backend: ntfy' })
    // A URL field beside "Web push" would look like a setting that does something.
    expect(screen.queryByPlaceholderText('https://ntfy.example/personalclaw')).toBeNull()

    cleanup()
    invalidateKeys('settings:companion')
    invalidateKeys('settings:companion:mobile')
    sessionStorage.clear()
    config.mockResolvedValue(cfgWith({ push_backend: 'ntfy', ntfy_topic_url: '' }))
    render(<CompanionPanel />)
    expect(await screen.findByPlaceholderText('https://ntfy.example/personalclaw')).toBeTruthy()
  })

  it('saves the topic URL under `mobile.ntfy_topic_url`, and only when it changed', async () => {
    config.mockResolvedValue(cfgWith({ push_backend: 'ntfy', ntfy_topic_url: '' }))
    render(<CompanionPanel />)
    const input = await screen.findByPlaceholderText('https://ntfy.example/personalclaw')

    // Two Save buttons exist on this panel (instance name, topic URL); pick the enabled one
    // after typing rather than by index, which would silently follow a layout change.
    const saves = () => screen.getAllByRole('button', { name: /save/i })
    expect(saves().every((b) => (b as HTMLButtonElement).getAttribute('aria-disabled') === 'true' || (b as HTMLButtonElement).disabled)).toBe(true)

    await userEvent.type(input, 'https://ntfy.example/mine')
    const enabled = saves().find((b) => !(b as HTMLButtonElement).disabled && (b as HTMLButtonElement).getAttribute('aria-disabled') !== 'true')
    expect(enabled, 'no Save became enabled after typing').toBeTruthy()
    await userEvent.click(enabled!)
    await waitFor(() =>
      expect(patchConfig).toHaveBeenCalledWith('mobile.ntfy_topic_url', 'https://ntfy.example/mine'),
    )
  })

  it('states the ids-only promise where the user chooses the transport', async () => {
    // The one place a user decides to route approvals through a third party is the one place
    // the guarantee has to be legible. A promise made only in a docstring is not a promise
    // the user can read.
    config.mockResolvedValue(cfgWith({ push_backend: 'webpush', ntfy_topic_url: '' }))
    render(<CompanionPanel />)
    await screen.findByRole('button', { name: 'Push backend: ntfy' })
    expect(screen.getByText(/ids only/i).textContent).toMatch(/never the tool/i)
  })

  it('renders defaults rather than crashing when the config has no `mobile` section', async () => {
    // An older config.json predates the section entirely.
    config.mockResolvedValue({ companion: { discovery_enabled: false, instance_name: '' } })
    render(<CompanionPanel />)
    const webpush = await screen.findByRole('button', { name: 'Push backend: Web push' })
    expect(webpush.getAttribute('aria-pressed')).toBe('true')
  })
})
