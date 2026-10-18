import type { FullConfig, Reporter, Suite, TestCase } from '@playwright/test/reporter'
import { basename } from 'node:path'
import { SPEC_ISOLATION, isolationViolation } from './isolation'

// ── Every spec's isolation mode, in the run's own report, and enforced ─────────────────────────
//
// Listed FIRST in `playwright.config.ts`'s reporters, so the annotations it adds are on every test
// before the JSON and blob reporters read them, and so a merged shard report carries them too.
// Each test gets an `isolation` annotation (`shared` or `serial`, and why), and a test of the
// stateful set's second pass also gets the seed its order came from.
//
// A spec running in a project its mode does not allow — a stateful spec in the shared parallel
// project, say — fails the run, named, before its result could be trusted. `--list` reaches this
// too, which is how the planted control in `src/design/e2eIsolation.test.ts` drives it.

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

export default class IsolationReporter implements Reporter {
  private violations: string[] = []
  private seed = ''

  onBegin(config: FullConfig, suite: Suite): void {
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
    if (order.length) {
      console.log(`isolation: the stateful set runs serially, then again in seed ${this.seed}'s order: ${order.join(' → ')}`)
    }
  }

  // A test that RUNS can have its annotations replaced by the worker's when it ends (a
  // `describe.configure({ mode: 'serial' })` sends its own), so the ones added in `onBegin` would
  // reach the report only for a test that never ran. Added again here, before the JSON and blob
  // reporters (listed after this one) record the result.
  onTestEnd(test: TestCase): void {
    annotate(test, this.seed)
  }

  async onEnd(): Promise<{ status: 'failed' } | undefined> {
    if (!this.violations.length) return undefined
    console.error(`\n✗ isolation: ${this.violations.length} spec(s) ran where their mode forbids:`)
    for (const why of this.violations) console.error(`    ${why}`)
    return { status: 'failed' }
  }
}
