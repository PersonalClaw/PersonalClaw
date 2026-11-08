import { describe, expect, it } from 'vitest'
import type { Loop, SpawnedAgent } from '../../../lib/api'
import { needsYou } from '../../../lib/needsYou'
import type { AgentActivityEntity } from '../../../lib/useAgentActivity'
import { waitingItem, waitingList } from '../../../test/waiting'
import { MAX_ROWS, ago, buildDay, clock, type DayInput } from './agentDayModel'

// ── Home's "Your agents today": the band's rows, read off what the gateway already says ──────────
//
// A row per agent, on a window of time around now. What it did comes from its model calls (the day
// read), what it is doing from the live feed, what waits on you from Needs You (the read Attention
// counts), what failed from its helpers and loops, and what comes next from the schedule. No time is
// written that no source gave.

const at = (hh: number, mm: number, day = 3) => new Date(2026, 10, day, hh, mm).getTime()
const NOW = at(18, 20)
const iso = (ms: number) => new Date(ms).toISOString()

function input(over: Partial<DayInput> = {}): DayInput {
  return {
    now: NOW, narrow: false, defaultAgent: 'PersonalClaw',
    day: { since: iso(at(14, 20)), until: iso(NOW), cut: 0, agents: [] },
    live: [], asks: [], helpers: [], loops: [], scheduled: [],
    ...over,
  }
}

function live(over: Partial<AgentActivityEntity> & Pick<AgentActivityEntity, 'id'>): AgentActivityEntity {
  return { kind: 'session', state: 'working', title: 'Work', refs: { link: '' }, ...over }
}

function block(start: number, end: number, session = 'dashboard:lisbon', source = 'chat') {
  return { start: iso(start), end: iso(end), session, source }
}

const loop = (over: Partial<Loop>): Loop => ({ id: 'desks', name: 'Compare three standing desks', status: 'running',
  agent: 'Researcher', max_cycles: 8, total_cycles: 3, completed_at: null, ...over } as Loop)

