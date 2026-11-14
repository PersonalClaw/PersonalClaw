import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import { chat, mountList, navigate, project, running, waiting } from '../../test/projectListHarness'

// ── The folds hold what they say ─────────────────────────────────────────────────────────────────
//
// Two folds end the list. "Made for goal loops" holds the projects a loop made for itself
// (`made_for`), each saying which loop, or that its loop was deleted; its line carries the live
// counts, so nothing live hides behind it. "Not in a project" holds the chats in no project, its
// live rows counted by the same rule as a project's. A search that matches inside a fold opens it,
// and the owner can still close it; a new search hands it back. Each fold is a full-width outlined
// row; open, one outlined container holds it and its cards. No count at 0 is drawn.

const GARDEN = project('p-garden', 'Garden')
const GATE = project('p-gate', 'Fix the gate', { made_for: { work: 'loop:c0ffee01', kind: 'code', name: 'Fix the gate' } })
const DIGEST = project('p-digest', 'Weekly reading digest', { made_for: { work: 'loop:d1', kind: 'goal', name: 'Weekly reading digest' } })
const GONE = project('p-gone', 'Old spike', { made_for: { work: 'loop:dead', kind: '', name: '' } })
const UNREAD = project('p-unread', 'Unread', { made_for: { work: 'loop:x', kind: null, name: '' } })

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); resetDataStore(); navigate.mockReset() })
afterEach(() => { resetDataStore() })

describe('the made-for fold', () => {
  it('holds the projects a loop made, folded, its line carrying the live counts', async () => {
    mountList({ projects: [GARDEN, GATE, DIGEST, GONE], running: [running('loop:c0ffee01', 'Fix the gate', 'p-gate')], waiting: [] })
    const fold = await screen.findByRole('button', { name: /^Made for goal loops, 3 projects/ })
    await waitFor(() => expect(fold.getAttribute('aria-label')).toBe('Made for goal loops, 3 projects, 1 running'))
    expect(fold.getAttribute('aria-expanded')).toBe('false')
    const outside = (await screen.findAllByRole('heading', { level: 2 })).map((h) => h.textContent)
    expect(outside).toEqual(['Garden'])
    fireEvent.click(fold)
    expect(fold.getAttribute('aria-expanded')).toBe('true')
    const list = screen.getByRole('list', { name: 'Made for goal loops' })
    const inside = within(list)
    expect(inside.getAllByRole('heading', { level: 2 }).map((h) => h.textContent).sort()).toEqual(['Fix the gate', 'Old spike', 'Weekly reading digest'])
    expect([...list.children].map((c) => c.tagName), 'each card its own item').toEqual(['LI', 'LI', 'LI'])
    expect(fold.closest('[data-fold]')?.getAttribute('data-fold'), 'one container holds the row and its cards').toBe('open')
    expect(fold.closest('[data-fold]')?.contains(list)).toBe(true)
  })

  it("draws each card's line as the loop it was made for, a deleted one's and an unread one's included", async () => {
    mountList({ projects: [GARDEN, GATE, DIGEST, GONE, UNREAD], waiting: [], running: [] })
    fireEvent.click(await screen.findByRole('button', { name: /^Made for goal loops/ }))
    const lineOf = (name: string) => screen.getByRole('heading', { level: 2, name: new RegExp(`^${name}`) }).closest('li')?.querySelector('[data-line-two]')?.textContent
    expect(lineOf('Fix the gate')).toBe('Made for the code session Fix the gate')
    expect(lineOf('Weekly reading digest')).toBe('Made for the goal loop Weekly reading digest')
    expect(lineOf('Old spike')).toBe('Made for a loop that was deleted.')
    expect(lineOf('Unread')).toBe('Made for a loop.')
  })

  it('draws its row full width, with its count and its live counts at its end', async () => {
    mountList({ projects: [GARDEN, GATE], running: [running('loop:c0ffee01', 'Fix the gate', 'p-gate')], waiting: [] })
    const fold = await screen.findByRole('button', { name: /^Made for goal loops/ })
    await waitFor(() => expect(fold.textContent).toContain('1 running'))
    expect(fold.className).toContain('w-full')
    expect(fold.querySelector('[data-fold-count]')?.textContent).toBe('1 project')
    expect((fold.querySelector('[data-mark]') as HTMLElement).style.background).toBe('var(--color-state-active)')
    expect(fold.closest('[data-fold]')?.getAttribute('data-fold')).toBe('closed')
  })

  it('opens when a search matches inside it, and the owner can still close it', async () => {
    mountList({ projects: [GARDEN, GATE], query: { q: 'gate' } })
    const fold = await screen.findByRole('button', { name: /^Made for goal loops/ })
    expect(fold.getAttribute('aria-expanded')).toBe('true')
    fireEvent.click(fold)
    expect(fold.getAttribute('aria-expanded'), 'a fold the search opened closes').toBe('false')
  })

  it('hands a fold the owner opened back to the search when the search changes', async () => {
    mountList({ projects: [GARDEN, GATE] })
    const fold = await screen.findByRole('button', { name: /^Made for goal loops/ })
    fireEvent.click(fold)
    expect(fold.getAttribute('aria-expanded')).toBe('true')
    const search = screen.getByRole('searchbox', { name: /Search projects and chats/ })
    await userEvent.type(search, 'garden')
    await waitFor(() => expect(screen.queryByRole('button', { name: /^Made for goal loops/ })).toBeNull())
    await userEvent.clear(search)
    const back = await screen.findByRole('button', { name: /^Made for goal loops/ })
    expect(back.getAttribute('aria-expanded'), 'the owner\'s open was handed back to the search').toBe('false')
  })
})

