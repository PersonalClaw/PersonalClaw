import { describe, expect, it } from 'vitest'
import { SITUATIONS, viewOf, situationOf, type MatrixEvent, type Situation } from '../../lib/buddy/machine'
import { TIMINGS } from '../../lib/buddy/timing'
import { advance, fire, startPlayground, use, type Playground } from './playground'
import { DAY_LENGTH, SCRIPTED_DAY, dayAt } from './scriptedDay'

// ── The playground's model (#3892) ──────────────────────────────────────────────────────────────
//
// The controls send the machine its inputs and the clock fires its timers at their lengths. The
// scripted day runs end to end through the real reducer, into every family and the situations its
// story names, and nothing in it is refused.

const used = (ids: readonly string[], from: Playground = startPlayground()) => ids.reduce(use, from)

describe('the clock', () => {
  it('fires a timer at its length and not before', () => {
    const pg = used(['start'])
    expect(pg.due.showDelay).toBe(TIMINGS.showDelay)
    expect(advance(pg, TIMINGS.showDelay - 1).machine.appearing).toBe(true)
    expect(advance(pg, TIMINGS.showDelay).machine.appearing).toBe(false)
  })

  it('fires the next timer at its time, moving the clock there', () => {
    const pg = fire(used(['start']))
    expect(pg.now).toBe(TIMINGS.showDelay)
    expect(pg.log.at(-1)?.label).toBe('showDelay timer')
  })

  it('cancels a timer the machine cancels', () => {
    const pg = used(['start', 'click'])
    expect(pg.due.collapse).toBeDefined()
    expect(used(['escape'], pg).due.collapse).toBeUndefined()
  })
})

describe('a control that is off sends nothing', () => {
  it('Allow with no alert shown, and Stop listening while not listening', () => {
    const pg = startPlayground()
    expect(use(pg, 'allow')).toBe(pg)
    expect(use(pg, 'unlisten')).toBe(pg)
  })

  it('a cell the matrix says cannot occur: a click on a hidden buddy', () => {
    const pg = startPlayground()
    expect(situationOf(pg.machine)).toBe('hidden')
    expect(use(pg, 'click')).toBe(pg)
  })
})

describe('the scripted day', () => {
  const { pg, done } = dayAt(DAY_LENGTH)
  const visited = new Set<Situation>(pg.log.flatMap((e) => [e.from, e.to]))
  const events = new Set<MatrixEvent | null>(pg.log.map((e) => e.event))

  it('runs every step, each one heard', () => {
    expect(done).toBe(SCRIPTED_DAY.length)
    const heard = pg.log.filter((e) => !e.label.endsWith(' timer') && e.label !== 'Buddy status on')
    expect(heard).toHaveLength(SCRIPTED_DAY.length)
  })

  it('passes through the situations its story tells', () => {
    for (const s of ['hidden', 'compact', 'alert', 'finished', 'listening', 'offline'] as const) expect(visited, s).toContain(s)
    expect(visited.size).toBeLessThanOrEqual(SITUATIONS.length)
  })

  it('sends an answer three times, each shown and never sent', () => {
    const answers = pg.log.flatMap((e) => e.effects).filter((e) => e.kind === 'answer')
    expect(answers.map((a) => a.kind === 'answer' && a.choice.kind)).toEqual(['allow', 'send', 'deny'])
  })

  it('tells every family, the return summary, offline and back, and a restart', () => {
    const families = new Set<string>()
    const truths = new Set<string>()
    let p = startPlayground()
    for (let t = 0, i = 0; t <= DAY_LENGTH; t += 500) {
      ;({ pg: p, done: i } = dayAt(t, p, i))
      const view = viewOf(p.machine)
      if (view.family) families.add(view.family)
      if (view.truth) truths.add(view.truth.kind)
    }
    expect([...families].sort()).toEqual(['active', 'blocked', 'quiet', 'waiting'])
    for (const kind of ['phase', 'alert', 'done', 'listening', 'returnSummary', 'offline', 'recovery', 'restarting']) expect(truths, kind).toContain(kind)
    expect(events).toContain('answered')
  })

  it('plays the same day the same way twice', () => {
    expect(dayAt(DAY_LENGTH).pg.log).toEqual(pg.log)
  })
})
