/**
 * FLUID-MOTION §S3 T3.2 (atom FM-6) — the orchestrated surface entrance UNDER reduced motion.
 *
 * Its own file, with the `matchMedia` stub installed at MODULE SCOPE before any render, for
 * the reason `ui/personality/TerminalStrip.reducedMotion.test.tsx` records: framer-motion
 * caches its `prefers-reduced-motion` probe in a module singleton, so a stub applied after an
 * earlier render in the same file is INERT and the reduced-motion case silently measures the
 * motion-allowed one.
 *
 * Reduced motion here means ABSENCE, not a quicker cascade: no variants, no hidden initial
 * state, no transition — plain DOM. The paired motion-allowed case (`Entrance.test.tsx`)
 * asserts the cascade IS wired, so the two are non-vacuous in both directions; this file also
 * asserts the regions and their controls are all still there, because "renders nothing" would
 * satisfy "no motion" while deleting the surface.
 *
 * What is asserted is the DECISION and the DOM, not a painted frame — jsdom has no compositor.
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

// Reduced motion ON for this entire file, before the components are ever rendered.
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

const { EntranceGroup, EntranceRegion } = await import('./Entrance')

function Surface({ onPoke = () => {} }: { onPoke?: () => void }) {
  return (
    <EntranceGroup className="flex flex-col">
      <EntranceRegion><p>first region</p></EntranceRegion>
      <EntranceRegion>
        <button type="button" onClick={onPoke}>poke</button>
      </EntranceRegion>
      <EntranceRegion><p>third region</p></EntranceRegion>
    </EntranceGroup>
  )
}

const group = () => document.querySelector<HTMLElement>('[data-entrance]')!
const regions = () => [...document.querySelectorAll<HTMLElement>('[data-entrance-region]')]

describe('under prefers-reduced-motion the surface just IS', () => {
  it('the group takes the no-entrance branch', () => {
    render(<Surface />)
    expect(group()).toHaveAttribute('data-entrance', 'none')
    expect(regions()).toHaveLength(3)
    for (const r of regions()) expect(r).toHaveAttribute('data-entrance-region', 'none')
  })

  it('nothing carries an initial hidden state or a transform', () => {
    render(<Surface />)
    // The specific failure this catches: collapsing the cascade to a very small step
    // instead of removing it. A "fast" entrance still writes `opacity: 0` and a
    // `translateY` here on the first commit, so an empty style is the only pass.
    for (const el of [group(), ...regions()]) {
      expect(el.style.opacity, `${el.dataset.entrance ?? el.dataset.entranceRegion} still starts hidden`).toBe('')
      expect(el.style.transform).toBe('')
    }
  })

  it('no node under the group animates at all', () => {
    render(<Surface />)
    // Breadth over the two attributes above: any future animated layer inside a region
    // has to be gated too, and a CSS-keyframe entrance would slip past a style check
    // that only looked at opacity.
    const animated = [...group().querySelectorAll<HTMLElement>('*')]
      .filter((el) => /\banimate-|\banimation:|\btransition:/.test(el.className + (el.getAttribute('style') ?? '')))
    expect(animated.map((el) => el.outerHTML), 'these nodes still animate').toEqual([])
  })

  it('still renders every region and its controls still work', () => {
    // The assertion that stops the three above from passing vacuously — "no motion" must
    // not have been bought by rendering less.
    const onPoke = vi.fn()
    render(<Surface onPoke={onPoke} />)
    expect(screen.getByText('first region')).toBeInTheDocument()
    expect(screen.getByText('third region')).toBeInTheDocument()
    const poke = screen.getByRole('button', { name: 'poke' })
    poke.click()
    expect(onPoke).toHaveBeenCalledTimes(1)
  })

  it('the group keeps the layout classes the surface handed it', () => {
    // The no-entrance branch is a different element from the motion one, so it is the
    // place a className/style could silently go missing and take a page's column with it.
    render(<Surface />)
    expect(group().className).toBe('flex flex-col')
  })
})
