import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { SessionMapRail } from './SessionMapRail'
import { sessionMapMarks } from './sessionMap'
import type { ChatTurn, Segment, SubagentCard } from './chatTypes'

// ── SSM-4 — the Session Map rail component (the marks) ──────────────────────────────────
//
// done_when: a <nav aria-label="Session map"> renders EXACTLY sessionMapMarks(turns).length
// marks; marks use --color-primary / --color-on-surface-low (no raw hex/px — tokenLint.test.ts;
// no colored side-stripe — sideStripeDoctrine.test.ts, both global source-scan rails that this
// file is auto-included in); the rail self-suppresses (renders null) when marks < 2.
//
// VACUITY FLOOR. "renders N marks" is faked by a component that renders N of ANYTHING, and
// "self-suppresses" is faked by a component that never renders. So:
//   · the count is asserted against the REAL sessionMapMarks output — not a hand-picked N —
//     so a wrong contract or a per-turn-only rail fails here as well;
//   · both TONE tokens are asserted to actually land on the elements (an all-grey rail, or one
//     that paints a raw value, fails) and NO third tone is allowed;
//   · the suppression is proven AT THE BOUNDARY (1 → null, 2 → rendered), so it can neither be
//     a no-op nor an always-null.
//
// 🔴 TWO THINGS THIS FILE ASSERTS THE OPPOSITE OF SINCE THE CODEX REDESIGN, both deliberately:
//
//  1. THE TRACK IS GONE, and its absence is pinned rather than left to drift back. SSM-4 shipped a
//     1px `--color-rail` hairline behind the ticks; the Codex reference draws no track, and
//     `--color-rail` measures 1.000:1 against `--color-canvas` in light mode — it painted nothing
//     there at all. With marks on a constant pitch the column reads as a rail without one. Asserting
//     the absence is what stops a later "restore the spine" reintroducing an invisible element and a
//     cross-surface token argument with it.
//  2. THE TONE MOVED FROM `background` TO `color`. The painted thing is now the LINE inside the
//     row, not the row: a 32×10 pressable row painted coral would be a solid block. The line takes
//     `currentColor`, which is also how the reference does it. `design/schemeContrast.test.ts`
//     parses the `const tone = isCurrent ? … : …` declaration out of the component, so the token
//     PAIR is still measured in all 12 schemes from one place.

const USER_TS = '2026-09-16T10:00:00.000Z'
const ASST_TS = '2026-09-16T10:00:05.000Z'

// The SSM-1 fixture shape: an assistant turn carrying every listed sub-event (two tool calls,
// one ok + one failed, an approval, an error, a stats activity), plus a text segment that
// yields no mark. sessionMapMarks turns this into user + assistant + 5 sub-events (+1 subagent).
const assistantSegments: Segment[] = [
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
  { role: 'assistant', ts: ASST_TS, visibleIndex: 1, segments: assistantSegments },
]
const fixtureSubagents: SubagentCard[] = [
  { id: 's-1', task: 'Investigate the flaky snapshot test', agent: 'general-purpose', done: true },
]

const railProps = { turnNodes: new Map<number, Element>(), scrollRef: { current: null }, onJumpTo: () => {} }

const railMarks = (c: HTMLElement) => c.querySelectorAll('[data-session-mark]')
const markLines = (c: HTMLElement) => c.querySelectorAll('[data-session-map-mark-line]')
/** The scale a mark's line is rendered at, read off the style framer wrote.
 *
 *  🪤 `transform: none` IS SCALE 1. framer collapses the transform to the keyword when every
 *  component is at its default, and `scaleX`'s default is 1 — so a mark at FULL length writes no
 *  `scaleX(…)`. A style with no `transform` at all still reads NaN, so a deleted animation fails. */
function scaleOf(el: Element | null): number {
  const style = (el as HTMLElement | null)?.getAttribute('style') ?? ''
  const m = /scaleX\(([\d.]+)\)/.exec(style)
  if (m) return Number(m[1])
  return /transform:\s*none/.test(style) ? 1 : NaN
}

