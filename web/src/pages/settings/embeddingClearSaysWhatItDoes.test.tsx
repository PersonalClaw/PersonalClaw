// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import type { ProviderModels } from '../../lib/api'

// ── Clearing Embedding asks about a clear, not a change ─────────────────────────────────────────
//
// Clicking the bound embedding model off asked "Change the embedding model?" and warned that it
// "will re-index ALL knowledge and memories", with a "Change & re-index" button. Nothing
// re-indexed: the save starts the re-index only once a model is bound. The clear now says what it
// does — search matches by keyword until a model is chosen again — and a change still warns about
// the re-index it starts.

const modelsAvailable = vi.fn()
const activeChains = vi.fn()
const setActiveModel = vi.fn()
const startEmbeddingReindex = vi.fn()
const confirmDialog = vi.fn()

vi.mock('../../lib/api', async (orig) => {
  const actual = await orig<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      modelsAvailable: () => modelsAvailable(),
      activeChains: () => activeChains(),
      activeChain: (u: string) => activeChains().then((c: Record<string, unknown>) => c[u]),
      modelsActive: () => activeChains().then((c: Record<string, { value: string[] }>) =>
        Object.fromEntries(Object.entries(c).map(([u, chain]) => [u, chain.value]))),
      setActiveModel: (u: string, m: string[], base: string) => setActiveModel(u, m, base),
      startEmbeddingReindex: () => startEmbeddingReindex(),
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
vi.mock('../../ui/dialog', async (orig) => ({
  ...(await orig<typeof import('../../ui/dialog')>()),
  confirm: (...a: unknown[]) => confirmDialog(...a),
}))

import { ModelsPanel } from './ModelsPanel'
import { resetDataStore } from '../../lib/data'

const embedder = (id: string) => ({
  id, name: id, capabilities: ['embedding'], provider: 'my-ollama', provider_type: 'ollama',
})
const CATALOG: ProviderModels[] = [
  { name: 'my-ollama', type: 'ollama', models: [embedder('embed-a:1'), embedder('embed-b:1')] },
]

beforeEach(() => {
  resetDataStore()
  modelsAvailable.mockReset().mockResolvedValue(CATALOG)
  activeChains.mockReset().mockResolvedValue({ embedding: { value: ['my-ollama:embed-a:1'], revision: 'rev-a' } })
  setActiveModel.mockReset().mockResolvedValue({ ok: true, revision: 'rev-b' })
  startEmbeddingReindex.mockReset().mockReturnValue(new Promise(() => {}))
  confirmDialog.mockReset().mockResolvedValue(true)
})

async function clickInTheEmbeddingCard(model: string) {
  render(<ModelsPanel />)
  fireEvent.click(await screen.findByRole('button', { name: /^Embedding/, expanded: false }))
  fireEvent.click(await screen.findByRole('button', { name: model }))
}

describe('the embedding confirm says what the save will do', () => {
  it('clearing the bound model asks about a clear, and re-indexes nothing', async () => {
    await clickInTheEmbeddingCard('embed-a:1')
    await waitFor(() => expect(setActiveModel).toHaveBeenCalledWith('embedding', [], 'rev-a'))

    const [asked] = confirmDialog.mock.calls[0] as [{ title: string; body: string; confirmLabel: string }]
    expect(asked.title).toBe('Stop using an embedding model?')
    expect(asked.confirmLabel).toBe('Clear')
    expect(asked.body).toContain('match by keyword')
    expect(asked.body).not.toMatch(/will re-index/i)
    expect(startEmbeddingReindex).not.toHaveBeenCalled()
  })

  it('choosing another model still warns about the re-index it starts', async () => {
    await clickInTheEmbeddingCard('embed-b:1')
    await waitFor(() => expect(setActiveModel).toHaveBeenCalledWith('embedding', ['my-ollama:embed-b:1'], 'rev-a'))

    const [asked] = confirmDialog.mock.calls[0] as [{ title: string; confirmLabel: string }]
    expect(asked.title).toBe('Change the embedding model?')
    expect(asked.confirmLabel).toBe('Change & re-index')
    await waitFor(() => expect(startEmbeddingReindex).toHaveBeenCalledTimes(1))
  })
})
