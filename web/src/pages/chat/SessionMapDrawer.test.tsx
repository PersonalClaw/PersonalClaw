import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SessionMapDrawer } from './SessionMapDrawer'
import { sessionMapEntries } from './sessionMap'
import { sessionMapMarkName } from './SessionMapCard'
import type { ChatTurn } from './chatTypes'

// ── SSM-10 — THE COARSE-POINTER FORM ────────────────────────────────────────────────────────
//
// §A.8: because the Session Map is the SOLE in-session index nav, the mobile form may not simply
// drop it. The rail collapses to one named control — `ChatPage`'s "Session map" header control —
// that opens this drawer, and every entry becomes a tappable row. The owner's 2026-09-25 rules
// apply to both forms: one entry per USER message, previewing the start of its reply.
//
// WHAT THIS FILE OWNS vs. WHAT THE BROWSER GATE OWNS. Two of this atom's properties cannot be
// observed in jsdom, and asserting them here would be asserting a class name rather than a fact:
//   · the 44px touch floor — jsdom computes no layout, so `e2e/sessionMap.spec.ts` MEASURES a
//     row's real height at a real 390px viewport;
//   · "the rail collapses" — the pointer-form switch lives in `ChatPage`, which is not mountable
//     here (the reason `contextLedgerReach.test.tsx` records). The browser gate drives it.
// What IS testable here is the drawer's own contract, and it is the half that decides whether a
// tap navigates anywhere: one row per message, the rail's names, the entries' coordinates.

/** Three questions, one of whose replies is busy with tool calls, an approval and an error, and
 *  whose coordinates are NOT their row positions (`hydrateTurns` merges messages, so a turn's
 *  `visibleIndex` runs ahead of its array position on any tool-using transcript). */
const TURNS: ChatTurn[] = [
  { role: 'user', ts: '2026-09-18T12:00:00Z', visibleIndex: 0, segments: [{ kind: 'text', text: 'Please **run** the build.' }] },
  {
    role: 'assistant',
    ts: '2026-09-18T12:00:09Z',
    visibleIndex: 3,
    segments: [
      { kind: 'text', text: 'On it — building now.' },
      { kind: 'tool', id: 't-ok', tool: 'Terminal', detail: 'npm run build', done: true },
      { kind: 'tool', id: 't-bad', tool: 'Terminal', detail: 'npm test', done: true, ok: false },
      { kind: 'approval', id: 'a-1', tool: 'Write', input: 'src/app.ts' },
      { kind: 'error', text: 'Build failed: 2 type errors' },
    ],
  },
  { role: 'user', ts: '2026-09-18T12:01:00Z', visibleIndex: 4, segments: [{ kind: 'text', text: 'Fix the type errors.' }] },
  { role: 'assistant', ts: '2026-09-18T12:01:30Z', visibleIndex: 7, segments: [{ kind: 'text', text: 'Both fixed.' }] },
  { role: 'user', ts: '2026-09-18T12:02:00Z', visibleIndex: 8, segments: [{ kind: 'text', text: 'Great, thanks.' }] },
]

const ENTRIES = sessionMapEntries(TURNS)

const rows = () => screen.getAllByRole('button')

describe('the Session Map drawer (SSM-10)', () => {
  it('🔑 renders exactly one tappable row per USER message — no row for a reply or its work', () => {
    render(<SessionMapDrawer entries={ENTRIES} onJumpTo={() => {}} />)
    expect(ENTRIES).toHaveLength(3)
    expect(rows()).toHaveLength(TURNS.filter((t) => t.role === 'user').length)
  })

  it('🔑 a tap jumps to the entry\'s own coordinate, not its row position', async () => {
    const onJumpTo = vi.fn()
    render(<SessionMapDrawer entries={ENTRIES} onJumpTo={onJumpTo} />)
    // Row 2 is the third question: row index 2, coordinate 8. A drawer that handed back its own
    // list position would send the reader to the wrong turn — the defect the array-position node
    // registry had.
    await userEvent.click(rows()[2])
    expect(onJumpTo).toHaveBeenCalledTimes(1)
    expect(onJumpTo).toHaveBeenCalledWith(8)
  })

  it('🔑 names every row with the RAIL\'s name, so the two forms cannot drift', () => {
    render(<SessionMapDrawer entries={ENTRIES} onJumpTo={() => {}} />)
    const names = rows().map((b) => b.getAttribute('aria-label'))
    expect(names).toEqual(ENTRIES.map((_e, i) => sessionMapMarkName(ENTRIES, i)))
    expect(names[0]).toBe('Message 1 of 3: Please run the build.')
    for (const n of names) expect(n!.length).toBeLessThan(80)
  })

  it('every row inlines the card: the message, then the start of its reply, muted', () => {
    render(<SessionMapDrawer entries={ENTRIES} onJumpTo={() => {}} />)
    const first = rows()[0]
    expect(first.querySelector('[data-session-map-request]')?.textContent).toBe('Please run the build.')
    const res = first.querySelector('[data-session-map-response]') as HTMLElement
    // The reply's TEXT — not its tool line, approval or error, which follow it in the turn.
    expect(res.textContent).toBe('On it — building now.')
    expect(res.className.split(/\s+/)).toContain('text-on-surface-var')
    // And no kind column: every row is a user message.
    expect(first.querySelector('[data-session-map-row-kind]')).toBeNull()
  })

  it('🪤 has NO return-to-newest control of its own (SSM-9 owns the app\'s only one)', () => {
    render(<SessionMapDrawer entries={ENTRIES} onJumpTo={() => {}} />)
    expect(screen.queryByRole('button', { name: /latest|newest|bottom/i })).toBeNull()
  })

  it('self-suppresses below the shared threshold — and SAYS SO, because the user opened it', () => {
    render(<SessionMapDrawer entries={sessionMapEntries(TURNS.slice(0, 2))} onJumpTo={() => {}} />)
    // The rail renders nothing at this threshold; a drawer the user deliberately opened must not
    // be a blank panel, so the same rule surfaces as an explanation instead of an absence.
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText('Nothing to map yet')).toBeInTheDocument()
  })
})
