// @vitest-environment jsdom
import { StrictMode } from 'react'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import type { AvailableModel, DownloadJob, ProviderModels } from '../../lib/api'

// ── A Repair still running after a reload shows its progress again ─────────────────────────────
//
// A Repair pressed on a Settings → Models chip draws its progress under the row (#3732). Reload
// the page while it runs and the row was idle again: the truncated chip, a Repair button, and no
// progress, no Cancel, no word of how it ended, while the gateway kept downloading. Each row
// tracked only the download it had started itself. The page now reads the download list once and
// hands each row its model's job. These reload the page as a user does, with the job already
// running (or already ended) on the gateway, and read the row.

const modelsAvailable = vi.fn()
const activeChains = vi.fn()
const startModelDownload = vi.fn()
const cancelModelDownload = vi.fn()
const modelDownloads = vi.fn()

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
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))

import { ModelsPanel, latestDownloads } from './ModelsPanel'
import { resetDataStore } from '../../lib/data'

const WARNING = 'Free space could not be checked, so the download was not verified to fit.'

const MODEL = 'SmolLM2-135M-Instruct-Q8_0'
/** The bundled model as `/api/models/available` lists it: on disk, `truncated` or intact. */
const SMOL = (integrity: string): AvailableModel => ({
  id: MODEL, name: MODEL, display_name: 'SmolLM2-135M-Instruct',
  capabilities: ['chat'], provider: 'bundled-chat', provider_type: 'bundled-chat',
  size_mb: 138.102539, license: 'Apache-2.0', downloaded: true, integrity,
})
const catalog = (integrity: string): ProviderModels[] => [
  { name: 'bundled-chat', type: 'bundled-chat', local: true, models: [SMOL(integrity)] },
]
const job = (state: DownloadJob['state'], extra: Partial<DownloadJob> = {}): DownloadJob => ({
  id: 'job-9', provider: 'bundled-chat', model: MODEL, kind: 'weights', state,
  progress: 0, speed_bps: 0, eta_s: 0, total_bytes: 144_811_072, downloaded_bytes: 0, error: '', reason: '', warning: '',
  ...extra,
})

/** jsdom has no EventSource. Each one keeps its own listeners and knows whether it was closed, so
 *  a frame reaches only a stream still open — a closed one cannot pass for a live one. */
class FakeStream {
  static all: FakeStream[] = []
  closed = false
  listeners = new Map<string, ((e: Event) => void)[]>()
  onerror: unknown = null
  constructor(readonly url: string) { FakeStream.all.push(this) }
  close() { this.closed = true }
  addEventListener(type: string, fn: (e: Event) => void) { this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]) }
}
const open = () => FakeStream.all.filter((s) => !s.closed)
/** Push a frame down every stream still open, as the runner would. */
const emit = (type: string, j: DownloadJob) => {
  for (const s of open()) for (const fn of s.listeners.get(type) ?? []) fn({ data: JSON.stringify(j) } as unknown as MessageEvent)
}

