import { describe, it, expect, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SessionMapDrawer } from './SessionMapDrawer'
import { sessionMapEntries } from './sessionMap'
import { sessionMapMarkName } from './SessionMapCard'
import type { ChatTurn } from './chatTypes'

// ── THE COARSE-POINTER FORM ─────────────────────────────────────────────────────────────────
//
// Because the Session Map is the SOLE in-session index nav, the mobile form may not simply
// drop it. The rail collapses to one named control — `ChatPage`'s "Session map" header control —
// that opens this drawer, and every entry becomes a tappable row. The same rules
// apply to both forms: one entry per USER message, previewing the start of its reply.
//
// WHAT THIS FILE OWNS vs. WHAT THE BROWSER GATE OWNS. Two of this change's properties cannot be
// observed in jsdom, and asserting them here would be asserting a class name rather than a fact:
//   · the 44px touch floor — jsdom computes no layout, so `e2e/sessionMap.spec.ts` MEASURES a
//     row's real height at a real 390px viewport;
//   · "the rail collapses" — the pointer-form switch lives in `ChatPage`, which is not mountable
//     here (the reason `contextLedgerReach.test.tsx` records). The browser gate drives it.
// What IS testable here is the drawer's own contract, and it is the half that decides whether a
// tap navigates anywhere: one row per message, the rail's names, the entries' coordinates.

/** Four questions, one of whose replies is busy with tool calls, an approval and an error, and
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
  { role: 'user', ts: '2026-09-18T12:03:00Z', visibleIndex: 9, segments: [{ kind: 'text', text: 'Ship it.' }] },
]

const ENTRIES = sessionMapEntries(TURNS)

const rows = () => [...document.querySelectorAll('[data-session-map-row]')] as HTMLElement[]
/** A row's jump: `ui/RowHitTarget`'s button, the row's first. */
const jumpOf = (row: HTMLElement) => row.querySelector('button') as HTMLButtonElement

describe('the Session Map drawer', () => {
  it('🔑 renders exactly one tappable row per USER message — no row for a reply or its work', () => {
    render(<SessionMapDrawer entries={ENTRIES} onJumpTo={() => {}} />)
    expect(ENTRIES).toHaveLength(4)
    expect(rows()).toHaveLength(TURNS.filter((t) => t.role === 'user').length)
  })

  it('🔑 a tap jumps to the entry\'s own coordinate, not its row position', async () => {
    const onJumpTo = vi.fn()
    render(<SessionMapDrawer entries={ENTRIES} onJumpTo={onJumpTo} />)
    // Row 2 is the third question: row index 2, coordinate 8. A drawer that handed back its own
    // list position would send the reader to the wrong turn — the defect the array-position node
    // registry had.
    await userEvent.click(jumpOf(rows()[2]))
    expect(onJumpTo).toHaveBeenCalledTimes(1)
    expect(onJumpTo).toHaveBeenCalledWith(8)
  })

  it('🔑 names every row with the RAIL\'s name, so the two forms cannot drift', () => {
    render(<SessionMapDrawer entries={ENTRIES} onJumpTo={() => {}} />)
    const names = rows().map((r) => jumpOf(r).getAttribute('aria-label'))
    expect(names).toEqual(ENTRIES.map((_e, i) => sessionMapMarkName(ENTRIES, i)))
    expect(names[0]).toBe('Message 1 of 4: Please run the build.')
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

  it('🪤 has NO return-to-newest control of its own (SessionMapReturnLatest is the app\'s only one)', () => {
    render(<SessionMapDrawer entries={ENTRIES} onJumpTo={() => {}} />)
    expect(screen.queryByRole('button', { name: /latest|newest|bottom/i })).toBeNull()
  })

  it('self-suppresses below the shared threshold — and SAYS SO, because the user opened it', () => {
    render(<SessionMapDrawer entries={sessionMapEntries(TURNS.slice(0, 5))} onJumpTo={() => {}} />)
    // The rail renders nothing at this threshold; a drawer the user deliberately opened must not
    // be a blank panel, so the same rule surfaces as an explanation instead of an absence.
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText('Nothing to map yet')).toBeInTheDocument()
  })

  it('🔑 every row ends in a 44 px bookmark control naming its message, through the rail’s own handler', () => {
    const onToggle = vi.fn()
    const marked = sessionMapEntries(TURNS, new Set(['2026-09-18T12:01:00Z']))
    const onJump = vi.fn()
    render(<SessionMapDrawer entries={marked} onJumpTo={onJump} onToggleBookmark={onToggle} />)
    expect(screen.getByRole('button', { name: 'Bookmark this message: message 1' })).toBeTruthy()
    const off = screen.getByRole('button', { name: 'Remove bookmark: message 2' })
    fireEvent.click(off)
    expect(onToggle).toHaveBeenCalledWith(marked[1])
    expect(onJump, 'the bookmark’s click also jumped').not.toHaveBeenCalled()
    // The bookmarked row says so in its name and carries the rail's dot.
    expect(jumpOf(rows()[1]).getAttribute('aria-label')).toBe('Message 2 of 4, bookmarked: Fix the type errors.')
    expect(rows()[1].querySelector('[data-session-map-row-mark] span + span')).not.toBeNull()
    expect(rows()[0].querySelector('[data-session-map-row-mark] span + span')).toBeNull()
  })

  it('says which exchanges were on screen behind it as it opened, without colour alone', () => {
    const nodes = new Map<number, Element>()
    const box = (top: number, bottom: number) => ({ top, bottom, left: 0, right: 300, width: 300, height: bottom - top, x: 0, y: top, toJSON: () => ({}) }) as DOMRect
    for (const t of TURNS) {
      const el = document.createElement('div')
      const v = t.visibleIndex!
      // Only the second exchange (turns 4 and 7) is inside the transcript's box.
      el.getBoundingClientRect = () => (v === 4 || v === 7 ? box(100, 300) : box(-900, -800))
      nodes.set(v, el)
    }
    const scroller = document.createElement('div')
    scroller.getBoundingClientRect = () => box(0, 600)
    render(<SessionMapDrawer entries={ENTRIES} onJumpTo={() => {}} turnNodes={nodes} scrollRef={{ current: scroller }} />)
    expect(rows().map((r) => jumpOf(r).getAttribute('aria-current'))).toEqual([null, 'true', null, null])
    // The rail's marker for it is the longer, on-screen rest as well as the accent.
    const lines = rows().map((r) => (r.querySelector('[data-session-map-row-mark] span') as HTMLElement).style.transform)
    expect(lines[1]).not.toBe(lines[0])
  })

  it('the rows are still: no staggered entrance on a list opened to navigate', () => {
    render(<SessionMapDrawer entries={ENTRIES} onJumpTo={() => {}} />)
    for (const r of rows()) expect(r.getAttribute('style') ?? '', 'a row animates in').not.toMatch(/opacity|transform/)
  })
})
