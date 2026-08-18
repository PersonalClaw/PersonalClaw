/**
 * FLUID-MOTION §S2 (atom FM-4) — the morph family is ONE vocabulary.
 *
 * "Reads as one system on visual review" is a taste judgment, and a screenshot cannot hold a
 * regression to it: two primitives can look fine in isolation on the day they ship and drift
 * apart the next time someone dials a number at one call site. So the coherence is asserted
 * where it is actually decidable — the four primitives resolve their timing to ONE module, and
 * this file proves both halves of that: the SOURCE carries no timing arithmetic of its own, and
 * the RESOLVED values agree about what the user's knob means.
 *
 * Four failures, none of which the others would catch:
 *
 *   1. A primitive hardcodes a transition again (the pre-FM-4 state: two hand-rolled
 *      `expr(70, 0.4)` bonuses with opposite signs, one raw Material bezier). Caught by the
 *      source rail — a primitive may import `expr`/`exprHeavy` for AMPLITUDE, never `physics`,
 *      `spring`, `ease` or `duration` for TIMING.
 *   2. The bases drift out of their travel order, so a bud off a button flies slower than a
 *      full-page morph. Caught by the ordering rail.
 *   3. The expressiveness knob means opposite things in two members — the actual bug this atom
 *      found. Caught by asserting the bonus is the SAME magnitude and the SAME sign everywhere.
 *   4. The floor collapses to zero, quietly turning the aesthetic dial into a second
 *      accessibility switch. Caught by the expressiveness-0 block, which is deliberately
 *      separate from `family.reducedMotion.test.tsx`: those are two different off-switches and
 *      only one of them stops the animation.
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { afterEach, describe, expect, it } from 'vitest'

// Through the BARREL for the reachability rail (`Morph.test.tsx`'s reason): drop the export
// from `ui/motion/index.ts` and every case here fails, typecheck first.
import { MORPH_FAMILY, familyFade, familySpring, familyTween } from './index'
import { duration, ease, exprHeavy, physics, spring } from '../../design/motion'
import { runtime } from '../../design/runtime'

const DEFAULT_EXPRESSIVENESS = runtime.expressiveness
afterEach(() => { runtime.expressiveness = DEFAULT_EXPRESSIVENESS })

/** The four members of the family, by file. Adding a fifth primitive without adding it here is
 *  the one gap this file cannot close for itself, which is why the count is asserted below. */
const MEMBERS = ['Morph.tsx', 'LiquidShape.tsx', 'Bud.tsx', 'Disintegrate.tsx'] as const

const source = (name: string) => readFileSync(join(process.cwd(), 'src/ui/motion', name), 'utf8')

/** Non-comment lines only. Every one of these primitives documents its own tiers in prose that
 *  legitimately names durations and curves; linting the prose would flag the documentation. */
const code = (text: string) => text
  .split('\n')
  .filter((l) => {
    const t = l.trim()
    return t !== '' && !t.startsWith('//') && !t.startsWith('*') && !t.startsWith('/*')
  })
  .join('\n')

/** The named bindings a member imports from `design/motion`, or `null` if it imports none. */
function motionImports(text: string): string[] | null {
  const m = text.match(/import\s*\{([^}]*)\}\s*from\s*'\.\.\/\.\.\/design\/motion'/)
  if (!m) return null
  return m[1].split(',').map((s) => s.trim()).filter(Boolean)
}

