import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { ModelsPanel } from './ModelsPanel'
import { occupantDetail, pressureDetail, pressureTone, reclaimableCount, sortOccupants } from '../../lib/residency'
import type { LoadedModel, MemoryPressure } from '../../lib/api'

// ── The loaded-models section (LMMV-5, LOCAL-MODEL-MANAGER-V2 §7) ─────────────
//
// `GET /api/models/loaded` is a backend truth nobody could see before this section existed,
// so the tests are written at the level a user meets it:
//
//  · the memory bar is a NAMED progressbar with a real aria-valuenow — an unnamed bar
//    announces "progressbar, 63%" with no subject, which in a list of meters says nothing.
//  · a resident model that is no longer bound is REACHABLE and its Unload button carries the
//    model's name, so a screen-reader user listing controls can tell six Unloads apart.
//  · a FAILED fetch renders the failure, never an empty list — "nothing is loaded" and "the
//    gateway didn't answer" look identical on screen, and one of them lies about your memory.
//  · an unreadable host does NOT paint a warning colour on numbers nobody measured.

const modelsLoaded = vi.fn()

// 🪤 PARTIAL mock, via `importOriginal`: the REAL `ApiError`/`hasApiCode` are kept. The four eval
// panels branch on `hasApiCode(error, '<code>')`, so a factory that returned only `api` made the
// mocked module throw "No \"hasApiCode\" export is defined" from inside the render — and a fixture
// that rejected with a bare `Error` would carry no `.code`, so the branch under test would never
// fire and the test would pass by rendering the generic failure instead.
vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      modelsLoaded: () => modelsLoaded(),
      unloadModelProvider: vi.fn(() => Promise.resolve({ ok: true, freed: true })),
      personalclawConfig: () => Promise.resolve({ agent: { prompt_cache_enabled: true } }),
      patchConfig: vi.fn(() => Promise.resolve({})),
      modelsAvailable: () => Promise.resolve([]),
      modelsActive: () => Promise.resolve({}),
      modelsHealth: () => Promise.resolve({ providers: [] }),
      judgeBench: () => Promise.reject(new actual.ApiError('No judge benchmark has run yet. Run `personalclaw judge-bench` to produce one.', 404, 'judge_bench_absent')),
      modelDownloadCleanupCandidates: () => Promise.resolve({ candidates: [], total_bytes: 0 }),
    },
  }
})
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

const PRESSURE: MemoryPressure = {
  total_mb: 16384, used_mb: 12288, available_mb: 4096, used_pct: 75,
  warn_pct: 85, warn: false, source: 'vm_stat',
}

const RESIDENT: LoadedModel[] = [
  { provider: 'sentence-transformers', model: 'all-MiniLM-L6-v2', kind: 'sidecar', rss_mb: 812, is_active: false, generation: 2, pid: 4242 },
  { provider: 'faster-whisper', model: 'large-v3', kind: 'in-process', rss_mb: null, is_active: true },
]

