import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { SessionMapRail, MARK_REST_SCALE } from './SessionMapRail'
import { sessionMapEntries } from './sessionMap'
import type { ChatTurn, Segment } from './chatTypes'

// ── SSM-4 — the Session Map rail component (the markers) ────────────────────────────────
//
// done_when, as the owner ruled it on 2026-09-25: a <nav aria-label="Session map"> renders ONE
// marker per USER message and none for replies or the work inside them; every marker RESTS AT ONE
// LENGTH; what is on screen is told by COLOUR alone (--color-primary on screen, --color-map-rest
// off it — no raw hex/px, tokenLint.test.ts; no coloured side-stripe, sideStripeDoctrine.test.ts);
// the rail self-suppresses (renders null) below two messages.
//
// VACUITY FLOOR. "renders N markers" is faked by a component that renders N of ANYTHING, and
// "self-suppresses" is faked by a component that never renders. So:
//   · the count is asserted against the REAL sessionMapEntries output AND against the number of
//     user turns in the fixture, whose replies carry tool calls, an approval, an error and a
//     stats line — every one of which used to be a marker of its own;
//   · both TONE tokens are asserted to actually land on the elements, and NO third tone is allowed;
//   · the uniform length is asserted across a rail that HAS both tones, so "every marker is the
//     same length" cannot pass on a rail where every marker is also the same colour;
//   · the suppression is proven AT THE BOUNDARY (1 → null, 2 → rendered).

const USER_TS = '2026-09-16T10:00:00.000Z'
const ASST_TS = '2026-09-16T10:00:05.000Z'

/** A reply carrying every kind of work the typed index marks: two tool calls (one failed), an
 *  approval, an error and a stats line. None of them may become a marker. */
const busyReply: Segment[] = [
  { kind: 'text', text: 'Running the build now.' },
  { kind: 'tool', id: 't-ok', tool: 'Terminal', detail: 'npm run build', done: true },
  { kind: 'tool', id: 't-fail', tool: 'Read', detail: 'src/missing.ts', done: true, ok: false,
    agentError: { code: 'file_not_found', what: 'no such file', why: 'path typo', fix: 'check the path' } },
  { kind: 'approval', id: 'a-1', tool: 'Terminal', input: 'rm -rf build', risk: 'destructive' },
  { kind: 'error', text: 'ValidationException: input is too long for the model.' },
  { kind: 'activity', text: 'cost $0.02 · 1.2k tokens · 4.1s', activityKind: 'stats' },
]

const fixtureTurns: ChatTurn[] = [
  { role: 'user', ts: USER_TS, visibleIndex: 0, segments: [{ kind: 'text', text: 'Please run the build.' }] },
  { role: 'assistant', ts: ASST_TS, visibleIndex: 1, segments: busyReply },
  { role: 'user', ts: USER_TS, visibleIndex: 2, segments: [{ kind: 'text', text: 'Now fix the missing file.' }] },
  { role: 'assistant', ts: ASST_TS, visibleIndex: 3, segments: [{ kind: 'text', text: 'Fixed it.' }] },
  { role: 'user', ts: USER_TS, visibleIndex: 4, segments: [{ kind: 'text', text: 'Thanks — ship it.' }] },
]

const railProps = { turnNodes: new Map<number, Element>(), scrollRef: { current: null }, onJumpTo: () => {} }

const railMarks = (c: HTMLElement) => [...c.querySelectorAll('[data-session-mark]')] as HTMLElement[]
const markLines = (c: HTMLElement) => [...c.querySelectorAll('[data-session-map-mark-line]')] as HTMLElement[]
/** The scale a marker's line is rendered at, read off the style framer wrote.
 *
 *  🪤 `transform: none` IS SCALE 1. framer collapses the transform to the keyword when every
 *  component is at its default, and `scaleX`'s default is 1 — so a marker at FULL length writes no
 *  `scaleX(…)`. A style with no `transform` at all still reads NaN, so a deleted animation fails. */
function scaleOf(el: Element | null): number {
  const style = (el as HTMLElement | null)?.getAttribute('style') ?? ''
  const m = /scaleX\(([\d.]+)\)/.exec(style)
  if (m) return Number(m[1])
  return /transform:\s*none/.test(style) ? 1 : NaN
}

