/**
 * FLUID-MOTION §S2 (atoms FM-3 / FM-4) — the WHOLE morph family under reduced motion.
 *
 * `Morph.reducedMotion.test.tsx`, `LiquidShape.reducedMotion.test.tsx` and
 * `Entrance.reducedMotion.test.tsx` each cover one primitive in depth. This file covers the
 * property that only exists ACROSS members: **one off-switch, one mechanism, one accessor,
 * assertable from the DOM for every member.**
 *
 * 🔑 THE CASES ARE DERIVED, NOT ENUMERATED (FM-3). This file used to hard-code four imports and
 * four `it` cases, which made it a rail that structurally could not fail for a component it did
 * not name — and two unnamed components were in fact broken. `WavyProgress` had NO reduced-motion
 * branch at all: its indeterminate crest traveled forever, because `pathOffset` is not a transform
 * and the root `<MotionConfig reducedMotion="user">` therefore never touched it. `DotGlow` answered
 * the question its own way, with a raw and unguarded `window.matchMedia` call. The enumeration now
 * lives in `reducedMotionFamily.tsx` as data, every case below is generated from it, and the
 * coverage floor at the bottom fails when a family component has no row — so the next component is
 * covered by adding a row, and forgetting the row is a red, not a gap.
 *
 * 🔑 ONE ACCESSOR, AND IT IS `design/motion`'s `prefersReducedMotion()` (FM-3). The family decided
 * this question three ways: framer's `useReducedMotion` (Morph/Bud/Disintegrate/LiquidShape), a raw
 * `matchMedia` read (DotGlow), and not at all (WavyProgress). They converged on the module that
 * already declares itself the single decision point — which `Entrance` was already doing, by taking
 * its gate from a `design/motion` helper rather than deciding for itself. The choice of survivor was
 * forced rather than aesthetic: framer caches its probe in a MODULE SINGLETON, and
 * `DotGlow.reducedMotion.test.tsx` measures both regimes in one file by swapping the stub between
 * them, so a cached probe would have broken a shipped rail.
 *
 * 🔑 WHICH IS WHY THIS FILE CAN NOW MEASURE BOTH REGIMES. A call-time accessor is flippable, so
 * every case asserts the attribute under reduced motion AND under motion allowed. That second half
 * is the negative control the old file could not have: a hardcoded `data-x="instant"` satisfies the
 * reduced assertion on its own and cannot satisfy both.
 *
 * Reduced motion here means INSTANT, not fast, and every case carries a positive control —
 * rendering nothing satisfies every "no motion" assertion in this file for free.
 */

import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

// The OS preference, live and flippable: `matches` is a getter rather than a snapshot, so
// `REDUCE` is read when the component asks instead of when this stub was installed.
let REDUCE = true

Object.defineProperty(window, 'matchMedia', {
  configurable: true,
  writable: true,
  value: (query: string) => ({
    get matches() {
      // Only the reduced-motion query answers from REDUCE; everything else keeps the
      // suite's default (the widest desktop render).
      return query.includes('prefers-reduced-motion') ? REDUCE : false
    },
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
    onchange: null,
  }) as unknown as MediaQueryList,
})

// DotGlow resolves its backdrop mode from the appearance store; 'waves' is the default and the
// only mode with a live loop to suppress. jsdom implements no canvas, so DotGlow's effect returns
// at the null 2D context — which is fine here, because the branch this file reads is stated in
// RENDER. Frame-level proof that the loop stops is `DotGlow.reducedMotion.test.tsx`'s job.
vi.mock('../../app/appearance', () => ({
  useAppearance: () => ({ selectValue: () => 'waves' }),
}))

const { REDUCED_MOTION_FAMILY } = await import('./reducedMotionFamily')
const { Disintegrate, MORPH_FAMILY, familySpring } = await import('./index')
const { instant } = await import('../../design/motion')

beforeEach(() => { REDUCE = true })

describe('every family member takes an instant branch, and says so in the DOM', () => {
  it('the registry is NOT EMPTY — the vacuity floor under every derived case below', () => {
    // A generated suite over an empty list is green and measures nothing, which is the
    // failure mode this whole file was rewritten to escape. Assert the denominator.
    expect(REDUCED_MOTION_FAMILY.length).toBeGreaterThan(0)
  })

  for (const member of REDUCED_MOTION_FAMILY) {
    it(member.id, () => {
      REDUCE = true
      const reduced = render(member.render())
      const off = reduced.container.querySelector(member.selector)
      expect(off, `nothing matched ${member.selector} under reduced motion`).not.toBeNull()
      expect(off!.getAttribute(member.attr)).toBe(member.reduced)
      // Positive control: an empty render answers "no motion" for free.
      expect(member.positiveControl(off!), 'positive control failed — it rendered nothing useful').toBeTruthy()
      reduced.unmount()

      // The other direction. Without it, a constant attribute would pass above.
      REDUCE = false
      const allowed = render(member.render())
      const on = allowed.container.querySelector(member.selector)
      expect(on, `nothing matched ${member.selector} with motion allowed`).not.toBeNull()
      expect(on!.getAttribute(member.attr), 'the attribute is a constant, not a branch').toBe(member.animated)
      allowed.unmount()
    })
  }
})

