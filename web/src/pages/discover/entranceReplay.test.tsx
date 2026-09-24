import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

import { DiscoverPage } from './DiscoverPage'

// ── FLUID-MOTION §S3 T3.2 (atom FM-6) — the replay rule on a REAL surface ───────────────
//
// `ui/motion/Entrance.test.tsx` proves a re-render does not replay the cascade for the
// primitive. That is necessary and not sufficient: the defect this atom actually has to avoid
// is a group placed under a data-dependent branch, which the primitive can never notice
// because it is the SURFACE that chooses the placement. On a data surface the regions ARE the
// data (Discover's areas), so the group sits on the loaded column — and the only thing
// stopping every dismiss from re-running the whole cascade is that `useQuery` HOLDS the
// last value on a same-key revalidation instead of dropping back to `undefined`.
//
// That is a property of a collaborator, not of this file, so it is asserted here against the
// real hook: dismiss a tip, let the refetch land, and require the group and each surviving
// area band to be the SAME DOM nodes. Fresh nodes would mean a remount, and a remount is a
// visible flicker of the entire page on every dismiss.
//
// Discover is the lightest of this atom's three surfaces to drive (one GET, one POST), which
// is why the placement rail lives on it; the dashboard's and the inbox's placement — above
// every branch — is the easy case the primitive's own test already covers.

const discover = vi.fn()
const dismissDiscoverTip = vi.fn((_id: string) => Promise.resolve({}))

vi.mock('../../lib/api', () => ({
  api: {
    discover: () => discover(),
    dismissDiscoverTip: (id: string) => dismissDiscoverTip(id),
  },
}))

function payload(tipIds: string[]) {
  return {
    enabled: true,
    visible_count: tipIds.length,
    areas: [
      {
        area: 'Chat',
        tips: tipIds.map((id) => ({
          id, title: `Tip ${id}`, lesson: 'What it teaches.',
          try_it: { label: 'Try it', route: 'chat', query: {} },
        })),
      },
      {
        area: 'Tasks',
        tips: [{ id: 'keep', title: 'Tip keep', lesson: 'Stays.', try_it: { label: 'Try it', route: 'tasks', query: {} } }],
      },
    ],
  }
}

const group = () => document.querySelector<HTMLElement>('[data-entrance]')
const regions = () => [...document.querySelectorAll<HTMLElement>('[data-entrance-region]')]

beforeEach(() => {
  discover.mockReset()
  dismissDiscoverTip.mockClear()
  // The hook caches per key in a module-level Map that survives between tests; a unique
  // key is not available from outside, so seed every run with the same first payload and
  // let the assertions work off node identity rather than off a cold first paint.
  discover.mockResolvedValue(payload(['a', 'b']))
})

describe('Discover stages its regions', () => {
  it('renders one entrance group whose regions are the intro and each area band', async () => {
    render(<DiscoverPage navigate={() => {}} />)
    await screen.findByText('Chat')
    // intro + Chat + Tasks. If the group were placed above the branch instead, the areas
    // would not be its direct children and this reads 1.
    await waitFor(() => expect(regions()).toHaveLength(3))
    expect(group()).toHaveAttribute('data-entrance', 'staggered')
  })

  it('the tour card and every tip are on screen regardless of the cascade', async () => {
    render(<DiscoverPage navigate={() => {}} />)
    // The tour card is deliberately outside the group (it is not part of the catalog), so
    // this also pins that the entrance did not swallow it.
    expect(screen.getByRole('button', { name: 'Start the tour' })).toBeInTheDocument()
    expect(await screen.findByText('Tip a')).toBeInTheDocument()
    expect(screen.getByText('Tip keep')).toBeInTheDocument()
  })
})

describe('a dismiss does not replay the entrance', () => {
  it('refetching after a dismiss keeps the same group and area nodes', async () => {
    render(<DiscoverPage navigate={() => {}} />)
    await screen.findByText('Tip a')
    await waitFor(() => expect(regions()).toHaveLength(3))
    const before = { g: group(), r: regions() }

    // The refetch returns one fewer tip — a real content change, not a no-op re-render.
    discover.mockResolvedValue(payload(['b']))
    screen.getAllByRole('button', { name: /^Dismiss/ })[0].click()

    await waitFor(() => expect(screen.queryByText('Tip a')).toBeNull())
    expect(dismissDiscoverTip).toHaveBeenCalledWith('a')
    // Same nodes ⇒ no mount ⇒ no second cascade. This is the assertion that reds if the
    // group is moved under the loading branch, or keyed on the tip count.
    expect(group()).toBe(before.g)
    expect(regions()).toEqual(before.r)
  })
})
