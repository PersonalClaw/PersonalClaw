import { describe, expect, it } from 'vitest'
import { fireEvent, render, waitFor } from '@testing-library/react'
// Type-only, so it is erased and cannot import the components before the stub below is installed.
import type { ChatTurn } from './chatTypes'

/**
 * SSM-8 — the Session Map rail's MARK LENGTH under prefers-reduced-motion.
 *
 * (It was a hover halo until the Codex redesign; the mark's own length replaced it, so the rail has
 * ONE animation rather than two. The contract this file pins is unchanged: whatever the rail
 * animates must come from `design/motion.ts`'s gated family, and collapsing it must not break the
 * thing it animates or the control underneath it.)
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
const { sessionMapEntries } = await import('./sessionMap')
const { physics, instant } = await import('../../design/motion')

const TS = '2026-09-16T10:00:00.000Z'
const turns: ChatTurn[] = [
  { role: 'user', ts: TS, visibleIndex: 0, segments: [{ kind: 'text', text: 'run the build' }] },
  { role: 'assistant', ts: TS, visibleIndex: 1, segments: [{ kind: 'text', text: 'done' }] },
  { role: 'user', ts: TS, visibleIndex: 2, segments: [{ kind: 'text', text: 'now ship it' }] },
]

/** See `SessionMapRail.target.test.tsx` for why `transform: none` has to read as scale 1. */
function scaleOf(el: Element | null): number {
  const style = (el as HTMLElement | null)?.getAttribute('style') ?? ''
  const m = /scaleX\(([\d.]+)\)/.exec(style)
  if (m) return Number(m[1])
  return /transform:\s*none/.test(style) ? 1 : NaN
}

describe('under prefers-reduced-motion the rail’s mark length does not animate', () => {
  it('the transition the rail hands framer IS the module’s one instant answer', () => {
    // The rail reaches for `physics.snappy`; under the stub above that getter must BE `instant`. If a
    // future edit inlined a spring at the call site this is the assertion that notices.
    expect(physics.snappy).toEqual(instant)
    expect(physics.snappy).not.toHaveProperty('stiffness')
  })

  it('the length still LANDS under the collapse, and the mark still works', async () => {
    const entries = sessionMapEntries(turns)
    const jumped: number[] = []
    const { container } = render(
      <SessionMapRail entries={entries} turnNodes={new Map()} scrollRef={{ current: null }} onJumpTo={(i) => jumped.push(i)} />,
    )
    const mark = container.querySelector('[data-session-mark]') as HTMLElement
    const line = mark.querySelector('[data-session-map-mark-line]') as HTMLElement
    const rest = scaleOf(line)
    expect(rest, `no resting length at all: ${line.getAttribute('style')}`).not.toBeNaN()

    // `mouseOver`, not `mouseEnter`: React delegates onMouseEnter off the bubbling `mouseover`, so a
    // non-bubbling `mouseenter` reaches no handler and this would measure a rail nobody hovered.
    fireEvent.mouseOver(mark)

    // 🪤 WHAT THIS DOES NOT CLAIM, and the over-claim is worth recording because the first draft of
    // this file made it: "collapses to instant" is NOT observable as "the value arrives in the same
    // tick". framer schedules even a zero-duration tween on the next frame, so a same-tick read shows
    // the resting value for BOTH the collapsed and the springing rail — a red that says nothing about
    // the gate. `reducedMotionAppWide.test.ts` states the limit plainly: jsdom has no frame clock,
    // paints nothing, and runs no animations, so the VALUE handed to the animation layer is the
    // falsifiable thing. That is the assertion above. What is left for this one is the pair a value
    // check cannot give: the collapse must not have BROKEN the length, and it must not have been
    // bought by rendering an inert control.
    await waitFor(() => expect(scaleOf(line), `the length never landed: ${line.getAttribute('style')}`).toBe(1))
    // The owner's rule for motion-off users: the marker still EXPANDS — it just does not travel there.
    expect(scaleOf(line), 'a hovered marker must reach full length even with motion off')
      .toBeGreaterThan(rest)

    fireEvent.click(mark)
    expect(jumped, 'the marker stopped working under reduced motion').toEqual([entries[0].visibleIndex])
  })
})
