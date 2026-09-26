/** OU-14 — the no-account way past onboarding's model wall.
 *
 *  The model lane is the step the activation audit named as the biggest drop-off, and every card
 *  in it needs an account somewhere. This component is the alternative, so what has to be true of
 *  it is narrow and load-bearing: it offers a download WITH its name, licence, size and what it is,
 *  it never appears on a home that does not need it, when the download fails it says which failure
 *  happened, and when the download FINISHES it says so and hands the lane the offer — once.
 *
 *  The finish half is the one the owner hit: the card vanished at 100% with no success message and
 *  the lane never noticed, so Continue stayed disabled beside a server that already reported the
 *  model ready. It is driven below through a fake `EventSource`, because the transition arrives on
 *  the download's SSE stream and a test that only mounts a finished job never sees it happen.
 */
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BundledModelOffer } from './BundledModelOffer'
import { api } from '../../lib/api'
import type { DownloadJob } from '../../lib/api'

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      onboarding: vi.fn(),
      modelDownloads: vi.fn(),
      startModelDownload: vi.fn(),
      cancelModelDownload: vi.fn(),
      setActiveModel: vi.fn(),
      downloadStreamUrl: vi.fn((id: string) => `http://localhost/stream/${id}`),
    },
  }
})
const onboarding = vi.mocked(api.onboarding)
const modelDownloads = vi.mocked(api.modelDownloads)
const startModelDownload = vi.mocked(api.startModelDownload)
const cancelModelDownload = vi.mocked(api.cancelModelDownload)
const setActiveModel = vi.mocked(api.setActiveModel)

const UNSET = { needs_model: true, has_model_provider: false, has_chat_binding: false }
const OFFER = {
  provider: 'bundled-chat',
  model: 'SmolLM2-135M-Instruct-Q8_0',
  label: 'SmolLM2-135M-Instruct',
  bytes: 144811072,
  licence: 'Apache-2.0',
  description: 'a small chat model',
}

const job = (state: DownloadJob['state'], extra: Partial<DownloadJob> = {}): DownloadJob => ({
  id: 'job-1', provider: OFFER.provider, model: OFFER.model, kind: 'weights', state,
  downloaded_bytes: 0, total_bytes: OFFER.bytes, progress: 0, speed_bps: 0, eta_s: 0,
  error: '', reason: '', ...extra,
})

/** A stand-in for the browser's EventSource that a test can push frames through. */
class FakeEventSource {
  static all: FakeEventSource[] = []
  listeners: Record<string, ((e: MessageEvent) => void)[]> = {}
  onerror: (() => void) | null = null
  closed = false
  constructor(public url: string) { FakeEventSource.all.push(this) }
  addEventListener(ev: string, fn: (e: MessageEvent) => void) { (this.listeners[ev] ||= []).push(fn) }
  close() { this.closed = true }
  emit(ev: string, data: unknown) {
    for (const fn of this.listeners[ev] ?? []) fn({ data: JSON.stringify(data) } as MessageEvent)
  }
}

beforeEach(() => {
  onboarding.mockReset()
  modelDownloads.mockReset().mockResolvedValue([])
  startModelDownload.mockReset()
  setActiveModel.mockReset().mockResolvedValue({ ok: true } as never)
  cancelModelDownload.mockReset().mockResolvedValue(undefined as never)
  FakeEventSource.all = []
  vi.stubGlobal('EventSource', FakeEventSource)
})
afterEach(() => { vi.unstubAllGlobals() })

