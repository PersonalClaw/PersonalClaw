import { describe, it, expect, afterEach, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import * as motion from './motion'
import {
  dragElastic, dragSpring, instant, listItemEnter, overlayEnter, physics,
  prefersReducedMotion, swipeDismiss,
} from './motion'
import { runtime } from './runtime'
import { TOKENS } from './tokenRegistry'

// ── The motion BUDGET rails (plan FLUID-MOTION S1 / atom FM-1) ──────────────
// Two claims carry the whole physics-preset system, and both were previously only
// asserted by a comment:
//   1. every named preset SCALES with the user's `--bounciness` slider, and
//   2. every named preset ZEROES OUT under prefers-reduced-motion.
// A preset that quietly ignores either dial is worse than no preset: the Design
// panel shows a control that does nothing, and the a11y setting is a promise the
// app breaks. Both are pinned per-preset below, plus the two ways they leak in
// practice — a variant that snapshots its transition at import, and a spread that
// re-infers a spring on top of the reduced-motion transition.

// jsdom does not implement `matchMedia` at all, so this is an assignment rather than
// a `vi.spyOn`: spying on a property that does not exist leaves behind a function that
// returns undefined once restored, which then throws inside the guard instead of
// answering it. Captured and put back verbatim in afterEach.
const ORIGINAL_MATCH_MEDIA = window.matchMedia

function defineMatchMedia(value: typeof window.matchMedia): void {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value })
}

