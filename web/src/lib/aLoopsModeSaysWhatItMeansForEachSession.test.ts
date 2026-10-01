import { describe, it, expect } from 'vitest'
import { loopModeMeaning } from './loopMode'

// ── A loop's Mode says what it means for every session the loop runs ───────────────────────────
//
// The words on the composer, the plan review and the cockpit used to speak of the workers alone,
// while the planner ran unattended whatever the Mode said and an Attended Code loop merged its
// work into your branch unasked. The Mode now decides the planner, the workers, the merges and
// whose spend it is, and its words say each of those, for the kinds that have them.

describe('what a Mode means, kind by kind', () => {
  it('an Attended Code loop: its planner and workers ask, its merges wait for you, its spend is yours', () => {
    const s = loopModeMeaning(true, 'code')
    expect(s).toMatch(/planner’s and workers’ tool calls ask for your approval/)
    expect(s).toMatch(/goes into your branch only when you approve it/)
    expect(s).toMatch(/under your git name/)
    expect(s).toMatch(/daily cap for unattended work does not count it/)
  })

  it('an Unattended Code loop: nobody is asked, it merges by itself, its spend is capped', () => {
    const s = loopModeMeaning(false, 'code')
    expect(s).toMatch(/planner’s and workers’ tool calls run without asking/)
    expect(s).toMatch(/merges each finished task into your branch by itself, under your git name/)
    expect(s).toMatch(/counts against the daily cap/)
  })

  it.each(['goal', 'research', 'design'])('a %s loop, which merges nothing, says nothing of merging', (kind) => {
    for (const attended of [true, false]) {
      const s = loopModeMeaning(attended, kind)
      expect(s).toMatch(/planner’s and workers’/)
      expect(s).not.toMatch(/branch|merge/)
    }
  })

  it('a general loop is a workflow run: its steps ask, and its spend is a run’s', () => {
    expect(loopModeMeaning(true, 'general')).toMatch(/Each step asks you before it starts/)
    expect(loopModeMeaning(true, 'general')).toMatch(/counts against the daily cap, as every workflow run’s does/)
    expect(loopModeMeaning(false, 'general')).toMatch(/without asking/)
  })
})
