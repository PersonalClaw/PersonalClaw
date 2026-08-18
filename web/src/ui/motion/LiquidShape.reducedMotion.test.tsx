/**
 * FLUID-MOTION §S2 T2.2 (atom FM-3) — `LiquidShape` UNDER reduced motion.
 *
 * Its own file, with the `matchMedia` stub installed at MODULE SCOPE before any render,
 * for the reason `Entrance.reducedMotion.test.tsx` and `ui/personality/
 * TerminalStrip.reducedMotion.test.tsx` both record: framer-motion caches its
 * `prefers-reduced-motion` probe in a module singleton, so a stub applied after an
 * earlier render in the same file is INERT and the reduced-motion case silently
 * measures the motion-allowed one.
 *
 * Reduced motion here means INSTANT, not fast. Three things must hold, and each is a
 * different failure the others would not catch:
 *
 *   1. The state change lands SYNCHRONOUSLY — no waitFor, no flush, no tick. A quick
 *      spring would pass a `waitFor`-shaped test while still animating.
 *   2. The silhouette is STABLE over time. The bold tier runs a perpetual idle breathe;
 *      if that driver survived reduced motion, `d` would keep changing after the
 *      component settled. Nothing else in this file would notice.
 *   3. The path is actually THERE, with real geometry. "Renders nothing" satisfies both
 *      assertions above for free, so each one carries a positive control.
 */

import { describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'

// Reduced motion ON for this entire file, before the component is ever rendered.
Object.defineProperty(window, 'matchMedia', {
  configurable: true,
  writable: true,
  value: (query: string) => ({
    matches: query.includes('prefers-reduced-motion'),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
    onchange: null,
  }) as unknown as MediaQueryList,
})

const { LiquidShape } = await import('./LiquidShape')

const svg = () => document.querySelector<SVGSVGElement>('svg[data-liquid-shape]')!
const path = () => document.querySelector<SVGPathElement>('svg[data-liquid-shape] path')!
const d = () => path().getAttribute('d') ?? ''

describe('under prefers-reduced-motion the shape just IS', () => {
  it('takes the instant branch, and the surface is still drawn', () => {
    render(<LiquidShape from="circle" to="blob" active />)
    expect(svg()).toHaveAttribute('data-liquid-shape', 'instant')
    expect(svg()).toHaveAttribute('data-liquid-tier', 'reduced')
    // Positive control: the instant branch must still RENDER the blob. A reduced-motion
    // path that drew nothing would pass every "no motion" assertion in this file.
    expect(d()).toMatch(/^M[\d.]/)
    expect(d().match(/C/g)).toHaveLength(16)
  })

  it('changes state synchronously — instant, not merely quick', () => {
    const { rerender } = render(<LiquidShape from="circle" to="blob" active={false} />)
    const resting = d()
    expect(resting).toMatch(/^M[\d.]/)
    rerender(<LiquidShape from="circle" to="blob" active />)
    // No waitFor and no timer advance on purpose: the target geometry must already be
    // on screen. A 20ms spring would need a tick here and would fail.
    expect(d()).not.toBe(resting)
  })

  it('never runs the idle breathe — the silhouette does not drift', async () => {
    vi.useRealTimers()
    render(<LiquidShape from="circle" to="squircle" active />)
    const first = d()
    expect(first).toMatch(/^M[\d.]/)
    // Long enough for many frames of the bold tier's breathe driver to have ticked.
    await new Promise((r) => setTimeout(r, 120))
    expect(d()).toBe(first)
  })
})
