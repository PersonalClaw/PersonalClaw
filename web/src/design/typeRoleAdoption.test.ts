import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Type-role adoption ratchet (audit AB-1) ─────────────────────────────────
// The type system's sanctioned voice is the data-type role ladder in
// design/tokens.css (display/headline/title/body/label/caption): each role
// carries size + line-height + variable-font weight together, so text set
// through a role stays coherent across the ramp. Pages had hand-rolled the
// sizes as arbitrary Tailwind literals (`text-[0.8125rem]` etc.) thousands of
// times — size without the role's line-height or weight, the largest single
// source of typographic drift in the tree.
//
// This ratchet — the statusTint / tableAdoption idiom — holds the COUNT of
// arbitrary rem-size literals DOWN across pages/, app/, ui/ and lib/: a NEW
// one turns CI red, and each migration onto a role lowers the baseline IN THE
// SAME COMMIT. The number may only shrink.
//
// A COUNT, not a zero rail: legitimate holdouts exist — form controls size
// through the Field primitives, and one-off display moments may deliberately
// sit off-ramp. The ratchet only stops NEW literals while migrations drain
// the stock file by file.
//
// Runs in the existing CI `web` vitest job (source-text scan, no browser).

const SRC = join(process.cwd(), 'src')
const SCAN_ROOTS = ['pages', 'app', 'ui', 'lib'] as const

// An arbitrary rem font-size utility: text-[0.75rem], text-[1.0625rem].
// Token/var forms (text-[var(--…)]) and non-size text-[] color arbitraries do
// not match — the ratchet is about hand-rolled SIZES only.
const ARBITRARY_TEXT_SIZE = /\btext-\[[0-9.]+rem\]/

function listSource(dir: string): string[] {
  const out: string[] = []
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) out.push(...listSource(p))
    else if (/\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name)) out.push(p)
  }
  return out
}

function countArbitrarySizes(): { total: number; byFile: Record<string, number> } {
  const byFile: Record<string, number> = {}
  let total = 0
  for (const root of SCAN_ROOTS) {
    for (const p of listSource(join(SRC, root))) {
      const n = (readFileSync(p, 'utf8').match(new RegExp(ARBITRARY_TEXT_SIZE, 'g')) || []).length
      if (n > 0) {
        byFile[p.slice(SRC.length + 1)] = n
        total += n
      }
    }
  }
  return { total, byFile }
}

interface Baseline { arbitraryTextSizes: number }

function loadBaseline(): Baseline {
  const raw = readFileSync(join(SRC, 'design/typeRoleAdoption.baseline.json'), 'utf8')
  return JSON.parse(raw) as Baseline
}

describe('type-role ratchet (arbitrary rem text sizes may only shrink)', () => {
  const base = loadBaseline()
  const live = countArbitrarySizes()

  it(`arbitrary text-size count must not exceed the baseline (${loadBaseline().arbitraryTextSizes})`, () => {
    const worst = Object.entries(live.byFile).sort((a, b) => b[1] - a[1]).slice(0, 5)
      .map(([f, n]) => `  ${f}: ${n}`).join('\n')
    expect(
      live.total,
      `Arbitrary rem text-size count rose to ${live.total} (baseline ${base.arbitraryTextSizes}). ` +
        `Set text through a data-type role (design/tokens.css) instead of a hand-rolled size — ` +
        `or, if migrating DOWN, lower arbitraryTextSizes in typeRoleAdoption.baseline.json in the ` +
        `same commit. Heaviest files:\n${worst}`,
    ).toBeLessThanOrEqual(base.arbitraryTextSizes)
  })

  it('the scanner is not vacuously green — the regex still recognizes the shapes', () => {
    // Anti-rot self-test (the zLayerScale idiom): the pattern must match the
    // literal shapes the rail exists to catch and must NOT match role/token forms.
    for (const bad of ['text-[0.8125rem]', 'text-[0.75rem]', 'text-[1.0625rem]']) {
      expect(ARBITRARY_TEXT_SIZE.test(bad), `must match "${bad}"`).toBe(true)
    }
    for (const good of ['data-type="body-s"', 'text-[var(--fs-caption)]', 'text-on-surface']) {
      expect(ARBITRARY_TEXT_SIZE.test(good), `must NOT match "${good}"`).toBe(false)
    }
    // And the live scan must still see the known migration stock — an empty scan
    // with a ~2800 baseline means the walker broke, not that the tree got clean.
    expect(live.total, 'scan found no literals at all — walker or regex rot').toBeGreaterThan(0)
  })
})
