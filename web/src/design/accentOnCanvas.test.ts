import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Accent TEXT on the canvas uses the emphasis shade ────────────────────────────────────────────
//
// The shell paints `--color-canvas` behind every route (`background: var(--color-canvas)`), and accent
// text lands on it wherever a panel does not paint its own surface. Measured in light by axe AND
// `ux-audit`, agreeing: `--color-primary` on `--color-canvas` is **4.37:1** where 4.5 is required.
//
// 🔑 THE GUARANTEE WAS MEASURED AGAINST THE WRONG BACKDROP. `schemeContrast.test.ts` asserted accent
// text against **white**, where every scheme passes (4.83-11.37). Computed across the curated set for
// the backdrop the app actually paints:
//
//     primary → canvas            FAILS in **7 of 12** schemes (4.37-4.41)
//     primary-emphasis → canvas   passes in **all 12**          (worst 4.82, coral 6.0)
//
// So this is a pairing the design system already ships, not a new colour — the same shape as cycle
// 146's `accentChip`. That rail now measures the canvas dimension per scheme; this one pins the call
// sites, because a token rail cannot see which ink a component chose (reverting the Studio tab to the
// failing colour left `schemeContrast` green — measured).
//
// 🔑 SCOPE, MEASURED RATHER THAN GUESSED. A runtime census over 20 routes at BOTH viewports found
// exactly **two** small accent texts painted on the canvas: the Memory Studio tab (both viewports) and
// the inbox-settings link (phone only, where the panel goes full-width). The other 43 small-coral-text
// sites sit on `--color-surface`/`surface-container` (white in light), where the same ink passes at
// 4.83 — so this change does NOT pre-empt the owner's standing "coral as accent text" decision; it
// fixes the two places where the measurement fails today.
//
// 🪤 AND THE HELPER THAT READS THE TOKEN NEEDED FIXING FIRST. Slicing tokens.css from
// `indexOf('.light')` landed in a COMMENT that says ".light mode" 380 characters in, so it read the
// DARK canvas (#0f0f0f) and made the new assertion red at 2.89:1 against a backdrop no light-mode user
// ever sees. Match the rule block, not the first occurrence of the selector's name.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('the two canvas-painted accent texts use the emphasis shade', () => {
  it("the Memory Studio tab's active ink is the emphasis token", () => {
    const code = read('pages/settings/MemoryPanel.tsx')
    expect(code).toMatch(/borderColor: 'var\(--color-primary\)', color: 'var\(--color-primary-emphasis\)'/)
    expect(code, 'the border may stay on the base accent — it is not text')
      .toMatch(/borderColor: 'var\(--color-primary\)'/)
  })

  it('the inbox-settings link uses the emphasis class, in both copies of the panel', () => {
    for (const rel of ['pages/inbox/InboxSettingsPanel.tsx', 'pages/settings/InboxSettingsPanel.tsx']) {
      expect(read(rel), `${rel} link ink`).toMatch(/text-primary-emphasis text-\[0\.8125rem\] hover:underline">Open notification rules/)
    }
  })

  it('neither site carries the old failing ink any more', () => {
    expect(read('pages/inbox/InboxSettingsPanel.tsx'))
      .not.toMatch(/className="text-primary text-\[0\.8125rem\] hover:underline">Open notification rules/)
    expect(read('pages/settings/MemoryPanel.tsx'))
      .not.toMatch(/borderColor: 'var\(--color-primary\)', color: 'var\(--color-primary\)'/)
  })

  it('the call sites carry the measurement, not just the token', () => {
    // A bare token swap reads like a style preference; the number is what stops it being reverted.
    expect(read('pages/settings/MemoryPanel.tsx')).toMatch(/4\.37:1/)
  })

  it('the scheme rail now measures the canvas, which is what makes this rule enforceable', () => {
    const rail = read('design/schemeContrast.test.ts')
    expect(rail).toMatch(/primary-emphasis as accent text on the CANVAS/)
    expect(rail, 'and it reads the LIGHT block, not the first mention of .light')
      .toMatch(/\\.light\\s\*\\\{/)
  })

  it('the emphasis token exists in light for every scheme', () => {
    // The pairing only works because each scheme defines it; `schemes.ts` maps [dark, light].
    const schemes = read('design/schemes.ts')
    const defs = [...schemes.matchAll(/primaryEmphasis:\s*\['#[0-9a-fA-F]{6}',\s*'#[0-9a-fA-F]{6}'\]/g)]
    expect(defs.length, 'schemes defining a light emphasis shade').toBeGreaterThanOrEqual(12)
  })
})
