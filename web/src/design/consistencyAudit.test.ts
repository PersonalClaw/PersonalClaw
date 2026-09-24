import { describe, it, expect } from 'vitest'
import { buildAuditPayload, scanDrift } from './consistencyAudit.report'

// ── Design-System Consistency Audit — reporter checks (S1/T1.1) ─────────────
// This is a REPORTER, not a ratchet: it never fails on drift. It runs the
// scanner over web/src and asserts the inventory it produces is well formed.
//
// It does NOT write anything. It used to write docs/design/consistency-audit.json
// as a side effect, which made `npm test --workspace web` leave a modified tracked
// file behind on a clean clone — the local gate everybody runs immediately before
// committing, so the churn landed exactly where `git add -A` would sweep it into an
// unrelated commit (issue 261). At least eight plan execution logs record the diff
// being regenerated and discarded as noise, and the committed copy went stale anyway.
//
// Generation now lives in consistencyAudit.generate.test.ts, behind an explicit
// opt-in: `npm run audit:consistency`. A test verifies; a script generates.

describe('consistency-audit: drift reporter (measure only, never fails on drift)', () => {
  it('scans a meaningful number of source files', () => {
    expect(scanDrift().totals.filesScanned).toBeGreaterThan(100)
  })

  it('builds a well-formed drift inventory', () => {
    const payload = buildAuditPayload()
    expect(payload.byCategory).toHaveProperty('color')
    expect(Array.isArray(payload.ranked)).toBe(true)
    // Global a11y safety nets must be present (they're the app-wide coverage).
    expect(payload.a11y.hasGlobalReducedMotion).toBe(true)
    expect(payload.a11y.hasGlobalFocusRing).toBe(true)
  })

  it(
    'is a pure function of the tree, so two runs agree',
    () => {
      // 🪤 The property that makes the artifact committable at all, and the one a
      // `generatedAt` broke. If this ever fails, the inventory has picked up something
      // that is not the tree — a clock, a path, an env var — and committing it will churn
      // forever no matter where the write lives.
      //
      // Compared as JSON rather than with a deep-equal on the object graph: the payload
      // carries every drift hit in web/src, and `toEqual` walking it twice is far slower
      // than one string compare of the exact bytes that would be written.
      expect(JSON.stringify(buildAuditPayload())).toBe(JSON.stringify(buildAuditPayload()))
    },
    // Two full scans of web/src. The generous ceiling is deliberate: this machine
    // routinely runs several suites at once, and the default 20s made this the first
    // thing to time out under load — a flake in a purity check reads as impurity.
    120_000,
  )
})
