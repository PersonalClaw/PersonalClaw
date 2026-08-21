import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { OnThisMachine } from './OnThisMachine'
import type { LoadedModel, MemoryPressure } from '../../../lib/api'

// ── The dashboard's "On this machine" band (LMMV-5, LOCAL-MODEL-MANAGER-V2 §7) ─
//
// The dashboard half of the residency surface. Two things are worth pinning here, because
// both are failure modes this codebase has shipped before:
//
//  · a FAILED fetch must not render as an empty machine. `SlotEmptyState` with "no models
//    loaded" on a dead gateway is a confident false statement about the user's RAM.
//  · every per-row Unload carries the ROW'S SUBJECT in its accessible name — five rows of
//    "Unload" with nothing to choose between them is the exact WCAG 4.1.2 shape the widget
//    kit's RowAction doc warns about.

const modelsLoaded = vi.fn()

vi.mock('../../../lib/api', () => ({
  api: {
    modelsLoaded: () => modelsLoaded(),
    unloadModelProvider: vi.fn(() => Promise.resolve({ ok: true, freed: true })),
  },
}))
vi.mock('../../../app/appSdk', () => ({ notify: vi.fn() }))
vi.mock('../../../lib/data', () => ({
  useQuery: (_k: string, fn: () => Promise<unknown>) => {
    const [data, setData] = useState<unknown>(null)
    const [error, setError] = useState<unknown>(null)
    useEffect(() => { fn().then(setData).catch(setError) }, [])
    return { data, error, refresh: () => {} }
  },
  invalidateKeys: () => {},
}))

const PRESSURE: MemoryPressure = {
  total_mb: 16384, used_mb: 8192, available_mb: 8192, used_pct: 50,
  warn_pct: 85, warn: false, source: 'meminfo',
}

const RESIDENT: LoadedModel[] = [
  { provider: 'sentence-transformers', model: 'all-MiniLM-L6-v2', kind: 'sidecar', rss_mb: 812, is_active: false, generation: 1 },
]

describe('the dashboard On this machine band', () => {
  beforeEach(() => {
    modelsLoaded.mockResolvedValue({ loaded: RESIDENT, providers: [], pressure: PRESSURE })
  })

  it('renders the named memory meter and the resident row', async () => {
    render(<OnThisMachine />)
    const bar = await waitFor(() => screen.getByRole('progressbar', { name: /system memory in use/i }))
    expect(bar.getAttribute('aria-valuenow')).toBe('50')
    expect(screen.getByText('all-MiniLM-L6-v2')).toBeTruthy()
  })

  it('names each Unload for the model it acts on', async () => {
    render(<OnThisMachine />)
    await waitFor(() => expect(screen.getByRole('button', { name: /unload: all-minilm-l6-v2/i })).toBeTruthy())
  })

  it('a failed fetch says it could not read, instead of claiming nothing is loaded', async () => {
    modelsLoaded.mockRejectedValue(new Error('boom'))
    render(<OnThisMachine />)
    await waitFor(() => expect(screen.getByText(/couldn’t read what’s loaded/i)).toBeTruthy())
    expect(screen.queryByText(/no models are loaded/i)).toBeNull()
  })

  it('an empty machine says so', async () => {
    modelsLoaded.mockResolvedValue({ loaded: [], providers: [], pressure: PRESSURE })
    render(<OnThisMachine />)
    await waitFor(() => expect(screen.getByText(/no models are loaded/i)).toBeTruthy())
  })
})
