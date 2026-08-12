import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { TextLink } from './TextLink'

// ── Forty targets that a spacing exemption appeared to excuse, and did not ─────────────────
//
// A first census across six surfaces reported: `#/tasks` **40 under-24px targets, 0 without a spacing
// exemption** — i.e. "all fine". That reading was wrong, and the way it was wrong is the finding:
//
//   nearest OTHER target        16–40px away   → looks like SC 2.5.8's exception applies
//   enclosing clickable row     1212×47, `cursor-pointer`, wraps all 40
//
// 🔑 **A CONTROL INSIDE A LARGER CLICKABLE SURFACE CAN NEVER USE THE SPACING EXCEPTION** — the 24px
// circle is inside another target by construction. Cycle 72 recorded this for the settings hub's
// switches (inside a full-card nav button); the same probe blind spot recurred here because gaps were
// measured against SIBLINGS only. Re-measured against ancestors: **40 of 40 nested**, so 40 real
// failures, not 0.
//
//   30 ×  20×20   "Select task" checkbox      → 24×24 hit box, painted control still 20×20
//   10 × 248×20   the project `TextLink`      → 28px hit box via padding, text unmoved
//
// After: **0 undersized targets across `#/tasks`, `#/projects`, `#/knowledge`, `#/artifacts`,
// `#/inbox`**, with all four captured surfaces **pixel-identical (0%)**.
//
// 🪤 TWO WRONG FIXES, BOTH CAUGHT BY MEASURING RATHER THAN REASONING:
//
//   1. `inline-flex min-h-6` on `TextLink` made the element an atomic inline box, which re-rounded the
//      text baseline and moved **0.83%** of `#/tasks`' pixels for no benefit. Vertical PADDING grows
//      the hit box without touching the display type — 0%.
//   2. `py-0.5` (2px a side) measured **23.99px**: an inline box's rect is the union of its line boxes,
//      and a 19.99px line box plus 2+2 lands just under the floor. `py-1` has real headroom. **A value
//      that is only exactly right when the font rounds kindly is not right.**

describe('TextLink is a 24px target wherever it sits', () => {
  it('carries vertical padding, not a display change', () => {
    render(<TextLink onClick={vi.fn()}>Personal</TextLink>)
    const a = screen.getByText('Personal')
    expect(a.className).toMatch(/\bpy-1\b/)
    expect(a.className, 'a min-height would need inline-flex, which moves the baseline').not.toMatch(/min-h-6/)
  })

  it('hands the space back so the line rhythm is unchanged', () => {
    render(<TextLink onClick={vi.fn()}>Personal</TextLink>)
    expect(screen.getByText('Personal').className).toMatch(/-my-1/)
  })

  it('still only goes inline-flex when it has an icon to align', () => {
    const { container } = render(<TextLink onClick={vi.fn()}>plain</TextLink>)
    expect(container.firstElementChild!.className).not.toMatch(/inline-flex/)
  })
})

describe("the task row's checkbox paints 20px and clicks 24px", () => {
  const src = readFileSync(join(process.cwd(), 'src/pages/tasks/TasksListPage.tsx'), 'utf8')

  it('is a transparent 24px button around the painted control', () => {
    expect(src).toMatch(/className="shrink-0 grid size-6 -m-0\.5 place-items-center"/)
  })

  it('keeps the 20px painted box, now as a child span', () => {
    // The fix is the hit box, not the design: the border/fill still measures 20×20 at the same origin.
    expect(src).toMatch(/<span className=\{`grid size-5 place-items-center rounded-md border/)
  })

  it('returns the 4px so no row reflows', () => {
    // Measured: row tops [137,192,246,301] and heights 47 identical before and after; the painted box
    // stayed at exactly (228,150) while the hit box grew to (226,148).
    expect(src).toMatch(/size-6 -m-0\.5/)
  })
})
