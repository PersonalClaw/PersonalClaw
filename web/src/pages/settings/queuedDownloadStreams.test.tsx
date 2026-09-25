import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { api, type AvailableModel, type DownloadJob } from '../../lib/api'
import { LocalModelManager } from './LocalModelManager'

// ── A freshly-started download must be SUBSCRIBED TO, not just started (#3520) ─────────────────
//
// `POST /api/models/downloads` answers `state: "queued"`, deterministically and always:
// `ModelDownloadRegistry.start()` creates the job with the default `queued`, schedules its worker
// with `asyncio.ensure_future`, and the handler returns `202` with **no `await` in between** — so
// the coroutine that would set `running` cannot have run yet.
//
// `useModelDownloads` tested `state !== 'running'` in all three of its checks. So on the one path
// that STARTS a download it opened no SSE stream and reported the job settled, and the row froze
// at its initial byte count. Measured on a fresh container: `Getting a small model ready — 0 MiB
// of 138 MiB` held for over six minutes, with a Cancel button beside it, while the full
// 144,811,072-byte weight had already landed on disk about thirty seconds in. The whole `/api/*`
// census after the click was one `202 POST /api/models/downloads` and nothing else — no stream, no
// re-list. Only a manual reload, which nothing suggests, showed the truth.
//
// 🔑 THESE TESTS DRIVE THE HOOK'S START PATH rather than asserting its source, and the mount path
// is deliberately starved (`modelDownloads` → `[]`) — that path was never broken, and leaving a
// job in it is precisely what made the bug look cosmetic, because re-attaching on mount is what a
// reload does. What is asserted is what a user sees: the row says it is downloading, progress
// frames actually arrive and move it, and nothing declares the job finished while it is queued.

const MODELS: AvailableModel[] = [{
  id: 'llama3:8b', name: 'llama3:8b', capabilities: ['chat'], provider: 'ollama',
  provider_type: 'ollama', size_mb: 4600, downloaded: false,
}]

/** Exactly what the POST hands back — the state the server really sends, not `running`. */
const QUEUED: DownloadJob = {
  id: 'job-7', provider: 'ollama', model: 'llama3:8b', kind: 'weights', state: 'queued',
  progress: 0, speed_bps: 0, eta_s: 0,
  total_bytes: 4_600_000_000, downloaded_bytes: 0,
  error: '', reason: '',
}

/** An `EventSource` stand-in that records every URL it was opened with and lets a test push a
 *  frame back through the listener the hook registered. jsdom has no EventSource at all. */
type Frame = { type: string; job: DownloadJob }
let opened: string[] = []
let emit: (f: Frame) => void = () => {}

beforeEach(() => {
  opened = []
  const listeners = new Map<string, ((e: Event) => void)[]>()
  emit = ({ type, job }) => {
    for (const fn of listeners.get(type) ?? []) {
      fn({ data: JSON.stringify(job) } as unknown as MessageEvent)
    }
  }
  ;(globalThis as unknown as { EventSource: unknown }).EventSource = class {
    constructor(url: string) { opened.push(url) }
    close() {}
    addEventListener(type: string, fn: (e: Event) => void) {
      listeners.set(type, [...(listeners.get(type) ?? []), fn])
    }
    onerror: unknown = null
  }
  // NOTHING in flight on mount. The re-attach path must not be able to rescue the start path.
  vi.spyOn(api, 'modelDownloads').mockResolvedValue([])
  vi.spyOn(api, 'downloadStreamUrl').mockImplementation((id: string) => `/api/models/downloads/${id}/stream`)
  vi.spyOn(api, 'startModelDownload').mockResolvedValue(QUEUED)
})
afterEach(() => vi.restoreAllMocks())

const mount = () => {
  const onChanged = vi.fn()
  render(<LocalModelManager provider="ollama" models={MODELS} onChanged={onChanged} />)
  return { onChanged }
}

const byLabel = (re: RegExp) => screen.getAllByRole('button')
  .find((b) => re.test(b.getAttribute('aria-label') ?? ''))

