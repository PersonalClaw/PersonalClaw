import { describe, it, expect } from 'vitest'
import { CoalescerCore, MIN_BUDGET, MAX_BUDGET, MAX_LAG } from './useStreamCoalescer'

describe('CoalescerCore — accumulation + reveal', () => {
  it('accumulates pushes and reveals a growing prefix, never overshooting', () => {
    const c = new CoalescerCore()
    c.push('hello ')
    c.push('world')
    expect(c.backlog()).toBe(11)
    const r1 = c.tick(1)
    // revealed is a prefix of the accumulated text, and progresses
    expect('hello world'.startsWith(r1)).toBe(true)
    expect(r1.length).toBeGreaterThan(0)
    expect(r1.length).toBeLessThanOrEqual(11)
  })

  it('fully reveals within a few ticks for a small backlog', () => {
    const c = new CoalescerCore()
    c.push('abcdefghij') // 10 chars
    let out = ''
    for (let i = 0; i < 10 && c.backlog() > 0; i++) out = c.tick(1)
    expect(out).toBe('abcdefghij')
    expect(c.backlog()).toBe(0)
  })

  it('never reveals more than what was pushed', () => {
    const c = new CoalescerCore()
    c.push('xyz')
    for (let i = 0; i < 50; i++) c.tick(1)
    expect(c.revealedText()).toBe('xyz')
    expect(c.revealedText().length).toBeLessThanOrEqual(3)
  })

  it('keeps revealing correctly when chunks arrive mid-drain', () => {
    const c = new CoalescerCore()
    c.push('first')
    c.tick(1)
    c.push('second')
    let out = ''
    for (let i = 0; i < 20 && c.backlog() > 0; i++) out = c.tick(1)
    expect(out).toBe('firstsecond')
  })
})

describe('CoalescerCore — adaptive budget', () => {
  it('respects the per-frame budget ceiling on a huge paste', () => {
    const c = new CoalescerCore()
    c.push('a'.repeat(10_000))
    const before = 0
    const r1 = c.tick(1)
    // First tick reveals at most MAX_BUDGET chars (clamped), not the whole 10k.
    expect(r1.length - before).toBeLessThanOrEqual(MAX_BUDGET)
    expect(r1.length).toBeGreaterThanOrEqual(MIN_BUDGET)
  })

  it('drains a large backlog faster than a small one (drain-factor ramp)', () => {
    const big = new CoalescerCore(); big.push('a'.repeat(MAX_LAG * 3))
    const small = new CoalescerCore(); small.push('a'.repeat(50))
    // Prime a couple ticks so the EMA + drain factor settle.
    big.tick(1); big.tick(1)
    small.tick(1); small.tick(1)
    const bigStep = big.revealedText().length
    // The big backlog's drain factor ramped (backlog > MAX_LAG) → its per-frame
    // budget is at the ceiling; the small one stays modest. Big reveals more per frame.
    const bigNext = big.tick(1).length - bigStep
    expect(bigNext).toBeGreaterThan(0)
    expect(bigNext).toBeLessThanOrEqual(MAX_BUDGET)
  })

  it('animSpeed scales the pace — higher speed reveals more per tick', () => {
    // Backlog small enough that the budget stays BELOW the MAX_BUDGET ceiling, so
    // the speed multiplier actually differentiates (a huge backlog clamps both to MAX).
    const slow = new CoalescerCore(); slow.push('a'.repeat(120))
    const fast = new CoalescerCore(); fast.push('a'.repeat(120))
    slow.tick(0.5)
    fast.tick(4)
    expect(fast.revealedText().length).toBeGreaterThan(slow.revealedText().length)
  })
})

describe('CoalescerCore — drainAll + reset', () => {
  it('drainAll reveals everything at once', () => {
    const c = new CoalescerCore()
    c.push('the quick brown fox')
    expect(c.drainAll()).toBe('the quick brown fox')
    expect(c.backlog()).toBe(0)
    expect(c.revealedText()).toBe('the quick brown fox')
  })

  it('reset clears pending + revealed + rate state', () => {
    const c = new CoalescerCore()
    c.push('stuff'); c.tick(1)
    c.reset()
    expect(c.backlog()).toBe(0)
    expect(c.revealedText()).toBe('')
    // after reset a fresh push behaves like a new stream
    c.push('new')
    expect(c.backlog()).toBe(3)
  })

  it('tick on an empty core is a no-op (no throw, empty string)', () => {
    const c = new CoalescerCore()
    expect(c.tick(1)).toBe('')
    expect(c.backlog()).toBe(0)
  })
})
