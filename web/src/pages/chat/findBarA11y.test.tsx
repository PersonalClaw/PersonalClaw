import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createRef } from 'react'
import { render, within, fireEvent, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FindBar, findAnnouncement } from '../../ui/FindBar'
import { findSegments } from './findSegments'
import { FollowupChips, followupAnnouncement } from './FollowupChips'
import type { ChatTurn } from './chatTypes'

// ── CC-6: the find bar and the chips were POLISHED, not declared ────────────────────────
//
// The wrap-up atom's done_when asks for "FindBar/chips keyboard traversal + aria-live
// clean". Three things were measured wrong on arrival, and each one reads as fine from
// the markup:
//
//   1. `aria-live` EXISTED and said the wrong thing. The counter span carried
//      aria-live="polite" — so a rail that greps for the attribute passes — but its
//      content is the glyph `3/17`, which a screen reader reads as digits and a slash.
//      The zero case read `0/0`. axe cannot express "was the user told, in words?".
//   2. Escape was bound on the INPUT only. Tab moves through Previous / Next / Close
//      (IconButton keeps its tab stop, by design), and from all three of those stops
//      the one key a user presses to leave a transient bar did nothing.
//   3. Closing the bar dropped focus on <body>, because the bar autofocuses its input
//      and nothing handed focus back. The next Tab then restarted at the top of the
//      page — the transcript the user was reading became unreachable without a
//      full re-traverse.
//
// And the chips (S3) arrive from a WebSocket 1-3s after the reply completes: a purely
// visual change, with `role="group"` naming them only once you already found them.
//
// These tests DRIVE the interactions (keydown on real nodes, focus assertions against
// document.activeElement) rather than asserting an attribute is present.

const turnOf = (text: string): ChatTurn => ({ role: 'user', segments: [{ kind: 'text', text }] })

/** Mount the bar over a scroll container, with a real previously-focused element so
 *  focus RETURN is observable. Returns the render plus that element. */
function mountBar(text: string, onClose = () => {}) {
  const host = document.createElement('div')
  host.textContent = text
  document.body.appendChild(host)

  // The element that "had focus" before ⌘F — in the app this is the composer or the
  // transcript. A button is focusable in jsdom, which is all the restore path needs.
  const opener = document.createElement('button')
  opener.textContent = 'composer'
  document.body.appendChild(opener)
  opener.focus()
  expect(document.activeElement).toBe(opener)

  const scrollRef = createRef<HTMLDivElement>() as React.MutableRefObject<HTMLDivElement | null>
  scrollRef.current = host
  const utils = render(
    <FindBar items={[turnOf(text)]} segmentsOf={findSegments} nodeOf={() => null}
      scrollRef={scrollRef} label="Find in conversation" onClose={onClose} />,
  )
  return { ...utils, host, opener }
}

/** Type into the bar's field and wait past the 150ms scan debounce. */
async function typeQuery(container: HTMLElement, query: string) {
  const input = within(container).getByLabelText('Find in conversation')
  fireEvent.change(input, { target: { value: query } })
  await act(async () => { await new Promise((r) => setTimeout(r, 200)) })
  return input
}

/** The sr-only announcement the bar is currently making. `role="status"` distinguishes
 *  it from the aria-hidden glyph counter beside it. */
function announced(container: HTMLElement): string {
  return within(container).getByRole('status').textContent?.trim() ?? ''
}

describe('findAnnouncement — the wording, as a value', () => {
  it('is silent before a search (a live region must exist EMPTY, not absent)', () => {
    expect(findAnnouncement('', 0, 0)).toBe('')
    expect(findAnnouncement('   ', 0, 12)).toBe('')
  })

  it('words the zero case instead of "0/0"', () => {
    expect(findAnnouncement('nothing', 0, 0)).toBe('No matches')
  })

  it('words the position AND the total, so cycling re-announces', () => {
    expect(findAnnouncement('kiln', 0, 17)).toBe('Match 1 of 17')
    expect(findAnnouncement('kiln', 2, 17)).toBe('Match 3 of 17')
  })

  it('avoids the ResultAnnouncement noun trap ("No matching matches")', () => {
    // `ui/ListControls.tsx`'s primitive builds "No matching ${noun}". The honest noun
    // for find is "matches", which that template renders as a stutter. Guard the copy.
    expect(findAnnouncement('x', 0, 0)).not.toContain('matching matches')
  })
})

