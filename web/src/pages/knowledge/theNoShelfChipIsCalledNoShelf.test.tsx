// @vitest-environment jsdom
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import type { KnowledgeCollection, KnowledgeItem } from '../../lib/api'

// ── The shelf rail's first chip is No shelf, and All items names only the view ──────────────────
//
// Knowledge's item-list view is called All items (#3815): Library is the destination the page is a
// tab of, so the view could no longer share its name. The chip that clears the shelf read All items
// too, so the page named two different controls with the same words, one over the other. It is No
// shelf: it chooses no shelf, and the list shows everything.

const item = (id: string, title: string): KnowledgeItem => ({
  id, title, item_type: 'note', created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
} as KnowledgeItem)

const SHELF: KnowledgeCollection = { id: 'shelf-1', name: 'Reading list', kind: 'manual', item_count: 1 } as KnowledgeCollection

function mockApi() {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      knowledgeStats: () => Promise.resolve({ items: 2, entities: 0, relations: 0 }),
      knowledgeItems: () => Promise.resolve({ items: [item('k1', 'On long articles'), item('k2', 'A short note')] }),
      knowledgeCollections: () => Promise.resolve([SHELF]),
      knowledgeCollectionItems: () => Promise.resolve({ items: [item('k1', 'On long articles')] }),
      knowledgeIntents: () => Promise.resolve([]),
      embeddingReindexJobs: () => Promise.resolve({ jobs: [], active: null }),
    },
  }))
}

/** The page on the All items view, its query held as the router holds it. */
async function mount() {
  const { KnowledgeListPage } = await import('./KnowledgeListPage')
  function Page() {
    const [query, setQueryState] = useState<Record<string, string>>({ view: 'library' })
    const setQuery = (patch: Record<string, string | null | undefined>) => setQueryState((q) => {
      const next = { ...q }
      for (const [k, v] of Object.entries(patch)) { if (v == null || v === '') delete next[k]; else next[k] = v }
      return next
    })
    return (
      <KnowledgeListPage query={query} setQuery={setQuery} onCreate={() => {}} onOpenItem={() => {}}
        onOpenSources={() => {}} onOpenReports={() => {}} onOpenChat={() => {}} />
    )
  }
  render(<Page />)
}

/** The shelf rail: the chips from the one that clears the shelf to New shelf. */
const shelfRail = async () => (await screen.findByRole('button', { name: /New shelf/ })).parentElement as HTMLElement
/** The chips the rail has pressed, each by its words (a shelf's chip by its full name, its title). */
const pressed = (rail: HTMLElement) => within(rail).getAllByRole('button')
  .filter((b) => b.getAttribute('aria-pressed') === 'true').map((b) => b.title || b.textContent?.trim())

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  localStorage.clear()
  mockApi()
})
afterEach(() => { vi.doUnmock('../../lib/api') })

describe('the shelf rail on the All items view', () => {
  it('starts with No shelf, chosen while no shelf is', async () => {
    await mount()
    const rail = await shelfRail()
    const chips = within(rail).getAllByRole('button').map((b) => b.textContent?.trim())
    expect(chips[0]).toBe('No shelf')
    expect(await within(rail).findByRole('button', { name: /Reading list/ })).toBeTruthy()
    expect(pressed(rail)).toEqual(['No shelf'])
  })

  it('All items names the view alone: no chip on the page says it', async () => {
    await mount()
    await shelfRail()
    expect(screen.getByRole('radio', { name: 'All items' }).getAttribute('aria-checked')).toBe('true')
    expect(screen.queryAllByRole('button', { name: /All items/ })).toEqual([])
  })

  it('No shelf undoes a chosen shelf, and the list shows everything again', async () => {
    await mount()
    fireEvent.click(await within(await shelfRail()).findByRole('button', { name: /Reading list/ }))
    expect(await screen.findByText('Manual shelf — the items you put here.')).toBeTruthy()
    expect(await screen.findByText('On long articles')).toBeTruthy()
    expect(screen.queryByText('A short note'), 'the shelf replaced the list').toBeNull()
    // The rail is read again after each choice: a shelf replaces the list's source, and the list
    // draws again around its new rows.
    expect(pressed(await shelfRail())).toEqual(['Reading list'])
    fireEvent.click(within(await shelfRail()).getByRole('button', { name: 'No shelf' }))
    expect(await screen.findByText('A short note')).toBeTruthy()
    expect(pressed(await shelfRail())).toEqual(['No shelf'])
    expect(screen.queryByText('Manual shelf — the items you put here.')).toBeNull()
  })
})
