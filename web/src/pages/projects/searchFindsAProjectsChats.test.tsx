import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import { chat, mountList, navigate, project, waiting } from '../../test/projectListHarness'

// ── Search finds a project's chats ───────────────────────────────────────────────────────────────
//
// The list's search matches project names and the titles of their chats, goal loops and code
// sessions. A match shows under its project as its own row; one whose work waits on you says
// "Waiting on you" with its item's title as its line, the words its card uses. At most five under a
// project, then "N more matches in Garden", which opens the project searched. No match says so,
// with Clear search.

const GARDEN = project('p-garden', 'Garden')
const TRIP = project('p-trip', 'Trip')
const SEEDS = Array.from({ length: 7 }, (_, i) => chat(`c-seed${i}`, `Seed order ${i + 1}`, 'p-garden'))

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); resetDataStore(); navigate.mockReset() })
afterEach(() => { resetDataStore() })

const headings = async () => (await screen.findAllByRole('heading', { level: 2 })).map((h) => h.textContent)

describe("the list's search", () => {
  it('matches a project by its name, and its chats by their titles', async () => {
    mountList({ projects: [GARDEN, TRIP], sessions: [chat('c-pack', 'Pack the tent', 'p-trip')], query: { q: 'tent' } })
    await waitFor(async () => expect(await headings()).toEqual(['Trip']))
    expect(screen.getByRole('button', { name: 'Chat: Pack the tent' })).toBeInTheDocument()
    // Trip is found by its chat, so it is that chat that matches; a project counts by its own name.
    expect(document.querySelector('[data-list-line]')?.textContent).toBe('1 chat match')
  })

  it('counts a project matched by its name, and says the list holds its cards at rest', async () => {
    mountList({ projects: [GARDEN, TRIP], sessions: [chat('c-trip', 'Trip notes', 'p-trip')], query: { q: 'trip' } })
    await waitFor(() => expect(document.querySelector('[data-list-line]')?.textContent).toBe('1 project and 1 chat match'))
  })

  it('at rest counts the cards, the folds aside', async () => {
    const gate = { ...GARDEN, id: 'p-gate', name: 'Fix the gate', made_for: { work: 'loop:c1', kind: 'code', name: 'Fix the gate' } }
    mountList({ projects: [GARDEN, TRIP, gate] })
    await waitFor(() => expect(document.querySelector('[data-list-line]')?.textContent).toBe('2 projects'))
  })

  it('shows at most five matches under a project, then opens the project searched', async () => {
    mountList({ projects: [GARDEN], sessions: SEEDS, query: { q: 'seed' } })
    const garden = (await screen.findByRole('heading', { level: 2, name: /^Garden/ })).closest('li') as HTMLElement
    await waitFor(() => expect(garden.querySelectorAll('[data-work-row="thread"]')).toHaveLength(5))
    const more = within(garden).getByRole('button', { name: '2 more matches in Garden' })
    expect(more.className, 'a 44 px target at the compact widths').toContain('max-lg:min-h-11')
    await userEvent.click(more)
    expect(navigate).toHaveBeenCalledWith('projects/p-garden?q=seed')
  })

  it("a match whose work waits on you says so, with its item's title as its line", async () => {
    const beds = chat('c-beds', 'Plan the spring beds', 'p-garden')
    const ask = waiting('approval:a1', 'approval', 'p-garden', { work: 'chat:c-beds', loop_kind: '', brief: { tool: 'bash', tool_input: 'npm run sensor:calibrate' } })
    mountList({ projects: [GARDEN], sessions: [beds], waiting: [ask], query: { q: 'spring' } })
    const row = await screen.findByRole('button', { name: /^Chat: Plan the spring beds, waiting on you/ })
    const li = row.closest('li') as HTMLElement
    expect(within(li).getByText('bash(npm run sensor:calibrate)').className, "an approval's call, in mono").toContain('font-mono')
  })

  it('draws a match with no state by its kind, glyph and word, and its date as its meta', async () => {
    const old = chat('c-old', 'Raised bed frames', 'p-garden', { last_activity_ts: '2026-11-05T10:00:00Z' })
    mountList({ projects: [GARDEN], sessions: [old], query: { q: 'bed' } })
    const row = await screen.findByRole('button', { name: 'Chat: Raised bed frames' })
    const li = row.closest('li') as HTMLElement
    const state = li.querySelector('[data-row-state]') as HTMLElement
    expect(state.querySelector('[data-row-glyph]')).toBeTruthy()
    expect(state.textContent).toBe('Chat')
    expect(li.textContent?.endsWith('Nov 5'), li.textContent ?? '').toBe(true)
  })

  it('says when nothing matches, with Clear search', async () => {
    const { address } = mountList({ projects: [GARDEN], query: { q: 'gate' } })
    expect(await screen.findByText('No project or chat matches “gate”.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Clear search' }))
    await waitFor(() => expect(address.query.q).toBeUndefined())
  })
})
