/** The Filter names a maker only when one is picked.
 *
 *  Made by said "made by " with nothing picked, so the library's Filter read "Filter: made by · Any
 *  project · Any collection". With no maker picked it says nothing of makers, as Kind says nothing of
 *  kinds; with one picked it names it. */
import { describe, expect, it, vi } from 'vitest'
import { headerFilterLabel } from '../../ui/HeaderFilter'
import { librarySections, type FilterInputs } from './libraryFilter'

const inputs = (query: Record<string, string>): FilterInputs => ({
  query, projects: [], collections: [], push: vi.fn(), phone: false, layout: 'list', setLayout: vi.fn(),
  onNewCollection: vi.fn(), onManageTags: vi.fn(),
  data: { items: [], total: 3, complete: true, failed: [], sort: 'activity', facets: { made: { you: 2, chats: 1 }, kind: { note: 3 } } },
})

describe('the Filter names only a maker it shows', () => {
  it('🔴 with no maker picked it says nothing of makers', () => {
    const label = headerFilterLabel(librarySections(inputs({})), 'Filter')
    expect(label).not.toMatch(/made by/i)
    expect(label).toBe('Filter: Any project · Any collection')
  })

  it('a picked maker is named', () => {
    expect(headerFilterLabel(librarySections(inputs({ made: 'chats' })), 'Filter')).toBe('Filter: made by Chats · Any project · Any collection')
  })
})
