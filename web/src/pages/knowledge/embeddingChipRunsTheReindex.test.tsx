// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReindexJob } from '../../lib/api'

// ── The Knowledge page's embedding chip runs the one re-index, and shows it running ─────────────
//
// Its "stale — re-embed" and "embed rest" buttons posted to a second embed loop of their own, which
// re-embedded the item vectors and never their passages, and ran beside the re-index when one was
// already going, as it is through the first start after an update. And while that re-index ran the
// chip showed nothing of it: the cleared items read as "embed rest", an offer to start the same work
// twice. The chip now starts, or joins, the re-index Settings → Models and the gateway's start run,
// and shows its progress while it runs, wherever it was started.

class FakeSource {
  static all: FakeSource[] = []
  listeners: Record<string, ((e: Event) => void)[]> = {}
  onerror: (() => void) | null = null
  constructor(public url: string) { FakeSource.all.push(this) }
  addEventListener(type: string, fn: (e: Event) => void) { (this.listeners[type] ??= []).push(fn) }
  close() {}
  emit(type: string, data: ReindexJob) {
    for (const fn of this.listeners[type] ?? []) fn(new MessageEvent(type, { data: JSON.stringify(data) }))
  }
}

const job = (over: Partial<ReindexJob> = {}): ReindexJob => ({
  id: 'reindex-1', model: 'nomic-embed-text', status: 'running', phase: 'reindexing knowledge',
  done: 3, total: 10, knowledge: 0, memory: 0, error: '', ...over,
})
const stats = (over: Record<string, unknown> = {}) => ({
  items: 5, entities: 0, relations: 0,
  embeddings: { enabled: true, available: true, model: 'nomic-embed-text', embedded_items: 5, stale_items: 2, ...over },
})

const knowledgeStats = vi.fn()
const reindexJobs = vi.fn()
const startReindex = vi.fn()

function mockApi() {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      knowledgeStats: () => knowledgeStats(),
      knowledgeItems: () => Promise.resolve({ items: [] }),
      knowledgeCollections: () => Promise.resolve([]),
      knowledgeIntents: () => Promise.resolve([]),
      embeddingReindexJobs: () => reindexJobs(),
      startEmbeddingReindex: () => startReindex(),
      embeddingReindexStreamUrl: (id: string) => `/api/models/embedding/reindex/${id}/stream`,
    },
  }))
}

async function mount() {
  const { KnowledgeListPage } = await import('./KnowledgeListPage')
  render(<KnowledgeListPage query={{}} setQuery={() => {}} onCreate={() => {}} onOpenItem={() => {}}
    onOpenSources={() => {}} onOpenReports={() => {}} onOpenChat={() => {}} />)
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  FakeSource.all = []
  vi.stubGlobal('EventSource', FakeSource)
  knowledgeStats.mockReset().mockResolvedValue(stats())
  reindexJobs.mockReset().mockResolvedValue({ jobs: [], active: null })
  startReindex.mockReset().mockResolvedValue(job({ done: 0 }))
  mockApi()
})
afterEach(() => { vi.unstubAllGlobals(); vi.doUnmock('../../lib/api') })

describe('the embedding chip is the one re-index', () => {
  it('its re-embed starts the re-index, and it shows the job from then on', async () => {
    await mount()

    fireEvent.click(await screen.findByRole('button', { name: /stale — re-embed/ }))

    await waitFor(() => expect(startReindex).toHaveBeenCalledTimes(1))
    expect(await screen.findByText('re-embedding')).toBeTruthy()
    expect(screen.getByText('0/10')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /re-embed|embed rest/ }), 'no second start offered').toBeNull()
  })

  it('shows a re-index the gateway started, with no offer to start it again', async () => {
    reindexJobs.mockResolvedValue({ jobs: [job()], active: job() })
    knowledgeStats.mockResolvedValue(stats({ embedded_items: 1, stale_items: 0 }))  // cleared items
    await mount()

    expect(await screen.findByText('re-embedding')).toBeTruthy()
    expect(screen.getByText('3/10')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /embed rest|re-embed/ })).toBeNull()
    expect(startReindex).not.toHaveBeenCalled()
  })

  it('re-reads the counts when the re-index ends', async () => {
    reindexJobs.mockResolvedValue({ jobs: [job()], active: job() })
    await mount()
    await screen.findByText('re-embedding')
    const reads = knowledgeStats.mock.calls.length
    knowledgeStats.mockResolvedValue(stats({ stale_items: 0 }))

    act(() => FakeSource.all[0].emit('done', job({ status: 'done', phase: 'done', done: 10 })))

    await waitFor(() => expect(knowledgeStats.mock.calls.length).toBeGreaterThan(reads))
    expect(await screen.findByText('embedded')).toBeTruthy()
  })

  it('a refused start is said on the control that retries it', async () => {
    startReindex.mockRejectedValue(new Error(JSON.stringify({ error: 'The selected embedding model is not available.' })))
    await mount()

    fireEvent.click(await screen.findByRole('button', { name: /stale — re-embed/ }))

    const retry = await screen.findByRole('button', { name: /re-embed stopped — retry/ })
    expect(retry.getAttribute('title')).toMatch(/^The last re-index stopped: The selected embedding model is not available\./)
  })
})

describe('the click is answered at once', () => {
  it('says it is starting until the route answers, and cannot be pressed twice', async () => {
    let answer: (j: ReindexJob) => void = () => {}
    startReindex.mockReturnValue(new Promise<ReindexJob>((r) => { answer = r }))
    await mount()

    fireEvent.click(await screen.findByRole('button', { name: /stale — re-embed/ }))

    const pending = await screen.findByRole('button', { name: /starting…/ })
    expect(pending.hasAttribute('disabled')).toBe(true)
    expect(pending.getAttribute('aria-busy')).toBe('true')
    await act(async () => { answer(job({ done: 0 })) })
    expect(await screen.findByText('re-embedding')).toBeTruthy()
    expect(startReindex).toHaveBeenCalledTimes(1)
  })
})