describe('the rows', () => {
  it('draws what an agent did in the window as done marks, at their times', () => {
    const view = buildDay(input({
      day: { since: iso(at(14, 20)), until: iso(NOW), cut: 0, agents: [
        { agent: '', blocks: [block(at(16, 50), at(17, 0)), block(at(17, 35), at(17, 45))], before: null },
      ] },
    }))

    const [row] = view.rows
    expect(row.name).toBe('PersonalClaw')
    expect(row.marks.map((m) => [m.kind, m.start, m.end])).toEqual([
      ['done', at(16, 50), at(17, 0)], ['done', at(17, 35), at(17, 45)],
    ])
    expect(row.state).toBe('idle')
  })

  it('reads the default agent by its name, and its unnamed and named work is one row', () => {
    const view = buildDay(input({
      day: { since: iso(at(14, 20)), until: iso(NOW), cut: 0, agents: [
        { agent: '', blocks: [block(at(16, 0), at(16, 5))], before: null },
        { agent: 'PersonalClaw', blocks: [block(at(17, 0), at(17, 5))], before: null },
      ] },
    }))

    expect(view.rows.map((r) => r.name)).toEqual(['PersonalClaw'])
    expect(view.rows[0].marks).toHaveLength(2)
  })

  it('says an agent is waiting on you while it has an open ask, never working', () => {
    const asks = needsYou(waitingList(
      waitingItem({ kind: 'approval', id: 'approval:a1', work: 'chat:lisbon', brief: { tool: 'write_file' },
        asked_at: iso(at(18, 18)) }),
    ), [], [])
    const view = buildDay(input({
      live: [live({ id: 'session:lisbon', work: 'chat:lisbon', agent: '', since: iso(at(18, 0)) })],
      asks,
    }))

    const [row] = view.rows
    expect(row.state).toBe('waiting')
    expect(row.marks.map((m) => m.kind)).toContain('waiting')
    expect(row.attention).toBe(`#/dashboard?view=attention&open=${encodeURIComponent(asks[0].key)}`)
    expect(row.say).toContain('write_file')
    expect(row.asked).toBe('2 min')
    expect(row.sentence).toMatch(/ Asked 2 min ago\.$/)
  })

  it('says how long ago an ask was asked from the ask itself, not from its mark the window cuts', () => {
    const asks = needsYou(waitingList(
      waitingItem({ kind: 'approval', id: 'approval:a1', work: 'chat:lisbon', asked_at: iso(at(13, 0)) }),
    ), [], [])
    const view = buildDay(input({ live: [live({ id: 'session:lisbon', work: 'chat:lisbon', agent: '', since: iso(at(12, 0)) })], asks }))

    const [row] = view.rows
    expect(row.marks.find((m) => m.kind === 'waiting')?.start).toBe(view.since)
    expect(row.asked).toBe('5 h 20 min')
  })

  it('says how long ago in the words a person uses', () => {
    expect([ago(20_000), ago(2 * 60_000), ago(60 * 60_000), ago(70 * 60_000), ago(26 * 3600_000), ago(50 * 3600_000)])
      .toEqual(['1 min', '2 min', '1 h', '1 h 10 min', '1 day', '2 days'])
  })

  it('counts what waits on you exactly as Attention does, from the same rows', () => {
    const asks = needsYou(waitingList(
      waitingItem({ kind: 'approval', id: 'approval:a1', work: 'chat:lisbon' }),
      waitingItem({ kind: 'loop_question', id: 'loop_question:fix', work: 'loop:fix', brief: { merge: true } }),
      waitingItem({ kind: 'question', id: 'question:x', work: 'chat:nobody-runs-this' }),
    ), [], [{ id: 'p1', slug: 'tidy', description: 'Tidy notes', created_at: iso(at(9, 0)) }])
    const view = buildDay(input({
      live: [
        live({ id: 'session:lisbon', work: 'chat:lisbon', agent: '' }),
        live({ id: 'loop:fix', kind: 'loop', work: 'loop:fix', agent: 'Code reviewer' }),
      ],
      asks,
    }))

    expect(view.waitingCount).toBe(asks.length)
    expect(view.rows.filter((r) => r.state === 'waiting').map((r) => r.name)).toEqual(['PersonalClaw', 'Code reviewer'])
    expect(view.unattributed).toBe(2)
  })

  it('draws a working agent from when its work began up to now', () => {
    const view = buildDay(input({
      live: [live({ id: 'loop:desks', kind: 'loop', work: 'loop:desks', agent: 'Researcher', since: iso(at(17, 51)),
        title: 'Compare three standing desks' })],
      loops: [loop({})],
    }))

    const [row] = view.rows
    expect(row.state).toBe('working')
    expect(row.marks.find((m) => m.kind === 'working')).toMatchObject({ start: at(17, 51), end: NOW })
    expect(row.say).toBe('Compare three standing desks')
  })

  it('marks a helper that failed today at the time it ended, with what went wrong', () => {
    const helper: SpawnedAgent = { id: 'h1', task: 'cut the release', done: true, agent: 'Release helper',
      started: at(16, 10) / 1000, ended: at(16, 12) / 1000, error: 'Couldn’t reach api.github.com\nmore' }
    const view = buildDay(input({ helpers: [helper] }))

    const [row] = view.rows
    expect(row.state).toBe('failed')
    expect(row.marks).toEqual([expect.objectContaining({ kind: 'failed', start: at(16, 12) })])
    expect(row.say).toBe(`Failed at ${clock(at(16, 12))}: Couldn’t reach api.github.com`)
    expect(row.asked).toBe('')
  })

  it('says in its sentence that a row which waits on you now failed earlier today', () => {
    const asks = needsYou(waitingList(
      waitingItem({ kind: 'approval', id: 'approval:a1', work: 'chat:lisbon', asked_at: iso(at(18, 18)) }),
    ), [], [])
    const helper: SpawnedAgent = { id: 'h1', task: 'cut the release', done: true, agent: 'PersonalClaw',
      started: at(16, 10) / 1000, ended: at(16, 12) / 1000, error: 'Couldn’t reach api.github.com' }
    const view = buildDay(input({ live: [live({ id: 'session:lisbon', work: 'chat:lisbon', agent: '' })], asks, helpers: [helper] }))

    const [row] = view.rows
    expect(row.state).toBe('waiting')
    expect(row.marks.map((m) => m.kind)).toContain('failed')
    expect(row.failed).toBe(`Failed at ${clock(at(16, 12))}: Couldn’t reach api.github.com`)
    expect(row.sentence).toContain(`Failed at ${clock(at(16, 12))}: Couldn’t reach api.github.com.`)
  })

  it('never says nothing ran today beside a failure today', () => {
    const helper: SpawnedAgent = { id: 'h1', task: 'early', done: true, agent: 'Release helper',
      started: at(9, 0) / 1000, ended: at(9, 2) / 1000, error: 'boom' }
    const view = buildDay(input({ helpers: [helper] }))

    expect(view.rows.map((r) => r.state)).toEqual(['failed'])
    expect(view.empty).toBe('window')
  })

  it('leaves out a helper that failed on an earlier day', () => {
    const helper: SpawnedAgent = { id: 'h0', task: 'old', done: true, agent: 'Release helper',
      started: at(16, 10, 2) / 1000, ended: at(16, 12, 2) / 1000, error: 'boom' }

    expect(buildDay(input({ helpers: [helper] })).rows).toEqual([])
  })
})

