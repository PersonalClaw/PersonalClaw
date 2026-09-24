import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ProgressRing } from './ProgressRing'

// ── One ring, and the copy that silently lost its animation ────────────────────
//
// `ProgressRing` existed TWICE — `dashboard/widgets/ActiveWork` and `loops/LoopsListPage` — with
// identical signatures and identical geometry: r = size/2 - 2.5, 2.5 stroke, a `surface-high` track,
// round cap, rotated -90° so the arc grows clockwise from 12 o'clock. Every number agreed.
//
// The ONE difference was invisible in a still screenshot and obvious in use: the dashboard tweened
// the arc through `motion.circle` + `spring.spatialSlow`, while the list wrote `strokeDashoffset`
// straight onto a plain `<circle>`. So the same ring animated on one surface and JUMPED on the other
// — inside a row that itself animates (motion.div + a ContextMenu). Not two designs; one design and
// one copy that lost a behaviour.
//
// The animated form wins: a cycle completing is exactly the moment worth showing, and a value that
// slides reads as progress ADVANCING rather than being replaced.
//
// Two contracts this pins that a plain "it renders" test would not:
//   · MOUNT IS SILENT (`initial={false}`). Without it, navigating to a list of eight loops sweeps
//     eight arcs up from zero every time — an animation that reports nothing, since none of those
//     values just changed.
//   · REDUCED MOTION sets the arc directly, matching the global token rule (tokens.css collapses
//     durations under prefers-reduced-motion rather than easing them).

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

/** The arc is the SECOND circle — the first is the unfilled track. */
function arcOf(container: HTMLElement): SVGCircleElement {
  const circles = container.querySelectorAll('circle')
  expect(circles.length, 'a track circle and an arc circle').toBe(2)
  return circles[1] as SVGCircleElement
}

describe('ProgressRing geometry', () => {
  it('renders a track and an arc with the shared geometry', () => {
    const { container } = render(<ProgressRing label="Cycle progress" pct={0.5} tone="var(--color-primary)" />)
    const svg = container.querySelector('svg')!
    expect(svg.getAttribute('width')).toBe('28')
    expect(svg.getAttribute('viewBox')).toBe('0 0 28 28')

    const circles = container.querySelectorAll('circle')
    // r = size/2 - 2.5 — the value both copies used, so a migration can't quietly resize the ring.
    expect(circles[0].getAttribute('r')).toBe('11.5')
    expect(circles[0].getAttribute('stroke')).toBe('var(--color-surface-high)')
    expect(circles[0].getAttribute('stroke-width')).toBe('2.5')
  })

  it('starts the arc at 12 o_clock and takes the caller tone', () => {
    const { container } = render(<ProgressRing label="Cycle progress" pct={0.25} tone="var(--color-warn)" />)
    const arc = arcOf(container as HTMLElement)
    expect(arc.getAttribute('stroke')).toBe('var(--color-warn)')
    expect(arc.getAttribute('stroke-linecap')).toBe('round')
    // Without the -90 rotation the arc would grow from 3 o'clock.
    expect(arc.getAttribute('transform')).toBe('rotate(-90 14 14)')
  })

  it('honours size, and scales the radius with it', () => {
    const { container } = render(<ProgressRing label="Cycle progress" pct={1} tone="red" size={40} />)
    expect(container.querySelector('svg')!.getAttribute('width')).toBe('40')
    expect(container.querySelector('circle')!.getAttribute('r')).toBe('17.5')
  })

  it('takes pct as a FRACTION: the dash array is the full circumference', () => {
    // 0..1, not 0..100 — the contract both call sites already relied on (they pass
    // Math.min(1, total/max)). A 0..100 reading would wind the arc round 100 times.
    const { container } = render(<ProgressRing label="Cycle progress" pct={0.5} tone="red" />)
    const c = 2 * Math.PI * 11.5
    expect(Number(arcOf(container as HTMLElement).getAttribute('stroke-dasharray'))).toBeCloseTo(c, 3)
  })
})

describe('the divergence this primitive resolves', () => {
  it('the arc is a motion element with a spring — not a plain circle', () => {
    // THE defect, asserted at its source: LoopsListPage's copy rendered a plain <circle> with
    // strokeDashoffset set directly, so it could not animate at all. Reverting the primitive to that
    // shape reds this.
    const src = read('ui/ProgressRing.tsx')
    expect(src).toMatch(/<motion\.circle/)
    expect(src).toMatch(/animate=\{\{ strokeDashoffset: offset \}\}/)
    expect(src).toMatch(/spring\.spatialSlow/)
  })

  it('mount is silent — initial={false}, so a list does not sweep every arc from zero', () => {
    expect(read('ui/ProgressRing.tsx')).toMatch(/initial=\{false\}/)
  })

  it('reduced motion sets the arc directly instead of easing it', () => {
    const src = read('ui/ProgressRing.tsx')
    expect(src).toMatch(/useReducedMotion/)
    expect(src).toMatch(/reduce \? \{ duration: 0 \} : spring\.spatialSlow/)
  })

  it('neither page declares its own ProgressRing any more', () => {
    for (const rel of ['pages/dashboard/widgets/ActiveWork.tsx', 'pages/loops/LoopsListPage.tsx']) {
      const src = read(rel)
      expect(/function ProgressRing\b/.test(src), `${rel} should not declare its own ring`).toBe(false)
      expect(src).toMatch(/import \{ ProgressRing \} from '.*ui\/ProgressRing'/)
    }
  })

  it('LoopsListPage no longer sets strokeDashoffset by hand anywhere', () => {
    // The specific line that made the list's ring jump. Pinning its absence keeps a future
    // "just inline it here" edit from re-creating the divergence.
    //
    // Strip comments first: the migration note in that file NAMES `strokeDashoffset` to explain
    // what was removed, and a bare text search counts the explanation as the defect. (Last cycle
    // the same shape inflated a design metric — prose is not code.)
    const code = read('pages/loops/LoopsListPage.tsx')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '')
    expect(/strokeDashoffset/.test(code)).toBe(false)
  })
})