describe('a download that comes back queued', () => {
  it('🪤 vacuity floor — the row offers Download before the click, and the click reaches the API', async () => {
    // Without this, every assertion below could pass against a manager that rendered no row at all.
    const { onChanged } = mount()
    await waitFor(() => expect(byLabel(/^Download llama3:8b$/)).toBeTruthy())
    expect(onChanged, 'nothing has settled yet').not.toHaveBeenCalled()
    fireEvent.click(byLabel(/^Download llama3:8b$/)!)
    await waitFor(() => expect(api.startModelDownload).toHaveBeenCalledWith('ollama', 'llama3:8b'))
  })

  it('🔴 opens its progress stream — `queued` is not terminal', async () => {
    mount()
    await waitFor(() => expect(byLabel(/^Download llama3:8b$/)).toBeTruthy())
    fireEvent.click(byLabel(/^Download llama3:8b$/)!)
    await waitFor(() => expect(opened).toEqual(['/api/models/downloads/job-7/stream']))
  })

  it('🔴 renders as downloading, so the row is not still offering the button it just used', async () => {
    mount()
    await waitFor(() => expect(byLabel(/^Download llama3:8b$/)).toBeTruthy())
    fireEvent.click(byLabel(/^Download llama3:8b$/)!)
    // The Cancel control replaces Download exactly when the row believes a download is live.
    await waitFor(() => expect(byLabel(/^Cancel llama3:8b$/)).toBeTruthy())
    expect(byLabel(/^Download llama3:8b$/), 'Download must not still be offered').toBeFalsy()
  })

  it('🔴 does not report the job settled while it is still queued', async () => {
    // `onChanged` is the "re-read the model list, this is over" callback. Firing it on `queued`
    // is what made a download that had just begun look like one that had already finished.
    const { onChanged } = mount()
    await waitFor(() => expect(byLabel(/^Download llama3:8b$/)).toBeTruthy())
    fireEvent.click(byLabel(/^Download llama3:8b$/)!)
    await waitFor(() => expect(api.startModelDownload).toHaveBeenCalled())
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('🔴 the subscription is LIVE — a progress frame moves the row', async () => {
    // The strongest form of the assertion: not that an EventSource was constructed, but that the
    // frames it carries reach the render. A stream opened and never read would satisfy the test
    // above and still leave the user watching a frozen number.
    mount()
    await waitFor(() => expect(byLabel(/^Download llama3:8b$/)).toBeTruthy())
    fireEvent.click(byLabel(/^Download llama3:8b$/)!)
    await waitFor(() => expect(opened.length).toBe(1))
    // The row reads `downloading` with no byte count until bytes have actually arrived, so the
    // NUMBER is the evidence the frame landed. Matched loosely on the digits rather than pinned to
    // the MiB-vs-MB rounding convention, which is `LocalModelManager`'s own concern, not this one's.
    expect(screen.getByText('downloading'), 'no bytes yet, so no count').toBeTruthy()
    emit({ type: 'progress', job: { ...QUEUED, state: 'running', progress: 0.25, downloaded_bytes: 1_150_000_000 } })
    await waitFor(() => expect(screen.getByText(/^downloading · \d+ \/ 4600 MB$/)).toBeTruthy())
  })

  it('a terminal frame still settles it — the fix must not break the end of the job', async () => {
    const { onChanged } = mount()
    await waitFor(() => expect(byLabel(/^Download llama3:8b$/)).toBeTruthy())
    fireEvent.click(byLabel(/^Download llama3:8b$/)!)
    await waitFor(() => expect(opened.length).toBe(1))
    emit({ type: 'done', job: { ...QUEUED, state: 'done', progress: 1, downloaded_bytes: QUEUED.total_bytes } })
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('an immediately-`done` job settles WITHOUT a stream — the already-downloaded short-circuit', async () => {
    // The branch the old `state !== 'running'` test was written for, and the one a naive fix would
    // break: the weights are already on disk, the server answers `done`, and there is nothing to
    // stream. It must still tell the caller to re-read the list.
    vi.spyOn(api, 'startModelDownload').mockResolvedValue({
      ...QUEUED, state: 'done', progress: 1, downloaded_bytes: QUEUED.total_bytes,
    })
    const { onChanged } = mount()
    await waitFor(() => expect(byLabel(/^Download llama3:8b$/)).toBeTruthy())
    fireEvent.click(byLabel(/^Download llama3:8b$/)!)
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
    expect(opened, 'a finished job has no progress to stream').toEqual([])
  })
})
