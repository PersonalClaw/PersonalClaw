import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The task panel's two tick targets clear SC 2.5.8's 24px floor ──────────────────────
//
// FOUND BY THE FIRST AUDIT OF THIS SURFACE THAT HAD DATA. `tasks-detail` is `needsData`, and the
// harness pinned `#/tasks?open=t-6892ad5f`, an id that 404s against the shipped `demo-home` fixture —
// so every prior sweep measured a not-found panel with no criteria and no steps in it. Re-pointed at a
// real id (`t-1a4c7e02`), the surface immediately produced a blocking finding nobody had seen:
//
//     axe target-size [serious] × 3   .size-4.rounded-sm[aria-label="Mark criterion incomplete"]
//
// Measured at tablet 834×1112 on `origin/main` vs this branch:
//
//                              before        after
//     criterion tick target    16×16   →     24×24
//     step marker target       20×20   →     24×24
//     sub-24px targets in the panel   7 →    0
//     axe blocking                    1 →    0
//
// axe flagged only the criteria: the criteria list is `gap-1` (4px) so the undersized-target SPACING
// exception cannot rescue them, while the steps list is `gap-1.5` and squeaks through it. 20px is still
// under the floor though, and leaving it at 20 beside a criterion tick at 24 would put two target sizes
// in one panel — so both moved.
//
// 🪤 THE IDIOM IS "GROW THE BOX, KEEP THE PAINT", AND THE RECLAIM IS HORIZONTAL ONLY.
// `Toggle` reclaims its growth with `-my-px` so a row keeps its height. That is WRONG here: these
// targets are stacked 4px apart, so reclaiming the vertical would make consecutive 24px boxes OVERLAP,
// and a click landing on whichever is painted on top is worse than a small target. A vertical stack has
// to spend the height (measured: the list grew 66.5px → 80px). The HORIZONTAL reclaim is safe, because
// the neighbour to the right is text: with `-mx-*` the painted glyph, its x, its background, its radius
// and the 8px tick-to-text gap are all byte-identical before and after.
//
// 🪤 WHY THIS IS SOURCE-LEVEL. jsdom has no layout, so a `getBoundingClientRect` assertion would pass
// against a 0×0 box and prove nothing. The live numbers above are the evidence and live in the PR; what
// this rail holds is the mechanism — the target box, the painted glyph, and the reclaim axis.

const FILE = join(process.cwd(), 'src/pages/tasks/TaskDetail.tsx')
const src = readFileSync(FILE, 'utf8')

/** Every `<button …>` opening tag in the file, sliced to the first `>` OUTSIDE any `{}`.
 *  🪤 Not the first `>`, and not the first that isn't `=>` — these tags carry arrow functions and
 *  ternaries in their props, so both of those stop early. This exact shortcut produced a false PASS in
 *  a rail in this program before it shipped. */
function buttonTags(): string[] {
  const tags: string[] = []
  let i = 0
  while ((i = src.indexOf('<button', i)) !== -1) {
    let depth = 0
    for (let k = i + 7; k < src.length; k++) {
      const c = src[k]
      if (c === '{') depth++
      else if (c === '}') depth--
      else if (c === '>' && depth === 0) { tags.push(src.slice(i, k + 1)); i = k + 1; break }
      if (k === src.length - 1) i = src.length
    }
  }
  return tags
}

const TICKS = ['Mark criterion', 'Mark step']

describe("the task panel's tick targets clear the 24px floor", () => {
  const tags = buttonTags()

  it('the scan found the panel\'s buttons (vacuity floor)', () => {
    // FIVE at the time of writing, and the number is measured, not guessed: my first draft floored it
    // at 6 and the rail failed against a correct parse. `grep -c '<button'` on the file agrees at 5 —
    // the two ticks, two dependency rows, and the comment composer. A floor, not an equality, so adding
    // a button does not red this; but if the matcher ever stops matching, the per-tick assertions below
    // would pass by finding nothing, which is the failure mode this guards.
    expect(tags.length, 'no <button> tags parsed out of TaskDetail.tsx').toBeGreaterThanOrEqual(5)
    for (const label of TICKS) {
      expect(tags.some((t) => t.includes(label)), `no button matched ${label}`).toBe(true)
    }
  })

  it('each tick button IS the 24px target', () => {
    for (const label of TICKS) {
      const tag = tags.find((t) => t.includes(label))!
      expect(tag, `${label}: the target box must be size-6 (24px)`).toMatch(/\bsize-6\b/)
      // The old spellings are the defect this replaced, and a regression would look exactly like them.
      expect(tag, `${label}: size-4 was the 16px defect`).not.toMatch(/\bsize-4\b/)
      expect(tag, `${label}: size-5 was the 20px defect`).not.toMatch(/\bsize-5\b/)
    }
  })

  it('the reclaim is HORIZONTAL only — never -my-*, which would overlap stacked targets', () => {
    for (const label of TICKS) {
      const tag = tags.find((t) => t.includes(label))!
      expect(tag, `${label}: expected a horizontal reclaim`).toMatch(/-mx-(?:0\.5|1)\b/)
      expect(
        tag,
        `${label}: a vertical reclaim would make consecutive 24px targets overlap — the list must ` +
          `spend the height instead`,
      ).not.toMatch(/-my-|-mt-|-mb-/)
    }
  })

  it('the PAINT stays its own size, on an inner span', () => {
    // The target grew; the glyph did not. A ring or a background on the 24px box would be a 24px halo
    // where there used to be a 16px/20px one.
    const criterion = src.slice(src.indexOf('Mark criterion'))
    expect(criterion.slice(0, 700), 'the criterion tick keeps a 16px painted square')
      .toMatch(/<span className="inline-flex size-4 [^"]*rounded-sm/)
    const step = src.slice(src.indexOf('Mark step'))
    expect(step.slice(0, 900), 'the step marker keeps a 20px painted pill')
      .toMatch(/<span className="inline-flex size-5 [^"]*rounded-pill/)
  })

  it('the hover ring moved to the paint and still respects disabled', () => {
    // `enabled:hover:ring-*` on the button became `group-hover:ring-*` on the span, so the read-only
    // panel must stay ringless the way `enabled:` used to guarantee.
    for (const label of TICKS) {
      const after = src.slice(src.indexOf(label), src.indexOf(label) + 900)
      expect(after, `${label}: the ring belongs on the painted glyph`).toMatch(/group-hover:ring-2/)
      expect(after, `${label}: read-only must stay ringless`).toMatch(/group-disabled:ring-0/)
      expect(after, `${label}: the button needs the group anchor`).toMatch(/className="group /)
    }
  })
})