/** Force the reduced-motion media query on or off for one test. */
function setReducedMotion(on: boolean): void {
  defineMatchMedia(((query: string) => ({
    matches: on && query.includes('prefers-reduced-motion'),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia)
}

const DEFAULTS = { bounciness: runtime.bounciness, dragElastic: runtime.dragElastic, swipeVelocity: runtime.swipeVelocity, swipeDistance: runtime.swipeDistance }

afterEach(() => {
  vi.restoreAllMocks()
  defineMatchMedia(ORIGINAL_MATCH_MEDIA)
  Object.assign(runtime, DEFAULTS)
})

/** Resolve a Framer variant that is a function (a dynamic variant) to its target. */
function resolveVariant(v: unknown): Record<string, unknown> {
  expect(typeof v).toBe('function')
  return (v as (custom: unknown) => Record<string, unknown>)(undefined)
}

const PRESETS = ['snappy', 'smooth', 'fluid', 'playful'] as const

// Damping endpoints per preset: [at bounciness 0 (calm), at bounciness 1 (playful)].
// Straight from the plan's §C1 constants — pinned so a retune is a deliberate edit
// here and not a silent drift in feel.
const ENDPOINTS: Record<(typeof PRESETS)[number], [number, number]> = {
  snappy: [40, 30],
  smooth: [38, 34],
  fluid: [34, 26],
  playful: [34, 14],
}

describe('physics presets', () => {
  it('is a CLOSED set of four — no parallel spring vocabulary alongside it', () => {
    expect(Object.keys(physics).sort()).toEqual([...PRESETS].sort())
    // The alias set `springs` and the never-imported `pressable` object were deleted
    // in favour of these presets. Re-exporting either would put two names on one
    // decision again, which is the defect this reconciliation removed.
    expect(motion).not.toHaveProperty('springs')
    expect(motion).not.toHaveProperty('bounce')
    expect(motion).not.toHaveProperty('pressable')
  })

  it.each(PRESETS)('%s scales its overshoot with the bounciness slider', (name) => {
    const [calm, playful] = ENDPOINTS[name]

    runtime.bounciness = 0
    const atCalm = physics[name] as { type?: string; damping?: number; stiffness?: number }
    runtime.bounciness = 1
    const atPlayful = physics[name] as { type?: string; damping?: number; stiffness?: number }

    expect(atCalm.type).toBe('spring')
    expect(atCalm.damping).toBe(calm)
    expect(atPlayful.damping).toBe(playful)
    // Lower damping = more overshoot, so playful MUST be the bouncier end. This is
    // the assertion that fails if a preset is wired to a constant damping.
    expect(atPlayful.damping!).toBeLessThan(atCalm.damping!)
    // Stiffness is the preset's identity and must NOT move with the slider.
    expect(atPlayful.stiffness).toBe(atCalm.stiffness)
  })

  it('reads the slider at ANIMATION time, not once at import', () => {
    runtime.bounciness = 1
    const before = (physics.playful as { damping?: number }).damping
    runtime.bounciness = 0.5
    expect((physics.playful as { damping?: number }).damping).not.toBe(before)
  })

  it.each(PRESETS)('%s zeroes out under prefers-reduced-motion', (name) => {
    setReducedMotion(true)
    const t = physics[name] as { type?: string; duration?: number; damping?: number; stiffness?: number }
    expect(t).toEqual(instant)
    expect(t.duration).toBe(0)
    expect(t.type).not.toBe('spring')
    expect(t.stiffness).toBeUndefined()
    expect(t.damping).toBeUndefined()
  })

  it('survives a spread that overrides one field (the reduced-motion leak)', () => {
    setReducedMotion(true)
    // Real call sites do exactly this to nudge a preset's stiffness. Without an
    // explicit `type: 'tween'` on `instant`, the leftover stiffness lets Framer infer
    // a spring again and the collapse leaks straight back through the spread.
    const spread = { ...physics.fluid, stiffness: 240 }
    expect(spread.type).toBe('tween')
    expect(spread.duration).toBe(0)
  })

  it('prefersReducedMotion answers the media query, and false without one', () => {
    setReducedMotion(true)
    expect(prefersReducedMotion()).toBe(true)
    setReducedMotion(false)
    expect(prefersReducedMotion()).toBe(false)
  })
})

describe('preset-bearing variants', () => {
  // A module-level `{ transition: physics.x }` object literal reads the getter ONCE at
  // import: every overlay and list row in the app would then ignore the slider for the
  // rest of the session, and would keep whatever reduced-motion answer was true at
  // import. Both variants are functions so Framer resolves them per animation.
  it.each([['overlayEnter', overlayEnter], ['listItemEnter', listItemEnter]] as const)(
    '%s resolves its preset fresh on every animation', (_name, variants) => {
      runtime.bounciness = 1
      const playful = resolveVariant(variants.animate).transition as { damping?: number }
      runtime.bounciness = 0
      const calm = resolveVariant(variants.animate).transition as { damping?: number }
      expect(playful.damping).not.toBe(calm.damping)

      setReducedMotion(true)
      expect(resolveVariant(variants.animate).transition).toEqual(instant)
    },
  )
})

describe('gesture helpers', () => {
  it('dragSpring is a bounciness-scaled spring that zeroes under reduced motion', () => {
    runtime.bounciness = 0
    const calm = dragSpring() as { type?: string; damping?: number }
    runtime.bounciness = 1
    const playful = dragSpring() as { type?: string; damping?: number }
    expect(calm.type).toBe('spring')
    expect(playful.damping!).toBeLessThan(calm.damping!)

    setReducedMotion(true)
    expect(dragSpring()).toEqual(instant)
  })

  it('dragElastic reads its token and clamps to 0..1', () => {
    runtime.dragElastic = 0.4
    expect(dragElastic()).toBe(0.4)
    runtime.dragElastic = 5
    expect(dragElastic()).toBe(1)
    runtime.dragElastic = -1
    expect(dragElastic()).toBe(0)
  })

  it('dragElastic stays put under reduced motion — the drag is a FUNCTION, not decoration', () => {
    setReducedMotion(true)
    runtime.dragElastic = 0.9
    // Zeroing this would pin a swipe-to-dismiss card against a zero-width constraint
    // box and make the gesture unperformable, which is removing a capability rather
    // than removing motion.
    expect(dragElastic()).toBe(0.9)
  })

  it('swipeDismiss dismisses on a fast flick OR a slow haul past the distance', () => {
    expect(swipeDismiss(runtime.swipeVelocity + 1, 0).dismiss).toBe(true)
    expect(swipeDismiss(0, runtime.swipeDistance + 1).dismiss).toBe(true)
    expect(swipeDismiss(runtime.swipeVelocity - 1, runtime.swipeDistance - 1).dismiss).toBe(false)
    // Either direction, so the helper works on a left/up swipe too; call sites that
    // only accept one direction clamp before calling.
    expect(swipeDismiss(-(runtime.swipeVelocity + 1), 0).dismiss).toBe(true)
  })

  it('swipeDismiss thresholds follow their tokens', () => {
    runtime.swipeVelocity = 1200
    runtime.swipeDistance = 200
    expect(swipeDismiss(600, 100).dismiss).toBe(false)
    expect(swipeDismiss(1300, 0).dismiss).toBe(true)
    expect(swipeDismiss(0, 240).dismiss).toBe(true)
  })

  it('resolves a kept swipe with the return spring and a dismissed one with an exit curve', () => {
    const kept = swipeDismiss(0, 0)
    expect(kept.dismiss).toBe(false)
    expect((kept.transition as { type?: string }).type).toBe('spring')

    const gone = swipeDismiss(9999, 0)
    expect(gone.dismiss).toBe(true)
    // An element that is LEAVING must not overshoot back into the surface it left.
    expect((gone.transition as { type?: string }).type).not.toBe('spring')
    expect((gone.transition as { duration?: number }).duration).toBeGreaterThan(0)
  })

  it('both swipe branches go instant under reduced motion, verdict unchanged', () => {
    setReducedMotion(true)
    expect(swipeDismiss(9999, 0)).toEqual({ dismiss: true, transition: instant })
    expect(swipeDismiss(0, 0)).toEqual({ dismiss: false, transition: instant })
  })
})

// ── Token round trip ───────────────────────────────────────────────────────
// A Motion token that drives JS physics has to exist in THREE places: the registry
// (so the Design panel renders a control), tokens.css (so the group has one declared
// default), and runtime.ts (so motion.ts can read it without getComputedStyle). One
// of the three missing is the defect class this rail catches.
describe('motion token round trip', () => {
  const css = readFileSync(join(process.cwd(), 'src/design/tokens.css'), 'utf8')
  const runtimeScalars = TOKENS.filter((t) => t.kind === 'scalar' && t.runtimeKey)

  it('the gesture-physics tokens are registered in the Motion group', () => {
    for (const varName of ['--drag-elastic', '--swipe-dismiss-velocity', '--swipe-dismiss-distance']) {
      const t = TOKENS.find((x) => x.varName === varName)
      expect(t, `${varName} missing from tokenRegistry`).toBeTruthy()
      expect(t!.group).toBe('Motion')
    }
  })

  it.each(runtimeScalars.map((t) => [t.varName, t] as const))(
    '%s round-trips registry → tokens.css → runtime', (varName, token) => {
      expect(css, `${varName} has no default in tokens.css`).toContain(`${varName}:`)
      const key = (token as { runtimeKey?: string }).runtimeKey!
      expect(runtime, `runtime.${key} missing for ${varName}`).toHaveProperty(key)
      expect(typeof (runtime as unknown as Record<string, unknown>)[key]).toBe('number')
    },
  )

  it('every registered scalar default matches its tokens.css declaration', () => {
    for (const t of runtimeScalars) {
      const m = new RegExp(`${t.varName}:\\s*([^;]+);`).exec(css)
      expect(m, `${t.varName} not declared in tokens.css`).toBeTruthy()
      const declared = parseFloat(m![1])
      expect(declared, `${t.varName}: registry ${(t as { value: number }).value} vs css ${m![1]}`)
        .toBe((t as { value: number }).value)
    }
  })
})
