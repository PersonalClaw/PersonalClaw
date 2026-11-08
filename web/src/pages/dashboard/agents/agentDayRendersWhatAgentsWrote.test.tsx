import { describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { needsYou } from '../../../lib/needsYou'
import type { AgentActivityEntity } from '../../../lib/useAgentActivity'
import { waitingItem, waitingList } from '../../../test/waiting'
import { HOSTILE, LEADING, MD_LINE, inert, plain, rawSinks, rendersLine } from '../../../test/markdownSinks'
import { AgentDayView } from './AgentDay'
import { buildDay, type DayInput } from './agentDayModel'

// ── Your agents today: a row's line renders what its agent wrote ────────────────────────────────
//
// 🔴 A row's line is an ask's title, the title of the work it does now, or why it failed, and an
// agent wrote each of them: the row showed their marks. Now the line's content renders as the line it
// is (`MarkdownLine`), and our words around it ("Failed at 18:10: ", " and 1 more", "Last ran 17:40",
// the Scheduled row's schedule words) stay the text they are. The row's sentence, its accessible name,
// reads each content part as plain text, so its own words stay exact. Agent and schedule names are
// names, and stay as written.

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
    helpers: [],
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
    expect(failed.line.textContent).toBe(`Failed at 18:10: ${SEEN}`)
  })

  it("each row's sentence reads the content as plain text, and its own words exactly", () => {
    show(day(MD_LINE))
    expect(row('PersonalClaw').li.getAttribute('aria-label')).toMatch(new RegExp(`^PersonalClaw, waiting on you: ${SEEN.replace(/[().]/g, '\\$&')} and 1 more\\.`))
    expect(row('Code reviewer').li.getAttribute('aria-label')).toBe(`Code reviewer, failed: Failed at 18:10: ${SEEN}.`)
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

describe('the app builds the rest of the row, and draws it as text', () => {
  it('no field AgentDay draws is drawn raw but a name', () => {
    const source = readFileSync(join(process.cwd(), 'src', 'pages/dashboard/agents/AgentDay.tsx'), 'utf8')
    expect(rawSinks(source, ['say', 'content', 'failed', 'title', 'why'])).toEqual([])
  })
})
