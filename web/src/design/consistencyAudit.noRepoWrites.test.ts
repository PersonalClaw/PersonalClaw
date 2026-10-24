// @module-tag tree-scan
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
import { readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { WRITE_ENV } from './consistencyAudit.generate.test'
import { filesUnder } from '../test/sourceTree'

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
  'design/schemeSheets.test.ts':
    'The generator for the committed scheme override sheets (design/schemes/*.css), gated on ' +
    'PC_WRITE_SCHEMES=1 and invoked only by `npm run generate:schemes`. A plain test run only ' +
    'compares the committed sheets with what their tables generate.',
  'app/featureMap.test.tsx':
    'The generator for the committed feature map (docs/feature-map.md), gated on ' +
    'PC_WRITE_FEATURE_MAP=1 and invoked only by `npm run docs:feature-map`. A plain test run only ' +
    'compares the committed map with what the router, the sidebar, the palette and the links give.',
  'buddyRig/poseSources.test.ts':
    'The generator for the committed buddy pose sources and contact sheets ' +
    '(docs/design/buddy/poses/), gated on PC_WRITE_BUDDY_POSES=1. A plain test run only compares ' +
    'the committed drawings with what the pose set draws.',
}

/** Test files that write only inside a scratch directory of their own, under the OS temp
 *  directory, and so never into the repository at all. Each creates it with
 *  `mkdtempSync(join(tmpdir(), …))` and removes it after every test, which is checked below.
 *  The same bar as above: a row is a decision with a reason, not a way to make this green. */
const MAY_WRITE_SCRATCH: Record<string, string> = {
  'design/e2eIsolation.test.ts':
    'Writes two Playwright configs (the real projects, and one with the stateful set planted ' +
    'into the shared project) for `playwright test --list`, to prove the isolation reporter ' +
    'fails a run that shares a stateful spec; and stand-in specs with a config running the real ' +
    'projects over them, to prove the stateful passes run in order and a skipped pass reds the run.',
  'design/e2eMergedRun.test.ts':
    'Writes planted merged reports for the run check, and leg folders of stand-in blobs for the ' +
    'leg merge, to prove each reds on the run it exists to refuse.',
  'design/e2eStaleDist.test.ts':
    'Lays out a scratch repository of the repository\'s shape (the freshness script, web/ sources, a ' +
    'stamped dist and a preview that serves it) and a stand-in spec, to prove the real config\'s preview ' +
    'server refuses a stale or unstamped dist before any test runs, naming the freshness check.',
  'design/platformBaselines.test.ts':
    'Writes planted CI reports, and the -linux set `e2e/platform-baselines.mjs` writes from them, under ' +
    'a temporary folder, to prove it writes each surface\'s two files and refuses a report missing a theme or a tree.',
  'design/uiQualityReport.test.ts':
    'Writes planted merged reports, and a step summary file, under a temporary folder for the UI ' +
    'quality check\'s report, to prove it names every failed step and reds on a report that measured nothing.',
  'app/thirdPartyNotices.test.ts':
    'Lays out a throwaway repository (a package-lock.json, installed packages and a web ' +
    'build) for the third-party notices plugin to read, and to write its notices into.',
  'design/e2eRunPlaces.test.ts':
    'Makes browser-run homes, output folders and held ports in a scratch folder of its own, to ' +
    'prove two runs get two of each. Its real-config listing makes one empty run folder under ' +
    'the gitignored web/test-results/, and the test checks the run removed it again.',
  'design/e2eHarnessHome.test.ts':
    'Lays out a stand-in user home, a symlink into it and a stand-in CLI script, to prove a spec ' +
    'refuses an unset or default gateway home before the CLI starts.',
  'design/pinnedRun.test.ts':
    'Lays out a scratch web/ (the pin, the lock, and the manifest an update writes) for ' +
    '`e2e/run-pinned.mjs` to read and write, to prove each refusal and that an update writes the ' +
    'manifest from the probe while a compare holds the probe to it.',
  'design/uiMatrixSteps.test.ts':
    'Writes planted Playwright JSON reports for `e2e/assert-no-route-skips.mjs` to read, to prove ' +
    'the report checker fails a skipped scan, a missing scan and a scan short of one matrix step.',
  'test/sourceTree.test.ts':
    'Lays out small throwaway trees to prove the one walker the tree scans share lists them ' +
    'in the order the walkers it replaced did, never follows a link, and remembers a listing ' +
    'and a read for the rest of the test file.',
  'design/surfaceTreeHostPaths.test.ts':
    'Makes a home folder and a link to it whose target path ends in the link\'s own path, as a ' +
    'macOS temp folder\'s real path does, to prove the surface matrix folds both forms of a run\'s ' +
    'home to `<e2e home>` in a committed accessibility-tree baseline.',
}

function testFiles(dir: string): string[] {
  return filesUnder(dir, (entry) => /\.test\.tsx?$/.test(entry))
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
