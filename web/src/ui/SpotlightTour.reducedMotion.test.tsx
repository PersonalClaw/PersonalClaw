/**
 * ONBOARDING-UX T5.1 — the product tour UNDER reduced motion.
 *
 * Its own file, with the `matchMedia` stub installed at MODULE SCOPE before any render,
 * for the reason `ui/personality/TerminalStrip.reducedMotion.test.tsx` records:
 * framer-motion caches its `prefers-reduced-motion` probe in a module singleton, so a stub
 * applied after an earlier render in the same file is INERT and the reduced-motion case
 * silently measures the motion-allowed one.
 *
 * Reduced motion here means ABSENCE, not a slower animation. The paired motion-allowed case
 * lives in `SpotlightTour.test.tsx`, which asserts the halo IS drawn — read together the two
 * are non-vacuous in both directions, because this file also asserts the static ring, the
 * card and the copy are all still there. A tour that rendered nothing would satisfy "no
 * halo" while deleting the feature.
 */

import { describe, expect, it, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { Compass } from 'lucide-react'
import { SpotlightTour, type SpotlightStep } from './SpotlightTour'

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

const STEPS: SpotlightStep[] = [
  { id: 'one', anchor: 'one', icon: Compass, title: 'The first thing', body: 'What the first thing is for.' },
]

// jsdom has no layout, so without a box the overlay takes its unanchored path and draws no
// ring at all — which would make every assertion below pass for the wrong reason.
const REAL_RECT = Element.prototype.getBoundingClientRect
beforeEach(() => {
  Element.prototype.getBoundingClientRect = function (): DOMRect {
    return { x: 40, y: 80, top: 80, left: 40, width: 200, height: 120, right: 240, bottom: 200, toJSON: () => ({}) } as DOMRect
  }
})
afterEach(() => { Element.prototype.getBoundingClientRect = REAL_RECT })

function mount() {
  return render(
    <>
      <div data-tour="one">anchor one</div>
      <SpotlightTour steps={STEPS} index={0} label="Test tour" onIndex={() => {}} onExit={() => {}} />
    </>,
  )
}

describe('under prefers-reduced-motion the spotlight is a still frame', () => {
  it('draws NO pulsing halo', async () => {
    mount()
    await screen.findByRole('dialog')
    // Absence, not a slowed loop: the halo is the tour's only repeating animation, so no
    // halo node means there is nothing left that can move.
    await waitFor(() => expect(document.querySelector('[data-tour-halo]')).toBeNull())
  })

  it('still spotlights the anchor — this is a frozen frame, not a blank overlay', async () => {
    // The assertion that stops the one above from passing vacuously.
    mount()
    const d = await screen.findByRole('dialog')
    expect(d).toHaveAttribute('data-tour-anchored', 'true')
    await waitFor(() => {
      const rings = [...document.querySelectorAll<HTMLElement>('.fixed.rounded-lg')]
        .filter((el) => (el.getAttribute('style') ?? '').includes('--color-primary'))
      expect(rings.length, 'the static outline ring must still be drawn').toBe(1)
    })
    // Four dim bands, so the spotlight still READS as a spotlight.
    expect(document.querySelectorAll('.bg-canvas\\/70').length).toBe(4)
  })

  it('still teaches — the card, its copy and its controls are untouched', async () => {
    mount()
    await screen.findByRole('dialog')
    expect(screen.getByText('The first thing')).toBeInTheDocument()
    expect(screen.getByText('What the first thing is for.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'End the tour' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Done/ })).toBeInTheDocument()
  })

  it('no node in the overlay carries an animation class', async () => {
    // Breadth over the halo selector: any future animated layer has to be gated too.
    mount()
    const d = await screen.findByRole('dialog')
    const overlay = d.parentElement!
    const animated = [...overlay.querySelectorAll<HTMLElement>('*')]
      .filter((el) => /\banimate-|\banimation:/.test(el.className + (el.getAttribute('style') ?? '')))
    expect(animated.map((el) => el.className), 'these nodes still animate').toEqual([])
  })
})
