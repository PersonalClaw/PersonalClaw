import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import { projectPulse } from '../../lib/projectPulse'
import { NOW, chat, mountList, navigate, project, running, waiting } from '../../test/projectListHarness'

// ── The Projects page is one list ────────────────────────────────────────────────────────────────
//
// One card per project, each saying what needs you and what is running in it, from Home's own reads
// (`GET /api/waiting`, `GET /api/running`) filtered by the `project_id` the gateway decided once for
// each piece of work, so a project's counts and Home's agree. A count at 0 is not drawn. Under each
// card, at most three live rows, then "N more in Garden". Each row draws only what its item's own
// fields carry: an approval is its call as its card writes it, `tool(args)`, with its purpose, its
// work's kind and its age. Each state's mark is a ringed dot in its state family's tone, work held up
// a ring in the quiet one, and work that failed in the danger tone. Each card keeps its form at
// every width, every control it draws is a 44 px target on a phone, and past the shared threshold
// the list windows.

const GARDEN = project('p-garden', 'Garden', { workspace_dir: '/home/me/code/garden', brief: 'Plan and grow the spring beds. Keep the soil sensors working.' })
const TRIP = project('p-trip', 'Trip')

const ASKS = [
  waiting('approval:a1', 'approval', 'p-garden', {
    work: 'loop:beds', loop_kind: 'goal',
    brief: { tool: 'bash', tool_input: 'npm run sensor:calibrate', tool_purpose: 'Calibrate the soil sensors after the rain' },
  }),
  waiting('loop_question:q1', 'loop_question', 'p-garden', { work: 'loop:seeds', brief: { question: 'Which seed vendor should I order from?' } }),
  waiting('plan_review:p1', 'plan_review', 'p-garden', { work: 'loop:plan', brief: { step: { title: 'Lay out the beds' } } }),
  waiting('gate:g1', 'gate', 'p-garden', { work: 'workflow:r1', loop_kind: '', brief: { ask: { prompt: 'Ship the harvest log?' } } }),
]

beforeEach(() => { vi.useFakeTimers({ toFake: ['Date'] }); vi.setSystemTime(NOW); localStorage.clear(); sessionStorage.clear(); resetDataStore(); navigate.mockReset() })
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); resetDataStore() })

const card = async (name: string) => (await screen.findByRole('heading', { level: 2, name: new RegExp(`^${name}\\b`) })).closest('li') as HTMLElement

