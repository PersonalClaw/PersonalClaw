/** Every model size the Models page states is in one unit system: binary, labelled MiB and GiB.
 *
 *  The download offer, the chat notice and the local-model rows say "138 MiB", because a catalog's
 *  `size_mb` is MiB. Around them the same kind of number was labelled the decimal way: the refusal
 *  under the offer said "138.1 MB" (fixed server-side, `local_models/fit.size_text`), a fit chip
 *  with no server reason fell back to "needs about 4200 MB", the leftover partial downloads were
 *  offered back as "Reclaim 1.5 GB", the memory reserve they are all weighed against was entered
 *  in "GB", and a loaded model read "812 MB" under a memory bar in "GB", all of them binary numbers.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { AvailableModel, LoadedModel, MemoryPressure } from '../../lib/api'
import { occupantDetail, pressureDetail } from '../../lib/residency'
import { fitDescription } from './modelFit'
import { mib, modelBytes } from '../chat/bundledModelDownload'

const GiB = 1024 ** 3
const MiB = 1024 ** 2

vi.mock('../../lib/api', async (orig) => {
  const actual = await orig<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      modelsAvailable: () => Promise.resolve([]),
      activeChains: () => Promise.resolve({}),
      activeChain: () => Promise.resolve({ value: [], revision: '' }),
      modelDownloads: () => Promise.resolve([]),
      modelsHealth: () => Promise.resolve({ providers: [] }),
      embeddingReindexJobs: () => Promise.resolve({ jobs: [], active: null }),
      judgeBench: () => Promise.resolve({ ran: false }),
      modelDownloadCleanupCandidates: () => Promise.resolve({ candidates: [], total_bytes: 1.5 * GiB }),
      hfTokenStatus: () => Promise.resolve({ sources: [] }),
      modelsLoaded: () => Promise.resolve({
        loaded: [], providers: [],
        pressure: { total_mb: 0, used_mb: 0, available_mb: 0, used_pct: 0, warn_pct: 85, warn: false, source: 'unavailable' },
      }),
      personalclawConfig: () => Promise.resolve({ agent: { prompt_cache_enabled: true }, local_models: { memory_reserve_gb: 3 } }),
    },
  }
})
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))

import { ModelsPanel } from './ModelsPanel'

describe('one unit system for model sizes', () => {
  it('a model-sized byte count is MiB below a GiB and GiB from there', () => {
    expect(mib(144811072)).toBe('138 MiB')
    expect(modelBytes(300 * MiB)).toBe('300 MiB')
    expect(modelBytes(1.5 * GiB)).toBe('1.5 GiB')
  })

  it('a fit chip with no server reason states its need in MiB', () => {
    const row = { id: 'm', name: 'm', provider: 'ollama', capabilities: [], fit: 'red', fit_need_mb: 4200 } as unknown as AvailableModel
    expect(fitDescription(row)).toBe("Won't fit — needs about 4200 MiB on this device")
  })

  it('the Models page offers leftovers back, and takes its memory reserve, in the same units', async () => {
    render(<ModelsPanel />)
    expect(await screen.findByRole('button', { name: /Reclaim 1\.5 GiB/ })).toBeInTheDocument()
    expect(screen.getByText('Memory reserve (GiB)')).toBeInTheDocument()
  })

  it('a loaded model and the memory it sits in are stated in the same units', () => {
    const pressure: MemoryPressure = {
      total_mb: 49152, used_mb: 29491, available_mb: 19661, used_pct: 60, warn_pct: 85, warn: false, source: 'vm_stat',
    }
    expect(pressureDetail(pressure)).toBe('28.8 GiB of 48.0 GiB in use · 60%')
    const row: LoadedModel = { provider: 'ollama', model: 'm', kind: 'sidecar', rss_mb: 812, is_active: true }
    expect(occupantDetail(row)).toBe('sidecar · 812 MiB')
  })
})
