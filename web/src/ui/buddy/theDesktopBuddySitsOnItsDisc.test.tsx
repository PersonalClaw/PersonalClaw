// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { BuddyOrb } from '../BuddyOrb'
import { BuddyFace } from '../../pages/buddy/BuddyPresence'
import { recorder, STILL_CLOCK, type Entry } from '../../test/orbPaint'
import { css, type Inks } from '../orb/halftone'
import { paintBuddy, PLATE_R, STILL_WHOLE } from './buddyHalftone'
import { stillOrbPng } from './stillOrbPng'

// ── On the desktop the buddy sits on a disc of its own (round 6's desktop strip) ─────────────────
//
// The buddy's window puts the orb on a disc: the surface, lifted by a soft shadow and ringed by the
// hairline, so it reads over any wallpaper or window. The disc is drawn before any dot, centred on
// the orb, `PLATE_R` of its size across the middle; everywhere else (the playground's sheets, a
// banner's icon) the orb is drawn on the surface it sits on, with no disc.

const WHITE = { r: 255, g: 255, b: 255 }
const HAIR = { r: 225, g: 227, b: 225 }
const INK = { r: 177, g: 46, b: 24 }
const REST = { r: 95, g: 99, b: 104 }
const INKS: Inks = { alive: INK, rest: REST, ok: INK, warn: INK, info: INK, danger: INK, plate: WHITE, hair: HAIR, dark: false }

/** Each `arc` call in a log, with its place in it. */
const arcs = (log: Entry[]) => log.flatMap((e, i) => (e[0] === 'arc' ? [{ i, x: Number(e[1]), y: Number(e[2]), r: Number(e[3]) }] : []))
/** The place in a log of the first `name` set to `value` at or after `from`. */
const setAt = (log: Entry[], name: string, value: unknown, from = 0) => log.findIndex((e, i) => i >= from && e[0] === '=' && e[1] === name && e[2] === value)

describe('paintBuddy, on and off its disc', () => {
  const size = 48, pad = 10, centre = pad + size / 2

  it('draws the disc first, in the surface with a shadow, then rings it with the hairline', () => {
    const log: Entry[] = []
    paintBuddy(recorder(log), { ready: 1 }, STILL_CLOCK, INKS, { size, pad, dpr: 1, plate: true, whole: STILL_WHOLE })
    const all = arcs(log)
    expect(all.length, 'the dots are drawn').toBeGreaterThan(100)
    const [disc, ...dots] = all
    expect(disc, 'the first arc is the disc, about the orb\'s centre').toMatchObject({ x: centre, y: centre })
    expect(disc.r).toBeCloseTo(size * PLATE_R, 6)
    expect(dots.every((d) => d.r < size * 0.1), 'every later arc is a dot').toBe(true)
    const blur = log.findIndex((e, i) => i < disc.i && e[0] === '=' && e[1] === 'shadowBlur' && Number(e[2]) > 0)
    expect(blur, 'a soft shadow is set before the disc').toBeGreaterThanOrEqual(0)
    expect(setAt(log, 'fillStyle', css(WHITE)), 'the disc is the surface').toBeLessThan(disc.i)
    const fill = log.findIndex((e, i) => i > disc.i && e[0] === 'fill')
    const ring = setAt(log, 'strokeStyle', css(HAIR), disc.i)
    const stroke = log.findIndex((e, i) => i > ring && e[0] === 'stroke')
    expect(fill, 'the disc is filled').toBeGreaterThan(disc.i)
    expect(ring, 'then ringed in the hairline').toBeGreaterThan(fill)
    expect(stroke).toBeGreaterThan(ring)
    expect(setAt(log, 'shadowColor', 'transparent', disc.i), 'and the shadow ends before the hairline').toBeLessThan(ring)
    expect(stroke, 'all of it before the first dot').toBeLessThan(dots[0].i)
  })

  it('moves and swells the disc with the whole orb', () => {
    const log: Entry[] = []
    paintBuddy(recorder(log), { ready: 1 }, STILL_CLOCK, INKS, { size, pad, dpr: 2, plate: true, whole: { lift: -0.1, sway: 0.05, swell: 1.2, dim: 1 } })
    const disc = arcs(log)[0]
    const at = log.slice(0, disc.i).filter((e) => e[0] === 'setTransform').at(-1)!
    // The orb's centre lands at (centre + sway, centre + lift), and the disc scales about it.
    expect(at.slice(1, 5)).toEqual([2 * 1.2, 0, 0, 2 * 1.2])
    expect(Number(at[5]) + centre * 2 * 1.2).toBeCloseTo((centre + 0.05 * size) * 2, 6)
    expect(Number(at[6]) + centre * 2 * 1.2).toBeCloseTo((centre - 0.1 * size) * 2, 6)
    expect(disc).toMatchObject({ x: centre, y: centre })
  })

  it('draws no disc and no shadow without it', () => {
    const log: Entry[] = []
    paintBuddy(recorder(log), { ready: 1 }, STILL_CLOCK, INKS, { size, pad, dpr: 1, plate: false, whole: STILL_WHOLE })
    expect(arcs(log).some((a) => Math.abs(a.r - size * PLATE_R) < 1e-6)).toBe(false)
    expect(log.some((e) => e[0] === '=' && e[1] === 'shadowBlur' && Number(e[2]) > 0)).toBe(false)
  })
})

describe('the buddy\'s window is the one place with the disc', () => {
  const getContext = HTMLCanvasElement.prototype.getContext
  const toDataURL = HTMLCanvasElement.prototype.toDataURL
  let log: Entry[] = []
  const TOKENS = { '--color-orb-alive': '#b12e18', '--color-orb-rest': '#5f6368', '--color-surface': '#ffffff', '--color-outline-variant': '#e1e3e1' }

  beforeEach(() => {
    log = []
    HTMLCanvasElement.prototype.getContext = (() => recorder(log)) as never
    HTMLCanvasElement.prototype.toDataURL = (() => 'data:image/png;base64,AAAA') as never
    for (const [k, v] of Object.entries(TOKENS)) document.documentElement.style.setProperty(k, v)
  })
  afterEach(() => {
    cleanup()
    HTMLCanvasElement.prototype.getContext = getContext
    HTMLCanvasElement.prototype.toDataURL = toDataURL
    for (const k of Object.keys(TOKENS)) document.documentElement.style.removeProperty(k)
  })

  /** Whether the log holds a disc for an orb of `size`. */
  const hasDisc = (size: number) => arcs(log).some((a) => Math.abs(a.r - size * PLATE_R) < 1e-6)

  it('the window\'s face sets its orb on the disc, and paints it', () => {
    const { container } = render(<BuddyFace state="idle" label="Nothing running" />)
    const orb = container.querySelector('[data-buddy-orb]')
    expect(orb?.hasAttribute('data-buddy-plate'), 'BuddyFace passes plate').toBe(true)
    expect(hasDisc(48)).toBe(true)
  })

  it('an orb elsewhere has none', () => {
    const { container } = render(<BuddyOrb state="idle" line="" size={48} still />)
    expect(container.querySelector('[data-buddy-orb]')?.hasAttribute('data-buddy-plate')).toBe(false)
    expect(hasDisc(48)).toBe(false)
  })

  it('a banner\'s icon has none', () => {
    expect(stillOrbPng('approval', 96)).toBe('data:image/png;base64,AAAA')
    expect(hasDisc(96)).toBe(false)
  })
})
