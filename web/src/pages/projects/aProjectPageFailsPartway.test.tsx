import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'
import { board, chat, failed, libraryItem, libraryRead, mountPage, PID, task, taskList, workRow } from '../../test/projectPageHarness'

// ── A project's page that fails partway (§5.13) ─────────────────────────────────────────────────
//
// Each source failing alone draws its line and the rest: the runs, the loops, the tasks, the thread
// read, the always-on read, waiting or running. A `?open=` naming a gone task says "This task no
// longer exists." with Close. The project read itself: a 404 draws "This project no longer exists."
// with Back to Projects, and any other failure `LoadError` with `what="project"`. The library read:
// a store it names in `failed` draws that part's line (Results' or Knowledge's) and the other part's
// rows, and a failed request draws both lines.

beforeEach(() => { resetDataStore() })
afterEach(() => { cleanup(); vi.restoreAllMocks(); resetDataStore() })

const REST = 'The rest of the page is still here.'
const seeded = { sessions: [chat('c-1', 'Seed ideas', PID)], lists: [taskList('tl-own', 'Garden')], tasks: [task('t-1', 'Order the soil', 'tl-own')] }

describe("a project's page fails partway", () => {
  it("the runs: Up next and Recent say so, and the rest draw", async () => {
    mountPage({ ...seeded, board: board([workRow('l-1', 'Plan the beds', { state: 'suspended' })], [{ name: 'runs', items: [], status: 'error', error: 'down', loadedAt: 0 }]) })
    expect((await screen.findAllByText(`Couldn't load its runs. ${REST}`)).length).toBeGreaterThan(0)
    expect(await screen.findByText('Plan the beds')).toBeTruthy()
    expect(screen.getByText('Order the soil')).toBeTruthy()
  })

  it('the loops: Up next says so', async () => {
    mountPage({ ...seeded, board: board([], [{ name: 'loops', items: [], status: 'error', error: 'down', loadedAt: 0 }]) })
    expect(await screen.findByText(`Couldn't load its loops. ${REST}`)).toBeTruthy()
  })

  it('the tasks: the group says so and draws no count', async () => {
    mountPage({ ...seeded, tasks: new Error('down') })
    expect(await screen.findByText(`Couldn't load its tasks. ${REST}`)).toBeTruthy()
    expect(screen.queryByText(/^\d+ open$/)).toBeNull()
  })

  it('the thread read: Recent says so', async () => {
    mountPage({ ...seeded, sessions: new Error('down') })
    expect(await screen.findByText(`Couldn't load its chats and loops. ${REST}`)).toBeTruthy()
  })

  it('waiting or running: one line at the top, and no all-clear', async () => {
    mountPage({ ...seeded, running: new Error('down') })
    expect(await screen.findByText(`Couldn't check what needs you or what's running in Garden. ${REST}`)).toBeTruthy()
    expect(screen.queryByText('Nothing needs you in Garden, and nothing is running.')).toBeNull()
  })

  it('a ?open= naming a gone task says so, with Close', async () => {
    mountPage({ ...seeded, query: { open: 'task:t-gone' } })
    expect(await screen.findByText('This task no longer exists.')).toBeTruthy()
    expect(screen.getAllByRole('button', { name: 'Close' }).length).toBeGreaterThan(0)
  })

  it('the project read: a 404 says the project is gone, with Back to Projects', async () => {
    mountPage({ project: failed(404, 'not found') })
    expect(await screen.findByText('This project no longer exists.')).toBeTruthy()
    // The body's way back (the header draws its own on a phone).
    expect(screen.getAllByRole('button', { name: 'Back to Projects' }).length).toBeGreaterThan(0)
  })

  it("the project read: any other failure is LoadError's, never a deletion", async () => {
    mountPage({ project: failed(500, 'the gateway is restarting') })
    expect(await screen.findByText("Couldn't load your project")).toBeTruthy()
    expect(screen.getByText(/the gateway is restarting/)).toBeTruthy()
    expect(screen.queryByText('This project no longer exists.')).toBeNull()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeTruthy()
  })

  // The library read, split by store: a store it names in `failed` draws its own line and no count,
  // while the other draws its rows; only a failed request draws both lines.
  const LIB = [libraryItem('artifact:report-1', 'Spring planting plan'), libraryItem('knowledge:k-1', 'Tomato varieties')]

  it("the library's artifacts store: Results says so, with no count, and Knowledge draws its rows", async () => {
    mountPage({ sessions: [chat('c-1', 'Seed ideas', PID)], library: libraryRead(LIB, { knowledge: 6 }, ['artifacts']) })
    expect(await screen.findByText(/^Couldn't load what its work made\. The rest of the page is still here\./)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'See all in Library' })).toBeTruthy()
    expect(screen.getByText('Tomato varieties')).toBeTruthy()
    expect(screen.getByText('6 items')).toBeTruthy()
    expect(screen.queryByText(/Couldn't load its knowledge/)).toBeNull()
  })

  it("the library's knowledge store: Knowledge says so, with no count, and Results draws its tiles", async () => {
    mountPage({ sessions: [chat('c-1', 'Seed ideas', PID)], library: libraryRead(LIB, { artifacts: 12 }, ['knowledge']) })
    expect(await screen.findByText(/^Couldn't load its knowledge\. The rest of the page is still here\./)).toBeTruthy()
    expect(screen.queryByText(/ items$/)).toBeNull()
    expect(screen.getByRole('button', { name: /^Spring planting plan/ })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'See all 12 in Library' })).toBeTruthy()
    expect(screen.queryByText(/Couldn't load what its work made/)).toBeNull()
  })

  it('the library request: both stores say so', async () => {
    mountPage({ sessions: [chat('c-1', 'Seed ideas', PID)], library: failed(500) })
    expect(await screen.findByText(/^Couldn't load what its work made\./)).toBeTruthy()
    expect(screen.getByText(/^Couldn't load its knowledge\./)).toBeTruthy()
    expect(screen.getByRole('heading', { level: 2, name: 'Recent' })).toBeTruthy()
  })
})
