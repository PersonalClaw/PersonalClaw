/**
 * Settings → Memory says when a read failed, instead of spinning or claiming an answer.
 *
 * Four reads in this panel resolved a failure to a value (`.catch(() => null)` or `setLinks([])`):
 *
 *   - the Settings tab's own read: `null` held the form's skeleton up forever, with no message and
 *     no Retry, and `persist` kept the `null` for the next visit to paint the same spinner;
 *   - the Health tab's lint: `null` rendered "No issues flagged — memory is clean", the one
 *     reassurance a health check exists to give, about memory nobody had checked;
 *   - its observability read: `null` hid the whole section;
 *   - an entity's backlinks: `[]` rendered "Nothing links here yet — … this entity may be worth
 *     removing", advice to delete an entity because its links could not be read.
 *
 * And a refused Settings save left the control on the value the server had refused. Driven through
 * the real panel, with the failure the browser really produces (`Failed to fetch`), so the sentence
 * asserted is the component's own and not a message the test supplied.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import type { MemoryEntity } from '../../lib/api'

const offline = () => Promise.reject(new TypeError('Failed to fetch'))

const SETTINGS = { history_idle_hours: 2, history_max_days: 90, semantic_confidence_threshold: 0.8 }
const LINT = { flags: [], auto_fixed: {} }
const OBS = {
  stats: { semantic: 3 },
  rejections: {},
  context_preview: { total_chars: 10, semantic_chars: 5, episodic_chars: 5, lessons_chars: 0 },
}
const ENTITY = {
  id: 'ent_a1b2c3d4', name: 'Kettle Creek Market', entity_type: 'place', aliases: [],
  source: 'user', inbound_count: 3, last_linked_at: '2026-02-01T00:00:00Z',
} as unknown as MemoryEntity

let saveMemorySettings: ReturnType<typeof vi.fn>

async function mount(query: Record<string, string>, over: Record<string, unknown>) {
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as object),
        memoryStats: () => Promise.resolve(null),
        memorySettings: () => Promise.resolve(SETTINGS),
        memoryVaultStatus: () => Promise.resolve(null),
        memoryFacets: () => Promise.resolve([]),
        memoryLint: () => Promise.resolve(LINT),
        memoryObservability: () => Promise.resolve(OBS),
        memoryVolunteerPrecision: () => Promise.resolve(null),
        memorySemantic: () => Promise.resolve([]),
        memoryEpisodic: () => Promise.resolve([]),
        lessons: () => Promise.resolve([]),
        memoryGraph: () => Promise.resolve({ nodes: [], edges: [] }),
        memoryEntityGraph: () => Promise.resolve({ nodes: [], edges: [] }),
        memoryEntities: () => Promise.resolve({
          entities: [ENTITY], summary: {}, enabled: true, ranking: { degraded: false, summary: '' },
        }),
        memorySlots: () => Promise.resolve({ slots: [] }),
        memoryEntityProposals: () => Promise.resolve({ proposals: [], enabled: true }),
        memoryEntityBacklinks: () => Promise.resolve({ links: [] }),
        saveMemorySettings,
        ...over,
      },
    }
  })
  const { MemoryPanel } = await import('./MemoryPanel')
  return render(<MemoryPanel query={query} setQuery={() => {}} />)
}

beforeEach(() => {
  cleanup()
  vi.resetModules()
  sessionStorage.clear()
  saveMemorySettings = vi.fn(() => Promise.resolve({ ok: true }))
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView
})
afterEach(() => cleanup())

describe('the Settings tab', () => {
  it('says a failed read, with a Retry, instead of a skeleton that never leaves', async () => {
    let calls = 0
    await mount({ tab: 'settings' }, {
      memorySettings: () => (++calls === 1 ? offline() : Promise.resolve(SETTINGS)),
    })
    expect(await screen.findByText("Couldn't load your memory settings")).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }))
    expect(await screen.findByRole('spinbutton', { name: 'Learned-fact confidence' })).toBeTruthy()
    expect(calls).toBe(2)
  })

  it('puts a refused save back, so the control does not show a value the server refused', async () => {
    saveMemorySettings = vi.fn(() => Promise.reject(new Error('memory config is read-only')))
    await mount({ tab: 'settings' }, {})
    const idle = (await screen.findByRole('spinbutton', { name: 'Idle before history rollup (hours)' })) as HTMLInputElement
    await waitFor(() => expect(idle.value).toBe('2'))
    fireEvent.change(idle, { target: { value: '5' } })
    fireEvent.blur(idle)
    await waitFor(() => expect(saveMemorySettings).toHaveBeenCalledWith({ history_idle_hours: 5 }))
    await waitFor(() => expect(idle.value).toBe('2'))
  })
})

describe('the Health tab', () => {
  it('a failed lint is said, never "memory is clean"', async () => {
    await mount({ tab: 'health' }, { memoryLint: offline })
    expect(await screen.findByText("Couldn't load memory health.")).toBeTruthy()
    expect(screen.queryByText(/memory is clean/), 'a health check nobody ran is not a clean one').toBeNull()
    // The rest of the tab still renders what it could read.
    expect(screen.getByText('Observability')).toBeTruthy()
  })

  it('a failed observability read is said in its own section, not dropped', async () => {
    await mount({ tab: 'health' }, { memoryObservability: offline })
    expect(await screen.findByText("Couldn't load memory observability.")).toBeTruthy()
    expect(screen.getByText(/memory is clean/), 'the lint that did load still answers').toBeTruthy()
  })

  it('both failing is said once, for the tab', async () => {
    await mount({ tab: 'health' }, { memoryLint: offline, memoryObservability: offline })
    expect(await screen.findByText("Couldn't load your memory health")).toBeTruthy()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeTruthy()
  })
})

describe("an entity's backlinks", () => {
  it('a failed read is said, and never advises removing the entity', async () => {
    await mount({}, { memoryEntityBacklinks: offline })
    await waitFor(() => expect(screen.getByText('Kettle Creek Market')).toBeTruthy())
    fireEvent.click(screen.getByText('Kettle Creek Market'))
    expect(await screen.findByText("Couldn't load what links here.")).toBeTruthy()
    expect(screen.queryByText(/may be worth removing/), 'a failed read is not evidence nothing links here').toBeNull()
  })
})
