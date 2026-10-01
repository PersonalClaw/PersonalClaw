import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { ModelsPanel } from './ModelsPanel'

// ── HF token cascade surface + a local model's Test ──
//
// Tested at the level a user meets it:
//  · the token section shows each source's MASKED preview + HuggingFace's whoami verdict — the
//    raw value never appears (Success Criterion 4);
//  · a gated model with no valid token carries a "needs token" pre-warn BEFORE Download;
//  · a downloaded local model's Test makes one real call and shows the gateway's sentence.

const hfTokenStatus = vi.fn()
const modelsAvailable = vi.fn()
const setHfToken = vi.fn((_token: string) => Promise.resolve({ sources: [] }))
const modelTest = vi.fn()

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      hfTokenStatus: () => hfTokenStatus(),
      setHfToken: (t: string) => setHfToken(t),
      clearHfToken: vi.fn(() => Promise.resolve({ sources: [] })),
      modelsAvailable: () => modelsAvailable(),
      activeChains: () => Promise.resolve({}),
      modelsHealth: () => Promise.resolve({ providers: [] }),
      embeddingReindexJobs: () => Promise.resolve({ jobs: [], active: null }),
      judgeBench: () => Promise.resolve({ ran: false }),
      modelDownloadCleanupCandidates: () => Promise.resolve({ candidates: [], total_bytes: 0 }),
      // The panel reads the download list once, so a row can re-attach to a running download.
      modelDownloads: () => Promise.resolve([]),
      modelsLoaded: () => Promise.resolve({
        loaded: [], providers: [],
        pressure: { total_mb: 0, used_mb: 0, available_mb: 0, used_pct: 0, warn_pct: 85, warn: false, source: 'unavailable' },
      }),
      personalclawConfig: () => Promise.resolve({ agent: { prompt_cache_enabled: true } }),
      modelTest: (u: string, m: string) => modelTest(u, m),
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
  modelTest.mockReset()
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

  it('says the huggingface-cli sign-in is not read until the owner allows its folder', async () => {
    const note = 'PersonalClaw has not been allowed to read the Hugging Face folder other tools share. '
      + "Turn it on in Settings → Security → Outside PersonalClaw's home."
    hfTokenStatus.mockResolvedValue({ sources: [
      { source: 'credential_store', present: false, valid: false, username: '', masked: '', active: false },
      { source: 'env', present: false, valid: false, username: '', masked: '', active: false },
      { source: 'hf_cli_file', present: false, valid: false, username: '', masked: '', active: false, note },
    ] })
    render(<ModelsPanel />)
    expect(await screen.findByText(note)).toBeInTheDocument()
    expect(screen.getAllByText(/^not set$/i).length).toBe(2)
  })

  it('saves a pasted token to the credential store', async () => {
    hfTokenStatus.mockResolvedValue({ sources: [
      { source: 'credential_store', present: false, valid: false, username: '', masked: '', active: false },
      { source: 'env', present: false, valid: false, username: '', masked: '', active: false },
      { source: 'hf_cli_file', present: false, valid: false, username: '', masked: '', active: false },
    ] })
    render(<ModelsPanel />)

    const input = await screen.findByLabelText('HuggingFace token')
    fireEvent.change(input, { target: { value: 'fake-hf-token-pasted_secret_token' } })
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await waitFor(() => expect(setHfToken).toHaveBeenCalledWith('fake-hf-token-pasted_secret_token'))
  })
})

describe('a gated model and its Test', () => {
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

  const tested = (ok: boolean, detail: string, reason: string, ms: number) => ({
    use_case: 'diarization', model: 'diarization-pyannote:pyannote/speaker-diarization-3.1',
    ok, detail, reason, duration_ms: ms,
  })

  it('pre-warns "needs token" and runs a real Test that shows what it found', async () => {
    modelTest.mockResolvedValue(tested(true, 'Ran on a half-second test tone and found 0 speaker turns.', '', 12))
    render(<ModelsPanel />)

    // Expand the Speaker-diarization card so its model rows render.
    fireEvent.click(await screen.findByRole('button', { name: /speaker diarization/i }))

    // The gated pre-warn chip (server-computed token_ready:false).
    expect(await screen.findByText(/needs token/i)).toBeInTheDocument()

    // Click Test → one real call runs for this use case, and the gateway's sentence renders inline.
    fireEvent.click(screen.getByRole('button', { name: /^test pyannote/i }))
    expect(await screen.findByText(/Ran on a half-second test tone and found 0 speaker turns\. \(12 ms\)/)).toBeInTheDocument()
    expect(modelTest).toHaveBeenCalledWith('diarization', 'diarization-pyannote:pyannote/speaker-diarization-3.1')
  })

  it('shows a typed failure reason when the Test fails (contract break)', async () => {
    modelTest.mockResolvedValue(tested(false, 'boom', 'error:AttributeError', 3))
    render(<ModelsPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /speaker diarization/i }))
    fireEvent.click(await screen.findByRole('button', { name: /^test pyannote/i }))
    expect(await screen.findByText('boom (error:AttributeError, 3 ms)')).toBeInTheDocument()
  })

  it('shows a failure in the provider\u2019s own words, whole, with its typed reason after them', async () => {
    // 🔴 Before: the typed reason stood in for the sentence, so the next step a provider named
    // never showed, and the gateway cut the sentence at 200 characters on its way here anyway.
    const sentence = 'Speaker diarization needs the pyannote pipeline\u2019s terms accepted on Hugging Face. '
      + 'Accept them on the model\u2019s page, sign in with a token that can read it under HuggingFace '
      + 'token below, then run Test again. Details: 403 Client Error: Forbidden for url: '
      + 'https://huggingface.example/pyannote/speaker-diarization-3.1/resolve/main/config.yaml'
    expect(sentence.length).toBeGreaterThan(200)
    modelTest.mockResolvedValue(tested(false, sentence, 'error:SttError', 3))
    render(<ModelsPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /speaker diarization/i }))
    fireEvent.click(await screen.findByRole('button', { name: /^test pyannote/i }))
    expect(await screen.findByText(`${sentence} (error:SttError, 3 ms)`)).toBeInTheDocument()
  })
})

describe('an image or video provider that cannot generate right now', () => {
  const REASON = 'No Gemini API key is set on this instance, so it can\u2019t make images. Add it in '
    + 'Google Gemini API Key on this Google Gemini instance in Settings \u2192 Providers, or set GEMINI_API_KEY.'

  beforeEach(() => {
    hfTokenStatus.mockResolvedValue({ sources: [] })
  })

  it('stays in its row with the reason it gives, instead of vanishing', async () => {
    // 🔴 Before: /api/models/available left an unavailable provider out, and the panel flattened
    // its rows into models alone, so an instance whose key was missing was simply not there.
    modelsAvailable.mockResolvedValue([
      { name: 'Gemini', type: 'image_gen', models: [], error: REASON },
      { name: 'Veo elsewhere', type: 'video_gen', models: [], error: 'A video reason.' },
    ])
    render(<ModelsPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /image · generation/i }))

    const list = await screen.findByRole('list', { name: 'Image · Generation providers that can\u2019t be used right now' })
    expect(list).toHaveTextContent(`Gemini: ${REASON}`)
    expect(list).not.toHaveTextContent('A video reason.')
    // It is not "add a backend first": the backend is there, and the reason says what it lacks.
    expect(screen.queryByText(/No models with Image · Generation capability/)).toBeNull()
  })
})
