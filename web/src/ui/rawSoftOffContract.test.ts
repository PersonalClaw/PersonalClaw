import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── The hand-rolled half of the disabled-button family ────────────────────────────────────
//
// Cycle 109 triaged the 143 `<Button disabled={…}>` and closed the 13 that owed a reason; it also
// measured what a primitive-scoped census CANNOT see — **41 raw `<button disabled={…}>`, 21 with a
// non-busy gate.** #1168 took the two on `#/loops`; this takes the rest of the fixable class.
//
// Verified live on `#/settings/account`, where all three blocked Saves now agree — one through
// `Button`, two hand-rolled with the same contract:
//
//   label            nativeDisabled  aria-disabled  title                        focusable  opacity
//   Save (name)      false           true           "No changes to save"         true       0.4
//   Save (bot name)  false           true           "No changes to save"         true       0.4
//   Save password    false           true           "Use at least 12 characters"  true       0.4
//
// 🪤 THE TWO TRAPS IN CONVERTING A RAW CONTROL, both of which make a soft-off button look ENABLED:
//
//   1. `disabled:opacity-40` cannot match an element that is no longer natively disabled. The class
//      list has to name BOTH selectors. (Measured: opacity 0.4 above is the proof it took.)
//   2. `enabled:hover:*` STARTS matching once the native attribute goes, so a refused row gains a
//      hover tint it never had. `formControls`' cycle row neutralises it explicitly.
//
// `Button` hides both because it computes its own class list; a hand-rolled control does not.
//
// 📌 WHAT IS DELIBERATELY STILL NATIVE, by class:
//   • busy — an in-flight action must not be re-clickable, and `aria-busy` already says so.
//   • pass-through `disabled` on a PRIMITIVE (`Segmented`, `Toggle`, `TextLink`, `ProjectPicker`,
//     `HeaderActions`) — the primitive cannot know the reason; the fix is for each to accept one,
//     which is its own change.
//   • self-evident — `LoopPlanReview`'s Install reads "Installed" when done.
//   • NOT AN ACTION AT ALL — `TaskDetail`'s three read-only/capability gates. #1168 established the
//     shape (render a non-interactive element instead of a disabled button) and they need a small
//     per-site decision about how the state is then conveyed. Named here so they cannot hide.

// 🪤 AND A TRAP IN THIS RAIL'S OWN MATCHER, hit while writing it: `\bdisabled=` matches inside
// `aria-disabled=` — the word boundary sits after the hyphen — so the first version counted every
// button this change CONVERTED as an offender. The lookbehind is the fix, and the lesson is the
// session's recurring one: a census that flags the thing you just fixed is measuring itself.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const FIXED: Array<{ file: string; reason: string; guard: RegExp }> = [
  { file: 'pages/settings/AccountPanel.tsx', reason: 'No changes to save', guard: /aria-disabled=\{!dirty \|\| undefined\}/ },
  { file: 'pages/settings/AccountPanel.tsx', reason: 'No changes to save', guard: /aria-disabled=\{!botDirty \|\| undefined\}/ },
  { file: 'ui/content/ContentSurface.tsx', reason: 'no changes to save', guard: /aria-disabled=\{\(!dirty && !saving\) \|\| undefined\}/ },
  { file: 'pages/tasks/formControls.tsx', reason: 'That would create a dependency cycle', guard: /aria-disabled=\{cyclic \|\| undefined\}/ },
  { file: 'pages/knowledge/KnowledgeDetail.tsx', reason: 'Nothing more to show', guard: /aria-disabled=\{!hasMore \|\| undefined\}/ },
]

