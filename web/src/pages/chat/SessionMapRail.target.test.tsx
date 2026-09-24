import { describe, expect, it } from 'vitest'
import { act, fireEvent, render, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { SessionMapRail } from './SessionMapRail'
import { sessionMapMarks } from './sessionMap'
import type { ChatTurn, Segment } from './chatTypes'

// ── SSM-8 — hit target, focus ring, and something for reduced motion to collapse ─────────────
//
// done_when: thin marks carry `.hit-24-x` and `hitTargetThinHandle.test.ts` passes FOR THE RAIL;
// `focusRingPerElement` / `focusRingContrast` pass; every rail animation collapses to instant under
// `prefers-reduced-motion`.
//
// 🪤 THIS ATOM IS THE CLEAREST VACUITY TRAP IN THE SSM TAIL, and the trap is that all three named
// rails were already GREEN while none of them looked at the Session Map:
//
//   · `hitTargetThinHandle.test.ts` read exactly two files (`ui/NavRail`, `ui/SidePanel`) and derived
//     its adopter list from `src/ui` alone, so "it passes for the rail" was true of a file it never
//     opened. Fixed AT THE RAIL: the Session Map rail is now its third named adopter and the
//     derivation walks all of `src`. That is where the hit-target clause is asserted.
//   · `focusRingPerElement.test.ts` is an explicit inventory of 17 controls that kill their outline.
//     A mark is not in it and must never be: the fix is that the mark does NOT set `outline-none`, so
//     it inherits the global `:focus-visible` ring. Asserted here as the SOURCE property (absence),
//     because jsdom computes no styles and an inventory rail cannot assert an absence for a file it
//     does not list.
//   · `reducedMotionAppWide.test.ts` cannot see a rail with NO ANIMATION. So the rail has one — the
//     halo — and it runs on a gated `physics` getter. Its collapse is observed in
//     `SessionMapRail.reducedMotion.test.tsx`; what THIS file pins is that the animation exists at
//     all, because a later edit deleting it would make that rail vacuous again in silence.

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

function mount() {
  const marks = sessionMapMarks(turns)
  const { container } = render(
    <SessionMapRail marks={marks} turnNodes={new Map()} scrollRef={{ current: null }} onJumpTo={() => {}} />,
  )
  return { container, marks, ticks: [...container.querySelectorAll('[data-session-mark]')] as HTMLButtonElement[] }
}

describe('SessionMapRail — the 24px pressable band (SSM-8)', () => {
  it('every mark carries .hit-24-x, and is positioned so the band anchors to the tick', () => {
    const { ticks } = mount()
    expect(ticks.length).toBeGreaterThan(1)
    for (const el of ticks) {
      const cls = el.className.split(/\s+/)
      expect(cls, 'the band utility must be on the mark itself').toContain('hit-24-x')
      // `.hit-24-x` sets no `position` on purpose, so the call site owns the containing block. A
      // statically-positioned mark would anchor the band to some ancestor, silently.
      expect(cls, 'the mark must establish its own containing block').toContain('absolute')
      // Still the 4px tick the utility is for: a mark already wider than 24px would not need it.
      expect(cls).toContain('size-1')
    }
  })

  it('the band is the mark’s ONLY hit-target treatment — no second idiom', () => {
    // `.hit-24` (the symmetric variant) would add ~10px above the topmost tick, overhanging into the
    // transcript header. Two idioms on one control is how a fix for one stolen target creates another.
    const src = railCode()
    for (const cls of (mount().ticks[0].className.match(/hit-\S+/g) ?? [])) expect(cls).toBe('hit-24-x')
    expect(src).not.toMatch(/className="[^"]*\bhit-24\b(?!-x)/)
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
  it('each mark renders a halo, and it grows on hover/focus', async () => {
    const { ticks } = mount()
    const halos = ticks.map((el) => el.querySelector('[data-session-map-halo]'))
    expect(halos.filter(Boolean), 'no halo = nothing for the reduced-motion rail to be about')
      .toHaveLength(ticks.length)
    const halo = halos[0] as HTMLElement
    // At rest it is invisible; the transform/opacity are written by framer-motion, so this reads the
    // rendered style rather than a prop.
    await act(async () => { await Promise.resolve() })
    expect(halo.style.opacity, `at rest: ${halo.getAttribute('style')}`).toBe('0')
    // Decorative, and must never steal the button's own pointer events.
    expect(halo.getAttribute('aria-hidden')).toBe('true')
    expect(halo.className).toContain('pointer-events-none')

    // …and it actually GROWS, which is the motion-allowed half `SessionMapRail.reducedMotion.test.tsx`
    // needs to exist for its negative to mean anything. `mouseOver`, not `mouseEnter`: React
    // delegates onMouseEnter off the bubbling event.
    fireEvent.mouseOver(ticks[0])
    await waitFor(() => expect(halo.style.opacity, `after hover: ${halo.getAttribute('style')}`).not.toBe('0'))
  })

  it('the halo’s transition comes from the gated `physics` family, not a hand-rolled spring', () => {
    // `reducedMotionAppWide.test.ts` proves every `physics` member collapses to `instant`, and
    // censuses source for spring params minted OUTSIDE `design/motion.ts`. Both only bind on the rail
    // if the rail reaches for the module — so that is what is asserted, at the source.
    const src = railCode()
    expect(src).toMatch(/transition=\{physics\.snappy\}/)
    expect(src, 'a hand-rolled spring would escape the app-wide off-switch').not.toMatch(/stiffness|damping|bounce:/)
  })
})