describe('FindBar aria-live is CONTENT, not an attribute', () => {
  afterEach(() => { document.body.innerHTML = '' })

  it('announces words when a query matches, and the glyph counter is hidden from AT', async () => {
    const { container } = mountBar('kiln target and target again')
    expect(announced(container)).toBe('')          // mounted, empty — the reliable shape

    await typeQuery(container, 'target')
    await waitFor(() => expect(announced(container)).toBe('Match 1 of 1'))

    // The `1/1` glyph is still on screen for sighted users, but aria-hidden so the
    // digits-and-slash form is not what gets read out.
    const glyph = container.querySelector('[aria-hidden="true"].tabular-nums')
    expect(glyph?.textContent).toBe('1/1')
  })

  it('announces "No matches" rather than "0/0"', async () => {
    const { container } = mountBar('kiln target')
    await typeQuery(container, 'zzzz')
    await waitFor(() => expect(announced(container)).toBe('No matches'))
    // The old behaviour: the live region's content WAS the string "0/0".
    expect(announced(container)).not.toBe('0/0')
  })

  it('re-announces the position when the user cycles matches', async () => {
    // Two turns so there are two distinct scroll targets to cycle between.
    const host = document.createElement('div')
    document.body.appendChild(host)
    const scrollRef = createRef<HTMLDivElement>() as React.MutableRefObject<HTMLDivElement | null>
    scrollRef.current = host
    const { container } = render(
      <FindBar items={[turnOf('target one'), turnOf('target two')]} segmentsOf={findSegments}
        nodeOf={() => null} scrollRef={scrollRef} label="Find in conversation" onClose={() => {}} />,
    )
    await typeQuery(container, 'target')
    await waitFor(() => expect(announced(container)).toBe('Match 1 of 2'))

    fireEvent.click(within(container).getByLabelText('Next match'))
    await waitFor(() => expect(announced(container)).toBe('Match 2 of 2'))
  })
})

describe('FindBar keyboard traversal is DRIVEN, not declared', () => {
  afterEach(() => { document.body.innerHTML = '' })

  it('reaches every control in visual order and leaves nothing unreachable', async () => {
    const { container } = mountBar('kiln target')
    // Four stops, in DOM order: the field, then prev / next / close. Asserted as an
    // ORDER, because a tabIndex present on each in isolation says nothing about it.
    const stops = Array.from(
      container.querySelectorAll<HTMLElement>('input, button:not([tabindex="-1"])'))
      .map((el) => el.getAttribute('aria-label') ?? el.tagName.toLowerCase())
    expect(stops).toEqual(['Find in conversation', 'Previous match', 'Next match', 'Close find'])
    for (const el of container.querySelectorAll<HTMLElement>('input, button')) {
      // Negative tabIndex would silently drop a stop out of the sequence above.
      expect(el.tabIndex).toBeGreaterThanOrEqual(0)
    }
  })

  it('↑ / ↓ cycle from the field instead of moving the caret', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const scrollRef = createRef<HTMLDivElement>() as React.MutableRefObject<HTMLDivElement | null>
    scrollRef.current = host
    const { container } = render(
      <FindBar items={[turnOf('target one'), turnOf('target two')]} segmentsOf={findSegments}
        nodeOf={() => null} scrollRef={scrollRef} label="Find in conversation" onClose={() => {}} />,
    )
    const input = await typeQuery(container, 'target')
    await waitFor(() => expect(announced(container)).toBe('Match 1 of 2'))

    fireEvent.keyDown(input, { key: 'ArrowDown' })
    await waitFor(() => expect(announced(container)).toBe('Match 2 of 2'))
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    await waitFor(() => expect(announced(container)).toBe('Match 1 of 2'))
  })

  it('Enter / Shift+Enter cycle from the field', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const scrollRef = createRef<HTMLDivElement>() as React.MutableRefObject<HTMLDivElement | null>
    scrollRef.current = host
    const { container } = render(
      <FindBar items={[turnOf('target one'), turnOf('target two')]} segmentsOf={findSegments}
        nodeOf={() => null} scrollRef={scrollRef} label="Find in conversation" onClose={() => {}} />,
    )
    const input = await typeQuery(container, 'target')
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(announced(container)).toBe('Match 2 of 2'))
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    await waitFor(() => expect(announced(container)).toBe('Match 1 of 2'))
  })

  it('Escape closes from EVERY tab stop, not just the field', async () => {
    for (const label of ['Find in conversation', 'Previous match', 'Next match', 'Close find']) {
      const onClose = vi.fn()
      const { container, unmount } = mountBar('kiln target', onClose)
      const el = within(container).getByLabelText(label)
      el.focus()
      fireEvent.keyDown(el, { key: 'Escape' })
      expect(onClose, `Escape from "${label}" did not close the bar`).toHaveBeenCalledTimes(1)
      unmount()
      document.body.innerHTML = ''
    }
  })

  it('hands focus back to whatever had it when the bar closes', async () => {
    const { unmount, opener } = mountBar('kiln target')
    // The bar autofocuses its own field, so focus has genuinely moved away first.
    await waitFor(() => expect(document.activeElement).not.toBe(opener))
    unmount()
    // Without the restore, activeElement is <body> here and the next Tab starts over
    // at the top of the page.
    expect(document.activeElement).toBe(opener)
  })
})

