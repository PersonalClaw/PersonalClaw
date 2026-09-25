// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import type { AvailableModel, DownloadJob, ProviderModels } from '../../lib/api'

// ── A chosen model that is not on this machine is downloaded where it was chosen ─────────────────
//
// The owner: "For the downloadable models, if user selects a model from settings/models page it
// shows 'not downloaded' if the model isn't yet downloaded. But it should just give the user
// download option right there and then allowing them to just download the model instead."
//
// It was a warning chip, "not downloaded", whose tooltip sent the user to Providers to find the
// download. These drive the page as a user does: choose the model, see what it costs, download it
// through the ONE download machine (`useModelDownloads`: the job, its progress stream, cancel), and
// find the choice in effect when the download lands — with nothing to choose again.

const modelsAvailable = vi.fn()
const modelsActive = vi.fn()
const setActiveModel = vi.fn()
const startModelDownload = vi.fn()
const cancelModelDownload = vi.fn()
const modelDownloads = vi.fn()

vi.mock('../../lib/api', async (orig) => {
  const actual = await orig<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      modelsAvailable: () => modelsAvailable(),
      modelsActive: () => modelsActive(),
      setActiveModel: (u: string, m: string[]) => setActiveModel(u, m),
      startModelDownload: (p: string, m: string) => startModelDownload(p, m),
      cancelModelDownload: (id: string) => cancelModelDownload(id),
      modelDownloads: () => modelDownloads(),
      downloadStreamUrl: (id: string) => `/api/models/downloads/${id}/stream`,
      modelsHealth: () => Promise.resolve({ providers: [] }),
      judgeBench: () => Promise.reject(new actual.ApiError('none', 404, 'judge_bench_absent')),
      modelDownloadCleanupCandidates: () => Promise.resolve({ candidates: [], total_bytes: 0 }),
      hfTokenStatus: () => Promise.resolve({ sources: [] }),
      modelsLoaded: () => Promise.resolve({
        loaded: [], providers: [],
        pressure: { total_mb: 0, used_mb: 0, available_mb: 0, used_pct: 0, warn_pct: 85, warn: false, source: 'unavailable' },
      }),
      personalclawConfig: () => Promise.resolve({ agent: { prompt_cache_enabled: true }, local_models: {} }),
    },
  }
})
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))

import { ModelsPanel } from './ModelsPanel'
import { resetDataStore } from '../../lib/data'

const REF = 'bundled-chat:SmolLM2-135M-Instruct-Q8_0'
/** The bundled model as `/api/models/available` lists it: a file id, a name, a size in MiB, a licence. */
const SMOL = (downloaded: boolean): AvailableModel => ({
  id: 'SmolLM2-135M-Instruct-Q8_0', name: 'SmolLM2-135M-Instruct-Q8_0', display_name: 'SmolLM2-135M-Instruct',
  capabilities: ['chat'], provider: 'bundled-chat', provider_type: 'bundled-chat',
  size_mb: 138.102539, license: 'Apache-2.0', downloaded,
})
const catalog = (downloaded: boolean): ProviderModels[] => [
  { name: 'bundled-chat', type: 'bundled-chat', local: true, models: [SMOL(downloaded)] },
]
const job = (state: DownloadJob['state'], extra: Partial<DownloadJob> = {}): DownloadJob => ({
  id: 'job-9', provider: 'bundled-chat', model: 'SmolLM2-135M-Instruct-Q8_0', kind: 'weights', state,
  progress: 0, speed_bps: 0, eta_s: 0, total_bytes: 144_811_072, downloaded_bytes: 0, error: '', reason: '',
  ...extra,
})

/** jsdom has no EventSource; this one lets a test push the frames the runner would send. */
let emit: (type: string, j: DownloadJob) => void = () => {}
let opened: string[] = []
beforeEach(() => {
  resetDataStore()
  opened = []
  const listeners = new Map<string, ((e: Event) => void)[]>()
  emit = (type, j) => { for (const fn of listeners.get(type) ?? []) fn({ data: JSON.stringify(j) } as unknown as MessageEvent) }
  ;(globalThis as unknown as { EventSource: unknown }).EventSource = class {
    constructor(url: string) { opened.push(url) }
    close() {}
    addEventListener(type: string, fn: (e: Event) => void) { listeners.set(type, [...(listeners.get(type) ?? []), fn]) }
    onerror: unknown = null
  }
  let bound: string[] = []
  modelsAvailable.mockReset().mockResolvedValue(catalog(false))
  modelsActive.mockReset().mockImplementation(() => Promise.resolve({ chat: bound }))
  setActiveModel.mockReset().mockImplementation((_u: string, m: string[]) => { bound = m; return Promise.resolve({ ok: true }) })
  startModelDownload.mockReset().mockResolvedValue(job('queued'))
  cancelModelDownload.mockReset().mockResolvedValue(undefined)
  modelDownloads.mockReset().mockResolvedValue([])
})
afterEach(() => { delete (globalThis as unknown as { EventSource?: unknown }).EventSource })

async function openChat() {
  render(<ModelsPanel />)
  const header = await screen.findByRole('button', { name: /^Chat/, expanded: false })
  fireEvent.click(header)
}