describe('the ring announces itself, and its abbreviation has a full word', () => {
  // ── Cycle 178: a graphic that was the only carrier of a number, and said nothing ──────────────
  //
  // Read from the live AX tree on BOTH call sites (`#/loops/history` and `#/dashboard`): the svg had
  // **no `role`, no `aria-label`, no `aria-hidden` and no `<title>`**. Not named, and not marked
  // decorative either — so assistive tech got nothing where a sighted user reads a progress arc.
  // WCAG 1.1.1 / 4.1.2.
  //
  // 🔑 THE SEMANTICS CONVERGE ON `ui/WavyProgress`, this app's other progress indicator, which already
  // ships `role="progressbar"` + `aria-valuemin/max/now`. What that one does NOT ship is a NAME, so a
  // screen-reader user hears a bare percentage with no subject — which is why `label` is REQUIRED
  // here rather than optional. Making it required also proved its worth immediately: TypeScript
  // listed every call site, including four in this file's sibling motion test, so none could be
  // missed.
  //
  // 🪤 AND THE ABBREVIATION BESIDE IT. "0 fnd" is an abbreviation nothing else in the app uses, and a
  // screen reader reads it literally. The visible form CANNOT grow — the box is `w-9` (36px, measured
  // unchanged after the fix) and widening it reflows the row — so the abbreviation stays for the eye
  // while the full word is added for assistive tech through the `sr-only` idiom already used in 19
  // places, with `title` giving a sighted user the same expansion on hover.
  //
  // Verified after: `#/loops/history` → one progressbar named "Cycle progress: 0 of 30"
  // (`aria-valuenow=0`, `aria-valuemax=100`), abbreviation visible "0 fnd" / AT "0 findings", box
  // still 36px. `#/dashboard` → one progressbar named "Cycle progress for <loop name>", valuenow 40.

  it('is a progressbar with a value, not a bare svg', () => {
    const { container } = render(<ProgressRing label="Cycle progress" pct={0.4} tone="red" />)
    const svg = container.querySelector('svg')!
    expect(svg.getAttribute('role')).toBe('progressbar')
    expect(svg.getAttribute('aria-valuemin')).toBe('0')
    expect(svg.getAttribute('aria-valuemax')).toBe('100')
    expect(svg.getAttribute('aria-valuenow')).toBe('40')
  })

  it('carries the name its caller gave it', () => {
    const { container } = render(<ProgressRing label="Cycle progress: 3 of 8" pct={0.375} tone="red" />)
    expect(container.querySelector('svg')!.getAttribute('aria-label')).toBe('Cycle progress: 3 of 8')
  })

  it('clamps the reported value the way the arc does', () => {
    // A loop that ran past its budget reads full rather than reporting 140%.
    const over = render(<ProgressRing label="x" pct={1.4} tone="red" />).container.querySelector('svg')!
    const under = render(<ProgressRing label="x" pct={-0.2} tone="red" />).container.querySelector('svg')!
    expect(over.getAttribute('aria-valuenow')).toBe('100')
    expect(under.getAttribute('aria-valuenow')).toBe('0')
  })

  it('every call site names what it is counting', () => {
    const SRC = join(process.cwd(), 'src')
    const sites = ['pages/loops/LoopsListPage.tsx', 'pages/dashboard/widgets/ActiveWork.tsx']
    for (const rel of sites) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, `${rel} must pass a label`).toMatch(/<ProgressRing[\s\S]{0,220}?label=\{`Cycle progress/)
    }
  })

  it("the loops row's abbreviation keeps a full-word equivalent", () => {
    const src = readFileSync(join(process.cwd(), 'src/pages/loops/LoopsListPage.tsx'), 'utf8')
    expect(src, 'the eye keeps the abbreviation').toMatch(/aria-hidden="true">\{c\.findings\?\.length \?\? 0\} fnd</)
    expect(src, 'assistive tech gets the word').toMatch(/className="sr-only">\{c\.findings\?\.length \?\? 0\} findings</)
    expect(src, 'and a hover expansion').toMatch(/title=\{`\$\{c\.findings\?\.length \?\? 0\} findings`\}/)
  })
})