describe('what comes next', () => {
  it('puts a scheduled run in the window on a row of its own, named by its trigger', () => {
    const view = buildDay(input({
      scheduled: [
        { trigger_id: 't1', trigger_name: 'Evening summary', at: at(21, 30) / 1000 },
        { trigger_id: 't2', trigger_name: 'Morning triage', at: at(7, 30, 4) / 1000 },
      ],
    }))

    const row = view.rows.find((r) => r.key === 'scheduled')!
    expect(row.name).toBe('Scheduled')
    expect(row.sentence).toMatch(/^Scheduled, agent not named: /)
    expect(row.marks).toEqual([expect.objectContaining({ kind: 'scheduled', start: at(21, 30), label: 'Evening summary' })])
    expect(row.after).toBe(`tomorrow ${clock(at(7, 30, 4))}`)
  })

  it('names each schedule in the window once, and says the one after it comes then', () => {
    const view = buildDay(input({
      scheduled: [
        { trigger_id: 't0', trigger_name: 'Water the plants', at: at(19, 0) / 1000 },
        { trigger_id: 't1', trigger_name: 'Feed the cat', at: at(19, 30) / 1000 },
        { trigger_id: 't2', trigger_name: 'Evening summary', at: at(21, 30) / 1000 },
        { trigger_id: 't3', trigger_name: 'Morning triage', at: at(7, 30, 4) / 1000 },
      ],
    }))

    const row = view.rows.find((r) => r.key === 'scheduled')!
    // Its line and its sentence name every trigger once: the track that draws them is hidden from
    // a screen reader.
    expect(row.say).toBe(`${clock(at(19, 0))} Water the plants · ${clock(at(19, 30))} Feed the cat · ${clock(at(21, 30))} Evening summary`)
    expect(row.sentence).toMatch(new RegExp(`^Scheduled, agent not named: ${row.say}\\. `))
    expect(row.sentence).toMatch(new RegExp(` Then tomorrow ${clock(at(7, 30, 4))} Morning triage\\.$`))
  })

  it('names a trigger that runs every 5 minutes once, with how often and its last, never 48 times', () => {
    const every5 = Array.from({ length: 200 }, (_, i) => ({ trigger_id: 'mail', trigger_name: 'Check mail', at: (at(18, 25) + i * 5 * 60_000) / 1000 }))
    const view = buildDay(input({ scheduled: [...every5, { trigger_id: 'plants', trigger_name: 'Water the plants', at: at(19, 0) / 1000 }] }))

    const row = view.rows.find((r) => r.key === 'scheduled')!
    expect(row.sentence.match(/Check mail/g)).toHaveLength(1)
    expect(row.sentence).toMatch(new RegExp(`^Scheduled, agent not named: ${clock(at(18, 25))} Check mail, every 5 min to ${clock(at(22, 20))} · ${clock(at(19, 0))} Water the plants\\.$`))
    expect(row.sentence.length).toBeLessThan(200)
  })

  it('says the run after the window when it comes from a trigger that ran once in it', () => {
    const view = buildDay(input({
      scheduled: [
        { trigger_id: 't0', trigger_name: 'Water the plants', at: at(19, 0) / 1000 },
        { trigger_id: 't1', trigger_name: 'Feed the cat', at: at(19, 30) / 1000 },
        { trigger_id: 't2', trigger_name: 'Evening summary', at: at(21, 30) / 1000 },
        { trigger_id: 't0', trigger_name: 'Water the plants', at: at(19, 0, 4) / 1000 },
      ],
    }))

    const row = view.rows.find((r) => r.key === 'scheduled')!
    expect(row.sentence).toMatch(new RegExp(` Then tomorrow ${clock(at(19, 0, 4))} Water the plants\\.$`))
  })

  it('says the run after the window when more than two fall in it, whatever their names say', () => {
    const next = `tomorrow ${clock(at(7, 30, 4))}`
    const view = buildDay(input({
      scheduled: [
        { trigger_id: 't0', trigger_name: `Plan for ${next}`, at: at(19, 0) / 1000 },
        { trigger_id: 't1', trigger_name: 'Feed the cat', at: at(19, 30) / 1000 },
        { trigger_id: 't2', trigger_name: 'Evening summary', at: at(21, 30) / 1000 },
        { trigger_id: 't3', trigger_name: 'Morning triage', at: at(7, 30, 4) / 1000 },
      ],
    }))

    const row = view.rows.find((r) => r.key === 'scheduled')!
    expect(row.sentence).toMatch(new RegExp(` Then ${next} Morning triage\\.$`))
  })

  it('names its next runs in its words, the one after the window too, so the track needs no edge label', () => {
    const view = buildDay(input({
      scheduled: [
        { trigger_id: 't0', trigger_name: 'Water the plants', at: at(19, 45) / 1000 },
        { trigger_id: 't1', trigger_name: 'Evening summary', at: at(21, 30) / 1000 },
        { trigger_id: 't2', trigger_name: 'Morning triage', at: at(7, 30, 4) / 1000 },
      ],
    }))

    const row = view.rows.find((r) => r.key === 'scheduled')!
    expect(row.say).toBe(`${clock(at(19, 45))} Water the plants · ${clock(at(21, 30))} Evening summary · tomorrow ${clock(at(7, 30, 4))} Morning triage`)
  })

  it('leaves out a run the schedule says will not fire', () => {
    const view = buildDay(input({
      scheduled: [{ trigger_id: 't1', trigger_name: 'Evening summary', at: at(21, 30) / 1000, suppressed_by: 'quiet hours' }],
    }))

    expect(view.rows).toEqual([])
  })

  it('draws a loop with only a cycle budget as an open tail that names no time', () => {
    const view = buildDay(input({
      live: [live({ id: 'loop:desks', kind: 'loop', work: 'loop:desks', agent: 'Researcher', since: iso(at(17, 51)) })],
      loops: [loop({ max_cycles: 8 })],
    }))

    const tail = view.rows[0].marks.find((m) => m.kind === 'tail')!
    expect(tail).toMatchObject({ start: NOW, end: view.until, label: 'Runs to cycle 8' })
    expect(tail.label).not.toMatch(/\d{1,2}:\d{2}/)
  })

  it('writes no time that no source gave', () => {
    const asks = needsYou(waitingList(waitingItem({ kind: 'approval', id: 'approval:a1', work: 'chat:lisbon',
      asked_at: iso(at(18, 18)) })), [], [])
    const sources = {
      day: { since: iso(at(14, 20)), until: iso(NOW), cut: 0, agents: [
        { agent: '', blocks: [block(at(18, 0), at(18, 18))], before: iso(at(13, 10)) },
        { agent: 'Inbox triage', blocks: [], before: iso(at(7, 30)) },
      ] },
      live: [
        live({ id: 'session:lisbon', work: 'chat:lisbon', agent: '', since: iso(at(18, 0)) }),
        live({ id: 'loop:desks', kind: 'loop', work: 'loop:desks', agent: 'Researcher', since: iso(at(17, 51)) }),
      ],
      asks,
      loops: [loop({})],
      helpers: [{ id: 'h1', task: 't', done: true, agent: 'Release helper', started: at(16, 10) / 1000,
        ended: at(16, 12) / 1000, error: 'boom' }],
      scheduled: [{ trigger_id: 't1', trigger_name: 'Evening summary', at: at(21, 30) / 1000 },
        { trigger_id: 't2', trigger_name: 'Morning triage', at: at(7, 30, 4) / 1000 }],
    }
    const known = new Set([at(13, 10), at(7, 30), at(16, 12), at(21, 30), at(7, 30, 4), at(18, 0), at(18, 18), at(17, 51), NOW]
      .map(clock))
    const view = buildDay(input(sources))

    const words = [...view.rows, ...view.idle]
      .flatMap((r) => [r.say, r.sentence, r.before, r.after, ...r.marks.map((m) => m.label)])
      .join(' ')
    const times = words.match(/\d{1,2}:\d{2}/g) ?? []
    expect(times.length).toBeGreaterThan(0)
    expect(times.filter((t) => !known.has(t))).toEqual([])
  })
})

