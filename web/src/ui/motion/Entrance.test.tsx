/**
 * FLUID-MOTION §S3 T3.2 (atom FM-6) — the orchestrated surface entrance, MOTION ALLOWED.
 *
 * The reduced-motion half is `Entrance.reducedMotion.test.tsx` and has to be its own file:
 * framer-motion caches its `prefers-reduced-motion` probe in a module singleton, so a stub
 * installed after any render in the same file is inert (the landmine
 * `ui/personality/TerminalStrip.reducedMotion.test.tsx` records). Read together the two are
 * non-vacuous in both directions — this file asserts the cascade IS wired, that one asserts
 * it is gone and the content is not.
 *
 * jsdom has no compositor, so what is asserted here is the DECISION and the DOM, never a
 * painted frame: which branch the group took, that the variant reached the regions at all,
 * and — the property that actually matters to a user — that every region is present and
 * usable on the first commit regardless.
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

import { EntranceGroup, EntranceRegion } from './Entrance'

/** A surface with three regions, the middle one holding a control. `label` exists so a
 *  re-render can change real content without remounting anything. */
function Surface({ label = 'first', onPoke = () => {} }: { label?: string; onPoke?: () => void }) {
  return (
    <EntranceGroup className="flex flex-col">
      <EntranceRegion><p>{label} region</p></EntranceRegion>
      <EntranceRegion>
        <button type="button" onClick={onPoke}>poke</button>
      </EntranceRegion>
      <EntranceRegion><p>third region</p></EntranceRegion>
    </EntranceGroup>
  )
}

const group = () => document.querySelector<HTMLElement>('[data-entrance]')!
const regions = () => [...document.querySelectorAll<HTMLElement>('[data-entrance-region]')]

describe('EntranceGroup / EntranceRegion — the cascade is wired', () => {
  it('the group declares the staggered branch and every region joins it', () => {
    render(<Surface />)
    expect(group()).toHaveAttribute('data-entrance', 'staggered')
    expect(regions()).toHaveLength(3)
    for (const r of regions()) expect(r).toHaveAttribute('data-entrance-region', 'staggered')
  })

  it('the variant actually reaches the regions — they start hidden and land visible', async () => {
    // This is the assertion that separates "wired" from "a div with a data attribute":
    // the regions carry `variants` only, with no `animate` of their own, so an inline
    // opacity can only be there because the GROUP's variant label propagated down —
    // which is the same propagation `staggerChildren` rides. If the group stopped
    // driving them, `initial` would never be applied and this reads 1 from the start.
    render(<Surface />)
    expect(regions().map((r) => r.style.opacity)).toEqual(['0', '0', '0'])
    // ...and the cascade RESOLVES. A stagger that never finished would leave content
    // permanently invisible, which is the worst version of this feature.
    await waitFor(() => expect(regions().every((r) => r.style.opacity === '1')).toBe(true))
  })
})

describe('an entrance never gates content', () => {
  it('every region is in the document on the FIRST commit, before anything animates', () => {
    render(<Surface />)
    // No waitFor, no timer advance: synchronous after the initial commit, exactly as
    // FM-5 requires of a route transition one level up — the page changes whether the
    // animation runs, fails, or is not supported.
    expect(screen.getByText('first region')).toBeInTheDocument()
    expect(screen.getByText('third region')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'poke' })).toBeInTheDocument()
  })

  it('a control inside a mid-cascade region is already usable', () => {
    const onPoke = vi.fn()
    render(<Surface onPoke={onPoke} />)
    // Not `userEvent`: a real pointer would be the wrong instrument here. The claim is
    // that the region is live during its own entrance, so the click is dispatched at
    // the moment the entrance is still at opacity 0 (asserted above).
    screen.getByRole('button', { name: 'poke' }).click()
    expect(onPoke).toHaveBeenCalledTimes(1)
  })
})

describe('the replay rule — mount plays it, a re-render never does', () => {
  it('a data change re-renders the regions in place instead of remounting them', async () => {
    const { rerender } = render(<Surface label="first" />)
    await waitFor(() => expect(regions().every((r) => r.style.opacity === '1')).toBe(true))
    const before = { g: group(), r: regions() }

    // The shape a WS push / refetch takes on a live surface: same tree, new content.
    rerender(<Surface label="second" />)
    expect(screen.getByText('second region')).toBeInTheDocument()

    // Node identity is the whole proof. Framer plays `initial → animate` on MOUNT, so
    // the same nodes cannot have replayed; a group placed under a data-dependent branch
    // (or keyed on data) would fail here with fresh nodes and a fresh cascade.
    expect(group()).toBe(before.g)
    expect(regions()).toEqual(before.r)
    // And nothing snapped back to hidden, which is what the flicker would look like.
    expect(regions().map((r) => r.style.opacity)).toEqual(['1', '1', '1'])
  })
})

describe('a region without a group', () => {
  it('renders plain and visible — a forgotten group costs the entrance, never the content', () => {
    // The fail-safe direction of the context default. A region that assumed it was
    // inside a group would render `variants` with nothing to propagate the `animate`
    // label, and sit at opacity 0 forever.
    render(<EntranceRegion><p>orphan</p></EntranceRegion>)
    const r = regions()[0]
    expect(r).toHaveAttribute('data-entrance-region', 'none')
    expect(r.style.opacity).toBe('')
    expect(screen.getByText('orphan')).toBeInTheDocument()
  })
})
