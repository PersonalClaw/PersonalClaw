import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { act, render, renderHook, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useHashRoute } from './useHashRoute'

// ── Route transitions are COSMETIC ONLY (plan FLUID-MOTION §C3, atom FM-5) ─────
// The hash router wraps its route commit in a View Transition so navigating
// crossfades. That is a nicety; the navigation is not. Every test below exists to
// pin the same single property from a different angle:
//
//     the URL and the route state change REGARDLESS of what the transition does.
//
// A transition that gated either one would be a navigation bug wearing an animation
// costume — a slow, hung or unsupported transition would delay or lose the route
// change. jsdom implements no View Transitions API, so the supported cases are
// installed by hand and the unsupported case is jsdom's own default.

type Svt = (cb: () => void) => unknown

/** Promises that never settle — the "the animation hangs forever" shape. */
const NEVER = new Promise<void>(() => {})
const hangingTransition = { ready: NEVER, finished: NEVER, updateCallbackDone: NEVER, skipTransition: () => {} }

function installViewTransition(impl: Svt | undefined): void {
  if (impl) Object.defineProperty(document, 'startViewTransition', { configurable: true, writable: true, value: impl })
  else Reflect.deleteProperty(document, 'startViewTransition')
}

/** A stand-in that RUNS the update (as every real implementation must, even when it
 *  skips the animation) and hands back a transition whose promises never settle. */
function workingViewTransition() {
  return vi.fn((cb: () => void) => { cb(); return hangingTransition })
}

// jsdom does not implement matchMedia at all, so this is an assignment rather than a
// spy: spying on a property that does not exist leaves a function returning undefined
// once restored, which throws inside the guard instead of answering it.
const ORIGINAL_MATCH_MEDIA = window.matchMedia

function setReducedMotion(on: boolean): void {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: ((query: string) => ({
      matches: on && query.includes('prefers-reduced-motion'),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia,
  })
}

beforeEach(() => {
  // replaceState rather than `location.hash =` — seeding the start route must not emit
  // a hashchange of its own into the test that follows.
  history.replaceState(null, '', '#/dashboard')
})

afterEach(() => {
  installViewTransition(undefined)
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA })
  vi.restoreAllMocks()
})

function mount() {
  return renderHook(() => useHashRoute('dashboard'))
}

/** A real render of the router's output, for the one assertion that needs the DOM
 *  rather than the hook's return value. */
function RouteProbe() {
  const { route } = useHashRoute('dashboard')
  return <span data-testid="route">{route}</span>
}

/** What the browser does on back/forward: the hash moves and a hashchange lands,
 *  with no `navigate()` call anywhere. */
function hashChangeTo(hash: string): void {
  act(() => {
    history.replaceState(null, '', hash)
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })
}

/** Drive a navigation the way the browser does. The router writes `location.hash`,
 *  and the `hashchange` that write emits is the seam the transition hangs off. jsdom
 *  queues that event on its own event loop, so it is dispatched here instead of waited
 *  for; a later duplicate delivery from jsdom is idempotent, because by then the route
 *  already matches and neither re-commits differently nor starts a second transition. */
function navigate(nav: () => void): void {
  act(() => {
    nav()
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })
}