describe('the fold of chats in no project', () => {
  it('draws no count at 0, and during a search only with a chat that matches', async () => {
    const asks = [waiting('question:q2', 'question', '', { work: 'chat:c-none', loop_kind: '', brief: { questions: [{ question: 'Which year?' }] } })]
    mountList({ projects: [GARDEN], sessions: [], waiting: asks, running: [] })
    const fold = await screen.findByRole('button', { name: /^Not in a project/ })
    await waitFor(() => expect(fold.getAttribute('aria-label')).toBe('Not in a project, 1 needs you'))
    expect(fold.querySelector('[data-fold-count]')).toBeNull()
  })

  it('is not drawn during a search no chat matches, though something in no project is live', async () => {
    const asks = [waiting('question:q3', 'question', '', { work: 'chat:c-none', loop_kind: '', brief: { questions: [{ question: 'Which year?' }] } })]
    mountList({ projects: [GARDEN], sessions: [], waiting: asks, running: [], query: { q: 'garden' } })
    await screen.findByRole('heading', { level: 2, name: /^Garden/ })
    expect(screen.queryByRole('button', { name: /^Not in a project/ })).toBeNull()
  })

  it('is not drawn during a search no chat in no project matches', async () => {
    const loose = chat('c-tax', 'A question about tax', '')
    mountList({ projects: [GARDEN], sessions: [loose], waiting: [], running: [], query: { q: 'garden' } })
    await screen.findByRole('heading', { level: 2, name: /^Garden/ })
    expect(screen.queryByRole('button', { name: /^Not in a project/ })).toBeNull()
  })

  it('counts its chats and its live rows by a project’s rule, and opens Chat history for the rest', async () => {
    const loose = chat('c-tax', 'A question about tax', '')
    const asks = [waiting('question:q1', 'question', '', { work: 'chat:c-tax', loop_kind: '', brief: { questions: [{ question: 'Which year?' }] } })]
    mountList({ projects: [GARDEN], sessions: [loose], waiting: asks, running: [] })
    const fold = await screen.findByRole('button', { name: /^Not in a project/ })
    await waitFor(() => expect(fold.getAttribute('aria-label')).toBe('Not in a project, 1 chat, 1 needs you'))
    await userEvent.click(fold)
    expect(await screen.findByText('Which year?')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Open Chat history' }))
    expect(navigate).toHaveBeenCalledWith('chat/history')
  })
})
