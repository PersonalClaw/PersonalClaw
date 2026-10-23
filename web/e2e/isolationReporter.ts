import type { FullConfig, Reporter, Suite, TestCase, TestResult } from '@playwright/test/reporter'
import { basename } from 'node:path'
import { SPEC_ISOLATION, isolationViolation, sharedHomeViolation } from './isolation'

// ── Every spec's isolation mode, in the run's own report, and enforced ─────────────────────────
//
// Listed FIRST in `playwright.config.ts`'s reporters, so the annotations it adds are on every test
// before the JSON and blob reporters read them, and so a merged shard report carries them too (see
// `onTestEnd` for the half of that a blob report needs).
// Each test gets an `isolation` annotation (`shared` or `serial`, and why), and a test of the
// stateful set's second pass also gets the seed its order came from.
//
// A spec running in a project its mode does not allow — a stateful spec in the shared parallel
// project, say, or a spec that runs alone in a run beside another project's — fails the run, named,
// before its result could be trusted. `--list` reaches this
// too, which is how the planted control in `src/design/e2eIsolation.test.ts` drives it.
//
// 🔑 AND A TEST THAT NEVER RAN FAILS THE RUN, NAMED. A project whose dependency failed is skipped
// whole, and so is a teardown that has a teardown of its own; the second case still exits 0, which
// is how the stateful-only run lost its whole second pass without one red. A test that skipped
// itself (`test.skip`) has a result, so it is not one of these.

export function annotate(test: TestCase, seed: string): string {
  const file = basename(test.location.file)
  const project = test.parent.project()?.name ?? ''
  const entry = SPEC_ISOLATION[file]
  if (entry && !test.annotations.some((a) => a.type === 'isolation')) {
    test.annotations.push({ type: 'isolation', description: `${entry.mode}: ${entry.why}` })
  }
  if (project.startsWith('stateful-again-') && !test.annotations.some((a) => a.type === 'stateful-seed')) {
    test.annotations.push({ type: 'stateful-seed', description: seed })
  }
  return isolationViolation(project, file)
}

/** `project › file › title`, as the run checks name a test. */
function named(test: TestCase): string {
  return [test.parent.project()?.name ?? '', basename(test.location.file), ...test.titlePath().slice(3)].join(' › ')
}

export default class IsolationReporter implements Reporter {
  private violations: string[] = []
  private seed = ''
  private suite: Suite | undefined
  // `--list` runs no test, so every test there has no result. Playwright hands each reporter its
  // mode as `_mode` (1.64); e2eIsolation.test.ts's `--list` of the real projects reds if it stops.
  private readonly listing: boolean

  constructor(options?: { _mode?: string }) {
    this.listing = options?._mode === 'list'
  }

  onBegin(config: FullConfig, suite: Suite): void {
    this.suite = suite
    this.seed = String(config.metadata?.statefulSeed ?? '')
    const order = (config.metadata?.statefulOrder as string[] | undefined) ?? []
    const seen = new Set<string>()
    for (const test of suite.allTests()) {
      const why = annotate(test, this.seed)
      if (why && !seen.has(why)) {
        seen.add(why)
        this.violations.push(why)
      }
    }
    const shared = sharedHomeViolation(suite.allTests().map((test) => test.parent.project()?.name ?? ''))
    if (shared) this.violations.push(shared)
    if (order.length) {
      console.log(`isolation: the stateful set runs serially, then again in seed ${this.seed}'s order: ${order.join(' → ')}`)
    }
  }

  // A test that RUNS can have its annotations replaced by the worker's when it ends (a
  // `describe.configure({ mode: 'serial' })` sends its own), so the ones added in `onBegin` would
  // reach the report only for a test that never ran. Added again here, before the JSON and blob
  // reporters (listed after this one) record the result.
  //
  // 🪤 AND ON THE RESULT, FOR A MERGED REPORT. A blob report carries a test's annotations at its end
  // as its RESULT's, and `merge-reports` makes those the test's own: a test whose worker sent
  // annotations of its own reached the merged report of a sharded run without the ones added here
  // (measured: the pwa and staleWrite tests, 6 of them, while the leg's own JSON report had them).
  // So a result that carries annotations gets these too.
  onTestEnd(test: TestCase, result?: TestResult): void {
    annotate(test, this.seed)
    const own = result?.annotations
    if (!own?.length) return
    for (const a of test.annotations) {
      if ((a.type === 'isolation' || a.type === 'stateful-seed') && !own.some((o) => o.type === a.type)) own.push(a)
    }
  }

  async onEnd(): Promise<{ status: 'failed' } | undefined> {
    const unrun = this.listing ? [] : (this.suite?.allTests() ?? []).filter((test) => !test.results.length).map(named)
    if (!this.violations.length && !unrun.length) return undefined
    if (this.violations.length) {
      console.error(`\n✗ isolation: ${this.violations.length} spec(s) ran where their mode forbids:`)
      for (const why of this.violations) console.error(`    ${why}`)
    }
    if (unrun.length) {
      console.error(
        `\n✗ ${unrun.length} test(s) did not run: a project they need failed, or the run stopped before them.` +
          ' A test that never started measured nothing, so the run is red:',
      )
      for (const name of unrun.slice(0, 20)) console.error(`    ${name}`)
      if (unrun.length > 20) console.error(`    and ${unrun.length - 20} more`)
    }
    return { status: 'failed' }
  }
}
