import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { AgentActivityEntity, AgentActivityFeed } from '../../../lib/useAgentActivity'

// ── The world renders from the hook alone, and holds still when asked ─────────
//
// Two claims live here, and both are the kind that pass vacuously if you are not
// careful:
//
//  1. **No private endpoints.** The world's data comes from `useAgentActivity()` and
//     nowhere else, so an app-contributed world (APP-PLATFORM-EVOLUTION) can be
//     handed the identical value with no network permission. Asserted structurally —
//     a behavioural test cannot see a fetch that only fires in a branch it missed.
//  2. **`prefers-reduced-motion` yields a STATIC LAYOUT.** Not a slower animation:
//     no animation frame is ever scheduled. That claim is worthless without the
//     POSITIVE CONTROL that the animated path DOES schedule frames — otherwise a
//     component that renders nothing at all would pass it. Both directions run below.

const SRC = join(process.cwd(), 'src/pages/dashboard/world')
/** Source with comments stripped — the prose here talks ABOUT `/api/` paths on
 *  purpose, and a scan that counted those would be measuring its own documentation. */
const code = (f: string) =>
  readFileSync(join(SRC, f), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the world reaches no endpoint of its own', () => {
  const FILES = ['AgentWorld.tsx', 'worldScene.ts']

  it.each(FILES)('%s mentions no /api/ path', (f) => {
    const body = code(f)
    // Vacuity floor: prove the scan is looking at real, stripped source before
    // believing that it found nothing.
    expect(body.length, `${f} came back empty — the scan is measuring nothing`).toBeGreaterThan(600)
    expect(body, `${f} must not name an endpoint`).not.toMatch(/["'`]\/api\//)
  })

  it.each(FILES)('%s opens no transport of its own', (f) => {
    const body = code(f)
    for (const banned of ['fetch(', 'XMLHttpRequest', 'new WebSocket', 'EventSource', 'navigator.sendBeacon']) {
      expect(body, `${f} must not use ${banned}`).not.toContain(banned)
    }
  })

  it.each(FILES)('%s never imports the api client', (f) => {
    // `lib/api` is the only module that knows a URL. Importing it is the seam breaking,
    // even if today's call happens to be a harmless type read.
    expect(code(f)).not.toMatch(/from\s+['"][^'"]*lib\/api['"]/)
  })

  it('AgentWorld takes its data from useAgentActivity and no other hook', () => {
    const body = code('AgentWorld.tsx')
    expect(body).toContain('useAgentActivity()')
    // No sibling data source may sneak in beside it.
    for (const banned of ['useDashboardLive', 'useCachedData', 'useChatSocket', 'useVisiblePoll']) {
      expect(body, `the world must not call ${banned} — it consumes ONE contract`).not.toContain(banned)
    }
  })

  it('AgentWorld takes no props — a world is handed a contract, not wired to a host', () => {
    const body = code('AgentWorld.tsx')
    expect(body).toMatch(/export function AgentWorld\(\)/)
  })
})

// ── Reduced-motion audit ─────────────────────────────────────────────────────

interface FakeCtx {
  calls: Record<string, number>
  ctx: CanvasRenderingContext2D
}

/** A counting 2D context. jsdom has none, and `pickRenderTier` would otherwise send
 *  every test down the `static` path — where no frame is scheduled either way, which
 *  would make the reduced-motion assertion pass for the wrong reason. */
function fakeContext(): FakeCtx {
  const calls: Record<string, number> = {}
  const tally = (name: string) => (...__: unknown[]) => { calls[name] = (calls[name] ?? 0) + 1 }
  return {
    calls,
    ctx: {
      clearRect: tally('clearRect'), setTransform: tally('setTransform'),
      save: tally('save'), restore: tally('restore'),
      beginPath: tally('beginPath'), arc: tally('arc'),
      fill: tally('fill'), stroke: tally('stroke'),
      fillStyle: '', strokeStyle: '', lineWidth: 0, lineCap: 'butt',
      globalAlpha: 1, globalCompositeOperation: 'source-over',
    } as unknown as CanvasRenderingContext2D,
  }
}

const ORIGINAL = {
  matchMedia: window.matchMedia,
  raf: window.requestAnimationFrame,
  caf: window.cancelAnimationFrame,
  getContext: HTMLCanvasElement.prototype.getContext,
}

function setReducedMotion(on: boolean): void {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: ((query: string) => ({
      matches: on && query.includes('prefers-reduced-motion'),
      media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia,
  })
}

const entities: AgentActivityEntity[] = [
  { id: 'loop:a', kind: 'loop', state: 'working', title: 'Ship it', progress: 0.4, refs: { link: '#/loops/a' } },
  { id: 'loop:b', kind: 'loop', state: 'needs_input', title: 'Asks me', refs: { link: '#/loops/b' } },
  { id: 'session:c', kind: 'session', state: 'idle', title: 'Old chat', refs: { link: '#/chat/c' } },
]

/** The design tokens the painter resolves off the document. jsdom loads no
 *  stylesheet, so without these every `getPropertyValue` returns '' — and the
 *  "it DOES paint" floor below would be measuring the absence of a theme rather
 *  than the presence of a scene. Declared from the registry's own names. */
const TONE_TOKENS = ['--color-ok', '--color-info', '--color-warn', '--color-danger',
  '--color-on-surface-low', '--color-outline-variant']

let frames: FrameRequestCallback[] = []
let instrument = fakeContext()
let feed: AgentActivityFeed

/** Mount the world over a stubbed contract, so the render test never touches the
 *  network or a socket — which is also the cleanest demonstration of the seam. */
async function mountWorld() {
  vi.doMock('../../../lib/useAgentActivity', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    useAgentActivity: () => feed,
  }))
  const { AgentWorld } = await import('./AgentWorld')
  render(<AgentWorld />)
}