describe('SessionMapRail — the marks (SSM-4)', () => {
  it('renders a <nav aria-label="Session map"> landmark', () => {
    const { getByRole } = render(<SessionMapRail marks={sessionMapMarks(fixtureTurns, fixtureSubagents)} {...railProps} />)
    expect(getByRole('navigation', { name: 'Session map' })).toBeInTheDocument()
  })

  it('renders EXACTLY sessionMapMarks(turns).length marks', () => {
    const marks = sessionMapMarks(fixtureTurns, fixtureSubagents)
    // Ties to SSM-1's own count — user + assistant + (2 tool + approval + error + activity) + subagent.
    expect(marks).toHaveLength(8)
    const { container } = render(<SessionMapRail marks={marks} {...railProps} />)
    expect(railMarks(container)).toHaveLength(marks.length)
  })

  it('🔴 draws NO track — the spine was deleted, not retinted', () => {
    const { container } = render(<SessionMapRail marks={sessionMapMarks(fixtureTurns, fixtureSubagents)} {...railProps} />)
    // The element AND the token, so neither a renamed spine nor a differently-tagged one passes.
    expect(container.querySelector('[data-session-map-track]'), 'the spine is back').toBeNull()
    expect(container.innerHTML, 'nothing in the rail may paint the rail tone').not.toContain('--color-rail')
    // …and the thing that replaced it is really there: one drawn line per mark.
    expect(markLines(container)).toHaveLength(sessionMapMarks(fixtureTurns, fixtureSubagents).length)
  })

  it('paints marks with ONLY the --color-primary / --color-on-surface-low vocabulary, both present', () => {
    const { container } = render(<SessionMapRail marks={sessionMapMarks(fixtureTurns, fixtureSubagents)} {...railProps} />)
    const tones = [...railMarks(container)].map((m) => (m as HTMLElement).style.color)
    // No third tone and no raw value — every mark is exactly one of the two tokens.
    for (const t of tones) {
      expect(t === 'var(--color-primary)' || t === 'var(--color-on-surface-low)', `unexpected mark tone: ${t}`).toBe(true)
    }
    // And BOTH tones actually appear (an all-grey or all-coral rail would be a lie about state).
    expect(tones).toContain('var(--color-primary)')        // the current region
    expect(tones).toContain('var(--color-on-surface-low)') // history
    // The line inherits it rather than restating it — one declaration, which is the one
    // `schemeContrast.test.ts` parses. A line painting its own literal would break that guard.
    for (const line of markLines(container)) {
      // Lower-cased: jsdom normalises the CSS-wide keyword, so a case-sensitive compare fails on a
      // rail that is painting correctly.
      expect((line as HTMLElement).style.background.toLowerCase(), 'the line must take the row’s currentColor')
        .toBe('currentcolor')
    }
  })

  it('lights the current region (the newest turn + its sub-events) coral, history neutral', () => {
    const marks = sessionMapMarks(fixtureTurns, fixtureSubagents)
    const { container } = render(<SessionMapRail marks={marks} {...railProps} />)
    const nodes = [...railMarks(container)] as HTMLElement[]
    // The assistant turn is the newest (visibleIndex 1); the user turn (0) is history.
    marks.forEach((mark, i) => {
      const expected = mark.visibleIndex === 1 ? 'var(--color-primary)' : 'var(--color-on-surface-low)'
      expect(nodes[i].style.color, `mark ${i} (${mark.kind})`).toBe(expected)
    })
    expect(nodes[0].style.color).toBe('var(--color-on-surface-low)') // user turn = history
    expect(nodes[1].style.color).toBe('var(--color-primary)')        // assistant turn = current
  })

  it('🔑 says "current" in ARIA too, so the region is not hue-alone', () => {
    // The open design finding this closes: `--color-primary` against `--color-on-surface-low`
    // measures 1.004:1 in dark/coral, so a reader who cannot separate the two hues had NO signal.
    // Codex marks every on-screen item `aria-current="true"`; PC carried the fact as `data-` only.
    const marks = sessionMapMarks(fixtureTurns, fixtureSubagents)
    const { container } = render(<SessionMapRail marks={marks} {...railProps} />)
    const nodes = [...railMarks(container)] as HTMLElement[]
    marks.forEach((mark, i) => {
      const current = mark.visibleIndex === 1
      expect(nodes[i].getAttribute('aria-current'), `mark ${i} (${mark.kind})`).toBe(current ? 'true' : null)
    })
    // Non-vacuity: both answers appear, so neither an all-current nor an all-history rail passes.
    const flags = nodes.map((n) => n.getAttribute('aria-current'))
    expect(flags).toContain('true')
    expect(flags).toContain(null)
  })

  it('🔑 LENGTH is the second channel — three idle rungs, and the pair (length, tone) is distinguishing', () => {
    // The Codex property, and the VISUAL half of the hue-alone fix. Asserted as the rendered scale
    // rather than a class name, because the scale is what a reader sees.
    //
    // Three turns so every class exists at once: a HISTORY sub-event (the shortest rung), a HISTORY
    // turn, a CURRENT sub-event and a CURRENT turn (the longest). With no IntersectionObserver rows
    // the current region is the newest turn, so turn 2's marks are current and turns 0-1's are not.
    const turns: ChatTurn[] = [
      { role: 'user', ts: USER_TS, visibleIndex: 0, segments: [{ kind: 'text', text: 'first' }] },
      { role: 'assistant', ts: ASST_TS, visibleIndex: 1, segments: [
        { kind: 'text', text: 'ok' },
        { kind: 'tool', id: 'h', tool: 'Read', detail: 'a.ts', done: true },
      ] },
      { role: 'assistant', ts: ASST_TS, visibleIndex: 2, segments: [
        { kind: 'text', text: 'done' },
        { kind: 'tool', id: 'c', tool: 'Write', detail: 'b.ts', done: true },
      ] },
    ]
    const marks = sessionMapMarks(turns)
    // [0] history turn · [1] history turn · [2] history sub-event · [3] current turn · [4] current sub-event
    expect(marks.map((m) => `${m.kind}@${m.visibleIndex}`))
      .toEqual(['user@0', 'assistant@1', 'tool@1', 'assistant@2', 'tool@2'])
    const { container } = render(<SessionMapRail marks={marks} {...railProps} />)
    const lines = [...markLines(container)]
    const at = (i: number) => scaleOf(lines[i])
    // Every scale must be a real number: a missing transform reads NaN and fails here, so a rail
    // that simply stopped animating cannot pass this by rendering nothing.
    for (let i = 0; i < lines.length; i++) expect(at(i), `mark ${i} has no scaleX`).not.toBeNaN()

    const historySub = at(2), historyTurn = at(1), currentSub = at(4), currentTurn = at(3)
    expect(historySub, 'a history sub-event is the shortest rung').toBeLessThan(historyTurn)
    expect(currentTurn, 'a current turn is the longest rung').toBeGreaterThan(historyTurn)
    // 🔑 THE ONE EQUALITY, AND IT IS BY DESIGN: a CURRENT sub-event idles at exactly the length of a
    // HISTORY turn. That is not a collision because TONE separates those two (coral vs neutral ink),
    // so the pair is still distinguishing for all four classes while neither channel carries the
    // state alone. Pinned as an equality so a later edit that makes them differ has to say why.
    expect(currentSub, 'a current sub-event idles at a history turn’s length').toBe(historyTurn)
    // The floor is a fraction, not zero: a mark that scaled to 0 would be invisible, not short.
    for (let i = 0; i < lines.length; i++) expect(at(i)).toBeGreaterThanOrEqual(0.25)
    // And nothing reaches full length at rest — full length is reserved for the lensed mark.
    expect(Math.max(...lines.map((_, i) => at(i))), 'nothing idles at full length').toBeLessThan(1)
  })

  it('self-suppresses (renders null) below the 2-mark threshold, at the boundary', () => {
    // 1 mark → null: a one-mark map indexes nothing.
    const one = sessionMapMarks([{ role: 'user', ts: USER_TS, visibleIndex: 0, segments: [{ kind: 'text', text: 'hi' }] }])
    expect(one).toHaveLength(1)
    const { container: c1, queryByRole: q1 } = render(<SessionMapRail marks={one} {...railProps} />)
    expect(q1('navigation')).toBeNull()
    expect(railMarks(c1)).toHaveLength(0)
    // Exactly 2 marks → rendered: the threshold renders AT two, it does not require three.
    const two = sessionMapMarks(fixtureTurns).slice(0, 2)
    expect(two).toHaveLength(2)
    const { queryByRole: q2, container: c2 } = render(<SessionMapRail marks={two} {...railProps} />)
    expect(q2('navigation', { name: 'Session map' })).not.toBeNull()
    expect(railMarks(c2)).toHaveLength(2)
  })
})
