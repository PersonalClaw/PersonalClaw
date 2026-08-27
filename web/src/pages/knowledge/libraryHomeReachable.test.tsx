import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { KnowledgeListPage } from './KnowledgeListPage'
import { api } from '../../lib/api'
import { resetDataStore } from '../../lib/data/store'

// ── Is the library home REACHABLE? (`KL-8`) ──────────────────────────────────────────────
//
// The component-level file next door (libraryHome.test.tsx) proves the shelves behave. It would
// pass identically if nothing on `#/knowledge` ever rendered them — the frontend twin of a handler
// with no `router.add_get`, and the exact shape this repo keeps finding as "a page component
// nothing routes to". So this file mounts the REAL page and asks two things:
//
//   1. the view strip OFFERS Home (a lens nobody can select is a lens nobody has), and
//   2. selecting it renders the shelves rather than the item list.
//
// The vacuity guard is the third assertion: an obviously-bogus lens name must NOT render them,
// or a page that ignored `view` entirely would pass 1 and 2.

function mount(view = '') {
  const query: Record<string, string> = view ? { view } : {}
  const setQuery = vi.fn()
  render(<KnowledgeListPage onCreate={() => {}} onOpenItem={() => {}} onOpenReader={() => {}}
    onOpenSources={() => {}} onOpenReports={() => {}} onOpenChat={() => {}} query={query} setQuery={setQuery} />)
  return setQuery
}

beforeEach(() => {
  resetDataStore()
  localStorage.clear()
  vi.restoreAllMocks()
  // A library with something in it: the page delegates the truly-empty case to its own
  // "Knowledge base is empty" state, so a zero-item fixture would hide the home behind it.
  vi.spyOn(api, 'knowledgeStats').mockResolvedValue({ items: 3, entities: 0, relations: 0, embeddings: { enabled: false } } as never)
  vi.spyOn(api, 'knowledgeItems').mockResolvedValue({ items: [], total: 0, page: 1, limit: 100 } as never)
  vi.spyOn(api, 'knowledgeCollections').mockResolvedValue([])
  vi.spyOn(api, 'knowledgeLibraryHome').mockResolvedValue({
    recently_added: [], continue_reading: [], favorites: [],
    collections: [{ id: 'c1', name: 'Recipes', kind: 'manual', count: 2 }],
  })
})

describe('the Home lens is on the page, not just in the file tree', () => {
  it('offers Home FIRST in the view strip', async () => {
    mount()
    expect(await screen.findByRole('tab', { name: /Home/ })).toBeInTheDocument()
    // First, because it is the orienting lens. The strip is `role=tablist`/`role=tab`, not
    // buttons — asserting the wrong role here was how this test first passed vacuously.
    expect(screen.getAllByRole('tab').map((t) => t.textContent))
      // `Decisions` (PA-6) is the seventh lens — a filtered view of the same library, per
      // PROACTIVE-ASSISTANT §5.3 ("not a new nav section — decisions ARE knowledge items").
      // Appended, so Home keeps the first slot this test is really about.
      .toEqual(['Home', 'Library', 'Graph', 'Intents', 'Tags', 'Conflicts', 'Decisions'])
  })

  it('renders the shelves when Home is the active lens', async () => {
    mount('home')
    expect(await screen.findByRole('region', { name: 'Shelves' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Continue reading' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Recently added' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Favorites' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Recipes, 2 items' })).toBeInTheDocument()
  })

  it('does NOT render them under a lens that is not Home', async () => {
    mount('graph')
    await screen.findByRole('tab', { name: /Home/ })
    expect(screen.queryByRole('region', { name: 'Recently added' })).toBeNull()
  })

  it('selecting Home puts the lens in the URL, so it is linkable and survives a reload', async () => {
    const setQuery = mount()
    await userEvent.click(await screen.findByRole('tab', { name: /Home/ }))
    expect(setQuery).toHaveBeenCalledWith({ view: 'home' }, expect.anything())
  })
})
