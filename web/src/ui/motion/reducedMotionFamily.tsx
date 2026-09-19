/**
 * THE REDUCED-MOTION FAMILY REGISTRY (atom FM-3).
 *
 * One declarative row per component that decides whether to animate. `family.reducedMotion.test.tsx`
 * derives its cases from this list, so covering a new component is adding a ROW here — not editing a
 * test — and forgetting to add the row is itself a failure rather than a silent gap.
 *
 * 🔑 WHY THIS FILE EXISTS. The rail used to hard-code four imports and four `it` cases. That shape
 * cannot fail for a component it does not name, and two components it did not name had real defects:
 * `WavyProgress` had no reduced-motion branch at all (its indeterminate crest traveled forever, since
 * `pathOffset` is not a transform and the root `<MotionConfig reducedMotion="user">` therefore never
 * touched it), and `DotGlow` decided the question its own way with a raw, unguarded `window.matchMedia`
 * call. An enumerated rail is blind exactly where the enumeration stops, so the enumeration moved out
 * of the test and became data with a coverage floor over it.
 *
 * 🔑 WHAT EVERY MEMBER MUST SHARE — and it is a DOM fact, not a convention: the component states which
 * branch it took in an attribute. That is the only property assertable across members whose branches
 * are otherwise distinguishable solely by inline styles Framer owns. Each row carries BOTH values, so
 * the rail can prove the attribute is a real branch rather than a constant that happens to read right.
 *
 * 🪤 THE ATTRIBUTE NAMES ARE DELIBERATELY NOT UNIFORM. `data-morph`/`data-bud`/`data-liquid-shape`
 * shipped before this registry and are read by their own sibling rails; renaming them to one key would
 * be churn with no reader asking for it. Carrying the pair per row is precisely what a registry buys.
 */

import type { ReactElement } from 'react'

import { DotGlow } from '../DotGlow'
import { WavyProgress } from '../WavyProgress'
import { Bud } from './Bud'
import { Disintegrate } from './Disintegrate'
import { LiquidShape } from './LiquidShape'
import { Morph } from './Morph'

export type ReducedMotionMember = {
  /** Registry key, and the name the derived test case reports under. */
  id: string
  /** Path relative to `web/src`. The rail's coverage floor reads this to prove every source
   *  file that decides reduced motion has a row here. */
  source: string
  /** Render it in the state whose motion would otherwise run. */
  render: () => ReactElement
  /** Selects the element that STATES the decision. */
  selector: string
  /** The attribute carrying the decision… */
  attr: string
  /** …its value when the platform asked for less motion… */
  reduced: string
  /** …and when it did not. The negative control: a hardcoded attribute passes the
   *  reduced assertion on its own, and cannot pass both. */
  animated: string
  /** The component still did its job. Returns the thing that must be there, so a
   *  component that rendered nothing — which satisfies every "no motion" assertion for
   *  free — fails instead of passing. */
  positiveControl: (el: Element) => unknown
}

export const REDUCED_MOTION_FAMILY: ReducedMotionMember[] = [
  {
    id: 'Morph — drops the shared element',
    source: 'ui/motion/Morph.tsx',
    render: () => <Morph id="artifact-x"><p>card</p></Morph>,
    selector: '[data-morph]',
    attr: 'data-morph',
    reduced: 'none',
    animated: 'shared',
    // The card is still a card: children survive the branch that removes the flight.
    positiveControl: (el) => el.textContent?.includes('card') || null,
  },
  {
    id: 'Bud — drops the squish, keeping no transform and no projection node',
    source: 'ui/motion/Bud.tsx',
    render: () => <Bud from="top" className="p-2"><button type="button">pick</button></Bud>,
    selector: '[data-bud]',
    attr: 'data-bud',
    reduced: 'instant',
    animated: 'grown',
    // The panel is still a panel and its control is still reachable.
    positiveControl: (el) => el.querySelector('button'),
  },
  {
    id: 'LiquidShape — renders the target silhouette directly',
    source: 'ui/motion/LiquidShape.tsx',
    render: () => <LiquidShape from="circle" to="blob" active />,
    selector: 'svg[data-liquid-shape]',
    attr: 'data-liquid-shape',
    reduced: 'instant',
    animated: 'morph',
    // Real geometry, not an empty svg.
    positiveControl: (el) => el.querySelector('path')?.getAttribute('d')?.match(/^M[\d.]/) || null,
  },
  {
    id: 'Disintegrate — resolves with no animation and no danger wash',
    source: 'ui/motion/Disintegrate.tsx',
    // Held at `active: false` on purpose. Its reduced branch UNMOUNTS once the delete
    // resolves, and an unmounted component states nothing — so the branch is read in the
    // state where the row still exists, which is also the state a user sees it in.
    render: () => <Disintegrate active={false}><p>row</p></Disintegrate>,
    selector: '[data-disintegrate]',
    attr: 'data-disintegrate',
    reduced: 'instant',
    animated: 'animated',
    // The row is intact, and the tinted wash the animated branch overlays is absent.
    positiveControl: (el) => (el.textContent?.includes('row') ? !el.querySelector('.pointer-events-none') || null : null),
  },
  {
    id: 'DotGlow — the halftone backdrop goes static',
    source: 'ui/DotGlow.tsx',
    render: () => <DotGlow />,
    selector: '[data-dot-glow]',
    attr: 'data-dot-glow',
    reduced: 'instant',
    animated: 'animated',
    // The layer is still mounted (it paints one static frame); its canvas is the proof
    // that the decision is not "render nothing".
    positiveControl: (el) => el.querySelector('canvas'),
  },
  {
    id: 'WavyProgress (indeterminate) — the traveling crest never starts',
    source: 'ui/WavyProgress.tsx',
    render: () => <WavyProgress />,
    selector: 'svg[data-wavy-progress]',
    attr: 'data-wavy-progress',
    reduced: 'instant',
    animated: 'animated',
    // A real wave is drawn. This is the branch that regressed: an indefinite animation
    // cannot be collapsed to `instant` (duration 0 + `repeat: Infinity` is an unbounded
    // loop), so it must not be STARTED — and "not started" is one keystroke from
    // "not rendered", which this control separates.
    positiveControl: (el) => el.querySelector('path')?.getAttribute('d')?.match(/^M[\d.]/) || null,
  },
  {
    id: 'WavyProgress (determinate) — the fill lands instantly, still named',
    source: 'ui/WavyProgress.tsx',
    render: () => <WavyProgress value={0.42} label="Downloading llama3" />,
    selector: 'svg[data-wavy-progress]',
    attr: 'data-wavy-progress',
    reduced: 'instant',
    animated: 'animated',
    // Reduced motion must not cost the accessible name or the reported value — the
    // a11y contract `WavyProgress.test.tsx` pins has to hold on BOTH branches.
    positiveControl: (el) =>
      el.getAttribute('aria-label') === 'Downloading llama3' && el.getAttribute('aria-valuenow') === '42' ? true : null,
  },
]
