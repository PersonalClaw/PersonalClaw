import { describe, expect, it } from 'vitest'
import { clusterFloor, overflowCost, stackBelowCorners } from './HeaderActions'

// ── A cluster whose every control lives in its `…` still pays for the `…` ───────────────────────
//
// Needs you's header holds two settings, both in the `…`. Such a cluster draws nothing in the
// probes, so its full probe read 0 in a laid-out row too, and the `…` was counted as costing
// nothing, the reading kept for a test's document where every width is 0. At 390 px the stack test
// then found room beside a cluster it thought was empty, and the title read "Nee…" next to the 44
// px `…`. The cluster's own box says the row is laid out: the `…` costs its box there.

const DOTS = 44

describe("the `…`'s cost", () => {
  it('is its box when every control lives in it and the row is laid out', () => {
    expect(overflowCost({ inOverflow: true, fullProbe: 0, clusterBox: 44, dots: DOTS })).toBe(DOTS)
  })

  it('is its box beside controls drawn in the row, as before', () => {
    expect(overflowCost({ inOverflow: true, fullProbe: 120, clusterBox: 164, dots: DOTS })).toBe(DOTS)
  })

  it("is nothing in a test's document, where every width reads 0", () => {
    expect(overflowCost({ inOverflow: true, fullProbe: 0, clusterBox: 0, dots: DOTS })).toBe(0)
  })

  it('is nothing when no control lives in it', () => {
    expect(overflowCost({ inOverflow: false, fullProbe: 120, clusterBox: 120, dots: DOTS })).toBe(0)
  })
})

describe("Needs you's bar at 390 px", () => {
  // Its title kept whole (`data-title-keep`), about 121 px with the slot's padding, in a band of
  // about 150 px between the shell corners.
  const title = { band: 150, hasContent: true, minWidth: 121, naturalWidth: 121 }

  it('goes under the corners once its `…` is counted', () => {
    const dots = overflowCost({ inOverflow: true, fullProbe: 0, clusterBox: 44, dots: DOTS })
    const floor = clusterFloor({ iconTier: 0 + dots, neverOverflow: 0, overflowable: true, dots: DOTS })
    expect(stackBelowCorners({ ...title, floor })).toBe(true)
  })

  it('stays between them where the band holds the title and the `…` side by side', () => {
    const dots = overflowCost({ inOverflow: true, fullProbe: 0, clusterBox: 44, dots: DOTS })
    const floor = clusterFloor({ iconTier: 0 + dots, neverOverflow: 0, overflowable: true, dots: DOTS })
    expect(stackBelowCorners({ ...title, band: 520, floor })).toBe(false)
  })
})
