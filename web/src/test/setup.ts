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

afterEach(() => cleanup())
