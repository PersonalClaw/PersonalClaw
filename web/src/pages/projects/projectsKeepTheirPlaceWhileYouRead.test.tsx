import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { invalidateKeys, resetDataStore } from '../../lib/data'
import { projectOrder } from '../../lib/projectPulse'
import { mountList, navigate, project } from '../../test/projectListHarness'

// ── Projects keep their place while you read ─────────────────────────────────────────────────────
//
// Pinned first, in the pins' own order, then the newest activity first, ties by name: the order
// every list of projects takes (`projectOrder`). It is taken when the page opens and kept while it
// is open, so a project never jumps under the owner's eyes; its status never moves a row. Try again
// takes it afresh once the reads it asked again have answered. Pin changes queue, each from the
// pins the one before it wrote.

const at = (iso: string) => ({ updated_at: iso })
const OLD = project('p-old', 'Old', at('2026-11-01T10:00:00Z'))
const NEW = project('p-new', 'New', at('2026-11-12T09:00:00Z'))
const MID = project('p-mid', 'Mid', at('2026-11-10T09:00:00Z'))
const PINNED = project('p-pin', 'Pinned', at('2026-10-01T09:00:00Z'))

const names = async () => (await screen.findAllByRole('heading', { level: 2 })).map((h) => h.textContent)

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); resetDataStore(); navigate.mockReset() })
afterEach(() => { resetDataStore() })

describe('the order of projects', () => {
  it('is the pins in their order, then the newest activity, ties by name', () => {
    const tie = project('p-tie', 'Another', at('2026-11-10T09:00:00Z'))
    const ms = (p: { updated_at?: string }) => Date.parse(p.updated_at ?? '')
    const all = [OLD, NEW, MID, PINNED, tie]
    expect(projectOrder(all, ['p-pin', 'p-old'], (id) => ms(all.find((p) => p.id === id)!)).map((p) => p.name))
      .toEqual(['Pinned', 'Old', 'New', 'Another', 'Mid'])
  })

  it('is the list’s order when the page opens', async () => {
    mountList({ projects: [OLD, NEW, MID, PINNED], pins: ['p-pin'] })
    await waitFor(async () => expect(await names()).toEqual(['Pinned', 'New', 'Mid', 'Old']))
  })

  it('holds while the page is open: new activity moves no row', async () => {
    mountList({ projects: [OLD, NEW, MID], pins: [] })
    await waitFor(async () => expect(await names()).toEqual(['New', 'Mid', 'Old']))
    // Old does something: it is the newest now, and the list is read again.
    const { api } = await import('../../lib/api')
    const reads = vi.mocked(api.projects)
    reads.mockResolvedValue([project('p-old', 'Old', at('2026-11-12T09:59:00Z')), NEW, MID])
    act(() => invalidateKeys('projects:list'))
    await waitFor(() => expect(reads.mock.calls.length).toBeGreaterThanOrEqual(2))
    await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
    expect(await names()).toEqual(['New', 'Mid', 'Old'])
  })

  it('is taken afresh by Try again from what the reads answer, not from what was there at the press', async () => {
    mountList({ projects: [OLD, NEW, MID], running: new Error('500'), pins: [] })
    await waitFor(async () => expect(await names()).toEqual(['New', 'Mid', 'Old']))
    const { api } = await import('../../lib/api')
    vi.mocked(api.projects).mockResolvedValue([project('p-old', 'Old', at('2026-11-12T09:59:00Z')), NEW, MID])
    vi.mocked(api.running).mockResolvedValue({ running: [], finished: [] })
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(async () => expect(await names()).toEqual(['Old', 'New', 'Mid']))
  })
})

describe('the pins', () => {
  it('two quick pins both land: each change starts from the one before it', async () => {
    const { updateSettings } = mountList({ projects: [OLD, NEW], pins: [] })
    let release: () => void = () => {}
    const gate = new Promise<void>((r) => { release = r })
    const real = updateSettings.getMockImplementation()!
    updateSettings.mockImplementation(async (body) => { await gate; return real(body) })
    const pinFrom = async (name: string) => {
      const li = (await screen.findByRole('heading', { level: 2, name: new RegExp(`^${name}`) })).closest('li') as HTMLElement
      await userEvent.click(within(li).getByRole('button', { name: `More actions for ${name}` }))
      await userEvent.click(await screen.findByRole('button', { name: 'Pin to the top' }))
    }
    await pinFrom('Old')
    await pinFrom('New')
    release()
    await waitFor(() => expect(updateSettings).toHaveBeenLastCalledWith({ pinned_project_ids: ['p-old', 'p-new'] }))
  })
})
