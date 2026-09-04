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

// ── The deep routes the first census could not see ────────────────────────────────────────────
//
// That pass measured `#/tasks`, `#/projects`, `#/knowledge`, `#/artifacts`, `#/inbox` — the LIST
// routes, which were the whole surface inventory at the time — and drove them to 0 undersized targets.
// The inventory has since grown to 49 with the path-segment detail routes added, and two of them were
// never in scope. Re-censused with the app's own audit across all 49 surfaces (drag handles excluded):
// **15 sub-24px targets on 4 surfaces**, of which the raw icon-BUTTON family is five —
//
//   projects-detail   21×21  "Rename"                    ← fixed here
//   projects-detail   16×16  "Open Context in Files"      ← fixed here
//   tasks-board       21×21  "Collapse column" ×3         ← the margin cannot fix it; `.hit-24` did
//
// (The other ten are a different family each: nine 16×16 `Select proposal` CHECKBOXES on
// `#/inbox?kind=proposal`, already logged, and one 307×18 `Go to path` INPUT on `#/files` whose
// failure is height-only.)
//
// Both fixes use the form this file already asserts — a `size-6` box with a negative margin so the
// glyph and the layout stay put. Measured: projects-detail goes 2 → 0 undersized, and the pixel diff
// is **0.0145% dark / 0.0046% light** confined to a 26×30 box, i.e. the two buttons and nothing else.
//
// 🪤 WHY `BoardCollapse` IS NOT IN THIS PASS — the margin cannot reach zero shift with a token.
// Its column-header row height is exactly `button + 4px`, so the button IS the tallest child:
//
//   p-1 (21×21, today)      row 25px   first card y=186
//   size-6 -m-0.5  (−2px)   row 24px   first card y=185   → whole board shifts UP 1px  (diff 2.5–3.6%)
//   size-6 -mx-0.5 (0px)    row 28px   first card y=189   → whole board shifts DOWN 3px (diff 5.5–7.7%)
//
// Zero shift needs a −1.5px vertical pull, which is not a spacing token, and a raw-px class would
// break the token rule. `tasks-board` is deterministic (two captures of one build diff 0%), so those
// percentages are real reflow, not noise. Left alone deliberately rather than trading a WCAG note for
// a 1px reflow across every card on the board; it needs either a new hit-area idiom (an overlay
// pseudo-element) or an owner call on the 1px.
//
// 🔁 THE FIRST OF THOSE TWO IS NOW BUILT, and this note's own words are what specified it: `.hit-24`
// in `design/tokens.css`. A pseudo-element is part of its originating element's rendering and
// hit-testing rather than a node of its own, so an inset-negative `::before` enlarges what the pointer
// can hit while the element's box — and the layout around it — is untouched. Measured on
// `#/tasks?view=board` at 1440×1000, before → after:
//
//     button box                 21×21          →  21×21        (identical)
//     button position            419,157        →  419,157      (identical)
//     column-header row height   25             →  25           (identical)
//     EFFECTIVE pointer target   21.75×21.75    →  24.5×24.5    ← scanned outward in 0.25px steps
//
// So the reflow this note refused to trade for is not paid at all, and the button padding stays
// exactly as the assertion below pins it.
//
// ⚠️ AND WHAT IT DOES NOT DO, so nobody re-measures hoping otherwise: axe — and any
// `getBoundingClientRect` check, including this file's own census — reads the ELEMENT's box, which does
// not change. A `target-size` report on a `.hit-24` control does not go away. SC 2.5.8 is about the
// target a person can hit, and that is what moved; the tool cannot see it. So the margin idiom stays
// preferred wherever slack exists, precisely because there the measurable number improves too.
describe('the detail routes added after the first census', () => {
  const SRC = join(process.cwd(), 'src')
  const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

  it("the project header's Rename button carries a 24px hit box", () => {
    // The box is now the PRIMITIVE's guarantee: IconButton sizes its hit area
    // from the `size` prop, so the pin asserts the prop instead of a class recipe.
    expect(read('pages/projects/ProjectsSection.tsx'))
      .toMatch(/IconButton icon=\{Pencil\} label="Rename" size=\{24\}/)
  })

  it("the context/workspace row's Open-in-Files button carries one too", () => {
    expect(read('pages/projects/ProjectsSection.tsx'))
      .toMatch(/IconButton icon=\{FolderOpen\} label=\{`Open \$\{label\} in Files`\}[\s\S]{0,40}?size=\{24\}/)
  })

  it('neither reintroduces the bare padding that made them undersized', () => {
    const src = read('pages/projects/ProjectsSection.tsx')
    expect(src, 'the 21x21 Rename shape').not.toMatch(/aria-label="Rename"[\s\S]{0,120}?rounded-md p-1 /)
    expect(src, 'the 16x16 Open-in-Files shape').not.toMatch(/in Files`\}[\s\S]{0,200}?rounded p-0\.5 /)
  })

  // Pins the holdout so it is not "converged" without confronting the reflow measured above.
  it('BoardCollapse is deliberately still padding-based', () => {
    expect(read('ui/BoardCollapse.tsx'), 'if this changes, re-measure the board row height first')
      .toMatch(/rounded-md p-1 /)
  })

  it('and it reaches 24px through the overlay idiom instead of a margin', () => {
    const src = read('ui/BoardCollapse.tsx')
    expect(src, 'the collapse button should carry .hit-24').toMatch(/\bhit-24\b/)
    // The margin idiom is what this file measured as a 1px board-wide reflow. If it ever appears here,
    // the reflow is back and the numbers in the note above are stale.
    expect(src, 'a negative margin here reflows the whole board — that is the measured trade-off')
      .not.toMatch(/-m[xy]?-/)
  })
})

// ── The `.hit-24` utility itself ────────────────────────────────────────────────────────
//
// The idiom is only trustworthy if the inset is DERIVED. A hand-tuned `-1.5px` per call site would be
// the raw-px class the note above rejected, and it would silently be wrong on any control that is not
// 21px. `--hit-min` is the floor and `--hit-size` is the drawn size, so the pseudo-element grows by
// half the shortfall — which is why applying it to a 16px control is a one-line `--hit-size` override
// rather than new arithmetic.
describe('.hit-24 expands the pointer target without touching layout', () => {
  const tokens = readFileSync(join(process.cwd(), 'src/design/tokens.css'), 'utf8')
  const rule = tokens.slice(tokens.indexOf('.hit-24 {'), tokens.indexOf('}', tokens.indexOf('.hit-24::before')) + 1)

  it('the utility exists and is not vacuous', () => {
    expect(tokens, '.hit-24 is not defined').toMatch(/^\.hit-24 \{/m)
    expect(tokens, '.hit-24::before is not defined').toMatch(/^\.hit-24::before \{/m)
    expect(rule.length, 'the slice found no rule body').toBeGreaterThan(80)
  })

  it('the host is positioned, or an absolute ::before escapes to the wrong ancestor', () => {
    expect(rule).toMatch(/\.hit-24 \{[^}]*position:\s*relative/s)
  })

  it('the inset is DERIVED from the floor, never a hand-tuned pixel', () => {
    expect(rule, 'the floor must be a variable so a call site can state its own drawn size')
      .toMatch(/--hit-min:\s*24px/)
    expect(rule).toMatch(/--hit-size:/)
    // half the shortfall per side, clamped so an already-large control is unaffected
    expect(rule, 'inset must compute from --hit-min and --hit-size').toMatch(/inset:\s*calc\([^)]*var\(--hit-min\)/)
    expect(rule, 'the shortfall must be halved — a full inset overshoots by 2x').toMatch(/\/\s*2\s*\)/)
    expect(rule, 'clamp at 0 so a control already at the floor does not shrink').toMatch(/max\(0px,/)
  })

  it('the ::before paints nothing and forwards its events', () => {
    expect(rule, 'content is required or the pseudo-element does not generate a box').toMatch(/content:\s*""/)
    expect(rule).toMatch(/position:\s*absolute/)
    // pointer-events:none would defeat the entire purpose.
    expect(rule, 'the overlay must accept pointer events on its host\'s behalf').not.toMatch(/pointer-events:\s*none/)
  })

  it('it states what it cannot do, so the caveat is not rediscovered', () => {
    // axe reads the element's own box. A tool-visible fix needs the margin idiom; this one is
    // real-target-only, and saying so in the source is what stops a later pass "fixing" the report.
    expect(tokens, 'the axe caveat must be recorded beside the utility').toMatch(/axe[\s\S]{0,400}does not change/)
  })
})
