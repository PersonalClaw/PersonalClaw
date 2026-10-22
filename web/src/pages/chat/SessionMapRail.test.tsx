import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { SessionMapRail } from './SessionMapRail'
import { MARK_IN_VIEW_PX, MARK_MAX_PX, MARK_REST_PX } from './sessionMapGeometry'
import { sessionMapEntries } from './sessionMap'
import type { ChatTurn, Segment } from './chatTypes'

// ── The Session Map rail component (the markers) ────────────────────────────────────────
//
// The contract (#4776): a <nav aria-label="Session map"> renders ONE marker per USER message and
// none for replies or the work inside them; at rest a marker is short, and an exchange on screen
// rests LONGER and in the accent (--color-primary on screen, --color-map-rest off it), so what is in
// view is told by length as well as colour; the rail self-suppresses below four messages.
//
// VACUITY FLOOR. "renders N markers" is faked by a component that renders N of ANYTHING, and
// "self-suppresses" is faked by a component that never renders. So:
//   · the count is asserted against the REAL sessionMapEntries output AND against the number of
//     user turns in the fixture, whose replies carry tool calls, an approval, an error and a
//     stats line — every one of which used to be a marker of its own;
//   · both TONE tokens are asserted to actually land on the elements, and NO third tone is allowed;
//   · the two resting lengths are asserted on a rail that HAS both tones, each with its own tone;
//   · the suppression is proven AT THE BOUNDARY (3 → null, 4 → rendered).

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
  { role: 'user', ts: USER_TS, visibleIndex: 4, segments: [{ kind: 'text', text: 'Now write the notes.' }] },
  { role: 'assistant', ts: ASST_TS, visibleIndex: 5, segments: [{ kind: 'text', text: 'Written.' }] },
  { role: 'user', ts: USER_TS, visibleIndex: 6, segments: [{ kind: 'text', text: 'Thanks — ship it.' }] },
]

const railProps = { turnNodes: new Map<number, Element>(), scrollRef: { current: null }, onJumpTo: () => {} }

const railMarks = (c: HTMLElement) => [...c.querySelectorAll('[data-session-mark]')] as HTMLElement[]
const markLines = (c: HTMLElement) => [...c.querySelectorAll('[data-session-map-mark-line]')] as HTMLElement[]
/** The painted length of a marker's line, in px: its `MARK_MAX_PX` width times the `scaleX` it is
 *  drawn at. A line with no `scaleX` reads NaN, so a deleted length fails. */
function lengthOf(el: Element | null): number {
  const style = (el as HTMLElement | null)?.getAttribute('style') ?? ''
  const m = /scaleX\(([\d.]+)\)/.exec(style)
  return m ? Math.round(Number(m[1]) * MARK_MAX_PX * 100) / 100 : NaN
}

