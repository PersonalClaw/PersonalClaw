import { describe, it, expect } from 'vitest'
import { writeFileSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { scanDrift, rankFiles, scanA11y } from './consistencyAudit.report'

// ── Design-System Consistency Audit — reporter runner (S1/T1.1) ────────────
// This is a REPORTER, not a ratchet: it never fails on drift. It runs the
// scanner over web/src and writes a machine-readable JSON inventory to
// docs/design/consistency-audit.json (repo-root relative). The S1 audit doc
// (docs/design/consistency-audit.md) is authored from that data. The only
// assertions here are sanity checks that the scanner actually ran.

describe('consistency-audit: drift reporter (measure only, never fails on drift)', () => {
  const res = scanDrift()
  const ranked = rankFiles(res)
  const a11y = scanA11y()

  it('scans a meaningful number of source files', () => {
    expect(res.totals.filesScanned).toBeGreaterThan(100)
  })

  it('emits the drift inventory JSON for the audit doc', () => {
    // web/ package dir is process.cwd(); repo root is one up.
    const outDir = join(process.cwd(), '..', 'docs', 'design')
    mkdirSync(outDir, { recursive: true })
    // NO timestamp. This artifact is COMMITTED, so it must be a pure function of the tree it
    // scans: a `generatedAt` made every suite run rewrite the file, which meant a real data
    // refresh could never be told apart from noise. The consequence is in the plan logs — the
    // diff was discarded as "pure timestamp churn" at least eight times across weeks, and the
    // committed copy went stale as a direct result (filesScanned drifted 310 → 442 → 518 → 523
    // → 527 while nobody committed a refresh). Git already records when a file changed.
    const payload = {
      totals: res.totals,
      byCategory: res.byCategory,
      ranked: ranked.slice(0, 40),
      a11y: {
        outlineNoneCount: a11y.outlineNoneCount,
        outlineNoneFiles: a11y.outlineNoneFiles.length,
        localFocusVisibleFiles: a11y.localFocusVisibleFiles.length,
        reducedMotionFiles: a11y.reducedMotionFiles.length,
        animatedFiles: a11y.animatedFiles,
        hasGlobalReducedMotion: a11y.hasGlobalReducedMotion,
        hasGlobalFocusRing: a11y.hasGlobalFocusRing,
      },
      byFile: res.byFile,
      primitivesByFile: res.primitivesByFile,
      drift: res.drift,
    }
    writeFileSync(join(outDir, 'consistency-audit.json'), JSON.stringify(payload, null, 2) + '\n', 'utf8')
    // Sanity: the object serialized and has the expected shape.
    expect(payload.byCategory).toHaveProperty('color')
    expect(Array.isArray(payload.ranked)).toBe(true)
    // Global a11y safety nets must be present (they're the app-wide coverage).
    expect(payload.a11y.hasGlobalReducedMotion).toBe(true)
    expect(payload.a11y.hasGlobalFocusRing).toBe(true)
  })
})
