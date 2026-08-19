import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A label on a TRANSLUCENT tint of its own hue ─────────────────────────────
//
// `Button`'s `tonal` variant is `bg-primary/15` (`hover:bg-primary/25`) + an accent-hued label — 22
// sites across onboarding, chat, settings, discover and the code cockpits. Tinting the ground with
// the SAME hue as the ink walks the two toward each other, and the token exists because in the
// light theme it walked them 0.04 too far (`text-primary` #c8452e on the tint measured 4.46:1).
//
// 🪤 THE TINT IS NOT THE LEVER, WHICH IS WHY THE FIRST FIX FAILED. Lightening the ground
// (`bg-primary/15` → `/10`, the move the DegradedChip's warn chip needed) left the audit at
// **exactly 4.46:1** — the measurement did not budge. Two candidates were built and audited before
// one moved the number, and the ink was the one that did. `bg-primary/8` is worth knowing about
// too: it compiles to NOTHING (no such step), so a "fix" written that way would have been an inert
// class with the defect still shipping.
//
// ── ✅ SUPERSEDED 2026-08-18: "DARK IS A NO-OP" WAS FALSE, AND THE LITERAL WAS THE BUG ─────────
//
// This file used to argue, in a 🔑 block, that dark must deliberately carry the SAME LITERAL as
// coral's `--color-primary` (#ff6b5b): "dark is the default theme and it already passes; repainting
// 22 labels there to fix a light-mode miss would be collateral damage dressed as an accessibility
// fix."
//
// The reasoning was sound and the premise was wrong. It measured ONE scheme. A literal ink is
// painted over the ACTIVE scheme's tint, so its contrast is a function of a scheme it knows nothing
// about — and there are twelve. Composited across the curated set (12 schemes × 2 modes × 3 grounds
// × {rest, hover} = 144 combos, `schemeContrast.test.ts` §"tonal tint"):
//
//     the literal pair                    58 of 144 below AA, worst 3.07:1 (Mono/dark/container/hover)
//       ...of which the RESTING state      8 of  72, worst 4.07:1 — two clicks from any dashboard
//     dark = var(--color-primary-emphasis) 0 of 72, worst 5.40:1
//     light = var(--color-on-primary-container) 0 of 72, worst 9.62:1
//
// Two corrections to the old argument, both measured:
//
//  1. "DARK ALREADY PASSES" HELD ONLY FOR CORAL AT REST. Six schemes fail dark at rest
//     (Mono 4.07, Amber 4.27, Phosphor 4.33, Jade 4.35, Honey 4.36, Forest 4.41), and on hover the
//     DEFAULT coral fails too (3.97 on surface-container, 4.13 on surface-low). So repainting dark
//     is not collateral for a light-mode fix — it IS the fix, for a dark-mode defect.
//  2. THE COLLATERAL IT WORRIED ABOUT IS REAL, AND SMALLER THAN THE DEFECT. Coral's dark tonal
//     label moves #ff6b5b → #ff9a86 and its light label #a33922 → #3f1008. That is a visible change
//     to the default theme, and it is the accessible version of it.
//
// What survives intact is this file's other 🔑 point — ONE class serves both grounds, the
// `--color-on-danger` shape — and its trap note above. What changed is that neither mode freezes a
// hex any more: dark references the scheme's OWN accent shade (so it follows a scheme change, and a
// user's hand-picked custom primary, instead of being outrun by it), and light references the ink
// the system already paints on a pale accent tint, which sits far enough from every scheme's light
// tint that one value can serve all twelve — a margin the rail asserts rather than assumes.

const WEB = process.cwd()
const tokens = () => readFileSync(join(WEB, 'src/design/tokens.css'), 'utf8')