describe('a converted raw control keeps its tab stop AND its dimming', () => {
  for (const { file, reason, guard } of FIXED) {
    it(`${file} — "${reason}"`, () => {
      const src = readFileSync(join(SRC, file), 'utf8')
      expect(src, 'the gate must publish aria-disabled, not the native attribute').toMatch(guard)
      expect(src.toLowerCase(), 'and it must say why').toContain(reason.toLowerCase())
      // Trap 1, PER TAG — a file-scoped version of this check stayed GREEN while one of the two
      // Saves in the same file had lost its `aria-disabled:opacity-40`: the same blind spot as
      // #1154's `>N` floor. Every soft-off tag must carry its own pair.
      const softTags = [...src.matchAll(/<button\b[\s\S]{0,900}?>/g)]
        .map((m) => m[0])
        .filter((t) => /aria-disabled=/.test(t) && /disabled:opacity-40/.test(t))
      for (const t of softTags) {
        expect(t, 'a soft-off tag needs aria-disabled:opacity-40 as well').toMatch(/aria-disabled:opacity-40/)
      }
    })
  }

  it('neutralises the hover tint that `enabled:` starts allowing', () => {
    // Trap 2. Only the cycle row has an `enabled:hover:` tint among the converted set.
    const src = readFileSync(join(SRC, 'pages/tasks/formControls.tsx'), 'utf8')
    expect(src).toMatch(/enabled:hover:bg-surface-high aria-disabled:hover:bg-transparent/)
  })

  it('refuses the click it can no longer refuse natively', () => {
    for (const rel of ['pages/settings/AccountPanel.tsx', 'pages/tasks/formControls.tsx', 'pages/knowledge/KnowledgeDetail.tsx', 'ui/content/ContentSurface.tsx']) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      // Either order — `x ? handler : undefined` or `x ? undefined : handler` — and the arrow body
      // may contain braces, so match the ternary rather than the whole expression.
      expect(src, `${rel} must guard its handler`).toMatch(/onClick=\{[^}\n]*\?[^\n]*undefined/)
    }
  })
})

describe('the remaining raw disabled buttons are accounted for', () => {
  /** In-flight vocabulary. Grown twice by measurement: `repairing`, `regenningTitle` and `retrying`
   *  were all classified as "blocked" by an earlier, shorter list — a reminder that this set is
   *  discovered, not guessed. */
  const BUSY = /\b(busy|saving|sending|loading|installing|pending|working|submitting|launching|testing|running|deleting|creating|refreshing|syncing|starting|stopping|genning|importing|exporting|uploading|repairing|regen\w*|retrying|reloading|applying|generating|fetching|polling|checking)\b/i

  /** Every class that is CORRECTLY still native, with the reason it is. A silent filter would let a
   *  real one hide behind the same shape. */
  const ACCOUNTED = new Map<string, string>([
    ['ui/HeaderActions.tsx', 'pass-through `disabled` on a primitive — the caller owns the reason'],
    ['ui/ProjectPicker.tsx', 'pass-through `disabled` on a primitive'],
    ['ui/Segmented.tsx', 'pass-through `disabled` on a primitive'],
    ['ui/TextLink.tsx', 'pass-through `disabled` on a primitive'],
    ['ui/Toggle.tsx', 'pass-through `disabled` on a primitive'],
    ['pages/settings/ProjectionRulesPanel.tsx', 'pass-through `disabled` from its caller'],
    ['pages/settings/SecurityPanel.tsx', 'pass-through `disabled` from its caller'],
    ['pages/loops/LoopPlanReview.tsx', 'self-evident — the label reads "Installed" when done'],
    ['pages/tasks/TaskDetail.tsx', 'NOT AN ACTION: read-only + capability gates, awaiting the #1168 shape'],
  ])

  const unaccounted = walk(SRC).flatMap((f) => {
    const rel = f.slice(SRC.length + 1)
    if (ACCOUNTED.has(rel)) return []
    const src = readFileSync(f, 'utf8')
    return [...src.matchAll(/<button\b[^>]{0,600}?(?<!aria-)disabled=\{([^}]*(?:\{[^}]*\}[^}]*)*)\}/gs)]
      .filter((m) => m[1].split(/\|\||&&/).map((s) => s.trim()).filter(Boolean).some((c) => !BUSY.test(c)))
      .map(() => rel)
  })

  it('leaves none unclassified', () => {
    expect([...new Set(unaccounted)], 'a raw disabled button with a gate a user could act on').toEqual([])
  })

  it('still finds the population it is filtering (not vacuously green)', () => {
    const all = walk(SRC).flatMap((f) => [...readFileSync(f, 'utf8').matchAll(/<button\b[^>]{0,600}?(?<!aria-)disabled=\{/gs)].map(() => 1))
    expect(all.length, 'the matcher must find the raw disabled buttons').toBeGreaterThanOrEqual(30)
  })
})
