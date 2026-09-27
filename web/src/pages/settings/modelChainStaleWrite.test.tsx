// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import type { ProviderModels } from '../../lib/api'

// ── A use case's model chain is saved over the chain this panel painted ─────────────────────────
//
// Every edit here sends the WHOLE chain — `PUT /api/models/active/{use_case}` replaces it — and it
// was built from the chain this panel read. So a panel opened before another tab, onboarding or a
// provider's removal changed the chain put its old copy straight back. The save now names the
// revision of the chain it painted; a stale one is refused (`409 stale_write`), and the edit — an
// operation on the chain, never the chain — waits in the notice to be re-applied on top of what
// is stored now.

const modelsAvailable = vi.fn()
const activeChains = vi.fn()
const activeChain = vi.fn()
const setActiveModel = vi.fn()

vi.mock('../../lib/api', async (orig) => {
  const actual = await orig<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      modelsAvailable: () => modelsAvailable(),
      activeChains: () => activeChains(),
      activeChain: (u: string) => activeChain(u),
      // The same GET without the revisions, as the readers that never save take it.
      modelsActive: () => activeChains().then((c: Record<string, { value: string[] }>) =>
        Object.fromEntries(Object.entries(c).map(([u, chain]) => [u, chain.value]))),
      setActiveModel: (u: string, m: string[], base: string) => setActiveModel(u, m, base),
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
import { ApiError } from '../../lib/api'
import { HELD_CHANGE_REASON } from '../../lib/staleWrite'

const hosted = (id: string, provider = 'openai') => ({
  id, name: id, capabilities: ['chat'], provider, provider_type: provider,
})
const CATALOG: ProviderModels[] = [
  { name: 'openai', type: 'openai', models: [hosted('gpt-5'), hosted('gpt-4o')] },
  { name: 'anthropic', type: 'anthropic', models: [hosted('claude-x', 'anthropic')] },
]
const stale = () => new ApiError('The chat model chain changed after the copy it was built from was read.', 409, 'stale_write')

beforeEach(() => {
  resetDataStore()
  modelsAvailable.mockReset().mockResolvedValue(CATALOG)
  // Painted: one model, at revision rev-a.
  activeChains.mockReset().mockResolvedValue({ chat: { value: ['openai:gpt-5'], revision: 'rev-a' } })
  // Stored since, by another tab: Claude put first, at rev-b.
  activeChain.mockReset().mockResolvedValue({ value: ['anthropic:claude-x', 'openai:gpt-5'], revision: 'rev-b' })
  setActiveModel.mockReset().mockResolvedValue({ ok: true, revision: 'rev-c' })
})

async function addGpt4oToChat() {
  render(<ModelsPanel />)
  fireEvent.click(await screen.findByRole('button', { name: /^Chat/, expanded: false }))
  fireEvent.click(await screen.findByRole('button', { name: 'gpt-4o' }))
}

describe('the model chain is saved over the chain the panel painted', () => {
  it('a save names the revision of the chain it was built from', async () => {
    await addGpt4oToChat()
    await waitFor(() => expect(setActiveModel).toHaveBeenCalledWith('chat', ['openai:gpt-5', 'openai:gpt-4o'], 'rev-a'))
    expect(screen.queryByText(/changed elsewhere/)).toBeNull()
  })

  it('a chain changed elsewhere is not overwritten: the edit waits in the notice, then lands on top of it', async () => {
    setActiveModel.mockRejectedValueOnce(stale())
    await addGpt4oToChat()
    const notice = await screen.findByRole('alert')
    expect(notice.textContent).toContain('changed elsewhere')
    // Kept, not dropped: the change waits for the user, and no second edit can be built from the
    // same stale copy meanwhile. A model's row says so and refuses the click, keeping its tab stop.
    expect(setActiveModel).toHaveBeenCalledTimes(1)
    const held = screen.getByRole('button', { name: 'claude-x' })
    expect(held.getAttribute('aria-disabled')).toBe('true')
    expect(held.getAttribute('title')).toBe(HELD_CHANGE_REASON)
    fireEvent.click(held)
    expect(setActiveModel).toHaveBeenCalledTimes(1)

    const reapply = await screen.findByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.getAttribute('aria-disabled')).not.toBe('true'))
    fireEvent.click(reapply)
    // The same edit — add gpt-4o — applied to what is stored now, over ITS revision: the other
    // tab's Claude stays first.
    await waitFor(() => expect(setActiveModel).toHaveBeenLastCalledWith(
      'chat', ['anthropic:claude-x', 'openai:gpt-5', 'openai:gpt-4o'], 'rev-b'))
    await waitFor(() => expect(screen.queryByText(/changed elsewhere/)).toBeNull())
  })
})
