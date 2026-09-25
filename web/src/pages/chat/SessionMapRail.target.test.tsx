import { describe, expect, it } from 'vitest'
import { act, fireEvent, render, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { SessionMapRail } from './SessionMapRail'
import { sessionMapMarks } from './sessionMap'
import type { ChatTurn, Segment } from './chatTypes'

// ── SSM-8 — hit target, focus ring, and something for reduced motion to collapse ─────────────
//
// done_when: a mark's pressable area clears the one the tick form had; `focusRingPerElement` /
// `focusRingContrast` pass; every rail animation collapses to instant under
// `prefers-reduced-motion`.
//
// 🪤 THIS ATOM IS THE CLEAREST VACUITY TRAP IN THE SSM TAIL, and the trap is that all three named
// rails were already GREEN while none of them looked at the Session Map:
//
//   · `hitTargetThinHandle.test.ts` read exactly two files (`ui/NavRail`, `ui/SidePanel`) and derived
//     its adopter list from `src/ui` alone, so "it passes for the rail" was true of a file it never
//     opened. Fixed then by enrolling the rail as its third adopter; the derivation still walks all
//     of `src`, which is the part that mattered and stays.
//     🔴 THE RAIL HAS SINCE LEFT THAT UTILITY, AND THIS FILE CHANGED WITH IT. `.hit-24-x` centres a
//     FIXED 24px on the element's own width, so it only enlarges something NARROWER than 24px — and
//     it pins `top/bottom` to the element, so it only ever widened the HORIZONTAL axis. On a vertical
//     list of marks that was never the failing axis: the tick was 4px TALL. The Codex redesign gives
//     each mark its whole 32×10 row, which moves the axis that was actually failing (4 → 10px) and
//     makes the band inert. So the clause is asserted here as the OUTCOME — the mark's own declared
//     box, and that it covers the full row pitch — with the real measured box asserted in
//     `web/e2e/sessionMap.spec.ts`, where layout exists. A `hit-24-x` left on a 32px row would have
//     been markup that `hitTargetThinHandle` went on crediting for nothing.
//     🪤 AND IT IS STILL NOT SC 2.5.8, which is said plainly rather than implied: 32×10 flush rows
//     do not meet 24×24 and do not qualify for the spacing exception. Neither did 4px ticks with gaps.
//     The 44px touch floor is owed and met on the coarse-pointer form (SSM-10's drawer rows, asserted
//     in the browser gate); this is the fine-pointer surface, and what changed is that it is now
//     3.3× the area with no dead space between targets.
//   · `focusRingPerElement.test.ts` is an explicit inventory of 17 controls that kill their outline.
//     A mark is not in it and must never be: the fix is that the mark does NOT set `outline-none`, so
//     it inherits the global `:focus-visible` ring. Asserted here as the SOURCE property (absence),
//     because jsdom computes no styles and an inventory rail cannot assert an absence for a file it
//     does not list.
//   · `reducedMotionAppWide.test.ts` cannot see a rail with NO ANIMATION. So the rail has one — the
//     mark's LENGTH — and it runs on a gated `physics` getter. Its collapse is observed in
//     `SessionMapRail.reducedMotion.test.tsx`; what THIS file pins is that the animation exists at
//     all, because a later edit deleting it would make that rail vacuous again in silence. (It used
//     to be a hover halo; the length replaced it, so there is one animation rather than two.)

const TS = '2026-09-16T10:00:00.000Z'
const RAIL = join(process.cwd(), 'src/pages/chat/SessionMapRail.tsx')

/** The rail's source with every comment blanked (length-preserving).
 *
 *  🪤 LOAD-BEARING, AND IT COST A RED. The rail DOCUMENTS that it must not set `outline-none`, so a
 *  raw-text `not.toContain('outline-none')` failed on the sentence explaining why the string is
 *  absent — documenting the decision broke the assertion and documenting nothing would have passed
 *  it. Same lesson `design/hitTargetThinHandle.test.ts` and `primitiveAdoption.baseline.json` each
 *  record: a text scanner cannot tell a declaration from a sentence about one unless it is told to. */
function railCode(): string {
  return readFileSync(RAIL, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:"'`])\/\/[^\n]*/g, (m, lead: string) => lead + ' '.repeat(m.length - lead.length))
}

const turns: ChatTurn[] = [
  { role: 'user', ts: TS, visibleIndex: 0, segments: [{ kind: 'text', text: 'run the build' }] },
  { role: 'assistant', ts: TS, visibleIndex: 1, segments: [
    { kind: 'text', text: 'done' },
    { kind: 'tool', id: 't', tool: 'Terminal', detail: 'npm run build', done: true } as Segment,
  ] },
]

/** The horizontal scale a mark's line is rendered at.
 *
 *  🪤 `transform: none` IS SCALE 1, NOT A MISSING ANIMATION. framer-motion collapses the whole
 *  transform to the keyword when every component sits at its default, and `scaleX`'s default is 1 —
 *  so a mark at FULL length writes no `scaleX(…)` at all. Reading the regex alone made the
 *  hover assertion NaN against a rail that was working perfectly. An element with no `transform` in
 *  its style at all still reads NaN, which is what keeps "the animation was deleted" a failure. */
function scaleOf(el: Element | null): number {
  const style = (el as HTMLElement | null)?.getAttribute('style') ?? ''
  const m = /scaleX\(([\d.]+)\)/.exec(style)
  if (m) return Number(m[1])
  return /transform:\s*none/.test(style) ? 1 : NaN
}

function mount() {
  const marks = sessionMapMarks(turns)
  const { container } = render(
    <SessionMapRail marks={marks} turnNodes={new Map()} scrollRef={{ current: null }} onJumpTo={() => {}} />,
  )
  return { container, marks, ticks: [...container.querySelectorAll('[data-session-mark]')] as HTMLButtonElement[] }
}

describe('SessionMapRail — the mark IS its row (SSM-8)', () => {
  it('every mark declares its own box, and the rows are flush', () => {
    const { container, ticks } = mount()
    expect(ticks.length).toBeGreaterThan(1)
    for (const el of ticks) {
      const cls = el.className.split(/\s+/)
      // The pressable box is the ROW, not the drawn line — a mark sized to its own ink is what left
      // 4px targets with dead space between them. Height and width both, declared here.
      expect(cls, 'the mark must declare the row pitch as its own height').toContain('h-2.5')
      expect(cls, 'and span the rail’s full width').toContain('w-full')
      // 🔴 AND IT MUST NOT BE `absolute`. `ui/Popover` wraps the trigger in its own unsized
      // `<div className="relative">`, which becomes the containing block for anything absolute
      // inside it — so `absolute inset-0` here measured THAT wrapper and rendered a rail of 32×0
      // buttons. Measured in a browser (Playwright: "element is not visible", 321 retries) while
      // every assertion in this jsdom file passed, because jsdom computes no layout. Explicit
      // dimensions cannot be captured by a wrapper this file does not own.
      expect(cls, 'an absolute mark resolves against Popover’s wrapper, not the row').not.toContain('absolute')
      expect(cls, 'and `inset-0` is the shape that made them zero-size').not.toContain('inset-0')
    }
    // The rows are FLUSH — no gap utility anywhere in the list, so every pixel of the rail belongs to
    // some mark. A `gap-*` here would reintroduce unclickable bands between targets.
    const rows = [...container.querySelectorAll('[data-session-map-list] > *')] as HTMLElement[]
    expect(rows, 'one row per mark').toHaveLength(ticks.length)
    for (const row of rows) {
      // `shrink-0` is the row's whole job: without it the flex column compresses its children when
      // the list outgrows `max-h-full`, instead of scrolling — the marks would shrink, not overflow.
      expect(row.className, 'a row must refuse to shrink').toContain('shrink-0')
      expect(row.className, 'the rail must not add vertical rhythm between targets').not.toMatch(/\bgap(-y)?-/)
      // And the pitch is declared ONCE. A row that also sets a height is a second source for the
      // one number this layout turns on.
      expect(row.className, 'the pitch belongs to the mark, not to the row').not.toMatch(/\bh-\d/)
    }
    expect(
      container.querySelector('[data-session-map-list]')!.className,
      'nor may the list space its rows apart',
    ).not.toMatch(/\bgap(-y)?-|\bspace-y-/)
  })

  it('🔴 the mark carries NO hit-target utility — the row already is one', () => {
    // The deliberate removal, pinned so it cannot creep back as inert markup. Both variants: the
    // horizontal band is now inside a 32px element, and the symmetric `.hit-24` would overhang into
    // the neighbouring marks — the stolen-target defect `hitTargetThinHandle.test.ts` records twice.
    const src = railCode()
    expect(mount().ticks[0].className.match(/hit-\d+\S*/g), 'no band utility on the mark').toBeNull()
    expect(src, 'and none anywhere in a className in this file').not.toMatch(/className="[^"]*\bhit-\d+/)
  })
})

describe('SessionMapRail — the focus ring (SSM-8)', () => {
  it('🔑 the mark does NOT kill its outline, so it takes the global :focus-visible ring', () => {
    // The one thing that would put this rail into `focusRingPerElement`'s 17-control inventory — and
    // the defect that inventory exists for: a control that sets `outline-none` and replaces it with
    // nothing is focusable and invisible. jsdom computes no styles, so this is asserted as the source
    // property it actually is.
    const src = railCode()
    expect(src, 'the mark must not opt out of the app-wide ring').not.toContain('outline-none')
    // And the ring it inherits is the opaque one (`focusRingContrast.test.ts` owns the number): the
    // rail must not mint a local alpha ring either.
    expect(src).not.toMatch(/ring-primary\/\d/)
  })

  it('and the ring has something to land on: the mark is really focusable', () => {
    const { ticks } = mount()
    const stop = ticks.find((el) => el.tabIndex === 0)!
    act(() => { stop.focus() })
    expect(document.activeElement).toBe(stop)
    // Not `aria-hidden` — SSM-4 shipped the marks hidden, which is what made every a11y clause in
    // this atom vacuous.
    for (const el of ticks) expect(el.getAttribute('aria-hidden')).toBeNull()
  })
})

describe('SessionMapRail — the rail HAS an animation for reduced motion to collapse (SSM-8)', () => {
  it('each mark renders a drawn line, and hovering GROWS it — plus its neighbours', async () => {
    const { ticks } = mount()
    const lines = ticks.map((el) => el.querySelector('[data-session-map-mark-line]'))
    expect(lines.filter(Boolean), 'no line = nothing for the reduced-motion rail to be about')
      .toHaveLength(ticks.length)
    const line = lines[0] as HTMLElement
    // The line is the ink; it is decorative because the BUTTON carries the accessible name.
    expect(line.getAttribute('aria-hidden')).toBe('true')
    await act(async () => { await Promise.resolve() })
    const restScale = scaleOf(line)
    expect(restScale, `at rest: ${line.getAttribute('style')}`).not.toBeNaN()
    expect(restScale, 'a mark at rest must be shorter than full length').toBeLessThan(1)

    // …and it actually GROWS, which is the motion-allowed half `SessionMapRail.reducedMotion.test.tsx`
    // needs to exist for its negative to mean anything. `mouseOver`, not `mouseEnter`: React
    // delegates onMouseEnter off the bubbling event.
    fireEvent.mouseOver(ticks[0])
    await waitFor(() => expect(scaleOf(line), `after hover: ${line.getAttribute('style')}`).toBe(1))
    // 🔑 THE LENS, which is the reference's signature move and the reason a 2px mark is findable at
    // all: the hovered mark's NEIGHBOUR follows it up, by distance. Asserted on the neighbour rather
    // than only on the hovered mark, because a rail that merely highlighted one mark would pass a
    // hover test and still feel nothing like the thing it is modelled on.
    const neighbour = lines[1] as HTMLElement
    await waitFor(() => expect(scaleOf(neighbour), 'the ±1 neighbour must lengthen too')
      .toBeGreaterThan(restScale))
    expect(scaleOf(neighbour), 'but not as far as the mark under the pointer').toBeLessThan(1)
    // And it lets go: leaving the rail returns every mark to its resting length.
    fireEvent.mouseOut(ticks[0])
    await waitFor(() => expect(scaleOf(line)).toBe(restScale))
  })

  it('the length’s transition comes from the gated `physics` family, not a hand-rolled spring', () => {
    // `reducedMotionAppWide.test.ts` proves every `physics` member collapses to `instant`, and
    // censuses source for spring params minted OUTSIDE `design/motion.ts`. Both only bind on the rail
    // if the rail reaches for the module — so that is what is asserted, at the source.
    const src = railCode()
    expect(src).toMatch(/transition=\{physics\.snappy\}/)
    expect(src, 'a hand-rolled spring would escape the app-wide off-switch').not.toMatch(/stiffness|damping|bounce:/)
  })
})
