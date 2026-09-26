import { describe, it, expect } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SessionMapRail } from './SessionMapRail'
import { SessionMapCard, sessionMapMarkName } from './SessionMapCard'
import { sessionMapEntries } from './sessionMap'
import { clockTime, fullStamp, isoStamp } from '../../lib/epoch'
import type { ChatTurn, Segment } from './chatTypes'

// ── SSM-6 — the hover/focus preview card ────────────────────────────────────────────────────
//
// done_when, in the owner's reference form (2026-09-25): hovering a marker opens a `Popover`-based
// card showing the user's request, truncated, in `--color-on-surface` (asserted NOT
// `--color-on-surface-low`), a MUTED excerpt of the beginning of the reply, and a small timestamp;
// focus alone does not auto-open the card.
//
// VACUITY FLOOR — the ways this clause is satisfiable by something that does not work:
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
]

const entries = () => sessionMapEntries(fixtureTurns)

/** The rail's SSM-5/SSM-7 wiring, inert for these pointer cases: no turn nodes means the observer
 *  reports nothing on screen, which `currentMarkRange` answers with the newest message. */
const railProps = { turnNodes: new Map<number, Element>(), scrollRef: { current: null }, onJumpTo: () => {} }

const markers = (c: HTMLElement) => [...c.querySelectorAll('[data-session-mark]')] as HTMLElement[]
const card = () => document.querySelector('[data-session-map-card]') as HTMLElement | null
const classes = (el: HTMLElement) => el.className.split(/\s+/).filter(Boolean)

/** Hover a marker and wait out the open delay. */
async function hover(marker: HTMLElement) {
  fireEvent.mouseOver(marker)
  await waitFor(() => expect(card()).not.toBeNull())
  return card()!
}

