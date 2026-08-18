/**
 * FLUID-MOTION §S2 (atom FM-4) — the WHOLE morph family under reduced motion.
 *
 * `Morph.reducedMotion.test.tsx`, `LiquidShape.reducedMotion.test.tsx` and
 * `Entrance.reducedMotion.test.tsx` each cover one primitive in depth. This file covers the
 * property that only exists across all four: **one off-switch, one mechanism, assertable from
 * the DOM for every member.** Before this atom `Bud` was the exception — it delegated to the
 * root `<MotionConfig reducedMotion="user">`, which neutralizes framer TRANSFORMS but keeps
 * animating `borderRadius` and still installs the projection node `layout` asks for, and it
 * left no attribute to check, so "Bud is fine under reduced motion" was an assertion nobody
 * could test.
 *
 * The stub is at MODULE SCOPE before any render, for the reason the sibling files record:
 * framer-motion caches its `prefers-reduced-motion` probe in a module singleton, so a stub
 * applied after an earlier render in the same file is INERT and the reduced-motion case
 * silently measures the motion-allowed one.
 *
 * Reduced motion here means INSTANT, not fast, and every case carries a positive control —
 * rendering nothing satisfies every "no motion" assertion in this file for free.
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

// Reduced motion ON for this entire file, before any component is ever rendered.
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

const { Bud, Disintegrate, LiquidShape, Morph, MORPH_FAMILY, familySpring } = await import('./index')
const { instant } = await import('../../design/motion')

describe('every family member takes an instant branch, and says so in the DOM', () => {
  it('Morph drops the shared element', () => {
    render(<Morph id="artifact-x"><p>card</p></Morph>)
    expect(document.querySelector('[data-morph]')).toHaveAttribute('data-morph', 'none')
    expect(screen.getByText('card')).toBeInTheDocument()
  })

  it('Bud drops the squish — no transform, no projection node, children intact', () => {
    // The specific failure: keeping the motion.div and trusting the root MotionConfig. A
    // motion component writes its style pipeline on mount, so an ABSENT style attribute is the
    // only pass — and `borderRadius` is not a transform, so `reducedMotion="user"` would have
    // animated the pill→panel corner relax regardless.
    const onPoke = vi.fn()
    render(<Bud from="top" className="p-2"><button type="button" onClick={onPoke}>pick</button></Bud>)
    const el = document.querySelector<HTMLElement>('[data-bud]')!
    expect(el).toHaveAttribute('data-bud', 'instant')
    expect(el.tagName).toBe('DIV')
    expect(el.getAttribute('style')).toBeNull()
    expect(el.style.transform).toBe('')
    expect(el.style.willChange).toBe('')
    // Positive control: the panel is still a panel, and its control still works.
    expect(el.className).toBe('p-2')
    screen.getByRole('button', { name: 'pick' }).click()
    expect(onPoke).toHaveBeenCalledTimes(1)
  })

  it('LiquidShape renders the target silhouette directly', () => {
    render(<LiquidShape from="circle" to="blob" active />)
    const svg = document.querySelector('svg[data-liquid-shape]')!
    expect(svg).toHaveAttribute('data-liquid-shape', 'instant')
    expect(svg).toHaveAttribute('data-liquid-tier', 'reduced')
    // Positive control: real geometry, not an empty svg.
    expect(svg.querySelector('path')!.getAttribute('d')).toMatch(/^M[\d.]/)
  })

  it('Disintegrate resolves with no animation at all', async () => {
    const onDone = vi.fn()
    // Positive control first: inactive under reduced motion still renders the row.
    const { rerender } = render(
      <Disintegrate active={false} onDone={onDone}><p>row</p></Disintegrate>,
    )
    expect(screen.getByText('row')).toBeInTheDocument()
    expect(onDone).not.toHaveBeenCalled()

    rerender(<Disintegrate active onDone={onDone}><p>row</p></Disintegrate>)
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1))
    // No motion tree survives, so there is no tinted wash left to fade either — which is why
    // `familyFade()` is deliberately ungated: under reduced motion it never renders.
    expect(screen.queryByText('row')).not.toBeInTheDocument()
    expect(document.querySelector('.pointer-events-none')).toBeNull()
  })
})

describe('the shared spring collapses, and collapses CLEANLY', () => {
  it('returns `instant` untouched for every base — no spring residue', () => {
    // The spread hazard `motion.ts` documents on `instant`: `{ ...physics.fluid, stiffness: N }`
    // re-introduces a spring from the leftover `stiffness` even after the preset collapsed.
    // One helper now carries this for the whole family, so one assertion covers all of it.
    for (const base of [MORPH_FAMILY.flight, MORPH_FAMILY.state, MORPH_FAMILY.spawn]) {
      const t = familySpring(base) as Record<string, unknown>
      expect(t).toEqual(instant)
      expect(t.type).toBe('tween')
      expect(t.duration).toBe(0)
      expect(t.stiffness).toBeUndefined()
      expect(t.damping).toBeUndefined()
    }
  })

  it('is INSTANT, not merely quick — and that is a different state from expressiveness 0', async () => {
    // The two off-switches, side by side, in the state where the difference is visible. The
    // expressiveness knob cannot reach this: `vocabulary.test.ts` proves that at expressiveness
    // 0 the same call returns a real spring at the floor of the bonus. Here it returns no
    // animation at all, whatever the knob says.
    const { runtime } = await import('../../design/runtime')
    const before = runtime.expressiveness
    try {
      runtime.expressiveness = 1
      expect(familySpring(MORPH_FAMILY.flight)).toEqual(instant)
      runtime.expressiveness = 0
      expect(familySpring(MORPH_FAMILY.flight)).toEqual(instant)
    } finally {
      runtime.expressiveness = before
    }
  })
})
