/**
 * PERSONALITY-THEMES §S2 (PT-3) — TerminalStrip UNDER reduced motion.
 *
 * Its own file, with the `matchMedia` stub installed at MODULE SCOPE before any
 * render in this file, on purpose. A sibling atom in this plan lost time to exactly
 * this: framer-motion caches its `prefers-reduced-motion` probe in a module
 * singleton, so a stub applied after an earlier render in the same file is INERT and
 * the reduced-motion case silently measures the motion-allowed one. Splitting the
 * file removes the ordering hazard entirely rather than relying on a comment telling
 * the next author to be careful.
 *
 * The paired motion-allowed case lives in `TerminalStrip.test.tsx`, which asserts
 * the beam IS rendered. Read together they are non-vacuous in both directions: this
 * file's absence claim cannot pass on a component that renders nothing, because it
 * also asserts the static raster is still there.
 */

import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { TerminalStrip } from './TerminalStrip'

// Reduced motion ON for this entire file, before the component ever renders.
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

const root = (c: HTMLElement) => c.querySelector<HTMLElement>('[data-shell-element="terminal-scanlines"]')!

describe('under prefers-reduced-motion the raster is a static frame', () => {
  it('renders NO travelling beam', () => {
    // Absence, not a paused animation. `.crt-beam` is the only animated node in the
    // component (its `animation` lives on that class in design/tokens.css), so no
    // `.crt-beam` node means there is nothing left that can move.
    const { container } = render(<TerminalStrip />)
    expect(container.querySelector('.crt-beam'), 'the beam must not render').toBeNull()
  })

  it('still renders the static raster — this is a frozen frame, not a blank layer', () => {
    // The assertion that stops the test above from passing vacuously. A reduced-motion
    // user keeps the look and loses only the movement; rendering nothing would silently
    // satisfy "no beam" while deleting the whole shell element.
    const { container } = render(<TerminalStrip />)
    expect(root(container), 'the shell element must still mount').not.toBeNull()
    expect(root(container).className).toContain('crt-raster')
  })

  it('keeps the decorative contract', () => {
    const { container } = render(<TerminalStrip />)
    expect(root(container).getAttribute('aria-hidden')).toBe('true')
    expect(root(container).className).toContain('pointer-events-none')
  })

  it('no node in the subtree carries an animation class', () => {
    // Breadth over the specific selector above: any future animated layer added to
    // the strip has to be gated too, and this reddens if one is not.
    const { container } = render(<TerminalStrip />)
    const animated = [...container.querySelectorAll<HTMLElement>('*')]
      .filter((el) => /\banimate-|\bcrt-beam\b|\banimation:/.test(el.className + (el.getAttribute('style') ?? '')))
    expect(animated.map((el) => el.className), 'these nodes still animate').toEqual([])
  })
})
