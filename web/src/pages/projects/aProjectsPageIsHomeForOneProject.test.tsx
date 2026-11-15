import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, within } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'
import { chat, mountPage, navigate, PID, running, waiting } from '../../test/projectPageHarness'

// ── A project's page is Home for one project (§3.3, §5.4, §5.5, §5.13) ──────────────────────────
//
// Needs you holds exactly the waiting items with this `project_id`; Running now exactly its running
// rows whose `waiting` is empty and whose work has no item in Needs you, so a loop's question, a plan
// waiting for launch and a run at a gate show once, in Needs you. Nothing shows in two groups, and an
// empty group is not drawn. The all-clear line shows only when both reads succeeded and nothing is
// unreadable. An unreadable source draws Home's line in Needs you, and with nothing else waiting in
// Garden, "Nothing else in Garden is waiting on you." under it, never Home's line for every project.

beforeEach(() => { resetDataStore(); localStorage.clear(); navigate.mockReset() })
afterEach(() => { cleanup(); vi.restoreAllMocks(); resetDataStore() })

const group = (name: string) => (screen.queryByRole('heading', { level: 2, name })?.closest('section, [data-group]') ?? null) as HTMLElement | null

describe("a project's page", () => {
  it('holds exactly its own waiting items in Needs you, and each piece of work once', async () => {
    mountPage({
      waiting: [
        waiting('w-1', 'loop_question', PID, { work: 'loop:l-1', brief: { question: 'Which bed first?' } }),
        waiting('w-2', 'plan_review', PID, { work: 'loop:l-2', brief: { step: 'Plan the beds' } }),
        waiting('w-3', 'gate', PID, { work: 'workflow:r-1', brief: { ask: 'Approve the order' } }),
        waiting('w-9', 'loop_question', 'p-other', { work: 'loop:l-9', brief: { question: 'Elsewhere?' } }),
      ],
      running: [
        running('loop:l-1', 'Plant the beds', PID, { waiting: [] }),
        running('loop:l-3', 'Water the beds', PID),
        running('loop:l-4', 'Blocked on you', PID, { waiting: ['w-x'] }),
        running('loop:l-8', 'Another project', 'p-other'),
      ],
    })
    const needs = await screen.findByRole('heading', { level: 2, name: 'Needs you' })
    const needsGroup = needs.closest('section')!
    expect(within(needsGroup).getAllByRole('listitem')).toHaveLength(3)
    expect(within(needsGroup).queryByText(/Elsewhere/)).toBeNull()
    const lane = await screen.findByText('Water the beds')
    expect(lane).toBeTruthy()
    expect(screen.queryByText('Plant the beds'), 'work with an item in Needs you was drawn again in Running now').toBeNull()
    expect(screen.queryByText('Blocked on you'), 'a row waiting on an answer was drawn as running').toBeNull()
    expect(screen.queryByText('Another project')).toBeNull()
  })

  it('draws no empty group, and the all-clear only when both reads succeeded and nothing is unreadable', async () => {
    // A chat that ran here once: the project is not new, so its all-clear shows rather than the first run.
    mountPage({ sessions: [chat('c-1', 'Seed ideas', PID)] })
    expect(await screen.findByText('Nothing needs you in Garden, and nothing is running.')).toBeTruthy()
    expect(group('Needs you')).toBeNull()
    expect(screen.queryByRole('heading', { level: 2, name: 'Running now' })).toBeNull()
    expect(screen.queryByRole('heading', { level: 2, name: 'Up next' })).toBeNull()
    expect(screen.queryByRole('heading', { level: 2, name: 'Tasks' })).toBeNull()
    expect(await screen.findByRole('heading', { level: 2, name: 'Recent' })).toBeTruthy()
  })

  it('says what could not be read, in the project’s form, and draws no all-clear', async () => {
    mountPage({ unreadable: ['approval', 'loop_question'] })
    const needs = await screen.findByRole('heading', { level: 2, name: 'Needs you' })
    const section = needs.closest('section')!
    expect(within(section).getByText(/^Could not read /)).toBeTruthy()
    expect(within(section).getByText('Nothing else in Garden is waiting on you.')).toBeTruthy()
    expect(screen.queryByText('Nothing else is waiting on you.')).toBeNull()
    expect(screen.queryByText('Nothing needs you in Garden, and nothing is running.')).toBeNull()
  })

  it('a project nothing has happened in yet says so, with starters, and no all-clear', async () => {
    mountPage({})
    expect(await screen.findByText('Nothing has happened in Garden yet. Chats, loops and tasks you start here show up on this page.')).toBeTruthy()
    expect(screen.queryByText('Nothing needs you in Garden, and nothing is running.')).toBeNull()
  })

  it("the header's About this project jumps to the section, and names no address", async () => {
    // The app's routes are its hash, so a `#<id>` link would leave the page for a route of that name.
    Element.prototype.scrollIntoView ??= () => {}
    const scrolled = vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(() => {})
    const { writes } = mountPage({ sessions: [chat('c-1', 'Seed ideas', PID)] })
    const jump = await screen.findByRole('button', { name: 'About this project' })
    expect(screen.queryByRole('link', { name: 'About this project' })).toBeNull()
    jump.click()
    expect(scrolled).toHaveBeenCalled()
    expect(navigate).not.toHaveBeenCalled()
    expect(writes).toEqual([])
  })

  it('draws no all-clear while the waiting read has failed', async () => {
    mountPage({ waiting: new Error('down') })
    expect(await screen.findByText(/Couldn't check what needs you or what's running in Garden/)).toBeTruthy()
    expect(screen.queryByText('Nothing needs you in Garden, and nothing is running.')).toBeNull()
  })
})
