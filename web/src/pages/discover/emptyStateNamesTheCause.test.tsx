import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'

import { DiscoverPage } from './DiscoverPage'

// ── #452 (the copy half) — "no tips left" has TWO causes and must name the right one ─────
//
// A tip auto-hides once you have used its area; an explicit dismiss hides it for good. Both
// land on `visible_count: 0`, and this page congratulated the user for "exploring every part
// of PersonalClaw" either way — so a user who hid all ten in their first minute was told they
// had explored everything, and a user who genuinely used every area was reassured about
// dismissals they never made. Neither sentence was conditional on anything.
//
// The distinguishing fact now rides the payload (`dismissed_count`, see
// `legibility/discover.py::compute_discover`), which is what makes this fixable without a new
// route. Both fixtures below hold `visible_count: 0` — the ONLY difference is the cause.
//
// 🪤 NOT FIXED, deliberately: dismissing is still one-way. There is no un-dismiss route and no
// reset control, so this rail must not be read as closing #452 — it closes the false sentence.

const discover = vi.fn()

vi.mock('../../lib/api', () => ({
  api: {
    discover: () => discover(),
    dismissDiscoverTip: vi.fn(() => Promise.resolve({})),
  },
}))

const emptyPayload = (dismissedCount: number) => ({
  enabled: true, areas: [], visible_count: 0, total: 10, dismissed_count: dismissedCount,
})

beforeEach(() => {
  discover.mockReset()
  sessionStorage.clear()
})

describe('the Discover empty state names why it is empty', () => {
  it('nothing dismissed → the congratulation, and NO claim about dismissals', async () => {
    discover.mockResolvedValue(emptyPayload(0))
    render(<DiscoverPage navigate={vi.fn()} />)
    expect(await screen.findByText(/explored every part of PersonalClaw/i)).toBeTruthy()
    // The old hint asserted "anything you dismissed stays hidden" unconditionally. With a
    // dismissed set of zero that answers a question the user never asked.
    expect(screen.queryByText(/anything you dismissed stays hidden/i)).toBeNull()
  })

  it('tips dismissed → says how many were HIDDEN, not that you explored everything', async () => {
    discover.mockResolvedValue(emptyPayload(10))
    render(<DiscoverPage navigate={vi.fn()} />)
    expect(await screen.findByText(/you hid 10 of 10/i)).toBeTruthy()
    // The regression this pins: the congratulation used to render here too.
    expect(screen.queryByText(/explored every part of PersonalClaw/i)).toBeNull()
  })

  it('a partial dismissal still reports the real number', async () => {
    discover.mockResolvedValue(emptyPayload(3))
    render(<DiscoverPage navigate={vi.fn()} />)
    expect(await screen.findByText(/you hid 3 of 10/i)).toBeTruthy()
  })

  it('the tour stays reachable in BOTH empty states — it is never earned or used up', async () => {
    discover.mockResolvedValue(emptyPayload(10))
    render(<DiscoverPage navigate={vi.fn()} />)
    expect(await screen.findByText(/Replay the tour/i)).toBeTruthy()
  })
})
