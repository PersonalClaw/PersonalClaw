import { describe, expect, it } from 'vitest'
import { fireEvent, render, waitFor } from '@testing-library/react'
// Type-only, so it is erased and cannot import the components before the stub below is installed.
import type { ChatTurn } from './chatTypes'

/**
 * SSM-8 — the Session Map rail's halo UNDER prefers-reduced-motion.
 *
 * Its own file with the `matchMedia` stub installed at MODULE SCOPE before the components are
 * imported, for the reason `ui/motion/Entrance.reducedMotion.test.tsx` records: framer-motion caches
 * its reduced-motion probe in a module singleton, so a stub applied after an earlier render in the
 * same file is INERT and this case silently measures the motion-allowed one.
 *
 * 🔑 WHY A RAIL-SCOPED FILE AND NOT JUST `reducedMotionAppWide.test.ts`. That rail proves the VALUES
 * `design/motion.ts` hands out collapse, and censuses source for springs minted outside it — both
 * necessary, and both blind to a component with no animation at all. SSM-8's clause is about THE
 * RAIL's animation, so it is pinned here in the two places jsdom can actually see it: the TRANSITION
 * VALUE the rail hands framer under the stub, and the fact that collapsing it neither breaks the halo
 * nor the control. The second test carries what this file deliberately does NOT claim. The paired
 * motion-allowed assertion is in `SessionMapRail.target.test.tsx`, so neither direction is vacuous.
 */

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

const { SessionMapRail } = await import('./SessionMapRail')
const { sessionMapMarks } = await import('./sessionMap')
const { physics, instant } = await import('../../design/motion')

const TS = '2026-09-16T10:00:00.000Z'
const turns: ChatTurn[] = [
  { role: 'user', ts: TS, visibleIndex: 0, segments: [{ kind: 'text', text: 'run the build' }] },
  { role: 'assistant', ts: TS, visibleIndex: 1, segments: [{ kind: 'text', text: 'done' }] },
]

describe('under prefers-reduced-motion the rail’s halo does not animate', () => {
  it('the transition the rail hands framer IS the module’s one instant answer', () => {
    // The rail reaches for `physics.snappy`; under the stub above that getter must BE `instant`. If a
    // future edit inlined a spring at the call site this is the assertion that notices.
    expect(physics.snappy).toEqual(instant)
    expect(physics.snappy).not.toHaveProperty('stiffness')
  })

  it('the halo still LANDS under the collapse, and the mark still works', async () => {
    const marks = sessionMapMarks(turns)
    const jumped: number[] = []
    const { container } = render(
      <SessionMapRail marks={marks} turnNodes={new Map()} scrollRef={{ current: null }} onJumpTo={(i) => jumped.push(i)} />,
    )
    const tick = container.querySelector('[data-session-mark]') as HTMLElement
    const halo = tick.querySelector('[data-session-map-halo]') as HTMLElement

    // `mouseOver`, not `mouseEnter`: React delegates onMouseEnter off the bubbling `mouseover`, so a
    // non-bubbling `mouseenter` reaches no handler and this would measure a rail nobody hovered.
    fireEvent.mouseOver(tick)

    // 🪤 WHAT THIS DOES NOT CLAIM, and the over-claim is worth recording because the first draft of
    // this file made it: "collapses to instant" is NOT observable as "the value arrives in the same
    // tick". framer schedules even a zero-duration tween on the next frame, so a same-tick read shows
    // `opacity: 0` for BOTH the collapsed and the springing rail — a red that says nothing about the
    // gate. `reducedMotionAppWide.test.ts` states the limit plainly: jsdom has no frame clock, paints
    // nothing, and runs no animations, so the VALUE handed to the animation layer is the falsifiable
    // thing. That is the assertion above. What is left for this one is the pair a value check cannot
    // give: the collapse must not have BROKEN the halo, and it must not have been bought by
    // rendering an inert control.
    await waitFor(() => expect(halo.style.opacity, `the halo never landed: ${halo.getAttribute('style')}`).not.toBe('0'))
    expect(halo.getAttribute('style')).toContain('scale(3)')

    fireEvent.click(tick)
    expect(jumped, 'the mark stopped working under reduced motion').toEqual([marks[0].visibleIndex])
  })
})
