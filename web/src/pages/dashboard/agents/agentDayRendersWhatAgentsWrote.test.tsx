import { describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { needsYou } from '../../../lib/needsYou'
import type { AgentActivityEntity } from '../../../lib/useAgentActivity'
import { waitingItem, waitingList } from '../../../test/waiting'
import { HOSTILE, LEADING, MD_LINE, inert, plain, rawSinks, rendersLine } from '../../../test/markdownSinks'
import { AgentDayView } from './AgentDay'
import { buildDay, clock, type DayInput } from './agentDayModel'

// ── Your agents today: a row's line renders what its agent wrote ────────────────────────────────
//
// 🔴 A row's line is an ask's title, the title of the work it does now, or why it failed, and an
// agent wrote each of them: the row showed their backticks and asterisks. Now the line's content renders as the line it
// is (`MarkdownLine`), and our words around it ("Failed at 18:10: ", " and 1 more", "Last ran 17:40",
// the Scheduled row's schedule words) stay the text they are. The row's sentence, its accessible name,
// reads each content part as plain text, so its own words stay exact. Agent and schedule names are
// names, and stay as written. An approval's title, a tool's name or the command itself, stays text.

const at = (hh: number, mm: number) => new Date(2026, 10, 3, hh, mm).getTime()
const NOW = at(18, 20)
const iso = (ms: number) => new Date(ms).toISOString()
const SEEN = 'Skip model_calls in export_digest() in published.py'

function live(over: Partial<AgentActivityEntity> & Pick<AgentActivityEntity, 'id'>): AgentActivityEntity {
  return { kind: 'session', state: 'working', title: 'Work', refs: { link: '' }, ...over }
}

/** A day where PersonalClaw asks two questions (the first is `text`), the Researcher works on
 *  `text`, and the Code reviewer's loop failed with `text`. */
function day(text: string): DayInput {
  const question = (id: string, q: string, mm: number) => waitingItem({
    kind: 'question', id: `question:${id}`, work: 'chat:lisbon', session: 'lisbon', asked_at: iso(at(18, mm)),
    brief: { asked_by: 'PersonalClaw', questions: [{ question: q }] },
    answer: { method: 'POST', path: `/api/questions/${id}`, takes: ['answer'], body: {} },
  })
  return {
    now: NOW, narrow: false, defaultAgent: 'PersonalClaw',
    day: { since: iso(at(14, 20)), until: iso(NOW), cut: 0, agents: [
      { agent: '', blocks: [{ start: iso(at(18, 0)), end: iso(at(18, 18)), session: 'dashboard:lisbon', source: 'chat' }], before: null },
      { agent: 'Code reviewer', blocks: [{ start: iso(at(17, 0)), end: iso(at(18, 8)), session: 'loop-fix', source: 'loop' }], before: null },
    ] },
    live: [
      live({ id: 'session:lisbon', work: 'chat:lisbon', agent: '', since: iso(at(18, 0)), refs: { link: '#/chat/lisbon', session: 'lisbon' } }),
      live({ id: 'loop:desks', kind: 'loop', work: 'loop:desks', agent: 'Researcher', since: iso(at(17, 51)), title: text, refs: { link: '#/loops/desks' } }),
    ],
    asks: needsYou(waitingList(question('q1', text, 18), question('q2', 'Which desk?', 17)), [], []),
    // The Researcher works now, and a helper of its failed earlier: its sentence says so in a part of its own.
    helpers: [{ id: 'h1', task: 'early', done: true, agent: 'Researcher', started: at(16, 0) / 1000, ended: at(16, 5) / 1000, error: text } as never],
    loops: [
      { id: 'desks', max_cycles: 8, total_cycles: 3, status: 'running', agent: 'Researcher' } as never,
      { id: 'fix', max_cycles: 4, total_cycles: 4, status: 'failed', agent: 'Code reviewer', completed_at: at(18, 10) / 1000, error_message: text } as never,
    ],
    scheduled: [],
  }
}

function show(input: DayInput) {
  render(<AgentDayView view={buildDay(input)} failed={false} settling={false} narrow={false}
    budget={null} filter="all" onFilter={vi.fn()} />)
}

/** The row whose sentence starts with `name`, and its line. */
function row(name: string) {
  const li = within(screen.getByRole('list', { name: 'Your agents' })).getAllByRole('listitem')
    .find((r) => (r.getAttribute('aria-label') ?? '').startsWith(`${name},`))!
  return { li, line: li.querySelector('p > span')! }
}

describe("a row's line renders what its agent wrote", () => {
  it('an ask, the work it does now and why it failed render as lines, and our words stay text', () => {
    show(day(MD_LINE))
    const asks = row('PersonalClaw')
    expect(rendersLine(asks.line)).toEqual([])
    expect(asks.line.textContent).toBe(`${SEEN} and 1 more`)
    const works = row('Researcher')
    expect(rendersLine(works.line)).toEqual([])
    expect(works.line.textContent).toBe(SEEN)
    const failed = row('Code reviewer')
    expect(rendersLine(failed.line)).toEqual([])
    expect(failed.line.textContent).toBe(`Failed at ${clock(at(18, 10))}: ${SEEN}`)
    // Only the content is the rendered line: our lead and tail sit beside it, never inside it.
    for (const x of [asks, works, failed]) expect(x.line.querySelector(':scope > span')!.textContent).toBe(SEEN)
  })

  it("each row's sentence reads the content as plain text, and its own words exactly", () => {
    show(day(MD_LINE))
    const asks = `PersonalClaw, waiting on you: ${SEEN} and 1 more.`
    expect(row('PersonalClaw').li.getAttribute('aria-label')?.slice(0, asks.length)).toBe(asks)
    expect(row('Code reviewer').li.getAttribute('aria-label')).toBe(`Code reviewer, failed: Failed at ${clock(at(18, 10))}: ${SEEN}.`)
    // A row that works now and failed earlier says so in a part of its own, read as plain text too.
    expect(row('Researcher').li.getAttribute('aria-label')).toContain(`Failed at ${clock(at(16, 5))}: ${SEEN}.`)
    for (const r of screen.getAllByRole('listitem')) expect(plain(r.getAttribute('aria-label') ?? ''), 'a sentence carries a mark').toEqual([])
  })

  it('a hostile line is inert in each of the three', () => {
    show(day(HOSTILE))
    for (const name of ['PersonalClaw', 'Researcher', 'Code reviewer']) expect(inert(row(name).line), name).toEqual([])
  })

  it.each(LEADING)('%s: a line that reads as a block elsewhere renders as the text it is', (_construct, text) => {
    show(day(text))
    expect(row('Researcher').line.textContent).toBe(text)
  })
})

describe("an approval's title is a tool's name, and stays text", () => {
  it("in its row's line and in its sentence", () => {
    show({ ...day(MD_LINE), asks: needsYou(waitingList(waitingItem({ kind: 'approval', id: 'approval:a1', work: 'chat:lisbon',
      asked_at: iso(at(18, 18)), brief: { tool: 'shell.run_**now**' } })), [], []) })
    const asks = row('PersonalClaw')
    expect(asks.line.querySelector('strong, code')).toBeNull()
    expect(asks.line.textContent).toBe('shell.run_**now**')
    expect(asks.li.getAttribute('aria-label')).toMatch(/^PersonalClaw, waiting on you: shell\.run_\*\*now\*\*\./)
  })
})

describe('what the app builds, and the names, stay text', () => {
  it("the Scheduled row's schedule words, a Last ran line and an agent's name", () => {
    const base = day(MD_LINE)
    const read = base.day!
    show({
      ...base,
      scheduled: [{ trigger_id: 't1', trigger_name: 'Ship **nightly** `digest`', at: at(21, 30) / 1000 }],
      day: { ...read, agents: [...read.agents,
        { agent: 'Archivist', blocks: [{ start: iso(at(15, 0)), end: iso(at(15, 10)), session: 'loop-arch', source: 'loop' }], before: null }] },
      helpers: [{ id: 'h2', task: 'release', done: true, agent: 'Release **helper**', started: at(17, 0) / 1000, ended: at(17, 5) / 1000, error: 'It stopped.' } as never],
    })
    const scheduled = row('Scheduled')
    expect(scheduled.li.querySelector('strong, code'), 'the line and the track label stay text').toBeNull()
    expect(scheduled.line.textContent).toContain('Ship **nightly** `digest`')
    expect(scheduled.li.getAttribute('aria-label')).toContain('Ship **nightly** `digest`')
    expect(row('Archivist').line.textContent).toBe(`Last ran ${clock(at(15, 10))}`)
    expect(row('Release **helper**').li.querySelector('strong'), "an agent's name is a name").toBeNull()
  })
})

describe("AgentDay draws no agent's words raw", () => {
  it('say, content, failed, title and why reach the page only through MarkdownLine', () => {
    const source = readFileSync(join(process.cwd(), 'src', 'pages/dashboard/agents/AgentDay.tsx'), 'utf8')
    expect(rawSinks(source, ['say', 'content', 'failed', 'title', 'why'])).toEqual([])
  })
})