beforeEach(() => {
  vi.resetModules()
  frames = []
  instrument = fakeContext()
  feed = { entities, truncated: 0, error: null, loading: false, refresh: () => {} }
  Object.defineProperty(window, 'requestAnimationFrame', {
    configurable: true, writable: true, value: (cb: FrameRequestCallback) => frames.push(cb),
  })
  Object.defineProperty(window, 'cancelAnimationFrame', {
    configurable: true, writable: true, value: () => {},
  })
  HTMLCanvasElement.prototype.getContext = (() => instrument.ctx) as never
  for (const t of TONE_TOKENS) document.documentElement.style.setProperty(t, '#22c55e')
  // A real box, so the painter runs on numbers instead of 0/0.
  for (const prop of ['clientWidth', 'clientHeight'] as const) {
    Object.defineProperty(HTMLCanvasElement.prototype, prop, {
      configurable: true, value: prop === 'clientWidth' ? 640 : 288,
    })
  }
})

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL.matchMedia })
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.raf })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.caf })
  HTMLCanvasElement.prototype.getContext = ORIGINAL.getContext
  for (const t of TONE_TOKENS) document.documentElement.style.removeProperty(t)
  vi.restoreAllMocks()
})

describe('prefers-reduced-motion: reduce yields a static layout', () => {
  it('schedules NO animation frame at all', async () => {
    setReducedMotion(true)
    await mountWorld()
    expect(frames.length, 'reduced motion must not start an animation loop').toBe(0)
  })

  it('but it DOES paint — the floor under the claim above', async () => {
    setReducedMotion(true)
    await mountWorld()
    // Without this, "no frames" would also be satisfied by a world that drew nothing.
    expect(instrument.calls.arc ?? 0, 'nothing was drawn').toBeGreaterThan(0)
    expect(instrument.calls.clearRect ?? 0).toBeGreaterThan(0)
  })

  it('POSITIVE CONTROL: with the preference OFF, the loop does run', async () => {
    setReducedMotion(false)
    await mountWorld()
    expect(frames.length, 'the animated path never scheduled a frame').toBe(1)
    // …and keeps running: one frame re-schedules the next.
    const first = frames.shift()!
    first(16)
    expect(frames.length, 'the loop stopped after one frame').toBe(1)
  })

  it('the static paint is IDENTICAL across two mounts — nothing is time-dependent', async () => {
    setReducedMotion(true)
    await mountWorld()
    const a = { ...instrument.calls }
    instrument = fakeContext()
    HTMLCanvasElement.prototype.getContext = (() => instrument.ctx) as never
    vi.resetModules()
    await mountWorld()
    // A static layout that drew a different number of shapes on the second mount would
    // be a clock leaking into the "static" path.
    expect(instrument.calls).toEqual(a)
  })
})