describe('the four primitives own no timing of their own', () => {
  it('finds all four members, and they are real files', () => {
    // The vacuity floor. Every assertion below is a "does NOT contain" — a typo in a filename,
    // or a member deleted out from under this file, would pass all of them on empty strings.
    expect(MEMBERS).toHaveLength(4)
    for (const name of MEMBERS) {
      const text = source(name)
      expect(text.length, name).toBeGreaterThan(1000)
      expect(code(text), name).toContain('export function')
    }
  })

  it('each imports the family vocabulary', () => {
    for (const name of MEMBERS) {
      expect(code(source(name)), name).toMatch(/from '\.\/vocabulary'/)
    }
  })

  it('none reaches past it for a preset, a curve or a length', () => {
    // `expr` and `exprHeavy` are AMPLITUDE and the tier gate — a primitive decides how FAR its
    // own effect goes and whether its heavy layer exists at all. `physics`/`spring`/`ease`/
    // `duration` are TIMING, and timing is the family's, not the member's. This is the rail
    // that would have caught `Disintegrate`'s raw `[0.4, 0, 0.2, 1]` and `Bud`'s inverted
    // `260 - expr(70, 0.4)` on the day each was written.
    const AMPLITUDE_ONLY = ['expr', 'exprHeavy']
    for (const name of MEMBERS) {
      const named = motionImports(source(name))
      if (named === null) continue
      expect(named.sort(), `${name} imports timing from design/motion`)
        .toEqual(named.filter((n) => AMPLITUDE_ONLY.includes(n)).sort())
    }
    // …and at least one member really does import amplitude, so the loop above is not vacuous
    // by way of nobody importing anything.
    expect(motionImports(source('Disintegrate.tsx'))).toEqual(['expr', 'exprHeavy'])
  })

  it('none writes a stiffness, a raw duration or a raw bezier', () => {
    for (const name of MEMBERS) {
      const text = code(source(name))
      expect(text, `${name} sets its own stiffness`).not.toMatch(/stiffness:/)
      expect(text, `${name} hardcodes a duration`).not.toMatch(/duration:\s*[\d.]/)
      expect(text, `${name} hardcodes a bezier`).not.toMatch(/ease:\s*\[/)
    }
    // The ONE named exception, asserted rather than waved at: `LiquidShape`'s idle breathe is a
    // perpetual loop driver, and a loop that eases would read as a pulse. It is linear by
    // requirement and its length is a named `TUNING` field, so it is not a fifth curve.
    const liquid = code(source('LiquidShape.tsx'))
    expect(liquid).toContain("ease: 'linear'")
    expect(liquid).toContain('duration: TUNING.breatheCycle')
  })

  it('the heavy/refined tier splits at ONE threshold for the whole family', () => {
    // `exprHeavy(0.7)` in one member and `exprHeavy()` in another would give the family two
    // different definitions of "bold" — the same class of bug as the two stiffness signs, in
    // the amplitude half. Every call site must take the default gate.
    for (const name of MEMBERS) {
      expect(code(source(name)), `${name} passes a custom heavy threshold`)
        .not.toMatch(/exprHeavy\(\s*[^)\s]/)
    }
    expect(code(source('LiquidShape.tsx'))).toContain('exprHeavy()')
    expect(code(source('Disintegrate.tsx'))).toContain('exprHeavy()')
  })

  it('every member self-gates reduced motion in JS', () => {
    // The family's off-switch is one mechanism, not "three self-gate and one delegates to the
    // root MotionConfig" (which is what `Bud` did before this atom, and why its off-switch was
    // the only one you could not assert from the DOM).
    for (const name of MEMBERS) {
      expect(code(source(name)), `${name} does not self-gate`).toContain('useReducedMotion()')
    }
  })
})

describe('all three springs resolve to one preset and one bonus', () => {
  const BASES = [
    ['flight', MORPH_FAMILY.flight],
    ['state', MORPH_FAMILY.state],
    ['spawn', MORPH_FAMILY.spawn],
  ] as const

  const stiffness = (base: number) => (familySpring(base) as { stiffness: number }).stiffness

  it.each(BASES)('%s rides physics.fluid — same damping, same mass', (_name, base) => {
    // Asserting the PRESET identity rather than restating damping numbers is how this rail
    // inherits `bouncy()`'s bounciness scaling and its reduced-motion collapse: the family
    // cannot drift off the preset without this failing.
    const t = familySpring(base) as { type?: string; damping?: number; mass?: number }
    const fluid = physics.fluid as { damping?: number; mass?: number }
    expect(t.type).toBe('spring')
    expect(t.damping).toBe(fluid.damping)
    expect(t.mass).toBe(fluid.mass)
  })

  it('adds the SAME bonus to every base — one knob, one meaning', () => {
    // The bug this atom exists for: `Morph` was `190 + expr(70, 0.4)` and `Bud` was
    // `260 - expr(70, 0.4)`. Identical magnitude, identical floor, opposite sign — so dialing
    // expressiveness up made one primitive tauter and the other slacker.
    // `toBeCloseTo`, not equality: `base + bonus - base` is float subtraction, so three bases
    // that share one bonus by construction still differ in the 14th decimal. A tolerance of
    // 1e-10 is many orders of magnitude below any stiffness anyone could hear.
    const bonuses = BASES.map(([, base]) => stiffness(base) - base)
    for (const b of bonuses) {
      expect(b, `bonuses diverged: ${bonuses.join(', ')}`).toBeCloseTo(bonuses[0], 10)
    }
    expect(bonuses[0]).toBeGreaterThan(0)
  })

  it('BOLD means tauter for every member, in the same direction', () => {
    for (const [name, base] of BASES) {
      runtime.expressiveness = 1
      const bold = stiffness(base)
      runtime.expressiveness = 0
      const refined = stiffness(base)
      expect(bold, `${name} inverts the knob`).toBeGreaterThan(refined)
      expect(bold).toBe(base + MORPH_FAMILY.stiffnessBonus)
    }
  })

  it('orders its bases by TRAVEL — the further it flies, the softer it starts', () => {
    // A cross-page flight on a bud's spring reads as a snap; a bud on a flight's spring reads
    // as lag on a button press. The ordering IS the rule, so it is asserted rather than left
    // to whoever next edits three numbers.
    expect(MORPH_FAMILY.flight).toBeLessThan(MORPH_FAMILY.state)
    expect(MORPH_FAMILY.state).toBeLessThan(MORPH_FAMILY.spawn)
  })
})

describe('the family has exactly one tween and one fade', () => {
  it('the dissolve rides the house emphasized curve, both tiers', () => {
    const bold = familyTween(true) as { duration: number; ease: unknown }
    const refined = familyTween(false) as { duration: number; ease: unknown }
    // ONE curve across the tiers: the refined tier shortens the dissolve, it does not re-shape
    // it. And the curve is the house's, not the Material standard curve this used to hardcode —
    // `motion.ts` says in as many words that its curves are "NOT the literal Material M3 values".
    expect(bold.ease).toBe(ease.emphasized)
    expect(refined.ease).toBe(ease.emphasized)
    expect(bold.duration).toBe(duration.medium)
    expect(refined.duration).toBeCloseTo(duration.medium * MORPH_FAMILY.refinedScale, 10)
    expect(refined.duration).toBeLessThan(bold.duration)
    expect(refined.duration).toBeGreaterThan(0)
  })

  it('the fade IS the app fade — no length invented for "something is fading"', () => {
    expect(familyFade()).toBe(spring.effects)
  })
})

describe('expressiveness 0 is the FLOOR, not the off switch', () => {
  // Deliberately its own block, and deliberately NOT the reduced-motion file. These are two
  // separate off-switches and they do different things: at expressiveness 0 the family still
  // animates, on a calmer spring; under `prefers-reduced-motion` it does not animate at all.
  // Conflating them is how a "zero motion" claim gets made about a state that is still moving.
  it('still returns a real spring, at the floor of the bonus', () => {
    runtime.expressiveness = 0
    for (const base of [MORPH_FAMILY.flight, MORPH_FAMILY.state, MORPH_FAMILY.spawn]) {
      const t = familySpring(base) as { type?: string; stiffness: number }
      expect(t.type).toBe('spring')
      expect(t.stiffness).toBe(base + MORPH_FAMILY.stiffnessBonus * MORPH_FAMILY.floor)
      // The floor is what makes refined CALM rather than dead — a bare base would mean the
      // aesthetic dial had quietly become a second accessibility switch.
      expect(t.stiffness).toBeGreaterThan(base)
    }
  })

  it('drops the heavy tier — the one thing that does switch OFF at 0', () => {
    runtime.expressiveness = 0
    expect(exprHeavy()).toBe(false)
    runtime.expressiveness = DEFAULT_EXPRESSIVENESS
    expect(exprHeavy()).toBe(true)
    // …and the dissolve gets shorter, not merely gentler, once there is nothing to fragment.
    expect((familyTween(false) as { duration: number }).duration)
      .toBeLessThan((familyTween(true) as { duration: number }).duration)
  })
})
