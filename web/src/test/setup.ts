// Setup for the `dom` project only (`vitest.config.ts`): jest-dom matchers (toBeInTheDocument,
// toBeDisabled, …), automatic React cleanup between tests, and stand-ins for what jsdom lacks.
// The module-state resets every file needs, in either project, are `resetModuleState.ts`, listed
// before this file; Vitest runs `afterEach` hooks last registered first, so the cleanup below runs
// before those resets, as it did when both lived here.
import '@testing-library/jest-dom/vitest'
import * as webStreams from 'node:stream/web'
import { cleanup, configure } from '@testing-library/react'
import { afterAll, afterEach } from 'vitest'

// The `dom` project runs each file in a VM context (`pool: 'vmForks'`, vitest.config.ts), whose
// globals are jsdom's: Node's web streams (ReadableStream, TransformStream, ...) are not among them.
// A file that builds a jsdom of its own (`src/ui/widget/reactFrame.test.ts`) loads jsdom's bundled
// undici, which needs them as it loads. Each one the context lacks is Node's own, as a file under
// the forks pool saw it; one the context has is left as it is.
for (const [name, value] of Object.entries(webStreams)) {
  if (!(name in globalThis)) {
    Object.defineProperty(globalThis, name, { value, writable: true, configurable: true })
  }
}

// `waitFor`'s default timeout is 1000 ms, and it was never raised when this suite's real
// wall-clock was measured. #1675 raised vitest's `testTimeout` 5 s → 20 s on the finding that
// ~400 files across 18 workers inflate per-test wall-clock roughly 3x under contention (the
// `*LoadError.test.tsx` family measured 1012 ms alone vs 3371 ms in the full suite) — but the
// per-assertion budget inside `waitFor` kept its 1 s default, so every async assertion still
// had a 1 s window in a suite running ~3x slower.
//
// That is the whole mechanism behind the CI-only dialog-teardown flake ("a DISMISSED
// confirmation writes nothing" / "cancelling fires NO request", ~15 appearances, always green
// locally and on a re-run). Measured here: those dialogs do NOT close synchronously even with
// an act-wrapped click — dropping the `waitFor` fails deterministically — so the close is
// genuinely async and the 1 s budget was the only thing making it flaky.
//
// This raises a CEILING, not an assertion: a genuinely stuck update still fails, just later,
// and still well inside the 20 s per-test budget.
configure({ asyncUtilTimeout: 5_000 })

// jsdom has no ResizeObserver, but the responsive header cluster (HeaderActions) observes its
// container on mount — so any component that renders a header controls group crashes without this.
// A no-op stub is the right fixture: it never fires, so the cluster stays at its full tier, which
// is exactly the widest, all-controls-visible render a test wants to assert against.
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver
}

// jsdom ships no `matchMedia` either, and rendering ANY `useIsMobile` consumer (the shell,
// the composer, the chat find bar) throws without it — `useIsMobile` reads the query
// unguarded on the very first render, so the failure is a crash, not a wrong layout.
// Same shape of fixture as ResizeObserver above, and the same reasoning about its default:
// a stub that matches NOTHING renders the desktop / full-motion / dark branch, which is the
// widest render a test wants to assert against. It is also exactly what the production
// readers already fall back to — every one of them (`design/motion.ts`, `app/theme.tsx`,
// `useStreamCoalescer`) guards `typeof window.matchMedia !== 'function'` and treats it as
// false — so this changes no existing expectation, it only stops the crash.
// Installed only when absent, and left configurable, so a test that stubs its own
// matchMedia (DegradedChip, DotGlow, SpotlightTour) still wins.
if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string): MediaQueryList => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList,
  })
}

// Nor `Path2D`, and the backdrop's default dot glyph (`ui/DotGlow`'s claw) fills the mark's outline
// as one, so a test that paints the default backdrop through a stubbed 2D context throws without it.
// The stand-in remembers only the path data it was built from: a stubbed context never rasterizes,
// so that is all there is to read back (`DotGlow.glyphs.test.tsx` does). Its path methods are
// no-ops, so a library that sees `Path2D` and builds paths with it (mermaid's graph renderer checks
// for it) behaves as in a browser that draws nowhere. Installed only when absent.
if (typeof globalThis.Path2D === 'undefined') {
  globalThis.Path2D = class {
    d: unknown
    constructor(d?: unknown) { this.d = d }
    addPath(): void {}
    arc(): void {}
    arcTo(): void {}
    bezierCurveTo(): void {}
    closePath(): void {}
    ellipse(): void {}
    lineTo(): void {}
    moveTo(): void {}
    quadraticCurveTo(): void {}
    rect(): void {}
    roundRect(): void {}
  } as unknown as typeof Path2D
}

afterEach(() => { cleanup() })

// The `dom` project runs its files in VM contexts (`pool: 'vmForks'`, vitest.config.ts), many in
// one process. Vitest holds a `vi.mock` or `vi.doMock` until the next import applies it, in a queue
// that process shares, so a file whose last tests queue a mock and import nothing after it hands
// that mock to the next file the process runs, which then imports the mock in place of the module.
// Under the forks pool the queue ended with the file's process; here whatever a file leaves queued
// is dropped as it ends. A Vitest that no longer keeps the queue there fails every file, loudly,
// rather than let the leak back in unseen.
afterAll(() => {
  const mocker = (globalThis as { __vitest_mocker__?: { constructor: { pendingIds?: unknown } } })
    .__vitest_mocker__
  const queued = mocker?.constructor.pendingIds
  if (!Array.isArray(queued)) {
    throw new Error("setup.ts: Vitest's queue of pending mocks is not where this file drops a file's leftovers; re-check that a VM context's mocks end with its file")
  }
  queued.length = 0
})