// ── The canvas does not exist on the first render ─────────────────────────────
//
// 🔴 THE RAIL THE SUITE WAS MISSING, and the reason a green suite shipped a world
// that painted nothing in Chrome. Every test above mounts with entities ALREADY
// present, so the `<canvas>` is in the DOM at mount and a mount-time tier probe
// happens to find it. The real sequence is the opposite: the `loading` holdback
// returns `null`, the canvas is absent, and it appears only after the four GETs
// settle. A probe with empty deps ran once against a null ref, resolved 'static',
// and never looked again — leaving `canvas.width/height` at the HTML default
// `300x150` against a `1210x288` CSS box, with `anyAlphaPx: 0` over the whole
// backing store.
//
// So this rail reproduces the ORDER, not just the state, and asserts the two things
// a stub can prove about a painter that actually ran: the backing store was sized
// from the MEASURED box, and per-node drawing happened after the entities arrived.

/** Which feed the stubbed hook is currently serving. */
let phase: 'loading' | 'loaded' = 'loading'
/** Force a re-render of the mounted world, the way a settling fetch would. */
let bump: (() => void) | null = null

const LOADING_FEED: AgentActivityFeed =
  { entities: [], truncated: 0, error: null, loading: true, refresh: () => {} }

/** Mount over a hook whose answer can CHANGE, so the canvas appears late. */
async function mountLate(loaded: AgentActivityFeed) {
  vi.doMock('../../../lib/useAgentActivity', async (orig) => {
    const react = await import('react')
    return {
      ...(await orig<Record<string, unknown>>()),
      useAgentActivity: () => {
        const [, force] = react.useState(0)
        bump = () => force((n) => n + 1)
        return phase === 'loading' ? LOADING_FEED : loaded
      },
    }
  })
  const { AgentWorld } = await import('./AgentWorld')
  render(<AgentWorld />)
}

/** Let the stubbed fetch "settle": flip the feed and re-render. */
async function settle() {
  phase = 'loaded'
  await act(async () => { bump!() })
}

describe('the world paints when the canvas arrives AFTER the first render', () => {
  beforeEach(() => { phase = 'loading'; bump = null; setReducedMotion(false) })

  it('sizes its backing store from the measured box and draws every node', async () => {
    const loaded: AgentActivityFeed =
      { entities, truncated: 0, error: null, loading: false, refresh: () => {} }
    // VACUITY FLOOR: there must be something to draw, or every assertion below is
    // satisfied by a world that correctly drew nothing.
    expect(loaded.entities.length, 'nothing seeded — the rail would pass empty').toBeGreaterThan(0)

    await mountLate(loaded)
    // The real sequence, asserted rather than assumed: no canvas on the first render.
    expect(screen.queryByRole('img'), 'the canvas must be ABSENT while loading').toBeNull()
    expect(instrument.calls.arc ?? 0, 'nothing should be drawn yet').toBe(0)

    await settle()

    const canvas = screen.getByRole('img') as HTMLCanvasElement
    // 300x150 is the HTML default — the exact fingerprint of a painter that never ran.
    expect(canvas.width, 'backing store left at the HTML default — the painter never ran').not.toBe(300)
    expect(canvas.height, 'backing store left at the HTML default — the painter never ran').not.toBe(150)
    expect(canvas.width, 'backing store not sized from the measured box').toBe(640)
    expect(canvas.height).toBe(288)

    // On the animated path the FIRST paint happens inside the scheduled frame, so run
    // it. That the frame exists at all is half the claim: the tier gate guarded the
    // loop too, so a stranded probe froze the world as well as blanking it.
    expect(frames.length, 'the late canvas never got an animation loop').toBe(1)
    await act(async () => { frames.shift()!(16) })

    expect(instrument.calls.clearRect ?? 0, 'the frame was never cleared').toBeGreaterThan(0)
    expect(instrument.calls.arc ?? 0, 'no node was ever drawn')
      .toBeGreaterThan(loaded.entities.length)
  })

  it('and the DOM fallback list is NOT shown once a real context is found', async () => {
    // The pre-fix bug rendered the fallback list over an empty rectangle. Its absence
    // here is what says the tier was re-probed against the real element.
    await mountLate({ entities, truncated: 0, error: null, loading: false, refresh: () => {} })
    await settle()
    expect(screen.queryByRole('list'), 'the world fell back despite having a 2d context').toBeNull()
  })

  it('VACUITY CONTROL: settling to an EMPTY feed draws nothing at all', async () => {
    // Proves the arc/width assertions above are driven by the entities and not by the
    // mere act of settling — otherwise the floor could never fail.
    await mountLate({ entities: [], truncated: 0, error: null, loading: false, refresh: () => {} })
    await settle()
    expect(screen.getByText(/Nothing is running\./)).toBeInTheDocument()
    expect(screen.queryByRole('img')).toBeNull()
    expect(instrument.calls.arc ?? 0).toBe(0)
  })

  it('a late canvas also gets an animation loop, not just one frame', async () => {
    // The tier gate guarded the rAF loop too, so a stranded probe froze the world as
    // well as blanking it.
    await mountLate({ entities, truncated: 0, error: null, loading: false, refresh: () => {} })
    expect(frames.length, 'no loop should start before the canvas exists').toBe(0)
    await settle()
    expect(frames.length, 'the late canvas never got an animation loop').toBe(1)
  })
})

