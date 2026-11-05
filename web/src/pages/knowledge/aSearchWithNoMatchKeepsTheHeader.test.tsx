// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { KnowledgeListPage } from './KnowledgeListPage'
import { api } from '../../lib/api'
import { resetDataStore } from '../../lib/data/store'

// ── A search with no match keeps the library's header as it was ──────────────────────────────────
//
// Regenerate intelligence was in the header only while the list showed items, so a query with no
// match took it out of the row: the row re-tiered under the field being typed in, at 1440 the
// field swapped forms, and the search lost the reader's focus mid-word. It works on the whole
// library, so it stays, and is unavailable only while the library holds nothing.

function mount() {
  render(<KnowledgeListPage onCreate={() => {}} onOpenItem={() => {}} onOpenReader={() => {}}
    onOpenSources={() => {}} onOpenReports={() => {}} onOpenChat={() => {}} query={{ view: 'library' }} setQuery={vi.fn()} />)
}

beforeEach(() => {
  resetDataStore()
  localStorage.clear()
  vi.restoreAllMocks()
  vi.spyOn(api, 'knowledgeCollections').mockResolvedValue([])
  vi.spyOn(api, 'knowledgeItems').mockResolvedValue({ items: [], total: 0, page: 1, limit: 100 } as never)
})

describe('the library header and a search with no match', () => {
  it('keeps Regenerate in the row while no item matches', async () => {
    vi.spyOn(api, 'knowledgeStats').mockResolvedValue({ items: 3, entities: 0, relations: 0, embeddings: { enabled: false } } as never)
    mount()
    const regen = await screen.findByRole('button', { name: /Regenerate intelligence/ })
    await userEvent.type(screen.getByRole('searchbox', { name: 'Search knowledge' }), 'zzz{Enter}')
    expect(screen.getByRole('button', { name: /Regenerate intelligence/ })).toBe(regen)
  })

  it('says why it is unavailable while the library is empty', async () => {
    vi.spyOn(api, 'knowledgeStats').mockResolvedValue({ items: 0, entities: 0, relations: 0, embeddings: { enabled: false } } as never)
    mount()
    const regen = await screen.findByRole('button', { name: /Regenerate intelligence/ })
    expect(regen).toHaveAttribute('aria-disabled', 'true')
    expect(regen.getAttribute('title')).toContain('Nothing in the library yet')
  })
})
