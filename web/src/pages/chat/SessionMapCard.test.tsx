import { describe, it, expect } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SessionMapRail } from './SessionMapRail'
import { SessionMapCard, sessionMapCardContent, sessionMapMarkName } from './SessionMapCard'
import { sessionMapMarks } from './sessionMap'
import { clockTime, fullStamp, isoStamp } from '../../lib/epoch'
import type { ChatTurn, Segment } from './chatTypes'

// ── SSM-6 — the hover/focus preview card ────────────────────────────────────────────────────
//
// done_when: hovering/focusing a mark opens a `Popover`-based card showing a role label, a
// timestamp, a truncated request line in `--color-on-surface`/`-var` (asserted NOT
// `--color-on-surface-low`), and a response excerpt; `overlaySurfaceA11y.test.tsx` and
// `computedNames.test.tsx` pass; focus alone does not auto-open the card.
//
// VACUITY FLOOR — four ways this clause is satisfiable by something that does not work:
//
//  1. "HOVER OPENS A CARD" passes for a card that is ALWAYS mounted. So every open assertion is
//     preceded by an assertion that the card is ABSENT, and closed again by asserting it goes away
//     on mouse-out — three states, not one.
//  2. "FOCUS DOES NOT AUTO-OPEN" passes trivially for a rail with NO FOCUSABLE ELEMENT AT ALL —
//     which is exactly what SSM-4 shipped (marks `aria-hidden`, no button, no key handler). The
//     clause is only worth anything if the focus target is real, so the test focuses the mark and
//     asserts `document.activeElement` IS it, and that the SAME element opens the card on hover.
//     It also passes for a card that never opens under any input; the hover tests close that.
//  3. "NOT `--color-on-surface-low`" passes a regex that cannot tell the two apart: `text-on-surface`
//     is a SUBSTRING of `text-on-surface-low`. Every colour assertion here compares whole class
//     TOKENS (`className.split(/\s+/)`), never a substring match. (Tailwind v4 generates these
//     utilities straight from the `@theme` custom properties in `design/tokens.css`, so the token
//     `--color-on-surface` and the utility `text-on-surface` are the same fact.)
//  4. "A RESPONSE EXCERPT" passes for a card that shows the exchange and nothing else — and then a
//     turn's seven marks all render the SAME card, so the map answers "what is this tick?" with
//     "the same as its neighbour". Pinned directly: the cards of one turn's ticks are DISTINCT.
//
// Real timers on purpose. The open delay is the product behaviour under test (a pointer crossing
// the rail must not strobe a card per tick), so the test waits it out rather than faking it away.

const USER_TS = '2026-09-16T10:00:00.000Z'
const ASST_TS = '2026-09-16T10:00:05.000Z'

const assistantSegments: Segment[] = [
  { kind: 'text', text: 'Build finished in 4.1s with no errors.' },
  { kind: 'tool', id: 't-ok', tool: 'Terminal', detail: 'npm run build', done: true },
  { kind: 'error', text: 'ValidationException: input is too long for the model.' },
]

const fixtureTurns: ChatTurn[] = [
  { role: 'user', ts: USER_TS, visibleIndex: 0, segments: [{ kind: 'text', text: 'Please **run** the build.' }] },
  { role: 'assistant', ts: ASST_TS, visibleIndex: 1, segments: assistantSegments },
]

// user, assistant, tool, error — four marks over two turns.
const marks = () => sessionMapMarks(fixtureTurns)

const ticks = (c: HTMLElement) => [...c.querySelectorAll('[data-session-mark]')] as HTMLElement[]
const card = () => document.querySelector('[data-session-map-card]') as HTMLElement | null
const classes = (el: HTMLElement) => el.className.split(/\s+/).filter(Boolean)

/** Hover a tick and wait out the open delay. */
async function hover(tick: HTMLElement) {
  fireEvent.mouseOver(tick)
  await waitFor(() => expect(card()).not.toBeNull())
  return card()!
}

