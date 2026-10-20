// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, within, fireEvent } from '@testing-library/react'
import type { ProviderHealth, ProviderModels } from '../../lib/api'

// ── A chain entry that cannot run says so, in words, beside the entry ──────────────────────────
//
// Measured on a live install, Settings › Models › Background:
//   * with its first instance down, the entry's only sign was a dot whose tooltip read
//     "recovering — next call probes it", although every call to it was failing;
//   * with the third entry's model gone from its instance, that entry had no mark at all.
// The page had both facts: the models read carries each instance's listing error and measured
// connection, and the model list says what each local instance still has.

const modelsAvailable = vi.fn()
const activeChains = vi.fn()
const modelsHealth = vi.fn()

vi.mock('../../lib/api', async (orig) => {
  const actual = await orig<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      modelsAvailable: () => modelsAvailable(),
      activeChains: () => activeChains(),
      activeChain: () => activeChains().then((c: Record<string, unknown>) => c.background),
      setActiveModel: () => Promise.resolve({ ok: true, revision: 'r2' }),
      modelDownloads: () => Promise.resolve([]),
      modelsHealth: () => modelsHealth(),
      embeddingReindexJobs: () => Promise.resolve({ jobs: [], active: null }),
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

import { ModelsPanel, chainEntryStatus, providerListings } from './ModelsPanel'
import { resetDataStore } from '../../lib/data'

const REFUSED = 'Could not reach http://127.0.0.1:11435 — the connection was refused. Check the URL and that the service is running.'

const local = (id: string, provider: string) => ({
  id, name: id, capabilities: ['chat'], provider, provider_type: provider, downloaded: true,
})
const connected = { state: 'connected' as const, detail: 'Connected — 1 model(s) available', rejected_credential: false, checked_at: 1 }

/** The Background chain's three instances, the first down and the third without its model. */
const CATALOG: ProviderModels[] = [
  { name: 'ollama-bg', type: 'ollama-bg', local: true, models: [], error: REFUSED, connection: connected },
  { name: 'ollama', type: 'ollama', local: true, models: [local('gemma4:12b', 'ollama')], connection: connected },
  { name: 'ollama-small', type: 'ollama-small', local: true, models: [local('fake-llm:1b', 'ollama-small')], connection: connected },
]
const CHAIN = ['ollama-bg:gemma4:12b', 'ollama:gemma4:12b', 'ollama-small:qwen3:1.7b']

function health(over: Partial<ProviderHealth>): ProviderHealth {
  return {
    name: 'ollama-bg', breaker_state: 'closed', consecutive_failures: 0, calls: 0, passed: 0, failed: 0,
    pass_rate: null, p50_ms: 0, p90_ms: 0, p99_ms: 0, failure_modes: {}, degraded: false, ...over,
  }
}

describe('chainEntryStatus', () => {
  const listings = providerListings(CATALOG)

  it('an instance whose models could not be listed is not answering, in its own words', () => {
    expect(chainEntryStatus('ollama-bg:gemma4:12b', listings['ollama-bg'], undefined, undefined))
      .toEqual({ tone: 'danger', label: 'not answering', detail: REFUSED })
  })

  it('a measured connection failure says so, and a refused key is named as one', () => {
    const failed = { error: '', local: false, listed: [], connection: { ...connected, state: 'failed' as const, detail: 'The endpoint answered HTTP 401.', rejected_credential: true } }
    expect(chainEntryStatus('vendor:m', failed, undefined, undefined)?.label).toBe('key rejected')
    const down = { ...failed, connection: { ...failed.connection, rejected_credential: false, detail: REFUSED } }
    expect(chainEntryStatus('vendor:m', down, undefined, undefined)).toEqual({ tone: 'danger', label: 'not answering', detail: REFUSED })
  })

  it('a tripped breaker is failing, half-open included — it is not recovering', () => {
    const ok = listings.ollama
    const halfOpen = chainEntryStatus('ollama:gemma4:12b', ok, health({ name: 'ollama', breaker_state: 'half_open', consecutive_failures: 7 }), undefined)
    expect(halfOpen?.label).toBe('failing')
    expect(halfOpen?.detail).toBe('ollama: its last 7 calls failed. The next call to it tests whether it answers again.')
    const open = chainEntryStatus('ollama:gemma4:12b', ok, health({ name: 'ollama', breaker_state: 'open', consecutive_failures: 5 }), undefined)
    expect(open?.detail).toContain('It is skipped until it answers again')
  })

  it('a model a local instance no longer lists is unavailable, and says where to prune it', () => {
    expect(chainEntryStatus('ollama-small:qwen3:1.7b', listings['ollama-small'], undefined, undefined)).toEqual({
      tone: 'danger', label: 'unavailable',
      detail: 'ollama-small no longer lists qwen3:1.7b, so it cannot run. Remove it here, or prune it from Settings → Doctor.',
    })
  })

  it('a hosted provider is never judged by its list: it can serve a model it does not list', () => {
    const hosted = { error: '', local: false, listed: ['gpt-4o'], connection: connected }
    expect(chainEntryStatus('openai:gpt-5', hosted, undefined, undefined)).toBeNull()
  })

  it('a local model it lists but has not downloaded will not run until it is', () => {
    const listing = { error: '', local: true, listed: ['base.en'], connection: undefined }
    expect(chainEntryStatus('faster-whisper:base.en', listing, undefined, { ...local('base.en', 'faster-whisper'), downloaded: false })?.label)
      .toBe('not downloaded')
  })

  it('a model its provider lists for other jobs cannot do this one, and says what it is for', () => {
    // Bound before the gateway refused such a binding: an embedding model and a safety classifier
    // its provider lists for nothing, each in a chat chain.
    const embedder = { ...local('nomic-embed-text:latest', 'ollama'), capabilities: ['embedding'] }
    expect(chainEntryStatus('ollama:nomic-embed-text:latest', listings.ollama, undefined, embedder, 'background')).toEqual({
      tone: 'danger', label: 'cannot do this',
      detail: 'ollama lists nomic-embed-text:latest for Embedding, not for Chat, so it cannot run here. Remove it here.',
    })
    const guard = { ...local('llama-guard3:8b', 'ollama'), capabilities: [] }
    expect(chainEntryStatus('ollama:llama-guard3:8b', listings.ollama, undefined, guard, 'chat')?.detail)
      .toBe('ollama lists llama-guard3:8b for nothing PersonalClaw can use it for, so it cannot run here. Remove it here.')
    // A model that does the row's job is judged by everything else, as before.
    expect(chainEntryStatus('ollama:gemma4:12b', listings.ollama, undefined, local('gemma4:12b', 'ollama'), 'reasoning')).toBeNull()
  })

  it('a healthy entry says nothing', () => {
    expect(chainEntryStatus('ollama:gemma4:12b', listings.ollama, health({ name: 'ollama' }), local('gemma4:12b', 'ollama'))).toBeNull()
  })

  it("leaves a media row out: why a provider cannot generate says nothing of its chat models", () => {
    const rows: ProviderModels[] = [
      { name: 'vendor', type: 'vendor', models: [{ ...local('chat-1', 'vendor'), downloaded: undefined }] },
      { name: 'vendor', type: 'image_gen', models: [], error: 'No image model is configured.' },
    ]
    expect(providerListings(rows).vendor).toEqual({ error: '', connection: undefined, local: false, listed: ['chat-1'] })
  })
})

describe('Settings › Models › Background', () => {
  beforeEach(() => {
    resetDataStore()
    modelsAvailable.mockReset().mockResolvedValue(CATALOG)
    activeChains.mockReset().mockResolvedValue({ background: { value: CHAIN, revision: 'r1' } })
    modelsHealth.mockReset().mockResolvedValue({
      providers: [health({ breaker_state: 'half_open', consecutive_failures: 7 }), health({ name: 'ollama' })],
    })
  })

  it('shows the down instance and the gone model on their own entries, in words', async () => {
    render(<ModelsPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /^Background/, expanded: false }))

    const entry = async (label: string) => (await screen.findByText(label, { selector: 'span.uppercase' })).parentElement!
    const first = await entry('default')
    expect(within(first).getByText(/not answering/)).toBeTruthy()
    expect(first.textContent).toContain(REFUSED)
    // The dot no longer reads "recovering" for an instance whose calls all failed.
    expect(within(first).getByRole('img').getAttribute('aria-label')).toBe(
      'ollama-bg: failing — its last 7 calls failed; the next call tests it again')

    const second = await entry('fallback 1')
    expect(second.textContent).not.toMatch(/not answering|failing|unavailable/)

    const third = await entry('fallback 2')
    expect(within(third).getByText(/unavailable/)).toBeTruthy()
    expect(third.textContent).toContain('ollama-small no longer lists qwen3:1.7b')
  })

  it("the picker says the down instance's model is not answering, not that it is not downloaded", async () => {
    render(<ModelsPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /^Background/, expanded: false }))
    // The picker row of the model bound on the down instance (its instance listed nothing).
    const toggle = (await screen.findAllByRole('button', { name: 'gemma4:12b' }))
      .find((b) => b.closest('div.flex-col')?.textContent?.includes('ollama-bg'))!
    const row = toggle.closest('div.flex-col') as HTMLElement
    expect(row.textContent).toContain('not answering')
    expect(row.textContent).not.toContain('not downloaded')
    expect(within(row).queryByRole('button', { name: /download/i }), 'a Download offered to an instance that is down').toBeNull()
  })
})
