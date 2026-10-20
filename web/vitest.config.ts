import { configDefaults, defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { join } from 'node:path'
import { census } from './src/test/domCensus'
import { indexTree } from './src/test/sourceTreeIndex'

// Vitest config for the web app's unit/component tests. Kept separate from
// vite.config.ts (which carries the dev-server proxy/token plumbing that has no
// place in a test run). Integration flows that need a live backend + WS
// (send→stream→render, stop, reconnect) are covered by the as-a-user
// Chrome DevTools validation pass, not mocked here.
//
// Two projects. `dom` gives component tests a jsdom window and the DOM setup (`setup.ts`:
// jest-dom matchers, React cleanup, stand-ins for what jsdom lacks); `node` runs the test files
// that never reach a DOM, which then stop paying for one: building a jsdom window was 42% of a
// whole run's time. Which `.test.ts` files are `node` is decided from their source, and what they
// import, by `src/test/domCensus.ts`; every `.test.tsx` file is `dom`. Both projects reset the
// module state every file needs (`resetModuleState.ts`).
//
// The tree is listed once, here: the census reads that listing, and every test file gets it
// (`provide`), so a tree-scanning file reads `src/` and `e2e/` off it instead of walking them
// again (`src/test/sourceTree.ts`).
const WEB = import.meta.dirname
const TREE = indexTree(WEB)
const NODE_FILES = census(WEB, TREE[join(WEB, 'src')]).node

/** What both projects share. */
const shared = {
  globals: true,
  // Vitest turns every stylesheet import into an empty string, a `?raw` read included. Widget
  // styles compile Tailwind from its stylesheet's TEXT (`src/ui/widget/widgetStyles.ts`), and an
  // empty stylesheet compiles to nothing at all — so that one read is left to Vite.
  css: { include: [/\/tailwindcss\/index\.css\?raw$/] },
  // Vitest's default 5 s is a WALL-CLOCK budget, and this suite runs ~400 files
  // across 18 workers — so wall-clock per test inflates roughly 3x under
  // contention. Measured on the `*LoadError.test.tsx` family, which polls the DOM
  // through `waitFor` after a rejected fetch: **1012 ms running alone, 3371 ms in
  // the full suite.** Against a 5 s budget that is a ~1.5x margin, and adding any
  // two test files anywhere tipped it over — `main` was green at 396 files and red
  // at 398, in four files that the change under test never touched.
  //
  // This raises the ceiling, not any assertion: a genuinely hung test still fails,
  // it just fails later. The alternative — sprinkling per-test timeouts on whichever
  // file tipped this week — leaves the same landmine for the next one, because the
  // margin is a property of the SUITE, not of those tests.
  testTimeout: 20_000,
  hookTimeout: 20_000,
  // A test file that reads the source tree says so with `// @module-tag tree-scan`, and its
  // tests get this budget instead of the one above. It reads the tree through
  // `src/test/sourceTree.ts`, which takes the run's listing of `src/` and `e2e/` (made once, above)
  // and reads each file once per test file, so the first test to ask
  // pays for the files it reads and the rest read them from memory: on a loaded machine that
  // first read is what can outrun 20 s, and a scan cut off part way proves nothing either way.
  // `src/test/sourceTree.test.ts` holds every tree scan to this tag and to that one reader.
  tags: [
    {
      name: 'tree-scan',
      description: 'Reads the source tree through src/test/sourceTree.ts',
      timeout: 60_000,
    },
  ],
}

export default defineConfig({
  plugins: [react()],
  test: {
    // The run's listing of `src/` and `e2e/`, for every tree-scanning file; the `globalSetup`
    // lists them again before each rerun in watch mode (`sourceTreeIndex.ts`).
    provide: { sourceTree: TREE },
    globalSetup: ['./src/test/sourceTreeIndex.ts'],
    // Vitest 5 uses maxWorkers for every pool, including the default forks pool.
    // Keep bare local invocations from claiming every core; CI sets Vitest's built-in
    // VITEST_MAX_WORKERS to the runner's core count.
    maxWorkers: 3,
    // Under CI each test's result is also written as JUnit, at the repository root's reports/,
    // which `tools/ci run-job` reads to rerun a failed test once, alone, to classify it
    // (tools/ci/jobs.py, VITEST_JUNIT). The job stays red either way. JUnit is added to Vitest's
    // own default reporters, its github-actions annotations on a CI runner among them; locally
    // neither key is set, so Vitest's defaults stand untouched.
    ...(process.env.CI
      ? {
          reporters: [...configDefaults.reporters, 'junit'],
          outputFile: { junit: join(WEB, '..', 'reports', 'junit-web-unit.xml') },
        }
      : {}),
    projects: [
      {
        extends: true,
        test: {
          ...shared,
          name: 'dom',
          environment: 'jsdom',
          setupFiles: ['./src/test/resetModuleState.ts', './src/test/setup.ts'],
          include: ['src/**/*.{test,spec}.{ts,tsx}'],
          exclude: NODE_FILES,
        },
      },
      {
        extends: true,
        test: {
          ...shared,
          name: 'node',
          environment: 'node',
          setupFiles: ['./src/test/resetModuleState.ts'],
          include: NODE_FILES,
        },
      },
    ],
  },
})