describe('sessionMapCardContent — the exchange behind a mark (pure)', () => {
  const m = marks()

  it('reads the role label off the mark, in the transcript’s own vocabulary', () => {
    expect(sessionMapCardContent(m, 0).roleLabel).toBe('You')
    expect(sessionMapCardContent(m, 1).roleLabel).toBe('Assistant')
    // A sub-event inherits the owning turn's side — a tool call is the agent acting, not the user.
    expect(sessionMapCardContent(m, 2).roleLabel).toBe('Assistant')
  })

  it('carries the owning turn’s timestamp verbatim, sub-events included', () => {
    expect(sessionMapCardContent(m, 0).ts).toBe(USER_TS)
    for (const i of [1, 2, 3]) expect(sessionMapCardContent(m, i).ts).toBe(ASST_TS)
  })

  it('the request is the nearest prompt AT OR BEFORE the mark', () => {
    // Markdown stripped by SSM-1's `previewText`, so the card never renders `**run**`.
    for (const i of [0, 1, 2, 3]) {
      expect(sessionMapCardContent(m, i).request).toBe('Please run the build.')
    }
  })

  it('the response is the reply for a prompt, and the mark ITSELF for anything else', () => {
    expect(sessionMapCardContent(m, 0).response).toBe('Build finished in 4.1s with no errors.')
    expect(sessionMapCardContent(m, 1).response).toBe('Build finished in 4.1s with no errors.')
    expect(sessionMapCardContent(m, 2).response).toBe('Terminal — npm run build')
    expect(sessionMapCardContent(m, 3).response).toBe('ValidationException: input is too long for the model.')
  })

  it('🪤 so one turn’s ticks do NOT all render the same card', () => {
    // marks 1..3 all share turn 2 (visibleIndex 1, one ts, one request). Without the rule above
    // they would be three identical cards.
    const shown = [1, 2, 3].map((i) => sessionMapCardContent(m, i).response)
    expect(new Set(shown).size, `three ticks, ${new Set(shown).size} distinct cards`).toBe(3)
  })

  it('counts TURNS, not marks — seven ticks share one coordinate', () => {
    const c = sessionMapCardContent(m, 3)
    expect(c.turnTotal).toBe(2)          // two turns, four marks
    expect(c.turnPosition).toBe(2)       // the error tick belongs to turn 2
    expect(sessionMapCardContent(m, 0).turnPosition).toBe(1)
  })

  it('says nothing rather than inventing a half of the exchange it does not have', () => {
    // An agent-initiated session (no prompt) and a prompt still streaming (no reply).
    const agentFirst = sessionMapMarks([fixtureTurns[1]])
    expect(sessionMapCardContent(agentFirst, 0).request).toBe('')
    const unanswered = sessionMapMarks([fixtureTurns[0], { role: 'user', ts: USER_TS, visibleIndex: 1, segments: [{ kind: 'text', text: 'and again' }] }])
    expect(sessionMapCardContent(unanswered, 1).response).toBe('')
  })
})

describe('sessionMapMarkName — short, distinguishing, bounded (§A.6)', () => {
  const m = marks()

  it('is the turn coordinate plus the mark’s OWN subject', () => {
    expect(sessionMapMarkName(m, 0)).toBe('Turn 1 of 2: Please run the build.')
    expect(sessionMapMarkName(m, 2)).toBe('Turn 2 of 2: Terminal — npm run build')
  })

  it('gives one turn’s ticks DISTINCT names', () => {
    // The ×83 duplicate-name defect `computedNames.test.tsx` measured, in rail form: naming from
    // the exchange would give marks 1-3 one name.
    const names = [1, 2, 3].map((i) => sessionMapMarkName(m, i))
    expect(new Set(names).size).toBe(3)
  })

  it('is never the full turn text', () => {
    const long = 'x'.repeat(400)
    const m2 = sessionMapMarks([{ role: 'user', ts: USER_TS, visibleIndex: 0, segments: [{ kind: 'text', text: long }] },
      fixtureTurns[1]])
    const name = sessionMapMarkName(m2, 0)
    expect(name.length).toBeLessThan(80)   // the app-wide interactive-name budget
    expect(name).toMatch(/…$/)
  })
})

