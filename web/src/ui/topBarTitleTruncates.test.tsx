import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ArrowLeft } from 'lucide-react'
import { TopBar } from './TopBar'
import { Button } from './Button'
import { TOUCH_TARGET } from './Toggle'

/** The prefix of the slot's rules for its title: any `data-type` element but a button. */
const ON_TITLE = '[&_[data-type]:not(button)]:'

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
    // One line, clipped at the slot's edge with an ellipsis. Without it the text is cut mid-glyph
    // at best, and paints through the slot at worst.
    expect(slot.className).toMatch(/\[&_\[data-type\]:not\(button\)\]:overflow-x-clip/)
    expect(slot.className).toMatch(/\[&_\[data-type\]:not\(button\)\]:text-ellipsis/)
    expect(slot.className).toMatch(/\[&_\[data-type\]:not\(button\)\]:whitespace-nowrap/)
    // A title flush against the control row reads as broken rather than truncated; measured
    // gapToControls: 0 on all six members before this.
    expect(slot.className).toMatch(/\[&_\[data-type\]:not\(button\)\]:pr-s/)
  })

  it('lets the shrink propagate through a nested wrapper', () => {
    const slot = leftSlot(contentAligned)
    // Three of the six members wrap the title one level deeper; a nested flex box otherwise
    // re-establishes `min-width: auto` and the text overflows again despite `truncate`.
    expect(slot.className).toMatch(/\[&_div\]:min-w-0/)
    expect(slot.className).toMatch(/\[&_\[data-type\]:not\(button\)\]:min-w-0/)
  })

  it('clips its title across only, so a tall glyph is never cut at the top or bottom', () => {
    // The title font's glyph box is about 1.25 em and title-l's line is 1.2 em. Under `truncate`,
    // whose overflow is hidden on both axes, the UI matrix measured the title 50 px tall in a
    // 48 px box at 200% text zoom (#/inbox, #/knowledge and about 30 other rows). `overflow-x:
    // clip` leaves `overflow-y` visible: `hidden` on one axis would turn the other's `visible`
    // into `auto`, and clip it again.
    const slot = leftSlot(contentAligned)
    const onTitle = slot.className.split(/\s+/).filter((c) => c.startsWith(ON_TITLE)).map((c) => c.slice(ON_TITLE.length))
    expect(onTitle).not.toContain('truncate')
    expect(onTitle.filter((c) => /^overflow-(hidden|clip|auto|scroll)$|^overflow-y-/.test(c)), onTitle.join(' ')).toEqual([])
    expect(onTitle).toContain('overflow-x-clip')
  })

  it('does NOT clip the slot itself (that would hide control rows, not truncate text)', () => {
    const slot = leftSlot(contentAligned)
    // Guards the rejected shortcut: `overflow-hidden` here measured reachable 3 → 1 on
    // #/loops and #/code, because those pages put a control row in this slot.
    const own = slot.className.split(/\s+/).filter((c) => !c.startsWith('[&'))
    expect(own, `slot's own classes: ${own.join(' ')}`).not.toContain('overflow-hidden')
  })
})

// ── The title's rules reach the title, not a button beside it ──────────────────────────────────
//
// The rules were scoped by `[data-type]` to reach the title and not the chips and buttons beside
// it, but a `Button` carries `data-type` too (its type role). So a back button in the slot took the
// title's `min-w-0` and `pr-s`: "Back to Projects" on a project's page was a 40 px target at 320
// to 768 px in spite of its TOUCH_TARGET, its right padding cut to 8 px. jsdom has no stylesheet, so
// each of the slot's descendant rules is read as the selector it compiles to and matched.

/** The slot's descendant rules (`[&_<selector>]:<utility>`), each with the elements it reaches. */
function descendantRules(slot: Element): { cls: string; reaches: Element[] }[] {
  return slot.className.split(/\s+/).filter((c) => c.startsWith('[&_')).map((cls) => {
    let depth = 0
    let end = 0
    for (let i = 0; i < cls.length; i++) {
      if (cls[i] === '[') depth++
      else if (cls[i] === ']' && --depth === 0) { end = i; break }
    }
    const selector = cls.slice(1, end).replace(/^&/, ':scope').replace(/_/g, ' ')
    return { cls, reaches: [...slot.querySelectorAll(selector)] }
  })
}

describe("the title's rules reach the title, not a button beside it", () => {
  it('🔴 a back button in the slot keeps its own box, while the title is still truncated', () => {
    render(
      <TopBar
        left={<div className="flex min-w-0 items-center gap-s">
          <Button variant="ghost" size="sm" ariaLabel="Back to Projects" className={TOUCH_TARGET}><ArrowLeft size={16} /></Button>
          <span data-type="title-l">Garden</span>
        </div>}
        right={null} />,
    )
    const slot = document.querySelector('[data-header-left]')!
    const back = screen.getByRole('button', { name: 'Back to Projects' })
    const rules = descendantRules(slot)
    expect(rules.filter((r) => r.reaches.includes(back)).map((r) => r.cls)).toEqual([])
    const title = screen.getByText('Garden')
    for (const u of ['min-w-0', 'overflow-x-clip', 'text-ellipsis', 'pr-s']) {
      expect(rules.some((r) => r.cls.endsWith(`]:${u}`) && r.reaches.includes(title)), u).toBe(true)
    }
  })
})