beforeEach(() => {
  resetDataStore()
  FakeStream.all = []
  ;(globalThis as unknown as { EventSource: unknown }).EventSource = FakeStream
  ;(Element.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView = () => {}
  modelsAvailable.mockReset().mockResolvedValue(catalog('truncated'))
  activeChains.mockReset().mockResolvedValue({ chat: { value: [], revision: 'rev:' } })
  startModelDownload.mockReset().mockResolvedValue(job('queued'))
  cancelModelDownload.mockReset().mockResolvedValue(undefined)
  modelDownloads.mockReset().mockResolvedValue([])
})
afterEach(() => {
  delete (globalThis as unknown as { EventSource?: unknown }).EventSource
  delete (Element.prototype as unknown as { scrollIntoView?: unknown }).scrollIntoView
})

/** The page after a reload, with the Chat card opened. */
async function reloadAndOpenChat(tree = <ModelsPanel />) {
  render(tree)
  fireEvent.click(await screen.findByRole('button', { name: /^Chat/, expanded: false }))
}

describe('a Repair still running after a reload shows its progress again', () => {
  it('draws the running download under the row, with its Cancel and its warning', async () => {
    modelDownloads.mockResolvedValue([job('running', { downloaded_bytes: 72_405_536, progress: 0.5, eta_s: 95, warning: WARNING })])
    await reloadAndOpenChat()
    const row = await screen.findByTestId('model-repair')
    expect(row.textContent).toMatch(/Downloading SmolLM2-135M-Instruct — 69 MiB of 138 MiB, about 2 min left/)
    expect(row.textContent).toContain(WARNING)
    expect(within(row).getByRole('button', { name: 'Cancel the model download' })).toBeTruthy()
    // The chip says the repair is under way instead of offering a second one.
    expect(screen.getByRole('button', { name: /repairing…/ })).toHaveProperty('disabled', true)
    // It found the gateway's job; it started nothing.
    expect(startModelDownload).not.toHaveBeenCalled()
    expect(open().map((s) => s.url)).toEqual(['/api/models/downloads/job-9/stream'])
  })

  it('follows the job to the end: the page re-reads and the truncated chip clears', async () => {
    modelDownloads.mockResolvedValue([job('running', { downloaded_bytes: 1 << 20, progress: 0.01 })])
    await reloadAndOpenChat()
    await screen.findByTestId('model-repair')
    emit('progress', job('running', { downloaded_bytes: 108_000_000, progress: 0.75, eta_s: 30 }))
    expect((await screen.findByTestId('model-repair')).textContent).toMatch(/103 MiB of 138 MiB/)
    modelsAvailable.mockResolvedValue(catalog(''))
    const reads = modelsAvailable.mock.calls.length
    emit('done', job('done', { downloaded_bytes: 144_811_072, progress: 1 }))
    await waitFor(() => expect(modelsAvailable.mock.calls.length).toBeGreaterThan(reads))
    await waitFor(() => expect(screen.queryByText('truncated')).toBeNull())
    expect(screen.queryByTestId('model-repair')).toBeNull()
  })

  it('Cancel stops the job it found, through the API', async () => {
    modelDownloads.mockResolvedValue([job('running', { downloaded_bytes: 1 << 20, progress: 0.01 })])
    await reloadAndOpenChat()
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel the model download' }))
    await waitFor(() => expect(cancelModelDownload).toHaveBeenCalledWith('job-9'))
  })

  it('a Repair that failed before the reload says why, and is offered again', async () => {
    modelDownloads.mockResolvedValue([job('error', { error: 'The mirror answered 503.' })])
    await reloadAndOpenChat()
    const row = await screen.findByTestId('model-repair')
    expect(within(row).getByRole('alert').textContent).toContain('The mirror answered 503.')
    expect(screen.getByRole('button', { name: /Repair/ })).toHaveProperty('disabled', false)
  })

  it('a newer running Repair wins over an earlier one that failed', async () => {
    modelDownloads.mockResolvedValue([
      job('error', { id: 'job-8', error: 'The mirror answered 503.' }),
      job('running', { downloaded_bytes: 72_405_536, progress: 0.5, eta_s: 95 }),
    ])
    await reloadAndOpenChat()
    const row = await screen.findByTestId('model-repair')
    expect(row.textContent).toMatch(/69 MiB of 138 MiB/)
    expect(within(row).queryByRole('alert')).toBeNull()
  })

  it('finds it while the row no longer reads truncated, because the fetch is under way', async () => {
    // Mid-Repair, the unfinished fetch beside the short weights explains the shortfall, so the
    // gateway lists the model as on disk with no `truncated` chip and no Repair button. The job
    // is still the Repair's, and its progress still belongs under this row.
    modelsAvailable.mockResolvedValue(catalog(''))
    modelDownloads.mockResolvedValue([job('running', { downloaded_bytes: 72_405_536, progress: 0.5, eta_s: 95 })])
    await reloadAndOpenChat()
    const row = await screen.findByTestId('model-repair')
    expect(row.textContent).toMatch(/69 MiB of 138 MiB/)
    expect(within(row).getByRole('button', { name: 'Cancel the model download' })).toBeTruthy()
  })

  it('and when that Repair fails, the page re-reads so the row offers Repair again', async () => {
    // The page was read mid-fetch, when the row did not read truncated. After the failure the
    // weights are short again: the row says so, offers Repair, and says why the last one failed.
    modelsAvailable.mockResolvedValue(catalog(''))
    modelDownloads.mockResolvedValue([job('running', { downloaded_bytes: 1 << 20, progress: 0.01 })])
    await reloadAndOpenChat()
    await screen.findByTestId('model-repair')
    modelsAvailable.mockResolvedValue(catalog('truncated'))
    const reads = modelsAvailable.mock.calls.length
    emit('error', job('error', { error: 'The mirror answered 503.' }))
    await waitFor(() => expect(modelsAvailable.mock.calls.length).toBeGreaterThan(reads))
    expect(await screen.findByRole('button', { name: /^Repair$/ })).toHaveProperty('disabled', false)
    expect(within(screen.getByTestId('model-repair')).getByRole('alert').textContent).toContain('The mirror answered 503.')
  })

  it('an old failure is not drawn under a model that is whole now', async () => {
    // The control for the rule above: a Repair that failed and was later made good some other
    // way is history, not the row's state.
    modelsAvailable.mockResolvedValue(catalog(''))
    modelDownloads.mockResolvedValue([job('error', { error: 'The mirror answered 503.' })])
    await reloadAndOpenChat()
    await screen.findByText('SmolLM2-135M-Instruct')
    await waitFor(() => expect(modelDownloads).toHaveBeenCalledTimes(1))
    expect(screen.queryByTestId('model-repair')).toBeNull()
    expect(screen.queryByText('The mirror answered 503.')).toBeNull()
  })

  it('re-attaches under StrictMode too, with one stream left open', async () => {
    // The app mounts in StrictMode, which runs every effect twice in development: the first
    // stream is closed and the row must open it again rather than keep the dead one.
    modelDownloads.mockResolvedValue([job('running', { downloaded_bytes: 1 << 20, progress: 0.01 })])
    await reloadAndOpenChat(<StrictMode><ModelsPanel /></StrictMode>)
    await screen.findByTestId('model-repair')
    await waitFor(() => expect(open().map((s) => s.url)).toEqual(['/api/models/downloads/job-9/stream']))
    emit('progress', job('running', { downloaded_bytes: 108_000_000, progress: 0.75, eta_s: 30 }))
    expect((await screen.findByTestId('model-repair')).textContent).toMatch(/103 MiB of 138 MiB/)
  })

  it('a Repair pressed before the list arrives is not replaced by an older job from it', async () => {
    // The list is slow; the user presses Repair first. The list then answers with the earlier
    // Repair that failed, which is older than the one this row just started.
    let answer: (jobs: DownloadJob[]) => void = () => {}
    modelDownloads.mockReturnValue(new Promise<DownloadJob[]>((resolve) => { answer = resolve }))
    startModelDownload.mockResolvedValue(job('queued', { id: 'job-10' }))
    await reloadAndOpenChat()
    fireEvent.click(await screen.findByRole('button', { name: /^Repair$/ }))
    await waitFor(() => expect(open().map((s) => s.url)).toEqual(['/api/models/downloads/job-10/stream']))
    answer([job('error', { id: 'job-8', error: 'The mirror answered 503.' })])
    emit('progress', job('running', { id: 'job-10', downloaded_bytes: 72_405_536, progress: 0.5, eta_s: 95 }))
    const row = await screen.findByTestId('model-repair')
    await waitFor(() => expect(row.textContent).toMatch(/69 MiB of 138 MiB/))
    expect(screen.queryByText('The mirror answered 503.')).toBeNull()
  })

  it('reads the download list once for the page, not once per row', async () => {
    await reloadAndOpenChat()
    await screen.findByRole('button', { name: /Repair/ })
    expect(modelDownloads).toHaveBeenCalledTimes(1)
    // Nothing was running, so nothing is drawn and no stream is opened.
    expect(screen.queryByTestId('model-repair')).toBeNull()
    expect(FakeStream.all).toEqual([])
  })
})

describe('latestDownloads: the job each row re-attaches to', () => {
  const key = `bundled-chat\u0000${MODEL}`
  it('is the running one, wherever the list puts it', () => {
    const running = job('running', { id: 'job-9' })
    const failed = job('error', { id: 'job-8' })
    expect(latestDownloads([failed, running]).get(key)).toBe(running)
    expect(latestDownloads([running, failed]).get(key)).toBe(running)
  })
  it('is the newest when none runs', () => {
    const done = job('done', { id: 'job-7' })
    const failed = job('error', { id: 'job-8' })
    expect(latestDownloads([done, failed]).get(key)).toBe(failed)
  })
  it('keeps each model apart', () => {
    const other = { ...job('running'), id: 'job-3', model: 'other-model' }
    const mine = job('error')
    const got = latestDownloads([other, mine])
    expect(got.get(key)).toBe(mine)
    expect(got.get('bundled-chat\u0000other-model')).toBe(other)
  })
})
