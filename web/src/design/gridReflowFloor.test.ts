import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── A px floor in a multi-column grid is a reflow bomb ────────────────────────────────────────
//
// WCAG SC 1.4.10 (Reflow) requires content to be usable at a 320px-wide viewport without
// two-dimensional scrolling. A CSS grid declaring two or more columns where at least one carries a
// **px minimum** cannot honour that on its own: below the sum of the minima, the columns that CAN
// shrink absorb every remaining pixel and the ones that cannot keep their width. If the shrinkable
// column is the PRIMARY one, the layout inverts — the secondary pane ends up wider than the content
// the page exists to show.
//
// That is not hypothetical. `pages/projects/ProjectsSection` shipped
// `gridTemplateColumns: 'minmax(0, 2fr) minmax(280px, 1fr)'` with no breakpoint anywhere in the
// file, measured on the seeded fixture at five widths:
//
//   viewport   container   Work (2fr)   Tasks (1fr)
//     1440       1244         829          414
//      834        638         357          280   ← the floor is reached here
//      390        390         109          280
//      320        320          39          280   ← 320 − 280 − 1px gap
//
// At 320px the `2fr` column is 39px and the `1fr` column is 7× wider. axe caught the CONSEQUENCE
// rather than the cause: at 39px the primary pane's content overflowed horizontally
// (`scrollWidth 56 / clientWidth 39`) with no focusable children and no `tabindex`, which is
// `scrollable-region-focusable` (serious) — a scroll region no keyboard user can reach. The tempting
// fix is to add `tabindex="0"` and satisfy axe; that ships the 39px pane. The cause is the floor.
//
// 🔑 WHAT THIS RAIL ASSERTS, and why it is shaped as an inventory rather than a single file check:
// the defect is a PATTERN, and it was a population of exactly one only because nobody had looked.
// A future two-pane surface written the same way is the same bug, so the assertion is over every
// `.tsx` under `src/` — a new hard-coded floored grid goes red here rather than at 320px in a
// browser nobody is driving.
//
// ✅ DELIBERATELY EXEMPT, both verified against the running app rather than assumed:
//
//   · `repeat(auto-fill | auto-fit, minmax(<px>, 1fr))` — five sites (`ui/ListScaffold`,
//     `pages/tools/ToolGroupsTile`, `pages/tools/ToolsPage`, `pages/apps/AppsSection` ×2,
//     `pages/learning/IdentityReportPanel`). `auto-fill`/`auto-fit` REDUCE THE COLUMN COUNT as
//     width drops, so they resolve to a single column near 320px on their own. The px value is a
//     wrap threshold, not a floor. This is the correct idiom for a card grid.
//   · `boardGridTemplate(...)` — two sites (`pages/tasks/TaskBoard`, `pages/ChatPage`). A kanban
//     board scrolls horizontally BY DESIGN and its narrow-width affordance is `ui/BoardCollapse`,
//     which collapses columns to rails. Its scroll container is a real focusable region. Flattening
//     a board to one column would destroy the comparison the board exists to enable — a
//     distinction, not drift.
//
// So the rule is narrow on purpose: an UNCONDITIONAL, EXPLICIT multi-column template with a px
// minimum. Anything responsive (`sm:`/`md:`/`lg:`/`xl:` prefixed, or paired with a `grid-cols-1`
// base) satisfies it, because that is the shape that can stack.

const SRC = join(process.cwd(), 'src')

/** Every `.tsx` under `src/`, so a new surface inherits this rail without being registered. */
function tsxFiles(dir: string, acc: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) tsxFiles(p, acc)
    else if (entry.endsWith('.tsx')) acc.push(p)
  }
  return acc
}

// Comments stripped BEFORE any matching, and this is load-bearing rather than tidiness: this
// program has four measured instances of a source rail matching its own documentation, including one
// where the mutation ESCAPED because the searched token appeared in both a `className` and the
// comment above it. The fix's own comment in `ProjectsSection` quotes the old
// `minmax(0, 2fr) minmax(280px, 1fr)` template verbatim to record what was wrong — matching the raw
// file would flag the very change that fixed it.
function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '')
}

const FILES = tsxFiles(SRC).map((path) => ({
  path,
  rel: path.slice(SRC.length + 1),
  code: stripComments(readFileSync(path, 'utf8')),
}))

