import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { ModelsPanel } from './ModelsPanel'

// ── The prompt-cache switch (PCS-5, config point 5 of five) ───────────────────
//
// PCS-4 shipped the cache marker UNCONDITIONALLY for the EXPLICIT adapter. This control is
// the only way a user can turn it off, so it is asserted at the level a user meets it:
//
//  · it RENDERS inside the Models panel a user actually opens — an allowlisted backend field
//    with no control is "backend truth, frontend silence", and no python test can see that.
//  · it has an ACCESSIBLE NAME, probed via getByRole rather than by reading the source.
//  · it READS `agent.prompt_cache_enabled` off the config payload, so an off switch means the
//    user turned it off — not that the panel never loaded the value.
//  · it PATCHes the allowlisted dotted path, which is what makes the round trip real.
//  · a FAILED read renders the failure instead of a switch at its fallback: a fabricated
//    "off" would read as "you disabled caching".

const patchConfig = vi.fn((_path: string, _value: unknown) => Promise.resolve({}))
const personalclawConfig = vi.fn()

vi.mock('../../lib/api', () => ({
  api: {
    personalclawConfig: () => personalclawConfig(),
    patchConfig: (path: string, value: unknown) => patchConfig(path, value),
    modelsAvailable: () => Promise.resolve([]),
    modelsActive: () => Promise.resolve({}),
    modelsHealth: () => Promise.resolve({ providers: [] }),
    // ES-4: the panel reads the judge benchmark's tier recommendations on mount to offer
    // the one-click rebind. 404 (no benchmark yet) is the ordinary case, so a reject here
    // is what the panel really sees on a fresh install.
    judgeBench: () => Promise.reject(new Error('judge_bench_absent')),
    modelDownloadCleanupCandidates: () => Promise.resolve({ candidates: [], reclaimable_bytes: 0 }),
    // The panel's loaded-models section (LMMV-5) fetches on mount too; an unmocked call
    // here would make this test fail for a reason that has nothing to do with caching.
    modelsLoaded: () => Promise.resolve({
      loaded: [],
      providers: [],
      pressure: { total_mb: 0, used_mb: 0, available_mb: 0, used_pct: 0, warn_pct: 85, warn: false, source: 'unavailable' },
    }),
  },
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))
vi.mock('../../lib/data', () => ({
  useQuery: (_k: string, fn: () => Promise<unknown>) => {
    const [data, setData] = useState<unknown>(null)
    const [error, setError] = useState<unknown>(null)
    useEffect(() => { fn().then(setData).catch(setError) }, [])
    return { data, error, refresh: () => {} }
  },
  invalidateKeys: () => {},
}))

describe('the prompt-cache switch in the Models panel', () => {
  beforeEach(() => {
    patchConfig.mockClear()
    personalclawConfig.mockResolvedValue({ agent: { prompt_cache_enabled: true } })
  })

  it('renders in the Models panel with an accessible name', async () => {
    render(<ModelsPanel />)
    const sw = await waitFor(() => screen.getByRole('switch', { name: /prompt caching/i }))
    expect(sw).toBeTruthy()
  })

  it('reads the saved value rather than a fallback', async () => {
    personalclawConfig.mockResolvedValue({ agent: { prompt_cache_enabled: false } })
    render(<ModelsPanel />)
    const sw = await waitFor(() => screen.getByRole('switch', { name: /prompt caching/i }))
    expect(sw.getAttribute('aria-checked')).toBe('false')
  })

  it('PATCHes the allowlisted dotted path when flipped', async () => {
    render(<ModelsPanel />)
    const sw = await waitFor(() => screen.getByRole('switch', { name: /prompt caching/i }))
    expect(sw.getAttribute('aria-checked')).toBe('true')
    fireEvent.click(sw)
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('agent.prompt_cache_enabled', false))
  })

  it('a failed config read renders the failure, not a switch at its fallback', async () => {
    personalclawConfig.mockRejectedValue(new Error('boom'))
    render(<ModelsPanel />)
    await waitFor(() => expect(screen.queryAllByText(/prompt-cache setting/i).length).toBeGreaterThan(0))
    expect(screen.queryByRole('switch', { name: /prompt caching/i })).toBeNull()
  })
})
