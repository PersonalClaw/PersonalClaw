import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── What actually tells you where a card will land, measured mid-drag ──────────────────────────
//
// Drag state is invisible to every scanner here: `ux-audit` and the Playwright baselines only ever
// see a surface's DEFAULT state, so the board's drop indicator has never been measured. Driven on
// `#/tasks?view=board` (light, 1440×900, 30 cards) by firing a real `dragstart` on a card and a
// `dragover` on a column, then reading computed styles:
//
//   over column        background color(srgb 0.912 …) opaque · outline 1.5px DASHED rgb(68 71 70)
//   sibling column     background color(srgb 1 1 1 / 0.4)   · outline 1.5px solid TRANSPARENT
//   over vs sibling    background-only contrast **1.146:1**
//
// 🔑 SO THE BACKGROUND IS NOT THE SIGNAL, AND MEASURING IT ALONE MANUFACTURES A FINDING. 1.146:1
// looks like an open-and-shut 1.4.11 failure for a state indicator (which wants 3:1) — and it would
// be, if the tint were all there was. It isn't: the over column also gets a dashed outline in the
// column's own tone, and a `scale` of 1 + expr(0.012). Three signals, one of which is a shape and one
// motion, so the indicator is neither color-only (1.4.1) nor low-contrast (the outline against the
// tinted background is dark-on-light, far above 3:1).
//
// This file exists because that outline is the load-bearing one and it is easy to lose: it lives in an
// INLINE STYLE beside the background, expressed as `1.5px solid transparent` when idle. A cleanup that
// "removes the transparent outline" or moves the tint to a class would leave the drop target signalled
// by a 1.146:1 background change and nothing else — and no test, no axe run and no baseline would
// notice, because the whole thing only exists while a pointer is mid-drag.
//
// 🪤 A nonzero `outlineWidth` is not a visible outline. The idle column reports `1.5px` too; what
// separates it from the over state is `outlineStyle` and the color. Any future probe here has to read
// all three — reading width alone would have reported both states as identical.

