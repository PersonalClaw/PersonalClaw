import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { needsYou } from '../../../lib/needsYou'
import type { AgentActivityEntity } from '../../../lib/useAgentActivity'
import { waitingItem, waitingList } from '../../../test/waiting'
import { AgentDayView, nameRoom } from './AgentDay'
import { buildDay, type DayInput } from './agentDayModel'

// ── Home's "Your agents today", as a person meets it ─────────────────────────────────────────────
//
// Each row reads as one sentence, its track is decoration the sentence already says, an ask is
// answered in Attention and never here, and nothing on it moves.

const at = (hh: number, mm: number) => new Date(2026, 10, 3, hh, mm).getTime()
const NOW = at(18, 20)
const iso = (ms: number) => new Date(ms).toISOString()

function live(over: Partial<AgentActivityEntity> & Pick<AgentActivityEntity, 'id'>): AgentActivityEntity {
  return { kind: 'session', state: 'working', title: 'Work', refs: { link: '' }, ...over }
}

function evening(over: Partial<DayInput> = {}): DayInput {
  const asks = needsYou(waitingList(waitingItem({ kind: 'approval', id: 'approval:a1', work: 'chat:lisbon',
    brief: { tool: 'write_file' }, asked_at: iso(at(18, 18)) })), [], [])
  return {
    now: NOW, narrow: false, defaultAgent: 'PersonalClaw',
    day: { since: iso(at(14, 20)), until: iso(NOW), cut: 0, agents: [
      { agent: '', blocks: [{ start: iso(at(18, 0)), end: iso(at(18, 18)), session: 'dashboard:lisbon', source: 'chat' }], before: null },
      { agent: 'Code reviewer', blocks: [{ start: iso(at(17, 0)), end: iso(at(18, 8)), session: 'loop-fix', source: 'loop' }], before: iso(at(14, 0)) },
    ] },
    live: [
      live({ id: 'session:lisbon', work: 'chat:lisbon', agent: '', since: iso(at(18, 0)), refs: { link: '#/chat/lisbon', session: 'lisbon' } }),
      live({ id: 'loop:desks', kind: 'loop', work: 'loop:desks', agent: 'Researcher', since: iso(at(17, 51)),
        title: 'Compare three standing desks', refs: { link: '#/loops/desks' } }),
    ],
    asks,
    helpers: [], loops: [{ id: 'desks', max_cycles: 8, total_cycles: 3, status: 'running', agent: 'Researcher' } as never],
    scheduled: [{ trigger_id: 't1', trigger_name: 'Evening summary', at: at(21, 30) / 1000 }],
    ...over,
  }
}

function show(input: DayInput, props: Partial<Parameters<typeof AgentDayView>[0]> = {}) {
  const onFilter = vi.fn()
  render(<AgentDayView view={buildDay(input)} failed={false} settling={false} narrow={input.narrow}
    budget={null} filter="all" onFilter={onFilter} {...props} />)
  return { onFilter }
}

afterEach(() => vi.restoreAllMocks())

