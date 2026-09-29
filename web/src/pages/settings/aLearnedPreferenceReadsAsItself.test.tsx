/**
 * A learned preference reads as the preference wherever Memory shows it, and the link from the
 * chat opens the Learned preferences list at its row.
 *
 * 🔴 Before: the Studio listed it as `pref.facet.style.bbaad48828 {"cls":"style","text":"shorter",…}`
 * with a JSON editor and a Delete that the next matching message undid, and nothing led from it to
 * the list that can forget it for good.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import type { MemoryFacet, SemanticEntry } from '../../lib/api'

const KEY = 'pref.facet.style.bbaad48828'
const FORGOTTEN_KEY = 'pref.facet.style.0d7fcee5ee'
const FACTS: SemanticEntry[] = [
  {
    key: KEY,
    value_json: JSON.stringify({ cls: 'style', text: 'keep your answers short', stability: 1, cue: 'explicit' }),
    source: 'facet', confidence: 0.9,
  },
  {
    key: FORGOTTEN_KEY,
    value_json: JSON.stringify({ cls: 'style', text: 'less preamble', stability: 1, cue: 'explicit', forgotten: true }),
    source: 'facet', confidence: 0.9,
  },
  { key: 'user.favorite_language', value_json: '"Python"', source: 'user_explicit', confidence: 1 },
]
const FACET: MemoryFacet = {
  key: KEY, cls: 'style', text: 'keep your answers short', cue: 'explicit', stability: 1,
  stored_stability: 1, state: 'Active', updated_at: '2026-09-29T16:22:58Z', pinned: false, forgotten: false,
}

async function mount(query: Record<string, string>) {
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as object),
        memoryStats: () => Promise.resolve(null),
        memorySemantic: () => Promise.resolve(FACTS),
        memoryEpisodic: () => Promise.resolve([]),
        lessons: () => Promise.resolve([]),
        memoryGraph: () => Promise.resolve({ nodes: [], edges: [] }),
        memoryEntityGraph: () => Promise.resolve({ nodes: [], edges: [] }),
        memoryEntities: () => Promise.resolve({ entities: [], summary: {}, enabled: true }),
        memorySlots: () => Promise.resolve({ slots: [] }),
        memoryEntityProposals: () => Promise.resolve({ proposals: [], enabled: true }),
        memorySettings: () => Promise.resolve({ history_idle_hours: 2, history_max_days: 90 }),
        memoryVaultStatus: () => Promise.resolve(null),
        memoryFacets: () => Promise.resolve([FACET]),
      },
    }
  })
  const { MemoryPanel } = await import('./MemoryPanel')
  render(<MemoryPanel query={query} setQuery={() => {}} />)
}

beforeEach(() => {
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView
})
afterEach(() => { cleanup(); vi.resetModules(); vi.doUnmock('../../lib/api') })

describe('in the Studio', () => {
  it('lists the preference in words, not its key over raw JSON', async () => {
    await mount({})
    await waitFor(() => expect(screen.getByText('keep your answers short')).toBeTruthy())
    expect(screen.queryByText(KEY)).toBeNull()
    expect(screen.queryByText(/"cls"/)).toBeNull()
    expect(screen.getByText('Learned style preference')).toBeTruthy()
  })

  it('inspects it without a JSON editor or a Delete, and links where it is forgotten', async () => {
    await mount({})
    fireEvent.click(await screen.findByText('keep your answers short'))
    const manage = await screen.findByRole('link', { name: /Manage in Learned preferences/ })
    expect(manage.getAttribute('href')).toBe(`#/settings/memory?tab=settings&pref=${encodeURIComponent(KEY)}`)
    expect(screen.queryByRole('button', { name: /Edit the value/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Delete' })).toBeNull()
  })

  it('says a forgotten one is forgotten, not that the assistant still gets it', async () => {
    await mount({})
    fireEvent.click(await screen.findByText('less preamble'))
    expect(screen.getByText('Forgotten style preference')).toBeTruthy()
    await screen.findByText(/it no longer reaches the assistant/)
    expect(screen.queryByText(/shown to the assistant on every turn/)).toBeNull()
    expect(screen.queryByRole('link', { name: /Manage in Learned preferences/ })).toBeNull()
  })
})

describe('in Learned preferences', () => {
  it('opens at the row the chat linked, with focus on it', async () => {
    await mount({ tab: 'settings', pref: KEY })
    const text = await screen.findByText('keep your answers short')
    const row = text.closest('[aria-current]') as HTMLElement
    expect(row, 'the linked row is marked').not.toBeNull()
    await waitFor(() => expect(document.activeElement).toBe(row))
  })
})
