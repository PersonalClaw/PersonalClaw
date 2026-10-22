import { describe, it, expect, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SessionMapRail } from './SessionMapRail'
import { SessionMapCard, sessionMapMarkName } from './SessionMapCard'
import { sessionMapEntries } from './sessionMap'
import { clockTime, fullStamp, isoStamp } from '../../lib/epoch'
import type { ChatTurn, Segment } from './chatTypes'

// ── The hover/focus preview card ────────────────────────────────────────────────────────────
//
// The contract, in the reference app's form: hovering a marker opens a `Popover`-based
// card showing the user's request, truncated, in `--color-on-surface` (asserted NOT
// `--color-on-surface-low`), a MUTED excerpt of the beginning of the reply, and a small timestamp;
// focus alone does not auto-open the card.
//
// VACUITY FLOOR — the ways this contract is satisfiable by something that does not work:
//
//  1. "HOVER OPENS A CARD" passes for a card that is ALWAYS mounted. So every open assertion is
//     preceded by an assertion that the card is ABSENT, and closed again by asserting it goes away
//     on mouse-out — three states, not one.
//  2. "FOCUS DOES NOT AUTO-OPEN" passes trivially for a rail with NO FOCUSABLE ELEMENT. So the test
//     focuses the marker and asserts `document.activeElement` IS it, and that the SAME element opens
//     the card on hover.
//  3. "NOT `--color-on-surface-low`" passes a regex that cannot tell the two apart:
//     `text-on-surface` is a SUBSTRING of `text-on-surface-low`. Every colour assertion here compares
//     whole class TOKENS (`className.split(/\s+/)`), never a substring match.
//  4. "THE BEGINNING OF THE REPLY" passes for a card that shows the reply's tool line or stats line
//     — which is what a map of every event used to preview. The fixture's reply carries a tool call
//     and an error AFTER its text, and the excerpt is asserted to be the text.
//
// Real timers on purpose. The open delay is the product behaviour under test (a pointer crossing
// the rail must not strobe a card per marker), so the test waits it out rather than faking it away.

const USER_TS = '2026-09-16T10:00:00.000Z'
const ASST_TS = '2026-09-16T10:00:05.000Z'

const reply: Segment[] = [
  { kind: 'text', text: 'Build finished in 4.1s with no errors.' },
  { kind: 'tool', id: 't-ok', tool: 'Terminal', detail: 'npm run build', done: true },
  { kind: 'error', text: 'ValidationException: input is too long for the model.' },
]

const fixtureTurns: ChatTurn[] = [
  { role: 'user', ts: USER_TS, visibleIndex: 0, segments: [{ kind: 'text', text: 'Please **run** the build.' }] },
  { role: 'assistant', ts: ASST_TS, visibleIndex: 1, segments: reply },
  { role: 'user', ts: USER_TS, visibleIndex: 2, segments: [{ kind: 'text', text: 'And deploy it.' }] },
  { role: 'assistant', ts: ASST_TS, visibleIndex: 3, segments: [{ kind: 'text', text: 'Deployed to staging.' }] },
  { role: 'user', ts: USER_TS, visibleIndex: 4, segments: [{ kind: 'text', text: 'Tag it.' }] },
  { role: 'user', ts: USER_TS, visibleIndex: 5, segments: [{ kind: 'text', text: 'Announce it.' }] },
]

const entries = () => sessionMapEntries(fixtureTurns)

/** The rail's viewport/jump wiring, inert for these pointer cases: no turn nodes means the observer
 *  reports nothing on screen, which `currentMarkRange` answers with the newest message. */
const railProps = { turnNodes: new Map<number, Element>(), scrollRef: { current: null }, onJumpTo: () => {} }

const markers = (c: HTMLElement) => [...c.querySelectorAll('[data-session-mark]')] as HTMLElement[]
const card = () => document.querySelector('[data-session-map-card]') as HTMLElement | null
const classes = (el: HTMLElement) => el.className.split(/\s+/).filter(Boolean)

/** Point at a marker and wait out the open delay. */
async function hover(marker: HTMLElement) {
  fireEvent.pointerMove(marker, { pointerType: 'mouse' })
  await waitFor(() => expect(card()).not.toBeNull())
  return card()!
}

