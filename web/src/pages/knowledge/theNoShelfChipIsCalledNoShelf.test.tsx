// @vitest-environment jsdom
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { KnowledgeCollection, KnowledgeItem } from '../../lib/api'
import { findHeaderFilter } from '../../test/headerFilter'

// ── The Shelf section's first row is No shelf, and All items names only the view ────────────────
//
// Knowledge's item-list view is called All items (#3815): Library is the destination the page is a
// tab of, so the view could no longer share its name. The row that clears the shelf read All items
// too, so the page named two different controls with the same words. It is No shelf: it chooses no
// shelf, and the list shows everything. The shelves were a chip rail under the header; they are the
// Shelf section of the header's filter, with New shelf as its last row.

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

/** The header filter's Shelf section, the filter opened if it is not: its rows from No shelf to
 *  New shelf. */
async function shelfSection(): Promise<HTMLElement> {
  const trigger = await findHeaderFilter()
  if (trigger.getAttribute('aria-expanded') !== 'true') fireEvent.click(trigger)
  const heading = await screen.findByText('Shelf', { selector: 'span' })
  return heading.parentElement!.parentElement as HTMLElement
}
/** Each row's words (its label, then any count). */
const rows = (section: HTMLElement) => within(section).getAllByRole('button').map((b) => b.textContent?.trim())
/** The rows the section has chosen: the one with the check. */
const chosen = (section: HTMLElement) => within(section).getAllByRole('button')
  .filter((b) => b.querySelector('svg.text-primary')).map((b) => b.textContent?.trim())

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  localStorage.clear()
  mockApi()
})
afterEach(() => { vi.doUnmock('../../lib/api') })

describe("the header filter's Shelf section on the All items view", () => {
  it('starts with No shelf, chosen while no shelf is, and ends with New shelf', async () => {
    await mount()
    const section = await shelfSection()
    await waitFor(() => expect(rows(section)).toContain('Reading list1'))
    expect(rows(section)[0]).toBe('No shelf')
    expect(rows(section).at(-1)).toBe('New shelf')
    expect(chosen(section)).toEqual(['No shelf'])
  })

  it('All items names the view alone: no control on the page says it', async () => {
    await mount()
    await shelfSection()
    expect(screen.getByRole('radio', { name: 'All items' }).getAttribute('aria-checked')).toBe('true')
    expect(screen.queryAllByRole('button', { name: /All items/ })).toEqual([])
  })

  it('No shelf undoes a chosen shelf, and the list shows everything again', async () => {
    await mount()
    fireEvent.click(await within(await shelfSection()).findByRole('button', { name: /Reading list/ }))
    expect(await screen.findByText('Manual shelf — the items you put here.')).toBeTruthy()
    expect(await screen.findByText('On long articles')).toBeTruthy()
    expect(screen.queryByText('A short note'), 'the shelf replaced the list').toBeNull()
    // The filter says what it is set to, and the section is read again after each choice.
    expect((await findHeaderFilter()).getAttribute('aria-label')).toBe('Show: Reading list')
    expect(chosen(await shelfSection())).toEqual(['Reading list1'])
    fireEvent.click(within(await shelfSection()).getByRole('button', { name: 'No shelf' }))
    expect(await screen.findByText('A short note')).toBeTruthy()
    expect(chosen(await shelfSection())).toEqual(['No shelf'])
    expect((await findHeaderFilter()).getAttribute('aria-label')).toBe('Filter')
    expect(screen.queryByText('Manual shelf — the items you put here.')).toBeNull()
  })
})