describe('a row', () => {
  it('reads as its one sentence, with the track hidden from a screen reader', () => {
    show(evening())

    const list = screen.getByRole('list', { name: 'Your agents' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows[0]).toHaveAccessibleName(expect.stringMatching(/^PersonalClaw, waiting on you: write_file\./))
    expect(rows.map((r) => r.getAttribute('aria-label'))).toEqual(buildDay(evening()).rows.map((r) => r.sentence))
    for (const r of rows) expect(r.querySelector('[aria-hidden="true"] span')).not.toBeNull()
  })

  it('links an ask to Attention and offers no way to answer it here', () => {
    show(evening())

    const link = screen.getByRole('link', { name: /^Answer in Attention/ })
    expect(link).toHaveAttribute('href', expect.stringMatching(/^#\/dashboard\?view=attention&open=waiting%3Aapproval%3Aa1$/))
    expect(screen.queryByRole('button', { name: /approve|allow|deny|answer/i })).toBeNull()
  })

  it('is reached by the keyboard: its work, then its ask', async () => {
    show(evening())
    const user = userEvent.setup()

    const order: string[] = []
    for (let i = 0; i < 6; i += 1) {
      await user.tab()
      const el = document.activeElement as HTMLElement
      order.push(el.textContent ?? '')
    }

    const pc = order.indexOf('PersonalClaw')
    expect(pc).toBeGreaterThanOrEqual(0)
    expect(order[pc + 1]).toBe('Answer in Attention')
  })

  it('says its state in words beside its mark', () => {
    show(evening())

    expect(screen.getAllByText('Waiting on you').length).toBeGreaterThan(0)
    expect(screen.getByText('Working')).toBeInTheDocument()
  })

  it('names no time no source gave: a loop runs to a cycle', () => {
    show(evening())

    expect(screen.getByText('Runs to cycle 8')).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/about \d/)
  })
})

describe('what you act on comes first', () => {
  const failing = { helpers: [{ id: 'h1', task: 'cut the release', done: true, agent: 'Release helper',
    started: at(16, 10) / 1000, ended: at(16, 12) / 1000, error: 'Couldn’t reach api.github.com' } as never] }

  it('puts the rows that wait on you and the rows that failed on top, under a count of them', () => {
    show(evening(failing))

    expect(screen.getByText('1 ask waits on you · 1 failed today')).toBeInTheDocument()
    const rows = within(screen.getByRole('list', { name: 'Your agents' })).getAllByRole('listitem')
    expect(rows.map((r) => r.dataset.lifted ?? '')).toEqual(['waiting', 'failed', '', '', ''])
    expect(rows[1]).toHaveAccessibleName(expect.stringMatching(/^Release helper, failed: Failed at \d\d:\d\d: Couldn’t reach/))
  })

  it('paints a lifted row on its state colour as a ground, never a side-stripe', () => {
    show(evening(failing))

    const [waiting, failed] = within(screen.getByRole('list', { name: 'Your agents' })).getAllByRole('listitem')
    expect(waiting.style.background).toContain('--color-state-waiting')
    for (const row of [waiting, failed]) {
      expect(row.style.borderLeftWidth).toBe('')
      expect([...row.classList].filter((c) => c.startsWith('border-l'))).toEqual([])
    }
    expect(failed.style.background).toContain('--color-state-blocked')
  })

  it('says how long ago an ask was asked', () => {
    show(evening())

    expect(screen.getByText('· asked 2 min ago')).toBeInTheDocument()
  })

  it('answers in Attention through a link drawn as a button, never a button that sets the address', () => {
    show(evening())

    const link = screen.getByRole('link', { name: /^Answer in Attention/ })
    expect(link.tagName).toBe('A')
    expect(link.className).toContain('rounded-pill')
    expect(screen.queryByRole('button', { name: /Answer in Attention/ })).toBeNull()
  })

  it('names each link for the ask it opens, its visible words first', () => {
    show(evening())

    expect(screen.getByRole('link', { name: 'Answer in Attention: PersonalClaw’s ask' })).toHaveTextContent('Answer in Attention')
  })

  it('names the link for the first of a row’s asks when it holds two', () => {
    const asks = needsYou(waitingList(
      waitingItem({ kind: 'approval', id: 'approval:a1', work: 'chat:lisbon', brief: { tool: 'write_file' }, asked_at: iso(at(18, 18)) }),
      waitingItem({ kind: 'question', id: 'question:q2', work: 'chat:lisbon', asked_at: iso(at(18, 19)) }),
    ), [], [])
    show(evening({ asks }))

    expect(screen.getByRole('link', { name: 'Answer in Attention: PersonalClaw’s first of 2 asks' })).toBeInTheDocument()
  })

  it('counts a row that failed today and waits on you now as failed, as its sentence says', () => {
    const helper = { id: 'h1', task: 'early', done: true, agent: 'PersonalClaw',
      started: at(16, 10) / 1000, ended: at(16, 12) / 1000, error: 'Couldn’t reach api.github.com' } as never
    show(evening({ helpers: [helper] }))

    expect(screen.getByText('1 ask waits on you · 1 failed today')).toBeInTheDocument()
  })

  it('draws a linked name’s focus ring inside it, and gives an unlinked name the same box', () => {
    show(evening())

    const tokens = (el: Element) => el.className.split(/\s+/)
    const link = screen.getByRole('link', { name: 'PersonalClaw' })
    expect(tokens(link)).toEqual(expect.arrayContaining(['focus-visible:-outline-offset-2', 'inline-flex', 'max-w-full', 'items-center', 'max-lg:flex']))
    const plain = screen.getByText('Scheduled').parentElement as HTMLElement
    expect(tokens(plain)).toEqual(expect.arrayContaining(['max-lg:flex', 'max-lg:items-center', 'max-lg:min-h-11']))
  })

  it('says when a row that works now failed earlier, before the hours drawn, after its line', () => {
    const helper = { id: 'h1', task: 'early', done: true, agent: 'Researcher',
      started: at(9, 0) / 1000, ended: at(9, 10) / 1000, error: 'boom' } as never
    show(evening({ asks: [], helpers: [helper] }))

    expect(screen.getByText('1 failed today')).toBeInTheDocument()
    const row = screen.getByRole('listitem', { name: /^Researcher, working:/ })
    expect(within(row).getByText('· failed at 09:10')).toBeInTheDocument()
    expect(row).not.toHaveAttribute('data-lifted')
  })

  it('gives the link a 44 px target on a touch screen, as the band’s other controls have', () => {
    show(evening())

    const link = screen.getByRole('link', { name: /^Answer in Attention/ })
    expect(link.className).toContain('max-lg:min-h-11')
    // A linked name is a target too: its row opens there.
    expect(screen.getByRole('link', { name: 'PersonalClaw' }).className).toContain('max-lg:min-h-11')
  })

  it('gives the more-asks line’s link a 44 px target on a touch screen', () => {
    const asks = needsYou(waitingList(waitingItem({ kind: 'question', id: 'question:x', work: 'chat:nobody-runs-this' })), [], [])
    show(evening({ asks }))

    expect(screen.getByRole('link', { name: 'Attention' }).className).toContain('max-lg:min-h-11')
  })

  it('keeps an ask’s age whole when its words are cut short', () => {
    show(evening())

    const age = screen.getByText('· asked 2 min ago')
    expect(age.className.split(/\s+/)).toContain('shrink-0')
    expect(age.className.split(/\s+/)).not.toContain('truncate')
    // Its line clips at its edge, so a long age never draws under "Answer in Attention".
    expect((age.parentElement as HTMLElement).className.split(/\s+/)).toContain('overflow-hidden')
  })

  it('counts an ask that is on no row, with no row lifted for it', () => {
    const asks = needsYou(waitingList(waitingItem({ kind: 'question', id: 'question:x', work: 'chat:nobody-runs-this' })), [], [])
    show(evening({ asks }))

    expect(screen.getByText('1 ask waits on you')).toBeInTheDocument()
    expect(document.querySelector('[data-lifted]')).toBeNull()
    // No row holds an ask, so it is not "1 more".
    expect(screen.getByText(/^1 ask in/)).toBeInTheDocument()
    expect(screen.queryByText(/more ask/)).toBeNull()
  })

  it('counts the asks as Attention does, the one on no row too', () => {
    const asks = needsYou(waitingList(
      waitingItem({ kind: 'approval', id: 'approval:a1', work: 'chat:lisbon', brief: { tool: 'write_file' }, asked_at: iso(at(18, 18)) }),
      waitingItem({ kind: 'question', id: 'question:x', work: 'chat:nobody-runs-this' }),
    ), [], [])
    show(evening({ asks }))

    expect(screen.getByText('2 asks wait on you')).toBeInTheDocument()
    expect(document.querySelectorAll('[data-lifted]')).toHaveLength(1)
    expect(screen.getByText(/1 more ask in/)).toBeInTheDocument()
  })

  it('shows only the filter’s rows under a filter, and still counts what Attention counts', () => {
    show(evening(), { filter: 'working' })

    expect(document.querySelector('[data-lifted]')).toBeNull()
    expect(screen.getByText('1 ask waits on you')).toBeInTheDocument()
  })

  it('lifts nothing and counts nothing when nothing waits on you and nothing failed', () => {
    show(evening({ asks: [] }))

    expect(screen.getByRole('listitem', { name: /^PersonalClaw, working:/ })).toBeInTheDocument()
    expect(screen.queryByText(/wait on you|waits on you|failed today/)).toBeNull()
    expect(document.querySelector('[data-lifted]')).toBeNull()
  })
})

describe('the scheduled names', () => {
  // 14:20 to 22:20: an hour is 12.5% of the track.
  const view = buildDay(evening())
  const run = (hh: number, mm: number, label: string) => ({ kind: 'scheduled' as const, start: at(hh, mm), end: at(hh, mm), label })

  it('share the gap when they face each other, so two names never draw over each other', () => {
    const marks = [run(19, 0, 'Soon'), run(22, 0, 'Evening summary')]

    expect(nameRoom(marks, 0, view)).toBe('calc(18.75% - 0.5rem)')
    expect(nameRoom(marks, 1, view)).toBe('calc(18.75% - 0.5rem)')
  })

  it('never reach back past now', () => {
    expect(nameRoom([run(22, 0, 'Evening summary')], 0, view)).toBe('calc(45.83% - 0.5rem)')
  })

  it('leave the track without an edge label: the row\'s words name the run after the window', () => {
    show(evening({ scheduled: [
      { trigger_id: 't1', trigger_name: 'Evening summary', at: at(21, 30) / 1000 },
      { trigger_id: 't2', trigger_name: 'Morning triage', at: new Date(2026, 10, 4, 7, 30).getTime() / 1000 },
    ] }))

    const row = screen.getByRole('listitem', { name: /^Scheduled, agent not named: / })
    expect(row.textContent).not.toContain('→')
    const words = within(row).getByText(/Evening summary · tomorrow \d\d:\d\d Morning triage$/)
    // On a phone the line wraps rather than cut off the run after the window.
    expect(words.className).toContain('max-sm:whitespace-normal')
  })
})

describe('the band', () => {
  it('says nothing ran in the last four hours, and three on a narrow screen', () => {
    const quiet = { day: { since: iso(at(14, 20)), until: iso(NOW), cut: 0, agents: [{ agent: 'Inbox triage', blocks: [], before: iso(at(7, 30)) }] },
      live: [], asks: [], loops: [], scheduled: [] }
    const { unmount } = render(<AgentDayView view={buildDay(evening(quiet))} failed={false} settling={false} narrow={false}
      budget={null} filter="all" onFilter={() => {}} />)
    expect(screen.getByText('Nothing ran in the last 4 hours.')).toBeInTheDocument()
    unmount()

    show(evening({ ...quiet, narrow: true }))
    expect(screen.getByText('Nothing ran in the last 3 hours.')).toBeInTheDocument()
  })

  it('says nothing ran today when nothing did', () => {
    show(evening({ day: { since: iso(at(14, 20)), until: iso(NOW), cut: 0, agents: [] }, live: [], asks: [], loops: [], scheduled: [] }))

    expect(screen.getByText('Nothing ran today.')).toBeInTheDocument()
  })

  it('says it could not read, never that nothing ran', () => {
    show(evening(), { failed: true })

    expect(screen.getByText('Couldn’t read what your agents did, so this is unknown right now.')).toBeInTheDocument()
    expect(screen.queryByText(/Nothing ran/)).toBeNull()
  })

  it('holds its controls on its own heading: the filter and Manage agents', () => {
    show(evening())

    const heading = screen.getByRole('heading', { level: 2, name: 'Your agents today' })
    const bar = heading.parentElement as HTMLElement
    expect(within(bar).getByRole('button', { name: 'Filter & sort' })).toBeInTheDocument()
    expect(within(bar).getByRole('link', { name: 'Manage agents' })).toHaveAttribute('href', '#/agents')
  })

  it('shows only the rows the filter names', () => {
    show(evening(), { filter: 'working' })

    const rows = within(screen.getByRole('list', { name: 'Your agents' })).getAllByRole('listitem')
    expect(rows.map((r) => r.getAttribute('aria-label'))).toEqual([expect.stringMatching(/^Researcher, working:/)])
  })

  it('folds idle agents into one line past eight rows, and opens them in place', async () => {
    const agents = Array.from({ length: 8 }, (_, i) => ({ agent: `Idle ${i}`, blocks: [{ start: iso(at(15, i)), end: iso(at(15, i + 1)), session: `s${i}`, source: 'chat' }], before: null }))
    show(evening({ day: { since: iso(at(14, 20)), until: iso(NOW), cut: 0, agents } }))

    const fold = screen.getByRole('button', { name: '8 idle' })
    expect(fold.className).toContain('max-lg:min-h-11')
    await userEvent.setup().click(fold)

    expect(screen.queryByRole('button', { name: '8 idle' })).toBeNull()
    expect(screen.getByRole('listitem', { name: /^Idle 0, idle:/ })).toBeInTheDocument()
  })

  it('says a scheduled run names no agent, in words beside its name', () => {
    show(evening())

    const row = screen.getByRole('listitem', { name: /^Scheduled, agent not named: / })
    expect(within(row).getByText('Agent not named')).toBeInTheDocument()
  })

  it('draws a scheduled run late in the window with its name before its mark, inside the track', () => {
    show(evening({ scheduled: [
      { trigger_id: 't1', trigger_name: 'Soon', at: at(19, 0) / 1000 },
      { trigger_id: 't2', trigger_name: 'Evening summary', at: at(22, 0) / 1000 },
    ] }))

    const label = (name: string) => screen.getByText(name).parentElement as HTMLElement
    expect(label('Soon').dataset.late).toBeUndefined()
    expect(label('Evening summary').dataset.late).toBe('true')
  })

  it('says when the oldest blocks of the hours it draws were left out', () => {
    const day = { ...evening().day!, cut: 3 }
    show(evening({ day }))

    expect(screen.getByText('The 3 oldest blocks of work in these hours are not drawn.')).toBeInTheDocument()
  })

  it('does not move: no animation frame, no animated class', () => {
    const raf = vi.spyOn(window, 'requestAnimationFrame')
    show(evening())

    expect(raf).not.toHaveBeenCalled()
    expect(document.querySelector('[class*="animate-"]')).toBeNull()
  })
})
