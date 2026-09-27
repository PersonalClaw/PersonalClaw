/** A download that started without its free-space check says so while it runs.
 *
 *  When the pre-download check cannot measure the disk (`local_models/fit.disk_precheck`), the
 *  download still starts — blocking a good download on a failed probe is the worse error — and the
 *  job carries "Free space could not be checked, so the download was not verified to fit." Before,
 *  only the 202 carried it, `DownloadJob` had no field for it and `useModelDownloads.start` dropped
 *  it, so no screen ever showed it and the download read as one that had been checked.
 *
 *  Driven through both places a download's progress is drawn: the shared progress row (the
 *  onboarding offer, the chat notice and the Models page's inline download) and the local-model
 *  rows of Settings → Models.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, type AvailableModel, type DownloadJob } from '../../lib/api'
import { BundledModelOffer } from '../../app/onboarding/BundledModelOffer'
import { LocalModelManager } from './LocalModelManager'

const WARNING = 'Free space could not be checked, so the download was not verified to fit.'

const OFFER = {
  provider: 'bundled-chat', model: 'SmolLM2-135M-Instruct-Q8_0', label: 'SmolLM2-135M-Instruct',
  bytes: 144811072, licence: 'Apache-2.0', description: 'a small chat model',
}

const job = (over: Partial<DownloadJob> = {}): DownloadJob => ({
  id: 'job-1', provider: OFFER.provider, model: OFFER.model, kind: 'weights', state: 'running',
  downloaded_bytes: 36202768, total_bytes: OFFER.bytes, progress: 0.25, speed_bps: 0, eta_s: 42,
  error: '', reason: '', warning: '', ...over,
})

class FakeEventSource {
  listeners: Record<string, ((e: MessageEvent) => void)[]> = {}
  onerror: (() => void) | null = null
  constructor(public url: string) {}
  addEventListener(ev: string, fn: (e: MessageEvent) => void) { (this.listeners[ev] ||= []).push(fn) }
  close() {}
}

beforeEach(() => {
  vi.stubGlobal('EventSource', FakeEventSource)
  vi.spyOn(api, 'downloadStreamUrl').mockImplementation((id: string) => `/api/models/downloads/${id}/stream`)
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('the progress row says a download was never checked for space', () => {
  it('on the onboarding offer, re-attached after a reload', async () => {
    vi.spyOn(api, 'onboarding').mockResolvedValue({
      needs_model: true, has_model_provider: false, has_chat_binding: false, chat_download_offer: OFFER,
    } as Awaited<ReturnType<typeof api.onboarding>>)
    vi.spyOn(api, 'modelDownloads').mockResolvedValue([job({ warning: WARNING })])
    render(<BundledModelOffer onReady={vi.fn()} />)
    const card = await screen.findByTestId('onboarding-model-offer')
    await waitFor(() => expect(card).toHaveTextContent(/Downloading SmolLM2-135M-Instruct — 35 MiB of 138 MiB/))
    expect(card).toHaveTextContent(WARNING)
  })

  it('says nothing of the kind for a download the check passed', async () => {
    vi.spyOn(api, 'onboarding').mockResolvedValue({
      needs_model: true, has_model_provider: false, has_chat_binding: false, chat_download_offer: OFFER,
    } as Awaited<ReturnType<typeof api.onboarding>>)
    vi.spyOn(api, 'modelDownloads').mockResolvedValue([job()])
    render(<BundledModelOffer onReady={vi.fn()} />)
    const card = await screen.findByTestId('onboarding-model-offer')
    await waitFor(() => expect(card).toHaveTextContent(/Downloading SmolLM2-135M-Instruct/))
    expect(card).not.toHaveTextContent(/could not be checked/)
  })

  it('on a Settings → Models row, from the job the start answered with', async () => {
    const models: AvailableModel[] = [{
      id: 'llama3:8b', name: 'llama3:8b', capabilities: ['chat'], provider: 'ollama',
      provider_type: 'ollama', size_mb: 4600, downloaded: false,
    }]
    vi.spyOn(api, 'modelDownloads').mockResolvedValue([])
    vi.spyOn(api, 'startModelDownload').mockResolvedValue(job({
      provider: 'ollama', model: 'llama3:8b', state: 'queued', downloaded_bytes: 0, progress: 0,
      total_bytes: 4600 * 1024 * 1024, eta_s: 0, warning: WARNING,
    }))
    render(<LocalModelManager provider="ollama" models={models} onChanged={vi.fn()} />)
    const download = screen.getAllByRole('button').find((b) => /^Download /.test(b.getAttribute('aria-label') ?? ''))
    expect(download, 'the row offers Download').toBeTruthy()
    await userEvent.click(download!)
    expect(await screen.findByText(WARNING)).toBeInTheDocument()
  })
})