describe('sessionMapMarkName — short, positioned, bounded (§A.6)', () => {
  it('is "Message N of M" plus the message’s own first words', () => {
    const e = entries()
    // Markdown stripped by SSM-1's `previewText`, so the name never carries `**run**`.
    expect(sessionMapMarkName(e, 0)).toBe('Message 1 of 2: Please run the build.')
    expect(sessionMapMarkName(e, 1)).toBe('Message 2 of 2: And deploy it.')
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

describe('the card: the request, a muted excerpt of the reply, and a timestamp (§A.3)', () => {
  const noop = () => {}

  it('a REAL <time> for the message’s stamp — the reference card had none', () => {
    render(<SessionMapCard entry={entries()[0]} onMouseEnter={noop} onMouseLeave={noop} />)
    const t = screen.getByText(clockTime(USER_TS))
    expect(t.tagName).toBe('TIME')
    expect(t.getAttribute('dateTime')).toBe(isoStamp(USER_TS))
    expect(t.getAttribute('title')).toBe(fullStamp(USER_TS))
  })

  it('carries NO role label — every entry is a user message, so the role is the map’s shape', () => {
    const { container } = render(<SessionMapCard entry={entries()[0]} onMouseEnter={noop} onMouseLeave={noop} />)
    expect(container.querySelector('[data-session-map-role]')).toBeNull()
    expect(container.textContent).not.toMatch(/\b(You|Assistant)\b/)
  })

  it('the REQUEST line is on-surface ink — explicitly NOT the dimmed metadata ramp', () => {
    const { container } = render(<SessionMapCard entry={entries()[0]} onMouseEnter={noop} onMouseLeave={noop} />)
    const req = container.querySelector('[data-session-map-request]') as HTMLElement
    expect(req.textContent).toBe('Please run the build.')
    // Whole class tokens: `text-on-surface` is a substring of `text-on-surface-low`, so a
    // substring match here would pass on the exact defect this clause forbids.
    expect(classes(req)).toContain('text-on-surface')
    expect(classes(req), 'the flagged low-contrast metadata treatment').not.toContain('text-on-surface-low')
    expect(classes(req)).toContain('line-clamp-2')   // truncated, not a wall of text
  })

  it('the clock IS allowed the dimmed ramp — it is chrome, not the sentence you came to read', () => {
    const { container } = render(<SessionMapCard entry={entries()[0]} onMouseEnter={noop} onMouseLeave={noop} />)
    const t = container.querySelector('time') as HTMLElement
    expect(classes(t)).toContain('text-on-surface-low')
    expect(t.getAttribute('data-type')).toBe('caption')
  })

  it('🔑 the excerpt is the BEGINNING of the reply — not its tool line or its error — muted, under a hairline', () => {
    const { container } = render(<SessionMapCard entry={entries()[0]} onMouseEnter={noop} onMouseLeave={noop} />)
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
    const { container } = render(<SessionMapCard entry={e[0]} onMouseEnter={noop} onMouseLeave={noop} />)
    const req = container.querySelector('[data-session-map-request]') as HTMLElement
    expect(req.textContent!.length).toBeLessThanOrEqual(140)   // SSM-1's preview cap
    expect(req.textContent).toMatch(/…$/)
  })

  it('says nothing rather than inventing an answer that has not arrived', () => {
    const e = sessionMapEntries([fixtureTurns[0], fixtureTurns[1], { role: 'user', ts: USER_TS, visibleIndex: 2, segments: [{ kind: 'text', text: 'and again' }] }])
    const { container } = render(<SessionMapCard entry={e[1]} onMouseEnter={noop} onMouseLeave={noop} />)
    expect(container.querySelector('[data-session-map-response]')).toBeNull()
  })

  it('renders no clock at all when the message carries no stamp', () => {
    const e = sessionMapEntries([{ role: 'user', ts: '', visibleIndex: 0, segments: [{ kind: 'text', text: 'hi' }] },
      { role: 'assistant', ts: '', visibleIndex: 1, segments: [{ kind: 'text', text: 'hello' }] }])
    const { container } = render(<SessionMapCard entry={e[0]} onMouseEnter={noop} onMouseLeave={noop} />)
    expect(container.querySelectorAll('time')).toHaveLength(0)
    expect(container.textContent).not.toMatch(/NaN|Invalid/)
  })
})

describe('the rail opens the card on HOVER and not on FOCUS', () => {
  it('a marker is a real, named, focusable control — one tab stop for the whole rail', () => {
    const e = entries()
    const { container } = render(<SessionMapRail entries={e} {...railProps} />)
    const m = markers(container)
    expect(m).toHaveLength(e.length)
    for (const el of m) expect(el.tagName).toBe('BUTTON')
    // Named, so a marker is not announced as bare "button" (axe button-name).
    m.forEach((el, i) => expect(el.getAttribute('aria-label')).toBe(sessionMapMarkName(e, i)))
    // ONE tab stop: the current region.
    expect(m.filter((el) => el.tabIndex === 0)).toHaveLength(1)
    expect(m.find((el) => el.tabIndex === 0)!.getAttribute('data-current')).toBe('true')
  })

  it('hover opens a Popover card, absent before and gone after', async () => {
    const { container } = render(<SessionMapRail entries={entries()} {...railProps} />)
    const marker = markers(container)[0]
    expect(card(), 'the card must not be mounted at rest').toBeNull()

    const open = await hover(marker)
    expect(open.textContent).toContain('Please run the build.')
    expect(open.textContent).toContain('Build finished in 4.1s')
    // It is the SHARED overlay primitive's flyout — frosted material, menu layer, radius (§A.3).
    const flyout = open.closest('.glass') as HTMLElement
    expect(flyout, 'the card must ride ui/Popover, not a bespoke floating div').not.toBeNull()
    expect(flyout.className).toContain('z-[var(--z-menu)]')
    expect(flyout.className).toContain('rounded-lgi')

    fireEvent.mouseOut(marker, { relatedTarget: document.body })
    await waitFor(() => expect(card()).toBeNull())
  })

  it('each marker opens its OWN card', async () => {
    const { container } = render(<SessionMapRail entries={entries()} {...railProps} />)
    const m = markers(container)
    expect((await hover(m[0])).textContent).toContain('Build finished in 4.1s')
    fireEvent.mouseOut(m[0], { relatedTarget: document.body })
    await waitFor(() => expect(card()).toBeNull())
    expect((await hover(m[1])).textContent).toContain('Deployed to staging.')
  })

  it('🔑 FOCUS ALONE DOES NOT OPEN IT — Tab-through must not strobe a card per marker', async () => {
    const { container } = render(<SessionMapRail entries={entries()} {...railProps} />)
    const marker = markers(container).find((el) => el.tabIndex === 0)!
    act(() => marker.focus())
    // The focus target is REAL — this clause is worthless if nothing can be focused.
    expect(document.activeElement).toBe(marker)
    expect(card()).toBeNull()
    // And it stays closed past the pointer's own open delay, so this is not just "not yet".
    await act(() => new Promise((r) => { window.setTimeout(r, 250) }))
    expect(card(), 'focus opened the card after the hover delay elapsed').toBeNull()
    // The same element DOES open it on hover, so the negative above is about focus, not a card
    // that never opens.
    expect(document.activeElement).toBe(marker)
    expect(await hover(marker)).not.toBeNull()
  })

  it('the pointer can cross the gap onto the card without dismissing it', async () => {
    const { container } = render(<SessionMapRail entries={entries()} {...railProps} />)
    const marker = markers(container)[0]
    const open = await hover(marker)
    // Leaving the marker arms the close; arriving on the card cancels it (the §A.3 bridge).
    fireEvent.mouseOut(marker, { relatedTarget: open })
    fireEvent.mouseOver(open)
    await act(() => new Promise((r) => { window.setTimeout(r, 250) }))
    expect(card(), 'the card dismissed itself while the pointer was on it').not.toBeNull()
    // Leaving the card itself does dismiss it.
    fireEvent.mouseOut(open, { relatedTarget: document.body })
    await waitFor(() => expect(card()).toBeNull())
  })
})
