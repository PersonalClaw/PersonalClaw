import { describe, it, expect, beforeEach } from 'vitest'
import {
  MARK_CLASS,
  MARK_ID_ATTR,
  anchorFromSelection,
  clearMarks,
  markAnchors,
  scrollProgress,
} from './readingAnchors'

// ── Reading-highlight anchoring (KNOWLEDGE-LIBRARY T3.1) ────────────────────────
// The atom's load-bearing clause is that a highlight PERSISTS and REAPPEARS. Persisting
// is the store's job (tests/test_knowledge_annotations.py); reappearing is this module's,
// and it is the half that can silently fail: a stored quote is only a highlight if it can
// be found again in the rendered prose.
//
// So these tests are built around the round trip — take a selection, derive the anchor,
// then feed that anchor back through `markAnchors` and assert it paints the SAME
// characters. A one-directional test (does the marker find a hard-coded string?) would
// pass with an occurrence rule that disagrees with the one used at capture time, which is
// exactly the bug that makes a second highlight of a repeated sentence land on the first.

let root: HTMLElement

beforeEach(() => {
  root = document.createElement('div')
  document.body.appendChild(root)
})

/** Select from `(startNode, startOffset)` to `(endNode, endOffset)` and return the
 *  anchor the reader would persist. */
function selectAndAnchor(startNode: Node, startOffset: number, endNode: Node, endOffset: number) {
  const range = document.createRange()
  range.setStart(startNode, startOffset)
  range.setEnd(endNode, endOffset)
  const selection = window.getSelection()!
  selection.removeAllRanges()
  selection.addRange(range)
  return anchorFromSelection(root, selection)
}

function marks() {
  return Array.from(root.querySelectorAll(`.${MARK_CLASS}`))
}

function markedText() {
  return marks().map((m) => m.textContent).join('')
}

// ── Capture ─────────────────────────────────────────────────────────────

it('a selection inside one paragraph yields the exact quote at occurrence 0', () => {
  root.innerHTML = '<p>The quick brown fox jumps.</p>'
  const text = root.querySelector('p')!.firstChild!

  expect(selectAndAnchor(text, 4, text, 19)).toEqual({ quote: 'quick brown fox', occurrence: 0 })
})

it('the SECOND instance of a repeated sentence anchors at occurrence 1', () => {
  // The fixture really does repeat the string — without that this test would be vacuous
  // and would pass against an implementation that always reports 0.
  root.innerHTML = '<p>Ship it. Then ship it. Done.</p>'
  const text = root.querySelector('p')!.firstChild!
  expect(text.textContent).toBe('Ship it. Then ship it. Done.')

  const first = selectAndAnchor(text, 0, text, 8)
  const second = selectAndAnchor(text, 14, text, 22)

  expect(first).toEqual({ quote: 'Ship it.', occurrence: 0 })
  expect(second).toEqual({ quote: 'ship it.', occurrence: 0 })
  // Case makes those two different strings; assert the same-string case explicitly.
  const third = selectAndAnchor(text, 5, text, 7)
  const fourth = selectAndAnchor(text, 19, text, 21)
  expect(third).toEqual({ quote: 'it', occurrence: 0 })
  expect(fourth).toEqual({ quote: 'it', occurrence: 1 })
})

it('a selection spanning inline markup keeps the flattened text as the quote', () => {
  root.innerHTML = '<p>a <strong>bold</strong> claim</p>'
  const p = root.querySelector('p')!
  const anchor = selectAndAnchor(p.firstChild!, 0, p.lastChild!, 6)

  expect(anchor).toEqual({ quote: 'a bold claim', occurrence: 0 })
})

it('a selection spanning two paragraphs is one anchor over the joined text', () => {
  root.innerHTML = '<p>first para</p><p>second para</p>'
  const [p1, p2] = Array.from(root.querySelectorAll('p'))
  const anchor = selectAndAnchor(p1.firstChild!, 6, p2.firstChild!, 6)

  expect(anchor).toEqual({ quote: 'parasecond', occurrence: 0 })
})

it('leading and trailing whitespace is trimmed off the stored quote', () => {
  root.innerHTML = '<p>keep   this   tidy</p>'
  const text = root.querySelector('p')!.firstChild!

  expect(selectAndAnchor(text, 4, text, 12)).toEqual({ quote: 'this', occurrence: 0 })
})

it('an empty, collapsed, or whitespace-only selection yields no anchor', () => {
  root.innerHTML = '<p>a b</p>'
  const text = root.querySelector('p')!.firstChild!

  expect(selectAndAnchor(text, 3, text, 3)).toBeNull()   // collapsed
  expect(selectAndAnchor(text, 1, text, 2)).toBeNull()   // just the space
  expect(anchorFromSelection(root, null)).toBeNull()
})

it('a selection outside the article yields no anchor', () => {
  root.innerHTML = '<p>inside</p>'
  const outside = document.createElement('p')
  outside.textContent = 'outside the article'
  document.body.appendChild(outside)

  expect(selectAndAnchor(outside.firstChild!, 0, outside.firstChild!, 7)).toBeNull()
})

// ── Round trip: capture → paint ─────────────────────────────────────────

it('an anchor derived from a selection paints exactly that selection back', () => {
  root.innerHTML = '<p>The quick brown fox jumps.</p>'
  const text = root.querySelector('p')!.firstChild!
  const anchor = selectAndAnchor(text, 4, text, 19)!

  const unresolved = markAnchors(root, [{ id: 'a1', ...anchor }])

  expect(unresolved).toEqual([])
  expect(markedText()).toBe('quick brown fox')
  expect(marks()[0].getAttribute(MARK_ID_ATTR)).toBe('a1')
  expect(root.textContent).toBe('The quick brown fox jumps.')
})