describe('FindBar docks on mobile (measured at the useIsMobile breakpoint)', () => {
  const ORIGINAL = window.matchMedia
  function setViewport(mobile: boolean) {
    // `useIsMobile` reads `matchMedia('(max-width: 768px)')`, not a real viewport —
    // jsdom has no layout, so the honest assertion here is on the query it asks and the
    // classes it picks. The GEOMETRY was measured in Chrome instead, both layouts at both
    // widths, and it is worth writing down because it corrected the claim this change was
    // first justified with:
    //   390px  w-fit → 344px wide, FITS (right 374 < 390), input 147px — not broken
    //   320px  w-fit → 344px wide, OVERFLOWS (right 344 > 320), left gutter eaten
    //          docked → 288px, fits, input 91px
    // i.e. the pill has a ~344px intrinsic floor. So this is a real fix below ~360px and
    // a 14px polish gain at 390px — not "the field gets squeezed out" at phone widths.
    Object.defineProperty(window, 'matchMedia', {
      configurable: true, writable: true,
      value: ((q: string) => ({
        matches: q === '(max-width: 768px)' ? mobile : false,
        media: q, onchange: null,
        addEventListener: () => {}, removeEventListener: () => {},
        addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
      })) as unknown as typeof window.matchMedia,
    })
  }
  beforeEach(() => { setViewport(false) })
  afterEach(() => {
    Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL })
    document.body.innerHTML = ''
  })

  it('is a right-aligned intrinsic-width pill on desktop', () => {
    setViewport(false)
    const { container } = mountBar('kiln')
    const bar = container.querySelector('[role="search"]')!
    expect(bar.className).toContain('w-fit')
    expect(bar.className).toContain('ml-auto')
    expect(bar.className).not.toContain('w-auto')
  })

  it('spans the column on mobile, so the pill cannot hang off a narrow viewport', () => {
    setViewport(true)
    const { container } = mountBar('kiln')
    const bar = container.querySelector('[role="search"]')!
    expect(bar.className).toContain('w-auto')
    expect(bar.className).not.toContain('w-fit')
    // Still docked under the header, still inside the gutter.
    expect(bar.className).toContain('sticky')
    expect(bar.className).toContain('mx-l')
  })
})

