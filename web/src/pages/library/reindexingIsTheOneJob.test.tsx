/** The library's Re-index is the one re-index, and shows it running.
 *
 *  The old Knowledge page's embedding chip once posted to an embed loop of its own, which re-embedded
 *  item vectors and never their passages and ran beside the re-index when one was going; and while
 *  that re-index ran the chip showed nothing of it, so the cleared items read as an offer to start the
 *  same work twice. Its place is Library settings' Search by meaning row, and the "…" row Re-index
 *  search by meaning while items wait (#6101): both start, or join, the re-index Settings › Models and
 *  the gateway's start run, and show its progress wherever it was started. */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { answer, answers, calls, resetGateway, route, row } from '../../test/libraryHarness'
import type { ReindexJob } from '../../lib/api'

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

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../test/libraryHarness')
  const kit = gatewayApi() as Record<string, unknown>
  // The stream's URL is a plain string, not a read.
  const api = new Proxy({}, { get: (_t, m: string) => (m === 'embeddingReindexStreamUrl' ? (id: string) => `/api/models/embedding/reindex/${id}/stream` : kit[m]) })
  return { ...(await orig<typeof import('../../lib/api')>()), api }
})
const { LibraryPage } = await import('./LibraryPage')
const { resetDataStore } = await import('../../lib/data/store')

const job = (over: Partial<ReindexJob> = {}): ReindexJob => ({
  id: 'reindex-1', model: 'nomic-embed-text', status: 'running', phase: 'reindexing knowledge',
  done: 3, total: 10, knowledge: 0, memory: 0, error: '', ...over,
})
const stats = (over: Record<string, unknown> = {}) => ({
  items: 5, entities: 0, relations: 0,
  embeddings: { enabled: true, available: true, model: 'nomic-embed-text', embedded_items: 3, stale_items: 2, ...over },
})

beforeEach(() => {
  resetDataStore(); resetGateway(); localStorage.clear(); sessionStorage.clear()
  FakeSource.all = []
  vi.stubGlobal('EventSource', FakeSource)
  answers.library = answer([row('knowledge:k1', 'Router manual')], { facets: { kind: { notes: 5 } } })
  answers.knowledgeStats = stats()
  answers.knowledgeConflicts = { conflicts: [] }
  answers.librarySettings = { power_user_view: true }
  answers.embeddingReindexJobs = { jobs: [], active: null }
  answers.startEmbeddingReindex = job({ done: 0 })
  answers.knowledgeIntents = { intents: [] }
  answers.researchReports = { reports: [] }
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function mount(query: Record<string, string> = { view: 'settings' }) {
  const r = route(query)
  render(<LibraryPage sub="" navEpoch={0} query={r.state.query} setQuery={r.setQuery} navigate={r.navigate} />)
}
const reindexButton = () => screen.findByRole('button', { name: 'Re-index' })

describe("Library settings' Re-index is the one re-index", () => {
  it('🔑 starts the re-index, and shows the job from then on', async () => {
    mount()
    fireEvent.click(await reindexButton())
    await waitFor(() => expect(calls.startEmbeddingReindex).toHaveLength(1))
    expect(await screen.findByText('Re-indexing · 0 of 10')).toBeTruthy()
    const again = screen.getByRole('button', { name: 'Re-index' })
    expect(again.getAttribute('title') ?? '', 'no second start offered').toContain("It's re-indexing now.")
  })

  it('🔑 shows a re-index the gateway started, with no offer to start it again', async () => {
    answers.embeddingReindexJobs = { jobs: [job()], active: job() }
    answers.knowledgeStats = stats({ embedded_items: 1, stale_items: 0 })
    mount()
    expect(await screen.findByText('Re-indexing · 3 of 10')).toBeTruthy()
    expect((await reindexButton()).getAttribute('title') ?? '').toContain("It's re-indexing now.")
    expect(calls.startEmbeddingReindex).toBeUndefined()
  })

  it('🔑 re-reads the counts when the re-index ends', async () => {
    answers.embeddingReindexJobs = { jobs: [job()], active: job() }
    mount()
    await screen.findByText('Re-indexing · 3 of 10')
    const reads = calls.knowledgeStats.length
    answers.knowledgeStats = stats({ embedded_items: 5, stale_items: 0 })
    act(() => FakeSource.all[0].emit('done', job({ status: 'done', phase: 'done', done: 10 })))
    await waitFor(() => expect(calls.knowledgeStats.length).toBeGreaterThan(reads))
    expect(await screen.findByText('On · 5 of 5 ready.')).toBeTruthy()
  })

  it('a refused start is said on its row', async () => {
    answers.startEmbeddingReindex = new Error(JSON.stringify({ error: 'The selected embedding model is not available.' }))
    mount()
    fireEvent.click(await reindexButton())
    expect(await screen.findByText('Re-index not started: The selected embedding model is not available.')).toBeTruthy()
  })

  it('says it is starting until the route answers, and cannot be pressed twice', async () => {
    let done: (j: ReindexJob) => void = () => {}
    answers.startEmbeddingReindex = () => new Promise<ReindexJob>((r) => { done = r })
    mount()
    fireEvent.click(await reindexButton())
    const pending = screen.getByRole('button', { name: 'Re-index' })
    expect(pending.getAttribute('title') ?? '').toContain('Starting…')
    fireEvent.click(pending)
    await act(async () => { done(job({ done: 0 })) })
    expect(await screen.findByText('Re-indexing · 0 of 10')).toBeTruthy()
    expect(calls.startEmbeddingReindex).toHaveLength(1)
  })
})

describe("the regular view's \"…\" offers the same re-index while items wait", () => {
  it('🔑 Re-index search by meaning starts the one job', async () => {
    answers.librarySettings = { power_user_view: false }
    mount({})
    await screen.findByRole('button', { name: /^Router manual/ })
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    fireEvent.click(await screen.findByText('Re-index search by meaning'))
    await waitFor(() => expect(calls.startEmbeddingReindex).toHaveLength(1))
  })

  it('is not offered when nothing waits', async () => {
    answers.librarySettings = { power_user_view: false }
    answers.knowledgeStats = stats({ embedded_items: 5, stale_items: 0 })
    mount({})
    await screen.findByRole('button', { name: /^Router manual/ })
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    await screen.findByText('Read missed items')
    expect(screen.queryByText('Re-index search by meaning')).toBeNull()
  })
})