function valueIn(block: string, prop: string): string | null {
  const m = block.match(new RegExp(`${prop}:\\s*([^;]+);`))
  return m ? m[1].trim() : null
}
function blocks(): { dark: string; light: string } {
  const src = tokens()
  const lightAt = src.search(/\.light\s*\{/)
  if (lightAt < 0) throw new Error('could not locate the .light block in tokens.css')
  return { dark: src.slice(0, lightAt), light: src.slice(lightAt) }
}

describe('--color-on-primary-tint', () => {
  it('is defined in BOTH themes', () => {
    const { dark, light } = blocks()
    // One definition would mean one ink for two opposite grounds — the `--color-on-danger` defect.
    expect(valueIn(dark, '--color-on-primary-tint'), 'the @theme default must define it').toBeTruthy()
    expect(valueIn(light, '--color-on-primary-tint'), 'light must override it — its ground is near-white').toBeTruthy()
  })

  it('the two themes carry DIFFERENT values', () => {
    const { dark, light } = blocks()
    expect(valueIn(light, '--color-on-primary-tint')).not.toBe(valueIn(dark, '--color-on-primary-tint'))
  })

  it('NEITHER mode freezes a hex — both reference a palette token', () => {
    // This replaces the old "dark must equal --color-primary" assertion, which pinned the DEFECT:
    // it demanded a literal, and a literal is what shipped coral's ink over eleven other schemes'
    // tints (58 of 144 combos below AA). See the SUPERSEDED block at the top of this file.
    // WHICH token each mode picks, and why they differ, is the next test.
    for (const [mode, block] of Object.entries(blocks())) {
      const ink = valueIn(block, '--color-on-primary-tint')!
      expect(ink, `${mode}: a frozen hex cannot follow a scheme retint`).not.toMatch(/^#[0-9a-fA-F]{3,8}$/)
      expect(ink, `${mode}: must reference a palette token`).toMatch(/^var\(--color-[a-z-]+\)$/)
    }
  })

  it('dark takes the scheme accent shade; light takes the pale-tint ink', () => {
    const { dark, light } = blocks()
    // Dark's tint composites toward the dark surface, so the ground rises with the scheme's own
    // primary — the ink has to rise with it. The scheme's emphasis shade does, and clears unaided
    // (worst 5.40:1).
    expect(valueIn(dark, '--color-on-primary-tint')).toBe('var(--color-primary-emphasis)')
    // Light's tint composites toward WHITE, so the ground stays bright and no accent-weight shade
    // clears it (this theme's own emphasis misses 33 of 72 light combos). It routes onto the ink the
    // system already paints on a pale accent tint instead — worst 9.62:1, which is what lets one
    // fixed value serve all 12 schemes. The margin itself is asserted in schemeContrast.test.ts
    // §"tonal tint"; this only pins the shape.
    expect(valueIn(light, '--color-on-primary-tint')).toBe('var(--color-on-primary-container)')
  })

  it('is never a color-mix — Lightning CSS mis-resolves one here', () => {
    // 🪤 BUILD-VERIFIED, NOT THEORETICAL. `color-mix(in srgb, var(--color-primary-emphasis) 78%,
    // black)` in the `.light` block measured better than the shipped fix (worst 5.01) and compiled
    // to a static fallback of **#c77869** — a pale salmon derived from the DARK block's emphasis,
    // because Lightning CSS resolves the var() against the first declaration it saw and then wraps
    // the real value in `@supports (color: color-mix(in lab, red, red))`. Any browser outside that
    // @supports got a near-invisible label. A plain var() reference emits verbatim.
    for (const [mode, block] of Object.entries(blocks())) {
      expect(valueIn(block, '--color-on-primary-tint'), `${mode}: keep this a plain var() reference`)
        .not.toMatch(/color-mix/)
    }
  })

  it('the tonal variant actually reads the token', () => {
    // 🪤 The `--color-on-danger` cycle's lesson: a token fix does nothing for a component that
    // never reads it, and `HeaderActions` was the one site that bypassed the token.
    const btn = readFileSync(join(WEB, 'src/ui/Button.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const tonal = /tonal:\s*'([^']+)'/.exec(btn)?.[1] ?? ''
    expect(tonal, 'the tonal variant must exist').not.toBe('')
    expect(tonal, 'and carry the new ink').toContain('text-on-primary-tint')
    expect(tonal, 'not the raw primary ink that measured 4.46:1').not.toMatch(/\btext-primary\b/)
  })

  it('no OTHER component hand-rolls the failing pair', () => {
    // The variant exists because pages used to write `bg-primary/15 text-primary` by hand; a site
    // that still does inherits the defect the token was added to fix.
    const walk = (dir: string): string[] => {
      const { readdirSync, statSync } = require('node:fs') as typeof import('node:fs')
      return readdirSync(dir).flatMap((n: string) => {
        const p = join(dir, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
      })
    }
    const offenders = walk(join(WEB, 'src'))
      .filter((f) => {
        const code = readFileSync(f, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
        // Same element carrying both halves of the failing pair.
        return /className="[^"]*bg-primary\/15[^"]*\btext-primary\b/.test(code)
          || /className="[^"]*\btext-primary\b[^"]*bg-primary\/15/.test(code)
      })
      .map((f) => f.slice(join(WEB, 'src').length + 1))
    expect(offenders, `these re-create the 4.46:1 pair by hand:\n${offenders.join('\n')}`).toEqual([])
  })
})