describe('SessionMapRail — the markers', () => {
  it('renders a <nav aria-label="Session map"> landmark that says how to use it', () => {
    const { getByRole } = render(<SessionMapRail entries={sessionMapEntries(fixtureTurns)} {...railProps} />)
    const nav = getByRole('navigation', { name: 'Session map' })
    // The landmark carries the explanation once, rather than every marker repeating it, and it
    // names every way through the rail: preview, jump, scrub and the chord.
    const hint = document.getElementById(nav.getAttribute('aria-describedby') ?? '')
    expect(hint, 'the rail names no description').not.toBeNull()
    expect(hint!.textContent).toMatch(/message you sent/i)
    expect(hint!.textContent).toMatch(/drag/i)
    expect(hint!.textContent).toMatch(/up and down arrows/i)
  })

  it('🔑 renders ONE marker per USER message — never one for a reply or the work inside it', () => {
    const entries = sessionMapEntries(fixtureTurns)
    const userTurns = fixtureTurns.filter((t) => t.role === 'user').length
    expect(entries, 'one entry per user turn').toHaveLength(userTurns)
    const { container } = render(<SessionMapRail entries={entries} {...railProps} />)
    expect(railMarks(container), 'the rail drew a marker for something that is not a user message')
      .toHaveLength(userTurns)
    expect(railMarks(container).map((m) => m.getAttribute('aria-label'))).toEqual([
      'Message 1 of 4: Please run the build.',
      'Message 2 of 4: Now fix the missing file.',
      'Message 3 of 4: Now write the notes.',
      'Message 4 of 4: Thanks — ship it.',
    ])
  })

  it('🔴 draws NO track — the spine was deleted, not retinted', () => {
    const { container } = render(<SessionMapRail entries={sessionMapEntries(fixtureTurns)} {...railProps} />)
    expect(container.querySelector('[data-session-map-track]'), 'the spine is back').toBeNull()
    expect(container.innerHTML, 'nothing in the rail may paint the rail tone').not.toContain('--color-rail')
    expect(markLines(container)).toHaveLength(railMarks(container).length)
  })

  it('paints markers with ONLY the --color-primary / --color-map-rest pair at rest, both present', () => {
    const { container } = render(<SessionMapRail entries={sessionMapEntries(fixtureTurns)} {...railProps} />)
    const tones = railMarks(container).map((m) => m.style.color)
    for (const t of tones) {
      expect(t === 'var(--color-primary)' || t === 'var(--color-map-rest)', `unexpected marker tone: ${t}`).toBe(true)
    }
    expect(tones).toContain('var(--color-primary)')
    expect(tones).toContain('var(--color-map-rest)')
    // The line inherits the row's colour rather than restating it — one declaration, which is the
    // one `schemeContrast.test.ts` parses. Lower-cased: jsdom normalises the CSS-wide keyword.
    for (const line of markLines(container)) {
      expect(line.style.background.toLowerCase(), 'the line must take the row’s currentColor').toBe('currentcolor')
    }
  })

  it('lights the newest exchange when nothing has reported on screen yet, the rest neutral', () => {
    const { container } = render(<SessionMapRail entries={sessionMapEntries(fixtureTurns)} {...railProps} />)
    expect(railMarks(container).map((m) => m.style.color)).toEqual([
      'var(--color-map-rest)', 'var(--color-map-rest)', 'var(--color-map-rest)', 'var(--color-primary)',
    ])
  })

  it('🔑 says "on screen" in ARIA too, so the region is not colour-alone for assistive tech', () => {
    const { container } = render(<SessionMapRail entries={sessionMapEntries(fixtureTurns)} {...railProps} />)
    expect(railMarks(container).map((n) => n.getAttribute('aria-current'))).toEqual([null, null, null, 'true'])
  })

  it('🔑 at rest an exchange on screen is LONGER as well as in the accent — never colour alone', () => {
    const { container } = render(<SessionMapRail entries={sessionMapEntries(fixtureTurns)} {...railProps} />)
    const lengths = markLines(container).map(lengthOf)
    for (const [i, l] of lengths.entries()) expect(l, `marker ${i} has no painted length`).not.toBeNaN()
    // Off screen, the short rest; on screen (the newest, here), the longer one — with its tone.
    expect(lengths).toEqual([MARK_REST_PX, MARK_REST_PX, MARK_REST_PX, MARK_IN_VIEW_PX])
    const tones = railMarks(container).map((m) => m.style.color)
    lengths.forEach((l, i) => expect(tones[i]).toBe(l === MARK_IN_VIEW_PX ? 'var(--color-primary)' : 'var(--color-map-rest)'))
    // Nothing rests at full length — that is reserved for the target.
    expect(Math.max(...lengths)).toBeLessThan(MARK_MAX_PX)
  })

  it('a bookmarked message carries its dot, and says so in its name; the others do not', () => {
    const entries = sessionMapEntries(fixtureTurns, new Set([USER_TS]))
    // Every fixture question shares one stamp, so mark only the second as bookmarked by hand.
    const marked = entries.map((e, i) => ({ ...e, bookmarked: i === 1 }))
    const { container } = render(<SessionMapRail entries={marked} {...railProps} />)
    const dots = railMarks(container).map((m) => !!m.querySelector('[data-session-map-bookmark]'))
    expect(dots).toEqual([false, true, false, false])
    expect(railMarks(container)[1].getAttribute('aria-label')).toBe('Message 2 of 4, bookmarked: Now fix the missing file.')
    // The dot sits just past the end of its line, wherever that end is.
    const dot = railMarks(container)[1].querySelector('[data-session-map-bookmark]') as HTMLElement
    expect(dot.style.transform).toBe(`translate(${MARK_REST_PX + 2}px, -50%)`)
  })

  it('self-suppresses (renders null) below four messages, at the boundary', () => {
    const three = sessionMapEntries(fixtureTurns.slice(0, 5))
    expect(three).toHaveLength(3)
    const { container: c1, queryByRole: q1 } = render(<SessionMapRail entries={three} {...railProps} />)
    expect(q1('navigation')).toBeNull()
    expect(railMarks(c1)).toHaveLength(0)
    // Exactly four messages → rendered: the threshold renders AT four.
    const four = sessionMapEntries(fixtureTurns)
    expect(four).toHaveLength(4)
    const { queryByRole: q2, container: c2 } = render(<SessionMapRail entries={four} {...railProps} />)
    expect(q2('navigation', { name: 'Session map' })).not.toBeNull()
    expect(railMarks(c2)).toHaveLength(4)
  })
})