describe('sessionMapMarkName — short, positioned, bounded', () => {
  it('is "Message N of M" plus the message’s own first words', () => {
    const e = entries()
    // Markdown stripped by the `previewText`, so the name never carries `**run**`.
    expect(sessionMapMarkName(e, 0)).toBe('Message 1 of 4: Please run the build.')
    expect(sessionMapMarkName(e, 1)).toBe('Message 2 of 4: And deploy it.')
  })

  it('says a bookmark between the position and the words', () => {
    const e = sessionMapEntries(fixtureTurns, new Set([USER_TS]))
    expect(sessionMapMarkName(e, 1)).toBe('Message 2 of 4, bookmarked: And deploy it.')
  })

  it('is never the full message text', () => {
    const long = 'x'.repeat(400)
    const e = sessionMapEntries([{ role: 'user', ts: USER_TS, visibleIndex: 0, segments: [{ kind: 'text', text: long }] },
      ...fixtureTurns.slice(1)])
    const name = sessionMapMarkName(e, 0)
    expect(name.length).toBeLessThan(80)   // the app-wide interactive-name budget
    expect(name).toMatch(/…$/)
  })
})

describe('the card: the request, a muted excerpt of the reply, and a timestamp', () => {
  it('a REAL <time> for the message’s stamp — the reference card had none', () => {
    render(<SessionMapCard entry={entries()[0]} />)
    const t = screen.getByText(clockTime(USER_TS))
    expect(t.tagName).toBe('TIME')
    expect(t.getAttribute('dateTime')).toBe(isoStamp(USER_TS))
    expect(t.getAttribute('title')).toBe(fullStamp(USER_TS))
  })

  it('carries NO role label — every entry is a user message, so the role is the map’s shape', () => {
    const { container } = render(<SessionMapCard entry={entries()[0]} />)
    expect(container.querySelector('[data-session-map-role]')).toBeNull()
    expect(container.textContent).not.toMatch(/\b(You|Assistant)\b/)
  })

  it('the REQUEST line is on-surface ink — explicitly NOT the dimmed metadata ramp', () => {
    const { container } = render(<SessionMapCard entry={entries()[0]} />)
    const req = container.querySelector('[data-session-map-request]') as HTMLElement
    expect(req.textContent).toBe('Please run the build.')
    // Whole class tokens: `text-on-surface` is a substring of `text-on-surface-low`, so a
    // substring match here would pass on the exact defect this contract forbids.
    expect(classes(req)).toContain('text-on-surface')
    expect(classes(req), 'the flagged low-contrast metadata treatment').not.toContain('text-on-surface-low')
    expect(classes(req)).toContain('line-clamp-2')   // truncated, not a wall of text
  })

  it('the clock IS allowed the dimmed ramp — it is chrome, not the sentence you came to read', () => {
    const { container } = render(<SessionMapCard entry={entries()[0]} />)
    const t = container.querySelector('time') as HTMLElement
    expect(classes(t)).toContain('text-on-surface-low')
    expect(t.getAttribute('data-type')).toBe('caption')
  })

  it('🔑 the excerpt is the BEGINNING of the reply — not its tool line or its error — muted, under a hairline', () => {
    const { container } = render(<SessionMapCard entry={entries()[0]} />)
    const res = container.querySelector('[data-session-map-response]') as HTMLElement
    expect(res.textContent).toBe('Build finished in 4.1s with no errors.')
    expect(classes(res)).toContain('text-on-surface-var')
    expect(classes(res)).toContain('border-t')
    expect(classes(res)).toContain('border-outline-variant')
  })

  it('truncates a long request rather than rendering the message', () => {
    const long = `Please ${'refactor the loader '.repeat(30)}today.`
    const e = sessionMapEntries([{ role: 'user', ts: USER_TS, visibleIndex: 0, segments: [{ kind: 'text', text: long }] },
      fixtureTurns[1]])
    const { container } = render(<SessionMapCard entry={e[0]} />)
    const req = container.querySelector('[data-session-map-request]') as HTMLElement
    expect(req.textContent!.length).toBeLessThanOrEqual(140)   // The preview cap
    expect(req.textContent).toMatch(/…$/)
  })

  it('says nothing rather than inventing an answer that has not arrived', () => {
    const e = sessionMapEntries([fixtureTurns[0], fixtureTurns[1], { role: 'user', ts: USER_TS, visibleIndex: 2, segments: [{ kind: 'text', text: 'and again' }] }])
    const { container } = render(<SessionMapCard entry={e[1]} />)
    expect(container.querySelector('[data-session-map-response]')).toBeNull()
  })

  it('renders no clock at all when the message carries no stamp', () => {
    const e = sessionMapEntries([{ role: 'user', ts: '', visibleIndex: 0, segments: [{ kind: 'text', text: 'hi' }] },
      { role: 'assistant', ts: '', visibleIndex: 1, segments: [{ kind: 'text', text: 'hello' }] }])
    const { container } = render(<SessionMapCard entry={e[0]} />)
    expect(container.querySelectorAll('time')).toHaveLength(0)
    expect(container.textContent).not.toMatch(/NaN|Invalid/)
  })
})

