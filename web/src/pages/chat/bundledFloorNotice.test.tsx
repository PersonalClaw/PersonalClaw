/** OU-14 — the chat surface's two honest states: the download OFFER and the floor LABEL.
 *
 *  Both directions matter and for different reasons. Absent when it should be absent, or the
 *  banner accuses a properly-bound home of running a toy model on every single chat. Present
 *  when it should be present, or the whole honesty half of OU-14 is a comment in a Python file:
 *  a newcomer's first ever reply comes from a 135M model with nothing on screen to explain it,
 *  and the conclusion they draw is about PersonalClaw rather than about the model.
 *
 *  The offer half was added when the owner ruled the wheel ships WITHOUT the weight, which put a
 *  138 MiB download in front of the first chat. What is asserted about it is deliberately not
 *  "a spinner appears": it is the SIZE before the click, byte counts and an ETA during, a cancel
 *  that is reachable, and a per-failure sentence — because a first run that silently stalls on a
 *  138 MiB transfer is worse than the model-bind wall this atom removed.
 */
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BundledFloorNotice } from './BundledFloorNotice'
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
      downloadStreamUrl: vi.fn(() => 'http://localhost/stream'),
    },
  }
})
const onboarding = vi.mocked(api.onboarding)
const modelDownloads = vi.mocked(api.modelDownloads)
const startModelDownload = vi.mocked(api.startModelDownload)
const cancelModelDownload = vi.mocked(api.cancelModelDownload)
const setActiveModel = vi.mocked(api.setActiveModel)

const BOUND = { needs_model: false, has_model_provider: true, has_chat_binding: true }
const FLOOR = { needs_model: false, has_model_provider: true, has_chat_binding: false }
const UNSET = { needs_model: true, has_model_provider: false, has_chat_binding: false }
const OFFER = {
  provider: 'bundled-chat',
  model: 'SmolLM2-135M-Instruct-Q8_0',
  label: 'SmolLM2-135M-Instruct',
  bytes: 144811072,
  licence: 'Apache-2.0',
  description: 'a small chat model',
}

/** A download job in *state*, shaped like the one the SSE stream sends. */
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
  setActiveModel.mockReset().mockResolvedValue({ ok: true } as never)
  // EventSource does not exist in jsdom; the hook guards construction, and these tests drive
  // job state through `modelDownloads`/`startModelDownload` rather than through a live stream.
  vi.stubGlobal('EventSource', undefined)
})

