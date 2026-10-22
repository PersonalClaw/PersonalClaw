import { describe, expect, it } from 'vitest'
import { EMPTY_WINDOW, TRAIL_MARKS, foldSteps, linesOf, stepsOnInput, trailOf, type Step, type StepEvent, type StepWindow } from './steps'
import type { Work } from './inputs'

// ── The expanded card's step window (#3893) ─────────────────────────────────────────────────────
//
// Three lines at most, the step before, the current one and the next when it is known, and a trail
// of done marks for the rest of the run: never a scrolling log, however long the run.

const step = (n: number, kind: Step['kind'] = 'tool'): Step => ({ id: `s${n}`, kind, label: `Step ${n}` })
const fold = (events: readonly StepEvent[], from: StepWindow = EMPTY_WINDOW) => events.reduce(foldSteps, from)
const run = (finished: number): StepEvent[] =>
  Array.from({ length: finished }, (_, i) => [{ type: 'begin', step: step(i + 1) }, { type: 'finish', id: `s${i + 1}` }] as StepEvent[]).flat()

describe('the step window', () => {
  it('ten finished steps leave three lines, eight marks and +2', () => {
    const w = fold([...run(10), { type: 'begin', step: step(11) }, { type: 'next', step: step(12, 'result') }])
    expect(linesOf(w).map((l) => [l.place, l.step.id])).toEqual([['before', 's10'], ['current', 's11'], ['next', 's12']])
    expect(trailOf(w.done)).toEqual({ marks: TRAIL_MARKS, more: 2 })
    expect(TRAIL_MARKS).toBe(8)
  })

  it('never grows into a log: every length of run shows at most three lines, the latest ones', () => {
    for (let n = 0; n <= 40; n++) {
      const w = fold([...run(n), { type: 'begin', step: step(n + 1) }])
      const lines = linesOf(w)
      expect(lines.length, `after ${n} steps`).toBeLessThanOrEqual(3)
      expect(lines.at(-1)?.step.id).toBe(`s${n + 1}`)
      expect(w.done).toBe(n)
    }
  })

  it('a step that begins finishes the one before it, and a next that arrives takes the next line', () => {
    const w = fold([{ type: 'begin', step: step(1) }, { type: 'next', step: step(2) }, { type: 'begin', step: step(2) }])
    expect(w.before?.id).toBe('s1')
    expect(w.current?.id).toBe('s2')
    expect(w.next, 'the next began, so the line below is unknown again').toBeNull()
    expect(w.done).toBe(1)
  })

  it('with no next step known, the window shows two lines', () => {
    const w = fold([{ type: 'begin', step: step(1) }, { type: 'begin', step: step(2) }])
    expect(linesOf(w).map((l) => l.place)).toEqual(['before', 'current'])
  })

  it('progress fills only the current step, and a finished step keeps none', () => {
    let w = fold([{ type: 'begin', step: step(1) }, { type: 'progress', id: 's1', progress: 0.4 }])
    expect(w.current?.progress).toBe(0.4)
    w = foldSteps(w, { type: 'progress', id: 'other', progress: 0.9 })
    expect(w.current?.progress).toBe(0.4)
    w = foldSteps(w, { type: 'finish', id: 's1' })
    expect(w.before?.progress).toBeUndefined()
    expect(w.current).toBeNull()
  })

  it('a finish for a step that is not current changes nothing', () => {
    const w = fold([{ type: 'begin', step: step(1) }])
    expect(foldSteps(w, { type: 'finish', id: 's9' })).toBe(w)
  })

  it('the trail is the run length up to eight, then +N', () => {
    expect(trailOf(0)).toEqual({ marks: 0, more: 0 })
    expect(trailOf(3)).toEqual({ marks: 3, more: 0 })
    expect(trailOf(8)).toEqual({ marks: 8, more: 0 })
    expect(trailOf(9)).toEqual({ marks: 8, more: 1 })
  })
})

describe('the machine\'s phase inputs fold into each work\'s window', () => {
  const work: Work = { id: 'w1', name: 'Release notes', phase: 'thinking', line: '', startedAt: 0 }

  it('a start opens the first step, each phase change is the next step, and the end finishes it', () => {
    let s = stepsOnInput({}, { type: 'start', work })
    s = stepsOnInput(s, { type: 'phase', id: 'w1', phase: 'searching', line: 'changelog style' })
    s = stepsOnInput(s, { type: 'phase', id: 'w1', phase: 'tool', line: 'Reading the changelog' })
    expect(s.w1.before).toMatchObject({ kind: 'tool', label: 'Searching: changelog style' })
    expect(s.w1.current).toMatchObject({ kind: 'tool', label: 'Working: Reading the changelog' })
    expect(s.w1.done).toBe(2)
    s = stepsOnInput(s, { type: 'finish', id: 'w1', how: 'done' })
    expect(s.w1.current).toBeNull()
    expect(s.w1.done).toBe(3)
  })

  it('a phase that repeats the current step\'s words is the same step', () => {
    let s = stepsOnInput({}, { type: 'start', work })
    s = stepsOnInput(s, { type: 'phase', id: 'w1', phase: 'searching', line: 'changelog style' })
    const again = stepsOnInput(s, { type: 'phase', id: 'w1', phase: 'searching', line: 'changelog style' })
    expect(again.w1).toBe(s.w1)
  })

  it('an input for no work it knows, or not about work, leaves the windows alone', () => {
    const s = stepsOnInput({}, { type: 'start', work })
    expect(stepsOnInput(s, { type: 'phase', id: 'nobody', phase: 'tool', line: 'x' })).toBe(s)
    expect(stepsOnInput(s, { type: 'click' })).toBe(s)
  })
})
