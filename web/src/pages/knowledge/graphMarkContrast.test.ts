import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { SCHEMES } from '../../design/schemes'

// ── The entity graph's marks must be perceivable (WCAG 2.1 SC 1.4.11) ─────────────────────────
//
// Measured in Chromium on a home seeded with 8 entities and 7 relations — the first time anything
// rendered this view, because a seed fixture cannot carry entities (they are SQLite-only) and the
// surface was not in the capture harness's inventory either:
//
//   node fill   (30% primary over surface)        1.68:1 dark   1.4:1 light
//   node stroke (--color-outline-variant)         2.04:1 dark   1.17:1 light
//   edge stroke (--color-outline-variant @0.5)    1.35:1 dark   1.07:1 light
//
// 3:1 is the bar for "graphical objects required to understand the content", and in a graph view the
// dots and the lines between them ARE the content. Nothing here was close.
//
// 🔑 THE OUTLINE CARRIES IT, NOT THE FILL. Raising the tint reaches 3:1 in dark only at 60%
// (3.21:1) and never in light (2.27:1 at 60%) — and it would restyle the graph. A ≥3:1 boundary is
// the standard remedy for a low-contrast shape.
//
// 🔑 WHY TWO MEASUREMENTS SETTLE TWELVE SCHEMES. `--color-on-surface-low` and `--color-canvas` are
// NEUTRALS: `design/schemes.ts` says so in as many words — "Neutral surfaces stay from tokens.css;
// this drives only the accent identity". A scheme cannot move them, so dark + light is the whole
// matrix. The assertion below pins that property rather than trusting this comment.
//
// 🪤 `--color-primary` measures fine (6.85:1 / 4.37:1) and is still wrong here: the same line uses
// it for `active`, so painting resting nodes with it would erase hover and selection. A token can
// pass the number and fail the meaning.

const SRC = readFileSync(join(process.cwd(), 'src/pages/knowledge/KnowledgeGraph.tsx'), 'utf8')
const TOKENS = readFileSync(join(process.cwd(), 'src/design/tokens.css'), 'utf8')

