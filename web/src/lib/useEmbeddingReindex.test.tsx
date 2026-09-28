// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReindexJob } from './api'

// ── The one embedding re-index is seen wherever it was started ──────────────────────────────────
//
// The gateway starts the re-index on its own: at its start, for whatever the bound model has not
// embedded (the first start after an update re-embeds every knowledge item once), and after an
// Embedding binding. Settings → Models showed only a job its own save had started, and the
// Knowledge page none at all, so through that first start nothing on screen said the library was
// being re-embedded, or why semantic search found less meanwhile. The hook reads the running job on
// mount and follows it; a start joins the running job, since the route answers it.

const jobs = vi.fn()
const startReindex = vi.fn()

vi.mock('./api', async (orig) => {
  const actual = await orig<typeof import('./api')>()
  return {
    ...actual,
    api: {
      embeddingReindexJobs: () => jobs(),
      startEmbeddingReindex: () => startReindex(),
      embeddingReindexStreamUrl: (id: string) => `/api/models/embedding/reindex/${id}/stream`,
    },
  }
})

import { useEmbeddingReindex } from './useEmbeddingReindex'

/** The SSE stream, driven by the test: `emit` delivers one frame the way the gateway sends it. */
class FakeSource {
  static all: FakeSource[] = []
  listeners: Record<string, ((e: Event) => void)[]> = {}
  onerror: (() => void) | null = null
  closed = false
  constructor(public url: string) { FakeSource.all.push(this) }
  addEventListener(type: string, fn: (e: Event) => void) { (this.listeners[type] ??= []).push(fn) }
  close() { this.closed = true }
  emit(type: string, data: ReindexJob) {
    for (const fn of this.listeners[type] ?? []) fn(new MessageEvent(type, { data: JSON.stringify(data) }))
  }
}

const job = (over: Partial<ReindexJob> = {}): ReindexJob => ({
  id: 'reindex-1', model: 'nomic-embed-text', status: 'running', phase: 'reindexing knowledge',
  done: 3, total: 10, knowledge: 0, memory: 0, error: '', ...over,
})

function Probe({ enabled = true, onSettled }: { enabled?: boolean; onSettled?: () => void }) {
  const { job: j, start } = useEmbeddingReindex({ enabled, onSettled })
  return (
    <div>
      <p data-testid="job">{j ? `${j.id}|${j.status}|${j.phase}|${j.done}/${j.total}|${j.error}` : 'none'}</p>
      <button type="button" onClick={start}>start</button>
    </div>
  )
}

const shown = () => screen.getByTestId('job').textContent

beforeEach(() => {
  FakeSource.all = []
  vi.stubGlobal('EventSource', FakeSource)
  jobs.mockReset().mockResolvedValue({ jobs: [], active: null })
  startReindex.mockReset()
})
afterEach(() => { vi.unstubAllGlobals() })

describe('a re-index the gateway started is followed from mount', () => {
  it('shows the running job and its progress, and says when it ends', async () => {
    jobs.mockResolvedValue({ jobs: [job()], active: job() })
    const settled = vi.fn()
    render(<Probe onSettled={settled} />)

    await waitFor(() => expect(shown()).toBe('reindex-1|running|reindexing knowledge|3/10|'))
    const [source] = FakeSource.all
    expect(source.url).toBe('/api/models/embedding/reindex/reindex-1/stream')

    act(() => source.emit('progress', job({ phase: 'reindexing passages', done: 7 })))
    expect(shown()).toBe('reindex-1|running|reindexing passages|7/10|')
    expect(settled).not.toHaveBeenCalled()

    act(() => source.emit('done', job({ status: 'done', phase: 'done', done: 10 })))
    expect(shown()).toBe('reindex-1|done|done|10/10|')
    expect(settled).toHaveBeenCalledTimes(1)
    expect(source.closed).toBe(true)
  })

  it('shows nothing when none is running', async () => {
    render(<Probe />)
    await waitFor(() => expect(jobs).toHaveBeenCalledTimes(1))
    expect(shown()).toBe('none')
    expect(FakeSource.all).toEqual([])
  })

  it('reads nothing on a host that does not show it', async () => {
    render(<Probe enabled={false} />)
    await act(async () => { await Promise.resolve() })
    expect(jobs).not.toHaveBeenCalled()
  })

  it('a lost feed says the job may still be running, not that it failed', async () => {
    jobs.mockResolvedValue({ jobs: [job()], active: job() })
    render(<Probe />)
    await waitFor(() => expect(FakeSource.all).toHaveLength(1))

    act(() => FakeSource.all[0].onerror?.())

    expect(shown()).toMatch(/^reindex-1\|error\|.*Lost the progress feed — the re-index may still be running/)
  })
})

describe('a start joins the one re-index', () => {
  it('follows the job the route answers, the running one included', async () => {
    startReindex.mockResolvedValue(job({ id: 'reindex-2', done: 0 }))
    render(<Probe />)
    await waitFor(() => expect(jobs).toHaveBeenCalled())

    fireEvent.click(screen.getByRole('button', { name: 'start' }))

    await waitFor(() => expect(shown()).toBe('reindex-2|running|reindexing knowledge|0/10|'))
    expect(FakeSource.all.map((s) => s.url)).toEqual(['/api/models/embedding/reindex/reindex-2/stream'])
  })

  it('a refused start says why, as a job that never existed', async () => {
    startReindex.mockRejectedValue(new Error(JSON.stringify({
      error: 'The selected embedding model is not available.', code: 'model_not_ready',
    })))
    render(<Probe />)
    await waitFor(() => expect(jobs).toHaveBeenCalled())

    fireEvent.click(screen.getByRole('button', { name: 'start' }))

    await waitFor(() => expect(shown()).toBe('|error|error|0/0|The selected embedding model is not available.'))
  })
})