describe('BundledFloorNotice — the download offer', () => {
  it('states the size BEFORE the click, and offers a way past it', async () => {
    onboarding.mockResolvedValue({ ...UNSET, chat_download_offer: OFFER })
    render(<BundledFloorNotice />)
    const card = await screen.findByTestId('bundled-model-offer')
    // 138 MiB, spelled out. The number is the honesty: a user agreeing to minutes of transfer
    // on a slow connection is entitled to know that before agreeing, not by watching a bar.
    expect(card).toHaveTextContent(/one-time 138 MiB download/i)
    expect(card).toHaveTextContent(/SmolLM2-135M-Instruct \(Apache-2\.0\)/)
    expect(screen.getByRole('button', { name: /download 138 MiB/i })).toBeInTheDocument()
    // Neither required nor the only option — both escapes are on screen.
    expect(screen.getByRole('button', { name: /not now/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /connect a provider/i })).toHaveAttribute('href', '#/settings/models')
    // …and it says what the model is like, so a short shaky answer is expected rather than a defect.
    expect(card).toHaveTextContent(/short, shaky answers and no tool use/i)
  })

  it('shows bytes, a percentage bar and an ETA while it runs, with a reachable cancel', async () => {
    onboarding.mockResolvedValue({ ...UNSET, chat_download_offer: OFFER })
    modelDownloads.mockResolvedValue([
      job('running', { downloaded_bytes: 72405536, progress: 0.5, eta_s: 95, speed_bps: 1 << 20 }),
    ])
    render(<BundledFloorNotice />)
    const card = await screen.findByTestId('bundled-model-offer')
    await waitFor(() => expect(card).toHaveTextContent(/69 MiB of 138 MiB/i))
    // 🔴 The ETA was on the wire and rendered NOWHERE before this atom. A bar with no estimate
    // is the "how long is this going to take" question a first-run download must answer.
    expect(card).toHaveTextContent(/about 2 min left/i)
    // The bar is DETERMINATE and at the real position. `progress` is a fraction 0..1 on the
    // wire, so a component treating it as a percentage would sit at 1% for the whole download —
    // asserting `aria-valuenow` is what distinguishes "a bar is drawn" from "a bar moves".
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50')
    expect(screen.getByRole('button', { name: /cancel the model download/i })).toBeInTheDocument()
    expect(card).toHaveTextContent(/cancel and set up any provider instead/i)
  })

  it('cancels through the API rather than only hiding the row', async () => {
    onboarding.mockResolvedValue({ ...UNSET, chat_download_offer: OFFER })
    modelDownloads.mockResolvedValue([job('running', { downloaded_bytes: 1 << 20, progress: 0.01 })])
    render(<BundledFloorNotice />)
    await screen.findByTestId('bundled-model-offer')
    const button = await screen.findByRole('button', { name: /cancel the model download/i })
    await userEvent.click(button)
    // The request is what STOPS the transfer. A row that cleared itself without calling this
    // would read "not downloading" while the server kept pulling bytes.
    await waitFor(() => expect(cancelModelDownload).toHaveBeenCalledWith('job-1'))
  })

  it('names WHICH failure happened and leaves the offer retryable', async () => {
    // The four outcomes (no network / a 404 / truncated / digest mismatch) reach the UI as the
    // job's own error sentence, so what this asserts is that the sentence is SHOWN — a generic
    // "download failed" would leave a user with no idea whether to retry or to stop waiting.
    const detail = 'could not reach the model source. Retry when you are connected, or bind a provider'
    onboarding.mockResolvedValue({ ...UNSET, chat_download_offer: OFFER })
    modelDownloads.mockResolvedValue([job('error', { error: detail })])
    render(<BundledFloorNotice />)
    const card = await screen.findByTestId('bundled-model-offer')
    await waitFor(() => expect(card).toHaveTextContent(/could not reach the model source/i))
    // Still offered, so the failure is recoverable rather than terminal.
    expect(screen.getByRole('button', { name: /download 138 MiB/i })).toBeInTheDocument()
  })

  it('reports a refused START instead of appearing to do nothing', async () => {
    onboarding.mockResolvedValue({ ...UNSET, chat_download_offer: OFFER })
    startModelDownload.mockRejectedValue(new Error('no local-model provider is registered'))
    render(<BundledFloorNotice />)
    await screen.findByTestId('bundled-model-offer')
    await userEvent.click(screen.getByRole('button', { name: /download 138 MiB/i }))
    await waitFor(() =>
      expect(screen.getByTestId('bundled-model-offer')).toHaveTextContent(/no local-model provider is registered/i),
    )
  })

  it('“Not now” dismisses the offer without binding anything', async () => {
    onboarding.mockResolvedValue({ ...UNSET, chat_download_offer: OFFER })
    render(<BundledFloorNotice />)
    await screen.findByTestId('bundled-model-offer')
    await userEvent.click(screen.getByRole('button', { name: /not now/i }))
    await waitFor(() => expect(screen.queryByTestId('bundled-model-offer')).toBeNull())
    expect(startModelDownload).not.toHaveBeenCalled()
  })

  it('renders nothing at all when there is no offer and no floor', async () => {
    onboarding.mockResolvedValue({ ...BOUND, chat_download_offer: null, chat_is_bundled_floor: false })
    const { container } = render(<BundledFloorNotice />)
    await waitFor(() => expect(onboarding).toHaveBeenCalled())
    expect(container.firstChild).toBeNull()
  })

  it('does not advertise the download beside a model that answers', async () => {
    // The server offers it whenever the model is not on disk, because onboarding's model step
    // always lets a user pick it. The chat screen is the one place it must not follow that.
    onboarding.mockResolvedValue({ ...BOUND, chat_download_offer: OFFER, chat_is_bundled_floor: false })
    const { container } = render(<BundledFloorNotice />)
    // The offer WAS read: the download tracker only asks for jobs once it knows the provider.
    await waitFor(() => expect(modelDownloads).toHaveBeenCalled())
    await act(async () => {})
    expect(container.firstChild).toBeNull()
  })

  it('still shows a download that is under way beside a model that answers', async () => {
    // Its progress is news wherever it was started (onboarding's model step, say).
    onboarding.mockResolvedValue({ ...BOUND, chat_download_offer: OFFER, chat_is_bundled_floor: false })
    modelDownloads.mockResolvedValue([job('running', { downloaded_bytes: 1 << 20, progress: 0.01 })])
    render(<BundledFloorNotice />)
    expect(await screen.findByTestId('bundled-model-offer')).toBeTruthy()
    expect(await screen.findByRole('progressbar')).toBeTruthy()
  })
})

