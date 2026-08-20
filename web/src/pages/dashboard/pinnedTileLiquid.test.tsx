/**
 * FLUID-MOTION §S2 T2.2 (atom FM-4) — `LiquidShape` AT ITS CALL SITE: the pinned ambient
 * dashboard tile (the plan's "ambient surfaces — liquid state transitions" consumer).
 *
 * This file asserts the CALL SITE, never the primitive. `LiquidShape`'s own geometry, tiers
 * and reduced-motion branch are covered by `ui/motion/LiquidShape*.test.tsx`; what is only
 * provable here is that a pinned tile actually REACHES it, aims it at the right state, and
 * hosts it somewhere a morph can be seen.
 *
 * The load-bearing one is the MOUNTED-HOST invariant. A morph is only observable if its host
 * survives the state change, and `PinnedTile`'s body is a ternary that swaps `WidgetFrame` for
 * the "Loading tile…" line — anything placed inside it is unmounted at exactly the moment the
 * state flips, so the silhouette would be constructed already-settled and would never animate.
 * That defect reviews as correctly wired and does nothing, which is why the invariant is
 * asserted on NODE IDENTITY across the flip (plus a structural rail that the liquid is a
 * sibling of the title inside the header row).
 *
 * Motion regime: framer-motion caches its `prefers-reduced-motion` answer in a module
 * singleton, so a `matchMedia` stub swapped mid-file is normally INERT (the note on
 * `LiquidShape.reducedMotion.test.tsx`). The stub below is installed at MODULE SCOPE before any
 * render — same technique — but its `matches` is a live getter and its `addEventListener`
 * captures the handler framer-motion registers for itself, so `setReducedMotion()` flips the
 * preference through framer-motion's OWN reactive path. `useReducedMotion` re-reads the cached
 * value on every mount, so each test picks the regime it needs and both regimes are reachable
 * from the one file this atom is fenced to.
 *
 * Every assertion carries a vacuity guard: an empty render answers "no liquid, no drift, no
 * remount" for free, so each test also asserts a positive control (the tile's title, and where
 * the claim needs it, the body that flipped).
 */