describe('the preview: what the reply did, its placeholder and its bookmark', () => {
  const busy: Segment[] = [
    { kind: 'text', text: 'On it.' },
    { kind: 'tool', id: 'a', tool: 'Terminal', detail: 'npm test', done: true },
    { kind: 'tool', id: 'b', tool: 'Read', detail: 'src/missing.ts', done: true, ok: false },
    { kind: 'approval', id: 'c', tool: 'Terminal', input: 'rm -rf build', risk: 'destructive' },
    { kind: 'error', text: 'ValidationException: too long.' },
  ]
  const busyEntry = () => sessionMapEntries([fixtureTurns[0], { role: 'assistant', ts: ASST_TS, visibleIndex: 1, segments: busy }])[0]

  it('🔑 lists the first two things the reply did, in order and by kind, and counts the rest', () => {
    const { container } = render(<SessionMapCard entry={busyEntry()} />)
    const outs = [...container.querySelectorAll('[data-session-map-output]')] as HTMLElement[]
    expect(outs.map((o) => o.getAttribute('data-session-map-output'))).toEqual(['tool', 'tool'])
    expect(outs[0].textContent).toBe('RanTerminal — npm test')
    // A failed tool call says so, not "Ran".
    expect(outs[1].textContent).toBe('FailedRead — src/missing.ts')
    expect(container.querySelector('[data-session-map-more]')!.textContent).toBe('+2 more')
  })

  it('lists nothing, and counts nothing, for a reply that only answered', () => {
    const { container } = render(<SessionMapCard entry={entries()[1]} />)
    expect(container.querySelector('[data-session-map-outputs]')).toBeNull()
    expect(container.querySelector('[data-session-map-more]')).toBeNull()
  })

  it('holds the place of a reply still on its way with a still placeholder, and says so', () => {
    const e = sessionMapEntries(fixtureTurns)
    const { container } = render(<SessionMapCard entry={e[3]} replyPending />)
    const pending = container.querySelector('[data-session-map-pending]') as HTMLElement
    expect(pending).not.toBeNull()
    expect(pending.textContent).toBe('The reply is on its way')
    expect(pending.innerHTML, 'a placeholder on a still rail must not shimmer').not.toMatch(/skeleton|animate-|shimmer/)
    // Not pending: nothing is invented.
    const { container: c2 } = render(<SessionMapCard entry={e[3]} />)
    expect(c2.querySelector('[data-session-map-pending]')).toBeNull()
  })

  it('🔑 offers the bookmark, says which way it goes, and hands the entry back', () => {
    const onToggle = vi.fn()
    const e = sessionMapEntries(fixtureTurns)
    const { getByRole, rerender } = render(<SessionMapCard entry={e[0]} onToggleBookmark={onToggle} />)
    fireEvent.click(getByRole('button', { name: 'Bookmark this message' }))
    expect(onToggle).toHaveBeenCalledWith(e[0])
    rerender(<SessionMapCard entry={{ ...e[0], bookmarked: true }} onToggleBookmark={onToggle} />)
    expect(getByRole('button', { name: 'Remove bookmark' })).toBeTruthy()
  })

  it('a message not yet saved cannot be bookmarked, and the control says why', () => {
    const onToggle = vi.fn()
    const e = sessionMapEntries([{ role: 'user', ts: '', visibleIndex: 0, segments: [{ kind: 'text', text: 'just sent' }] }])
    const { getByRole } = render(<SessionMapCard entry={e[0]} onToggleBookmark={onToggle} />)
    const b = getByRole('button', { name: 'Bookmark this message' })
    expect(b.getAttribute('aria-disabled')).toBe('true')
    expect(b.getAttribute('title')).toMatch(/once it is saved/)
    fireEvent.click(b)
    expect(onToggle).not.toHaveBeenCalled()
  })

  it('shows the keys that move between messages when the rail hands them over, and nothing otherwise', () => {
    const { container, rerender } = render(<SessionMapCard entry={entries()[0]} keys="⌥↑ ⌥↓ your previous and next message, from anywhere" />)
    expect(container.querySelector('[data-session-map-keys]')!.textContent).toBe('⌥↑ ⌥↓ your previous and next message, from anywhere')
    rerender(<SessionMapCard entry={entries()[0]} />)
    expect(container.querySelector('[data-session-map-keys]')).toBeNull()
  })

  it('no handler, no control: nothing offers what cannot be kept', () => {
    const { container } = render(<SessionMapCard entry={entries()[0]} />)
    expect(container.querySelector('button')).toBeNull()
  })
})

