// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import type { ProviderModels, ReindexJob } from '../../lib/api'

// ── Settings → Models shows the re-index the gateway started ────────────────────────────────────
//
// The Embedding row rendered a re-index only when its own save had started one. The gateway starts
// one itself, at its start (the first start after an update re-embeds every knowledge item once)
// and after a binding made anywhere, so opening Settings → Models in the middle of one showed no
// re-index at all. The row now reads the running job when it mounts and follows it.

const reindexJobs = vi.fn()

vi.mock('../../lib/api', async (orig) => {
  const actual = await orig<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      modelsAvailable: () => Promise.resolve(CATALOG),
      activeChains: () => Promise.resolve(CHAINS),
      activeChain: (u: string) => Promise.resolve(CHAINS[u as keyof typeof CHAINS]),
      modelsActive: () => Promise.resolve({ embedding: CHAINS.embedding.value }),
      setActiveModel: () => Promise.resolve({ ok: true, revision: 'rev-b' }),
      startEmbeddingReindex: () => new Promise(() => {}),
      embeddingReindexJobs: () => reindexJobs(),
      embeddingReindexStreamUrl: (id: string) => `/api/models/embedding/reindex/${id}/stream`,
      modelDownloads: () => Promise.resolve([]),
      modelsHealth: () => Promise.resolve({ providers: [] }),
      judgeBench: () => Promise.reject(new actual.ApiError('none', 404, 'judge_bench_absent')),
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

import { ModelsPanel } from './ModelsPanel'
import { resetDataStore } from '../../lib/data'

const CATALOG: ProviderModels[] = [{
  name: 'my-ollama', type: 'ollama',
  models: [{ id: 'embed-a:1', name: 'embed-a:1', capabilities: ['embedding'], provider: 'my-ollama', provider_type: 'ollama' }],
}]
const CHAINS = { embedding: { value: ['my-ollama:embed-a:1'], revision: 'rev-a' } }

class FakeSource {
  static urls: string[] = []
  static last: FakeSource | null = null
  listeners: Record<string, ((e: Event) => void)[]> = {}
  constructor(url: string) { FakeSource.urls.push(url); FakeSource.last = this }
  addEventListener(type: string, fn: (e: Event) => void) { (this.listeners[type] ??= []).push(fn) }
  close() {}
  onerror: (() => void) | null = null
  emit(type: string, data: ReindexJob) {
    for (const fn of this.listeners[type] ?? []) fn(new MessageEvent(type, { data: JSON.stringify(data) }))
  }
}

const running: ReindexJob = {
  id: 'reindex-1', model: 'embed-a:1', status: 'running', phase: 'reindexing knowledge',
  done: 3, total: 10, knowledge: 0, memory: 0, error: '',
}

beforeEach(() => {
  resetDataStore()
  FakeSource.urls = []
  vi.stubGlobal('EventSource', FakeSource)
  reindexJobs.mockReset().mockResolvedValue({ jobs: [running], active: running })
})
afterEach(() => { vi.unstubAllGlobals() })

describe('the Embedding row follows a re-index it did not start', () => {
  it('shows its phase and progress on open, and follows its feed', async () => {
    render(<ModelsPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /^Embedding/, expanded: false }))

    expect(await screen.findByText('Re-indexing embeddings — reindexing knowledge (3/10)')).toBeTruthy()
    expect(FakeSource.urls).toEqual(['/api/models/embedding/reindex/reindex-1/stream'])
    // Read once, by the Embedding row: every other use case's row reads nothing.
    expect(reindexJobs).toHaveBeenCalledTimes(1)
  })

  it('shows nothing when none is running', async () => {
    reindexJobs.mockResolvedValue({ jobs: [], active: null })
    render(<ModelsPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /^Embedding/, expanded: false }))

    await screen.findByRole('button', { name: 'embed-a:1' })
    expect(screen.queryByText(/Re-indexing embeddings/)).toBeNull()
  })
})

describe('the finished re-index says what it re-embedded', () => {
  it('counts the passages beside the items and the memories', async () => {
    const done: ReindexJob = { ...running, status: 'done', phase: 'done', done: 7, total: 7, knowledge: 3, memory: 2, chunks: 2 }
    reindexJobs.mockResolvedValue({ jobs: [running], active: running })
    render(<ModelsPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /^Embedding/, expanded: false }))
    await screen.findByText(/Re-indexing embeddings/)

    act(() => FakeSource.last?.emit('done', done))

    expect(await screen.findByText('Re-indexed 3 knowledge + 2 passage + 2 memory embeddings.')).toBeTruthy()
  })
})
