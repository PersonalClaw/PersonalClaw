/** The Filter names a picked collection by its name, not its id.
 *
 *  Driven (dogfood, 11-21): with a collection picked (`#/library?collection=<id>`) that holds
 *  nothing yet, the Filter read "Filter: Any project · 7921df9a-…" while the count line beside it
 *  said the collection's name. Its options were the collections the read's counts name, and an
 *  empty one has no count, so the picked collection had no option to name it and the label fell back
 *  to its id. The picked collection is an option whatever it holds. */
import { describe, expect, it, vi } from 'vitest'
import { headerFilterLabel } from '../../ui/HeaderFilter'
import type { KnowledgeCollection } from '../../lib/api'
import { librarySections, type FilterInputs } from './libraryFilter'

const ID = '7921df9a-aa24-4f5d-b2d9-abbc1d1b498c'
const SHELF = { id: ID, name: 'Empty shelf', kind: 'smart', query: 'routers' } as KnowledgeCollection
const TRIPS = { id: 'c-trips', name: 'Trips', kind: 'manual' } as KnowledgeCollection

const inputs = (query: Record<string, string>, collectionCounts: Record<string, number>): FilterInputs => ({
  query, projects: [], collections: [SHELF, TRIPS], push: vi.fn(), phone: false, layout: 'list', setLayout: vi.fn(),
  onNewCollection: vi.fn(), onManageTags: vi.fn(),
  data: { items: [], total: 0, complete: true, failed: [], sort: 'activity', facets: { collection: collectionCounts } },
})

describe('the Filter and a picked collection', () => {
  it('🔴 names a picked collection that holds nothing by its name', () => {
    const label = headerFilterLabel(librarySections(inputs({ collection: ID }, {})), 'Filter')
    expect(label).toBe('Filter: Any project · Empty shelf · Smart')
    expect(label).not.toContain(ID)
  })

  it('names one that holds items, as before', () => {
    expect(headerFilterLabel(librarySections(inputs({ collection: 'c-trips' }, { 'c-trips': 4 })), 'Filter'))
      .toBe('Filter: Any project · Trips')
  })

  it('offers no empty collection that is not picked', () => {
    const section = librarySections(inputs({}, { 'c-trips': 4 })).find((s) => s.title === 'Collection')
    expect(section?.options.map((o) => o.key)).toEqual(['', 'c-trips'])
  })
})
