import { join } from 'node:path'
import type { TestProject } from 'vitest/node'
import { listTree, type TreeIndex } from './sourceTree'

// The run's index of the source tree: `src/` and `e2e/`, listed ONCE per run and handed to every
// test file, which `sourceTree.ts` reads instead of walking either folder again. Texts are not
// indexed: `sourceTree.ts` says why.
//
// `vitest.config.ts` makes the index (`indexTree`) while it loads, because it needs the listing
// itself to decide which files run without a DOM (`domCensus.ts`), and hands it to the test files
// through `provide`: one walk serves both. This module is the run's `globalSetup`, and lists the
// tree again before each rerun in watch mode, so a file added or removed while it watches is seen
// by the next run's scans. (Which project a file runs in is fixed when the config loads: a
// `.test.ts` file added in watch mode runs in `dom`, the project that can run anything.)

/** The folders indexed, relative to `web/`: every tree a test scans lies under one of them. */
export const INDEXED = ['src', 'e2e'] as const

/** The index of the tree under `web` (absolute). */
export function indexTree(web: string): TreeIndex {
  return listTree(INDEXED.map((folder) => join(web, folder)))
}

export default function setup(project: TestProject): void {
  project.onTestsRerun(() => {
    project.provide('sourceTree', indexTree(project.config.root))
  })
}
