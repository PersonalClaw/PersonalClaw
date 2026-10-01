import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Vitest config for the web app's unit/component tests. Kept separate from
// vite.config.ts (which carries the dev-server proxy/token plumbing that has no
// place in a test run). jsdom gives component tests a DOM; setup wires
// jest-dom matchers. Integration flows that need a live backend + WS
// (send→stream→render, stop, reconnect) are covered by the as-a-user
// Chrome DevTools validation pass, not mocked here.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    // Vitest turns every stylesheet import into an empty string, a `?raw` read included. Widget
    // styles compile Tailwind from its stylesheet's TEXT (`src/ui/widget/widgetStyles.ts`), and an
    // empty stylesheet compiles to nothing at all — so that one read is left to Vite.
    css: { include: [/\/tailwindcss\/index\.css\?raw$/] },
    // Vitest 5 uses maxWorkers for every pool, including the default forks pool.
    // Keep bare local invocations from claiming every core; CI can override this
    // through Vitest's built-in VITEST_MAX_WORKERS environment variable.
    maxWorkers: 3,
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
    // `src/test/sourceTree.ts`, which lists each directory and reads each file once per test file,
    // so the first test to ask pays for all of `src/` (2,000+ files) and the rest read it from
    // memory: on a loaded machine that first read is what can outrun 20 s, and a scan cut off part
    // way proves nothing either way. `src/test/sourceTree.test.ts` holds every tree scan to this
    // tag and to that one reader.
    tags: [
      {
        name: 'tree-scan',
        description: 'Reads the source tree through src/test/sourceTree.ts',
        timeout: 60_000,
      },
    ],
  },
})
