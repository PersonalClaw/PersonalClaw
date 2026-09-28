// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import { ApiError, type AvailableModel, type DownloadJob, type ProviderModels } from '../../lib/api'

// ── A Repair started from a Models-page chip shows its progress where it was pressed ─────────────
//
// A model whose weights came down incomplete carries a "truncated" chip and a Repair button on
// Settings → Models. Repair posted the download and re-read the page, and that was all the row
// did: no progress, no Cancel, and the job's warning (the free space could not be checked, #3715)
// drawn only on Settings → Providers, the one other place that job's row appears. These press
// Repair as a user does and read the row it was pressed on.

const modelsAvailable = vi.fn()
const activeChains = vi.fn()
const startModelDownload = vi.fn()
const cancelModelDownload = vi.fn()
const modelDownloads = vi.fn()
const notify = vi.fn()

vi.mock('../../lib/api', async (orig) => {
  const actual = await orig<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      modelsAvailable: () => modelsAvailable(),
      activeChains: () => activeChains(),
      activeChain: (u: string) => activeChains().then((c: Record<string, { value: string[]; revision: string }>) => c[u] ?? { value: [], revision: '' }),
      setActiveModel: () => Promise.resolve({ ok: true, revision: 'rev:x' }),
      startModelDownload: (p: string, m: string) => startModelDownload(p, m),
      cancelModelDownload: (id: string) => cancelModelDownload(id),
      modelDownloads: () => modelDownloads(),
      downloadStreamUrl: (id: string) => `/api/models/downloads/${id}/stream`,
      modelsHealth: () => Promise.resolve({ providers: [] }),
      embeddingReindexJobs: () => Promise.resolve({ jobs: [], active: null }),
      judgeBench: () => Promise.resolve({ ran: false }),
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
vi.mock('../../app/appSdk', () => ({ notify: (...a: unknown[]) => notify(...a) }))

import { ModelsPanel } from './ModelsPanel'
import { resetDataStore } from '../../lib/data'

const WARNING = 'Free space could not be checked, so the download was not verified to fit.'
const REFUSAL = 'Not enough free disk space for this download: it needs 138.1 MiB, and 50.0 MiB is free.'

/** The bundled model as `/api/models/available` lists it — on disk, with too few of its bytes. */
const SMOL = (integrity: string): AvailableModel => ({
  id: 'SmolLM2-135M-Instruct-Q8_0', name: 'SmolLM2-135M-Instruct-Q8_0', display_name: 'SmolLM2-135M-Instruct',
  capabilities: ['chat'], provider: 'bundled-chat', provider_type: 'bundled-chat',
  size_mb: 138.102539, license: 'Apache-2.0', downloaded: true, integrity,
})
const catalog = (integrity: string): ProviderModels[] => [
  { name: 'bundled-chat', type: 'bundled-chat', local: true, models: [SMOL(integrity)] },
]
const job = (state: DownloadJob['state'], extra: Partial<DownloadJob> = {}): DownloadJob => ({
  id: 'job-9', provider: 'bundled-chat', model: 'SmolLM2-135M-Instruct-Q8_0', kind: 'weights', state,
  progress: 0, speed_bps: 0, eta_s: 0, total_bytes: 144_811_072, downloaded_bytes: 0, error: '', reason: '', warning: '',
  ...extra,
})

/** jsdom has no EventSource; this one lets a test push the frames the runner would send. */
let emit: (type: string, j: DownloadJob) => void = () => {}
let opened: string[] = []
/** jsdom has no `scrollIntoView` either; this records the elements that asked to be shown. */
let revealed: Element[] = []
beforeEach(() => {
  resetDataStore()
  opened = []
  revealed = []
  ;(Element.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView = function (this: Element) { revealed.push(this) }
  const listeners = new Map<string, ((e: Event) => void)[]>()
  emit = (type, j) => { for (const fn of listeners.get(type) ?? []) fn({ data: JSON.stringify(j) } as unknown as MessageEvent) }
  ;(globalThis as unknown as { EventSource: unknown }).EventSource = class {
    constructor(url: string) { opened.push(url) }
    close() {}
    addEventListener(type: string, fn: (e: Event) => void) { listeners.set(type, [...(listeners.get(type) ?? []), fn]) }
    onerror: unknown = null
  }
  modelsAvailable.mockReset().mockResolvedValue(catalog('truncated'))
  activeChains.mockReset().mockResolvedValue({ chat: { value: [], revision: 'rev:' } })
  startModelDownload.mockReset().mockResolvedValue(job('queued', { warning: WARNING }))
  cancelModelDownload.mockReset().mockResolvedValue(undefined)
  modelDownloads.mockReset().mockResolvedValue([])
  notify.mockReset()
})
afterEach(() => {
  delete (globalThis as unknown as { EventSource?: unknown }).EventSource
  delete (Element.prototype as unknown as { scrollIntoView?: unknown }).scrollIntoView
})

/** Open the Chat card and press the truncated model's Repair. */
async function pressRepair() {
  render(<ModelsPanel />)
  fireEvent.click(await screen.findByRole('button', { name: /^Chat/, expanded: false }))
  fireEvent.click(await screen.findByRole('button', { name: /Repair/ }))
  await waitFor(() => expect(startModelDownload).toHaveBeenCalledWith('bundled-chat', 'SmolLM2-135M-Instruct-Q8_0'))
}

describe('a Repair started from a Models-page chip shows its progress where it was pressed', () => {
  it('draws the running download under the row, with its Cancel and its warning', async () => {
    await pressRepair()
    // `queued` is not terminal: the progress stream is opened, and frames move the row.
    await waitFor(() => expect(opened).toEqual(['/api/models/downloads/job-9/stream']))
    emit('progress', job('running', { downloaded_bytes: 72_405_536, progress: 0.5, eta_s: 95, warning: WARNING }))
    const row = await screen.findByTestId('model-repair')
    expect(row.textContent).toMatch(/Downloading SmolLM2-135M-Instruct — 69 MiB of 138 MiB, about 2 min left/)
    expect(row.textContent).toContain(WARNING)
    expect(within(row).getByRole('button', { name: 'Cancel the model download' })).toBeTruthy()
    // It opens under the chip that was pressed, so it asks to be shown: a Repair pressed at the
    // window's bottom edge would otherwise draw it below the fold.
    expect(revealed).toContain(row)
    // The chip says the repair is under way instead of offering it again.
    expect(screen.getByRole('button', { name: /repairing…/ })).toHaveProperty('disabled', true)
    // The page reads the download list once, for every row; the Repair reads none of its own.
    expect(modelDownloads).toHaveBeenCalledTimes(1)
  })

  it('lands: the page re-reads and the truncated chip clears', async () => {
    await pressRepair()
    await waitFor(() => expect(opened.length).toBe(1))
    modelsAvailable.mockResolvedValue(catalog(''))
    const reads = modelsAvailable.mock.calls.length
    emit('done', job('done', { downloaded_bytes: 144_811_072, progress: 1 }))
    await waitFor(() => expect(modelsAvailable.mock.calls.length).toBeGreaterThan(reads))
    await waitFor(() => expect(screen.queryByText('truncated')).toBeNull())
    expect(screen.queryByTestId('model-repair')).toBeNull()
  })

  it('Cancel stops it through the API, not only on screen', async () => {
    await pressRepair()
    await waitFor(() => expect(opened.length).toBe(1))
    emit('progress', job('running', { downloaded_bytes: 1 << 20, progress: 0.01 }))
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel the model download' }))
    await waitFor(() => expect(cancelModelDownload).toHaveBeenCalledWith('job-9'))
  })

  it('a refused Repair says why in the row it was pressed on', async () => {
    startModelDownload.mockRejectedValue(new ApiError(REFUSAL, 507, 'insufficient_disk_space'))
    await pressRepair()
    const row = await screen.findByTestId('model-repair')
    expect(within(row).getByRole('alert').textContent).toContain(REFUSAL)
    // The Repair is offered again for a second try.
    expect(screen.getByRole('button', { name: /Repair/ })).toHaveProperty('disabled', false)
  })
})