describe('an undeclared tone token cannot silently blank the scene', () => {
  it('nodes still paint when every colour token is missing', async () => {
    // The back-door blank: `role="img"` promises a scene, every `var()` resolves to
    // '', and the canvas comes out empty. The painter falls back to the canvas's own
    // inherited ink instead, so the scene degrades in COLOUR, never into nothing.
    for (const t of TONE_TOKENS) document.documentElement.style.removeProperty(t)
    setReducedMotion(true)
    await mountWorld()
    expect(instrument.calls.arc ?? 0, 'a themeless document blanked the world').toBeGreaterThan(0)
  })
})

describe('the world is legible without seeing it', () => {
  it('the canvas carries the scene summary as its accessible name', async () => {
    setReducedMotion(true)
    await mountWorld()
    const img = screen.getByRole('img')
    expect(img.getAttribute('aria-label')).toContain('1 waiting on you')
    expect(img.getAttribute('aria-label')).toContain('1 working')
  })

  it('and the same facts are visible in text, not only announced', async () => {
    setReducedMotion(true)
    await mountWorld()
    expect(screen.getByText(/1 waiting on you, 1 working, 1 idle\./)).toBeInTheDocument()
  })

  it('with no drawing context the world falls back to a list, not a blank box', async () => {
    HTMLCanvasElement.prototype.getContext = (() => null) as never
    setReducedMotion(false)
    await mountWorld()
    // Every entity is still reachable, and no animation loop was started for a
    // canvas that cannot be drawn into.
    expect(frames.length).toBe(0)
    for (const e of entities) expect(screen.getByText(e.title)).toBeInTheDocument()
  })
})

describe('the world says when it does not know', () => {
  it('a failed read is stated, never rendered as a calm empty scene', async () => {
    feed = { entities: [], truncated: 0, error: new Error('gateway down'), loading: false, refresh: () => {} }
    setReducedMotion(true)
    await mountWorld()
    expect(screen.getByText(/world is unknown right now/i)).toBeInTheDocument()
    expect(screen.queryByRole('img'), 'no scene is drawn over an unknown').toBeNull()
  })

  it('genuinely empty says so, and is NOT the same sentence as unknown', async () => {
    feed = { entities: [], truncated: 0, error: null, loading: false, refresh: () => {} }
    setReducedMotion(true)
    await mountWorld()
    expect(screen.getByText(/Nothing is running\./)).toBeInTheDocument()
    expect(screen.queryByText(/unknown right now/)).toBeNull()
  })

  it('while loading it holds the empty state back rather than claiming emptiness', async () => {
    feed = { entities: [], truncated: 0, error: null, loading: true, refresh: () => {} }
    setReducedMotion(true)
    await mountWorld()
    expect(screen.queryByText(/Nothing is running\./)).toBeNull()
  })
})
