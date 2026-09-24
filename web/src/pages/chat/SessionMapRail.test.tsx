import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { SessionMapRail } from './SessionMapRail'
import { sessionMapMarks } from './sessionMap'
import type { ChatTurn, Segment, SubagentCard } from './chatTypes'

// ── SSM-4 — the Session Map rail component (marks + track) ──────────────────────────────
//
// done_when: a <nav aria-label="Session map"> renders EXACTLY sessionMapMarks(turns).length
// marks; the track uses --color-rail and marks use --color-primary / --color-on-surface-low
// (no raw hex/px — tokenLint.test.ts; no colored side-stripe — sideStripeDoctrine.test.ts,
// both global source-scan rails that this new file is auto-included in); the rail
// self-suppresses (renders null) when marks < 2.
//
// VACUITY FLOOR. "renders N marks" is faked by a component that renders N of ANYTHING, and
// "self-suppresses" is faked by a component that never renders. So:
//   · the count is asserted against the REAL sessionMapMarks output — not a hand-picked N —
//     so a wrong contract or a per-turn-only rail fails here as well;
//   · both TONE tokens are asserted to actually land on the elements (an all-grey rail, or one
//     that paints a raw value, fails) and NO third tone is allowed;
//   · the suppression is proven AT THE BOUNDARY (1 → null, 2 → rendered), so it can neither be
//     a no-op nor an always-null.

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
const track = (c: HTMLElement) => c.querySelector('[data-session-map-track]')

describe('SessionMapRail — marks + track (SSM-4)', () => {
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

  it('paints the track in the --color-rail chrome tone', () => {
    const { container } = render(<SessionMapRail marks={sessionMapMarks(fixtureTurns, fixtureSubagents)} {...railProps} />)
    const t = track(container) as HTMLElement | null
    expect(t).not.toBeNull()
    expect(t!.style.background).toContain('var(--color-rail)')
  })

  it('paints marks with ONLY the --color-primary / --color-on-surface-low vocabulary, both present', () => {
    const { container } = render(<SessionMapRail marks={sessionMapMarks(fixtureTurns, fixtureSubagents)} {...railProps} />)
    const bgs = [...railMarks(container)].map((m) => (m as HTMLElement).style.background)
    // No third tone and no raw value — every mark is exactly one of the two tokens.
    for (const bg of bgs) {
      expect(bg === 'var(--color-primary)' || bg === 'var(--color-on-surface-low)', `unexpected mark tone: ${bg}`).toBe(true)
    }
    // And BOTH tones actually appear (an all-grey or all-coral rail would be a lie about state).
    expect(bgs).toContain('var(--color-primary)')        // the current region
    expect(bgs).toContain('var(--color-on-surface-low)') // history
  })

  it('lights the current region (the newest turn + its sub-events) coral, history neutral', () => {
    const marks = sessionMapMarks(fixtureTurns, fixtureSubagents)
    const { container } = render(<SessionMapRail marks={marks} {...railProps} />)
    const nodes = [...railMarks(container)] as HTMLElement[]
    // The assistant turn is the newest (visibleIndex 1); the user turn (0) is history.
    marks.forEach((mark, i) => {
      const expected = mark.visibleIndex === 1 ? 'var(--color-primary)' : 'var(--color-on-surface-low)'
      expect(nodes[i].style.background, `mark ${i} (${mark.kind})`).toBe(expected)
    })
    expect(nodes[0].style.background).toBe('var(--color-on-surface-low)') // user turn = history
    expect(nodes[1].style.background).toBe('var(--color-primary)')        // assistant turn = current
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