describe('BundledModelOffer', () => {
  it('offers the download by name, licence and size, and says what the model is', async () => {
    onboarding.mockResolvedValue({ ...UNSET, chat_download_offer: OFFER })
    render(<BundledModelOffer onReady={vi.fn()} />)
    const card = await screen.findByTestId('onboarding-model-offer')
    // The whole offer, because agreeing to a download you were not told about is not agreeing.
    expect(card).toHaveTextContent('No account? Start with a small offline model')
    expect(card).toHaveTextContent(/SmolLM2-135M-Instruct · Apache-2\.0 · 138 MiB, downloaded once — then it runs offline on this machine/)
    expect(card).toHaveTextContent(/A small floor for getting started: expect short answers and no tools/)
    expect(screen.getByRole('button', { name: 'Download SmolLM2-135M-Instruct (138 MiB)' })).toBeInTheDocument()
  })

  it('starts the download through the shared job API, not a route of its own', async () => {
    onboarding.mockResolvedValue({ ...UNSET, chat_download_offer: OFFER })
    startModelDownload.mockResolvedValue(job('queued'))
    render(<BundledModelOffer onReady={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: /Download SmolLM2/ }))
    // The provider comes from the payload, so no app name is hard-coded in this component.
    await waitFor(() => expect(startModelDownload).toHaveBeenCalledWith(OFFER.provider, OFFER.model))
  })

  it('a reload mid-download re-attaches: bytes, a bar, an ETA and a cancel on mount', async () => {
    // The owner's report: a reload inside onboarding lost the bar, while the chat screen's resumed.
    // Nothing is clicked here — the running job the server lists is the whole input.
    onboarding.mockResolvedValue({ ...UNSET, chat_download_offer: OFFER })
    modelDownloads.mockResolvedValue([
      job('running', { downloaded_bytes: 36202768, progress: 0.25, eta_s: 42 }),
    ])
    render(<BundledModelOffer onReady={vi.fn()} />)
    const card = await screen.findByTestId('onboarding-model-offer')
    await waitFor(() => expect(card).toHaveTextContent(/Downloading SmolLM2-135M-Instruct — 35 MiB of 138 MiB, about 42s left/))
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '25')
    await userEvent.click(screen.getByRole('button', { name: /cancel the model download/i }))
    await waitFor(() => expect(cancelModelDownload).toHaveBeenCalledWith('job-1'))
  })

  it('says the download finished and hands the offer to the lane, exactly once', async () => {
    // idle → running → done, through the SSE stream the real runner publishes on.
    onboarding.mockResolvedValue({ ...UNSET, chat_download_offer: OFFER })
    startModelDownload.mockResolvedValue(job('queued'))
    const onReady = vi.fn()
    render(<BundledModelOffer onReady={onReady} />)
    await userEvent.click(await screen.findByRole('button', { name: /Download SmolLM2/ }))
    await waitFor(() => expect(FakeEventSource.all).toHaveLength(1))
    const stream = FakeEventSource.all[0]

    act(() => stream.emit('progress', job('running', { downloaded_bytes: 72405536, progress: 0.5, eta_s: 3 })))
    expect(await screen.findByRole('progressbar')).toHaveAttribute('aria-valuenow', '50')
    expect(onReady).not.toHaveBeenCalled()

    // The server stops offering the model once it is on disk — the refresh the stream's `done`
    // triggers returns no offer. The card must still say what happened.
    onboarding.mockResolvedValue({ ...UNSET, needs_model: false, chat_download_offer: null, chat_is_bundled_floor: true })
    act(() => stream.emit('done', job('done', { downloaded_bytes: OFFER.bytes, progress: 1 })))
    expect(await screen.findByText(/Downloaded SmolLM2-135M-Instruct — setting it up as your chat model/)).toBeInTheDocument()
    // Handed over only once the shared machine has offered it the chat binding — nothing else was
    // bound, so it is now the chat model, and the lane is told there was no refusal.
    await waitFor(() => expect(onReady).toHaveBeenCalledTimes(1))
    expect(setActiveModel).toHaveBeenCalledWith('chat', [`${OFFER.provider}:${OFFER.model}`])
    expect(onReady).toHaveBeenCalledWith(OFFER, '')
    expect(stream.closed).toBe(true)

    // …and later reads change nothing: once per download.
    const reads = onboarding.mock.calls.length
    await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
    expect(onboarding.mock.calls.length).toBeGreaterThanOrEqual(reads)
    expect(onReady).toHaveBeenCalledTimes(1)
    expect(setActiveModel).toHaveBeenCalledTimes(1)
  })

  it('a finished job the server remembers from an EARLIER session is not news', async () => {
    // The runner keeps finished jobs so a reload can see them. A model since deleted is offered
    // again, and that old `done` record must not read as "downloaded" beside it.
    onboarding.mockResolvedValue({ ...UNSET, chat_download_offer: OFFER })
    modelDownloads.mockResolvedValue([job('done', { downloaded_bytes: OFFER.bytes, progress: 1 })])
    const onReady = vi.fn()
    render(<BundledModelOffer onReady={onReady} />)
    expect(await screen.findByRole('button', { name: /Download SmolLM2/ })).toBeInTheDocument()
    await waitFor(() => expect(modelDownloads).toHaveBeenCalled())
    expect(onReady).not.toHaveBeenCalled()
  })

  it('names the failure rather than leaving the lane looking broken, and stays retryable', async () => {
    onboarding.mockResolvedValue({ ...UNSET, chat_download_offer: OFFER })
    modelDownloads.mockResolvedValue([
      job('error', { error: 'the model source answered HTTP 404 (Not Found)' }),
    ])
    render(<BundledModelOffer onReady={vi.fn()} />)
    const card = await screen.findByTestId('onboarding-model-offer')
    await waitFor(() => expect(card).toHaveTextContent(/HTTP 404/i))
    expect(screen.getByRole('button', { name: /Download SmolLM2/ })).toBeInTheDocument()
  })

  it('renders nothing when there is nothing to download', async () => {
    // Both shapes of "nothing": an explicit null, and a backend that predates the field. A
    // component that rendered on an absent key would put a download card on every bound home.
    for (const state of [{ ...UNSET, chat_download_offer: null }, UNSET]) {
      onboarding.mockResolvedValue(state)
      const { container, unmount } = render(<BundledModelOffer onReady={vi.fn()} />)
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
    render(<BundledModelOffer onReady={vi.fn()} />)
    const note = await screen.findByTestId('onboarding-model-offer-error')
    expect(note).toHaveTextContent(/Couldn.t check for a no-account option/i)
    expect(note).toHaveTextContent(/offline/)
    expect(note).toHaveTextContent(/pick a provider below/i)
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument()
    expect(screen.queryByTestId('onboarding-model-offer')).toBeNull()
  })
})
