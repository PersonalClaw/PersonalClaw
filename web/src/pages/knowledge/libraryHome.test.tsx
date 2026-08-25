import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { LibraryHome } from './LibraryHome'
import { api, type KnowledgeItem, type KnowledgeLibraryHome } from '../../lib/api'
import { resetDataStore, writeQuery } from '../../lib/data/store'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { getReadingPosition } from './readingPosition'

// ── The library home's four shelves (KNOWLEDGE-LIBRARY S3, T3.3 / `KL-8`) ─────────────────
//
// Every shelf here can legitimately be empty, and a failed fetch renders as an empty state
// unless something stops it. So the load-bearing assertions are the ones that separate the
// three answers a shelf can give:
//
//   • EMPTY  — a sentence naming what would be here and how it gets here.
//   • BROKEN — an alert naming the failure, with a retry. Never an empty state.
//   • STALE  — shelves painted from cache, WITH the failed refresh said out loud.
//
// The other half is the continue-reading shelf, whose whole promise is resuming: the row must
// carry how far in you are and hand off to the READER (`?read=1`), because that is the surface
// that restores the scroll. A row that opened the metadata view would look identical in a
// render assertion and resume nothing.

function item(over: Partial<KnowledgeItem> = {}): KnowledgeItem {
  return {
    id: 'k1', title: 'On long articles', item_type: 'note',
    created_at: '2026-08-20T00:00:00', updated_at: '2026-08-20T00:00:00', ...over,
  } as KnowledgeItem
}

function home(over: Partial<KnowledgeLibraryHome> = {}): KnowledgeLibraryHome {
  return { recently_added: [], continue_reading: [], favorites: [], collections: [], ...over }
}

const POS_KEY = 'knowledge-reading-positions'

function mount(over: Partial<Parameters<typeof LibraryHome>[0]> = {}) {
  const props = {
    onOpenItem: vi.fn(), onOpenReader: vi.fn(), onOpenCollection: vi.fn(), onShowCuration: vi.fn(),
    ...over,
  }
  render(<LibraryHome {...props} />)
  return props
}

