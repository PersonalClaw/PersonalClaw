import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { AppsPanel } from './AppsPanel'

// ── The registry-source switch (ET-4, config point 5 of five) ──────────────────
//
// `apps.registry_source_enabled` gates whether the curated app registry is seeded into the
// Store's source list. Points 1-4 (dataclass/_meta, load, to_dict, the PATCH allowlist) are
// covered by python rails; this is the one no python test can see — an allowlisted field with
// no control is "backend truth, frontend silence".
//
//  · it RENDERS in the panel a user actually opens, with an accessible name;
//  · it READS the saved value, so an off switch means the user turned it off — not that the
//    panel fabricated a default;
//  · it PATCHes the allowlisted dotted path, which is what makes the round trip real;
//  · it renders INDEPENDENTLY of the installed-apps list, because it is not per-app config —
//    it used to sit behind that list's skeleton, which blanks a config control whenever an
//    unrelated read is slow;
//  · a FAILED config read renders the failure instead of a switch at its fallback.

const patchConfig = vi.fn((_path: string, _value: unknown) => Promise.resolve({}))
const personalclawConfig = vi.fn()
const apps = vi.fn()

vi.mock('../../lib/api', () => ({
  api: {
    personalclawConfig: () => personalclawConfig(),
    patchConfig: (path: string, value: unknown) => patchConfig(path, value),
    apps: () => apps(),
  },
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn(), launchChat: vi.fn() }))
vi.mock('../apps/appConfigForm', () => ({
  AppConfigFields: () => null,
  useAppConfig: () => ({ savedAt: 0, dirty: false, error: null, reload: () => {}, fields: [] }),
}))
vi.mock('../../lib/useCachedData', () => ({
  useCachedData: (_k: string, fn: () => Promise<unknown>) => {
    const [data, setData] = useState<unknown>(null)
    const [error, setError] = useState<unknown>(null)
    useEffect(() => { fn().then(setData).catch(setError) }, [])
    return { data, error, refresh: () => {} }
  },
  invalidateCache: () => {},
}))

const NAME = /curated app registry/i

describe('the registry-source switch in Settings > Apps', () => {
  beforeEach(() => {
    patchConfig.mockClear()
    personalclawConfig.mockResolvedValue({ apps: { registry_source_enabled: true } })
    apps.mockResolvedValue([])
  })

  it('renders with an accessible name', async () => {
    render(<AppsPanel />)
    const sw = await waitFor(() => screen.getByRole('switch', { name: NAME }))
    expect(sw).toBeTruthy()
  })

  it('reads the saved value rather than a fallback', async () => {
    personalclawConfig.mockResolvedValue({ apps: { registry_source_enabled: false } })
    render(<AppsPanel />)
    const sw = await waitFor(() => screen.getByRole('switch', { name: NAME }))
    expect(sw.getAttribute('aria-checked')).toBe('false')
  })

  it('PATCHes the allowlisted dotted path when flipped', async () => {
    render(<AppsPanel />)
    const sw = await waitFor(() => screen.getByRole('switch', { name: NAME }))
    expect(sw.getAttribute('aria-checked')).toBe('true')
    fireEvent.click(sw)
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('apps.registry_source_enabled', false))
  })

  it('renders even while the installed-apps list is still loading', async () => {
    apps.mockReturnValue(new Promise(() => {}))  // never resolves
    render(<AppsPanel />)
    const sw = await waitFor(() => screen.getByRole('switch', { name: NAME }))
    expect(sw).toBeTruthy()
  })

  it('a failed config read renders the failure, not a switch at its fallback', async () => {
    personalclawConfig.mockRejectedValue(new Error('boom'))
    render(<AppsPanel />)
    await waitFor(() => expect(screen.queryAllByText(/app store settings/i).length).toBeGreaterThan(0))
    expect(screen.queryByRole('switch', { name: NAME })).toBeNull()
  })
})