describe('a schedule too frequent to name each run', () => {
  // 14:20 to 22:20 around 18:20. A minute's schedule is 240 runs to the window's edge, an hourly one 4.
  const minutes = (n = 240, id = 'heartbeat') => Array.from({ length: n }, (_, i) => (
    { trigger_id: id, trigger_name: 'Heartbeat tasks', at: (at(18, 21) + i * 60_000) / 1000 }))
  const hourly = Array.from({ length: 4 }, (_, i) => (
    { trigger_id: 'remedy', trigger_name: 'Self-remediation', at: (at(18, 23) + i * 3600_000) / 1000 }))
  const scheduledRow = (over: Partial<DayInput>) => buildDay(input(over)).rows.find((r) => r.key === 'scheduled')!
  // As the week read sends a schedule with quiet hours from `quietFrom` to 07:00: each quiet run
  // sent and marked, and the read cut at its 200th run, quiet ones counted.
  const quietFrom = (id: string, name: string, every: number, first: number, from: number) =>
    Array.from({ length: 200 }, (_, i) => {
      const t = first + i * every
      const h = new Date(t).getHours()
      return { trigger_id: id, trigger_name: name, at: t / 1000, ...(h >= from || h < 7 ? { suppressed_by: 'quiet' } : {}) }
    })
  const triage = { trigger_id: 'triage', trigger_name: 'Morning triage', at: at(7, 30, 4) / 1000 }

  it('is one strip from its first run to its last, with one name and how often it runs', () => {
    const row = scheduledRow({ scheduled: minutes() })

    expect(row.marks).toEqual([
      { kind: 'scheduled', start: at(18, 21), end: at(22, 20), label: 'Heartbeat tasks · every 1 min' },
    ])
  })

  it('leaves an hourly schedule a mark and a name for each run', () => {
    const row = scheduledRow({ scheduled: hourly })

    expect(row.marks.map((m) => [m.start, m.end, m.label])).toEqual(
      [at(18, 23), at(19, 23), at(20, 23), at(21, 23)].map((t) => [t, t, 'Self-remediation']))
    expect(row.say).toBe(`${clock(at(18, 23))} Self-remediation, 4 runs to ${clock(at(21, 23))}`)
  })

  it('draws the two together as one strip and a mark per hourly run, and says each once in its line', () => {
    const row = scheduledRow({ scheduled: [...minutes(), ...hourly] })

    expect(row.marks.map((m) => [m.start, m.end])).toEqual([
      [at(18, 21), at(22, 20)], [at(18, 23), at(18, 23)], [at(19, 23), at(19, 23)], [at(20, 23), at(20, 23)], [at(21, 23), at(21, 23)],
    ])
    // The line under the track is the per-schedule summary, word for word as the sentence says it.
    expect(row.say).toBe(`${clock(at(18, 21))} Heartbeat tasks, every 1 min to ${clock(at(22, 20))} · ${clock(at(18, 23))} Self-remediation, 4 runs to ${clock(at(21, 23))}`)
    expect(row.sentence).toBe(`Scheduled, agent not named: ${row.say}.`)
  })

  it('runs on to the window’s edge when the week read cut it short, and writes no count or last the cut made', () => {
    // The week read sends at most 200 runs a schedule: a minute's schedule stops at 21:40, and
    // the read names it cut.
    const row = scheduledRow({
      scheduled: [...minutes(200), { trigger_id: 'triage', trigger_name: 'Morning triage', at: at(7, 30, 4) / 1000 }],
      scheduledCut: ['heartbeat'],
    })

    expect(row.marks).toEqual([
      { kind: 'scheduled', start: at(18, 21), end: at(22, 20), label: 'Heartbeat tasks · every 1 min' },
    ])
    expect(row.say).toBe(`${clock(at(18, 21))} Heartbeat tasks, every 1 min`)
    // What comes first after the window is a heartbeat the read did not send, not tomorrow's triage.
    expect(row.after).toBe('')
    expect(row.sentence).toBe(`Scheduled, agent not named: ${row.say}.`)
  })

  it('ends at its last run that fires when the read was cut inside its quiet hours', () => {
    // Quiet from 20:00: the read's 200 runs end at 21:40, quiet, so the strip ends at 19:59.
    const row = scheduledRow({ scheduled: [...quietFrom('heartbeat', 'Heartbeat tasks', 60_000, at(18, 21), 20), triage], scheduledCut: ['heartbeat'] })

    expect(row.marks).toEqual([
      { kind: 'scheduled', start: at(18, 21), end: at(19, 59), label: 'Heartbeat tasks · every 1 min' },
    ])
    expect(row.say).toBe(`${clock(at(18, 21))} Heartbeat tasks, every 1 min to ${clock(at(19, 59))}`)
    // The schedule fires again at 07:00, a run the read never sent: what comes first is not known.
    expect(row.after).toBe('')
  })

  it('names no run after the window when the read was cut past it, in quiet hours', () => {
    // Every 2 min, quiet from 20:00: the read ends at 01:00, so the 07:00 run it never sent can come
    // before tomorrow's triage.
    const row = scheduledRow({ scheduled: [...quietFrom('sync', 'Sync notes', 120_000, at(18, 22), 20), triage], scheduledCut: ['sync'] })

    expect(row.marks).toEqual([
      { kind: 'scheduled', start: at(18, 22), end: at(19, 58), label: 'Sync notes · every 2 min' },
    ])
    expect(row.say).toBe(`${clock(at(18, 22))} Sync notes, every 2 min to ${clock(at(19, 58))}`)
    expect(row.after).toBe('')
  })

  it('names no run after the window when every run the read sent is quiet', () => {
    const view = buildDay(input({
      day: { since: iso(at(14, 20)), until: iso(NOW), cut: 0, agents: [{ agent: '', blocks: [block(at(17, 0), at(17, 10))], before: null }] },
      scheduled: [...quietFrom('heartbeat', 'Heartbeat tasks', 60_000, at(18, 21), 18), triage],
      scheduledCut: ['heartbeat'],
    }))

    expect(view.rows.map((r) => r.key)).toEqual(['PersonalClaw'])
    expect(view.rows.flatMap((r) => [r.say, r.sentence]).join(' ')).not.toContain('Morning triage')
  })

  it('names no run after the window when any cut schedule’s read ends before it', () => {
    // The heartbeat read ends at 21:40, the mail read tomorrow at 11:00: the digest at 22:22 may
    // come after a heartbeat the read did not send.
    const every5 = Array.from({ length: 200 }, (_, i) => ({ trigger_id: 'mail', trigger_name: 'Check mail', at: (at(18, 25) + i * 5 * 60_000) / 1000 }))
    const digest = { trigger_id: 'digest', trigger_name: 'Evening digest', at: at(22, 22) / 1000 }
    const row = scheduledRow({ scheduled: [...minutes(200), ...every5, digest, triage], scheduledCut: ['heartbeat', 'mail'] })

    expect(row.after).toBe('')
    expect(row.sentence).not.toContain('Then')
  })

  it('names no run after the window when a cut schedule’s read ended before now', () => {
    // The read's 200 minute runs end at 17:19, all past: nothing after them is known.
    const past = Array.from({ length: 200 }, (_, i) => ({ trigger_id: 'heartbeat', trigger_name: 'Heartbeat tasks', at: (at(14, 0) + i * 60_000) / 1000 }))
    const view = buildDay(input({
      day: { since: iso(at(14, 20)), until: iso(NOW), cut: 0, agents: [{ agent: '', blocks: [block(at(17, 0), at(17, 10))], before: null }] },
      scheduled: [...past, triage],
      scheduledCut: ['heartbeat'],
    }))

    expect(view.rows.map((r) => r.key)).toEqual(['PersonalClaw'])
    expect(view.rows.flatMap((r) => [r.say, r.sentence]).join(' ')).not.toContain('Morning triage')
  })

  it('keeps the seconds in how often it runs', () => {
    const every90s = Array.from({ length: 100 }, (_, i) => ({ trigger_id: 'sync', trigger_name: 'Sync', at: (at(18, 21) + i * 90_000) / 1000 }))

    expect(scheduledRow({ scheduled: every90s }).marks.map((m) => m.label)).toEqual(['Sync · every 1 min 30 s'])
  })

  it('ends at its last run in the window when the read was cut past the window', () => {
    const every5 = Array.from({ length: 200 }, (_, i) => ({ trigger_id: 'mail', trigger_name: 'Check mail', at: (at(18, 25) + i * 5 * 60_000) / 1000 }))
    const row = scheduledRow({ scheduled: [...every5, { trigger_id: 'plants', trigger_name: 'Water the plants', at: at(19, 0) / 1000 }], scheduledCut: ['mail'] })

    expect(row.say).toBe(`${clock(at(18, 25))} Check mail, every 5 min to ${clock(at(22, 20))} · ${clock(at(19, 0))} Water the plants`)
    expect(row.after).toBe(clock(at(22, 25)))
  })

  it('ends at the narrow window’s edge when the read was cut past it', () => {
    // On a phone the window ends at 21:20; the read's 200 minute runs go on to 21:40.
    const row = scheduledRow({ narrow: true, scheduled: [...minutes(200), triage], scheduledCut: ['heartbeat'] })

    expect(row.say).toBe(`${clock(at(18, 21))} Heartbeat tasks, every 1 min to ${clock(at(21, 20))}`)
    expect(row.after).toBe(clock(at(21, 21)))
  })

  it('folds runs 59 minutes apart into one strip', () => {
    const every59 = Array.from({ length: 4 }, (_, i) => ({ trigger_id: 'digest', trigger_name: 'Digest', at: (at(18, 30) + i * 59 * 60_000) / 1000 }))

    expect(scheduledRow({ scheduled: every59 }).marks).toEqual([
      { kind: 'scheduled', start: at(18, 30), end: at(21, 27), label: 'Digest · every 59 min' },
    ])
  })

  it('folds uneven runs under an hour apart into one strip that names no cadence', () => {
    const uneven = [[19, 0], [19, 5], [20, 0], [20, 5], [21, 0], [21, 5]].map(([h, m]) => (
      { trigger_id: 'notes', trigger_name: 'Sync notes', at: at(h, m) / 1000 }))
    const row = scheduledRow({ scheduled: uneven })

    expect(row.marks).toEqual([{ kind: 'scheduled', start: at(19, 0), end: at(21, 5), label: 'Sync notes' }])
    expect(row.say).toBe(`${clock(at(19, 0))} Sync notes, 6 runs to ${clock(at(21, 5))}`)
  })
})

