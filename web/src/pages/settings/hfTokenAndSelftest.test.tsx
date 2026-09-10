import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { ModelsPanel } from './ModelsPanel'

// ── HF token cascade surface + per-model selftest (LMMV-4, LOCAL-MODEL-MANAGER-V2 §5/§6) ──
//
// Tested at the level a user meets it:
//  · the token section shows each source's MASKED preview + HuggingFace's whoami verdict — the
//    raw value never appears (Success Criterion 4);
//  · a gated model with no valid token carries a "needs token" pre-warn BEFORE Download;
//  · a downloaded local model's Test button runs a real inference and shows the TYPED result.

const hfTokenStatus = vi.fn()
const modelsAvailable = vi.fn()
const setHfToken = vi.fn((_token: string) => Promise.resolve({ sources: [] }))
const localModelHealth = vi.fn()
const localModelSelftest = vi.fn()

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      hfTokenStatus: () => hfTokenStatus(),
      setHfToken: (t: string) => setHfToken(t),
      clearHfToken: vi.fn(() => Promise.resolve({ sources: [] })),
      modelsAvailable: () => modelsAvailable(),
      modelsActive: () => Promise.resolve({}),
      modelsHealth: () => Promise.resolve({ providers: [] }),
      judgeBench: () => Promise.reject(new actual.ApiError('none', 404, 'judge_bench_absent')),
      modelDownloadCleanupCandidates: () => Promise.resolve({ candidates: [], total_bytes: 0 }),
      modelsLoaded: () => Promise.resolve({
        loaded: [], providers: [],
        pressure: { total_mb: 0, used_mb: 0, available_mb: 0, used_pct: 0, warn_pct: 85, warn: false, source: 'unavailable' },
      }),
      personalclawConfig: () => Promise.resolve({ agent: { prompt_cache_enabled: true } }),
      localModelHealth: (p: string) => localModelHealth(p),
      localModelSelftest: (p: string, m?: string) => localModelSelftest(p, m),
    },
  }
})
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))
vi.mock('../../app/reportingWrite', () => ({ reportingWrite: (_label: string, fn: () => Promise<unknown>) => fn().then(() => true) }))
vi.mock('../../lib/data', () => ({
  useQuery: (_k: string, fn: () => Promise<unknown>) => {
    const [data, setData] = useState<unknown>(null)
    const [error, setError] = useState<unknown>(null)
    useEffect(() => { fn().then(setData).catch(setError) }, [])
    return { data, error, refresh: () => {} }
  },
  invalidateKeys: () => {},
}))

beforeEach(() => {
  hfTokenStatus.mockReset()
  modelsAvailable.mockReset().mockResolvedValue([])
  setHfToken.mockClear()
  localModelHealth.mockReset().mockResolvedValue({ provider: 'diarization-pyannote', ok: true, message: 'ready', latency_ms: 5 })
  localModelSelftest.mockReset()
})

describe('the HuggingFace token section in the Models panel', () => {
  it('shows each source masked with its whoami verdict and marks the active one', async () => {
    hfTokenStatus.mockResolvedValue({
      sources: [
        { source: 'credential_store', present: true, valid: true, username: 'someuser', masked: 'hf_…abcd', active: true },
        { source: 'env', present: false, valid: false, username: '', masked: '', active: false },
        { source: 'hf_cli_file', present: false, valid: false, username: '', masked: '', active: false },
      ],
    })
    render(<ModelsPanel />)

    // The MASKED preview is shown; the raw token never is.
    expect(await screen.findByText('hf_…abcd')).toBeInTheDocument()
    expect(screen.getByText(/valid — someuser/i)).toBeInTheDocument()
    expect(screen.getByText(/^active$/i)).toBeInTheDocument()
    // The two absent sources read "not set".
    expect(screen.getAllByText(/not set/i).length).toBe(2)
  })

  it('saves a pasted token to the credential store', async () => {
    hfTokenStatus.mockResolvedValue({ sources: [
      { source: 'credential_store', present: false, valid: false, username: '', masked: '', active: false },
      { source: 'env', present: false, valid: false, username: '', masked: '', active: false },
      { source: 'hf_cli_file', present: false, valid: false, username: '', masked: '', active: false },
    ] })
    render(<ModelsPanel />)

    const input = await screen.findByLabelText('HuggingFace token')
    fireEvent.change(input, { target: { value: 'hf_pasted_secret_token' } })
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await waitFor(() => expect(setHfToken).toHaveBeenCalledWith('hf_pasted_secret_token'))
  })
})

describe('a gated model and the per-model Test button', () => {
  const gatedModel = {
    id: 'pyannote/speaker-diarization-3.1', name: 'pyannote/speaker-diarization-3.1',
    provider: 'diarization-pyannote', provider_type: 'diarization-pyannote',
    capabilities: ['diarization'], downloaded: true, gated: true, token_ready: false,
  }

  beforeEach(() => {
    hfTokenStatus.mockResolvedValue({ sources: [
      { source: 'credential_store', present: false, valid: false, username: '', masked: '', active: false },
      { source: 'env', present: false, valid: false, username: '', masked: '', active: false },
      { source: 'hf_cli_file', present: false, valid: false, username: '', masked: '', active: false },
    ] })
    modelsAvailable.mockResolvedValue([{ name: 'diarization-pyannote', models: [gatedModel] }])
  })

  it('pre-warns "needs token" and runs a real selftest that shows the typed result', async () => {
    localModelSelftest.mockResolvedValue({
      provider: 'diarization-pyannote',
      capabilities: { diarization: { ok: true, duration_ms: 12, detail: 'ran the pipeline (0 turn(s))', reason: '' } },
    })
    render(<ModelsPanel />)

    // Expand the Speaker-diarization card so its model rows render.
    fireEvent.click(await screen.findByRole('button', { name: /speaker diarization/i }))

    // The gated pre-warn chip (server-computed token_ready:false).
    expect(await screen.findByText(/needs token/i)).toBeInTheDocument()

    // Click Test → a real inference runs and its TYPED per-capability result renders inline.
    fireEvent.click(screen.getByRole('button', { name: /^test$/i }))
    expect(await screen.findByText(/diarization: ran the pipeline/i)).toBeInTheDocument()
    expect(localModelSelftest).toHaveBeenCalledWith('diarization-pyannote', 'pyannote/speaker-diarization-3.1')
  })

  it('shows a typed failure reason when a selftest capability fails (contract break)', async () => {
    localModelSelftest.mockResolvedValue({
      provider: 'diarization-pyannote',
      capabilities: { diarization: { ok: false, duration_ms: 3, detail: 'boom', reason: 'selftest_error:AttributeError' } },
    })
    render(<ModelsPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /speaker diarization/i }))
    fireEvent.click(await screen.findByRole('button', { name: /^test$/i }))
    expect(await screen.findByText(/diarization: selftest_error:AttributeError/i)).toBeInTheDocument()
  })
})
