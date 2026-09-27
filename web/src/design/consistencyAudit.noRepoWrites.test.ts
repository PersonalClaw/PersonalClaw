/** No test in the web suite may write into the repository.
 *
 * `npm test --workspace web` is the step every contributor runs immediately before
 * committing. Anything it modifies lands in the blast radius of `git add -A`, so a test
 * that writes a tracked file quietly sweeps a generated artifact into an unrelated
 * commit — and on a clean clone it produces a diff the contributor did not make.
 *
 * That happened for months with `docs/design/consistency-audit.json` (issue 261). The
 * cost was not the churn itself but what the churn hid: at least eight plan execution
 * logs record regenerating the file, discarding the diff as noise, and noting the
 * committed copy was *already* stale. A signal everyone has learned to ignore is worse
 * than no signal, and the stale numbers meant the audit could no longer be told from a
 * real drift.
 *
 * Measured when this rail was written: **exactly one** file in the suite wrote to disk.
 * So the allowlist starts at one row and should stay short. This is a rail against a new
 * one appearing, and against the write creeping back into the plain test — the fix for
 * 261 is one `writeFileSync` away from being undone, by someone who reasonably thinks
 * "the test already has the data".
 *
 * 🪤 THE FAKE VERSION OF THIS TEST asserts that `consistencyAudit.test.ts` specifically
 * contains no `writeFileSync`. That passes forever while the next generated artifact
 * grows a writer in some other test file, which is the same bug with a different
 * filename. The rail has to scan the whole suite and name its exceptions.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { WRITE_ENV } from './consistencyAudit.generate.test'

const SRC = join(process.cwd(), 'src')

/** Mutating `node:fs` calls. Read APIs are fine — plenty of tests read fixtures. */
const FS_WRITE = /\b(writeFileSync|appendFileSync|mkdirSync|rmSync|rmdirSync|unlinkSync|cpSync|copyFileSync|renameSync|writeFile|createWriteStream)\s*\(/

/** Test files permitted to write, and the reason each is allowed to.
 *
 * A row here is a decision, never a way to make this test green. The bar: the write must
 * be impossible during a plain `npm test`, which is checked below rather than trusted.
 */
const MAY_WRITE: Record<string, string> = {
  'design/consistencyAudit.generate.test.ts':
    'The generator for the committed drift inventory, gated on ' +
    `${WRITE_ENV}=1 and invoked only by \`npm run audit:consistency\`. A plain test ` +
    'run skips it, so the tree stays clean.',
}

/** Test files that write only inside a scratch directory of their own, under the OS temp
 *  directory, and so never into the repository at all. Each creates it with
 *  `mkdtempSync(join(tmpdir(), …))` and removes it after every test, which is checked below.
 *  The same bar as above: a row is a decision with a reason, not a way to make this green. */
const MAY_WRITE_SCRATCH: Record<string, string> = {
  'app/thirdPartyNotices.test.ts':
    'Lays out a throwaway repository (a package-lock.json, installed packages and a web ' +
    'build) for the third-party notices plugin to read, and to write its notices into.',
}

function testFiles(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) out.push(...testFiles(p))
    else if (/\.test\.tsx?$/.test(entry)) out.push(p)
  }
  return out
}

describe('no test writes into the repository', () => {
  const files = testFiles(SRC)

  it('found the suite it is supposed to be scanning', () => {
    // The vacuity floor. A broken walk, a moved `src/`, or a changed filename convention
    // all produce an empty list, and an empty list satisfies every assertion below.
    expect(files.length).toBeGreaterThan(100)
    expect(files.some((f) => f.endsWith('consistencyAudit.test.ts'))).toBe(true)
    // And the pattern must actually match the thing it is looking for, or the scan is
    // measuring nothing.
    //
    // The samples are CONCATENATED rather than written literally, because this file is
    // inside the corpus it scans: written out in full, the fixture is indistinguishable
    // from a real call and the rail reports itself. It did, twice on the way here — once
    // for the sample, and then again for the comment that explained the sample, since the
    // scan reads comments as readily as code. Splitting the string keeps the file under
    // its own scan, so a genuine write added here is still caught. Same trap as a lint
    // rule whose fixtures live in the linted tree.
    expect(FS_WRITE.test('writeFileSync' + '(out, data)')).toBe(true)
    expect(FS_WRITE.test('mkdir' + 'Sync(dir)')).toBe(true)
    expect(FS_WRITE.test('const x = readFileSync(p)')).toBe(false)
    expect(FS_WRITE.test('readdirSync(dir)')).toBe(false)
  })

  it('no unlisted test file calls a mutating fs API', () => {
    const offenders = files
      .filter((f) => FS_WRITE.test(readFileSync(f, 'utf8')))
      .map((f) => relative(SRC, f).split(/[\\/]/).join('/'))
      .filter((rel) => !(rel in MAY_WRITE) && !(rel in MAY_WRITE_SCRATCH))
    expect(offenders).toEqual([])
  })

  it('every scratch writer writes under a temp directory of its own and removes it', () => {
    for (const [rel, reason] of Object.entries(MAY_WRITE_SCRATCH)) {
      expect(reason.trim().length, `${rel} needs a real reason`).toBeGreaterThan(20)
      const source = readFileSync(join(SRC, rel), 'utf8')
      expect(FS_WRITE.test(source), `${rel} no longer writes — delete its row`).toBe(true)
      expect(source, `${rel} must make its root with mkdtempSync(join(tmpdir(), …))`).toMatch(
        /mkdtempSync\(\s*join\(\s*tmpdir\(\)/,
      )
      expect(source, `${rel} must remove its root after each test`).toMatch(
        /afterEach\([\s\S]*?rmSync\([^)]*recursive: true/,
      )
    }
  })

  it('every allowlisted writer is gated so a plain test run cannot fire it', () => {
    // This is what keeps the allowlist from becoming a hole. A row grants permission to
    // write when ASKED, not permission to write during `npm test`.
    for (const [rel, reason] of Object.entries(MAY_WRITE)) {
      expect(reason.trim().length, `${rel} needs a real reason`).toBeGreaterThan(20)
      const source = readFileSync(join(SRC, rel), 'utf8')
      expect(FS_WRITE.test(source), `${rel} no longer writes — delete its row`).toBe(true)
      expect(source.includes(`process.env[WRITE_ENV]`) || source.includes(WRITE_ENV)).toBe(true)
      expect(source).toMatch(/it\.runIf\(/)
    }
  })

  it('the plain reporter test is not the writer any more', () => {
    // issue 261 pinned directly. The general scan above would catch this too, but naming
    // it means the red says which bug came back instead of just which file changed.
    const source = readFileSync(join(SRC, 'design/consistencyAudit.test.ts'), 'utf8')
    expect(FS_WRITE.test(source)).toBe(false)
  })
})
