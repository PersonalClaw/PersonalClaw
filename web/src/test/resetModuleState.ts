// Setup for EVERY test file, in either project (`vitest.config.ts`): the module-state resets every
// file needs, whether or not it has a DOM. The DOM-only setup is `setupDom.ts`, listed after this
// file for the `dom` project. Vitest runs `afterEach` hooks last registered first, so its React
// cleanup runs before these resets, in the order the single setup file had them.
import { afterEach } from 'vitest'
// Imported by its concrete path, NOT through `lib/data`, so the eight test files that
// `vi.mock('../../lib/data', …)` wholesale cannot replace it with a factory that has no
// `resetDataStore` and take the whole suite down.
import { resetDataStore } from '../lib/data/store'
import { resetSignedOutForTests } from '../lib/signedOut'

// The data layer's cache is a module singleton, so it OUTLIVES a test. That was already true
// of the helper it replaced, but harmless there because that hook re-fetched on every mount:
// a leaked entry only ever changed the first frame. This layer honours declared staleness, so
// a FRESH leaked entry means the next test's mount does not fetch at all — its `vi.fn()` mock
// is never called and it waits out its `waitFor` on data the previous test seeded. Measured
// while migrating: 15 of 22 failures in the first full run were exactly this, across seven
// files the change never touched (desktopCapabilities alone: 7).
//
// Cold cache per test is also just the correct fixture — a test asserting a first paint should
// not depend on which test ran before it.
//
// The tab's signed-out state (`lib/signedOut.ts`) is a module singleton for the same reason, and
// a leaked one is worse than a warm cache: once set, the api client refuses every request
// locally, so each later test in the file would fail on a request it never saw sent.
afterEach(() => { resetDataStore(); resetSignedOutForTests() })
