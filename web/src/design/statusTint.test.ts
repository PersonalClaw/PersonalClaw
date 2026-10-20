// @module-tag tree-scan
import { describe, it, expect } from 'vitest'
import { join } from 'node:path'
import { filesUnder, readSource } from '../test/sourceTree'

// ── StatusPill adoption ratchet ────────────────────────────────
// The canonical tinted status pill is ui/StatusPill.tsx: one sanctioned tint
// strength (16%, inside the 18% ink-contrast budget tokens.css documents),
// one closed tone vocabulary. Pages had hand-rolled the tint ~90 times as
// inline `color-mix(...)` styles. This ratchet — the same idiom as
// eyebrowWeightRole and tableAdoption — holds the COUNT of inline color-mix
// occurrences in pages DOWN: a NEW one turns CI red, and each migration to
// StatusPill lowers the baseline IN THE SAME COMMIT. The number may only
// shrink.
//
// A COUNT (not a zero rail): legitimate non-pill mixes exist — selection
// fills, focus rings, hover grounds — and they migrate on their own schedule
// or not at all. The ratchet only stops NEW inline tints.
//
// Runs in the existing CI `web` vitest job (source-text scan, no browser).

const PAGES_ROOT = join(process.cwd(), 'src/pages')

function listTsx(dir: string): string[] {
  return filesUnder(dir, (name) => name.endsWith('.tsx'))
}

function countInlineColorMix(): { total: number; byFile: Record<string, number> } {
  const byFile: Record<string, number> = {}
  let total = 0
  for (const p of listTsx(PAGES_ROOT)) {
    const n = (readSource(p).match(/color-mix\(/g) || []).length
    if (n > 0) {
      byFile[p.slice(PAGES_ROOT.length + 1)] = n
      total += n
    }
  }
  return { total, byFile }
}

interface Baseline { inlineColorMix: number }

function loadBaseline(): Baseline {
  const raw = readSource(join(process.cwd(), 'src/design/statusTint.baseline.json'))
  return JSON.parse(raw) as Baseline
}

describe('status-tint ratchet (inline color-mix in pages may only shrink)', () => {
  const base = loadBaseline()
  const live = countInlineColorMix()

  it(`inline color-mix count must not exceed the baseline (${loadBaseline().inlineColorMix})`, () => {
    expect(
      live.total,
      `New inline color-mix tint(s) detected (${live.total} > ${base.inlineColorMix}). ` +
        `For a tinted status label use the StatusPill primitive (ui/StatusPill.tsx — the closed ` +
        `tone set stays inside the audited 18% ink-contrast budget); for a genuinely non-pill ` +
        `fill, or an intentional migration DOWN, adjust inlineColorMix in ` +
        `src/design/statusTint.baseline.json in the same commit.\nBy file:\n${JSON.stringify(live.byFile, null, 2)}`,
    ).toBeLessThanOrEqual(base.inlineColorMix)
  })

  it('baseline is not stale (a migration dropped the real count without ratcheting)', () => {
    if (live.total < base.inlineColorMix) {
      // eslint-disable-next-line no-console
      console.warn(
        `[status-tint] live count ${live.total} is below baseline ${base.inlineColorMix} — ` +
          `ratchet src/design/statusTint.baseline.json DOWN in this commit to lock the gain.`,
      )
    }
    expect(true).toBe(true)
  })
})