const BOARD = join(process.cwd(), 'src/pages/tasks/TaskBoard.tsx')
const code = () => readFileSync(BOARD, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the drop target is signalled by more than a background tint', () => {
  it('the over state draws a dashed outline in the column tone', () => {
    // THE RATCHET. Measured alternative if this goes: a 1.146:1 background change, alone.
    expect(code(), 'the drop target needs a non-color-only edge')
      .toMatch(/outline: isOver \? `1\.5px dashed \$\{s\.tone\}` : '1\.5px solid transparent'/)
  })

  it('and it still carries the tint and the scale, so the three signals stay three', () => {
    const src = code()
    // Two columns paint the tint (the collapsed rail and the expanded column) — both, or the
    // collapsed rail becomes the odd one out mid-drag.
    const tint = src.match(/isOver \? `color-mix\(in srgb, \$\{s\.tone\} 12%, var\(--color-surface-container\)\)`/g) ?? []
    expect(tint.length, 'both the collapsed rail and the expanded column tint on dragover').toBe(2)
    expect(src, 'the lift that makes the target feel picked out').toMatch(/animate=\{\{ scale: isOver \? 1 \+ expr\(/)
  })

  it('the signal is bound to a live drag, not to hover', () => {
    // `overCol` alone would light a column on any dragover the browser reports, including after a
    // drag ends; the `dragId != null` half is what keeps it honest.
    expect(code()).toMatch(/const isOver = overCol === s\.key && dragId != null/)
  })

  it('the matcher would fail if the outline became color-only', () => {
    // Sabotage, both directions — a rail that cannot fail is not a rail.
    const re = /outline: isOver \? `1\.5px dashed \$\{s\.tone\}` : '1\.5px solid transparent'/
    expect(re.test("outline: isOver ? `1.5px dashed ${s.tone}` : '1.5px solid transparent'")).toBe(true)
    expect(re.test("outline: 'none'")).toBe(false)
    expect(re.test("background: isOver ? `color-mix(in srgb, ${s.tone} 12%, …)`")).toBe(false)
  })
})

// ── The other half of the same transient: the card you picked up ───────────────────────────────
//
// Cycle 186 measured the OUTER draggable and read `opacity: 1`, which suggested a card gave no
// picked-up feedback at all. Wrong element. The feedback lives on the inner `motion.div` and it is
// rich — measured mid-drag on `#/tasks?view=board`:
//
//   opacity      0.5
//   transform    matrix(1.034, -0.0289, 0.0289, 1.034)   ≈ scale 1.034 + rotate 1.65°
//   box-shadow   rgba(0, 0, 0, 0.6) 0 22px 55px -14px    ← `--shadow-lift`, resolved
//
// and `dragend` clears all of it (opacity 1, transform none, 0 cards lifted). So there is no defect
// here; this pins it for the same reason as the drop indicator above — it is a transient that no audit,
// no axe run and no baseline can see, so losing it would be silent.
//
// 🔑 THE LIFT IS TOKENISED AND THAT IS WORTH PROTECTING SPECIFICALLY. It reads
// `var(--shadow-lift)` / `var(--shadow-rest)`, not a raw Tailwind shadow — i.e. this surface is
// already on the right side of the scheme-blind-shadow finding in `design/sheetShadow.test.ts`, where
// seven floating sheets use `shadow-2xl` (Tailwind's fixed default, identical in both schemes). A
// "tidy-up" that swapped this for `shadow-2xl` would look harmless and would quietly enlarge that
// family, so the token form is asserted rather than assumed.
//
// 🪤 PROBE HYGIENE, LEARNED THE EXPENSIVE WAY. Cycle 186's synthetic `dragstart` was never paired with
// a `dragend`, so `dragId` stayed set and the board sat STUCK in the drag state across ticks — the next
// probe then read a stale state as a fresh measurement, and only an identical before/during pair
// (`changed: false`) exposed it. A synthetic drag probe must fire `dragend` in the same script, and a
// state-machine probe should assert its own PRECONDITION before measuring the transition.

describe('the picked-up card is legible, and stays tokenised', () => {
  const src = code   // the same stripped read as above; one helper, not two

  it('all four signals are bound to `dragging`', () => {
    // Four, not one: a card that only dimmed would be ambiguous with a disabled card, which is why
    // the scale, the tilt and the lift all matter. Vacuity — if these unbind, the rest measures air.
    const s = src()
    expect(s, 'the dim').toMatch(/opacity: dragging \? 0\.5 : 1,/)
    expect(s, 'the scale').toMatch(/scale: dragging \? 1 \+ expr\(/)
    expect(s, 'the tilt').toMatch(/rotate: dragging \? -expr\(/)
    expect(s, 'the lift').toMatch(/boxShadow: dragging \? 'var\(--shadow-lift\)' : 'var\(--shadow-rest\)',/)
  })

  it('the lift stays a TOKEN, so this surface never joins the scheme-blind shadow family', () => {
    // `design/sheetShadow.test.ts` holds seven floating sheets on `shadow-2xl` — Tailwind's fixed
    // default, identical in light and dark. This card is not one of them and must not become one.
    const s = src()
    expect(s).toMatch(/var\(--shadow-lift\)/)
    expect(/shadow-2xl/.test(s), 'the board must not adopt the scheme-blind shadow').toBe(false)
  })

  it('only the card being dragged lifts', () => {
    // `dragging={dragId === t.id}` — a truthy `dragId` alone would lift all 30 cards at once.
    expect(src()).toMatch(/dragging=\{dragId === t\.id\}/)
  })

  it('the matcher fails on a dim-only version', () => {
    // Sabotage, both directions: the shape this rail exists to reject is "we kept the opacity".
    const dimOnly = 'opacity: dragging ? 0.5 : 1,'
    expect(/boxShadow: dragging \? 'var\(--shadow-lift\)'/.test(dimOnly)).toBe(false)
    expect(/opacity: dragging \? 0\.5 : 1,/.test(dimOnly)).toBe(true)
  })
})