describe('the rail’s ONE preview opens on pointing, and not on focus', () => {
  it('a marker is a real, named, focusable control — one tab stop for the whole rail', () => {
    const e = entries()
    const { container } = render(<SessionMapRail entries={e} {...railProps} />)
    const m = markers(container)
    expect(m).toHaveLength(e.length)
    for (const el of m) expect(el.tagName).toBe('BUTTON')
    m.forEach((el, i) => expect(el.getAttribute('aria-label')).toBe(sessionMapMarkName(e, i)))
    expect(m.filter((el) => el.tabIndex === 0)).toHaveLength(1)
    expect(m.find((el) => el.tabIndex === 0)!.getAttribute('data-current')).toBe('true')
  })

  it('pointing opens the preview after the intent delay, in the overlay material, beside the rail', async () => {
    vi.useFakeTimers()
    try {
      const { container } = render(<SessionMapRail entries={entries()} {...railProps} />)
      const marker = markers(container)[0]
      expect(card(), 'the preview must not be mounted at rest').toBeNull()
      act(() => { fireEvent.pointerMove(marker, { pointerType: 'mouse' }) })
      // A pointer merely crossing the rail opens nothing.
      act(() => { vi.advanceTimersByTime(100) })
      expect(card()).toBeNull()
      act(() => { vi.advanceTimersByTime(100) })
      const open = card()!
      expect(open.textContent).toContain('Please run the build.')
      expect(open.textContent).toContain('Build finished in 4.1s')
      const surface = open.closest('[data-session-map-preview]') as HTMLElement
      expect(classes(surface)).toEqual(expect.arrayContaining(['glass', 'z-[var(--z-menu)]', 'rounded-lgi', 'left-full']))
      expect(surface.getAttribute('role')).toBe('group')
      expect(surface.getAttribute('aria-label')).toBe('Preview of message 1 of 4')
      // The rail's preview shows the chord keys, in this platform's own writing (`bindingLabel`).
      expect(surface.querySelector('[data-session-map-keys]')!.textContent).toMatch(/(⌥|Alt\+)↑ (⌥|Alt\+)↓ your previous and next message/)
    } finally { vi.useRealTimers() }
  })

  it('🔑 once open, moving to another marker moves the ONE preview at once — never a second', async () => {
    const { container } = render(<SessionMapRail entries={entries()} {...railProps} />)
    const m = markers(container)
    expect((await hover(m[0])).textContent).toContain('Build finished in 4.1s')
    act(() => { fireEvent.pointerMove(m[1], { pointerType: 'mouse' }) })
    expect(card()!.textContent, 'the preview waited out the delay again').toContain('Deployed to staging.')
    expect(document.querySelectorAll('[data-session-map-preview]')).toHaveLength(1)
  })

  it('🔑 FOCUS ALONE DOES NOT OPEN IT — Tab-through must not strobe a preview per marker', async () => {
    const { container } = render(<SessionMapRail entries={entries()} {...railProps} />)
    const marker = markers(container).find((el) => el.tabIndex === 0)!
    act(() => marker.focus())
    expect(document.activeElement).toBe(marker)
    await act(() => new Promise((r) => { window.setTimeout(r, 250) }))
    expect(card(), 'focus opened the preview after the hover delay elapsed').toBeNull()
    expect(await hover(marker)).not.toBeNull()
  })

  it('the pointer can cross the gap onto the preview without dismissing it, and leaving dismisses it', async () => {
    const { container } = render(<SessionMapRail entries={entries()} {...railProps} />)
    const nav = container.querySelector('nav')!
    await hover(markers(container)[0])
    // Leaving the rail's box arms the close; arriving on the preview (inside the rail) cancels it.
    fireEvent.pointerLeave(nav)
    fireEvent.pointerEnter(nav)
    await act(() => new Promise((r) => { window.setTimeout(r, 250) }))
    expect(card(), 'the preview dismissed itself while the pointer was on it').not.toBeNull()
    fireEvent.pointerLeave(nav)
    await waitFor(() => expect(card()).toBeNull())
  })
})
