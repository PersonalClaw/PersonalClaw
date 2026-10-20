import { describe, expect, it } from 'vitest'
import { fmtElapsed } from './duration'

// The format rounds to whole seconds once, before it splits off minutes and hours. Rounding
// the remainder after the split printed a 60 in the seconds place: 119.6 s read "1m 60s",
// 59.6 s read "60s", and a run under half a second read "0s", which says nothing happened.

describe('fmtElapsed rounds before it splits', () => {
  it('says under a second for a measured time that rounds to nothing', () => {
    expect(fmtElapsed(0.3)).toBe('under 1s')
    expect(fmtElapsed(0.001)).toBe('under 1s')
  })

  it('rounds half a second up, as Math.round does', () => {
    expect(fmtElapsed(0.5)).toBe('1s')
  })

  it('never prints 60 in the seconds place', () => {
    expect(fmtElapsed(59.4)).toBe('59s')
    expect(fmtElapsed(59.6)).toBe('1m 0s')
    expect(fmtElapsed(119.4)).toBe('1m 59s')
    expect(fmtElapsed(119.6)).toBe('2m 0s')
  })

  it('carries a rounded-up last minute into the hour', () => {
    expect(fmtElapsed(3599.4)).toBe('59m 59s')
    expect(fmtElapsed(3599.6)).toBe('1h 0m')
    expect(fmtElapsed(3600)).toBe('1h 0m')
  })

  it('says hours and minutes past the hour, the seconds rounded first', () => {
    expect(fmtElapsed(3659.6)).toBe('1h 1m')
    expect(fmtElapsed(3720)).toBe('1h 2m')
    expect(fmtElapsed(7322.2)).toBe('2h 2m')
  })

  it('still shows nothing when nothing was measured', () => {
    expect(fmtElapsed(undefined)).toBe('')
    expect(fmtElapsed(0)).toBe('')
    expect(fmtElapsed(-4)).toBe('')
  })
})
