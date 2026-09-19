import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SessionMapDrawer } from './SessionMapDrawer'
import { sessionMapMarks } from './sessionMap'
import { sessionMapMarkName } from './SessionMapCard'
import type { ChatTurn } from './chatTypes'

// ── SSM-10 — THE COARSE-POINTER FORM ────────────────────────────────────────────────────────
//
// §A.8: because the Session Map is the SOLE in-session index nav, the mobile form may not simply
// drop it (which is what KiroCrew's rail does on touch). The rail collapses to one named control
// — `ChatPage`'s "Session map" header control — that opens this drawer, and every mark becomes a
// tappable row.
//
// WHAT THIS FILE OWNS vs. WHAT THE BROWSER GATE OWNS. Two of this atom's properties cannot be
// observed in jsdom, and asserting them here would be asserting a class name rather than a fact:
//   · the 44px touch floor — jsdom computes no layout, so `e2e/sessionMap.spec.ts` MEASURES a
//     row's real height at a real 390px viewport;
//   · "the rail collapses" — the pointer-form switch lives in `ChatPage`, which is ~4k lines,
//     owns a socket and a composer and is not mountable here (the same reason
//     `contextLedgerReach.test.tsx` records). The browser gate drives it at both viewports.
// What IS testable here is the drawer's own contract, and it is the half that decides whether a
// tap navigates anywhere: one row per mark, the rail's names, the rail's coordinates.

/** A transcript that emits more marks than turns — a tool call, a failed tool call, an approval
 *  and an error all live inside one assistant turn, so the drawer must list SUB-EVENTS as their
 *  own rows and not collapse them into their turn. */
const TURNS: ChatTurn[] = [
  { role: 'user', ts: '2026-09-18T12:00:00Z', visibleIndex: 0, segments: [{ kind: 'text', text: 'Please **run** the build.' }] },
  {
    role: 'assistant',
    ts: '2026-09-18T12:00:09Z',
    visibleIndex: 1,
    segments: [
      { kind: 'text', text: 'On it.' },
      { kind: 'tool', id: 't-ok', tool: 'Terminal', detail: 'npm run build', done: true },
      { kind: 'tool', id: 't-bad', tool: 'Terminal', detail: 'npm test', done: true, ok: false },
      { kind: 'approval', id: 'a-1', tool: 'Write', input: 'src/app.ts' },
      { kind: 'error', text: 'Build failed: 2 type errors' },
    ],
  },
]

const MARKS = sessionMapMarks(TURNS)

const rows = () => screen.getAllByRole('button')

describe('the Session Map drawer (SSM-10)', () => {
  it('🔑 renders exactly one tappable row per mark — sub-events included', () => {
    render(<SessionMapDrawer marks={MARKS} onJumpTo={() => {}} />)
    // 6 marks from 2 turns: the guard against a drawer that lists only turns and silently drops
    // the tool/approval/error marks the rail shows.
    expect(MARKS).toHaveLength(6)
    expect(rows()).toHaveLength(MARKS.length)
    expect(MARKS.map((m) => m.kind)).toEqual(['user', 'assistant', 'tool', 'tool', 'approval', 'error'])
  })

  it('🔑 a tap jumps to the mark\'s own coordinate, not its row position', () => {
    const onJumpTo = vi.fn()
    render(<SessionMapDrawer marks={MARKS} onJumpTo={onJumpTo} />)
    // Row 3 is the FAILED tool call: row index 3, but it belongs to the assistant turn at
    // coordinate 1. A drawer that handed back its own list position would send the reader to the
    // wrong turn — the same defect the array-position node registry had.
    return userEvent.click(rows()[3]).then(() => {
      expect(onJumpTo).toHaveBeenCalledTimes(1)
      expect(onJumpTo).toHaveBeenCalledWith(MARKS[3].visibleIndex)
      expect(MARKS[3].visibleIndex).not.toBe(3)
    })
  })

  it('🔑 names every row with the RAIL\'s name, so the two forms cannot drift', () => {
    render(<SessionMapDrawer marks={MARKS} onJumpTo={() => {}} />)
    const names = rows().map((b) => b.getAttribute('aria-label'))
    expect(names).toEqual(MARKS.map((_m, i) => sessionMapMarkName(MARKS, i)))
    // Turn-scoped and bounded (§A.6) — never the full turn text.
    expect(names[0]).toMatch(/^Turn 1 of 2: /)
    for (const n of names) expect(n!.length).toBeLessThan(80)
  })

  it('every row carries its kind IN WORDS — the rail encodes it as tone, which a row cannot', () => {
    render(<SessionMapDrawer marks={MARKS} onJumpTo={() => {}} />)
    const kinds = rows().map((b) => b.querySelector('[data-session-map-row-kind]')?.textContent)
    expect(kinds).toEqual(['You', 'Assistant', 'Tool', 'Tool', 'Approval', 'Error'])
  })

  it('🪤 has NO return-to-newest control of its own (SSM-9 owns the app\'s only one)', () => {
    render(<SessionMapDrawer marks={MARKS} onJumpTo={() => {}} />)
    expect(screen.queryByRole('button', { name: /latest|newest|bottom/i })).toBeNull()
  })

  it('self-suppresses below the shared threshold — and SAYS SO, because the user opened it', () => {
    const one = sessionMapMarks([TURNS[0]])
    render(<SessionMapDrawer marks={one} onJumpTo={() => {}} />)
    // The rail renders nothing at this threshold; a drawer the user deliberately opened must not
    // be a blank panel, so the same rule surfaces as an explanation instead of an absence.
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText('Nothing to map yet')).toBeInTheDocument()
  })
})
