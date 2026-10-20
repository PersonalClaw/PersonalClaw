import { describe, expect, it } from 'vitest'
import { titleFloor, titleReserveFor, railCeiling, clusterFloor, stackBelowCorners, TITLE_MIN } from './HeaderActions'

// ── An EMPTY title slot is owed nothing ──────────────────────────────────────
//
// `HeaderActions` splits the header's inner width between the title and the control row. Both
// halves of that split have to agree on what the title is owed, and they did not:
// `availableWidth()` reserved `min(leftNatural, titleFloor(inner))` — nothing when the slot is
// empty — while the rail's CEILING subtracted `titleFloor(inner)` unconditionally.
//
// Several headers render `left={undefined}`. `#/chat` on a new chat is one, and 53px of phantom
// title reserve was exactly what its controls were short of. Measured at 390×844 against a live
// seeded gateway:
//
//     inner box                       155px
//     two 40px mode pills need         88px   (railScrollW)
//     rail was capped at               58px   = 155 − 44 (dots) − 53 (phantom title floor)
//     → the permission-mode pill painted out to x=181 and collided with the `…` at x=159.
//       The `…` is a LATER SIBLING, so it won the stacking order and the pill was unclickable:
//       a real mouse hover produced aria-expanded=false and ZERO menu items.
//
// After: `aria-expanded=true` with all four options (Normal / Trust reads / Trust / YOLO), and
// surfaces with unusable header controls went 5 → 4 at 390px. The 4 that remain are the
// already-logged overflowing-control-row taste call, untouched.
//
// WHY THIS TESTS PURE FUNCTIONS AND NOT A RENDER: I wrote it as a render assertion first, and
// it passed against the OLD code — jsdom reports every box as 0, so the entire width
// computation collapses to zeros and no component test can tell the two implementations apart.
// The arithmetic was therefore lifted out of the measure closure so the decision itself is
// observable. A test that cannot fail is worse than no test: it reads as coverage.

describe('titleFloor', () => {
  it('scales with the header and stays inside the legible band', () => {
    // A fixed 96px floor on a phone (~155px inner) leaves the cluster almost nothing and
    // forces overflow far too early — hence ~1/3, clamped.
    expect(titleFloor(155)).toBe(53)
    expect(titleFloor(1000)).toBe(96)   // clamped at the top
    expect(titleFloor(50)).toBe(48)     // clamped at the bottom
  })
})

describe('titleReserveFor', () => {
  it('reserves NOTHING when the slot holds no visible content', () => {
    // The whole defect in one assertion. `naturalWidth` is deliberately non-zero here because
    // that is what a real empty flex slot reports — its own padding and gap — so keying on
    // width instead of content is exactly the trap this guards.
    expect(titleReserveFor({ hasContent: false, naturalWidth: 49, inner: 155 })).toBe(0)
  })

  it('reserves the floor when a wide title IS present', () => {
    // Counterpart direction: the fix must not zero every reserve, or it re-opens the
    // 0px-title-slot defect closed on #/prompts (the page name vanished entirely).
    expect(titleReserveFor({ hasContent: true, naturalWidth: 300, inner: 155 })).toBe(53)
  })

  it('never reserves more than the title actually needs', () => {
    // A short title ("Tasks" ≈ 51px) must not hold back the full floor — the cluster gets the
    // difference, which is why `#/tasks` has slack at 390px while longer titles truncate.
    expect(titleReserveFor({ hasContent: true, naturalWidth: 51, inner: 155 })).toBe(51)
  })
})

describe('railCeiling', () => {
  it('gives a title-less header the width its controls need', () => {
    // 155 − 44 − 0 = 111 ≥ the 88px the two mode pills need, so nothing is clipped.
    const ceiling = railCeiling({ inner: 155, dots: 44, title: 0 })
    expect(ceiling).toBe(111)
    expect(ceiling).toBeGreaterThanOrEqual(88)
  })

  it('reproduces the old starvation when a phantom floor is subtracted', () => {
    // The pre-fix arithmetic, pinned so the regression is legible rather than folklore:
    // 155 − 44 − 53 = 58, which is 30px short of the pills' 88px.
    const starved = railCeiling({ inner: 155, dots: 44, title: titleFloor(155) })
    expect(starved).toBe(58)
    expect(starved).toBeLessThan(88)
  })

  it('still protects a real title from the controls', () => {
    // With a genuine title the ceiling must come DOWN, so the rail cannot eat the page name.
    expect(railCeiling({ inner: 155, dots: 44, title: 53 }))
      .toBeLessThan(railCeiling({ inner: 155, dots: 44, title: 0 }))
  })

  it('never returns a negative cap', () => {
    // A very narrow header must clamp at 0 rather than produce a nonsense max-width.
    expect(railCeiling({ inner: 40, dots: 44, title: 53 })).toBe(0)
  })
})