beforeEach(() => {
  resetDataStore()
  localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('the library home tells empty apart from broken', () => {
  it('gives every empty shelf a sentence instead of a blank region', async () => {
    vi.spyOn(api, 'knowledgeLibraryHome').mockResolvedValue(home())
    mount()

    for (const name of ['Shelves', 'Continue reading', 'Recently added', 'Favorites']) {
      expect(await screen.findByRole('region', { name }), name).toBeInTheDocument()
    }
    expect(screen.getByText(/No shelves yet/)).toBeInTheDocument()
    expect(screen.getByText(/Nothing in progress/)).toBeInTheDocument()
    expect(screen.getByText(/Nothing added yet/)).toBeInTheDocument()
    expect(screen.getByText(/No favorites yet/)).toBeInTheDocument()
    // An empty library is not an error.
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('a failed read is a retryable alert, NOT four empty shelves', async () => {
    vi.spyOn(api, 'knowledgeLibraryHome').mockRejectedValue(new Error('boom'))
    mount()

    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent).toMatch(/Couldn't load your library/i)
    expect(screen.getByRole('button', { name: /Try again|Retry/i })).toBeInTheDocument()
    // 🔑 The discrimination itself: none of the "nothing here yet" sentences may appear on a
    // read that never answered. Without this the two states are the same pixels.
    expect(screen.queryByText(/Nothing added yet/)).toBeNull()
    expect(screen.queryByText(/No favorites yet/)).toBeNull()
  })

  it('the FETCHER never swallows the rejection either', () => {
    // 🪤 Found by falsification: adding `.catch(() => ({ …empty shelves }))` to
    // `api.knowledgeLibraryHome` left every DOM test above GREEN, because they mock that method
    // and never reach the swallow. The component cannot tell an empty answer from a swallowed
    // failure — so the absence of the catch is asserted where it lives, from source, exactly as
    // `ui/loadErrorState` and `pages/listDestinationLoadError` do for the other read paths.
    const src = readFileSync(join(process.cwd(), 'src/lib/api.ts'), 'utf8')
    const call = src.match(/knowledgeLibraryHome:[\s\S]*?\n {2}[a-zA-Z]/)
    expect(call, 'could not locate knowledgeLibraryHome in lib/api.ts — the matcher rotted').toBeTruthy()
    // Vacuity floor: the window really is the call, not an empty capture.
    expect(call![0]).toMatch(/api\/knowledge\/library-home/)
    expect(call![0], 'a swallowed rejection makes "empty" and "broken" the same pixels').not.toMatch(/\.catch\(/)
  })

  it('says the refresh failed even while painting the shelves it already had', async () => {
    // The stale-with-error path: a value already in the cache (the user was just here) and a
    // revalidation that rejects. `useQuery` paints the cached bytes, so without the banner this
    // is a perfectly confident home built from a read that failed.
    writeQuery('knowledge:library-home', home({ recently_added: [item({ title: 'Kept copy' })] }))
    vi.spyOn(api, 'knowledgeLibraryHome').mockRejectedValue(new Error('boom'))
    mount()

    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/Couldn't refresh/i))
    // The rows are still there — stale, and now labelled as such rather than silently confident.
    expect(screen.getByRole('button', { name: 'Kept copy' })).toBeInTheDocument()
  })
})

describe('per-collection counts', () => {
  it('puts the count IN the shelf name and reports a capped one as such', async () => {
    vi.spyOn(api, 'knowledgeLibraryHome').mockResolvedValue(home({
      collections: [
        { id: 'c1', name: 'Recipes', kind: 'manual', count: 1 },
        { id: 'c2', name: 'Everything', kind: 'smart', count: 200, count_capped: true },
      ],
    }))
    const props = mount()

    expect(await screen.findByRole('button', { name: 'Recipes, 1 item' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Everything, 200 or more items' })).toBeInTheDocument()
    expect(screen.getByText('200+')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Recipes, 1 item' }))
    expect(props.onOpenCollection).toHaveBeenCalledWith('c1')
  })
})

describe('continue reading', () => {
  const reading = [item({ id: 'a', title: 'Article A' }), item({ id: 'b', title: 'Article B' })]

  it('shows how far in you are and resumes into the READER, not the metadata view', async () => {
    localStorage.setItem(POS_KEY, JSON.stringify({ a: { pct: 0.4, ts: 10 } }))
    vi.spyOn(api, 'knowledgeLibraryHome').mockResolvedValue(home({ continue_reading: [reading[0]] }))
    const props = mount()

    expect(await screen.findByText(/40% in/)).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: /Reading progress on Article A: 40%/ })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Resume reading: Article A' }))
    expect(props.onOpenReader).toHaveBeenCalledWith('a')
    expect(props.onOpenItem).not.toHaveBeenCalled()
  })

  it('orders by where you last read, which the server cannot know', async () => {
    // The server hands them back A then B (its own `updated_at` order); B was read later.
    localStorage.setItem(POS_KEY, JSON.stringify({
      a: { pct: 0.3, ts: 10 }, b: { pct: 0.6, ts: 99 },
    }))
    vi.spyOn(api, 'knowledgeLibraryHome').mockResolvedValue(home({ continue_reading: reading }))
    mount()

    await screen.findByRole('region', { name: 'Continue reading' })
    const titles = screen.getAllByRole('button', { name: /^Article / }).map((b) => b.getAttribute('aria-label'))
    expect(titles).toEqual(['Article B', 'Article A'])
  })

  it('an item with no local position says "Not started" rather than 0%', async () => {
    vi.spyOn(api, 'knowledgeLibraryHome').mockResolvedValue(home({ continue_reading: [reading[0]] }))
    mount()
    expect(await screen.findByText(/Not started/)).toBeInTheDocument()
  })

  it('a mark-read that FAILED leaves the row, the position, and says so', async () => {
    localStorage.setItem(POS_KEY, JSON.stringify({ a: { pct: 0.4, ts: 10 } }))
    vi.spyOn(api, 'knowledgeLibraryHome').mockResolvedValue(home({ continue_reading: [reading[0]] }))
    vi.spyOn(api, 'setKnowledgeReadState').mockRejectedValue(new Error('offline'))
    mount()

    await userEvent.click(await screen.findByRole('button', { name: 'Mark read: Article A' }))

    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/Couldn't mark/i))
    expect(screen.getByRole('button', { name: 'Article A' })).toBeInTheDocument()
    // 🪤 The resume point must survive a failed write. Clearing it here would lose the reader's
    // place to a network blip while the shelf still showed the row.
    expect(getReadingPosition('a')?.pct).toBe(0.4)
  })

  it('a mark-read that SUCCEEDED drops the resume point with the row', async () => {
    localStorage.setItem(POS_KEY, JSON.stringify({ a: { pct: 0.4, ts: 10 } }))
    vi.spyOn(api, 'knowledgeLibraryHome').mockResolvedValue(home({ continue_reading: [reading[0]] }))
    const write = vi.spyOn(api, 'setKnowledgeReadState').mockResolvedValue({ ok: true, read_state: 'read' })
    mount()

    await userEvent.click(await screen.findByRole('button', { name: 'Mark read: Article A' }))

    await waitFor(() => expect(write).toHaveBeenCalledWith('a', 'read'))
    await waitFor(() => expect(getReadingPosition('a')).toBeNull())
  })
})

describe('the shelves that hand off to the list', () => {
  it('offers "view all" only when there is something to view', async () => {
    vi.spyOn(api, 'knowledgeLibraryHome').mockResolvedValue(home({ favorites: [item({ title: 'Kept' })] }))
    const props = mount()

    await userEvent.click(await screen.findByRole('button', { name: 'View all favorites' }))
    expect(props.onShowCuration).toHaveBeenCalledWith('favorites')
    // The empty shelf beside it offers no dead-end link.
    expect(screen.queryByRole('button', { name: 'View all in progress' })).toBeNull()
  })

  it('a row opens the item', async () => {
    vi.spyOn(api, 'knowledgeLibraryHome').mockResolvedValue(home({ recently_added: [item({ id: 'z', title: 'Fresh note' })] }))
    const props = mount()

    await userEvent.click(await screen.findByRole('button', { name: 'Fresh note' }))
    expect(props.onOpenItem).toHaveBeenCalledWith('z')
  })
})