describe('a chosen model that is not on this machine offers its download right there', () => {
  it('choosing it states what the download costs and offers it — by the model’s name, not its file', async () => {
    await openChat()
    fireEvent.click(await screen.findByRole('button', { name: /SmolLM2-135M-Instruct/ }))
    await waitFor(() => expect(setActiveModel).toHaveBeenCalledWith('chat', [REF]))
    const offer = await screen.findByTestId('inline-model-download')
    expect(offer.textContent).toContain('SmolLM2-135M-Instruct is not on this machine yet — 138 MiB · Apache-2.0.')
    expect(within(offer).getByRole('button', { name: /Download 138 MiB/ })).toBeTruthy()
    // Nothing is fetched until the user asks: the size is stated, and the click is the consent.
    expect(startModelDownload).not.toHaveBeenCalled()
  })

  it('downloads through the shared tracker, shows progress with a Cancel, and lands with the choice in effect', async () => {
    await openChat()
    fireEvent.click(await screen.findByRole('button', { name: /SmolLM2-135M-Instruct/ }))
    const offer = await screen.findByTestId('inline-model-download')
    fireEvent.click(within(offer).getByRole('button', { name: /Download 138 MiB/ }))
    await waitFor(() => expect(startModelDownload).toHaveBeenCalledWith('bundled-chat', 'SmolLM2-135M-Instruct-Q8_0'))
    // `queued` is not terminal: the progress stream is opened, and frames move the row.
    await waitFor(() => expect(opened).toEqual(['/api/models/downloads/job-9/stream']))
    emit('progress', job('running', { downloaded_bytes: 72_405_536, progress: 0.5, eta_s: 95 }))
    expect(await screen.findByText(/Downloading SmolLM2-135M-Instruct — 69 MiB of 138 MiB, about 2 min left/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Cancel the model download' })).toBeTruthy()

    // It lands. The page re-reads, the model is on disk, and the binding written when it was chosen
    // is the one in effect — the user chose once.
    modelsAvailable.mockResolvedValue(catalog(true))
    const reads = modelsAvailable.mock.calls.length
    emit('done', job('done', { downloaded_bytes: 144_811_072, progress: 1 }))
    await waitFor(() => expect(modelsAvailable.mock.calls.length).toBeGreaterThan(reads))
    await waitFor(() => expect(screen.queryByTestId('inline-model-download')).toBeNull())
    expect(setActiveModel).toHaveBeenCalledTimes(1)
  })

  it('Cancel stops it through the API, not only on screen', async () => {
    await openChat()
    fireEvent.click(await screen.findByRole('button', { name: /SmolLM2-135M-Instruct/ }))
    fireEvent.click(within(await screen.findByTestId('inline-model-download')).getByRole('button', { name: /Download 138 MiB/ }))
    await waitFor(() => expect(opened.length).toBe(1))
    emit('progress', job('running', { downloaded_bytes: 1 << 20, progress: 0.01 }))
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel the model download' }))
    await waitFor(() => expect(cancelModelDownload).toHaveBeenCalledWith('job-9'))
  })

  it('a reload re-attaches to a download already running', async () => {
    // Bound before the reload, still downloading: the row finds the job instead of offering it again.
    setActiveModel.mockClear()
    modelsActive.mockResolvedValue({ chat: [REF] })
    modelDownloads.mockResolvedValue([job('running', { downloaded_bytes: 72_405_536, progress: 0.5 })])
    await openChat()
    expect(await screen.findByText(/Downloading SmolLM2-135M-Instruct — 69 MiB of 138 MiB/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Download 138 MiB/ })).toBeNull()
  })

  it('an Ollama model the provider can pull is offered the same way', async () => {
    // A searchable provider lists only what it already has, so a chosen model it has not pulled
    // appears as the bound-but-missing row. Its provider is in the local-model registry, so the
    // same Download runs the provider's own pull — no size is known before it starts, and none is
    // invented.
    modelsAvailable.mockResolvedValue([{ name: 'ollama', type: 'ollama', local: true, searchable: true, models: [] }])
    modelsActive.mockResolvedValue({ chat: ['ollama:llama3:8b'] })
    startModelDownload.mockResolvedValue({ ...job('queued'), provider: 'ollama', model: 'llama3:8b', total_bytes: 0 })
    await openChat()
    const offer = await screen.findByTestId('inline-model-download')
    expect(offer.textContent).toContain('llama3:8b is not on this machine yet.')
    fireEvent.click(within(offer).getByRole('button', { name: /^Download$/ }))
    await waitFor(() => expect(startModelDownload).toHaveBeenCalledWith('ollama', 'llama3:8b'))
  })

  it('a hosted model is never offered a download it cannot have', async () => {
    // The control: only a provider the local-model registry lists can fetch a model.
    modelsAvailable.mockResolvedValue([{ name: 'openai', type: 'openai', models: [] }])
    modelsActive.mockResolvedValue({ chat: ['openai:gpt-9-missing'] })
    await openChat()
    // Listed twice — the chain editor and its row — and offered a download in neither.
    expect((await screen.findAllByText('gpt-9-missing')).length).toBeGreaterThan(0)
    expect(screen.queryByTestId('inline-model-download')).toBeNull()
  })
})
