/**
 * FLUID-MOTION §S2 T2.1 (atom FM-2) — `Morph`, the shared-element wrapper, with motion ALLOWED.
 * The paired reduced-motion case lives in `Morph.reducedMotion.test.tsx`; between them the
 * gate is non-vacuous in both directions (neither file can pass by rendering nothing).
 *
 * What a DOM test can and cannot see here, stated plainly so the rails below aren't mistaken
 * for more than they are:
 *
 *   • `layoutId` is NOT a DOM attribute. Framer consumes it as a prop and writes only
 *     transforms, and jsdom has no layout — every box measures 0, so no shared transition can
 *     ever actually run in this environment. A test that claimed to have watched a card fly is
 *     lying. The morph itself was verified by driving a browser (see the atom's evidence).
 *   • What IS assertable, and what each rail therefore pins:
 *       – the BRANCH taken (`data-morph`), which is the reduced-motion decision;
 *       – the TRANSITION object, which is where the bounciness/expressiveness knobs and the
 *         reduced-motion collapse enter;
 *       – REACHABILITY through the barrel, because a primitive missing from `ui/motion/index.ts`
 *         is a file nobody can import;
 *       – that BOTH ENDS of the real wired morph declare the SAME id. That last one is the
 *         defect this whole family is prone to: two ends whose ids have drifted apart render
 *         perfectly, animate nothing, and raise no error anywhere.
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'

// Through the BARREL, not './Morph'. This import is the reachability rail: drop the export
// from `ui/motion/index.ts` and every case in this file fails at render with an undefined
// component (and typecheck fails first).
import { Morph, MORPH_FAMILY, familySpring } from './index'
import { runtime } from '../../design/runtime'
import { physics } from '../../design/motion'

const DEFAULT_EXPRESSIVENESS = runtime.expressiveness
afterEach(() => { runtime.expressiveness = DEFAULT_EXPRESSIVENESS })

const read = (name: string) => readFileSync(join(process.cwd(), 'src', name), 'utf8')
const root = () => document.querySelector<HTMLElement>('[data-morph]')!

describe('Morph — the shared-element branch', () => {
  it('takes the shared branch and keeps the layout classes it was handed', () => {
    // The className matters more than it looks: on the artifacts grid this wrapper IS the
    // grid item, so losing it silently changes the row's geometry rather than its motion.
    render(<Morph id="artifact-x" className="grid"><p>card</p></Morph>)
    expect(root()).toHaveAttribute('data-morph', 'shared')
    expect(root().className).toBe('grid')
    expect(screen.getByText('card')).toBeInTheDocument()
  })

  it('still passes clicks through to its children', () => {
    // Positive control for the whole file, and the soul guardrail's floor: "the task always
    // wins". A wrapper that swallowed the card's click would be a broken library, animated.
    const onPoke = vi.fn()
    render(<Morph id="artifact-x"><button type="button" onClick={onPoke}>open</button></Morph>)
    screen.getByRole('button', { name: 'open' }).click()
    expect(onPoke).toHaveBeenCalledTimes(1)
  })

  it('declares layoutId and NOT layout — the property no DOM assertion can reach', () => {
    // `layout` would additionally animate each end's own size changes: on a grid that means
    // every filter, scroll and hover re-measures every card. The docblock explains why it is
    // absent; this is what keeps it absent.
    const src = read('ui/motion/Morph.tsx')
    expect(src).toMatch(/layoutId=\{id\}/)
    expect(src, 'Morph must not add the `layout` prop — see its docblock on measuring cost')
      .not.toMatch(/<motion\.div[^>]*\slayout(\s|=|\})/)
  })
})

describe('the morph transition', () => {
  it('is the fluid preset, stiffened, so it tracks BOTH personality knobs', () => {
    // fluid == "large surfaces, layout shifts, morphs" (motion.ts), and every physics preset
    // routes through bouncy() — the single seam the bounciness slider enters. Asserting the
    // preset identity is how this rail inherits that, rather than restating damping numbers.
    const t = familySpring(MORPH_FAMILY.flight) as { type?: string; damping?: number; stiffness?: number }
    const fluid = physics.fluid as { damping?: number }
    expect(t.type).toBe('spring')
    expect(t.damping).toBe(fluid.damping)
  })

  it('scales its stiffness with expressiveness, bounded by the named constants', () => {
    // Bold flies tauter, refined glides — and refined keeps `MORPH_FAMILY.floor` of the bonus
    // rather than dropping to the bare base, so the refined tier is calm, not dead.
    const stiffness = () => (familySpring(MORPH_FAMILY.flight) as { stiffness: number }).stiffness
    runtime.expressiveness = 1
    const bold = stiffness()
    runtime.expressiveness = 0
    const refined = stiffness()

    expect(bold).toBe(MORPH_FAMILY.flight + MORPH_FAMILY.stiffnessBonus)
    expect(refined).toBe(MORPH_FAMILY.flight + MORPH_FAMILY.stiffnessBonus * MORPH_FAMILY.floor)
    expect(refined).toBeGreaterThan(MORPH_FAMILY.flight)
    expect(bold).toBeGreaterThan(refined)
  })
})

describe('the one real morph is wired at BOTH ends', () => {
  // A one-sided wiring is the silent failure mode: the grid alone, or the viewer alone, looks
  // completely correct and morphs nothing. So read both files and require the same id.
  const grid = read('pages/artifacts/ArtifactGrid.tsx')
  const section = read('pages/artifacts/ArtifactsSection.tsx')

  it('the library card is the opening end', () => {
    expect(grid).toMatch(/<Morph[^>]*id=\{`artifact-\$\{a\.slug\}`\}/)
    expect(grid).toMatch(/from '\.\.\/\.\.\/ui\/motion'/)
  })

  it('the full-page viewer is the closing end, on the SAME id', () => {
    expect(section).toMatch(/<Morph[^>]*id=\{`artifact-\$\{slug\}`\}/)
  })

  it('the two ends are never mounted at once — the precondition for a morph at all', () => {
    // Framer morphs when one `layoutId` end leaves in the same commit another arrives. Two
    // ends alive together is a different (and wrong) animation, and a target that mounts a
    // tick later is no animation at all. `ArtifactsSection` renders `slug ? viewer : grid`,
    // and `slug` is derived synchronously from the route — no await on either path.
    expect(section).toMatch(/const slug = \(sub \|\| ''\)\.split\('\/'\)\[0\] \|\| ''/)
    expect(section).toMatch(/\{slug \? \(/)
    // ...and the grid's data is held ABOVE the swap, so pressing Back re-renders rows in the
    // same commit instead of a skeleton. A refetch-on-return would kill the return morph.
    expect(section).toMatch(/const \[artifacts, setArtifacts\] = useState<Artifact\[\]>\(\[\]\)/)
  })
})
