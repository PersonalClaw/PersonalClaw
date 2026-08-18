// Vitest global setup — jest-dom matchers (toBeInTheDocument, toBeDisabled, …)
// and automatic React cleanup between tests.
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

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

afterEach(() => cleanup())
