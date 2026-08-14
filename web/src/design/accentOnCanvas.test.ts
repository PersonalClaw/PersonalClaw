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

// ── Cycle 155: THE THIRD GROUND — a small accent CHIP sits on `--color-surface-high` ─────────────
//
// axe on `#/knowledge` → Intents (a tab that had never been driven): the "proposes skill" chip measured
// **3.79 dark / 3.22 light** against the 4.5 floor. It was `text-primary/80`, and the alpha is only part
// of it — computed across the curated set on that ground:
//
//     primary/80 → surface-high         3.79 / 3.22   (the shipped value)
//     primary    → surface-high         worst **4.26** light — fails in 10 of 12 schemes
//     primary-emphasis → surface-high   worst **4.70** light, 7.01 dark — passes all 12
//
// So dropping the alpha would NOT have fixed it. Same answer as the canvas, third ground, and
// `ui/Markdown`'s inline-code chip had already reached for `text-primary-emphasis` on its own — the
// precedent was in the tree before this cycle named the rule.
//
// Nine sites where accent ink and `bg-surface-high` are on the SAME element now use the emphasis token.
// One is axe-measured; the rest are token-decidable at their rendered size (the markdown body is 15px,
// so `text-[0.8em]`/`[0.85em]`/`[0.92em]` are 12-13.8px — all under the 18.66px large-text threshold):
//
//   knowledge/KnowledgeListPage  the "proposes skill", in-progress and enriching chips   ← axe-measured
//   ui/Markdown                  the memory-citation chip (12px) and file-path chip (12.75px)
//   chat/PasteChip               the two file chips (13.8px, 12.75px)
//   loops/LoopPlanReview         two buttons whose ground BECOMES surface-high on hover
//
// 🪤 DELIBERATELY NOT INCLUDED, with reasons, so the next pass does not "finish the job":
//   ui/Markdown inline code       already `text-primary-emphasis` — the precedent
//   code/CodeCockpitPage          its accent ink pairs with `bg-primary/15`, the accent-CHIP family
//                                 (cycle 146), not this ground
//   knowledge/KnowledgeDetail     an ICON at `text-primary/70`; non-text carries a 3:1 floor, and it
//                                 measures 3.79 — passing
//
// 🪤 AND THE SIZE HAD TO BE MEASURED, NOT INFERRED FROM THE CLASS. An `em` size says nothing on its own;
// the same class is a failure at a 15px base and compliant large text at 24px. Read the computed
// `fontSize` off the rendered node.

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

describe('accent chips on surface-high use the emphasis shade', () => {
  const SITES: [string, RegExp][] = [
    ['pages/knowledge/KnowledgeListPage.tsx', /bg-surface-high px-1\.5 text-primary-emphasis text-\[0\.75rem\]/],
    ['ui/Markdown.tsx', /bg-surface-high px-1\.5 align-baseline text-\[0\.8em\] text-primary-emphasis/],
    ['ui/Markdown.tsx', /text-\[0\.85em\] font-mono text-primary-emphasis underline/],
    ['pages/chat/PasteChip.tsx', /text-\[0\.92em\] text-primary-emphasis/],
    ['pages/chat/PasteChip.tsx', /text-\[0\.85em\] text-primary-emphasis/],
    ['pages/loops/LoopPlanReview.tsx', /text-\[0\.75rem\] text-primary-emphasis hover:bg-surface-high/],
    ['pages/loops/LoopPlanReview.tsx', /text-\[0\.8125rem\] text-primary-emphasis hover:bg-surface-high/],
  ]
  for (const [rel, re] of SITES) {
    it(`${rel} pairs surface-high with the emphasis token (${re.source.slice(0, 34)}…)`, () => {
      expect(read(rel)).toMatch(re)
    })
  }

  it('no site pairs surface-high with the plain or alpha accent on the same element', () => {
    // THE RATCHET, and the reason it is written from the other side: a new chip that reaches for
    // `text-primary` on this ground cannot pass AA in 10 of 12 schemes, so it fails here first.
    const { readdirSync, statSync } = require('node:fs') as typeof import('node:fs')
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
      })
    const offenders: string[] = []
    for (const abs of walk(SRC)) {
      readFileSync(abs, 'utf8').split('\n').forEach((line, i) => {
        for (const m of line.matchAll(/className=(?:\{`|")([^"`]*)(?:`\}|")/g)) {
          const cls = m[1]
          if (!/(?:^|\s)bg-surface-high(?:\s|$)/.test(cls)) continue
          if (/(?:^|\s)text-primary(?:\/\d+)?(?:\s|$)/.test(cls)) offenders.push(`${abs.slice(SRC.length + 1)}:${i + 1}`)
        }
      })
    }
    expect(offenders, `these cannot reach AA on this ground:\n${offenders.join('\n')}`).toEqual([])
  })

  it('the scheme rail carries the number for every scheme, not just the default', () => {
    const rail = read('design/schemeContrast.test.ts')
    expect(rail).toMatch(/primary-emphasis as accent text on SURFACE-HIGH/)
    expect(rail, 'both modes').toMatch(/contrast\(emphasis\.dark, HIGH_DARK\)/)
  })
})
