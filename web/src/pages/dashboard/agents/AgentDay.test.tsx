import { ORB_STATE_OF_AGENT_ROW, markSceneOf } from '../../../lib/orbState'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, within } from '@testing-library/react'
import { STILL_CLOCK, lastPaint, orbPaint, recorder, type Entry } from '../../../test/orbPaint'
import userEvent from '@testing-library/user-event'
import { needsYou } from '../../../lib/needsYou'
import type { AgentActivityEntity } from '../../../lib/useAgentActivity'
import { waitingItem, waitingList } from '../../../test/waiting'
import { AgentDayView, YIELDS, nameRoom } from './AgentDay'
import { buildDay, type DayInput } from './agentDayModel'

// ── Home's "Your agents today", as a person meets it ─────────────────────────────────────────────
//
// Each row reads as one sentence, its track is decoration the sentence already says, an ask is
// answered on Needs you and never here, and nothing on it moves. It is where running work shows:
// each of an agent's runs, held-up work, and what a restart cut off.

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

/** The count over the band, as one line of text. */
const headText = () => document.querySelector('p[data-type="label-m"]')?.textContent ?? ''

/** A day with a failed helper beside the evening's work, so every row state is drawn. */
const failing = { helpers: [{ id: 'h1', task: 'cut the release', done: true, agent: 'Release helper',
  started: at(16, 10) / 1000, ended: at(16, 12) / 1000, error: 'Couldn’t reach api.github.com' } as never] }

const TONES: Record<string, string> = {
  '--color-state-active': '#3366cc', '--color-state-waiting': '#e08a1e', '--color-state-quiet': '#8a8f98', '--color-state-blocked': '#d0453a',
}
const rgb = (h: string) => { const n = parseInt(h.slice(1), 16); return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 } }

describe('a row’s state is the orb’s still mark', () => {
  it('each row paints its state’s mark in its family’s shared token, the failed one with its ×, and asks for no frame', async () => {
    const logs = new WeakMap<HTMLCanvasElement, Entry[]>()
    const original = { getContext: HTMLCanvasElement.prototype.getContext, raf: window.requestAnimationFrame }
    let asked = 0
    HTMLCanvasElement.prototype.getContext = function getContext(this: HTMLCanvasElement) {
      let log = logs.get(this)
      if (!log) { log = []; logs.set(this, log) }
      return recorder(log)
    } as never
    Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: () => { asked += 1; return asked } })
    for (const [k, v] of Object.entries(TONES)) document.documentElement.style.setProperty(k, v)
    try {
      vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
      show(evening(failing))
      await act(async () => { vi.advanceTimersByTime(1000) })
      vi.useRealTimers()
      const list = screen.getByRole('list', { name: 'Your agents' })
      const rows = within(list).getAllByRole('listitem')
      const FAMILY: Record<string, string> = { waiting: 'waiting', failed: 'blocked', working: 'active', idle: 'quiet' }
      const built = buildDay(evening(failing)).rows
      expect(built.map((r) => r.state), 'the control: the day holds a failed row').toContain('failed')
      let marked = 0
      rows.forEach((li, i) => {
        const r = built[i]
        const mark = li.querySelector<HTMLElement>('[data-orb-mark]')
        if (r.key === 'scheduled') { expect(mark, 'a scheduled row has a mark').toBeNull(); return }
        expect(mark, `${r.name} has no mark`).not.toBeNull()
        const look = ORB_STATE_OF_AGENT_ROW[r.state]
        const token = `--color-state-${FAMILY[r.state]}`
        expect(mark!.dataset.orbMark, r.name).toBe(look.state)
        expect(mark!.dataset.orbInkToken, r.name).toBe(token)
        const c = rgb(TONES[token])
        const inks = { alive: c, rest: c, ok: c, warn: c, info: c, danger: c, plate: undefined, hair: c, dark: true }
        const want = orbPaint({ [markSceneOf(look)]: 1 }, { size: 12, dpr: 1, clock: STILL_CLOCK, inks })
        expect(lastPaint(logs.get(mark!.querySelector('canvas')!) ?? []), `${r.name}: its mark is not its state's form in its tone`).toEqual(want)
        expect(mark!.dataset.orbBadge ?? null, `${r.name}'s badge`).toBe(r.state === 'failed' ? 'error' : null)
        expect(li.querySelector('[data-state-family] > span.size-2'), 'the old dot is drawn').toBeNull()
        marked += 1
      })
      expect(marked, 'the control: rows were marked').toBeGreaterThan(2)
      expect(asked, 'a still mark asked for a frame').toBe(0)
    } finally {
      HTMLCanvasElement.prototype.getContext = original.getContext
      Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: original.raf })
      for (const k of Object.keys(TONES)) document.documentElement.style.removeProperty(k)
    }
  })
})