describe('the card renders the four things KiroCrew’s does not (§A.3)', () => {
  const m = marks()
  const noop = () => {}

  it('a role label and a REAL <time> for the turn’s stamp', () => {
    render(<SessionMapCard marks={m} index={2} onMouseEnter={noop} onMouseLeave={noop} />)
    expect(screen.getByText('Assistant')).toBeInTheDocument()
    const t = screen.getByText(clockTime(ASST_TS))
    expect(t.tagName).toBe('TIME')
    expect(t.getAttribute('dateTime')).toBe(isoStamp(ASST_TS))
    expect(t.getAttribute('title')).toBe(fullStamp(ASST_TS))
  })

  it('the REQUEST line is on-surface ink — explicitly NOT the dimmed metadata ramp', () => {
    const { container } = render(<SessionMapCard marks={m} index={2} onMouseEnter={noop} onMouseLeave={noop} />)
    const req = container.querySelector('[data-session-map-request]') as HTMLElement
    expect(req).not.toBeNull()
    expect(req.textContent).toBe('Please run the build.')
    // Whole class tokens: `text-on-surface` is a substring of `text-on-surface-low`, so a
    // substring match here would pass on the exact defect this clause forbids.
    expect(classes(req)).toContain('text-on-surface')
    expect(classes(req), 'the flagged low-contrast metadata treatment').not.toContain('text-on-surface-low')
    expect(classes(req)).toContain('line-clamp-2')   // truncated, not a wall of text
  })

  it('the clock IS allowed the dimmed ramp — it is chrome, not the sentence you came to read', () => {
    const { container } = render(<SessionMapCard marks={m} index={2} onMouseEnter={noop} onMouseLeave={noop} />)
    const t = container.querySelector('time') as HTMLElement
    expect(classes(t)).toContain('text-on-surface-low')
    expect(t.getAttribute('data-type')).toBe('caption')
  })

  it('a RESPONSE excerpt under a hairline divider, in the variant ink', () => {
    const { container } = render(<SessionMapCard marks={m} index={0} onMouseEnter={noop} onMouseLeave={noop} />)
    const res = container.querySelector('[data-session-map-response]') as HTMLElement
    expect(res.textContent).toBe('Build finished in 4.1s with no errors.')
    expect(classes(res)).toContain('text-on-surface-var')
    expect(classes(res)).toContain('border-t')
    expect(classes(res)).toContain('border-outline-variant')
  })

  it('truncates a long request rather than rendering the turn', () => {
    const long = `Please ${'refactor the loader '.repeat(30)}today.`
    const m2 = sessionMapMarks([{ role: 'user', ts: USER_TS, visibleIndex: 0, segments: [{ kind: 'text', text: long }] },
      fixtureTurns[1]])
    const { container } = render(<SessionMapCard marks={m2} index={0} onMouseEnter={noop} onMouseLeave={noop} />)
    const req = container.querySelector('[data-session-map-request]') as HTMLElement
    expect(req.textContent!.length).toBeLessThanOrEqual(140)   // SSM-1's preview cap
    expect(req.textContent).toMatch(/…$/)
  })

  it('renders no clock at all when the turn carries no stamp', () => {
    // Every live-built assistant turn today: `assistantTurn()` never stamps a `ts` (plan SSM-2
    // DEVIATION). A card must show no time rather than "Invalid Date".
    const m2 = sessionMapMarks([{ role: 'user', ts: '', visibleIndex: 0, segments: [{ kind: 'text', text: 'hi' }] },
      { role: 'assistant', ts: '', visibleIndex: 1, segments: [{ kind: 'text', text: 'hello' }] }])
    const { container } = render(<SessionMapCard marks={m2} index={0} onMouseEnter={noop} onMouseLeave={noop} />)
    expect(container.querySelectorAll('time')).toHaveLength(0)
    expect(container.textContent).not.toMatch(/NaN|Invalid/)
  })
})

