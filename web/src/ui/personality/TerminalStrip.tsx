/**
 * TERMINAL STRIP — the `terminal-scanlines` shell element (PERSONALITY-THEMES §S2).
 *
 * A CRT raster laid over the whole shell for the retro-terminal personality: a fixed
 * lattice of hairlines, plus one soft band that travels down the viewport. It is
 * atmosphere and nothing else, so it follows `DotGlow`'s discipline for decorative
 * chrome exactly, and for the same three reasons:
 *
 * 1. **`aria-hidden` + `pointer-events-none`.** A decoration that assistive tech can
 *    read is noise in the reading order, and one the pointer can hit is a dead zone
 *    over real controls. Both are asserted by rendering (`shellElements.test.tsx`),
 *    for every registry entry, not just this one.
 * 2. **Static under reduced motion.** The raster and the beam are separate layers
 *    precisely so the motion can be dropped without dropping the look: when the
 *    query matches, the beam is NOT RENDERED and the lattice alone remains — the
 *    "static frame" the atom asks for. Absence, not a frozen animation, because a
 *    paused CSS animation still costs a compositor layer and still reads as a stuck
 *    element rather than a deliberate one.
 * 3. **Scheme ink, not a hardcoded phosphor.** Both layers paint through
 *    `--color-on-surface` / `--color-primary` (see `.crt-raster` / `.crt-beam` in
 *    `design/tokens.css`), so the raster tracks whatever scheme the personality
 *    names instead of pinning a second, unthemed green into the shell.
 *
 * Reduced motion is read through `design/motion.ts`'s `prefersReducedMotion()`,
 * which is documented as call-time and uncached. That matters here: framer-motion
 * caches its own probe in a module singleton, so a component built on
 * `useReducedMotion` cannot be proven static by a test that stubs `matchMedia`
 * after any earlier render in the same file. This reads the query live and
 * subscribes to changes, so turning Reduce Motion on mid-session stops the beam
 * without a reload — and both directions are provable
 * (`TerminalStrip.test.tsx` + `TerminalStrip.reducedMotion.test.tsx`).
 *
 * The strip rides `--z-overlay` (the CD-05 z-layer scale in `design/tokens.css`):
 * one rung above the content ceiling `--z-content` and below `--z-modal`, so the
 * raster sits over every page surface and under every surface a user is being
 * asked to act on — a dialog and a toast stay crisp.
 */

import { useEffect, useState } from 'react'
import { prefersReducedMotion } from '../../design/motion'

const REDUCE_QUERY = '(prefers-reduced-motion: reduce)'

export function TerminalStrip() {
  const [reduce, setReduce] = useState(prefersReducedMotion)
  // Live subscription so an OS-level Reduce Motion change takes effect immediately.
  // Guarded the way `prefersReducedMotion` is: jsdom (and any non-browser host) has
  // no `matchMedia` at all, and a decorative layer must not be the thing that throws.
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia(REDUCE_QUERY)
    const onChange = () => setReduce(mq.matches)
    mq.addEventListener('change', onChange)
    onChange()
    return () => mq.removeEventListener('change', onChange)
  }, [])

  return (
    // `data-shell-element` names which registry entry mounted — the same idea as
    // `data-personality` on <html>: it makes the active decoration legible to CSS,
    // to a debugger, and to the generic shell-element contract test, without any
    // surface a user or a screen reader can reach.
    <div
      aria-hidden
      data-shell-element="terminal-scanlines"
      className="crt-raster pointer-events-none fixed inset-0 z-[var(--z-overlay)]"
    >
      {!reduce && <div className="crt-beam absolute inset-x-0 top-0 h-[22vh]" />}
    </div>
  )
}