describe('SessionMapRail — the markers (SSM-4)', () => {
  it('renders a <nav aria-label="Session map"> landmark that says how to use it', () => {
    const { getByRole } = render(<SessionMapRail entries={sessionMapEntries(fixtureTurns)} {...railProps} />)
    const nav = getByRole('navigation', { name: 'Session map' })
    // The reference's first friction: marks that are small and UNEXPLAINED. The landmark carries
    // the explanation once, rather than every marker repeating it.
    const hint = document.getElementById(nav.getAttribute('aria-describedby') ?? '')
    expect(hint, 'the rail names no description').not.toBeNull()
    expect(hint!.textContent).toMatch(/message you sent/i)
  })

  it('🔑 renders ONE marker per USER message — never one for a reply or the work inside it', () => {
    const entries = sessionMapEntries(fixtureTurns)
    const userTurns = fixtureTurns.filter((t) => t.role === 'user').length
    expect(entries, 'one entry per user turn').toHaveLength(userTurns)
    const { container } = render(<SessionMapRail entries={entries} {...railProps} />)
    expect(railMarks(container), 'the rail drew a marker for something that is not a user message')
      .toHaveLength(userTurns)
    // And the markers ARE the user messages, in order — named by them.
    expect(railMarks(container).map((m) => m.getAttribute('aria-label'))).toEqual([
      'Message 1 of 3: Please run the build.',
      'Message 2 of 3: Now fix the missing file.',
      'Message 3 of 3: Thanks — ship it.',
    ])
  })

  it('🔴 draws NO track — the spine was deleted, not retinted', () => {
    const { container } = render(<SessionMapRail entries={sessionMapEntries(fixtureTurns)} {...railProps} />)
    // The element AND the token, so neither a renamed spine nor a differently-tagged one passes.
    expect(container.querySelector('[data-session-map-track]'), 'the spine is back').toBeNull()
    expect(container.innerHTML, 'nothing in the rail may paint the rail tone').not.toContain('--color-rail')
    // …and the thing that replaced it is really there: one drawn line per marker.
    expect(markLines(container)).toHaveLength(railMarks(container).length)
  })

  it('paints markers with ONLY the --color-primary / --color-map-rest pair at rest, both present', () => {
    const { container } = render(<SessionMapRail entries={sessionMapEntries(fixtureTurns)} {...railProps} />)
    const tones = railMarks(container).map((m) => m.style.color)
    for (const t of tones) {
      expect(t === 'var(--color-primary)' || t === 'var(--color-map-rest)', `unexpected marker tone: ${t}`).toBe(true)
    }
    // BOTH tones appear (an all-grey or all-coral rail would be a lie about what is on screen).
    expect(tones).toContain('var(--color-primary)')
    expect(tones).toContain('var(--color-map-rest)')
    // The line inherits the row's colour rather than restating it — one declaration, which is the
    // one `schemeContrast.test.ts` parses. Lower-cased: jsdom normalises the CSS-wide keyword.
    for (const line of markLines(container)) {
      expect(line.style.background.toLowerCase(), 'the line must take the row’s currentColor').toBe('currentcolor')
    }
  })

  it('lights the newest exchange coral when nothing has reported on screen yet, the rest neutral', () => {
    // No IntersectionObserver rows: the honest answer is the newest message, where an unscrolled
    // transcript is. Everything before it is off screen.
    const { container } = render(<SessionMapRail entries={sessionMapEntries(fixtureTurns)} {...railProps} />)
    expect(railMarks(container).map((m) => m.style.color)).toEqual([
      'var(--color-map-rest)', 'var(--color-map-rest)', 'var(--color-primary)',
    ])
  })

  it('🔑 says "on screen" in ARIA too, so the region is not colour-alone for assistive tech', () => {
    const { container } = render(<SessionMapRail entries={sessionMapEntries(fixtureTurns)} {...railProps} />)
    const flags = railMarks(container).map((n) => n.getAttribute('aria-current'))
    expect(flags).toEqual([null, null, 'true'])
  })

  it('🔑 every marker RESTS AT ONE LENGTH — being on screen is colour, never size (owner rule)', () => {
    // The previous form idled a current turn at ~18px, a history turn at ~12px and a sub-event at
    // 6px. The owner: the on-screen markers "don't need to expand to indicate that". So at rest the
    // lengths are identical across a rail that DOES carry both tones (asserted above).
    const { container } = render(<SessionMapRail entries={sessionMapEntries(fixtureTurns)} {...railProps} />)
    const scales = markLines(container).map(scaleOf)
    for (const [i, s] of scales.entries()) expect(s, `marker ${i} has no scaleX`).not.toBeNaN()
    expect(new Set(scales).size, `resting lengths differ: ${scales.join(', ')}`).toBe(1)
    expect(scales[0], 'the resting length is the rail\'s one declared constant').toBe(MARK_REST_SCALE)
    // Nothing rests at full length — that is reserved for the marker under the pointer or cursor.
    expect(scales[0]).toBeLessThan(1)
  })

  it('self-suppresses (renders null) below two messages, at the boundary', () => {
    const one = sessionMapEntries(fixtureTurns.slice(0, 2))
    expect(one).toHaveLength(1)
    const { container: c1, queryByRole: q1 } = render(<SessionMapRail entries={one} {...railProps} />)
    expect(q1('navigation')).toBeNull()
    expect(railMarks(c1)).toHaveLength(0)
    // Exactly 2 messages → rendered: the threshold renders AT two, it does not require three.
    const two = sessionMapEntries(fixtureTurns.slice(0, 3))
    expect(two).toHaveLength(2)
    const { queryByRole: q2, container: c2 } = render(<SessionMapRail entries={two} {...railProps} />)
    expect(q2('navigation', { name: 'Session map' })).not.toBeNull()
    expect(railMarks(c2)).toHaveLength(2)
  })
})