describe('the rail opens the card on HOVER and not on FOCUS', () => {
  it('a mark is a real, named, focusable control — one tab stop for the whole rail', () => {
    const m = marks()
    const { container } = render(<SessionMapRail marks={m} />)
    const t = ticks(container)
    expect(t).toHaveLength(m.length)
    for (const el of t) expect(el.tagName).toBe('BUTTON')
    // Named, so a mark is not announced as bare "button" (axe button-name).
    t.forEach((el, i) => expect(el.getAttribute('aria-label')).toBe(sessionMapMarkName(m, i)))
    // ONE tab stop: the current region. Four ticks would otherwise be four stops on the way to
    // the composer.
    expect(t.filter((el) => el.tabIndex === 0)).toHaveLength(1)
    expect(t.find((el) => el.tabIndex === 0)!.getAttribute('data-current')).toBe('true')
  })

  it('hover opens a Popover card, absent before and gone after', async () => {
    const { container } = render(<SessionMapRail marks={marks()} />)
    const tick = ticks(container)[2]                       // the tool mark
    expect(card(), 'the card must not be mounted at rest').toBeNull()

    const open = await hover(tick)
    expect(open.textContent).toContain('Terminal — npm run build')
    // It is the SHARED overlay primitive's flyout — frosted material, menu layer, radius (§A.3).
    const flyout = open.closest('.glass') as HTMLElement
    expect(flyout, 'the card must ride ui/Popover, not a bespoke floating div').not.toBeNull()
    expect(flyout.className).toContain('z-[var(--z-menu)]')
    expect(flyout.className).toContain('rounded-lgi')

    fireEvent.mouseOut(tick, { relatedTarget: document.body })
    await waitFor(() => expect(card()).toBeNull())
  })

  it('each tick opens its OWN card', async () => {
    const { container } = render(<SessionMapRail marks={marks()} />)
    const t = ticks(container)
    expect((await hover(t[0])).textContent).toContain('Build finished in 4.1s')
    fireEvent.mouseOut(t[0], { relatedTarget: document.body })
    await waitFor(() => expect(card()).toBeNull())
    expect((await hover(t[3])).textContent).toContain('ValidationException')
  })

  it('🔑 FOCUS ALONE DOES NOT OPEN IT — Tab-through must not strobe a card per tick', async () => {
    const { container } = render(<SessionMapRail marks={marks()} />)
    const tick = ticks(container).find((el) => el.tabIndex === 0)!
    act(() => tick.focus())
    // The focus target is REAL — this clause is worthless if nothing can be focused.
    expect(document.activeElement).toBe(tick)
    expect(card()).toBeNull()
    // And it stays closed past the pointer's own open delay, so this is not just "not yet".
    await act(() => new Promise((r) => { window.setTimeout(r, 250) }))
    expect(card(), 'focus opened the card after the hover delay elapsed').toBeNull()
    // The same element DOES open it on hover, so the negative above is about focus, not a card
    // that never opens.
    expect(document.activeElement).toBe(tick)
    expect(await hover(tick)).not.toBeNull()
  })

  it('the pointer can cross the gap onto the card without dismissing it', async () => {
    const { container } = render(<SessionMapRail marks={marks()} />)
    const tick = ticks(container)[2]
    const open = await hover(tick)
    // Leaving the 4px tick arms the close; arriving on the card cancels it (the §A.3 bridge).
    fireEvent.mouseOut(tick, { relatedTarget: open })
    fireEvent.mouseOver(open)
    await act(() => new Promise((r) => { window.setTimeout(r, 250) }))
    expect(card(), 'the card dismissed itself while the pointer was on it').not.toBeNull()
    // Leaving the card itself does dismiss it.
    fireEvent.mouseOut(open, { relatedTarget: document.body })
    await waitFor(() => expect(card()).toBeNull())
  })
})
