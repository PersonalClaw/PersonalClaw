import { describe, expect, it } from 'vitest'

import { KIND_TO_TEMPLATE, baseContainer, keysEquivalent, templateForKind } from './containerKey'

describe('stream-key equivalence', () => {
  it('treats a loop key and a run key for the same id as the same container', () => {
    // The regression this closes: `===` on raw keys drops every event silently — the
    // stream connects, the cockpit renders, and nothing updates.
    expect(keysEquivalent('loop:abc', 'run:abc')).toBe(true)
    expect(keysEquivalent('loop:abc', 'workflow:run:abc')).toBe(true)
    expect(keysEquivalent('workflow:abc', 'abc')).toBe(true)
  })

  it('does not conflate different containers', () => {
    expect(keysEquivalent('loop:abc', 'loop:xyz')).toBe(false)
    expect(keysEquivalent('workflow:run:abc', 'run:xyz')).toBe(false)
  })

  it('never treats empty keys as equivalent', () => {
    // Two blanks matching would route every unkeyed event to every open cockpit.
    expect(keysEquivalent('', '')).toBe(false)
    expect(keysEquivalent('loop:', 'run:')).toBe(false)
    expect(keysEquivalent(null, undefined)).toBe(false)
    expect(keysEquivalent('loop:abc', null)).toBe(false)
  })

  it('strips the longest prefix first', () => {
    // Shortest-first would leave `run:abc` behind and fail a comparison that should match.
    expect(baseContainer('workflow:run:abc')).toBe('abc')
    expect(baseContainer('workflow:abc')).toBe('abc')
    expect(baseContainer('loop:abc')).toBe('abc')
    expect(baseContainer('abc')).toBe('abc')
  })

  it('tolerates whitespace and missing values', () => {
    expect(baseContainer('  loop:abc  ')).toBe('abc')
    expect(baseContainer(null)).toBe('')
    expect(baseContainer(undefined)).toBe('')
  })

  it('is reflexive and symmetric', () => {
    expect(keysEquivalent('loop:abc', 'loop:abc')).toBe(true)
    expect(keysEquivalent('run:abc', 'loop:abc')).toBe(keysEquivalent('loop:abc', 'run:abc'))
  })
})

describe('legacy loop-kind aliases', () => {
  it('resolves every legacy kind', () => {
    for (const kind of ['general', 'goal', 'code', 'design', 'research']) {
      expect(templateForKind(kind)).not.toBe('')
    }
  })

  it('resolves an unknown kind to nothing rather than a default', () => {
    // Running the wrong workflow is harder to debug than running none.
    expect(templateForKind('nonsense')).toBe('')
    expect(templateForKind('')).toBe('')
    expect(templateForKind(null)).toBe('')
    expect(templateForKind(undefined)).toBe('')
  })

  it('tolerates case and whitespace in stored references', () => {
    // These come out of months-old chat transcripts and saved crons.
    expect(templateForKind('GOAL')).toBe('goal-pursuit-open-ended')
    expect(templateForKind(' code ')).toBe('code-implementation')
  })

  it('reads a verify command as the verifiable variant', () => {
    // A goal loop with a command that proves it WAS the verifiable variant in all but
    // name; ignoring that would drop the user into a template that discards their input.
    expect(templateForKind('goal', { hasVerifyCommand: true })).toBe('goal-pursuit-verifiable')
    expect(templateForKind('goal')).toBe('goal-pursuit-open-ended')
  })

  it('lets an explicit variant win over the inferred one', () => {
    expect(templateForKind('goal', { variant: 'open-ended', hasVerifyCommand: true })).toBe(
      'goal-pursuit-open-ended',
    )
    expect(templateForKind('goal', { variant: 'verifiable' })).toBe('goal-pursuit-verifiable')
  })

  it('only special-cases goal', () => {
    // A verify command on a research loop does not make it a verifiable goal.
    expect(templateForKind('research', { hasVerifyCommand: true })).toBe('deep-research')
  })

  it('exposes the table for the picker', () => {
    expect(Object.keys(KIND_TO_TEMPLATE).sort()).toEqual([
      'code',
      'design',
      'general',
      'goal',
      'research',
    ])
  })
})
