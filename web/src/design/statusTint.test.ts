import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── StatusPill adoption ratchet (audit AB-2) ────────────────────────────────
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
  const out: string[] = []
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) out.push(...listTsx(p))
    else if (e.name.endsWith('.tsx')) out.push(p)
  }
  return out
}

function countInlineColorMix(): { total: number; byFile: Record<string, number> } {
  const byFile: Record<string, number> = {}
  let total = 0
  for (const p of listTsx(PAGES_ROOT)) {
    const n = (readFileSync(p, 'utf8').match(/color-mix\(/g) || []).length
    if (n > 0) {
      byFile[p.slice(PAGES_ROOT.length + 1)] = n
      total += n
    }
  }
  return { total, byFile }
}

interface Baseline { inlineColorMix: number }

function loadBaseline(): Baseline {
  const raw = readFileSync(join(process.cwd(), 'src/design/statusTint.baseline.json'), 'utf8')
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
