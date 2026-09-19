/**
 * #485 — the prerequisite picker offers EVERY candidate, and says so when it cannot.
 *
 * `TaskCreatePage` loaded its candidate list with `api.tasks()` — no `limit`, so the server's
 * default of 50 applied. Past 50 tasks you simply could not select an existing task as a
 * prerequisite, and nothing indicated the list was partial: the picker looked complete and
 * answered "no such task".
 *
 * Asserted at the CALL SITE rather than only in the api client, because the defect was never in
 * the client — it was which read this page chose.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import type { TaskCollection } from '../../lib/api'

const collection = (over: Partial<TaskCollection> = {}): TaskCollection => ({
  tasks: [{ id: 't-1', title: 'a prerequisite' }] as TaskCollection['tasks'],
  total: 1,
  complete: true,
  owner: '',
  ...over,
})

let allTasks: ReturnType<typeof vi.fn>
let tasks: ReturnType<typeof vi.fn>

async function mountCreate(c: TaskCollection) {
  allTasks = vi.fn(() => Promise.resolve(c))
  tasks = vi.fn(() => Promise.resolve({ tasks: [], total: 0, complete: true, limit: 50, offset: 0 }))
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return { ...real, api: { ...(real.api as object), allTasks, tasks } }
  })
  const { TaskCreatePage } = await import('./TaskCreatePage')
  render(<TaskCreatePage onBack={() => {}} onCreated={() => {}} />)
  await waitFor(() => expect(allTasks).toHaveBeenCalled())
}

beforeEach(() => { cleanup(); vi.resetModules(); sessionStorage.clear() })
afterEach(() => cleanup())

describe('the create page loads its candidates', () => {
  it('collects them instead of taking one window', async () => {
    await mountCreate(collection())
    expect(allTasks).toHaveBeenCalledTimes(1)
    expect(tasks, 'the windowed read is the 50-row default this page must not use').not.toHaveBeenCalled()
  })

  it('says nothing when the list is whole', async () => {
    // Vacuity guard: the disclosure must not appear on every ordinary install.
    await mountCreate(collection({ total: 1, complete: true }))
    expect(document.querySelector('[data-partial]')).toBeNull()
  })

  it('discloses a partial candidate list, in the picker\'s own terms', async () => {
    await mountCreate(collection({ total: 12431, complete: false }))
    await waitFor(() => expect(document.querySelector('[data-partial]')).not.toBeNull())
    const el = screen.getByRole('status')
    expect(el.textContent).toContain('12,431')
    expect(el.textContent, 'the consequence, not just the count')
      .toMatch(/prerequisite outside this window cannot be picked/)
  })

  it('does not claim a truncation when the read FAILED', async () => {
    // 🪤 The failure path returns an empty collection, and "Showing 0 of 0" would be a confident
    // statement about data it never received. Empty-and-complete is the honest shape.
    allTasks = vi.fn(() => Promise.reject(new Error('gateway down')))
    tasks = vi.fn()
    vi.doMock('../../lib/api', async (orig) => {
      const real = await orig<Record<string, unknown>>()
      return { ...real, api: { ...(real.api as object), allTasks, tasks } }
    })
    const { TaskCreatePage } = await import('./TaskCreatePage')
    render(<TaskCreatePage onBack={() => {}} onCreated={() => {}} />)
    await waitFor(() => expect(allTasks).toHaveBeenCalled())
    expect(document.querySelector('[data-partial]')).toBeNull()
  })
})
