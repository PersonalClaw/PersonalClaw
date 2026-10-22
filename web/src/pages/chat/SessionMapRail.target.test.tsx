import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { SessionMapRail } from './SessionMapRail'
import { sessionMapEntries } from './sessionMap'
import { MARK_FALLOFF, MARK_IN_VIEW_PX, MARK_MAX_PX, MARK_REST_PX, markLength } from './sessionMapGeometry'
import type { ChatTurn, Segment } from './chatTypes'

// ── Hit target, focus ring, and something for reduced motion to collapse ─────────────────────
//
// The contract: a mark's pressable area clears the one the tick form had; `focusRingPerElement` /
// `focusRingContrast` pass; every rail animation collapses to instant under
// `prefers-reduced-motion`.
//
// 🪤 THIS CHANGE IS THE CLEAREST VACUITY TRAP IN THE SESSION MAP WORK, and the trap is that all three named
// rails were already GREEN while none of them looked at the Session Map:
//
//   · `hitTargetThinHandle.test.ts` read exactly two files (`ui/NavRail`, `ui/SidePanel`) and derived
//     its adopter list from `src/ui` alone, so "it passes for the rail" was true of a file it never
//     opened. Fixed then by enrolling the rail as its third adopter; the derivation still walks all
//     of `src`, which is the part that mattered and stays.
//     🔴 THE RAIL HAS SINCE LEFT THAT UTILITY, AND THIS FILE CHANGED WITH IT. `.hit-24-x` centres a
//     FIXED 24px on the element's own width, so it only enlarges something NARROWER than 24px — and
//     it pins `top/bottom` to the element, so it only ever widened the HORIZONTAL axis. On a vertical
//     list of marks that was never the failing axis: the tick was 4px TALL. Each marker now owns its
//     whole row, so the requirement is asserted here as the OUTCOME — the marker's own declared box, and
//     that it covers the full row pitch — with the real measured box asserted in
//     `web/e2e/sessionMap.spec.ts`, where layout exists. A `hit-24-x` left on a 32px row would have
//     been markup that `hitTargetThinHandle` went on crediting for nothing.
//     🔑 AND THE ROW IS NOW 32×24, WHICH IS SC 2.5.8. The Codex form's 32×10 flush rows did not meet
//     24×24; "precise pointer movement required" is among the reference's frictions,
//     and with one marker per USER message rather than one per event there are few enough rows to
//     afford the height. The 44px touch floor is still owed and met on the coarse-pointer form
//     (the drawer rows, asserted in the browser gate).
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

/** Eight questions, so a target in the middle has three neighbours on each side. */
const turns: ChatTurn[] = Array.from({ length: 16 }, (_, i) => (i % 2 === 0
  ? { role: 'user', ts: TS, visibleIndex: i, segments: [{ kind: 'text', text: `question ${i / 2 + 1}` }] }
  : { role: 'assistant', ts: TS, visibleIndex: i, segments: [
    { kind: 'text', text: 'done' },
    { kind: 'tool', id: `t${i}`, tool: 'Terminal', detail: 'npm run build', done: true } as Segment,
  ] }))

/** The painted length of a mark's line, in px, from the `scaleX` its style carries. */
function lengthOf(el: Element | null): number {
  const style = (el as HTMLElement | null)?.getAttribute('style') ?? ''
  const m = /scaleX\(([\d.]+)\)/.exec(style)
  return m ? Math.round(Number(m[1]) * MARK_MAX_PX * 100) / 100 : NaN
}

function mount() {
  const entries = sessionMapEntries(turns)
  const { container } = render(
    <SessionMapRail entries={entries} turnNodes={new Map()} scrollRef={{ current: null }} onJumpTo={() => {}} />,
  )
  const ticks = [...container.querySelectorAll('[data-session-mark]')] as HTMLButtonElement[]
  const lines = ticks.map((el) => el.querySelector('[data-session-map-mark-line]') as HTMLElement)
  return { container, entries, ticks, lines }
}