describe('a row', () => {
  it('reads as its one sentence, with the track hidden from a screen reader', () => {
    show(evening())

    const list = screen.getByRole('list', { name: 'Your agents' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows[0]).toHaveAccessibleName(expect.stringMatching(/^PersonalClaw, waiting on you: write_file\./))
    expect(rows.map((r) => r.getAttribute('aria-label'))).toEqual(buildDay(evening()).rows.map((r) => r.sentence))
    for (const r of rows) expect(r.querySelector('[aria-hidden="true"] span')).not.toBeNull()
  })

  it('links an ask to Needs you with its panel open, and offers no way to answer it here', () => {
    show(evening())

    const link = screen.getByRole('link', { name: /^Answer:/ })
    expect(link).toHaveAttribute('href', '#/mission-control?open=waiting%3Aapproval%3Aa1')
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
    expect(order[pc + 1]).toBe('Answer')
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

  it('puts the rows that wait on you and the rows that failed on top, under a count of them', () => {
    show(evening(failing))

    expect(headText()).toBe('1 needs you · 2 running · 1 failed today')
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

  it('answers on Needs you through a link drawn as a button, never a button that sets the address', () => {
    show(evening())

    const link = screen.getByRole('link', { name: /^Answer:/ })
    expect(link.tagName).toBe('A')
    expect(link.className).toContain('rounded-pill')
    expect(screen.queryByRole('button', { name: /^Answer/ })).toBeNull()
  })

  it('names each link for the ask it opens, its visible words first', () => {
    show(evening())

    expect(screen.getByRole('link', { name: 'Answer: PersonalClaw’s ask' })).toHaveTextContent('Answer')
  })

  it('names the link for the first of a row’s asks when it holds two', () => {
    const asks = needsYou(waitingList(
      waitingItem({ kind: 'approval', id: 'approval:a1', work: 'chat:lisbon', brief: { tool: 'write_file' }, asked_at: iso(at(18, 18)) }),
      waitingItem({ kind: 'question', id: 'question:q2', work: 'chat:lisbon', asked_at: iso(at(18, 19)) }),
    ), [], [])
    show(evening({ asks }))

    expect(screen.getByRole('link', { name: 'Answer: PersonalClaw’s first of 2 asks' })).toBeInTheDocument()
  })

  it('counts a row that failed today and waits on you now as failed, as its sentence says', () => {
    const helper = { id: 'h1', task: 'early', done: true, agent: 'PersonalClaw',
      started: at(16, 10) / 1000, ended: at(16, 12) / 1000, error: 'Couldn’t reach api.github.com' } as never
    show(evening({ helpers: [helper] }))

    expect(headText()).toBe('1 needs you · 2 running · 1 failed today')
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

    expect(headText()).toBe('2 running · 1 failed today')
    const row = screen.getByRole('listitem', { name: /^Researcher, working:/ })
    expect(within(row).getByText('· failed at 09:10')).toBeInTheDocument()
    expect(row).not.toHaveAttribute('data-lifted')
  })

  it('gives the link a 44 px target on a touch screen, as the band’s other controls have', () => {
    show(evening())

    const link = screen.getByRole('link', { name: /^Answer:/ })
    expect(link.className).toContain('max-lg:min-h-11')
    // A linked name is a target too: its row opens there.
    expect(screen.getByRole('link', { name: 'PersonalClaw' }).className).toContain('max-lg:min-h-11')
  })

  it('gives the more-asks line’s link a 44 px target on a touch screen', () => {
    const asks = needsYou(waitingList(
      waitingItem({ kind: 'approval', id: 'approval:a1', work: 'chat:lisbon', brief: { tool: 'write_file' }, asked_at: iso(at(18, 18)) }),
      waitingItem({ kind: 'question', id: 'question:x', work: 'chat:nobody-runs-this' }),
    ), [], [])
    show(evening({ asks }))

    expect(screen.getByRole('link', { name: '1 more needs you' }).className).toContain('max-lg:min-h-11')
  })

  it('keeps an ask’s age whole when its words are cut short', () => {
    show(evening())

    const age = screen.getByText('· asked 2 min ago')
    expect(age.className.split(/\s+/)).toContain('shrink-0')
    expect(age.className.split(/\s+/)).not.toContain('truncate')
    // Its line clips at its edge, so a long age never draws under "Answer".
    expect((age.parentElement as HTMLElement).className.split(/\s+/)).toContain('overflow-hidden')
  })

  it('counts an ask that is on no row, with no row lifted for it', () => {
    const asks = needsYou(waitingList(waitingItem({ kind: 'question', id: 'question:x', work: 'chat:nobody-runs-this' })), [], [])
    show(evening({ asks }))

    expect(headText()).toBe('1 needs you · 2 running')
    expect(screen.getByRole('link', { name: '1 needs you' })).toHaveAttribute('href', '#/mission-control')
    expect(document.querySelector('[data-lifted]')).toBeNull()
    // No row holds an ask, so no line says "1 more": the count is the head's alone.
    expect(screen.queryByText(/more needs? you/)).toBeNull()
    expect(screen.getAllByRole('link', { name: /needs? you$/ })).toHaveLength(1)
  })

  it('counts the asks as Needs you does, the one on no row too', () => {
    const asks = needsYou(waitingList(
      waitingItem({ kind: 'approval', id: 'approval:a1', work: 'chat:lisbon', brief: { tool: 'write_file' }, asked_at: iso(at(18, 18)) }),
      waitingItem({ kind: 'question', id: 'question:x', work: 'chat:nobody-runs-this' }),
    ), [], [])
    show(evening({ asks }))

    expect(headText()).toBe('2 need you · 2 running')
    expect(document.querySelectorAll('[data-lifted]')).toHaveLength(1)
    expect(screen.getByRole('link', { name: '1 more needs you' })).toHaveAttribute('href', '#/mission-control')
  })

  it('says the plural past one: "3 need you" and "2 more need you"', () => {
    const asks = needsYou(waitingList(
      waitingItem({ kind: 'approval', id: 'approval:a1', work: 'chat:lisbon', brief: { tool: 'write_file' }, asked_at: iso(at(18, 18)) }),
      waitingItem({ kind: 'question', id: 'question:x', work: 'chat:nobody-runs-this' }),
      waitingItem({ kind: 'question', id: 'question:y', work: 'chat:nobody-runs-that' }),
    ), [], [])
    show(evening({ asks }))

    expect(headText()).toBe('3 need you · 2 running')
    expect(screen.getByRole('link', { name: '3 need you' })).toHaveAttribute('href', '#/mission-control')
    expect(screen.getByRole('link', { name: '2 more need you' })).toHaveAttribute('href', '#/mission-control')
  })

  it('shows only the filter’s rows under a filter, and still counts what Needs you counts', () => {
    show(evening(), { filter: 'working' })

    expect(document.querySelector('[data-lifted]')).toBeNull()
    expect(headText()).toMatch(/^1 needs you · /)
  })

  it('lifts nothing and counts nothing when nothing waits on you and nothing failed', () => {
    show(evening({ asks: [] }))

    expect(screen.getByRole('listitem', { name: /^PersonalClaw, working:/ })).toBeInTheDocument()
    expect(headText(), 'the head counts only what runs').toBe('2 running')
    expect(document.querySelector('[data-lifted]')).toBeNull()
  })
})

describe('where running work shows', () => {
  const second = live({ id: 'loop:api', kind: 'loop', work: 'loop:api', agent: 'Researcher', since: iso(at(18, 5)),
    title: 'Update the API reference', refs: { link: '#/loops/api' } })

  it('an agent with two runs names each, each with its own Open, and its name opens the one its line names', () => {
    const base = evening({ asks: [] })
    show({ ...base, live: [...base.live, second] })

    const row = screen.getByRole('listitem', { name: /^Researcher, working: Compare three standing desks · and 1 more\. Also Update the API reference\./ })
    expect(within(row).getByRole('link', { name: 'Researcher' })).toHaveAttribute('href', '#/loops/desks')
    expect(within(row).getByRole('link', { name: 'Open Compare three standing desks' })).toHaveAttribute('href', '#/loops/desks')
    expect(within(row).getByRole('link', { name: 'Open Update the API reference' })).toHaveAttribute('href', '#/loops/api')
    expect(headText()).toBe('3 running')
  })

  it('an agent with one run draws no Open: its name opens it', () => {
    show(evening({ asks: [] }))

    expect(screen.queryByRole('link', { name: /^Open / })).toBeNull()
  })

  it('held-up work says Blocked with the gateway’s reason, in the blocked tone, and is counted apart', () => {
    const base = evening({ asks: [] })
    show({ ...base, live: [base.live[0], live({ ...base.live[1], state: 'blocked', line: 'Waiting for a model slot',
      phaseSince: iso(at(18, 10)) })] })

    const row = screen.getByRole('listitem', { name: /^Researcher, blocked: Blocked: Waiting for a model slot\./ })
    expect(within(row).getByText('Blocked')).toBeInTheDocument()
    expect(row.querySelector('[data-mark="blocked"]')).not.toBeNull()
    expect(row.querySelector('[data-state-family]')?.getAttribute('data-state-family')).toBe('blocked')
    expect(headText()).toBe('1 running · 1 blocked')
  })

  it('work a restart cut off says Interrupted, when, and why, and the head counts it', () => {
    show(evening({ asks: [], cut: [{ at: at(18, 12), row: { work: 'chat:w', kind: 'chat', session: 'w', phase: 'tool', line: '', since: null,
      name: 'Draft the post', agent: 'Writer agent', started_at: null, route: '#/chat/w', blocked: false, waiting: [], project_id: '', loop_kind: '' } }] }))

    const row = screen.getByRole('listitem', { name: 'Writer agent, interrupted: Interrupted at 18:12: The gateway restarted while it ran..' })
    expect(within(row).getByRole('link', { name: 'Writer agent' })).toHaveAttribute('href', '#/chat/w')
    expect(headText()).toBe('2 running · 1 cut off by a restart')
  })

  it('a read that failed is named in its own line with Retry, and the rows it did not need still draw', async () => {
    const onRetry = vi.fn()
    show(evening({ scheduled: [] }), { unread: ['the schedule'], onRetry })

    expect(screen.getByRole('alert')).toHaveTextContent("Couldn't load the schedule.")
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('listitem', { name: /^Researcher, working:/ })).toBeInTheDocument()
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

  it('end a strip’s name inside its strip', () => {
    // 19:00 to 19:45 is 9.38% of the track, with nothing after it.
    const strip = { kind: 'scheduled' as const, start: at(19, 0), end: at(19, 45), label: 'Sync notes · every 5 min' }

    expect(nameRoom([strip], 0, view)).toBe('calc(9.38% - 0.5rem)')
    // A run after the strip leaves the strip's own end as the bound.
    expect(nameRoom([strip, run(20, 0, 'Digest')], 0, view)).toBe('calc(9.38% - 0.5rem)')
  })

  it('stop a late name at the ring before it, on a strip or not', () => {
    // Digest, late at 21:00, faces the 20:00 ring on the strip: half that hour, 6.25% of the track.
    const strip = { kind: 'scheduled' as const, start: at(18, 30), end: at(20, 10), label: 'Sync · every 5 min' }
    const marks = [strip, run(19, 0, 'Hourly'), run(20, 0, 'Hourly'), run(21, 0, 'Digest')]

    expect(nameRoom(marks, 3, view)).toBe('calc(6.25% - 0.5rem)')
  })

  it('start a strip’s name past a run that falls just after the strip starts, whose name yields', () => {
    const minutes = Array.from({ length: 240 }, (_, i) => (
      { trigger_id: 'heartbeat', trigger_name: 'Heartbeat tasks', at: (at(18, 21) + i * 60_000) / 1000 }))
    const hourly = Array.from({ length: 4 }, (_, i) => (
      { trigger_id: 'remedy', trigger_name: 'Self-remediation', at: (at(18, 23) + i * 3600_000) / 1000 }))
    const day = buildDay(evening({ scheduled: [...minutes, ...hourly] }))
    const { marks } = day.rows.find((r) => r.key === 'scheduled')!

    // The strip runs 18:21 to 22:20. Its first hourly run falls two minutes in, too soon to hold the
    // name, so the name starts at that run and ends before the next, an hour on.
    expect(marks.map((_, i) => nameRoom(marks, i, day))).toEqual(['calc(12.50% - 0.5rem)', YIELDS, YIELDS, YIELDS, YIELDS])
    show(evening({ scheduled: [...minutes, ...hourly] }))
    const row = screen.getByRole('listitem', { name: /^Scheduled, agent not named: / })
    expect(within(row).getByText('Heartbeat tasks · every 1 min')).toBeInTheDocument()
    // Each run keeps its ring; its name is in the line under the track.
    expect(row.querySelectorAll('[aria-hidden="true"] span.size-3')).toHaveLength(4)
    // A ring is never under the name: the name sets no stacking of its own and comes first, so a
    // ring draws over it should the two meet.
    const name = within(row).getByText('Heartbeat tasks · every 1 min').parentElement!
    expect(name.style.left).toBe('50.63%')
    expect(name.className.split(/\s+/).some((c) => /^z-/.test(c))).toBe(false)
    row.querySelectorAll('[aria-hidden="true"] span.size-3').forEach((r) =>
      expect(name.compareDocumentPosition(r) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy())
    expect(within(row).queryByText('Self-remediation')).toBeNull()
  })

  it('end a strip’s name before the first run that falls on it', () => {
    // As on a home whose heartbeat runs every minute: its strip starts a minute after now, and an
    // hourly run falls on it 59 minutes later, which leaves room for the name before it.
    const minutes = Array.from({ length: 240 }, (_, i) => (
      { trigger_id: 'heartbeat', trigger_name: 'Heartbeat tasks', at: (at(18, 21) + i * 60_000) / 1000 }))
    const hourly = Array.from({ length: 4 }, (_, i) => (
      { trigger_id: 'remedy', trigger_name: 'Self-remediation', at: (at(19, 20) + i * 3600_000) / 1000 }))
    const day = buildDay(evening({ scheduled: [...minutes, ...hourly] }))
    const { marks } = day.rows.find((r) => r.key === 'scheduled')!

    // 18:21 to 19:20 is 59 of the track's 480 minutes.
    expect(marks.map((_, i) => nameRoom(marks, i, day))).toEqual(['calc(12.29% - 0.5rem)', YIELDS, YIELDS, YIELDS, YIELDS])
    show(evening({ scheduled: [...minutes, ...hourly] }))
    const row = screen.getByRole('listitem', { name: /^Scheduled, agent not named: / })
    expect(within(row).getByText('Heartbeat tasks · every 1 min').parentElement!.style.left).toBe('50.21%')
  })

  it('give a strip’s name the longest stretch between the runs on it when none is half an hour long', () => {
    // 18:21 to 18:50, with a run at 18:45: the 24 minutes before it, 5% of the track.
    const burst = { kind: 'scheduled' as const, start: at(18, 21), end: at(18, 50), label: 'Burst · every 1 min' }

    expect(nameRoom([burst, run(18, 45, 'Hourly')], 0, view)).toBe('calc(5.00% - 0.5rem)')
  })

  it('draw a schedule too frequent to name each run as one strip with a width, and its name once', () => {
    const minutes = Array.from({ length: 240 }, (_, i) => (
      { trigger_id: 'heartbeat', trigger_name: 'Heartbeat tasks', at: (at(18, 21) + i * 60_000) / 1000 }))
    show(evening({ scheduled: minutes }))

    const row = screen.getByRole('listitem', { name: /^Scheduled, agent not named: / })
    const strips = row.querySelectorAll<HTMLElement>('[data-strip]')
    expect(strips).toHaveLength(1)
    // 18:21 to 22:20 is 239 of the track's 480 minutes.
    expect(strips[0].getAttribute('style')).toMatch(/left: 50\.21%; width: max\(49\.79%, 4px\)/)
    expect(within(row).getAllByText('Heartbeat tasks · every 1 min')).toHaveLength(1)
    expect(within(row).queryByText('Heartbeat tasks')).toBeNull()
  })

  it('leave the track without an edge label: the row\'s words name the run after the window', () => {
    show(evening({ scheduled: [
      { trigger_id: 't1', trigger_name: 'Evening summary', at: at(21, 30) / 1000 },
      { trigger_id: 't2', trigger_name: 'Morning triage', at: new Date(2026, 10, 4, 7, 30).getTime() / 1000 },
    ] }))

    const row = screen.getByRole('listitem', { name: /^Scheduled, agent not named: / })
    expect(row.textContent).not.toContain('→')
    const words = within(row).getByText(/Evening summary · tomorrow \d\d:\d\d Morning triage$/)
    // The line wraps at every width rather than cut off a schedule or the run after the window.
    expect(words.className).not.toContain('truncate')
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
