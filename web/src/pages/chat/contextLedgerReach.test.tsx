import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ContextLedger } from './ContextLedger'

// ── LV-2 — "a visible learned-chip whose tap lands on the right approve/edit surface" ─────
//
// OWNER RULING (2026-08-26): the link must be reachable WITHOUT the user first opening the
// collapsed disclosure themselves. A learning you have to go looking for is not visible, and
// visibility is this plan's whole subject — a chip pointing at something behind a closed
// disclosure moves the work from "hidden" to "hinted", which is not what the criterion asks.
//
// So the contract under test is ONE ACTION with TWO HALVES:
//   A. the tap OPENS the ledger disclosure, and
//   B. the same tap brings the approve/edit link INTO VIEW and puts FOCUS on it —
//      because a keyboard user who taps the chip must land there too, not merely be able to
//      Tab towards it an unknown number of stops later.
//
// Asserted at the CALL SITE — this renders the real `ContextLedger` with its real click
// handler and real effect, and taps the chip the way a user does. It does NOT assert that a
// handler exists. `ChatPage.tsx` is ~4k lines, owns a socket and a composer, and is not
// mountable here; that is exactly why `ContextLedger` is its own module. The complementary
// rail that the page still RENDERS this component lives in `skillsUsedChip.test.ts`.
//
// Halves A and B are separate `it()` blocks on purpose: removing the focus/scroll half must
// red B alone and leave A green. (The converse cannot hold — with the disclosure wired shut
// there is no link in the DOM to focus — so B failing alongside A is the honest floor, and A
// names which half broke.)

// jsdom implements no `scrollIntoView`, so the stub IS the probe: it records the element it
// was invoked ON, which is the only way to tell "scrolled the right thing into view" from
// "scrolled something".
let scrolled: Element[] = []
const NO_SCROLL = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollIntoView')

beforeEach(() => {
  scrolled = []
  Element.prototype.scrollIntoView = function (this: Element) { scrolled.push(this) }
})
afterEach(() => {
  if (NO_SCROLL) Object.defineProperty(Element.prototype, 'scrollIntoView', NO_SCROLL)
  else delete (Element.prototype as unknown as Record<string, unknown>).scrollIntoView
})

/** A turn that fed context, captured a skill proposal, and reported telemetry — the shape
 *  that puts all three ledger rows on screen, so the learned row is NOT the first child and
 *  reaching it is a real navigation rather than a coincidence of ordering. */
const ledger = (over: Partial<Parameters<typeof ContextLedger>[0]> = {}) =>
  render(
    <ContextLedger
      fed="Recalled relevant context · 1,204 chars"
      learned="Learned: prefers tabs over spaces"
      learnedOrigin="proposal"
      stats="12.4s · 3,102 tokens"
      {...over}
    />,
  )

/** The collapsed chip is the ledger's only button; addressed by role, as a user would. */
const chip = () => screen.getByRole('button')
const approveLink = () => screen.getByRole('link', { name: /Review in Skill proposals/ })

describe('LV-2 — the learned chip reaches the approve/edit surface in ONE action', () => {
  it('VACUITY FLOOR — the target really is behind a CLOSED disclosure at first paint', () => {
    // Without this leg every assertion below is satisfiable by a ledger that renders expanded,
    // in which case "the tap opened it" proves nothing at all.
    ledger()
    expect(chip().getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('link')).toBeNull()
    expect(screen.queryByText(/prefers tabs over spaces/)).toBeNull()
    expect(scrolled).toHaveLength(0)
    // …and the collapsed chip does carry the learned signal, so this IS the learned chip.
    expect(chip().textContent).toContain('learned 1')
  })

  it('HALF A — one tap opens the disclosure and renders the right approve/edit link', () => {
    ledger()
    fireEvent.click(chip())
    expect(chip().getAttribute('aria-expanded')).toBe('true')
    // The RIGHT surface: a skill proposal is approved on the Skills page's proposals view,
    // not in the Memory studio.
    expect(approveLink().getAttribute('href')).toBe('#/skills?mode=proposals')
  })

  it('HALF B — the same tap brings that link into view and puts FOCUS on it', () => {
    ledger()
    const trigger = chip()
    trigger.focus() // a keyboard user tabbed to the chip and pressed Enter
    fireEvent.click(trigger)
    const link = approveLink()
    // Brought into view — and onto the LINK, not some ancestor that happens to scroll.
    expect(scrolled).toContain(link)
    // Focusable AND focused: a keyboard user lands on the approve surface, they do not merely
    // have it rendered somewhere below them.
    expect(document.activeElement).toBe(link)
    expect(link.tagName).toBe('A')
  })

  it('a lesson tap lands in the Memory studio instead — the routing is not one constant', () => {
    ledger({ learnedOrigin: 'lesson', learned: 'Learned: keep replies short' })
    fireEvent.click(chip())
    const link = screen.getByRole('link', { name: /Review lessons in Memory/ })
    expect(link.getAttribute('href')).toBe('#/settings/memory?tab=studio')
    expect(document.activeElement).toBe(link)
    expect(scrolled).toContain(link)
  })

  it('DEGRADE — an unknown origin still opens, and steals neither focus nor scroll', () => {
    // Every message persisted before T2.2 arrives without an origin, and so will anything a
    // future emitter adds. There is no surface to land on, so nothing may move: guessing one
    // would send the user where the artifact is not.
    ledger({ learnedOrigin: 'sop' })
    const trigger = chip()
    trigger.focus()
    fireEvent.click(trigger)
    expect(trigger.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByText(/prefers tabs over spaces/)).toBeTruthy() // the learning is still SHOWN
    expect(screen.queryByRole('link')).toBeNull()
    expect(document.activeElement).toBe(trigger) // focus stayed where the user put it
    expect(scrolled).toHaveLength(0)
  })

  it('a turn that learned nothing moves no focus (the effect is gated, not unconditional)', () => {
    ledger({ learned: undefined, learnedOrigin: undefined })
    const trigger = chip()
    trigger.focus()
    fireEvent.click(trigger)
    expect(trigger.getAttribute('aria-expanded')).toBe('true')
    expect(document.activeElement).toBe(trigger)
    expect(scrolled).toHaveLength(0)
  })

  it('collapsing again does not re-reach anything (the effect is gated on open)', () => {
    ledger()
    fireEvent.click(chip())
    expect(document.activeElement).toBe(approveLink())
    expect(scrolled).toHaveLength(1)
    fireEvent.click(chip())
    expect(chip().getAttribute('aria-expanded')).toBe('false')
    // The row stays mounted for a beat while the disclosure's exit animation runs, so the
    // property that matters on close is that nothing was scrolled or focused a SECOND time —
    // not that the node vanished synchronously.
    expect(scrolled).toHaveLength(1)
  })

  it('the hover text says what the tap does, and only when there is somewhere to land', () => {
    // `title` is a hover affordance, never the accessible name here — the chip's name is its
    // visible text ("…learned 1…"), which the vacuity leg above asserts.
    const withSurface = ledger()
    expect(chip().getAttribute('title')).toContain('jumps to where you can review it')
    withSurface.unmount()
    ledger({ learnedOrigin: 'sop' })
    expect(chip().getAttribute('title')).not.toContain('jumps to')
  })
})
