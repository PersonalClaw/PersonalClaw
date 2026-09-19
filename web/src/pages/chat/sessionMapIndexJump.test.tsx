import { describe, it, expect, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChatActivityPanel } from './ChatActivityPanel'
import { SessionMapRail } from './SessionMapRail'
import { hydrateTurns, deriveActivity, markCoordOf, type HistMsg } from './chatTypes'
import { sessionMapMarks } from './sessionMap'

// ── SSM-12 — THE INDEX TAB'S JUMP IS THE MAP'S JUMP ─────────────────────────────────────────
//
// The atom's clause: "Index tab `onJumpTo` reused by the rail; test confirms a rail jump and the
// former Index behavior invoke the same handler with the same visibleIndex."
//
// Two halves, and they fail in different places, so they are asserted separately:
//
//   1. BEHAVIOUR — hand the two real surfaces ONE handler and drive both. One spy receiving two
//      calls IS the "same handler" claim: there is a single function object, and both the rail's
//      tick and the Index tab's anchor reached it with the same argument. `describe` #1.
//   2. WIRING — that `ChatPage` actually hands them one handler rather than two that happen to
//      behave alike. Half 1 supplies the shared spy itself, so on its own it would pass against a
//      page that gave each surface its own scroll implementation. `describe` #2 reads
//      `ChatPage.tsx` and asserts the `onJumpTo` expression on the two call sites is the SAME bare
//      identifier, resolving to exactly one definition.
//
// 🔴 WHY THE FIXTURE IS NOT HAND-WRITTEN. "The same visibleIndex" is trivially true on any
// transcript where a turn's coordinate equals its array position — and every hand-written Session
// Map fixture in this directory is one, because each turn literally sets `visibleIndex: i`. On a
// real tool-using transcript `hydrateTurns` COLLAPSES consecutive assistant messages and the two
// diverge; that divergence is the whole reason SSM-12's clause says "the same visibleIndex"
// instead of "both jump somewhere". So the turns below are produced by running the real
// `hydrateTurns` over a real message list that collapses, and the non-vacuity floor is asserted
// first: the target turn's coordinate must NOT be its array position. A fixture that cannot fail
// this test is how the coordinate defect survived six confirmed atoms (see `sessionMapCoord.test.ts`).
//
// WHAT THIS FILE DOES NOT OWN. The jump's SCROLL is `ChatPage.jumpToTurn` reading `turnNodes`, and
// jsdom computes no layout — `e2e/sessionMap.spec.ts` drives the real scroll in a browser. Here
// the claim is handler identity and coordinate identity, both of which are honest under jsdom.

/** A transcript that COLLAPSES: the two consecutive assistant messages merge into one turn, so
 *  from turn 2 onward the backend's visible-list cursor runs ahead of the rendered turn array. */
const COLLAPSING: HistMsg[] = [
  { role: 'user', content: 'run the build' },
  { role: 'assistant', content: 'starting' },
  { role: 'assistant', content: 'done — 0 errors' },
  { role: 'user', content: 'now the tests' },
  { role: 'assistant', content: 'all green' },
]

const TURNS = hydrateTurns(COLLAPSING)
const ACTIVITY = deriveActivity(TURNS)
const MARKS = sessionMapMarks(TURNS)

const railProps = { turnNodes: new Map<number, Element>(), scrollRef: { current: null } }

describe('SSM-12 — the rail jump and the former Index behaviour are one navigation', () => {
  it('🔴 the fixture DIVERGES, so "the same visibleIndex" is a falsifiable claim here', () => {
    // Four turns for five messages, and the second user turn sits at array position 2 while its
    // coordinate is 3. Without this the test below passes on any two surfaces that both return a
    // list position.
    expect(TURNS).toHaveLength(4)
    const secondUserAt = 2
    expect(TURNS[secondUserAt].role).toBe('user')
    expect(markCoordOf(TURNS[secondUserAt], secondUserAt)).not.toBe(secondUserAt)
    // Both surfaces are non-empty, and the rail is above its self-suppression threshold.
    expect(ACTIVITY.index).toHaveLength(2)
    expect(MARKS.length).toBeGreaterThan(1)
  })

  it('🔑 ONE handler receives BOTH jumps, with the SAME visibleIndex', async () => {
    // `ChatPage`'s `jumpToTurn`, stood in for by a single spy — the same reference the page hands
    // to `<SessionMapRail>` and to `<ChatActivityPanel>` (asserted from source in the next block).
    const jumpToTurn = vi.fn()

    // The target: the SECOND user turn, the one whose coordinate is not its array position.
    const target = ACTIVITY.index[1]
    expect(target.label).toBe('now the tests')

    // ── the FORMER INDEX BEHAVIOUR — an Activity Index anchor ───────────────────────────────
    // `index` is the panel's default tab, so the anchors are on screen without a tab click.
    const panel = render(
      <ChatActivityPanel activity={ACTIVITY} onJumpTo={jumpToTurn} onOpenFile={() => {}} />,
    )
    await userEvent.click(panel.getByTitle(target.label))
    expect(jumpToTurn, 'clicking an Activity Index anchor reached no jump handler').toHaveBeenCalledTimes(1)
    const fromIndex = jumpToTurn.mock.calls[0]
    panel.unmount()

    // ── the RAIL JUMP — the map tick for that same turn ────────────────────────────────────
    const markIndex = MARKS.findIndex((m) => m.kind === 'user' && m.visibleIndex === target.visibleIndex)
    expect(markIndex, 'no user mark carries the Index anchor\'s coordinate — the two surfaces already disagree').toBeGreaterThan(-1)
    const rail = render(<SessionMapRail marks={MARKS} {...railProps} onJumpTo={jumpToTurn} />)
    const ticks = rail.container.querySelectorAll('[data-session-mark]')
    expect(ticks).toHaveLength(MARKS.length)
    await userEvent.click(ticks[markIndex])

    // ── THE CLAUSE ─────────────────────────────────────────────────────────────────────────
    // Two calls on ONE spy: a single function object served both paths. And the arguments are
    // equal, so it is the same turn — not merely "both jumped somewhere".
    expect(jumpToTurn, 'the rail tick reached no jump handler').toHaveBeenCalledTimes(2)
    const fromRail = jumpToTurn.mock.calls[1]
    expect(fromRail, `the rail jumped to ${fromRail[0]} and the Index anchor to ${fromIndex[0]} for the SAME turn`).toEqual(fromIndex)
    expect(fromIndex[0]).toBe(target.visibleIndex)
    // …and the shared value is the map coordinate, not the array position the old registry used.
    expect(fromIndex[0]).not.toBe(2)
  })

  it('🪤 the Index tab owns NO jump implementation of its own — there is nothing to fall back to', () => {
    // The clause is "reused", not "also available". If `ChatActivityPanel` grew a `scrollIntoView`
    // the two surfaces could pass the test above and still be two navigations at runtime.
    const src = readFileSync(join(process.cwd(), 'src/pages/chat/ChatActivityPanel.tsx'), 'utf8')
    expect(src.length).toBeGreaterThan(0)
    expect(src).not.toMatch(/scrollIntoView/)
    // Its only jump path is the injected prop.
    expect(src).toMatch(/onJumpTo\(e\.visibleIndex\)/)
  })
})

