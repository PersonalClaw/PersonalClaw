/**
 * PERSONALITY-THEMES §S2 (PT-3) — TerminalStrip WITH motion allowed.
 *
 * The paired half of `TerminalStrip.reducedMotion.test.tsx`. Neither file is worth
 * anything alone: "the beam is absent under reduced motion" passes trivially on a
 * component that never renders a beam, so the absence claim only means something
 * next to a case where the beam is PRESENT. This file is that case, and it also
 * proves the gate is live rather than a constant by flipping the query mid-mount.
 *
 * `matchMedia` is stubbed at MODULE SCOPE, not per test. jsdom does not implement it
 * at all, so an unstubbed run would exercise the `typeof matchMedia !== 'function'`
 * fallback and prove nothing about the query. Module scope also keeps the two files
 * honest about a hazard a sibling atom hit: framer-motion caches its reduced-motion
 * probe in a module singleton, so a stub installed after an earlier render in the
 * same file is inert. The component deliberately uses `design/motion.ts`'s
 * call-time `prefersReducedMotion()` instead, which is why the mid-session flip
 * below can work at all — but the files stay split so that a future switch to
 * `useReducedMotion` reddens here instead of silently passing.
 */

import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { TerminalStrip } from './TerminalStrip'

const ORIGINAL_MATCH_MEDIA = window.matchMedia

/** Listeners registered against the reduced-motion query, so a test can flip the
 *  preference the way the OS does — a `change` event, not a re-render. */
const listeners = new Set<() => void>()
let reduceMatches = false

function defineMatchMedia() {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      get matches() {
        return query.includes('prefers-reduced-motion') ? reduceMatches : false
      },
      media: query,
      addEventListener: (_: string, fn: () => void) => { listeners.add(fn) },
      removeEventListener: (_: string, fn: () => void) => { listeners.delete(fn) },
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
      onchange: null,
    }) as unknown as MediaQueryList,
  })
}

// Motion ALLOWED for this whole file.
defineMatchMedia()
reduceMatches = false

beforeEach(() => {
  listeners.clear()
  reduceMatches = false
})

afterAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA,
  })
})

const root = (c: HTMLElement) => c.querySelector<HTMLElement>('[data-shell-element="terminal-scanlines"]')!
const beam = (c: HTMLElement) => c.querySelector<HTMLElement>('.crt-beam')

describe('with motion allowed the raster animates', () => {
  it('renders the travelling beam alongside the static raster', () => {
    const { container } = render(<TerminalStrip />)
    // The static frame is always there — it is what reduced motion keeps.
    expect(root(container).className).toContain('crt-raster')
    // The moving layer is present ONLY here. `.crt-beam` carries the `animation`
    // (design/tokens.css); the class IS the motion, so its presence is the outcome.
    expect(beam(container), 'the beam must render when motion is allowed').not.toBeNull()
  })

  it('is invisible to assistive tech and to the pointer', () => {
    // Restated at the component level even though `shellElements.test.tsx` sweeps
    // every registry entry: this is the file someone edits when changing the strip.
    const { container } = render(<TerminalStrip />)
    expect(root(container).getAttribute('aria-hidden')).toBe('true')
    expect(root(container).className).toContain('pointer-events-none')
  })

  it('sits above page content and below the surfaces a user must act on', () => {
    // Page content tops out at z-50; Modal is z-[60], the update overlay z-[80],
    // the Toaster z-[200]. A decoration painted over a dialog is a defect.
    const { container } = render(<TerminalStrip />)
    expect(root(container).className).toContain('z-[55]')
  })

  it('goes static the moment the OS preference flips, with no reload', () => {
    // The gate is READ LIVE, not captured at import. Without this the two files
    // could both be passing on a hardcoded constant that happens to differ.
    const { container } = render(<TerminalStrip />)
    expect(beam(container)).not.toBeNull()

    reduceMatches = true
    act(() => { for (const fn of listeners) fn() })
    expect(beam(container), 'flipping to reduce must drop the beam').toBeNull()
    expect(root(container).className, 'the static raster must survive').toContain('crt-raster')

    reduceMatches = false
    act(() => { for (const fn of listeners) fn() })
    expect(beam(container), 'flipping back must restore the beam').not.toBeNull()
  })

  it('unsubscribes on unmount', () => {
    // A shell-level overlay mounts and unmounts on every personality switch; a
    // listener left behind sets state on an unmounted tree.
    const { unmount } = render(<TerminalStrip />)
    expect(listeners.size).toBe(1)
    unmount()
    expect(listeners.size).toBe(0)
  })

  it('survives a host with no matchMedia at all', () => {
    // The real jsdom/SSR case. A decorative layer must never be the thing that throws.
    Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: undefined })
    try {
      const { container } = render(<TerminalStrip />)
      expect(root(container)).not.toBeNull()
      expect(beam(container), 'no query to consult → motion allowed').not.toBeNull()
    } finally {
      defineMatchMedia()
    }
  })
})

// Guard against the stub itself rotting into a no-op: if `matchMedia` stopped being
// consulted, every assertion above would still pass on the fallback path.
it('the stub is actually the thing being consulted', () => {
  const spy = vi.spyOn(window, 'matchMedia')
  render(<TerminalStrip />)
  expect(spy).toHaveBeenCalledWith('(prefers-reduced-motion: reduce)')
  spy.mockRestore()
})
