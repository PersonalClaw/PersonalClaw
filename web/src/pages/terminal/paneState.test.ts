/** Regression (#645): closing the ACTIVE session while a split is open must not
 *  leave `active` and `split` naming the SAME session. It used to, because the
 *  promote-on-close picked the last remaining tab unconditionally and the only
 *  split guard covered the case where the SPLIT pane closed. The collapsed pair
 *  renders ONE pane (the render tests `active` first, so the doubled session
 *  resolves to 'left' only) under a toolbar still offering "Close split" — silent,
 *  zero console output, recoverable only by clicking "Close split".
 *
 *  Measured in a browser, with the URL as the witness:
 *    before  ?active=f224c6b61802&split=781be9c1729c   panes=2  "Close split"
 *    after   ?active=781be9c1729c&split=781be9c1729c   panes=1  "Close split"
 *
 *  Both panes are URL-backed (?active/?split), so each case below asserts on the
 *  pair the page writes as one query patch — coherent URL, not merely coherent
 *  React state. */
import { describe, it, expect } from 'vitest'
import { panesAfterClose, type PaneSelection } from './paneState'

/** Drop `closed` from `tabs` the way closeSession does, then resolve the panes. */
function close(tabs: readonly string[], closed: string, panes: PaneSelection) {
  return panesAfterClose(tabs.filter((id) => id !== closed), closed, panes)
}

describe('panesAfterClose', () => {
  it('clears the split when the last remaining tab IS the split (the #645 repro)', () => {
    // two sessions, both displayed: active left, split right.
    expect(close(['f224c6b61802', '781be9c1729c'], 'f224c6b61802', {
      active: 'f224c6b61802', split: '781be9c1729c',
    })).toEqual({ active: '781be9c1729c', split: null })
  })

  it('keeps the split when a distinct session can still fill the right pane', () => {
    // three sessions, active=c, split=b. Closing c must NOT drop the split: a is
    // free to take the left pane, so the user keeps the pane they parked at b.
    // This is the case a `remaining.length < 2` guard would silently break.
    expect(close(['a', 'b', 'c'], 'c', { active: 'c', split: 'b' }))
      .toEqual({ active: 'a', split: 'b' })
  })

  it('never promotes into the split, even when the split is the last tab in order', () => {
    // active=a, split=c, and c sorts last — a naive "promote the last remaining
    // tab" lands straight on the split. b is the honest promotion.
    expect(close(['a', 'b', 'c'], 'a', { active: 'a', split: 'c' }))
      .toEqual({ active: 'b', split: 'c' })
  })

  it('closes the split pane itself without disturbing the active pane', () => {
    expect(close(['a', 'b'], 'b', { active: 'a', split: 'b' }))
      .toEqual({ active: 'a', split: null })
  })

  it('leaves both panes alone when a hidden tab closes', () => {
    expect(close(['a', 'b', 'c'], 'c', { active: 'a', split: 'b' }))
      .toEqual({ active: 'a', split: 'b' })
  })

  it('promotes the last remaining tab when no split is open', () => {
    expect(close(['a', 'b', 'c'], 'a', { active: 'a', split: null }))
      .toEqual({ active: 'c', split: null })
  })

  it('empties both panes when the only session closes', () => {
    // '' + null is what the page patches out of the URL entirely, so the empty
    // state renders with a clean `#/terminal` rather than a dangling ?active=.
    expect(close(['a'], 'a', { active: 'a', split: null }))
      .toEqual({ active: '', split: null })
  })

  it('empties both panes when the split pane is the last one standing', () => {
    // active=a, split=b, and a is closed with b already gone from the strip (its
    // PTY died and the reaper removed it) — the fallback must not resurrect b.
    expect(panesAfterClose([], 'a', { active: 'a', split: 'b' }))
      .toEqual({ active: '', split: null })
  })

  it('is idempotent under a repeated close (a double-clicked chip)', () => {
    const once = close(['a', 'b'], 'a', { active: 'a', split: 'b' })
    expect(once).toEqual({ active: 'b', split: null })
    // the tab is already gone; resolving again must not move the panes.
    expect(close(['b'], 'a', once)).toEqual({ active: 'b', split: null })
  })
})