// ── The wiring half: ChatPage hands the two surfaces ONE handler ────────────────────────────
//
// `ChatPage.tsx` is ~4k lines, owns a socket and a composer and is not mountable under jsdom (the
// reason `contextLedgerReach.test.tsx` states in full, and why SSM-11's layout clauses live in
// `e2e/sessionMap.spec.ts`). Handler IDENTITY, though, is a property of the source text and not of
// the layout, so it is readable here — and a rendered page could not assert it any better, since
// two distinct closures that both scroll correctly are observationally identical in a browser.

const CHAT_PAGE = join(process.cwd(), 'src/pages/ChatPage.tsx')

/** The `onJumpTo={…}` expression the named JSX element is given, read out of ChatPage's source.
 *  Returns the raw expression so the caller can insist it is a bare identifier. */
function onJumpToExprOf(src: string, element: string): string {
  const open = src.indexOf(`<${element} `)
  expect(open, `no <${element}> call site in ChatPage.tsx — this rail would be vacuous`).toBeGreaterThan(-1)
  const close = src.indexOf('/>', open)
  expect(close, `<${element}> is not self-closing in ChatPage.tsx — widen this reader`).toBeGreaterThan(open)
  const tag = src.slice(open, close)
  const m = tag.match(/onJumpTo=\{([^}]*)\}/)
  expect(m, `<${element}> passes no onJumpTo prop`).not.toBeNull()
  return m![1].trim()
}

describe('SSM-12 — ChatPage gives the rail and the Index tab the same handler', () => {
  const src = readFileSync(CHAT_PAGE, 'utf8')

  it('🔑 both call sites pass the SAME bare identifier', () => {
    expect(src.length, 'ChatPage.tsx read empty').toBeGreaterThan(1000)
    const railExpr = onJumpToExprOf(src, 'SessionMapRail')
    const indexExpr = onJumpToExprOf(src, 'ChatActivityPanel')
    // A bare identifier on both sides. An inline arrow would type-check and behave identically
    // while being a SECOND function — exactly the dual path the atom removes.
    for (const [who, expr] of [['SessionMapRail', railExpr], ['ChatActivityPanel', indexExpr]]) {
      expect(expr, `<${who}> passes an expression, not a shared handler: onJumpTo={${expr}}`).toMatch(/^[A-Za-z_$][\w$]*$/)
    }
    expect(
      indexExpr,
      `the Activity Index jumps through \`${indexExpr}\` and the rail through \`${railExpr}\` — SSM-12 is the atom that makes them one`,
    ).toBe(railExpr)
  })

  it('🔑 that identifier resolves to exactly ONE definition', () => {
    const name = onJumpToExprOf(src, 'SessionMapRail')
    const defs = src.match(new RegExp(`(?:function|const)\\s+${name}\\b`, 'g')) ?? []
    expect(defs, `expected one definition of \`${name}\` in ChatPage.tsx, found ${defs.length}`).toHaveLength(1)
  })

  it('🪤 the two surfaces do not re-derive the coordinate on the way in', () => {
    // Both props are the handler itself, so neither call site can adapt, offset or re-map the
    // coordinate before the shared implementation sees it. Asserted as the absence of a wrapper on
    // either side, which is what `toBe(railExpr)` above already proves — kept as a named claim
    // because "same handler" and "same visibleIndex" are two clauses, and this is the second one's
    // structural floor: an identical handler reached through `onJumpTo={(i) => jump(i + 1)}` would
    // satisfy neither.
    expect(onJumpToExprOf(src, 'SessionMapRail')).not.toMatch(/=>/)
    expect(onJumpToExprOf(src, 'ChatActivityPanel')).not.toMatch(/=>/)
  })
})
