/**
 * FLUID-MOTION §S2 T2.1 (atom FM-2) — `Morph` UNDER reduced motion.
 *
 * Its own file, with the `matchMedia` stub installed at MODULE SCOPE before any render, for
 * the reason `Entrance.reducedMotion.test.tsx` and `LiquidShape.reducedMotion.test.tsx` both
 * record: framer-motion caches its `prefers-reduced-motion` probe in a module singleton, so a
 * stub applied after an earlier render in the same file is INERT and the reduced-motion case
 * silently measures the motion-allowed one.
 *
 * Reduced motion here means an INSTANT SWAP, not a quick one. Three separate failures, none of
 * which the others would catch:
 *
 *   1. The shared element is still declared, on a very short transition. The card would still
 *      fly, just faster — the exact thing the plan's contract forbids. Caught by the branch: the
 *      reduced-motion end is a plain `<div>` with no `layoutId` to pair with, so there is
 *      nothing for Framer to project and no animation to shorten.
 *   2. `familySpring()` is spread over: `{ ...physics.fluid, stiffness: N }` re-introduces a
 *      spring from the leftover `stiffness` even after the preset collapsed to `instant` —
 *      the hazard `motion.ts` documents on `instant` itself. Caught by asserting the returned
 *      object has NO spring residue at all.
 *   3. The gate is bought by rendering less. Every case below therefore carries a positive
 *      control: the children, their className, and a working button.
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

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

const { Morph } = await import('./Morph')
const { MORPH_FAMILY, familySpring } = await import('./vocabulary')
const { instant } = await import('../../design/motion')

const root = () => document.querySelector<HTMLElement>('[data-morph]')!

describe('under prefers-reduced-motion the card just IS the page', () => {
  it('takes the no-morph branch — no shared element at either end', () => {
    // Both ends render the same branch, which is what makes the swap instant: there is no
    // `layoutId` pair left for Framer to fly between, in either direction.
    render(<><Morph id="artifact-x" className="grid"><p>card</p></Morph></>)
    expect(root()).toHaveAttribute('data-morph', 'none')
    expect(root().tagName).toBe('DIV')
    expect(screen.getByText('card')).toBeInTheDocument()
  })

  it('writes no transform, no opacity and no will-change on the first commit', () => {
    // The specific failure this catches: keeping the motion component and handing it a
    // near-zero transition instead. A motion.div installs a projection node and its style
    // pipeline on mount, so an empty inline style is the only pass. `will-change` is included
    // because it is the one Framer writes even when the animation itself is imperceptible —
    // and it promotes a layer, which is the cost this branch exists to avoid.
    render(<Morph id="artifact-x"><p>card</p></Morph>)
    expect(root().style.transform).toBe('')
    expect(root().style.opacity).toBe('')
    expect(root().style.willChange).toBe('')
    expect(root().getAttribute('style')).toBeNull()
  })

  it('the transition has NO spring residue, even though the preset was spread', () => {
    // familySpring() spreads `physics.fluid` and overrides `stiffness` on the motion path.
    // Under reduced motion the preset is already `instant`, and a spread would let Framer
    // infer a spring right back from that leftover `stiffness`.
    const t = familySpring(MORPH_FAMILY.flight) as Record<string, unknown>
    expect(t).toEqual(instant)
    expect(t.type).toBe('tween')
    expect(t.duration).toBe(0)
    expect(t.stiffness).toBeUndefined()
    expect(t.damping).toBeUndefined()
  })

  it('still renders the card and its control still works', () => {
    // The assertion that stops the three above from passing vacuously — "no motion" must not
    // have been bought by dropping the children or their click target.
    const onPoke = vi.fn()
    render(
      <Morph id="artifact-x" className="grid" style={{ minWidth: 0 }}>
        <button type="button" onClick={onPoke}>open</button>
      </Morph>,
    )
    expect(root().className).toBe('grid')
    expect(root().style.minWidth).toBe('0px')
    screen.getByRole('button', { name: 'open' }).click()
    expect(onPoke).toHaveBeenCalledTimes(1)
  })
})
