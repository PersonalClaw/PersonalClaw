// @vitest-environment jsdom
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { KnowledgeListPage } from './KnowledgeListPage'
import { api, type KnowledgeItem } from '../../lib/api'
import { resetDataStore } from '../../lib/data/store'
import { findHeaderFilter, headerFilter } from '../../test/headerFilter'

// ── A search keeps the library's header as it was ─────────────────────────────────────────────────
//
// The query here is the page's own: the search box debounces into `?q`, which a host holds, so each
// test types a real search and waits for its list.
//
// - Regenerate intelligence was in the header only while the list showed items, so a query with no
//   match took it out of the row: the row re-tiered under the field being typed in, and the search
//   lost the reader's focus mid-word. It works on the whole library, so it stays, and is off only
//   while the library is known to hold nothing (not while the count loads, nor after its read fails).
// - The filter's Type and Curation sections showed only while the matches held more than one kind,
//   so a search whose list answered one kind (or none, while it loaded) took the section away: the
//   label fell to "Filter" while the hidden choice went on filtering, with no row left to clear it.
//   A section stays while its value is set, with the chosen option kept.

const NOTE = { id: 'k1', title: 'Garden notes', item_type: 'note', created_at: '2026-08-20T00:00:00', updated_at: '2026-08-20T00:00:00' }
const LINK = { id: 'k2', title: 'Garden supplier', item_type: 'bookmark', url: 'https://example.com', favorited: true, created_at: '2026-08-20T00:00:00', updated_at: '2026-08-20T00:00:00' }
const SHED = { id: 'k3', title: 'Shed notes', item_type: 'note', created_at: '2026-08-20T00:00:00', updated_at: '2026-08-20T00:00:00' }

const page = (items: unknown[]) => ({ items: items as KnowledgeItem[], total: items.length, page: 1, limit: 100 })

function Host({ initial = {} }: { initial?: Record<string, string> }) {
  const [query, setQuery] = useState<Record<string, string>>({ view: 'library', ...initial })
  const patch = (p: Record<string, string | null | undefined>) => setQuery((q) => {
    const next = { ...q }
    for (const [k, v] of Object.entries(p)) { if (v == null || v === '') delete next[k]; else next[k] = v }
    return next
  })
  return (
    <>
      <output data-testid="address">{new URLSearchParams(query).toString()}</output>
      <KnowledgeListPage onCreate={() => {}} onOpenItem={() => {}} onOpenReader={() => {}}
        onOpenSources={() => {}} onOpenReports={() => {}} onOpenChat={() => {}} query={query} setQuery={patch as never} />
    </>
  )
}

const searchFor = (text: string) => userEvent.type(screen.getByRole('searchbox', { name: 'Search knowledge' }), text)
const askedFor = (q: string) => waitFor(() => expect(api.knowledgeItems).toHaveBeenCalledWith(expect.objectContaining({ q })), { timeout: 2000 })

beforeEach(() => {
  resetDataStore()
  localStorage.clear()
  vi.restoreAllMocks()
  vi.spyOn(api, 'knowledgeCollections').mockResolvedValue([])
  vi.spyOn(api, 'knowledgeItems').mockResolvedValue(page([]) as never)
  vi.spyOn(api, 'knowledgeStats').mockResolvedValue({ items: 3, entities: 0, relations: 0, embeddings: { enabled: false } } as never)
})

