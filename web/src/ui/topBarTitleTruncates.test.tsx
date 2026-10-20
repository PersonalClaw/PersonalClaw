import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { TopBar } from './TopBar'

// ── The left slot's "flexes and truncates" promise, made true ────────────────
//
// `TopBar`'s left slot is `min-w-0 flex-1`, which shrinks the SLOT correctly — but a child
// `<span>` has `min-width: auto`, and **42 of the app's 52 `data-type="title-l"` call sites
// carry no `truncate` class**. So the title laid out at its full intrinsic width and painted
// straight through the slot, sliding under the `shrink-0` control row.
//
// Measured at 390×844 on a live seeded gateway, before this fix — the title's painted right
// edge vs where the controls start:
//
//     prompts −79px · workflows −64px · notifications −51px
//     inbox   −41px · knowledge −27px · learning      −27px
//
// 2 still overlapped at 834px, 0 at 1280px, and the numbers were identical in both themes —
// a pure layout defect, not a theme or contrast one. After: **0 of 36 surfaces overlap at
// 390px, 834px and 1280px.**
//
// The invariant lives on the SLOT rather than in 42 call sites, because a call site that has
// to remember `truncate` is a call site that will drift. These assertions therefore check the
// slot's own class contract, which is what every page inherits.
//
// TWO THINGS THIS DELIBERATELY DOES NOT DO, both learned by measuring:
//
//  1. It does NOT put `overflow-hidden` on the slot. That was the first attempt. It fixes
//     titles, but it also clips the pages that put a whole CONTROL ROW in this slot
//     (`#/loops`, `#/code`, `#/files` — the already-logged LoopComposer overflow), turning
//     controls that merely overlapped into controls that are GONE: reachable dropped 3 → 1 on
//     `#/loops` and `#/code`. Clipping text is graceful degradation; clipping a button is a
//     regression. The last assertion pins that absence so the shortcut cannot come back.
//  2. It relies on `HeaderActions` reserving `titleFloor()` px for the title. Truncating
//     inside a slot that can still reach **0px** shows nothing at all — measured on
//     `#/prompts`, where the page name vanished entirely. That ceiling fix ships alongside.

/** The rendered left slot for a given TopBar configuration. */
function leftSlot(contentAligned: boolean): HTMLElement {
  const { container } = render(
    <TopBar contentAligned={contentAligned}
      left={<span data-type="title-l">A very long page title that cannot possibly fit</span>}
      right={<button type="button">Action</button>} />,
  )
  const el = container.querySelector<HTMLElement>('[data-header-left]')
  if (!el) throw new Error('TopBar rendered no [data-header-left] slot')
  return el
}

describe.each([
  ['default', false],
  ['contentAligned', true],
])('TopBar left slot (%s)', (_label, contentAligned) => {
  it('still shrinks (the flex bound the truncation depends on)', () => {
    const slot = leftSlot(contentAligned)
    // Without both of these the slot never narrows, so nothing downstream can truncate.
    expect(slot.className).toMatch(/\bmin-w-0\b/)
    expect(slot.className).toMatch(/\bflex-1\b/)
  })

  it('truncates its title with an ellipsis and keeps a gap before the controls', () => {
    const slot = leftSlot(contentAligned)
    // `truncate` = overflow-hidden + text-overflow:ellipsis + nowrap. Without it the text is
    // cut mid-glyph at best, and paints through the slot at worst.
    expect(slot.className).toMatch(/\[&_\[data-type\]\]:truncate/)
    // A title flush against the control row reads as broken rather than truncated; measured
    // gapToControls: 0 on all six members before this.
    expect(slot.className).toMatch(/\[&_\[data-type\]\]:pr-s/)
  })

  it('lets the shrink propagate through a nested wrapper', () => {
    const slot = leftSlot(contentAligned)
    // Three of the six members wrap the title one level deeper; a nested flex box otherwise
    // re-establishes `min-width: auto` and the text overflows again despite `truncate`.
    expect(slot.className).toMatch(/\[&_div\]:min-w-0/)
    expect(slot.className).toMatch(/\[&_\[data-type\]\]:min-w-0/)
  })

  it('does NOT clip the slot itself (that would hide control rows, not truncate text)', () => {
    const slot = leftSlot(contentAligned)
    // Guards the rejected shortcut: `overflow-hidden` here measured reachable 3 → 1 on
    // #/loops and #/code, because those pages put a control row in this slot.
    const own = slot.className.split(/\s+/).filter((c) => !c.startsWith('[&'))
    expect(own, `slot's own classes: ${own.join(' ')}`).not.toContain('overflow-hidden')
  })
})