// ── A download finished HERE means what one finished in onboarding means ──────────────────────
//
// Onboarding made the downloaded model the chat model when nothing else was; the same download
// started from this screen did not, so chat answered from the implicit fallback — bound to
// nothing, on no model list, unnamed. The rule now lives in the one download machine both read.
describe('BundledFloorNotice — a finished download becomes the chat model', () => {
  /** Start the download on a model already on disk: the runner answers an immediately-`done` job. */
  async function downloadHere() {
    startModelDownload.mockResolvedValue(job('done', { downloaded_bytes: OFFER.bytes, progress: 1 }))
    render(<BundledFloorNotice />)
    await userEvent.click(await screen.findByRole('button', { name: /download 138 MiB/i }))
  }

  it('binds it as the chat model when nothing else is bound', async () => {
    onboarding.mockResolvedValue({ ...UNSET, chat_download_offer: OFFER, chat_model_refs: [] })
    await downloadHere()
    await waitFor(() => expect(setActiveModel).toHaveBeenCalledWith('chat', [`${OFFER.provider}:${OFFER.model}`]))
    expect(setActiveModel).toHaveBeenCalledTimes(1)
  })

  it('never overwrites a chat model the user already bound', async () => {
    onboarding.mockResolvedValue({ ...UNSET, chat_download_offer: OFFER, chat_model_refs: ['openai:gpt-5'] })
    await downloadHere()
    // The binding step ran (it re-read readiness)…
    await waitFor(() => expect(onboarding.mock.calls.length).toBeGreaterThanOrEqual(3))
    // …and left the user's binding alone.
    expect(setActiveModel).not.toHaveBeenCalled()
  })

  it('says so when the binding is refused, rather than swallowing it', async () => {
    onboarding.mockResolvedValue({ ...UNSET, chat_download_offer: OFFER, chat_model_refs: [] })
    setActiveModel.mockRejectedValue(new Error(JSON.stringify({ error: 'active_models.json is read-only' })))
    await downloadHere()
    expect(await screen.findByText(/could not be set as your chat model \(active_models\.json is read-only\)/)).toBeTruthy()
  })
})

describe('BundledFloorNotice', () => {
  it('says what is answering, and how to replace it, when chat is on the bundled floor', async () => {
    onboarding.mockResolvedValue({ ...FLOOR, chat_is_bundled_floor: true })
    render(<BundledFloorNotice />)
    const notice = await screen.findByTestId('bundled-floor-notice')
    // The three things a user needs: what it is, why it can answer at all, and the way out.
    expect(notice).toHaveTextContent(/small model PersonalClaw downloaded/i)
    expect(notice).toHaveTextContent(/no account and no API key/i)
    const link = screen.getByRole('link', { name: /connect a real model/i })
    expect(link).toHaveAttribute('href', '#/settings/models')
    // Announced, not merely drawn — it is news about the conversation, not decoration.
    expect(notice).toHaveAttribute('role', 'status')
  })

  it('renders nothing on a home that has bound a real model', async () => {
    onboarding.mockResolvedValue({ ...BOUND, chat_is_bundled_floor: false })
    const { container } = render(<BundledFloorNotice />)
    await waitFor(() => expect(onboarding).toHaveBeenCalled())
    expect(screen.queryByTestId('bundled-floor-notice')).toBeNull()
    expect(container.firstChild).toBeNull()
  })

  it('renders nothing when an older backend omits the field', async () => {
    // Absent is not "maybe": a missing flag must read as false, or every home talking to a
    // backend that predates OU-14 gets a warning about a model it does not have.
    onboarding.mockResolvedValue(BOUND)
    render(<BundledFloorNotice />)
    await waitFor(() => expect(onboarding).toHaveBeenCalled())
    expect(screen.queryByTestId('bundled-floor-notice')).toBeNull()
  })

  it('says the probe failed WITHOUT guessing a warning about the model', async () => {
    // Both halves matter and they pull in opposite directions. A failed probe must NOT produce
    // the floor banner — that would accuse a perfectly-bound home of running a toy model on the
    // strength of a network hiccup. But it must not be silent either: what is lost with the
    // answer is the DOWNLOAD OFFER, the only escape from an unconfigured install, so its
    // disappearance is explained and retryable rather than just happening.
    onboarding.mockRejectedValue(new Error('offline'))
    render(<BundledFloorNotice />)
    const card = await screen.findByTestId('bundled-model-probe-error')
    expect(card).toHaveTextContent(/Couldn.t check whether a model is set up/i)
    expect(card).toHaveTextContent(/offline/)
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument()
    // The half that must NOT happen.
    expect(screen.queryByTestId('bundled-floor-notice')).toBeNull()
    expect(screen.queryByTestId('bundled-model-offer')).toBeNull()
  })

  it('a retry that succeeds clears the failure notice', async () => {
    onboarding.mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValue({ ...FLOOR, chat_is_bundled_floor: true })
    render(<BundledFloorNotice />)
    await userEvent.click(await screen.findByRole('button', { name: /try again/i }))
    // The failure notice is a report of one read, not a sticky state.
    await waitFor(() => expect(screen.queryByTestId('bundled-model-probe-error')).toBeNull())
    expect(await screen.findByTestId('bundled-floor-notice')).toBeInTheDocument()
  })
})
