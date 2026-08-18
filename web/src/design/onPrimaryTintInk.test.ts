import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A label on a TRANSLUCENT tint of its own hue ─────────────────────────────
//
// `Button`'s `tonal` variant is `bg-primary/15` + a primary-hued label — 22 sites across
// onboarding, chat, settings, discover and the code cockpits. Tinting the ground with the SAME hue
// as the ink walks the two toward each other, and in the light theme it walked them 0.04 too far:
//
//     light   text-primary #c8452e on bg-primary/15   → 4.46:1  ✗ (AA needs 4.5 at 13px/450)
//     light   --color-on-primary-tint #a33922         → clears it  ✓
//     dark    text-primary #ff6b5b                    → ~6.9:1  ✓ already
//
// Measured on a live seeded gateway with `ux-audit` (the tool that composites translucent grounds
// and sibling backdrops); the ratio is pinned here rather than recomputed because jsdom cannot
// resolve a CSS var chain across a theme class.
//
// 🪤 THE TINT IS NOT THE LEVER, WHICH IS WHY THE FIRST FIX FAILED. Lightening the ground
// (`bg-primary/15` → `/10`, the move the DegradedChip's warn chip needed) left the audit at
// **exactly 4.46:1** — the measurement did not budge. Two candidates were built and audited before
// one moved the number, and the ink was the one that did. `bg-primary/8` is worth knowing about
// too: it compiles to NOTHING (no such step), so a "fix" written that way would have been an inert
// class with the defect still shipping.
//
// 🔑 DARK DELIBERATELY GETS THE SAME VALUE AS `--color-primary`. Dark is the default theme and it
// already passes; repainting 22 labels there to fix a light-mode miss would be collateral damage
// dressed as an accessibility fix. The token exists so ONE class can serve both grounds — the same
// shape as `--color-on-danger`, which carries opposite inks for the same reason.

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

  it('dark is a no-op — the default theme was already passing', () => {
    const { dark } = blocks()
    expect(
      valueIn(dark, '--color-on-primary-tint')!.toLowerCase(),
      'dark must equal --color-primary, so this fix repaints nothing in the default theme',
    ).toBe(valueIn(dark, '--color-primary')!.toLowerCase())
  })

  it('light is DEEPER than its own primary, and stays inside the palette', () => {
    const { light } = blocks()
    const ink = valueIn(light, '--color-on-primary-tint')!.toLowerCase()
    expect(ink, 'a light-theme fix that reused the same ink would fix nothing')
      .not.toBe(valueIn(light, '--color-primary')!.toLowerCase())
    // Reuses the value already in the palette rather than introducing a new shade.
    expect(ink, 'must be an existing palette value (primary-emphasis)')
      .toBe(valueIn(light, '--color-primary-emphasis')!.toLowerCase())
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
