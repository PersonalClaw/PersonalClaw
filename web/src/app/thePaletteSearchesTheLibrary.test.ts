import { describe, expect, it, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'

// ── ⌘K's Library group is the library's one search ────────────────────────────────────────────
//
// The palette's content half searched the knowledge store alone (`GET /api/knowledge/items?q=`),
// so a page or document an agent made, a report and what an intent found were never among its
// hits. Its group is the library's now, from the library's own search (`GET /api/library?q=`),
// and each hit opens at its own page: an artifact's, a knowledge item's, a report's write-up's, and
// what an intent found on that intent in today's Intents view, which has no page of its own yet.

const h = vi.hoisted(() => ({
  library: vi.fn(),
  researchReports: vi.fn(),
  knowledgeItems: vi.fn(),
}))
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api, ...h,
      sessionsSearch: () => Promise.resolve({ sessions: [], source: 'index' }),
      searchEpisodic: () => Promise.resolve([]),
      memorySemantic: () => Promise.resolve([]),
      lessons: () => Promise.resolve([]),
      searchTasks: () => Promise.resolve({ tasks: [], total: 0 }),
    },
  }
})

import { searchLibrary, useContentSearch } from './paletteSearch'

const WRITE_UP = '0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d'
const row = (ref: string, title: string, extra: Record<string, unknown> = {}) => ({ ref, title, excerpt: '', match: null, ...extra })
const answer = (items: unknown[]) => ({ items, total: items.length, complete: true, failed: [], facets: {}, sort: 'rank' })

beforeEach(() => {
  h.library.mockReset().mockResolvedValue(answer([
    row('artifact:lisbon', 'Lisbon trip plan', { match: { passage: 'two days in <<Lisbon>>', meaning_only: false } }),
    row('knowledge:k1', 'Router manual', { excerpt: 'Archer AX55' }),
    row('report:rpt-1a2b3c4d', 'Weekly perf digest'),
    row('findings:power-bill', 'Cheap ways to cut the power bill'),
  ]))
  h.researchReports.mockReset().mockResolvedValue({ reports: [{ id: 'rpt-1a2b3c4d', name: 'Weekly perf digest', finding_item_id: WRITE_UP }] })
  h.knowledgeItems.mockReset().mockResolvedValue({ items: [], total: 0, page: 1, limit: 5 })
})

describe("⌘K's Library group", () => {
  it('🔑 comes from GET /api/library?q=, not the knowledge store alone', async () => {
    const { result } = renderHook(({ q }) => useContentSearch(q, true), { initialProps: { q: 'lisbon' } })
    await waitFor(() => expect(result.current.hits.some((x) => x.source === 'library')).toBe(true))
    expect(h.library).toHaveBeenCalledWith({ q: 'lisbon' })
    expect(h.knowledgeItems, 'the knowledge store is not searched on its own').not.toHaveBeenCalled()
    expect(result.current.hits.filter((x) => x.source === 'library').map((x) => x.label))
      .toEqual(['Lisbon trip plan', 'Router manual', 'Weekly perf digest', 'Cheap ways to cut the power bill'])
  })

  it('🔑 each hit opens at its own page', async () => {
    const hits = await searchLibrary('lisbon')
    expect(Object.fromEntries(hits.map((x) => [x.label, x.path]))).toEqual({
      'Lisbon trip plan': 'artifacts/lisbon',
      'Router manual': 'knowledge/item/k1',
      'Weekly perf digest': `knowledge/item/${WRITE_UP}`,
      'Cheap ways to cut the power bill': 'knowledge?view=intents&intent=power-bill',
    })
    // The matched passage is the second line, without the index's marks; else the row's excerpt.
    expect(hits[0].detail).toBe('two days in Lisbon')
    expect(hits[1].detail).toBe('Archer AX55')
  })

  it('reads the reports once, and only when a report is among the hits', async () => {
    await searchLibrary('lisbon')
    expect(h.researchReports).toHaveBeenCalledTimes(1)
    h.researchReports.mockClear()
    h.library.mockResolvedValue(answer([row('knowledge:k1', 'Router manual')]))
    await searchLibrary('router')
    expect(h.researchReports).not.toHaveBeenCalled()
  })

  it('a report whose write-up cannot be found opens the Reports page, not nowhere', async () => {
    h.researchReports.mockRejectedValue(new Error('the reports could not be read'))
    expect((await searchLibrary('perf')).find((x) => x.label === 'Weekly perf digest')?.path).toBe('knowledge/reports')
    h.researchReports.mockResolvedValue({ reports: [{ id: 'rpt-1a2b3c4d', name: 'Weekly perf digest', finding_item_id: '' }] })
    expect((await searchLibrary('perf')).find((x) => x.label === 'Weekly perf digest')?.path).toBe('knowledge/reports')
  })

  it('takes five hits, and a row whose ref names no kind is left out', async () => {
    h.library.mockResolvedValue(answer([
      row('chat:abc', 'Not a library thing'),
      ...Array.from({ length: 7 }, (_, i) => row(`knowledge:k${i}`, `Note ${i}`)),
    ]))
    expect((await searchLibrary('note')).map((x) => x.label)).toEqual(['Note 0', 'Note 1', 'Note 2', 'Note 3', 'Note 4'])
  })
})