const point = (el: Element) => act(() => { fireEvent.pointerMove(el, { pointerType: 'mouse', buttons: 0 }) })

describe('SessionMapRail — the mark IS its row', () => {
  it('every mark declares its own box, and the rows are flush', () => {
    const { container, ticks } = mount()
    expect(ticks.length).toBe(8)
    for (const el of ticks) {
      const cls = el.className.split(/\s+/)
      // The pressable box is the ROW, not the drawn line, and it is the same box at every length the
      // line is painted: 24px, SC 2.5.8, across the rail's full width.
      expect(cls, 'the marker must declare the row pitch as its own height — 24px, SC 2.5.8').toContain('h-6')
      expect(cls, 'and span the rail’s full width').toContain('w-full')
      // `shrink-0`: without it the flex column compresses its rows when the list outgrows its cap,
      // instead of scrolling — the targets would shrink, not overflow.
      expect(cls, 'a row must refuse to shrink').toContain('shrink-0')
      expect(cls, 'and `inset-0` is the shape that made them zero-size').not.toContain('inset-0')
    }
    // The rows are FLUSH — no gap utility anywhere in the list, so every pixel belongs to some mark.
    const rows = [...container.querySelectorAll('[data-session-map-list] > *')] as HTMLElement[]
    expect(rows, 'one row per mark, and the row is the mark').toEqual(ticks)
    expect(
      container.querySelector('[data-session-map-list]')!.className,
      'nor may the list space its rows apart',
    ).not.toMatch(/\bgap(-y)?-|\bspace-y-/)
  })

  it('the list is capped at the reference height and scrolls itself past it', () => {
    const list = mount().container.querySelector('[data-session-map-list]') as HTMLElement
    expect(list.style.maxHeight).toBe('min(70vh, 40rem)')
    expect(list.className.split(/\s+/)).toContain('overflow-y-auto')
  })

  it('🔴 the list is padded by the FADE distance, so the edge mask never dims a resting marker', () => {
    const fade = /EDGE_FADE =[\s\S]*?black var\((--spacing-[a-z0-9]+)\)/.exec(railCode())?.[1]
    expect(fade, 'could not read the fade distance out of EDGE_FADE').toBe('--spacing-xl')
    const list = mount().container.querySelector('[data-session-map-list]') as HTMLElement
    expect(list.className.split(/\s+/), 'the list is not padded by the fade distance').toContain('py-xl')
  })

  it('🔴 the mark carries NO hit-target utility — the row already is one', () => {
    const src = railCode()
    expect(mount().ticks[0].className.match(/hit-\d+\S*/g), 'no band utility on the mark').toBeNull()
    expect(src, 'and none anywhere in a className in this file').not.toMatch(/className="[^"]*\bhit-\d+/)
  })
})

describe('SessionMapRail — the focus ring', () => {
  it('🔑 the mark does NOT kill its outline, so it takes the global :focus-visible ring', () => {
    const src = railCode()
    expect(src, 'the mark must not opt out of the app-wide ring').not.toContain('outline-none')
    expect(src).not.toMatch(/ring-primary\/\d/)
  })

  it('and the ring has something to land on: the mark is really focusable', () => {
    const { ticks } = mount()
    const stop = ticks.find((el) => el.tabIndex === 0)!
    act(() => { stop.focus() })
    expect(document.activeElement).toBe(stop)
    for (const el of ticks) expect(el.getAttribute('aria-hidden')).toBeNull()
  })
})