describe('route transitions', () => {
  it('crossfades a route change, and commits the route through the transition', () => {
    const started = workingViewTransition()
    installViewTransition(started)
    const { result } = mount()

    navigate(() => result.current.navigate('agents'))

    expect(started).toHaveBeenCalledTimes(1)
    expect(location.hash).toBe('#/agents')
    expect(result.current.route).toBe('agents')
  })

  // ── The three failure modes. Each asserts the ROUTE STATE, not just the URL: the
  // URL write lives outside the transition by construction, so a URL-only assertion
  // would still pass with the commit trapped inside a broken transition.

  it('navigates when the platform has no View Transitions API', () => {
    expect(document.startViewTransition).toBeUndefined()
    const { result } = mount()

    navigate(() => result.current.navigate('agents'))

    expect(location.hash).toBe('#/agents')
    expect(result.current.route).toBe('agents')
  })

  it('navigates when startViewTransition THROWS', () => {
    installViewTransition(() => { throw new Error('transition refused') })
    const { result } = mount()

    navigate(() => result.current.navigate('agents'))

    expect(location.hash).toBe('#/agents')
    expect(result.current.route).toBe('agents')
  })

  it('navigates when the transition NEVER SETTLES', () => {
    // The callback runs but `finished`/`ready` never resolve. The route is asserted
    // right after the synchronous navigation, so this fails the moment anything on
    // the path awaits the animation before committing.
    installViewTransition((cb) => { cb(); return hangingTransition })
    const { result } = mount()

    navigate(() => result.current.navigate('agents'))

    expect(location.hash).toBe('#/agents')
    expect(result.current.route).toBe('agents')
  })

  it('navigates under reduced motion with no transition at all', () => {
    // done_when: "reduced-motion → crossfade or none". This resolves to NONE.
    setReducedMotion(true)
    const started = workingViewTransition()
    installViewTransition(started)
    const { result } = mount()

    navigate(() => result.current.navigate('agents'))

    expect(started).not.toHaveBeenCalled()
    expect(location.hash).toBe('#/agents')
    expect(result.current.route).toBe('agents')
  })

  it('crossfades browser back/forward, which no page opts into', () => {
    // Back/forward reaches the router only as a hashchange — the same seam a nav click
    // uses, which is why wrapping that one listener covers history navigation for free.
    const started = workingViewTransition()
    installViewTransition(started)
    const { result } = mount()

    hashChangeTo('#/agents')

    expect(started).toHaveBeenCalledTimes(1)
    expect(result.current.route).toBe('agents')
  })

  it('crossfades EVERY route change, including returning to the one it started on', () => {
    // Guards the `applied` mirror. It is written on every commit, not only the animated
    // ones; leave it stale and going back to the starting route compares equal to it and
    // silently skips the crossfade — a missed animation with nothing else to show for it.
    const started = workingViewTransition()
    installViewTransition(started)
    const { result } = mount()

    hashChangeTo('#/agents')
    expect(result.current.route).toBe('agents')
    hashChangeTo('#/dashboard')

    expect(result.current.route).toBe('dashboard')
    expect(started).toHaveBeenCalledTimes(2)
  })

  it('commits the new DOM BEFORE the transition captures it', () => {
    // The flushSync contract, and the only assertion here that needs a real render.
    // The browser snapshots the "after" frame as soon as the update callback returns,
    // while React 18 would commit a plain setState later on the scheduler — so a
    // missing flush means both snapshots are the OLD frame and the crossfade animates
    // nothing at all. Reading the DOM from inside the callback is the closest jsdom can
    // get to standing where the compositor stands.
    let domWhenCaptured = ''
    installViewTransition((cb) => {
      cb()
      domWhenCaptured = screen.getByTestId('route').textContent ?? ''
      return hangingTransition
    })
    render(<RouteProbe />)

    hashChangeTo('#/agents')

    expect(domWhenCaptured).toBe('agents')
  })

  // ── What must NOT animate. "No motion that delays a user action or fights
  // readability" (the plan's soul guardrail) rules out crossfading the page on
  // in-place refinements — a search box would fade the whole surface per keystroke.

  it('does not crossfade a PUSHED query change, which reaches the same hashchange seam', () => {
    // The load-bearing case for the route-only gate, and the one a `replace` query
    // update cannot stand in for: opening a detail panel (`?open=<id>`) is a push, so
    // it writes location.hash and arrives at `onHash` exactly like a nav click does.
    // Only the route comparison keeps it from fading the whole page underneath a panel.
    const started = workingViewTransition()
    installViewTransition(started)
    const { result } = mount()

    navigate(() => result.current.setQuery({ open: 'task-1' }))

    expect(started).not.toHaveBeenCalled()
    expect(result.current.query.open).toBe('task-1')
    expect(result.current.route).toBe('dashboard')
  })

  it('does not crossfade a replaced in-place refinement, and still applies it', () => {
    // Search/tab/filter/sort are `replace` by doctrine, so they never emit a hashchange
    // at all — a second, independent reason a keystroke cannot fade the page.
    const started = workingViewTransition()
    installViewTransition(started)
    const { result } = mount()

    act(() => { result.current.setQuery({ q: 'ship' }, { replace: true }) })

    expect(started).not.toHaveBeenCalled()
    expect(result.current.query.q).toBe('ship')
    expect(result.current.route).toBe('dashboard')
  })

  it('does not crossfade a replace navigation — a URL correction is not a navigation', () => {
    // `navigate(x, { replace: true })` is how a redirect rewrites the address bar
    // (onboarding → dashboard). Fading it would animate a correction the user never
    // asked for, and it never emits a hashchange in the first place.
    const started = workingViewTransition()
    installViewTransition(started)
    const { result } = mount()

    act(() => { result.current.navigate('agents', { replace: true }) })

    expect(started).not.toHaveBeenCalled()
    expect(location.hash).toBe('#/agents')
    expect(result.current.route).toBe('agents')
  })

  // ── Cosmetic-only means the rest of the router's behaviour is untouched.

  it('leaves navEpoch semantics alone: a path nav bumps it, a query update does not', () => {
    installViewTransition(workingViewTransition())
    const { result } = mount()
    const before = result.current.navEpoch

    navigate(() => result.current.navigate('agents'))
    const afterNav = result.current.navEpoch
    expect(afterNav).toBeGreaterThan(before)

    act(() => { result.current.setQuery({ tab: 'runs' }, { replace: true }) })
    expect(result.current.navEpoch).toBe(afterNav)
  })

  it('still parses sub-path and query across an animated navigation', () => {
    installViewTransition(workingViewTransition())
    const { result } = mount()

    navigate(() => result.current.navigate('chat/abc-1?tab=files'))

    expect(result.current.route).toBe('chat')
    expect(result.current.sub).toBe('abc-1')
    expect(result.current.query.tab).toBe('files')
  })
})

describe('the route crossfade curve', () => {
  it('is declared on the app curve in tokens.css, not left to the UA default', () => {
    // The taste layer. The MECHANISM is the JS gate in useHashRoute/viewTransition —
    // deleting these selectors leaves a browser-flavoured crossfade rather than no
    // animation — so this pins only that a page change moves on the same
    // emphasized-decelerate curve as every other entrance in the app.
    const css = readFileSync(join(process.cwd(), 'src/design/tokens.css'), 'utf8')
    const rule = /::view-transition-old\(root\)[^{]*\{([^}]*)\}/.exec(css)
    expect(rule, '::view-transition-old(root) has no rule in tokens.css').toBeTruthy()
    expect(rule![1]).toContain('var(--ease-emphasized-decel)')
    expect(css).toContain('::view-transition-new(root)')
  })
})