/** WCAG 2.1 relative luminance + ratio, over sRGB hex. */
function luminance(hex: string): number {
  const h = hex.replace('#', '')
  const chan = (i: number) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * chan(0) + 0.7152 * chan(2) + 0.0722 * chan(4)
}
function ratio(a: string, b: string): number {
  const la = luminance(a), lb = luminance(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}
/** `fg` at `alpha` composited over `bg` — a stroke-opacity mark against the canvas. */
function over(fg: string, alpha: number, bg: string): string {
  const px = (hex: string, i: number) => parseInt(hex.replace('#', '').slice(i, i + 2), 16)
  const mix = (i: number) => Math.round(px(fg, i) * alpha + px(bg, i) * (1 - alpha))
  return `#${[0, 2, 4].map((i) => mix(i).toString(16).padStart(2, '0')).join('')}`
}

/** Read a token's value out of tokens.css for one mode, so a retint cannot drift this guard. */
function token(name: string, mode: 'dark' | 'light'): string {
  // tokens.css declares dark under `:root` and light under a `[data-mode="light"]`-ish block; take
  // the first declaration for dark and the last for light.
  const all = [...TOKENS.matchAll(new RegExp(`${name}\\s*:\\s*(#[0-9a-fA-F]{6})`, 'g'))].map((m) => m[1])
  expect(all.length, `${name} is declared in tokens.css`).toBeGreaterThanOrEqual(2)
  return mode === 'dark' ? all[0] : all[all.length - 1]
}

const MIN = 3 // SC 1.4.11

describe('the entity graph marks meet non-text contrast', () => {
  it('tokens.css yields two distinct values per token — the vacuity floor', () => {
    for (const name of ['--color-canvas', '--color-on-surface-low']) {
      const d = token(name, 'dark'), l = token(name, 'light')
      expect(d, `${name} dark`).toMatch(/^#[0-9a-fA-F]{6}$/)
      expect(l, `${name} light`).toMatch(/^#[0-9a-fA-F]{6}$/)
      expect(d, `${name} differs by mode, so the reads are not the same block twice`).not.toBe(l)
    }
  })

  for (const mode of ['dark', 'light'] as const) {
    it(`the resting node outline clears 3:1 in ${mode}`, () => {
      const r = ratio(token('--color-on-surface-low', mode), token('--color-canvas', mode))
      expect(r, `node outline in ${mode}`).toBeGreaterThanOrEqual(MIN)
    })

    it(`the resting relation clears 3:1 in ${mode} at the opacity it ships`, () => {
      const m = /strokeOpacity=\{hover && !active \? 0\.15 : ([\d.]+)\}/.exec(SRC)
      expect(m, 'the resting edge opacity is readable from source').toBeTruthy()
      const alpha = Number(m![1])
      const painted = over(token('--color-on-surface-low', mode), alpha, token('--color-canvas', mode))
      expect(ratio(painted, token('--color-canvas', mode)), `edge at ${alpha} in ${mode}`)
        .toBeGreaterThanOrEqual(MIN)
    })

    it(`the OLD token would still fail in ${mode} — this guard is not vacuous`, () => {
      expect(ratio(token('--color-outline-variant', mode), token('--color-canvas', mode)))
        .toBeLessThan(MIN)
    })
  }

  it('both marks use the neutral, so no scheme can move them', () => {
    // The claim that two measurements cover twelve schemes, asserted rather than asserted-in-prose.
    expect(SCHEMES.length).toBeGreaterThanOrEqual(11)
    const schemeSrc = readFileSync(join(process.cwd(), 'src/design/schemes.ts'), 'utf8')
    for (const name of ['on-surface-low', 'canvas']) {
      expect(schemeSrc, `${name} is not per-scheme`).not.toMatch(new RegExp(`--color-${name}\\s*:`))
    }
  })

  it('resting marks use the neutral and active marks keep the accent', () => {
    // The distinction the fix must not flatten: hover/selected is what `--color-primary` means here.
    expect(SRC).toMatch(/stroke=\{active \? 'var\(--color-primary\)' : 'var\(--color-on-surface-low\)'\}/)
    const strokes = [...SRC.matchAll(/stroke=\{active \? 'var\(--color-primary\)' : 'var\(--color-on-surface-low\)'\}/g)]
    expect(strokes.length, 'one for the relation, one for the entity').toBe(2)
    expect(SRC, 'the faint outline-variant stroke is gone from both marks')
      .not.toMatch(/: 'var\(--color-outline-variant\)'\}/)
  })

  it('the resting relation is at least a whole pixel wide', () => {
    // A sub-pixel stroke lands as partial pixel coverage, so it cannot reach the ratio its colour
    // promises — the measurement above would be a paper number at 0.6.
    const m = /strokeWidth=\{active \? 1\.6 : ([\d.]+)\}/.exec(SRC)
    expect(m, 'the resting edge width is readable from source').toBeTruthy()
    expect(Number(m![1])).toBeGreaterThanOrEqual(1)
  })

  it('and that width is what actually PAINTS, at any viewport', () => {
    // 🔴 The assertion above is necessary and was not sufficient. This graph's viewBox is 1000×1000
    // under `xMidYMid meet`, so it is never drawn 1:1 — the CTM scale measured 0.761 at 1440px and
    // 0.358 at 390px, painting a declared `1` as 0.76px and 0.36px. Width alone only shrank the
    // shortfall; `non-scaling-stroke` removes it, which is what makes the ratios above real numbers.
    //
    // Asserted on BOTH marks, because a relation drawn at its declared width beside a node that
    // still thins with the viewport is the same defect half-fixed.
    const marks = [...SRC.matchAll(/vectorEffect="non-scaling-stroke"/g)]
    expect(marks.length, 'one for the relation, one for the entity').toBe(2)
    expect(SRC, 'the relation declares it').toMatch(/<line[^>]*vectorEffect="non-scaling-stroke"/)
    expect(SRC, 'the entity declares it').toMatch(/<circle[^>]*vectorEffect="non-scaling-stroke"/)
  })

  it('the scale that makes it necessary is still what the code assumes', () => {
    // The vacuity guard for the reasoning, not the fix: if the viewBox ever stops being a fixed
    // world space, or `meet` becomes `slice`, the numbers in these comments need re-measuring rather
    // than trusting. Both are read from source so a change has to come past this test.
    expect(SRC, 'a fixed 1000×1000 world space').toMatch(/viewBox=\{`0 0 \$\{W\} \$\{H\}`\}/)
    expect(SRC, 'scaled to fit, which is why the CTM is below 1').toMatch(/preserveAspectRatio="xMidYMid meet"/)
  })
})
