/**
 * The three tiers' new press spring, UNDER prefers-reduced-motion.
 *
 * Its own file with the `matchMedia` stub installed at MODULE SCOPE before the components are
 * imported, for the reason `ui/motion/Entrance.reducedMotion.test.tsx` records: framer-motion
 * caches its reduced-motion probe in a module singleton, so a stub applied after an earlier
 * render in the same file is INERT and this case silently measures the motion-allowed one.
 *
 * 🔑 WHY THIS FILE EXISTS AT ALL: `useReducedMotion()` is REQUIRED here and the global CSS rule
 * in `tokens.css` cannot substitute for it. The press depth is a NUMBER computed in JS
 * (`1 - expr(0.05, 0.4)`) and handed to framer-motion, which writes it as an inline transform —
 * CSS never sees a declaration it could override. That is reduced motion's third layer
 * (system.md §5), and it is the one an author skips.
 *
 * Measured while writing this: motion allowed → `transform: scale(0.9727…)`, reduced →
 * `transform: none`. The paired motion-allowed assertions live in `buttonTierSixStates.test.tsx`,
 * so neither direction is vacuous.
 */

import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'

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

const { QuietButton } = await import('./QuietButton')
const { TileButton } = await import('./TileButton')
const { AddItemButton } = await import('./AddItemButton')

async function pressTransformOf(el: HTMLElement): Promise<string> {
  await act(async () => { fireEvent.pointerDown(el, { button: 0, isPrimary: true }) })
  await act(async () => { await new Promise((r) => setTimeout(r, 120)) })
  return el.getAttribute('style') ?? ''
}

describe('under prefers-reduced-motion the press does not move the button', () => {
  const cases: [string, (onClick: () => void) => void][] = [
    ['QuietButton', (onClick) => { render(<QuietButton onClick={onClick}>Download</QuietButton>) }],
    ['TileButton', (onClick) => { render(<TileButton onClick={onClick} ariaLabel="Download">tile body</TileButton>) }],
    ['AddItemButton', (onClick) => { render(<AddItemButton onClick={onClick}>Download</AddItemButton>) }],
  ]

  for (const [name, mount] of cases) {
    it(`${name} writes no scale`, async () => {
      const onClick = vi.fn()
      mount(onClick)
      const el = screen.getByRole('button', { name: 'Download' })
      const style = await pressTransformOf(el)
      // The specific failure this catches is TIGHTENING the spring instead of removing the scale:
      // a "fast" press still writes a scale here, and only `scale(1)`/`none` passes.
      expect(/scale\(0\./.test(style), `${name} still shrinks on press: ${style}`).toBe(false)

      // …and the stop-vacuity half: the button must still WORK. "No motion" must not have been
      // bought by rendering an inert element.
      el.click()
      expect(onClick, `${name} stopped working under reduced motion`).toHaveBeenCalledTimes(1)
    })
  }

  it('the stub is actually in force (this file is not measuring the allowed case)', () => {
    expect(window.matchMedia('(prefers-reduced-motion: reduce)').matches).toBe(true)
  })
})