describe('the Projects page', () => {
  it('draws one card per project, each with its name as its heading', async () => {
    mountList({ projects: [GARDEN, TRIP] })
    const headings = await screen.findAllByRole('heading', { level: 2 })
    expect(headings.map((h) => h.textContent)).toEqual(expect.arrayContaining(['Garden', 'Trip']))
    expect(headings).toHaveLength(2)
  })

  it('counts a loop’s question, a plan waiting for launch and a run at a gate once each, as needing you', async () => {
    const work = running('loop:fence', 'Fix the irrigation sensor tests', 'p-garden', { line: '18 of 24 passed' })
    // The run at the gate is also on the running list, waiting: it counts once, where it needs you.
    const atGate = running('workflow:r1', 'Harvest log', 'p-garden', { kind: 'workflow', waiting: ['gate:g1'] })
    mountList({ projects: [GARDEN, TRIP], waiting: ASKS, running: [work, atGate] })
    const garden = await card('Garden')
    await waitFor(() => expect(within(garden).getByText('4 need you')).toBeInTheDocument())
    expect(within(garden).getByText('1 running')).toBeInTheDocument()
    // Home's own count of the same reads.
    const pulse = projectPulse('p-garden', { waiting: ASKS, unreadable: [] }, { running: [work, atGate] }, [], GARDEN)
    expect([pulse.needsYou.length, pulse.running.length]).toEqual([4, 1])
    const heading = within(garden).getByRole('heading', { level: 2 })
    expect(heading.getAttribute('aria-label')).toMatch(/^Garden, 4 need you, 1 running, active \d+ minutes? ago$/)
  })

  it('draws no count at 0', async () => {
    mountList({ projects: [GARDEN, TRIP], waiting: ASKS, running: [] })
    const trip = await card('Trip')
    await waitFor(() => expect(within(screen.getByRole('list', { name: 'Projects' })).getAllByText(/need you|running/).length).toBeGreaterThan(0))
    expect(within(trip).queryByText(/need you|needs you|running/)).toBeNull()
    expect(within(await card('Garden')).queryByText(/running/)).toBeNull()
  })

  it('shows at most three live rows, then "N more in Garden", which opens it', async () => {
    mountList({ projects: [GARDEN], waiting: ASKS, running: [running('loop:fence', 'Fix the tests', 'p-garden')] })
    const garden = await card('Garden')
    await waitFor(() => expect(garden.querySelectorAll('[data-work-row]')).toHaveLength(3))
    const more = within(garden).getByRole('button', { name: '2 more in Garden' })
    more.click()
    expect(navigate).toHaveBeenCalledWith('projects/p-garden')
  })

  it("draws an approval's row as its card writes its call, with its purpose, its work's kind and its age", async () => {
    mountList({ projects: [GARDEN], waiting: [ASKS[0]] })
    const garden = await card('Garden')
    const row = await waitFor(() => {
      const r = garden.querySelector('[data-work-row="waiting"]') as HTMLElement
      expect(r).toBeTruthy()
      return r
    })
    expect(within(row).getByText('bash(npm run sensor:calibrate)').className).toContain('font-mono')
    expect(within(row).getByText('Calibrate the soil sensors after the rain')).toBeInTheDocument()
    expect(within(row).getByText('Goal loop · 6 min')).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'Goal loop: bash(npm run sensor:calibrate), waiting on you' })).toBeInTheDocument()
  })

  it('draws no words a row’s fields do not carry', async () => {
    // A loop's question with no "why" and a gate with no loop: no line, and no kind it was not given.
    const bare = waiting('gate:g2', 'gate', 'p-garden', { work: '', loop_kind: '', age_secs: null, brief: { ask: { prompt: 'Ship it?' } } })
    mountList({ projects: [GARDEN], waiting: [bare] })
    const garden = await card('Garden')
    const row = await waitFor(() => {
      const r = garden.querySelector('[data-work-row="waiting"]') as HTMLElement
      expect(r).toBeTruthy()
      return r
    })
    expect(row.textContent).toBe('Waiting on youShip it?')
  })

  it("marks each state in its family's tone, ringed in it, and draws red only for work that failed", async () => {
    const failed = running('loop:broke', 'Broken build', 'p-garden', { phase: 'error', line: 'Stopped: the tests failed' })
    mountList({ projects: [GARDEN], waiting: [ASKS[1], ASKS[0]], running: [running('loop:fence', 'Fix the tests', 'p-garden'), failed] })
    const garden = await card('Garden')
    await waitFor(() => expect(garden.querySelectorAll('[data-work-row]')).toHaveLength(3))
    const tone = (m: HTMLElement) => ({ fill: m.style.background, ring: m.style.borderColor, ringed: m.className.includes('border-[1.5px]') })
    const marks = [...garden.querySelectorAll('[data-work-row]')].map((r) => tone(r.querySelector('[data-mark]') as HTMLElement))
    const waitingTone = { fill: 'var(--color-state-waiting)', ring: 'var(--color-state-waiting)', ringed: true }
    // An approval is waiting on you as a question is: one tone for both.
    expect(marks).toEqual([waitingTone, waitingTone, { fill: 'var(--color-state-active)', ring: 'var(--color-state-active)', ringed: true }])
    // The pulse's dots: 8 px, ringed, in the same tones.
    const pulse = [...garden.querySelectorAll('[data-pulse] [data-mark]')] as HTMLElement[]
    expect(pulse.map((m) => [m.style.background, m.className.includes('size-2'), m.className.includes('border-[1.5px]')]))
      .toEqual([['var(--color-state-waiting)', true, true], ['var(--color-state-active)', true, true]])
  })

  it('draws work that is only held up as a quiet ring, apart from work under way and work that failed', async () => {
    mountList({ projects: [GARDEN], running: [running('loop:q', 'Queued', 'p-garden', { blocked: true, phase: 'waiting_model' })] })
    const garden = await card('Garden')
    const mark = await waitFor(() => {
      const m = garden.querySelector('[data-work-row] [data-mark]') as HTMLElement
      expect(m).toBeTruthy()
      return m
    })
    expect([mark.style.borderColor, mark.style.background]).toEqual(['var(--color-state-quiet)', 'transparent'])
  })

  it('says when a card last moved though its pulse could not be read', async () => {
    mountList({ projects: [GARDEN], running: new Error('500'), waiting: [] })
    const garden = await card('Garden')
    await waitFor(() => expect(within(garden).getByText(/^Active /)).toBeInTheDocument())
    expect(within(garden).getByRole('heading', { level: 2 }).getAttribute('aria-label')).toMatch(/, active /)
  })

  it('draws work that failed in the blocked tone, the one red mark', async () => {
    const failed = running('loop:broke', 'Broken build', 'p-garden', { phase: 'error', line: 'Stopped: the tests failed' })
    mountList({ projects: [GARDEN], running: [failed] })
    const garden = await card('Garden')
    const mark = await waitFor(() => {
      const m = garden.querySelector('[data-work-row] [data-mark]') as HTMLElement
      expect(m).toBeTruthy()
      return m
    })
    expect([mark.style.background, mark.style.borderColor]).toEqual(['var(--color-state-blocked)', 'var(--color-state-blocked)'])
  })

  it("writes an approval's object input as its card does, compact", async () => {
    const call = waiting('approval:a2', 'approval', 'p-garden', { brief: { tool: 'write_file', tool_input: { path: 'beds.md', lines: 3 } } })
    mountList({ projects: [GARDEN], waiting: [call] })
    const garden = await card('Garden')
    await waitFor(() => expect(within(garden).getByText('write_file({"path":"beds.md","lines":3})')).toBeInTheDocument())
  })

  it('draws a row in three columns from 1024 px, the state in a fixed first one, and stacked, 44 px, below it', async () => {
    mountList({ projects: [GARDEN], waiting: [ASKS[0]] })
    const garden = await card('Garden')
    const row = await waitFor(() => {
      const r = garden.querySelector('[data-work-row]') as HTMLElement
      expect(r).toBeTruthy()
      return r
    })
    for (const c of ['grid-cols-1', 'max-lg:min-h-11', 'lg:min-h-9']) expect(row.className).toContain(c)
    expect(row.className, "a 150 px state column from 1024 px, so every title starts at one x").toMatch(/lg:grid-cols-\[150px_minmax\(0,1fr\)_auto\]/)
    expect((row.querySelector('[data-row-title]') as HTMLElement).className).toContain('max-lg:line-clamp-2')
    expect((row.querySelector('[data-row-line]') as HTMLElement).className).toContain('max-lg:hidden')
    expect((row.parentElement as HTMLElement).className, 'the rows sit under the name from 1024 px, at the content edge below').toBe('flex flex-col lg:pl-7')
    expect((row.querySelector('[data-row-line]') as HTMLElement).className, 'a card two to a row drops the line').toContain('@max-3xl/card:hidden')
    expect((row.closest('[data-project-card]') as HTMLElement).className).toContain('@container/card')
  })

  it('says its folder by its last two segments, in mono, and its brief by its first sentence', async () => {
    mountList({ projects: [GARDEN] })
    const garden = await card('Garden')
    const line = garden.querySelector('[data-line-two]') as HTMLElement
    expect(line.textContent).toBe('code/garden · Plan and grow the spring beds.')
    expect(within(line).getByText('code/garden').className).toBe('font-mono text-on-surface-var')
    expect(line.querySelector('[aria-hidden]')?.textContent).toBe('·')
    expect(line.firstElementChild?.className, 'two lines at most on a phone').toContain('max-md:line-clamp-2')
    expect(garden.textContent).not.toMatch(/no workspace|lists?\b/)
  })

  it('says its latest row with no folder and no brief, and after a built-in\'s purpose', async () => {
    const trip = project('p-trip', 'Trip')
    const personal = project('p-personal', 'Personal', { is_builtin: true })
    mountList({ projects: [trip, personal], sessions: [chat('c-1', 'Book the ferry', 'p-trip'), chat('c-2', 'Renew the passport', 'p-personal')] })
    await waitFor(async () => expect((await card('Trip')).querySelector('[data-line-two]')?.textContent).toBe('Last: Book the ferry'))
    expect((await card('Personal')).querySelector('[data-line-two]')?.textContent).toBe('Tasks with no other project go here. · Last: Renew the passport')
  })

  it("keeps an unreadable folder's path, and calls only a gone one missing", async () => {
    mountList({ projects: [project('p-garden', 'Garden', { workspace_dir: '/srv/code/garden', folder_state: 'unreadable' })] })
    const garden = await card('Garden')
    expect(within(garden).queryByText('Folder missing')).toBeNull()
    expect(within(garden).getByText('code/garden')).toBeInTheDocument()
  })

  it('is a list of LIs, each card its own item, with its corners, its heading and its targets', async () => {
    mountList({ projects: [GARDEN, TRIP] })
    const list = await screen.findByRole('list', { name: 'Projects' })
    await waitFor(() => expect(list.children).toHaveLength(2))
    expect([...list.children].map((c) => c.tagName)).toEqual(['LI', 'LI'])
    const li = list.children[0] as HTMLElement
    for (const c of ['relative', 'rounded-lg']) expect(li.className.split(' ')).toContain(c)
    expect(li.querySelector('h2')?.getAttribute('data-type')).toBe('title-m')
    expect((li.querySelector('[data-pulse]') as HTMLElement).className, 'the pulse under the name on a phone').toContain('max-md:pl-[2.25rem]')
    expect((li.querySelector('h2')!.closest('[tabindex="-1"]') as HTMLElement).className, 'a 44 px open target').toContain('max-lg:min-h-11')
  })

  it('gives "N more in Garden" a 44 px box at the compact widths', async () => {
    mountList({ projects: [GARDEN], waiting: ASKS, running: [running('loop:fence', 'Fix the tests', 'p-garden')] })
    const more = await within(await card('Garden')).findByRole('button', { name: '2 more in Garden' })
    expect(more.className).toContain('max-lg:min-h-11')
  })

  it('takes Tab through a card as it reads: open, its "…", then its live rows and their "more"', async () => {
    mountList({ projects: [GARDEN], waiting: ASKS, running: [running('loop:fence', 'Fix the tests', 'p-garden')] })
    const garden = await card('Garden')
    await waitFor(() => expect(garden.querySelectorAll('[data-work-row]')).toHaveLength(3))
    const order = [...garden.querySelectorAll<HTMLElement>('button, a[href], [tabindex="0"]')]
      .map((el) => el.getAttribute('aria-label') ?? (el.closest('[data-work-row]') ? 'row' : el.textContent))
    expect(order.slice(0, 2)).toEqual(['Garden', 'More actions for Garden'])
    expect(order.at(-1)).toBe('2 more in Garden')
  })

  it('opens its "…" under a caption naming it, with breaks, and a Delete named for it', async () => {
    mountList({ projects: [GARDEN] })
    await userEvent.click(within(await card('Garden')).getByRole('button', { name: 'More actions for Garden' }))
    const del = await screen.findByRole('button', { name: 'Delete project: Garden' })
    expect(del.textContent).toBe('Delete project…')
    const caption = document.querySelector('[data-menu-caption]') as HTMLElement
    expect(caption.textContent).toBe('More actions for Garden')
    const menu = caption.parentElement as HTMLElement
    const breaks = [...menu.querySelectorAll('[data-menu-break]')].map((b) => b.closest('[data-menu-item]')?.textContent)
    expect(breaks).toEqual(['Start a chat here', 'Export'])
  })

  it("its context menu names its Delete for the project, as the \"…\" does", async () => {
    mountList({ projects: [GARDEN] })
    fireEvent.contextMenu((await card('Garden')).querySelector('h2') as HTMLElement)
    const menu = await screen.findByRole('menu')
    expect(within(menu).getByRole('menuitem', { name: 'Delete project: Garden' }).textContent).toBe('Delete project…')
  })

  it('windows past the shared threshold, one card to a row, still named and counted', async () => {
    const many = Array.from({ length: 70 }, (_, i) => project(`p-${String(i).padStart(8, '0')}`, `Plot ${i + 1}`))
    mountList({ projects: many })
    const list = await screen.findByRole('list', { name: 'Projects' })
    await waitFor(() => expect(list.querySelector('[role="listitem"]')).toBeTruthy())
    const first = list.querySelector('[role="listitem"]') as HTMLElement
    expect(first.getAttribute('aria-setsize')).toBe('70')
    const windowed = first.querySelector('[data-project-card]') as HTMLElement
    expect(windowed.tagName).toBe('DIV')
    // A div with no role may carry no name: the windowed card is a group its heading names.
    expect(windowed.getAttribute('role')).toBe('group')
    const label = document.getElementById(windowed.getAttribute('aria-labelledby') ?? '')
    expect(label?.textContent).toContain('Plot 1')
    expect(list.querySelectorAll('[data-project-card]').length, 'not every card is drawn').toBeLessThan(70)
  })

  it("its context menu says a built-in's reasons, as the \"…\" does", async () => {
    mountList({ projects: [project('p-personal', 'Personal', { is_builtin: true })] })
    const personal = await card('Personal')
    fireEvent.contextMenu(personal.querySelector('h2') as HTMLElement)
    const menu = await screen.findByRole('menu')
    expect(within(menu).getByText("Personal is built in. It can't be renamed.")).toBeInTheDocument()
  })

  it('is off for Pin while the pins could not be read', async () => {
    mountList({ projects: [GARDEN], settings: new Error('500') })
    await userEvent.click(within(await card('Garden')).getByRole('button', { name: 'More actions for Garden' }))
    const pin = await screen.findByRole('button', { name: 'Pin to the top' })
    expect(pin.getAttribute('aria-disabled')).toBe('true')
    expect(pin.getAttribute('title')).toBe("Couldn't read your pins.")
  })

  it('is off for Pin on an archived project, with its reason', async () => {
    mountList({ projects: [project('p-old', 'Old', { status: 'archived' })], query: { show: 'archived' } })
    await userEvent.click(within(await card('Old')).getByRole('button', { name: 'More actions for Old' }))
    const pin = await screen.findByRole('button', { name: 'Pin to the top' })
    expect(pin.getAttribute('aria-disabled')).toBe('true')
    expect(pin.getAttribute('title')).toBe("Restore it first. An archived project can't be pinned.")
  })

  it('says a folder is gone with a chip in place of its path', async () => {
    mountList({ projects: [project('p-garden', 'Garden', { workspace_dir: '/gone/garden', folder_state: 'missing' })] })
    const garden = await card('Garden')
    expect(within(garden).getByText('Folder missing')).toBeInTheDocument()
    expect(garden.textContent).not.toContain('gone/garden')
  })
})