describe('the window', () => {
  it('runs four hours either side of now, and three on a narrow screen', () => {
    expect(buildDay(input())).toMatchObject({ since: NOW - 4 * 3600_000, until: NOW + 4 * 3600_000 })
    expect(buildDay(input({ narrow: true }))).toMatchObject({ since: NOW - 3 * 3600_000, until: NOW + 3 * 3600_000 })
  })

  it('says nothing ran in the window when only earlier work and what is due are there', () => {
    const view = buildDay(input({
      day: { since: iso(at(14, 20)), until: iso(NOW), cut: 0, agents: [{ agent: 'Inbox triage', blocks: [], before: iso(at(7, 30)) }] },
      scheduled: [{ trigger_id: 't1', trigger_name: 'Evening summary', at: at(21, 30) / 1000 }],
    }))

    expect(view.empty).toBe('window')
    expect(view.rows.map((r) => r.key)).toEqual(['Inbox triage', 'scheduled'])
  })

  it('says nothing ran today when nothing did', () => {
    expect(buildDay(input()).empty).toBe('today')
  })

  it('takes the edge from the latest work earlier today', () => {
    const view = buildDay(input({
      day: { since: iso(at(14, 20)), until: iso(NOW), cut: 0, agents: [{ agent: 'Code reviewer', blocks: [], before: iso(at(14, 0)) }] },
    }))

    expect(view.rows[0]).toMatchObject({ before: clock(at(14, 0)), say: `Last ran ${clock(at(14, 0))}` })
    expect(view.rows[0].sentence).not.toContain('Earlier today')
  })

  it('says the work earlier today when its line says something else, whatever its words hold', () => {
    const view = buildDay(input({
      day: { since: iso(at(14, 20)), until: iso(NOW), cut: 0, agents: [{ agent: 'Code reviewer', blocks: [], before: iso(at(14, 0)) }] },
      live: [live({ id: 'loop:fix', kind: 'loop', work: 'loop:fix', agent: 'Code reviewer', title: `Review the ${clock(at(14, 0))} build` })],
    }))

    expect(view.rows[0].sentence).toContain(`Earlier today ${clock(at(14, 0))}.`)
  })
})