describe('the loaded-models section in the Models panel', () => {
  beforeEach(() => {
    modelsLoaded.mockResolvedValue({ loaded: RESIDENT, providers: [], pressure: PRESSURE })
  })

  it('renders the memory bar as a NAMED progressbar carrying the measured level', async () => {
    render(<ModelsPanel />)
    const bar = await waitFor(() => screen.getByRole('progressbar', { name: /system memory in use/i }))
    expect(bar.getAttribute('aria-valuenow')).toBe('75')
    expect(bar.getAttribute('aria-valuemax')).toBe('100')
  })

  it('lists every resident model with an Unload button named for that model', async () => {
    render(<ModelsPanel />)
    await waitFor(() => screen.getByRole('button', { name: /unload all-minilm-l6-v2/i }))
    expect(screen.getByRole('button', { name: /unload large-v3/i })).toBeTruthy()
  })

  it('says how many resident models are no longer bound — the reclaimable ones', async () => {
    render(<ModelsPanel />)
    await waitFor(() => expect(screen.getByText(/1 resident model no longer bound/i)).toBeTruthy())
  })

  it('a failed fetch renders the failure, not an empty machine', async () => {
    modelsLoaded.mockRejectedValue(new Error('gateway down'))
    render(<ModelsPanel />)
    await waitFor(() => expect(screen.queryAllByText(/loaded models/i).length).toBeGreaterThan(0))
    expect(screen.queryByRole('progressbar', { name: /system memory/i })).toBeNull()
  })

  it('says a warming provider is LOADING rather than leaving the payload unread', async () => {
    // ensure_ready() reports a state instead of a bare boolean precisely so a provider paging
    // a model in from disk does not read as hung. If nothing renders it, the backend knows and
    // the screen stays silent — the exact "backend truth, frontend silence" shape.
    modelsLoaded.mockResolvedValue({
      loaded: [],
      providers: [
        { provider: 'faster-whisper', display_name: 'Faster Whisper', ok: true, state: 'loading', kind: 'in-process', sidecar: null },
        { provider: 'piper-tts', display_name: 'Piper TTS', ok: false, state: 'unavailable', kind: 'in-process', sidecar: null },
        { provider: 'ollama', display_name: 'Ollama', ok: true, state: 'ready', kind: 'in-process', sidecar: null },
      ],
      pressure: PRESSURE,
    })
    render(<ModelsPanel />)
    await waitFor(() => expect(screen.getByText(/faster whisper: loading a model now/i)).toBeTruthy())
    expect(screen.getByText(/piper tts: unavailable on this machine/i)).toBeTruthy()
    // A ready provider is not worth a line — the section is about what needs attention.
    expect(screen.queryByText(/ollama/i)).toBeNull()
  })

  it('an empty machine says so rather than rendering nothing at all', async () => {
    modelsLoaded.mockResolvedValue({ loaded: [], providers: [], pressure: PRESSURE })
    render(<ModelsPanel />)
    await waitFor(() => expect(screen.getByText(/no models are loaded right now/i)).toBeTruthy())
  })
})

describe('the shared residency derivations', () => {
  it('orders reclaimable models first, then the heaviest', () => {
    const rows = sortOccupants([
      { provider: 'a', model: 'bound-big', kind: 'sidecar', rss_mb: 4000, is_active: true },
      { provider: 'b', model: 'free-small', kind: 'sidecar', rss_mb: 100, is_active: false },
      { provider: 'c', model: 'free-big', kind: 'sidecar', rss_mb: 900, is_active: false },
    ])
    expect(rows.map((r) => r.model)).toEqual(['free-big', 'free-small', 'bound-big'])
  })

  it('sorts an unknown RSS last instead of treating it as zero MB', () => {
    const rows = sortOccupants([
      { provider: 'a', model: 'unknown', kind: 'in-process', rss_mb: null, is_active: false },
      { provider: 'b', model: 'known', kind: 'sidecar', rss_mb: 5, is_active: false },
    ])
    expect(rows.map((r) => r.model)).toEqual(['known', 'unknown'])
  })

  it('counts the reclaimable rows', () => {
    expect(reclaimableCount(RESIDENT)).toBe(1)
  })

  it('never tones an unmeasured host as a warning', () => {
    const unknown: MemoryPressure = { ...PRESSURE, total_mb: 0, used_mb: 0, available_mb: 0, used_pct: 0, source: 'unavailable', warn: false }
    expect(pressureTone(unknown)).toBe('var(--color-outline-variant)')
    expect(pressureDetail(unknown)).toMatch(/unavailable/i)
  })

  it('escalates the tone as the level approaches the configured threshold', () => {
    expect(pressureTone({ ...PRESSURE, used_pct: 10 })).toBe('var(--color-primary)')
    expect(pressureTone({ ...PRESSURE, used_pct: 80 })).toBe('var(--color-warning)')
    expect(pressureTone({ ...PRESSURE, used_pct: 92, warn: true })).toBe('var(--color-danger)')
  })

  it("reports a sidecar's child-reported RSS and generation, and marks an unbound model", () => {
    expect(occupantDetail(RESIDENT[0])).toBe('sidecar · 812 MB · gen 2 · not bound')
    // An in-process model has no attributable RSS, so the detail says nothing about memory
    // rather than claiming 0 MB.
    expect(occupantDetail(RESIDENT[1])).toBe('in-process')
  })
})