/** `repeat(auto-fill|auto-fit, …)` reduces its own column count; the px is a wrap threshold. */
const SELF_REDUCING = /repeat\(\s*auto-(?:fill|fit)\s*,/

describe('a multi-column grid must not carry an unconditional px floor', () => {
  it('reads the tree (a rail over nothing asserts nothing)', () => {
    expect(FILES.length, 'no .tsx files were read from src/').toBeGreaterThan(200)
    expect(FILES.some((f) => f.rel === 'pages/projects/ProjectsSection.tsx')).toBe(true)
    // And the stripper must actually strip, or every assertion below reads prose as code.
    expect(stripComments('/* minmax(280px, 1fr) */ x')).not.toContain('280px')
    expect(stripComments('  // minmax(280px, 1fr)\ny')).not.toContain('280px')
  })

  it('no inline gridTemplateColumns declares an explicit multi-column template with a px minimum', () => {
    const offenders: string[] = []
    for (const f of FILES) {
      for (const m of f.code.matchAll(/gridTemplateColumns:\s*(`[^`]*`|'[^']*'|"[^"]*")/g)) {
        const template = m[1]
        if (SELF_REDUCING.test(template)) continue // a card grid — exempt, see the header
        // An explicit template with 2+ minmax/track entries, at least one of which has a px floor.
        const tracks = template.match(/minmax\([^)]*\)/g) ?? []
        const hasPxFloor = tracks.some((t) => /\d+px/.test(t))
        if (tracks.length >= 2 && hasPxFloor) {
          offenders.push(`${f.rel}: ${template}`)
        }
      }
    }
    expect(
      offenders,
      'An unconditional inline template cannot stack, so at 320px the shrinkable column is starved ' +
        '(measured: a 2fr primary pane resolved to 39px next to a 280px secondary). Move it to ' +
        'Tailwind classes with a `grid-cols-1` base and a breakpoint-prefixed multi-column value, ' +
        'or use `repeat(auto-fill, minmax(<px>, 1fr))` if it is a card grid.',
    ).toEqual([])
  })

  it('every arbitrary grid-cols with a px floor is breakpoint-gated over a single-column base', () => {
    const checked: string[] = []
    for (const f of FILES) {
      for (const m of f.code.matchAll(/(?:^|[\s"'`])((?:[a-z]+:)?grid-cols-\[[^\]]*\])/g)) {
        const cls = m[1]
        if (!/\d+px/.test(cls)) continue // no floor, nothing to starve
        if (SELF_REDUCING.test(cls)) continue
        checked.push(`${f.rel}: ${cls}`)
        expect(
          cls,
          `${f.rel} declares a px-floored multi-column grid with no breakpoint prefix — it applies ` +
            'at 320px, where it cannot fit. Prefix it (`md:`) and give the base a single column.',
        ).toMatch(/^(sm|md|lg|xl|2xl):/)
        // A prefixed value only reflows if the unprefixed base is one column.
        expect(
          f.code,
          `${f.rel} gates a multi-column grid behind a breakpoint but never declares the ` +
            'single-column base it falls back to.',
        ).toMatch(/\bgrid-cols-1\b/)
      }
    }
    // The population must be non-empty, or the two assertions above are vacuous — this is the
    // "assert the population moved" discipline: a rule that matches nothing passes forever.
    expect(checked.length, 'no px-floored arbitrary grid-cols found — the rail is vacuous').toBeGreaterThan(0)
  })

  it('the project hub keeps the responsive two-pane form (the measured fix)', () => {
    const hub = FILES.find((f) => f.rel === 'pages/projects/ProjectsSection.tsx')!.code
    // Base: one column, so Work stacks above Tasks below the breakpoint.
    expect(hub, 'the narrow base must be a single column').toMatch(/\bgrid-cols-1\b/)
    // Gated: the 2fr/1fr split, only where the container can hold both minima.
    expect(
      hub,
      'the two-pane split must stay behind a breakpoint — 280px + 280px + a 1px gap needs ~561px',
    ).toMatch(/\bmd:grid-cols-\[minmax\(0,2fr\)_minmax\(280px,1fr\)\]/)
    // And the inline style it replaced must not come back.
    expect(hub, 'the inline template must not return').not.toMatch(/gridTemplateColumns/)
  })
})
