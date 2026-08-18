/**
 * FLUID-MOTION §S2 T2.2 (atom FM-3) — `LiquidShape` with motion ALLOWED.
 *
 * What is asserted is the DECISION and the DOM, not a painted frame — jsdom has no
 * compositor, so "morphs smoothly" is a browser claim, not a jsdom one. What jsdom CAN
 * prove, and what this file pins:
 *
 *   1. REACHABILITY. `FM-4` owns adoption, so this primitive has no product call site
 *      yet — which makes the barrel the only thing standing between it and being
 *      unreachable dead code. The rail imports it THROUGH `./index`, so deleting the
 *      export line fails here rather than silently stranding the atom.
 *   2. The geometry genuinely differs between the two shape states (a morph that ends
 *      where it began is a no-op dressed as an effect).
 *   3. `expr()` actually scales the amplitude — AND at expressiveness 0 the shape is
 *      still not a circle, because `expr`'s floor exists so refined ≠ dead.
 *   4. The `exprHeavy` tier is reported, so `FM-7`'s zero-motion proof and any future
 *      reader can see which tier was taken instead of inferring it.
 *
 * The paired reduced-motion case lives in `LiquidShape.reducedMotion.test.tsx` (its own
 * file, because framer-motion caches its reduced-motion probe in a module singleton).
 */

import { describe, expect, it, afterEach } from 'vitest'
import { render } from '@testing-library/react'
import { runtime } from '../../design/runtime'
import { LiquidShape as FromBarrel } from './index'
import { LiquidShape } from './LiquidShape'

const DEFAULT_EXPRESSIVENESS = runtime.expressiveness
afterEach(() => { runtime.expressiveness = DEFAULT_EXPRESSIVENESS })

const svg = () => document.querySelector<SVGSVGElement>('svg[data-liquid-shape]')!
const path = () => document.querySelector<SVGPathElement>('svg[data-liquid-shape] path')!
const d = () => path().getAttribute('d') ?? ''

describe('LiquidShape is reachable', () => {
  it('is exported from the ui/motion barrel', () => {
    // Identity, not existence: a re-export of something else would pass a truthiness
    // check. `FM-4` wires the vocabulary; until then this is the only reachability.
    expect(FromBarrel).toBe(LiquidShape)
  })
})

describe('the silhouette', () => {
  it('renders a real closed path, not an empty shell', () => {
    // Vacuity floor: a component that rendered nothing would satisfy every
    // "the shapes differ" assertion below by comparing '' to ''.
    render(<LiquidShape from="circle" to="blob" active={false} />)
    expect(svg()).toBeInTheDocument()
    expect(d()).toMatch(/^M[\d.]/)
    expect(d().endsWith('Z')).toBe(true)
    // 16 control points => 16 cubic segments.
    expect(d().match(/C/g)).toHaveLength(16)
  })

  it('differs between the two shape states', () => {
    const { unmount } = render(<LiquidShape from="circle" to="blob" active={false} />)
    const resting = d()
    unmount()
    render(<LiquidShape from="circle" to="blob" active />)
    expect(d()).not.toBe(resting)
  })

  it('gives each named shape its own silhouette', () => {
    const shapes = ['circle', 'squircle', 'blob'] as const
    const seen = new Set<string>()
    for (const s of shapes) {
      const { unmount } = render(<LiquidShape from="circle" to={s} active />)
      seen.add(d())
      unmount()
    }
    // A vocabulary whose members render identically is one shape with three names.
    expect(seen.size).toBe(shapes.length)
  })
})

