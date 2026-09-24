/** OU-14 — the no-account way past onboarding's model wall.
 *
 *  The model lane is the step the activation audit named as the biggest drop-off, and every card
 *  in it needs an account somewhere. This component is the alternative, so what has to be true of
 *  it is narrow and load-bearing: it offers a download WITH ITS SIZE, it never appears on a home
 *  that does not need it, and when the download fails it says which failure happened instead of
 *  leaving the lane looking broken.
 *
 *  The size assertion is the one worth naming. 138 MiB is minutes on a slow connection, and a
 *  user agreeing to a transfer they were not told the cost of has not agreed to anything.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BundledModelOffer } from './BundledModelOffer'
import { api } from '../../lib/api'
import type { DownloadJob } from '../../lib/api'

vi.mock('../../lib/api', () => ({
  api: {
    onboarding: vi.fn(),
    modelDownloads: vi.fn(),
    startModelDownload: vi.fn(),
    cancelModelDownload: vi.fn(),
    downloadStreamUrl: vi.fn(() => 'http://localhost/stream'),
  },
}))
const onboarding = vi.mocked(api.onboarding)
const modelDownloads = vi.mocked(api.modelDownloads)
const startModelDownload = vi.mocked(api.startModelDownload)
const cancelModelDownload = vi.mocked(api.cancelModelDownload)

const UNSET = { needs_model: true, has_model_provider: false, has_chat_binding: false }
const OFFER = {
  provider: 'bundled-chat',
  model: 'SmolLM2-135M-Instruct-Q8_0',
  bytes: 144811072,
  licence: 'Apache-2.0',
  description: 'a small chat model',
}

const job = (state: DownloadJob['state'], extra: Partial<DownloadJob> = {}): DownloadJob => ({
  id: 'job-1', provider: OFFER.provider, model: OFFER.model, kind: 'weights', state,
  downloaded_bytes: 0, total_bytes: OFFER.bytes, progress: 0, speed_bps: 0, eta_s: 0,
  error: '', reason: '', ...extra,
})

beforeEach(() => {
  onboarding.mockReset()
  modelDownloads.mockReset().mockResolvedValue([])
  startModelDownload.mockReset()
  cancelModelDownload.mockReset().mockResolvedValue(undefined as never)
  vi.stubGlobal('EventSource', undefined)
})

describe('BundledModelOffer', () => {
  it('offers the download with its size, and says what the model is like', async () => {
    onboarding.mockResolvedValue({ ...UNSET, chat_download_offer: OFFER })
    render(<BundledModelOffer />)
    const card = await screen.findByTestId('onboarding-model-offer')
    expect(card).toHaveTextContent(/No account\? Download a small model instead — 138 MiB, once/i)
    expect(screen.getByRole('button', { name: /download 138 MiB/i })).toBeInTheDocument()
    // Honest about what it is, so a short answer later is expected rather than read as a defect.
    expect(card).toHaveTextContent(/short answers and no tool use/i)
    expect(card).toHaveTextContent(/add a real provider any time/i)
  })

  it('starts the download through the shared job API, not a route of its own', async () => {
    onboarding.mockResolvedValue({ ...UNSET, chat_download_offer: OFFER })
    startModelDownload.mockResolvedValue(job('running'))
    render(<BundledModelOffer />)
    await screen.findByTestId('onboarding-model-offer')
    await userEvent.click(screen.getByRole('button', { name: /download 138 MiB/i }))
    // The provider comes from the payload, so no app name is hard-coded in this component.
    await waitFor(() => expect(startModelDownload).toHaveBeenCalledWith(OFFER.provider, OFFER.model))
  })

  it('shows bytes, a bar, an ETA and a cancel while it runs', async () => {
    onboarding.mockResolvedValue({ ...UNSET, chat_download_offer: OFFER })
    modelDownloads.mockResolvedValue([
      job('running', { downloaded_bytes: 36202768, progress: 0.25, eta_s: 42 }),
    ])
    render(<BundledModelOffer />)
    const card = await screen.findByTestId('onboarding-model-offer')
    await waitFor(() => expect(card).toHaveTextContent(/35 MiB of 138 MiB/i))
    expect(card).toHaveTextContent(/about 42s left/i)
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '25')
    await userEvent.click(screen.getByRole('button', { name: /cancel the model download/i }))
    await waitFor(() => expect(cancelModelDownload).toHaveBeenCalledWith('job-1'))
  })

  it('names the failure rather than leaving the lane looking broken', async () => {
    onboarding.mockResolvedValue({ ...UNSET, chat_download_offer: OFFER })
    modelDownloads.mockResolvedValue([
      job('error', { error: 'the model source answered HTTP 404 (Not Found)' }),
    ])
    render(<BundledModelOffer />)
    const card = await screen.findByTestId('onboarding-model-offer')
    await waitFor(() => expect(card).toHaveTextContent(/HTTP 404/i))
    // …and the offer is still there, so a failure is recoverable rather than terminal.
    expect(screen.getByRole('button', { name: /download 138 MiB/i })).toBeInTheDocument()
  })

  it('renders nothing when there is nothing to download', async () => {
    // Both shapes of "nothing": an explicit null, and a backend that predates the field. A
    // component that rendered on an absent key would put a download card on every bound home.
    for (const state of [{ ...UNSET, chat_download_offer: null }, UNSET]) {
      onboarding.mockResolvedValue(state)
      const { container, unmount } = render(<BundledModelOffer />)
      await waitFor(() => expect(onboarding).toHaveBeenCalled())
      expect(container.firstChild).toBeNull()
      unmount()
    }
  })

  it('explains why the no-account option vanished when the probe fails', async () => {
    // Not silence: this card is the lane's only no-account path, so a user who cannot see it
    // should be told the check failed rather than conclude the option does not exist. The lane's
    // provider catalogue below is unaffected, and the copy says so.
    onboarding.mockRejectedValue(new Error('offline'))
    render(<BundledModelOffer />)
    const note = await screen.findByTestId('onboarding-model-offer-error')
    expect(note).toHaveTextContent(/Couldn.t check for a no-account option/i)
    expect(note).toHaveTextContent(/offline/)
    expect(note).toHaveTextContent(/pick a provider below/i)
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument()
    expect(screen.queryByTestId('onboarding-model-offer')).toBeNull()
  })
})
