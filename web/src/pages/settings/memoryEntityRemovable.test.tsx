/**
 * #524 — an entity can be removed from the studio, and the dialog says what goes.
 *
 * The studio offered Delete for a fact, an episode and a lesson, and nothing for an entity — while
 * the same panel proposes NEW entities to accept. The panel's own comment gave the reason: "an
 * entity's removal has to reason about the links pointing at it — which is the graph-maintenance
 * path, not this one." The store settled that when it shipped (`delete_entity` drops the links
 * pointing at the entity and keeps the records), so what was missing is a dialog that tells the
 * user that answer.
 *
 * Driven through the real panel rather than a lifted handler: the defect was an affordance that did
 * not exist on a surface, and `deletable` gating it is exactly the kind of line a unit test on the
 * handler would step over.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import type { MemoryEntity } from '../../lib/api'

const ENTITY: MemoryEntity = {
  id: 'ent_a1b2c3d4',
  name: 'Kettle Creek Market',
  entity_type: 'place',
  aliases: ['KCM'],
  source: 'user',
  inbound_count: 3,
  last_linked_at: '2026-02-01T00:00:00Z',
} as MemoryEntity

let deleteEntity: ReturnType<typeof vi.fn>
let confirmDelete: ReturnType<typeof vi.fn>
let notify: ReturnType<typeof vi.fn>

/** The confirm options the user would have been shown for the last dialog raised. */
const lastConfirm = () => confirmDelete.mock.calls.at(-1) ?? []

async function mountStudio(over: { entity?: Partial<MemoryEntity>; deleteFails?: boolean } = {}) {
  const entity = { ...ENTITY, ...(over.entity ?? {}) }
  deleteEntity = vi.fn(() =>
    over.deleteFails ? Promise.reject(new Error('memory.db is locked')) : Promise.resolve({ ok: true })
  )
  confirmDelete = vi.fn(() => Promise.resolve(true))
  notify = vi.fn()

  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as object),
        memoryStats: () => Promise.resolve(null),
        memorySemantic: () => Promise.resolve([]),
        memoryEpisodic: () => Promise.resolve([]),
        lessons: () => Promise.resolve([]),
        memoryGraph: () => Promise.resolve({ nodes: [], edges: [] }),
        memoryEntityGraph: () => Promise.resolve({ nodes: [], edges: [] }),
        memoryEntities: () => Promise.resolve({ entities: [entity], summary: {}, enabled: true }),
        memorySlots: () => Promise.resolve({ slots: [] }),
        memoryEntityProposals: () => Promise.resolve({ proposals: [], enabled: true }),
        memoryEntityBacklinks: () => Promise.resolve({ links: [] }),
        memoryEntityDelete: deleteEntity,
      },
    }
  })
  vi.doMock('../../ui/dialog', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return { ...real, confirmDelete }
  })
  vi.doMock('../../app/appSdk', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return { ...real, notify }
  })

  const { MemoryPanel } = await import('./MemoryPanel')
  render(<MemoryPanel query={{}} setQuery={() => {}} />)
  // The row lands in the explorer list once `settings:memory-entities` resolves.
  await waitFor(() => expect(screen.getByText('Kettle Creek Market')).toBeTruthy())
  fireEvent.click(screen.getByText('Kettle Creek Market'))
  return entity
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

describe('the entity inspector', () => {
  it('offers Delete at all', async () => {
    // The whole issue in one assertion: this control did not exist for an entity.
    await mountStudio()
    await waitFor(() => expect(screen.getAllByRole('button', { name: /^delete$/i }).length).toBeGreaterThan(0))
  })

  it('deletes the entity the user selected', async () => {
    await mountStudio()
    fireEvent.click((await screen.findAllByRole('button', { name: /^delete$/i }))[0])
    await waitFor(() => expect(deleteEntity).toHaveBeenCalledWith('ent_a1b2c3d4'))
  })

  it('names the entity and states what happens to the linked memories', async () => {
    // 🔑 The blast radius. "This cannot be undone" alone would not answer the question the panel
    // was deferring on — a user needs to know whether their MEMORIES go with it.
    await mountStudio()
    fireEvent.click((await screen.findAllByRole('button', { name: /^delete$/i }))[0])
    await waitFor(() => expect(confirmDelete).toHaveBeenCalled())
    const [noun, name, opts] = lastConfirm()
    expect(noun).toBe('entity')
    expect(name).toBe('Kettle Creek Market')
    expect(String(opts?.body)).toMatch(/3 memories link to it/i)
    expect(String(opts?.body)).toMatch(/links are dropped/i)
    expect(String(opts?.body), 'the memories are NOT deleted, and that is the point').toMatch(/memories themselves stay/i)
  })

  it('says nothing links to it when nothing does', async () => {
    // The count comes from the row, so an unlinked entity must not be told "0 memories link to it".
    await mountStudio({ entity: { inbound_count: 0 } })
    fireEvent.click((await screen.findAllByRole('button', { name: /^delete$/i }))[0])
    await waitFor(() => expect(confirmDelete).toHaveBeenCalled())
    expect(String(lastConfirm()[2]?.body)).toMatch(/nothing links to it yet/i)
  })

  it('speaks in the singular for one memory', async () => {
    await mountStudio({ entity: { inbound_count: 1 } })
    fireEvent.click((await screen.findAllByRole('button', { name: /^delete$/i }))[0])
    await waitFor(() => expect(confirmDelete).toHaveBeenCalled())
    const body = String(lastConfirm()[2]?.body)
    expect(body).toMatch(/1 memory links to it/i)
    expect(body).toMatch(/memory itself stays/i)
  })

  it('deletes nothing when the dialog is dismissed', async () => {
    await mountStudio()
    confirmDelete.mockResolvedValueOnce(false)
    fireEvent.click((await screen.findAllByRole('button', { name: /^delete$/i }))[0])
    await waitFor(() => expect(confirmDelete).toHaveBeenCalled())
    expect(deleteEntity).not.toHaveBeenCalled()
  })

  it('REPORTS a failed delete instead of letting the row quietly return', async () => {
    // This file's own rule for the sibling deletes: a destructive action the user confirmed must
    // not fail silently. The list refetches on failure, so without the report the entity reappears
    // with nothing said.
    await mountStudio({ deleteFails: true })
    fireEvent.click((await screen.findAllByRole('button', { name: /^delete$/i }))[0])
    await waitFor(() => expect(notify).toHaveBeenCalled())
    const [message, tone] = notify.mock.calls.at(-1) ?? []
    expect(String(message)).toMatch(/couldn't delete this entity/i)
    expect(String(message), 'the server’s reason, not a generic failure').toMatch(/locked/i)
    expect(tone).toBe('error')
  })
})