describe('expr() scales the amplitude', () => {
  // 🪤 An earlier version of this block just asserted "the bold blob and the refined
  // blob are different strings". Falsification killed it: replacing the `expr()` call
  // with a raw constant left that assertion GREEN, because the two renders still
  // differed via the exprHeavy breathe — which is on at 1 and off at 0. The test was
  // measuring the tier gate while claiming to measure the amplitude.
  //
  // So measure the amplitude directly instead. At a given expressiveness the ONLY
  // thing separating `blob` from `circle` is `character × amp` — the breathe term is
  // shape-independent, so it cancels. That distance IS the amplitude, and a raw
  // constant makes it stop responding to the knob.
  function silhouette(shape: 'circle' | 'blob', expressiveness: number, intensity = 1): number[] {
    runtime.expressiveness = expressiveness
    const { unmount } = render(<LiquidShape from={shape} to={shape} active intensity={intensity} />)
    const nums = (d().match(/-?\d+\.\d+/g) ?? []).map(Number)
    unmount()
    return nums
  }

  /** How far the blob departs from the circle at this setting — the amplitude. */
  function amplitudeAt(expressiveness: number, intensity = 1): number {
    const blob = silhouette('blob', expressiveness, intensity)
    const circle = silhouette('circle', expressiveness, intensity)
    expect(blob.length).toBeGreaterThan(50)          // vacuity: real geometry
    expect(blob).toHaveLength(circle.length)         // comparable point-for-point
    return Math.max(...blob.map((v, i) => Math.abs(v - circle[i])))
  }

  it('the amplitude tracks the expressiveness knob', () => {
    const bold = amplitudeAt(1)
    const mid = amplitudeAt(0.5)
    const refined = amplitudeAt(0)
    // Strictly monotonic: a constant amplitude (no expr()) collapses these to equal.
    expect(bold).toBeGreaterThan(mid)
    expect(mid).toBeGreaterThan(refined)
  })

  it('refined is quieter but NOT dead — that is what expr()s floor is for', () => {
    // expr(x, 0.35) keeps 35% at expressiveness 0. If the floor were dropped, the
    // shape vocabulary would vanish entirely on the calm setting.
    const refined = amplitudeAt(0)
    expect(refined).toBeGreaterThan(1)
    expect(refined / amplitudeAt(1)).toBeCloseTo(0.35, 2)
  })

  it('scales with `intensity` too, so a call site can be quieter than the knob', () => {
    expect(amplitudeAt(1, 1)).toBeGreaterThan(amplitudeAt(1, 0.2))
  })
})

describe('the exprHeavy tier is visible in the DOM', () => {
  it('takes the bold tier above the gate', () => {
    runtime.expressiveness = 0.8
    render(<LiquidShape from="circle" to="blob" active />)
    expect(svg()).toHaveAttribute('data-liquid-shape', 'morph')
    expect(svg()).toHaveAttribute('data-liquid-tier', 'bold')
  })

  it('drops to the refined tier below the gate', () => {
    // 0.4 is below exprHeavy's 0.5 default. The refined tier DROPS the idle
    // breathe rather than slowing it, per exprHeavy's contract.
    runtime.expressiveness = 0.4
    render(<LiquidShape from="circle" to="blob" active />)
    expect(svg()).toHaveAttribute('data-liquid-tier', 'refined')
  })
})

describe('it is decoration, and says so', () => {
  it('is aria-hidden, unfocusable and pointer-transparent', () => {
    render(<LiquidShape from="circle" to="blob" active />)
    expect(svg()).toHaveAttribute('aria-hidden', 'true')
    expect(svg()).toHaveAttribute('focusable', 'false')
    expect(svg().style.pointerEvents).toBe('none')
  })

  it('tints from a theme var, never a literal color', () => {
    render(<LiquidShape from="circle" to="blob" active />)
    const stops = [...document.querySelectorAll('radialGradient stop')]
    expect(stops).toHaveLength(2)
    for (const s of stops) expect(s.getAttribute('stop-color')).toBe('var(--color-primary)')
  })

  it('points its fill at its OWN gradient id', () => {
    // Not pedantry: a mismatch between the `url(#…)` and the gradient's id renders
    // a blob with no fill at all — invisible, and green on every other assertion
    // in this file. Two instances must also not collide on one id.
    render(<><LiquidShape from="circle" to="blob" active /><LiquidShape from="circle" to="blob" active /></>)
    const svgs = [...document.querySelectorAll('svg[data-liquid-shape]')]
    expect(svgs).toHaveLength(2)
    const ids = new Set<string>()
    for (const s of svgs) {
      const id = s.querySelector('radialGradient')!.getAttribute('id')!
      expect(s.querySelector('path')!.getAttribute('fill')).toBe(`url(#${id})`)
      // A fragment reference must survive being written into an attribute.
      expect(id).toMatch(/^liquid-[a-zA-Z0-9_-]+$/)
      ids.add(id)
    }
    expect(ids.size).toBe(2)
  })
})