describe('Regenerate and a search with no match', () => {
  it('stays the same control in the row once the search has answered nothing', async () => {
    vi.mocked(api.knowledgeItems).mockImplementation((async ({ q }: { q?: string }) => page(q ? [] : [NOTE, LINK])) as never)
    render(<Host />)
    await screen.findByText('Garden notes')
    const regen = screen.getByRole('button', { name: /Regenerate intelligence/ })
    await searchFor('zzz')
    await askedFor('zzz')
    expect(await screen.findByText('No items match “zzz”')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Regenerate intelligence/ })).toBe(regen)
    expect(regen).not.toHaveAttribute('aria-disabled', 'true')
  })

  it('says why it is off while the library is empty', async () => {
    vi.mocked(api.knowledgeStats).mockResolvedValue({ items: 0, entities: 0, relations: 0, embeddings: { enabled: false } } as never)
    render(<Host />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Regenerate intelligence/ })).toHaveAttribute('aria-disabled', 'true'))
    expect(screen.getByRole('button', { name: /Regenerate intelligence/ }).getAttribute('title')).toContain('Nothing in the library yet')
  })

  it('claims no empty library while the count is loading', async () => {
    vi.mocked(api.knowledgeStats).mockImplementation(() => new Promise(() => {}))
    render(<Host />)
    const regen = await screen.findByRole('button', { name: /Regenerate intelligence/ })
    expect(regen).not.toHaveAttribute('aria-disabled', 'true')
    expect(regen.getAttribute('title') ?? '').not.toContain('Nothing in the library yet')
  })

  it('claims no empty library after the count failed to read', async () => {
    vi.mocked(api.knowledgeStats).mockRejectedValue(new Error('stats read failed'))
    render(<Host />)
    await waitFor(() => expect(api.knowledgeStats).toHaveBeenCalled())
    await new Promise((r) => setTimeout(r, 50))
    const regen = screen.getByRole('button', { name: /Regenerate intelligence/ })
    expect(regen).not.toHaveAttribute('aria-disabled', 'true')
    expect(regen.getAttribute('title') ?? '').not.toContain('Nothing in the library yet')
  })
})

describe("the filter's label through a search", () => {
  it('keeps a set Type, and its row, while a one-kind list loads and after', async () => {
    let answer!: (v: unknown) => void
    vi.mocked(api.knowledgeItems).mockImplementation((async ({ q }: { q?: string }) =>
      (q ? new Promise((r) => { answer = r }) : page([NOTE, LINK]))) as never)
    render(<Host initial={{ type: 'note' }} />)
    await screen.findByText('Garden notes')
    const said = (await findHeaderFilter()).getAttribute('aria-label')
    expect(said, 'the label names the set Type').toContain('Note')

    await searchFor('shed')
    await askedFor('shed')
    expect(headerFilter().getAttribute('aria-label'), 'while the list loads').toBe(said)
    answer(page([SHED]))
    expect(await screen.findByText('Shed notes')).toBeTruthy()
    expect(headerFilter().getAttribute('aria-label'), 'after the one-kind list').toBe(said)

    await userEvent.click(headerFilter())
    const type = await screen.findByRole('group', { name: 'Type' })
    expect(within(type).getByRole('button', { name: /Note/ })).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(within(type).getByRole('button', { name: 'All types' }))
    await waitFor(() => expect(screen.getByTestId('address').textContent).not.toContain('type='))
  })

  it('keeps a chosen Curation, and its row, once the matches hold none of it', async () => {
    vi.mocked(api.knowledgeItems).mockImplementation((async ({ q }: { q?: string }) => page(q ? [SHED] : [NOTE, LINK])) as never)
    render(<Host />)
    await screen.findByText('Garden supplier')
    await userEvent.click(await findHeaderFilter())
    await userEvent.click(within(await screen.findByRole('group', { name: 'Curation' })).getByRole('button', { name: /Favorites/ }))
    await waitFor(() => expect(headerFilter().getAttribute('aria-label')).toContain('Favorites'))
    const said = headerFilter().getAttribute('aria-label')

    await searchFor('shed')
    await askedFor('shed')
    await waitFor(() => expect(api.knowledgeItems).toHaveBeenCalledTimes(2))
    await new Promise((r) => setTimeout(r, 50))
    expect(headerFilter().getAttribute('aria-label'), 'with no favourite among the matches').toBe(said)
    await userEvent.click(headerFilter())
    const curation = await screen.findByRole('group', { name: 'Curation' })
    expect(within(curation).getByRole('button', { name: /Favorites/ })).toHaveAttribute('aria-pressed', 'true')
  })
})