describe('SessionMapRail — the wave around the target', () => {
  it('the geometry: 26 px at the target, 0.7 / 0.4 / 0.2 of the way for the three either side', () => {
    expect(MARK_FALLOFF).toEqual([1, 0.7, 0.4, 0.2])
    expect(markLength(4, 4, false)).toBe(MARK_MAX_PX)
    expect(markLength(3, 4, false)).toBeCloseTo(MARK_REST_PX + (MARK_MAX_PX - MARK_REST_PX) * 0.7)
    expect(markLength(6, 4, false)).toBeCloseTo(MARK_REST_PX + (MARK_MAX_PX - MARK_REST_PX) * 0.4)
    expect(markLength(1, 4, false)).toBeCloseTo(MARK_REST_PX + (MARK_MAX_PX - MARK_REST_PX) * 0.2)
    expect(markLength(0, 4, false), 'four away rests').toBe(MARK_REST_PX)
    expect(markLength(0, null, true), 'an exchange on screen rests longer').toBe(MARK_IN_VIEW_PX)
  })

  it('🔑 pointing grows the target and pulls its neighbours after it, then lets go', () => {
    const { container, ticks, lines } = mount()
    expect(lines.filter(Boolean)).toHaveLength(ticks.length)
    expect(lines[0].getAttribute('aria-hidden')).toBe('true')
    const rest = lines.map(lengthOf)
    // The newest exchange is the one on screen at load (nothing reported), so it rests longer.
    expect(rest).toEqual([6, 6, 6, 6, 6, 6, 6, 12])
    point(ticks[3])
    const lit = lines.map(lengthOf)
    expect(lit).toEqual(ticks.map((_, i) => Math.round(markLength(i, 3, i === 7) * 100) / 100))
    expect(lit[3]).toBe(MARK_MAX_PX)
    expect(lit[2], 'the neighbour above follows').toBeGreaterThan(rest[2])
    expect(lit[4], 'the neighbour below follows').toBeGreaterThan(rest[4])
    expect(lit[2]).toBeGreaterThan(lit[1])
    // Only the TARGET brightens; its neighbours keep their tone.
    expect(ticks[3].style.color).toBe('var(--color-on-surface)')
    expect(ticks[2].style.color).toBe('var(--color-map-rest)')
    // 🔑 THE HIT AREAS NEVER MOVED: every row is still the one 24px box, whatever its line is.
    for (const el of ticks) expect(el.className).toContain('h-6')
    // And it lets go: the pointer leaving the rail returns every line to rest.
    vi.useFakeTimers()
    try {
      act(() => { fireEvent.pointerLeave(container.querySelector('nav')!) })
      act(() => { vi.advanceTimersByTime(200) })
    } finally { vi.useRealTimers() }
    expect(lines.map(lengthOf)).toEqual(rest)
  })

  it('an on-screen target brightens to the accent’s higher-contrast shade, not to the neutral ink', () => {
    const { ticks } = mount()
    const current = ticks.find((el) => el.getAttribute('data-current') === 'true')!
    expect(current.style.color).toBe('var(--color-primary)')
    point(current)
    expect(current.style.color).toBe('var(--color-primary-emphasis)')
  })

  it('the keyboard cursor is a target too: focus from an arrow grows its marker the same way', () => {
    const { ticks, lines } = mount()
    act(() => { ticks[7].focus() })
    act(() => { fireEvent.keyDown(ticks[7], { key: 'ArrowUp' }) })
    expect(document.activeElement).toBe(ticks[6])
    expect(lengthOf(lines[6])).toBe(MARK_MAX_PX)
    expect(lengthOf(lines[5])).toBeCloseTo(markLength(5, 6, false))
  })

  it('the length moves on a CSS transition, from the duration tokens, and never through a frame loop', () => {
    const src = railCode()
    expect(src).toMatch(/transition-transform duration-\(--duration-short\)/)
    expect(src, 'reduced motion stops it outright as well as through the global rule').toMatch(/motion-reduce:transition-none/)
    expect(src, 'no frame loop paints the rail').not.toMatch(/requestAnimationFrame|framer-motion/)
    expect(src, 'a hand-rolled spring would escape the app-wide off-switch').not.toMatch(/stiffness|damping|bounce:/)
  })
})
