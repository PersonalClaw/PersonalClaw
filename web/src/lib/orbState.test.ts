import { describe, expect, it } from 'vitest'
import { WORK_PHASES, type WorkPhase } from './workPhase'
import { ORB_STATES, ORB_STATE_OF, ORB_STATE_OF_AGENT_ROW, ORB_STATE_OF_PIP, orbStateOfWire } from './orbState'

// ── The orb's one mapping (#6117) ──────────────────────────────────────────────────────────────
//
// The gateway's run state reaches the orb as a phase of the contract (`lib/workPhase.ts`), and one
// table, this one, turns every phase into one of the orb's five states: ready, thinking, working,
// waiting on you and done. A wait on the owner carries the badge that says which wait it is, and a
// failed turn is done's form with its × and no success bloom. Nothing else maps a phase to the orb.

describe('every phase of the contract is one of the five states', () => {
  it('the table covers the whole contract and nothing else', () => {
    expect(Object.keys(ORB_STATE_OF).sort()).toEqual([...WORK_PHASES].sort())
    for (const phase of WORK_PHASES) expect(ORB_STATES).toContain(ORB_STATE_OF[phase].state)
  })

  it('there are five states, and each one is reached', () => {
    expect([...ORB_STATES]).toEqual(['ready', 'thinking', 'working', 'waiting', 'done'])
    const reached = new Set(WORK_PHASES.map((p) => ORB_STATE_OF[p].state))
    expect([...reached].sort()).toEqual([...ORB_STATES].sort())
  })

  it.each<[WorkPhase, string]>([
    ['idle', 'ready'],
    ['listening', 'ready'],
    ['queued', 'thinking'],
    ['thinking', 'thinking'],
    ['planning', 'thinking'],
    ['streaming', 'thinking'],
    ['waiting_model', 'thinking'],
    // A held-up model call: it is pending until the retry starts, and then the turn thinks again.
    ['retrying', 'thinking'],
    ['tool', 'working'],
    ['searching', 'working'],
    ['delegating', 'working'],
    ['approval', 'waiting'],
    ['input', 'waiting'],
    ['done', 'done'],
    ['error', 'done'],
  ])('%s is %s', (phase, state) => {
    expect(ORB_STATE_OF[phase].state).toBe(state)
  })
})

describe('a wait or a failure says which it is', () => {
  it('an approval wait carries !, a question ?, a failure ×, and nothing else carries a badge', () => {
    expect(ORB_STATE_OF.approval.badge).toBe('approval')
    expect(ORB_STATE_OF.input.badge).toBe('input')
    expect(ORB_STATE_OF.error.badge).toBe('error')
    const badged = WORK_PHASES.filter((p) => ORB_STATE_OF[p].badge)
    expect(badged.sort()).toEqual(['approval', 'error', 'input'])
  })

  it('done blooms once, and a failure closes to the same form with no bloom', () => {
    expect(ORB_STATE_OF.done.bloom).toBe(true)
    expect(ORB_STATE_OF.error.bloom).toBe(false)
    for (const phase of WORK_PHASES) if (phase !== 'done') expect(ORB_STATE_OF[phase].bloom).toBe(false)
  })

  it('a failure and done are drawn in the rest ink, and only a live agent is alive', () => {
    for (const phase of ['done', 'error', 'idle'] as const) expect(ORB_STATE_OF[phase].ink).toBe('rest')
    for (const phase of ['thinking', 'tool', 'approval', 'input'] as const) expect(ORB_STATE_OF[phase].ink).toBe('alive')
    // Listening is ready's still form, in the alive ink: the agent attends while the owner speaks.
    expect(ORB_STATE_OF.listening).toEqual({ state: 'ready', ink: 'alive', bloom: false })
  })
})

describe('only a phase reaches the orb', () => {
  it('a raw string, an orb state or a status line names no state', () => {
    for (const raw of ['working', 'ready', 'Thinking', 'waiting', '', 'tool ', null, undefined, 3]) {
      expect(orbStateOfWire(raw)).toBeNull()
    }
    expect(orbStateOfWire('tool')).toEqual(ORB_STATE_OF.tool)
  })

  it('the table cannot be written to', () => {
    expect(Object.isFrozen(ORB_STATE_OF)).toBe(true)
    for (const phase of WORK_PHASES) expect(Object.isFrozen(ORB_STATE_OF[phase])).toBe(true)
  })
})

describe('a sidebar row is drawn from its pip', () => {
  it('running is working, waiting the clasp, and failed and unread done, the failure with its ×', () => {
    expect(Object.fromEntries(Object.entries(ORB_STATE_OF_PIP).map(([pip, look]) => [pip, [look.state, look.badge ?? null]]))).toEqual({
      running: ['working', null], waiting: ['waiting', null], failed: ['done', 'error'], unread: ['done', null],
    })
    for (const look of Object.values(ORB_STATE_OF_PIP)) expect(look.bloom, 'a list mark blooms').toBe(false)
    expect(Object.isFrozen(ORB_STATE_OF_PIP)).toBe(true)
  })
})

describe("a Home agent row is drawn from its row's state", () => {
  it('working is working, waiting the clasp, failed done with its ×, and idle the open ring', () => {
    expect(Object.fromEntries(Object.entries(ORB_STATE_OF_AGENT_ROW).map(([row, look]) => [row, [look.state, look.badge ?? null]]))).toEqual({
      working: ['working', null], waiting: ['waiting', null], failed: ['done', 'error'], idle: ['ready', null],
    })
    for (const look of Object.values(ORB_STATE_OF_AGENT_ROW)) expect(look.bloom, 'a list mark blooms').toBe(false)
    expect(Object.isFrozen(ORB_STATE_OF_AGENT_ROW)).toBe(true)
  })
})