// ── A title slot that holds more than a title is owed what it cannot shed ─────────────────────────
//
// The floor above was reserved for the WHOLE slot, as if every slot held only a title. The chat's
// does not: a back button, the title, the regenerate affordance — and until they moved to the
// context line, a cost chip and a "Branched from" chip that could not shrink. Reserving 96px for a
// slot that could not get narrower than several hundred let the cluster claim the rest, and the
// slot painted UNDER it: measured on `0b487d9c7`, the back button under the Task pill at 320/390px
// and the chat's title at 0px at every width through 1440.
//
// The numbers below are the chat header's own, read off the live page after the fix: the slot
// cannot get narrower than 73px (back 40 + gaps + the title's padding + regenerate) and would like
// 490 (the title's 420px cap); the cluster's icon tier is 424px and its floor 140px (two 48px mode
// pills that never overflow, plus the 44px `…`); the band between the shell corners is 85, 155,
// 265, 465 and 477px at 320, 390, 500, 700 and 1024px.

const CHAT = { hasContent: true, minWidth: 73, naturalWidth: 490 }

describe('titleReserveFor — the slot keeps what it cannot shed', () => {
  it('gives the NAME its floor on top of the slot\'s own chrome', () => {
    // 1024px, not stacked: 73 + 96. The old whole-slot floor (96) left the title 23px.
    expect(titleReserveFor({ ...CHAT, inner: 477, floor: 140 })).toBe(169)
    expect(titleReserveFor({ ...CHAT, inner: 477 })).toBe(169)
  })

  it('lets the title yield past its floor so the controls keep theirs', () => {
    // 320px, stacked (288px row): 288 − 16 − 140 = 132, so the cluster keeps its full 140px floor.
    expect(titleReserveFor({ ...CHAT, inner: 288, floor: 140 })).toBe(132)
  })

  it('never yields below what the slot cannot shed', () => {
    // A row too narrow even for that overlaps whatever the split does; the split must not add a
    // second overlap by handing the cluster the slot's own chrome.
    expect(titleReserveFor({ ...CHAT, inner: 200, floor: 140 })).toBe(73)
  })

  it('still reserves nothing for an empty slot, and never more than a short title needs', () => {
    expect(titleReserveFor({ hasContent: false, naturalWidth: 49, minWidth: 49, inner: 155, floor: 140 })).toBe(0)
    expect(titleReserveFor({ hasContent: true, naturalWidth: 51, minWidth: 8, inner: 155, floor: 88 })).toBe(51)
  })
})

describe('clusterFloor', () => {
  it('is the never-overflow controls plus the `…` when anything can fall into it', () => {
    // The chat: two mode pills (48px each with their gap) + the `…`.
    expect(clusterFloor({ iconTier: 424, neverOverflow: 96, overflowable: true, dots: 44 })).toBe(140)
  })

  it('is the icon tier itself when that is narrower', () => {
    // Two plain controls: the `…` alone (44) is narrower than both icons (88) — so 44.
    expect(clusterFloor({ iconTier: 88, neverOverflow: 0, overflowable: true, dots: 44 })).toBe(44)
    // One never-overflow strip and nothing else: no `…` is ever drawn.
    expect(clusterFloor({ iconTier: 142, neverOverflow: 150, overflowable: false, dots: 44 })).toBe(142)
  })
})

describe('stackBelowCorners — when the band between the corners cannot hold the row', () => {
  const at = (band: number) => stackBelowCorners({ ...CHAT, band, floor: 140 })

  it('stacks the chat header on a phone', () => {
    // 73 + 48 (a legible title) + 16 + 140 = 277px against bands of 85 and 155.
    expect(at(85)).toBe(true)
    expect(at(155)).toBe(true)
    // 500px: 265 would hold everything with a 36px title, which is not a title anyone can read.
    expect(at(265)).toBe(true)
  })

  it('keeps it between the corners once the band holds it', () => {
    expect(at(465)).toBe(false)
    expect(at(477)).toBe(false)
    expect(at(277)).toBe(false)
    expect(at(276)).toBe(true)
  })

  it('stacks a title-less header only when its controls alone do not fit', () => {
    // #/chat before the first message: no title, and the same 140px floor. 390px holds it (as it
    // did before this change); 320px does not.
    const bare = (band: number) => stackBelowCorners({ hasContent: false, minWidth: 0, naturalWidth: 0, band, floor: 140 })
    expect(bare(155)).toBe(false)
    expect(bare(85)).toBe(true)
  })

  it('asks a short title for no more than it is', () => {
    // "Tasks" is 51px: the title term is min(51, 8 + 48) = 51, not the full legible floor.
    expect(stackBelowCorners({ hasContent: true, minWidth: 8, naturalWidth: 51, band: 51 + 16 + 140, floor: 140 })).toBe(false)
    expect(TITLE_MIN).toBe(48)
  })
})
