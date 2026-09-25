import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { KnowledgeListPage } from './KnowledgeListPage'
import { failedEnrichment, regenerateQueuedSentence } from './knowledgeMeta'
import { api, ApiError, type KnowledgeEnrichment, type KnowledgeItem } from '../../lib/api'
import { resetDataStore } from '../../lib/data/store'

// ── "Regenerate intelligence" tells the user what happened (B6, day-7 live validation) ─────────────
//
// Measured on a home with NO model bound: the request answered 200 `{"queued": 3}`, all three jobs
// failed in the background, and the page showed nothing — `regenerate()` swallowed the result
// ("surfaced by reload", and the reload surfaced nothing). The graph kept saying the items "have not
// been through entity extraction" while each item's own page said Insights and Entities failed.
//
// Mounted through the REAL page, so what is asserted is what a user sees: the refusal's sentence in
// a toast, an accepted run's count, the no-model fix offered up front, and a failed enrichment on the
// row itself — including an `unsearchable` row, the status no badge used to know.

const SERVER_SENTENCE =
  'No model is set up, so there is nothing to extract insights or entities with. Connect a model in Settings → Models, then regenerate.'

function statsWith(enrichment: KnowledgeEnrichment) {
  return { items: 2, entities: 0, relations: 0, embeddings: { enabled: false }, enrichment }
}

function mount(view: string, extra: { onOpenModels?: () => void } = {}) {
  render(<KnowledgeListPage onCreate={() => {}} onOpenItem={() => {}} onOpenReader={() => {}}
    onOpenSources={() => {}} onOpenReports={() => {}} onOpenChat={() => {}} {...extra}
    query={{ view }} setQuery={vi.fn()} />)
}

function captureToasts() {
  const toasts: { message: string; level: string }[] = []
  const on = (e: Event) => {
    const d = (e as CustomEvent).detail ?? {}
    toasts.push({ message: String(d.message ?? ''), level: String(d.level ?? '') })
  }
  window.addEventListener('ne:toast', on)
  return { toasts, stop: () => window.removeEventListener('ne:toast', on) }
}

const originalFetch = globalThis.fetch

beforeEach(() => {
  resetDataStore()
  localStorage.clear()
  vi.restoreAllMocks()
  // The graph reads its own endpoint through `fetch`; an unambiguous empty 200.
  globalThis.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ nodes: [], edges: [] }) })) as never
  vi.spyOn(api, 'knowledgeItems').mockResolvedValue({ items: [], total: 0, page: 1, limit: 100 } as never)
  vi.spyOn(api, 'knowledgeCollections').mockResolvedValue([])
})
afterEach(() => { globalThis.fetch = originalFetch })

describe('regenerate says what happened', () => {
  it('a refusal reaches the user in the server’s own words — nothing is swallowed', async () => {
    vi.spyOn(api, 'knowledgeStats').mockResolvedValue(
      statsWith({ model_available: true, entities: { ran: 0, failed: 0, running: 0, skipped: 0, not_run: 2 } }) as never)
    const regen = vi.spyOn(api, 'regenerateKnowledgeIntelligence')
      .mockRejectedValue(new ApiError(SERVER_SENTENCE, 409, 'model_unresolved'))
    const { toasts, stop } = captureToasts()
    try {
      mount('graph')
      await userEvent.click(await screen.findByRole('button', { name: /Regenerate intelligence/i }))
      await waitFor(() => expect(regen).toHaveBeenCalledWith('missing'))
      await waitFor(() => expect(toasts.map((t) => t.message).join('|')).toContain(SERVER_SENTENCE))
      expect(toasts.find((t) => t.message.includes(SERVER_SENTENCE))?.level).toBe('error')
      expect(toasts[0].message).toMatch(/^Couldn't regenerate intelligence/)
    } finally { stop() }
  })

  it('an accepted run says how many items it queued', async () => {
    vi.spyOn(api, 'knowledgeStats').mockResolvedValue(
      statsWith({ model_available: true, entities: { ran: 0, failed: 2, running: 0, skipped: 0, not_run: 0 } }) as never)
    vi.spyOn(api, 'regenerateKnowledgeIntelligence').mockResolvedValue({ queued: 2, scope: 'missing' })
    const { toasts, stop } = captureToasts()
    try {
      mount('graph')
      await userEvent.click(await screen.findByRole('button', { name: /Regenerate intelligence/i }))
      await waitFor(() => expect(toasts.map((t) => t.message)).toContain(regenerateQueuedSentence(2)))
    } finally { stop() }
    expect(regenerateQueuedSentence(2)).toMatch(/2 items/)
    expect(regenerateQueuedSentence(0)).toMatch(/^Nothing to regenerate/)
  })

  it('with no model set up, the graph says so BEFORE anything is queued, and offers the fix', async () => {
    vi.spyOn(api, 'knowledgeStats').mockResolvedValue(
      statsWith({ model_available: false, entities: { ran: 0, failed: 2, running: 0, skipped: 0, not_run: 0 } }) as never)
    const regen = vi.spyOn(api, 'regenerateKnowledgeIntelligence')
    const onOpenModels = vi.fn()
    mount('graph', { onOpenModels })
    expect(await screen.findByText('Entity extraction failed')).toBeTruthy()
    expect(document.body.textContent).toMatch(/no model is set up/)
    expect(document.body.textContent).not.toMatch(/have not been through entity extraction/)
    await userEvent.click(screen.getByRole('button', { name: /Connect a model/i }))
    expect(onOpenModels).toHaveBeenCalledTimes(1)
    expect(regen).not.toHaveBeenCalled()
  })
})

describe('the list shows an enrichment that failed', () => {
  const failedPhases = { passthrough: 'done', insights: 'failed', entities: 'failed', intents: 'skipped', embed: 'skipped' }
  const row = (over: Partial<KnowledgeItem>): KnowledgeItem => ({
    id: 'k1', title: 'Q4 pricing decision', content: 'We settled the Q4 tiers.', type: 'note', item_type: 'note',
    tags: [], processing_status: 'unsearchable', file_metadata: { node_phases: failedPhases },
    ...over,
  }) as KnowledgeItem

  it('badges an UNSEARCHABLE row whose insights and entities failed, with the reason readable', async () => {
    vi.spyOn(api, 'knowledgeStats').mockResolvedValue(
      statsWith({ model_available: false, entities: { ran: 0, failed: 1, running: 0, skipped: 0, not_run: 0 } }) as never)
    vi.spyOn(api, 'knowledgeItems').mockResolvedValue({ items: [row({})], total: 1, page: 1, limit: 100 } as never)
    mount('library')
    const badge = await screen.findByText('Enrichment failed')
    expect(badge.textContent).toContain('Insights and entity extraction failed — the model was unavailable')
  })

  it('failedEnrichment reads the persisted phases, and stays quiet while a run is in flight', () => {
    expect(failedEnrichment(row({}))?.reason).toBe('Insights and entity extraction failed — the model was unavailable')
    expect(failedEnrichment(row({ processing_status: 'queued' }))).toBeNull()
    expect(failedEnrichment(row({ file_metadata: { node_phases: { insights: 'done', entities: 'done' } } }))).toBeNull()
    expect(failedEnrichment(row({ file_metadata: {} }))).toBeNull()
    expect(failedEnrichment(row({ file_metadata: { node_phases: { insights: 'failed', entities: 'failed', intents: 'failed' } } }))?.reason)
      .toBe('Insights, entity extraction and intent matching failed — the model was unavailable')
  })
})
