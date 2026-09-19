import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { createRef } from 'react'
import { act, render, screen } from '@testing-library/react'
import { CommentLayer } from './CommentLayer'
import { commentStore } from './commentStore'
import { installFakeDocCommentServer, type FakeDocCommentServer } from './fakeDocCommentServer'

// ── Issue 632: the collapsed deck showed a comment as if it belonged to whatever ──
// document was open, with neither of the two honesty mechanisms the expanded deck
// already has.
//
// The collapsed front card used to be `ordered[0]` — the newest comment across ALL
// documents — labelled with ITS OWN document, undimmed, no matter which document
// was actually on screen. Driven: leave a comment on doc A, open unrelated doc B —
// the collapsed deck showed A's comment at full contrast with no "across N
// documents" disclosure, while the expanded branch (`:176`/`:182`) already computes
// exactly that disclosure and dims a foreign card with `muted`.
//
// Fixed by preferring the OPEN document's own newest comment as the collapsed front
// card (falling back to the newest overall only when this document has none),
// dimming that fallback card the same way the expanded deck dims a foreign one, and
// showing "across N documents" once more than one is represented — matching the
// suggested shape in the issue rather than inventing a third convention.

/** Mount `CommentLayer` over a real (non-scrolling) host so `DeckPortal`'s
 *  `scrollRef.current.parentElement` resolves synchronously, the way FindBar's own
 *  tests drive a ref-consuming component without a full page router. The deck
 *  itself portals into that parent (`document.body` here), NOT into RTL's own
 *  `container`, so callers query with `screen` (document-wide), not `within`. */
async function mount(docId: string, docLabel: string) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const scrollRef = createRef<HTMLElement>() as React.MutableRefObject<HTMLElement | null>
  scrollRef.current = host
  render(
    <CommentLayer scrollRef={scrollRef} docId={docId} docLabel={docLabel} onSubmit={() => {}} />,
  )
  // Mounting SUBSCRIBES, which hydrates the deck from the server (#429) — an async store
  // read that lands after `render` returns. Flushing it here is what keeps the assertions
  // looking at a settled deck instead of racing the fetch.
  await act(async () => {})
}

// The deck reads a SERVER store now (#429), so each test gets its own in-memory one.
let server: FakeDocCommentServer
beforeEach(async () => { server = installFakeDocCommentServer(); await commentStore.clear() })
// Only `restore()` here: clearing the store in `afterEach` emits to a component RTL has not
// unmounted yet, which is an un-acted React update. `beforeEach` already starts each test on a
// fresh in-memory server and an empty deck.
afterEach(() => { server.restore() })

describe('the collapsed deck names and dims a comment that belongs elsewhere', () => {
  it('labels the foreign document and dims the card when the open document has no comment of its own', async () => {
    await commentStore.add({
      docId: 'hoffmann-rates.md', docLabel: 'hoffmann-rates.md',
      quote: 'Torque tables are IN scope', comment: 'Needs updating before I invoice',
    })
    await mount('bus-tier.md', 'bus-tier.md')
    // The card names the comment's OWN document, not the one on screen — the
    // repro from the issue (a translation-rates comment surfacing on a bus-
    // scheduling report) must still show whose comment it actually is.
    expect(screen.getByText('· hoffmann-rates.md')).toBeInTheDocument()
    // …and carries the SAME muted treatment CommentCard gives a foreign card in
    // the expanded branch, so it does not read as belonging to the open document.
    expect(document.body.querySelector('.opacity-65'), 'the foreign front card must be dimmed').toBeTruthy()
  })

  it('prefers the open document\'s own comment over a newer foreign one, and does not dim it', async () => {
    await commentStore.add({ docId: 'a.md', docLabel: 'a.md', quote: 'x', comment: 'older, but on the open doc' })
    await commentStore.add({ docId: 'b.md', docLabel: 'b.md', quote: 'y', comment: 'newer, but foreign' })
    await mount('a.md', 'a.md')
    expect(screen.getByText('older, but on the open doc')).toBeInTheDocument()
    expect(document.body.querySelector('.opacity-65')).toBeNull()
    // Two documents ARE represented in the deck — that must stay disclosed even
    // though the front card itself belongs to the open document.
    expect(screen.getByText('across 2 documents')).toBeInTheDocument()
  })

  it('keeps the single-document label instead of the disclosure when only one document has comments', async () => {
    await commentStore.add({ docId: 'a.md', docLabel: 'a.md', quote: 'x', comment: 'on a' })
    await mount('a.md', 'a.md')
    expect(screen.getByText('· a.md')).toBeInTheDocument()
    expect(screen.queryByText(/across/)).toBeNull()
  })
})