describe('many agents', () => {
  it('sorts waiting, then failed, then working, then idle', () => {
    const asks = needsYou(waitingList(waitingItem({ kind: 'approval', id: 'approval:a1', work: 'chat:w' })), [], [])
    const view = buildDay(input({
      day: { since: iso(at(14, 20)), until: iso(NOW), cut: 0, agents: [{ agent: 'Idle one', blocks: [block(at(15, 0), at(15, 5))], before: null }] },
      live: [
        live({ id: 'session:busy', work: 'chat:busy', agent: 'Busy' }),
        live({ id: 'session:w', work: 'chat:w', agent: 'Waiter' }),
      ],
      asks,
      helpers: [{ id: 'h', task: 't', done: true, agent: 'Broke', started: at(16, 0) / 1000, ended: at(16, 1) / 1000, error: 'x' }],
    }))

    expect(view.rows.map((r) => [r.name, r.state])).toEqual([
      ['Waiter', 'waiting'], ['Broke', 'failed'], ['Busy', 'working'], ['Idle one', 'idle'],
    ])
  })

  it(`folds the idle agents into one line past ${MAX_ROWS} rows`, () => {
    const agents = Array.from({ length: 7 }, (_, i) => ({ agent: `Idle ${i}`, blocks: [block(at(15, i), at(15, i + 1))], before: null }))
    const view = buildDay(input({
      day: { since: iso(at(14, 20)), until: iso(NOW), cut: 0, agents },
      live: [1, 2, 3].map((i) => live({ id: `session:b${i}`, work: `chat:b${i}`, agent: `Busy ${i}` })),
    }))

    expect(view.rows.map((r) => r.state)).toEqual(['working', 'working', 'working'])
    expect(view.idle).toHaveLength(7)
  })

  it(`keeps every row when there are ${MAX_ROWS} or fewer`, () => {
    const agents = Array.from({ length: MAX_ROWS }, (_, i) => ({ agent: `Idle ${i}`, blocks: [block(at(15, i), at(15, i + 1))], before: null }))

    const view = buildDay(input({ day: { since: iso(at(14, 20)), until: iso(NOW), cut: 0, agents } }))

    expect(view.rows).toHaveLength(MAX_ROWS)
    expect(view.idle).toEqual([])
  })
})

describe('a row read aloud', () => {
  it('is one sentence: who, the state in words, and what', () => {
    const asks = needsYou(waitingList(waitingItem({ kind: 'approval', id: 'approval:a1', work: 'chat:lisbon',
      brief: { tool: 'write_file' } })), [], [])
    const view = buildDay(input({
      live: [live({ id: 'session:lisbon', work: 'chat:lisbon', agent: '' })],
      asks,
      scheduled: [{ trigger_id: 't1', trigger_name: 'Evening summary', at: at(21, 30) / 1000 }],
    }))

    expect(view.rows[0].sentence).toMatch(/^PersonalClaw, waiting on you: .*write_file/)
  })
})
