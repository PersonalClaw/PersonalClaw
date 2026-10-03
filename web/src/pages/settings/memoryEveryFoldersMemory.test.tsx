/**
 * Settings → Memory shows and manages every memory you have, not only the one every chat shares.
 *
 * A chat working in a folder of its own keeps what it learns in that folder's memory, and the page
 * read the shared memory alone: a folder's facts, lessons and episodes could be neither seen nor
 * forgotten, and a memory whose folder was gone could not be removed at all. "Memory of" lists
 * each memory, a folder's by its folder, and every read the panel makes names the one picked.
 *
 * Driven through the real panel, with the gateway's answers stubbed at the API seam.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import type { MemoryPartition } from '../../lib/api'

const SHARED: MemoryPartition = {
  id: '', global: true, folder: '', path: '', gone: false, projects: [], semantic: 4, episodic: 9,
}
const GARDEN: MemoryPartition = {
  id: 'home_user_garden-notes', global: false, folder: '~/garden-notes',
  path: '/home/user/garden-notes', gone: false,
  projects: [{ id: 'proj_spring', name: 'Spring beds' }], semantic: 2, episodic: 1,
}
const RECIPES: MemoryPartition = {
  id: 'home_user_old-recipes', global: false, folder: '~/old-recipes',
  path: '/home/user/old-recipes', gone: true, projects: [], semantic: 0, episodic: 3,
}

let semantic: ReturnType<typeof vi.fn>
let lessons: ReturnType<typeof vi.fn>
let removeMemoryPartition: ReturnType<typeof vi.fn>
let confirmDestructive: ReturnType<typeof vi.fn>
let notify: ReturnType<typeof vi.fn>
let setQuery: ReturnType<typeof vi.fn<(patch: Record<string, string | null | undefined>, opts?: { replace?: boolean }) => void>>

async function mountPanel(partitions: MemoryPartition[], query: Record<string, string> = {}) {
  semantic = vi.fn(() => Promise.resolve([]))
  lessons = vi.fn(() => Promise.resolve([]))
  removeMemoryPartition = vi.fn(() => Promise.resolve({ ok: true }))
  confirmDestructive = vi.fn(() => Promise.resolve(true))
  notify = vi.fn()
  setQuery = vi.fn<(patch: Record<string, string | null | undefined>, opts?: { replace?: boolean }) => void>()
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as object),
        memoryPartitions: () => Promise.resolve(partitions),
        removeMemoryPartition,
        memoryStats: () => Promise.resolve(null),
        memorySemantic: semantic,
        memoryEpisodic: () => Promise.resolve([]),
        lessons,
        memoryGraph: () => Promise.resolve({ nodes: [], edges: [] }),
        memoryEntityGraph: () => Promise.resolve({ nodes: [], edges: [] }),
        memoryEntities: () => Promise.resolve({ entities: [], summary: {}, enabled: true }),
        memorySlots: () => Promise.resolve({ slots: [] }),
        memoryEntityProposals: () => Promise.resolve({ proposals: [], enabled: true }),
      },
    }
  })
  vi.doMock('../../ui/dialog', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return { ...real, confirmDestructive }
  })
  vi.doMock('../../app/appSdk', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return { ...real, notify }
  })
  const { MemoryPanel } = await import('./MemoryPanel')
  render(<MemoryPanel query={query} setQuery={setQuery} />)
}

beforeEach(() => {
  cleanup()
  vi.resetModules()
  sessionStorage.clear()
  // jsdom implements no scrolling, and the studio scrolls its panes.
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView
})
afterEach(() => cleanup())

describe('Settings → Memory: every memory you have', () => {
  it('lists each memory, a folder\'s by its folder, and reads the one picked', async () => {
    await mountPanel([SHARED, GARDEN, RECIPES], { partition: GARDEN.id })

    const picker = (await screen.findByRole('combobox', { name: 'Which memory to show' })) as HTMLSelectElement
    const labels = Array.from(picker.options).map((o) => o.textContent)
    expect(labels).toEqual(['Every chat (shared memory)', '~/garden-notes', '~/old-recipes (folder gone)'])
    expect(picker.value).toBe(GARDEN.id)
    expect(screen.getByText(/What chats working in ~\/garden-notes keep/)).toBeTruthy()
    expect(screen.getByText(/It is the memory of Spring beds/)).toBeTruthy()
    // The studio's lists are the folder's, not the shared memory's.
    await waitFor(() => expect(semantic).toHaveBeenCalled())
    expect(semantic.mock.calls.every((c) => c[0] === GARDEN.id)).toBe(true)
    expect(lessons.mock.calls.every((c) => c[0] === GARDEN.id)).toBe(true)

    fireEvent.change(picker, { target: { value: '' } })
    expect(setQuery).toHaveBeenCalledWith({ partition: null }, { replace: true })
  })

  it('says a memory whose folder is gone is no longer read, and removes it when asked', async () => {
    await mountPanel([SHARED, GARDEN, RECIPES], { partition: RECIPES.id })

    expect(await screen.findByText(/~\/old-recipes is no longer there, so no chat reads this memory/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Remove this memory/ }))

    await waitFor(() => expect(removeMemoryPartition).toHaveBeenCalledWith(RECIPES.id))
    const [title, body, opts] = confirmDestructive.mock.calls[0]
    expect(title).toBe('Remove the memory of ~/old-recipes?')
    expect(body).toMatch(/the shared memory every chat reads is not touched/)
    expect(opts).toEqual({ confirmLabel: 'Remove' })
    expect(notify).toHaveBeenCalledWith('Removed the memory of ~/old-recipes.', 'success')
    // Back to the shared memory once the one shown is gone.
    expect(setQuery).toHaveBeenCalledWith({ partition: null }, { replace: true })
  })

  it('removes nothing when the removal is not confirmed', async () => {
    await mountPanel([SHARED, GARDEN], { partition: GARDEN.id })
    confirmDestructive.mockImplementation(() => Promise.resolve(false))

    fireEvent.click(await screen.findByRole('button', { name: /Remove this memory/ }))

    await waitFor(() => expect(confirmDestructive).toHaveBeenCalled())
    expect(removeMemoryPartition).not.toHaveBeenCalled()
  })

  it('offers no picker while the shared memory is the only one, and no removal for it', async () => {
    await mountPanel([SHARED])
    await waitFor(() => expect(semantic).toHaveBeenCalled())
    expect(screen.queryByRole('combobox', { name: 'Which memory to show' })).toBeNull()
    expect(screen.queryByRole('button', { name: /Remove this memory/ })).toBeNull()
  })

  it('says so when the memory a link names was removed, rather than showing another', async () => {
    await mountPanel([SHARED, GARDEN], { partition: 'home_user_tax-papers' })

    expect(await screen.findByRole('alert')).toHaveProperty(
      'textContent', 'This memory is not there any more: it was removed. Pick another one above.',
    )
  })
})