describe('the members whose off-switch is more than an attribute', () => {
  it('Disintegrate resolves, and takes the tinted wash with it', async () => {
    // The registry reads Disintegrate at `active: false`, because its reduced branch
    // UNMOUNTS once the delete resolves and an unmounted component states nothing. This is
    // the other half: driven to `active`, it resolves with no animation at all.
    const onDone = vi.fn()
    const { rerender } = render(
      <Disintegrate active={false} onDone={onDone}><p>row</p></Disintegrate>,
    )
    // Positive control first: inactive under reduced motion still renders the row.
    expect(screen.getByText('row')).toBeInTheDocument()
    expect(onDone).not.toHaveBeenCalled()

    rerender(<Disintegrate active onDone={onDone}><p>row</p></Disintegrate>)
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1))
    // No motion tree survives, so there is no tinted wash left to fade either — which is why
    // `familyFade()` is deliberately ungated: under reduced motion it never renders.
    expect(screen.queryByText('row')).not.toBeInTheDocument()
    expect(document.querySelector('.pointer-events-none')).toBeNull()
  })
})

describe('the shared spring collapses, and collapses CLEANLY', () => {
  it('returns `instant` untouched for every base — no spring residue', () => {
    // The spread hazard `motion.ts` documents on `instant`: `{ ...physics.fluid, stiffness: N }`
    // re-introduces a spring from the leftover `stiffness` even after the preset collapsed.
    // One helper now carries this for the whole family, so one assertion covers all of it.
    for (const base of [MORPH_FAMILY.flight, MORPH_FAMILY.state, MORPH_FAMILY.spawn]) {
      const t = familySpring(base) as Record<string, unknown>
      expect(t).toEqual(instant)
      expect(t.type).toBe('tween')
      expect(t.duration).toBe(0)
      expect(t.stiffness).toBeUndefined()
      expect(t.damping).toBeUndefined()
    }
  })

  it('is INSTANT, not merely quick — and that is a different state from expressiveness 0', async () => {
    // The two off-switches, side by side, in the state where the difference is visible. The
    // expressiveness knob cannot reach this: `vocabulary.test.ts` proves that at expressiveness
    // 0 the same call returns a real spring at the floor of the bonus. Here it returns no
    // animation at all, whatever the knob says.
    const { runtime } = await import('../../design/runtime')
    const before = runtime.expressiveness
    try {
      runtime.expressiveness = 1
      expect(familySpring(MORPH_FAMILY.flight)).toEqual(instant)
      runtime.expressiveness = 0
      expect(familySpring(MORPH_FAMILY.flight)).toEqual(instant)
    } finally {
      runtime.expressiveness = before
    }
  })
})

describe('coverage floor: the registry cannot fall behind the family', () => {
  // The scan is scoped to the motion family itself — `ui/motion/*` plus the top level of
  // `ui/`. Surfaces elsewhere that also call the accessor (`pages/knowledge/ReadingView`,
  // `pages/dashboard/world/AgentWorld`, `pages/settings/PersonalityPicker`,
  // `ui/personality/TerminalStrip`) are deliberately OUT of this rail's scope and carry their
  // own; stated here so the boundary is a decision on the record rather than a silent gap.
  const UI = join(process.cwd(), 'src/ui')
  const DIRS = [
    { abs: UI, rel: 'ui' },
    { abs: join(UI, 'motion'), rel: 'ui/motion' },
  ]

  /** Every family source file that decides reduced motion ITSELF, i.e. calls the accessor.
   *  A component that instead takes a ready-gated transition from a `design/motion` helper
   *  (`Entrance` is the one that does) is gated centrally, is covered by its own rail, and is
   *  not expected to carry a row here. */
  function decidesItself(): string[] {
    const found: string[] = []
    for (const { abs, rel } of DIRS) {
      for (const file of readdirSync(abs)) {
        if (!file.endsWith('.tsx') || file.includes('.test.')) continue
        const src = readFileSync(join(abs, file), 'utf8')
        // Strip comments, so this file's own prose about the accessor cannot register a component.
        const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
        if (/\bprefersReducedMotion\s*\(/.test(code)) found.push(`${rel}/${file}`)
      }
    }
    return found.sort()
  }

  it('finds the family it is scanning — the floor under the two claims below', () => {
    // A typo in the scan path yields an empty list, and an empty list satisfies "every file has
    // a row" perfectly. Pin that the scan actually reached the components.
    const scanned = decidesItself()
    expect(scanned.length).toBeGreaterThanOrEqual(6)
    expect(scanned).toContain('ui/WavyProgress.tsx')
    expect(scanned).toContain('ui/motion/LiquidShape.tsx')
  })

  it('every family component that decides reduced motion has a registry row', () => {
    const registered = new Set(REDUCED_MOTION_FAMILY.map((m) => m.source))
    const missing = decidesItself().filter((f) => !registered.has(f))
    expect(
      missing,
      `these decide reduced motion but no case covers them — add a row to reducedMotionFamily.tsx: ${missing.join(', ')}`,
    ).toEqual([])
  })

  it('and every registry row cites a real file that really decides it', () => {
    // The other direction: a row pointing at a moved or renamed file would keep the suite green
    // while covering nothing, and `source` is what the floor above joins on.
    for (const member of REDUCED_MOTION_FAMILY) {
      const src = readFileSync(join(process.cwd(), 'src', member.source), 'utf8')
      expect(src, `${member.source} (cited by "${member.id}") does not call the accessor`)
        .toMatch(/\bprefersReducedMotion\s*\(/)
    }
  })
})