describe('followupAnnouncement — chips arrival is spoken, and dismissal clears it', () => {
  afterEach(() => { document.body.innerHTML = '' })

  it('is empty with no chips, so a dismissal does not leave a stale claim', () => {
    expect(followupAnnouncement(0)).toBe('')
    expect(followupAnnouncement(-1)).toBe('')
  })

  it('counts and singularises', () => {
    expect(followupAnnouncement(1)).toBe('1 follow-up suggestion available')
    expect(followupAnnouncement(3)).toBe('3 follow-up suggestions available')
  })

  it('the chips themselves still carry a group name for whoever reaches them', () => {
    const { container } = render(
      <FollowupChips items={['a', 'b']} onPick={() => {}} onSend={() => {}} />)
    const group = within(container).getByRole('group', { name: 'Suggested follow-ups' })
    // Both halves of each chip stay reachable: the label button and the send glyph.
    expect(within(group).getAllByRole('button')).toHaveLength(4)
  })

  it('every chip send glyph names WHICH chip it sends', () => {
    const { container } = render(
      <FollowupChips items={['draft the summary', 'run the tests']} onPick={() => {}} onSend={() => {}} />)
    expect(within(container).getByLabelText('Send: draft the summary')).toBeTruthy()
    expect(within(container).getByLabelText('Send: run the tests')).toBeTruthy()
  })
})

// The chips half of the same done_when clause, driven the way the bar's half already is.
// The block above proves the chips are NAMED and that four buttons exist by role — which
// is a count, not a traversal: a role query finds an element `Tab` may never land on, and
// says nothing about what activating it does. That distinction is load-bearing here for
// one specific reason: the label half sends on `onDoubleClick`, and a double-click has no
// keyboard equivalent at all. So the send glyph is the ONLY keyboard path to sending a
// chip. If it ever lost its tab stop or its activation, sending a suggestion would
// silently become mouse-only while every assertion above still passed.
describe('FollowupChips keyboard traversal is DRIVEN, not declared', () => {
  afterEach(() => { document.body.innerHTML = '' })

  /** Whatever currently has focus, identified the way a screen reader would name it. */
  const focused = () => {
    const el = document.activeElement as HTMLElement | null
    if (!el || el === document.body) return 'body'
    return el.getAttribute('aria-label') ?? (el.textContent || el.tagName.toLowerCase())
  }

  it('Tab reaches both halves of every chip, in visual order', async () => {
    const user = userEvent.setup()
    render(<FollowupChips items={['draft the summary', 'run the tests']}
      onPick={() => {}} onSend={() => {}} />)

    const stops: string[] = []
    for (let i = 0; i < 4; i++) { await user.tab(); stops.push(focused()) }
    // Label then send glyph, chip by chip — asserted as an ORDER, because each element
    // being focusable in isolation says nothing about the sequence a user walks.
    expect(stops).toEqual([
      'draft the summary', 'Send: draft the summary',
      'run the tests', 'Send: run the tests',
    ])
    // Vacuity floor: if nothing were focusable, every entry above would read 'body' and
    // a laxer assertion would still have "passed" four times over.
    expect(stops).not.toContain('body')
  })

  it('Enter on a chip label fills the composer WITHOUT sending it', async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    const onSend = vi.fn()
    render(<FollowupChips items={['draft the summary']} onPick={onPick} onSend={onSend} />)

    await user.tab()
    expect(focused()).toBe('draft the summary')
    await user.keyboard('{Enter}')
    expect(onPick).toHaveBeenCalledWith('draft the summary')
    // Picking is an EDIT affordance ("click to edit · double-click to send"). If Enter
    // also sent, a keyboard user could never edit a suggestion before dispatching it.
    expect(onSend).not.toHaveBeenCalled()
  })

  it('Enter on the send glyph sends, so double-click is not the only way', async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    const onSend = vi.fn()
    render(<FollowupChips items={['draft the summary', 'run the tests']}
      onPick={onPick} onSend={onSend} />)

    // Second chip's glyph: three tabs (label, glyph, label) then one more.
    for (let i = 0; i < 4; i++) await user.tab()
    expect(focused()).toBe('Send: run the tests')
    await user.keyboard('{Enter}')
    expect(onSend).toHaveBeenCalledWith('run the tests')
    expect(onSend).toHaveBeenCalledTimes(1)
    // The glyph sends outright; it is not a second route into the pick path.
    expect(onPick).not.toHaveBeenCalled()
  })
})
