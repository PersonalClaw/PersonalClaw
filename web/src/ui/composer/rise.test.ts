/**
 * The composer's awake springs grow it by a bounded number of PIXELS, not a fraction of its width.
 *
 * Measured at the shipped `full` width on a 1440px window, the composer is 1212px wide in a 16px
 * gutter. Focus scaled it by `expr(0.016, 0.4)` = 0.01408 at the default expressiveness — 8.5px a
 * side — and drag-over by `expr(0.028, 0.4)` = 0.02464 — 14.9px a side, the whole gutter. The page
 * clip then cut its shadow (and at drag-over the composer itself) into a straight line at the rail.
 */
import { describe, expect, it } from 'vitest'
import { DROP_GROW_PX, RISE_GROW_PX, riseScale } from './rise'

const FOCUS = 0.016 * (0.4 + 0.6 * 0.8)   // expr(0.016, 0.4) at the default expressiveness 0.8
const DROP = 0.028 * (0.4 + 0.6 * 0.8)    // expr(0.028, 0.4)
const perSide = (scale: number, width: number) => ((scale - 1) * width) / 2

describe('riseScale', () => {
  it('a full-width composer grows by the px cap a side, not by its fraction', () => {
    expect(perSide(1 + FOCUS, 1212), 'the premise: the old fraction overran the cap').toBeGreaterThan(RISE_GROW_PX)
    expect(perSide(riseScale(FOCUS, RISE_GROW_PX, 1212), 1212)).toBeCloseTo(RISE_GROW_PX, 6)
    expect(perSide(riseScale(DROP, DROP_GROW_PX, 1212), 1212)).toBeCloseTo(DROP_GROW_PX, 6)
  })

  it('a composer too narrow to reach the cap keeps exactly the spring it had', () => {
    expect(riseScale(FOCUS, RISE_GROW_PX, 358)).toBe(1 + FOCUS)   // the 390px phone column
  })

  it('an unmeasured composer keeps the plain fraction instead of guessing a width', () => {
    expect(riseScale(FOCUS, RISE_GROW_PX, 0)).toBe(1 + FOCUS)
  })

  it('the caps leave the gutter room for the composer’s own awake shadow (see tokens.css)', () => {
    // 16px gutter − the larger cap must still hold `--shadow-composer-focus` (≤10px reach), which
    // `design/composerAwakeShadow.test.ts` measures from the token itself.
    expect(16 - Math.max(RISE_GROW_PX, DROP_GROW_PX)).toBeGreaterThanOrEqual(10)
  })
})