it('two anchors on the same repeated string paint DIFFERENT instances', () => {
  root.innerHTML = '<p>alpha beta alpha beta</p>'

  markAnchors(root, [
    { id: 'first', quote: 'alpha', occurrence: 0 },
    { id: 'second', quote: 'alpha', occurrence: 1 },
  ])

  const painted = marks()
  expect(painted).toHaveLength(2)
  // Their positions in the flattened text must differ — the whole point of `occurrence`.
  const offsets = painted.map((m) => root.textContent!.indexOf(m.textContent!, 0))
  expect(new Set(painted.map((m) => m.getAttribute(MARK_ID_ATTR)))).toEqual(new Set(['first', 'second']))
  expect(offsets).toHaveLength(2)
  const positions = painted.map((m) => {
    const before = document.createRange()
    before.setStart(root, 0)
    before.setEnd(m, 0)
    return before.toString().length
  })
  expect(positions[0]).not.toBe(positions[1])
})

it('a multi-node anchor paints one mark per node it crosses, carrying one id', () => {
  root.innerHTML = '<p>a <strong>bold</strong> claim</p>'
  const p = root.querySelector('p')!
  const anchor = selectAndAnchor(p.firstChild!, 0, p.lastChild!, 6)!

  markAnchors(root, [{ id: 'x', ...anchor }])

  expect(marks().length).toBeGreaterThan(1)
  expect(new Set(marks().map((m) => m.getAttribute(MARK_ID_ATTR)))).toEqual(new Set(['x']))
  expect(markedText()).toBe('a bold claim')
})

it('an anchor whose passage is gone resolves to nothing and is REPORTED', () => {
  root.innerHTML = '<p>the body was rewritten</p>'

  const unresolved = markAnchors(root, [
    { id: 'stale', quote: 'a sentence that is no longer here', occurrence: 0 },
    { id: 'live', quote: 'rewritten', occurrence: 0 },
  ])

  expect(unresolved).toEqual(['stale'])
  expect(markedText()).toBe('rewritten')
})

it('asking for an occurrence the text does not have is unresolved, not a wrong paint', () => {
  root.innerHTML = '<p>only once</p>'

  expect(markAnchors(root, [{ id: 'n', quote: 'once', occurrence: 3 }])).toEqual(['n'])
  expect(marks()).toHaveLength(0)
})

// ── Idempotence ─────────────────────────────────────────────────────────

it('re-marking does not nest or duplicate marks', () => {
  root.innerHTML = '<p>stable passage here</p>'
  const anchors = [{ id: 'a', quote: 'passage', occurrence: 0 }]

  markAnchors(root, anchors)
  markAnchors(root, anchors)
  markAnchors(root, anchors)

  expect(marks()).toHaveLength(1)
  expect(root.querySelectorAll(`.${MARK_CLASS} .${MARK_CLASS}`)).toHaveLength(0)
  expect(root.textContent).toBe('stable passage here')
})

it('clearMarks restores the original text and re-joins the split nodes', () => {
  root.innerHTML = '<p>stable passage here</p>'
  markAnchors(root, [{ id: 'a', quote: 'passage', occurrence: 0 }])

  clearMarks(root)

  expect(marks()).toHaveLength(0)
  expect(root.textContent).toBe('stable passage here')
  // One text node again — without normalize() the paragraph stays sharded, and a later
  // anchor spanning the seam would still work but the DOM would grow on every cycle.
  expect(root.querySelector('p')!.childNodes).toHaveLength(1)
})

it('a passage highlighted, cleared and re-anchored still round-trips', () => {
  root.innerHTML = '<p>The quick brown fox jumps.</p>'
  markAnchors(root, [{ id: 'a', quote: 'brown fox', occurrence: 0 }])
  clearMarks(root)

  const text = root.querySelector('p')!.firstChild!
  expect(selectAndAnchor(text, 10, text, 19)).toEqual({ quote: 'brown fox', occurrence: 0 })
})

it('script and style text is not part of the anchoring surface', () => {
  root.innerHTML = '<style>p { color: red }</style><p>real prose</p>'

  const anchor = selectAndAnchor(root.querySelector('p')!.firstChild!, 0, root.querySelector('p')!.firstChild!, 4)

  expect(anchor).toEqual({ quote: 'real', occurrence: 0 })
})

// ── Progress ────────────────────────────────────────────────────────────

describe('scrollProgress', () => {
  it('reads 0 at the top and 1 at the bottom', () => {
    expect(scrollProgress({ scrollTop: 0, scrollHeight: 1000, clientHeight: 500 })).toBe(0)
    expect(scrollProgress({ scrollTop: 500, scrollHeight: 1000, clientHeight: 500 })).toBe(1)
  })

  it('reads the fraction in between', () => {
    expect(scrollProgress({ scrollTop: 125, scrollHeight: 1000, clientHeight: 500 })).toBe(0.25)
  })

  it('an article shorter than the viewport is fully read, not 0%', () => {
    // Otherwise the indicator sticks at empty on exactly the articles a reader finishes
    // fastest — a wrong reading, not a missing one.
    expect(scrollProgress({ scrollTop: 0, scrollHeight: 300, clientHeight: 500 })).toBe(1)
    expect(scrollProgress({ scrollTop: 0, scrollHeight: 500, clientHeight: 500 })).toBe(1)
  })

  it('clamps overscroll', () => {
    expect(scrollProgress({ scrollTop: -40, scrollHeight: 1000, clientHeight: 500 })).toBe(0)
    expect(scrollProgress({ scrollTop: 900, scrollHeight: 1000, clientHeight: 500 })).toBe(1)
  })
})
