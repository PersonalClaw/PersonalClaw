/** Writes docs/design/consistency-audit.json. Runs ONLY when explicitly asked.
 *
 * `npm run audit:consistency` (from the repo root or the web workspace) sets the env var
 * this file gates on. A plain `npm test --workspace web` skips the write entirely, which
 * is the whole point: the artifact is tracked, and a test that rewrites a tracked file
 * leaves every contributor with a dirty tree at exactly the moment they are about to
 * commit (issue 261).
 *
 * **Why this is a `.test.ts` and not a script.** The scanners are TypeScript, and the
 * repo has no TS runner installed — no `tsx`, no `vite-node`. The alternatives were
 * worse than they look:
 *
 *   * `node web/scripts/write…ts` works on CI (Node 24 strips types unflagged) but not
 *     for a contributor on Node 20 or 22, where the rest of the suite runs fine. A
 *     generator that fails on a supported Node is not a fix.
 *   * Adding `tsx` puts a dependency and a lockfile entry into the tree to move one
 *     `writeFileSync`.
 *
 * So generation reuses the runner that already exists, and the separation that matters —
 * a plain test run writes nothing — is achieved by the gate rather than by the language.
 * The same shape as `e2e:update` next door, which is `playwright test
 * --update-snapshots`: the suite is the tool, the flag is the intent.
 *
 * `consistencyAudit.noRepoWrites.test.ts` is the rail that keeps it this way — it allows
 * exactly this file to write, and only while the write stays behind the gate.
 */

import { describe, it, expect } from 'vitest'
import { writeFileSync, mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { AUDIT_JSON_PATH, buildAuditPayload } from './consistencyAudit.report'

/** The opt-in. Named on the npm script, so the intent is visible in package.json. */
export const WRITE_ENV = 'PC_WRITE_AUDIT'

describe('consistency-audit: inventory generator', () => {
  it.runIf(process.env[WRITE_ENV] === '1')('writes the committed inventory', () => {
    // web/ is process.cwd() under vitest; the artifact lives one level up in docs/.
    const out = join(process.cwd(), ...AUDIT_JSON_PATH)
    mkdirSync(dirname(out), { recursive: true })
    const payload = buildAuditPayload()
    writeFileSync(out, JSON.stringify(payload, null, 2) + '\n', 'utf8')
    // Assert on what was written, so a generator that silently produced an empty
    // inventory fails here rather than committing one.
    expect(payload.totals.filesScanned).toBeGreaterThan(100)
    expect(Array.isArray(payload.ranked)).toBe(true)
  })
})
