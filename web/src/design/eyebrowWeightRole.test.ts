import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { countUppercaseTrackedEyebrows } from './consistencyAudit.report'

// ── Eyebrow weight-role ratchet (design-system consistency, CD-02) ──────────
// The Weight-First rule (web/DESIGN.md §3/§6) makes emphasis a variable-weight
// STEP, never uppercase-with-tracking. Section eyebrows and chip labels had
// drifted to the uppercase, letter-spaced treatment app-wide (150+ hits) with no
// guard. The canonical treatment is now the sentence-case `caption` type role,
// vended as the Eyebrow primitive (ui/Eyebrow.tsx). This ratchet — the same idiom
// as the primitive-adoption and inline-font-weight rails in
// primitiveAdoption.test.ts — holds the count of remaining tracked eyebrows DOWN:
// a NEW one turns CI red, and each migration to Eyebrow lowers the baseline IN
// THE SAME COMMIT. The number may only shrink.
//
// Runs in the existing CI `web` vitest job (source-text scan, no browser).

interface Baseline { uppercaseTrackedEyebrows: number }

function loadBaseline(): Baseline {
  const raw = readFileSync(join(process.cwd(), 'src/design/eyebrowWeightRole.baseline.json'), 'utf8')
  const j = JSON.parse(raw)
  return { uppercaseTrackedEyebrows: j.uppercaseTrackedEyebrows }
}

describe('eyebrow weight-role ratchet (uppercase-tracked eyebrows may only shrink)', () => {
  const base = loadBaseline()
  const live = countUppercaseTrackedEyebrows()

  it(`uppercase-tracked eyebrow count must not exceed the baseline (${base.uppercaseTrackedEyebrows})`, () => {
    expect(
      live.total,
      `New uppercase-tracked eyebrow(s) detected (${live.total} > ${base.uppercaseTrackedEyebrows}). ` +
        `The Weight-First rule (web/DESIGN.md §3/§6) bans uppercase-with-tracking — use the Eyebrow ` +
        `primitive (ui/Eyebrow.tsx: the sentence-case 'caption' role), or if this is an intentional ` +
        `migration DOWN, lower uppercaseTrackedEyebrows in src/design/eyebrowWeightRole.baseline.json ` +
        `in the same commit.\nOffenders:\n${JSON.stringify(live.byFile, null, 2)}`,
    ).toBeLessThanOrEqual(base.uppercaseTrackedEyebrows)
  })

  it('baseline is not stale (a migration dropped the real count without ratcheting)', () => {
    // Soft nudge, like windowedListAdoption: if the live count fell below the
    // baseline, the baseline should be ratcheted down in that commit to lock the
    // gain. Warn, don't fail hard, to avoid blocking unrelated work.
    if (live.total < base.uppercaseTrackedEyebrows) {
      // eslint-disable-next-line no-console
      console.warn(
        `[eyebrow-weight-role] live count ${live.total} is below baseline ` +
          `${base.uppercaseTrackedEyebrows} — ratchet src/design/eyebrowWeightRole.baseline.json DOWN ` +
          `to lock in the migration.`,
      )
    }
    expect(live.total).toBeLessThanOrEqual(base.uppercaseTrackedEyebrows)
  })

  it('the ratchet is not vacuous — it scanned the tree and found the frozen backlog', () => {
    expect(base.uppercaseTrackedEyebrows).toBeGreaterThan(0)
    expect(Object.keys(live.byFile).length).toBeGreaterThan(0)
  })
})
