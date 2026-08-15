import { describe, expect, it } from 'vitest'
import { act, render } from '@testing-library/react'
import { ProgressRing } from './ProgressRing'

// ── The behavioural proof: does the arc TWEEN, or JUMP? ─────────────────────────
//
// The source assertions in ProgressRing.test.tsx pin the MECHANISM (motion.circle, the spring,
// initial={false}). This file measures the OUTCOME, because a mechanism can be present and inert —
// e.g. a transition on a prop framer-motion isn't animating, which reads as correct code and
// behaves exactly like the copy that jumped.
//
// The distinguishing observable: after `pct` changes, an animated arc passes through intermediate
// `stroke-dashoffset` values before settling. A plain <circle> emits exactly ONE value — the new
// one — because there is nothing between the two states. So the test advances time in small steps
// and counts DISTINCT offsets. Reverting the primitive to the non-animating shape collapses that
// count to 1 and reds this.
//
// Why not measure this in the browser: it needs a `pct` that CHANGES while mounted, and the seeded
// loops hold still (their cycle counts only move when a loop actually runs). Re-rendering the real
// component with a new prop is the same code path the list takes when a cycle completes.

const C = 2 * Math.PI * 11.5   // circumference at the default size

function offsetOf(container: HTMLElement): number {
  const arc = container.querySelectorAll('circle')[1] as SVGCircleElement
  const attr = arc.getAttribute('stroke-dashoffset')
  return attr == null ? NaN : Number(attr)
}

describe('the arc tweens between values rather than jumping', () => {
  it('passes through intermediate offsets after pct changes', async () => {
    const { container, rerender } = render(<ProgressRing label="Cycle progress" pct={0} tone={"var(--color-primary)"} />)

    // Mount is silent (initial={false}) — the first paint is already the true value, not zero
    // swept up from nothing.
    expect(offsetOf(container as HTMLElement)).toBeCloseTo(C, 1)

    const seen = new Set<number>()
    await act(async () => {
      rerender(<ProgressRing label="Cycle progress" pct={1} tone={"var(--color-primary)"} />)
    })
    for (let i = 0; i < 40; i++) {
      await act(async () => { await new Promise((r) => setTimeout(r, 16)) })
      const v = offsetOf(container as HTMLElement)
      if (!Number.isNaN(v)) seen.add(Number(v.toFixed(2)))
    }

    // A spring from C → 0 yields many samples; a direct write yields only the endpoint.
    expect(
      seen.size,
      `expected the arc to pass through intermediate offsets, saw: ${[...seen].join(', ')}`,
    ).toBeGreaterThan(2)

    // And it must actually ARRIVE — an animation that never settles is its own bug.
    const final = offsetOf(container as HTMLElement)
    expect(final).toBeLessThan(C * 0.5)
  })

  it('a full ring is offset 0 and an empty ring is the whole circumference', () => {
    // The two endpoints callers rely on: a completed loop reads full, a fresh one reads empty.
    const full = render(<ProgressRing label="Cycle progress" pct={1} tone={"red"} />)
    expect(offsetOf(full.container as HTMLElement)).toBeCloseTo(0, 1)
    const empty = render(<ProgressRing label="Cycle progress" pct={0} tone={"red"} />)
    expect(offsetOf(empty.container as HTMLElement)).toBeCloseTo(C, 1)
  })
})
