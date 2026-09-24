import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { api, type AvailableModel, type DownloadJob } from '../../lib/api'
import { LocalModelManager } from './LocalModelManager'

// ── A failed cancel is the one this surface could not afford to swallow ────────────────────────────
//
// `saveFailureReported`'s rail states the convention: every optimistic write here flips local state
// first, so a swallowed rejection "is a lie, because the control is left showing a value the server
// refused". `LocalModelManager` obeyed it for `download` and `remove` — both `try/catch` into a per-row
// `setErr` — and broke it for exactly one action:
//
//     await api.cancelModelDownload(job.id).catch(() => {})   // swallowed
//     setJobs(prev => { delete prev[model] })                 // cleared anyway
//
// For a cancel the request IS the stop. So a failure left the row reading "not downloading" while the
// server kept pulling bytes, and a reload re-attached to the still-running job — the silent-revert shape
// that rail was written for, on the write where it costs the most (a multi-GB download nobody stopped).
//
// It also closed the SSE stream BEFORE the request, so a failed cancel additionally blinded the row to
// the progress that kept arriving.
//
// 🔑 THIS TEST DRIVES THE FAILURE rather than asserting the source. It renders the real manager, mounts a
// running job, rejects the cancel request, and checks the two things a user would notice: the progress
// row is STILL THERE, and they were TOLD. A regex could not tell those apart from a swallowed error.

// Real shapes, not casts: a `as` through a mismatched literal is how a fixture drifts from the API it
// claims to mirror, and typecheck rightly refused the first draft.
const MODELS: AvailableModel[] = [{
  id: 'llama3:8b', name: 'llama3:8b', capabilities: ['chat'], provider: 'ollama',
  provider_type: 'ollama', size_mb: 4600, downloaded: false,
}]

const RUNNING: DownloadJob = {
  id: 'job-1', provider: 'ollama', model: 'llama3:8b', kind: 'weights', state: 'running',
  progress: 0.2, speed_bps: 1_000_000, eta_s: 120,
  total_bytes: 4_600_000_000, downloaded_bytes: 920_000_000,
  error: '', reason: '',
}

beforeEach(() => {
  // EventSource does not exist in jsdom; the hook only needs it to not throw.
  ;(globalThis as unknown as { EventSource: unknown }).EventSource = class {
    close() {}
    addEventListener() {}
    onerror: unknown = null
  }
  vi.spyOn(api, 'modelDownloads').mockResolvedValue([RUNNING])
  vi.spyOn(api, 'downloadStreamUrl').mockReturnValue('/api/models/downloads/job-1/stream')
})
afterEach(() => vi.restoreAllMocks())

const mount = () => {
  const onChanged = vi.fn()
  render(<LocalModelManager provider="ollama" models={MODELS} onChanged={onChanged} />)
  return { onChanged }
}

/** The cancel control is an icon button; find it by its accessible name. */
const cancelButton = () =>
  screen.getAllByRole('button').find((b) => /cancel|stop/i.test(b.getAttribute('aria-label') ?? b.getAttribute('title') ?? ''))

describe('a download cancel that fails says so, and keeps the row', () => {
  it('re-attaches to the in-flight job on mount, so there is something to cancel', async () => {
    mount()
    // The vacuity floor: if the job never renders, everything below passes without testing anything.
    await waitFor(() => expect(cancelButton()).toBeTruthy())
  })

  it('a REJECTED cancel leaves the progress row in place and tells the user', async () => {
    const cancelSpy = vi.spyOn(api, 'cancelModelDownload')
      .mockRejectedValue(new Error('{"error":"job already finished"}'))
    mount()
    await waitFor(() => expect(cancelButton()).toBeTruthy())
    fireEvent.click(cancelButton()!)

    await waitFor(() => expect(cancelSpy).toHaveBeenCalledWith('job-1'))
    // 1. The user was told, in the row's own error surface.
    await waitFor(() => expect(screen.getByText(/Couldn't cancel this download/i)).toBeTruthy())
    // 2. And the row was NOT optimistically cleared — the download is still running server-side.
    expect(cancelButton(), 'the cancel control is still offered').toBeTruthy()
  })

  it('the reported message carries the server’s reason, not a generic string', () => {
    // The panel unwraps a JSON error body the same way `download` does; a bare "Cancel failed" would
    // hide which job the server refused and why.
    const src = readSource()
    expect(src).toMatch(/const p = JSON\.parse\(msg\); msg = p\.error \|\| msg/)
  })

  it('a SUCCESSFUL cancel does clear the row', async () => {
    vi.spyOn(api, 'cancelModelDownload').mockResolvedValue(undefined as never)
    const { onChanged } = mount()
    await waitFor(() => expect(cancelButton()).toBeTruthy())
    fireEvent.click(cancelButton()!)
    // The happy path must still work — a guard that only ever blocks would be worse than the bug.
    await waitFor(() => expect(cancelButton()).toBeFalsy())
    expect(onChanged, 'the model list refreshes').toHaveBeenCalled()
  })

  it('the hook no longer swallows the request', () => {
    const hook = readSource('useModelDownloads.ts')
    expect(hook, 'no bare catch on the cancel request')
      .not.toMatch(/api\.cancelModelDownload\([^)]*\)\.catch\(/)
    expect(hook, 'and the stream closes only after it succeeds')
      .toMatch(/await api\.cancelModelDownload\(job\.id\)\s*\n\s*closeStream\(job\.id\)/)
  })
})

function readSource(file = 'LocalModelManager.tsx'): string {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { readFileSync } = require('node:fs') as typeof import('node:fs')
  const { join } = require('node:path') as typeof import('node:path')
  return readFileSync(join(process.cwd(), 'src/pages/settings', file), 'utf8')
}
