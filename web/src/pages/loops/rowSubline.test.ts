import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { hasDistinctName } from './loopPhases'

// ── The loops list row printed the same sentence twice ────────────────────────────────────────
//
// `#/loops/history` — the loops LIST, which `#/loops` is not; that route renders the composer, and
// `LoopsSection` only reaches `LoopsListPage` at `seg === 'history'`. The surface had never been
// reviewed until cycle 164 added it to the capture inventory, and the first look at it showed this:
//
//   title  "zz45 Design a dispatcher-console design system for our b…"
//   sub    "zz45 Design a dispatcher-console design system for our b…"
//
// The title is the loop's `name` and the line under it was `c.goal` — but a loop with no explicit
// name is AUTO-NAMED from its goal, hard-truncated (measured: a 60-character mid-word cut of `task`,
// "…for our bus-r"), so both lines carry one sentence. Measured across this dev home's 3 non-code
// loops: **1 of 3**. The other two are named properly and their goal line earns its space, so the
// rule has to be conditional — suppressing it for everyone would delete real content.
//
// 🔑 THE SECOND LINE'S JOB IS "the latest on this loop" (`↳ key_insight || summary`), and `c.goal` is
// its fallback. When there is no finding yet AND no name of its own, it has nothing to add, and the
// row is better one line shorter than filled with a copy of the line above. The sibling loop list
// (`#/code`) already treats that slot as STATE rather than prose — `stage · kind · progress` — and
// never repeats its title.
//
// 🪤 WHAT WAS TRIED AND REJECTED, so it is not re-attempted:
//
//   the plan's current step   `LoopPeek` reads `loop.execution_plan` (role/target/agent_name), and
//                             that array is **empty for exactly the affected loop** — its steps live
//                             in `kind_config.design_steps` instead. A fallback that renders nothing
//                             for the one row that needs it is not a fallback.
//   a relative timestamp      there is no shared time helper: `relTime` exists FOUR times (tasks,
//                             knowledge, files, notifications) with three different signatures. Using
//                             one would couple the loops area to another page's meta module; adding a
//                             fifth is worse. Converging those four is its own coherence cycle.
//
// 🪤 AND THE ROW HEIGHT HAD TO BE PINNED. Dropping the line made that row **76px** against its
// siblings' 78px, which would shift every row below it in any capture diff. The floor lives on the
// text block (`min-h-[2.875rem]` = the measured 22.5px title + 23.5px sub-line-with-margin) rather
// than on an empty `<p>`, so no placeholder element is left behind for a later cleanup to delete.
//
// Driven at `?filter=all`, all three loops: the auto-named row's second line is now empty and every
// row is 78px with a 46px text block; the two named rows still show their `↳ …` insight, unchanged.

describe('hasDistinctName — does the goal line earn its space', () => {
  it('is false when the name is the top of the goal (the auto-named case)', () => {
    expect(hasDistinctName(
      'zz45 Design a dispatcher-console design system for our bus-r',
      'zz45 Design a dispatcher-console design system for our bus-routing tool: high-contrast tokens',
    )).toBe(false)
  })

  it('is true for a loop named by hand', () => {
    // Both real examples from the dev home, whose goal lines DO add information.
    expect(hasDistinctName('RAIDZ2 vs dRAID homelab report', 'Compare RAIDZ2 and dRAID for a 8-bay homelab')).toBe(true)
    expect(hasDistinctName('Morning bus tier options analysis', 'Work out whether staggering the morning tiers')).toBe(true)
  })

  it('ignores a trailing ellipsis the backend added', () => {
    // A truncation marked with "…" is still a truncation, and must not read as a distinct name.
    expect(hasDistinctName('Design a dispatcher console…', 'Design a dispatcher console for the depot')).toBe(false)
  })

  it('is case- and trailing-space-insensitive', () => {
    expect(hasDistinctName('  design a THING  ', 'Design a thing that works')).toBe(false)
  })

  it('is false for no name at all, because the title then IS the goal', () => {
    // `title = c.name || c.goal`, so an unnamed loop shows its goal above; repeating it below is the
    // very defect this rule exists to prevent.
    expect(hasDistinctName('', 'Ship the thing')).toBe(false)
    expect(hasDistinctName('   ', 'Ship the thing')).toBe(false)
  })

  it('a name that merely SHARES words is still distinct', () => {
    // The rule is prefix-only on purpose: "Bus report" vs a goal about buses is a real name.
    expect(hasDistinctName('Bus tier report', 'Analyse the bus tiers and write it up')).toBe(true)
  })
})

describe('the row uses the rule, and keeps its geometry', () => {
  const src = readFileSync(join(process.cwd(), 'src/pages/loops/LoopsListPage.tsx'), 'utf8')
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('renders the second line only when it has something to say', () => {
    expect(code).toMatch(/\{\(latestText \|\| goalEarnsItsLine\) && \(/)
    expect(code, 'the goal is still the fallback when the loop has its own name')
      .toMatch(/latestText \? <span className="text-on-surface-var">↳ \{latestText\}<\/span> : c\.goal/)
  })

  it('pins the text block height so a shorter row does not shift the list', () => {
    expect(code).toMatch(/min-h-\[2\.875rem\]/)
  })

  it('stops hand-slicing the title, so CSS truncates it with an ellipsis', () => {
    // `c.goal.slice(0, 70)` cut mid-word with no ellipsis while the span was ALREADY `truncate`;
    // the manual cut was both redundant and worse than the one the browser does at the real width.
    expect(code, 'the title is the name, else the whole goal').toMatch(/const title = c\.name \|\| c\.goal/)
    expect(code, 'no JS slice of the goal remains').not.toMatch(/c\.goal\.slice\(0, 70\)/)
  })

  it('the row hit target is named from the same title, bounded by the shared helper', () => {
    // Cycle 164 gave this row its keyboard route; the name must stay capped (rowSubject caps at 55
    // and adds an ellipsis) rather than growing to a whole goal paragraph.
    expect(code).toMatch(/<RowHitTarget label=\{rowSubject\(\[title\]\)\} \/>/)
  })
})