import { render, act, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from 'vitest'
import { invalidateCache } from '../../lib/useCachedData'
import { runtime } from '../../design/runtime'

// ── The OS preference, live and flippable ───────────────────────────────────────────────
let reducedMotion = false
const mediaListeners = new Set<() => void>()

Object.defineProperty(window, 'matchMedia', {
  configurable: true,
  writable: true,
  value: (query: string) => ({
    // Live, not a snapshot: framer-motion re-reads `.matches` from inside the listener it
    // registers, which is how a mid-file flip reaches its cached value.
    get matches() {
      // Only the reduced-motion query answers true. Everything else (`useIsMobile` and
      // friends) keeps the suite's default answer, which is the widest desktop render.
      return query.includes('prefers-reduced-motion') ? reducedMotion : false
    },
    media: query,
    addEventListener: (_type: string, fn: () => void) => { mediaListeners.add(fn) },
    removeEventListener: (_type: string, fn: () => void) => { mediaListeners.delete(fn) },
    addListener: (fn: () => void) => { mediaListeners.add(fn) },
    removeListener: (fn: () => void) => { mediaListeners.delete(fn) },
    dispatchEvent: () => false,
    onchange: null,
  }) as unknown as MediaQueryList,
})

/** Flip the preference and notify every handler registered against it — framer-motion's
 *  included, which is what updates the value its next mount will read. */
function setReducedMotion(on: boolean): void {
  reducedMotion = on
  mediaListeners.forEach((fn) => fn())
}

// ── The tile, driven through the rendered band (`PinnedTile` is private by design) ───────
const BODY = '<div>sales: 42</div>'

/** Whether the artifact fetch has a body yet — the ONE thing that moves this tile from
 *  loading to loaded, flipped between the first paint and the refresh click. */
let hasBody = false

/** Holds the artifact fetch OPEN so a test can observe the tile while a read is in flight.
 *  The composure silhouette depicts "settled" as body-present AND not-currently-fetching, and
 *  the second half is the only one a user can actually watch happen — body-presence alone was
 *  measured never to transition in a browser (the artifact resolves before the silhouette
 *  mounts). Without a gate like this the in-flight leg would be written and unasserted. */
let holdArtifact: Promise<void> | null = null
let releaseArtifact: (() => void) | null = null
function holdTheFetch(): void {
  holdArtifact = new Promise<void>((resolve) => { releaseArtifact = resolve })
}
async function releaseTheFetch(): Promise<void> {
  releaseArtifact?.()
  holdArtifact = null
  releaseArtifact = null
  for (let i = 0; i < 4; i++) await act(async () => { await Promise.resolve() })
}

const refreshTile = vi.fn()

vi.mock('../../app/appSdk', () => ({ launchChat: () => {}, notify: () => {} }))

vi.mock('../../lib/api', () => ({
  api: {
    dashboardViews: vi.fn(async () => [
      {
        id: 'overview',
        tiles: [
          {
            ref: 'artifact:sales',
            size: 'm',
            order: 0,
            added_by: 'user',
            // A LIVE tile: `isLive` needs mode:'ttl' + a skeleton, and only a live tile
            // renders `FreshnessBar` — the text carrier the silhouette leans on.
            refresh: {
              mode: 'ttl',
              ttl_secs: 900,
              skeleton: 'sales-skeleton',
              data: [{ id: 'health', provider: 'knowledge-health', config: {} }],
            },
          },
        ],
      },
    ]),
    artifact: vi.fn(async () => {
      if (holdArtifact) await holdArtifact
      return { slug: 'sales', name: 'Sales', content: hasBody ? BODY : undefined }
    }),
    // WidgetFrame's own calls — the band renders a real frame, so "the body painted" is
    // observable rather than stubbed away.
    artifactExists: vi.fn(async () => true),
    createArtifact: vi.fn(async () => ({})),
    deleteArtifact: vi.fn(async () => ({})),
    pinTile: vi.fn(async () => ({})),
    resolveTile: vi.fn(async () => ({})),
    refreshTile: (...args: unknown[]) => refreshTile(...args),
    tileLedgerHref: (viewId: string, ref: string) =>
      `/api/dashboard/views/${viewId}/tiles/refresh?ref=${encodeURIComponent(ref)}`,
  },
}))

const OK_ROW = {
  kind: 'tile_refreshed',
  event_id: 'overview__sales-evt-1',
  ts: '2026-08-17T09:00:00Z',
  ok: true,
  tokens: 0,
  cost_usd: 0,
  duration_ms: 8,
  nodes: [{ id: 'health', provider: 'knowledge-health', ok: true, duration_ms: 8 }],
}

// Imported after the stub is in place, mirroring the sanctioned reduced-motion file.
const { PinnedTiles, TILE_COMPOSURE_INTENSITY } = await import('./PinnedTiles')
const { LiquidShape } = await import('../../ui/motion/LiquidShape')

/** The tile's liquid, scoped to the band so the standalone reference renders below cannot be
 *  mistaken for it. Returns null when absent, so callers guard explicitly. */
const liquid = () =>
  document.querySelector<SVGSVGElement>('[data-testid="pinned-tiles"] svg[data-liquid-shape]')

const dOf = (el: Element | null) => el?.querySelector('path')?.getAttribute('d') ?? ''

beforeAll(() => {
  if (typeof URL.createObjectURL !== 'function') {
    URL.createObjectURL = () => 'blob:pinned-tile-liquid-test'
    URL.revokeObjectURL = () => {}
  }
})

beforeEach(() => {
  hasBody = false
  holdArtifact = null
  releaseArtifact = null
  refreshTile.mockReset()
  // `refreshed:false` on purpose: the 60 s poll then does NOT re-fetch the artifact, so the
  // first paint stays in its loading state until the test asks for the flip.
  refreshTile.mockResolvedValue({ refreshed: false, reason: 'within_ttl', ok: true, row: OK_ROW })
  localStorage.clear()
  // `useCachedData` holds a MODULE-LEVEL cache as well as the persisted one, so clearing
  // localStorage alone would serve the previous test's loaded artifact synchronously and every
  // tile would paint already-settled.
  invalidateCache('dashboard:views')
  invalidateCache('dashboard:tile:sales')
  setReducedMotion(false)
  runtime.expressiveness = 0.8
})

afterEach(() => {
  setReducedMotion(false)
  runtime.expressiveness = 0.8
})

async function paint() {
  render(<PinnedTiles />)
  // The views fetch, then the tile's artifact + its first refresh poll.
  for (let i = 0; i < 4; i++) await act(async () => { await Promise.resolve() })
}

/** Give the artifact a body and pull it in through the tile's own refresh control, so the
 *  loading→loaded flip happens on the ALREADY MOUNTED tree — which is the only way the
 *  mounted-host invariant is testable at all. */
async function loadTheBody() {
  hasBody = true
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh tile' })) })
  for (let i = 0; i < 4; i++) await act(async () => { await Promise.resolve() })
}

describe('a pinned tile wears its composure as a liquid silhouette', () => {
  it('reaches the primitive, on the animated branch, with the text carriers intact', async () => {
    await paint()

    // Positive control: an all-empty render would satisfy every "no drift / no remount"
    // assertion in this file for free.
    expect(screen.getByText('Sales')).toBeInTheDocument()

    const el = liquid()
    expect(el).not.toBeNull()
    expect(el).toHaveAttribute('data-liquid-shape', 'morph')
    expect(el).toHaveAttribute('data-liquid-tier', 'bold')
    expect(dOf(el)).toMatch(/^M[\d.]/)
    // Decorative by the primitive's contract — the call site adds no aria of its own.
    expect(el).toHaveAttribute('aria-hidden', 'true')

    // The silhouette must never be the only place a user could learn the state. Both text
    // carriers this call site leans on are still rendered.
    expect(screen.getByText('Loading tile…')).toBeInTheDocument()
    const chip = document.querySelector('[data-testid="tile-source-ok"]')
    expect(chip).not.toBeNull()
    expect(chip?.getAttribute('aria-label')).toBeTruthy()
  })

  it('depicts blob while loading and squircle once loaded, pinned by exact geometry', async () => {
    // Reduced motion for this one: the instant branch renders the silhouette as a pure
    // function of the state, with no spring in flight and no idle breathe, so the MAPPING can
    // be pinned by equality. `active` is the same prop on both branches, so pinning it here
    // pins the call site; the animated branch is what the tests either side of this cover.
    setReducedMotion(true)
    await paint()
    expect(screen.getByText('Sales')).toBeInTheDocument()

    const unsettled = dOf(liquid())
    expect(unsettled).toMatch(/^M[\d.]/)

    // Reference silhouettes from the primitive itself, at the call site's own amplitude.
    const reference = (active: boolean) => {
      const { container, unmount } = render(
        <LiquidShape from="blob" to="squircle" active={active} intensity={TILE_COMPOSURE_INTENSITY} />,
      )
      const d = container.querySelector('path')?.getAttribute('d') ?? ''
      unmount()
      expect(d).toMatch(/^M[\d.]/)
      return d
    }
    const blob = reference(false)
    const squircle = reference(true)
    // Vacuity guard: if the two silhouettes were indistinguishable, everything below would
    // pass without measuring anything.
    expect(blob).not.toBe(squircle)

    // Loading ⇒ the unsettled, organic form.
    expect(unsettled).toBe(blob)

    await loadTheBody()
    expect(screen.getByText('Sales')).toBeInTheDocument()

    // Loaded ⇒ the settled, deliberate one. Inverting `active` at the call site, or swapping
    // `from`/`to`, breaks both of these.
    const settled = dOf(liquid())
    expect(settled).toBe(squircle)
    expect(settled).not.toBe(unsettled)
  })

  it('unsettles while a re-read is IN FLIGHT and settles again when it lands', async () => {
    // The half a user can actually watch. Body-presence alone was measured never to transition
    // in a browser — the artifact resolves before the silhouette first mounts (0 of 276 sampled
    // frames caught the tile loading), so a silhouette keyed only on the body is a shape that
    // is always already settled. Keyed on the in-flight read too, pressing Refresh unsettles it.
    setReducedMotion(true) // instant branch: the silhouette is a pure function of the state
    await paint()
    await loadTheBody()
    expect(screen.getByText('Sales')).toBeInTheDocument()

    const settled = dOf(liquid())
    expect(settled).toMatch(/^M[\d.]/)

    // Re-read, held open. The body stays painted (SWR keeps the last good value), so this is
    // NOT the loading state — it is "settled content, currently being re-read".
    holdTheFetch()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh tile' })) })
    for (let i = 0; i < 4; i++) await act(async () => { await Promise.resolve() })

    const inFlight = dOf(liquid())
    expect(inFlight).toMatch(/^M[\d.]/)
    // Vacuity guard: if these were equal the assertion below would pass without the silhouette
    // ever having depicted anything.
    expect(inFlight).not.toBe(settled)

    await releaseTheFetch()
    expect(screen.getByText('Sales')).toBeInTheDocument()
    expect(dOf(liquid())).toBe(settled)
  })

  it('keeps the SAME liquid node across the loading→loaded flip', async () => {
    await paint()

    const before = liquid()
    expect(before).not.toBeNull()

    // Structural rail: hosted in the header row as a sibling of the title. The body ternary
    // below swaps its whole subtree on the flip, so a liquid living inside it fails here.
    const title = screen.getByText('Sales')
    expect(title.parentElement).not.toBeNull()
    expect(title.parentElement?.contains(before as Node)).toBe(true)

    await loadTheBody()

    // The flip really happened — without this the identity check below could hold simply
    // because nothing changed.
    expect(screen.queryByText('Loading tile…')).toBeNull()
    expect(document.querySelector('iframe')).not.toBeNull()
    expect(screen.getByText('Sales')).toBeInTheDocument()

    const after = liquid()
    expect(after).not.toBeNull()
    // IDENTITY, not presence: a remount would construct the silhouette already settled and
    // the morph would never run — the exact defect that reviews as correctly wired.
    // Compared as a BOOLEAN rather than `expect(after).toBe(before)`: on failure the matcher
    // serializes both DOM nodes for its diff and that serialization throws
    // "SecurityError: localStorage is not available for opaque origins" in jsdom, which
    // replaces the real message with an unrelated one. Measured — that is exactly what the
    // remount falsification reported before this line was written this way.
    expect(after === before).toBe(true)
    // And the surviving host is the animated branch, not a static redraw.
    expect(after).toHaveAttribute('data-liquid-shape', 'morph')
  })

  it('takes the INSTANT path under prefers-reduced-motion and stays put', async () => {
    setReducedMotion(true)
    await paint()
    expect(screen.getByText('Sales')).toBeInTheDocument()

    const el = liquid()
    expect(el).not.toBeNull()
    expect(el).toHaveAttribute('data-liquid-shape', 'instant')
    expect(el).toHaveAttribute('data-liquid-tier', 'reduced')

    const first = dOf(el)
    // Positive control: an instant branch that drew nothing would pass the drift check below.
    expect(first).toMatch(/^M[\d.]/)

    // No driver survives reduced motion, so the silhouette must not wander. Long enough for
    // many frames of the bold tier's breathe to have ticked.
    vi.useRealTimers()
    await new Promise((r) => setTimeout(r, 120))
    expect(dOf(liquid())).toBe(first)
  })

  it('drops to the refined tier at expressiveness 0 and still draws a real shape', async () => {
    runtime.expressiveness = 0
    await paint()
    expect(screen.getByText('Sales')).toBeInTheDocument()

    const el = liquid()
    expect(el).not.toBeNull()
    expect(el).toHaveAttribute('data-liquid-shape', 'morph')
    // Refined drops the heavy breathe entirely rather than shrinking it…
    expect(el).toHaveAttribute('data-liquid-tier', 'refined')
    // …but `expr`'s floor keeps the shape itself alive: refined must not read as dead.
    expect(dOf(el)).toMatch(/^M[\d.]/)
    expect(dOf(el).match(/C/g)).toHaveLength(16)
  })
})
